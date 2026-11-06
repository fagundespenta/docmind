"""API key authentication and per-key rate limiting."""

from __future__ import annotations

import hmac
import math
import threading
import time
from collections import deque

from fastapi import HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False, description="Your API key")


class RateLimiter:
    """Sliding-window limiter: at most ``limit`` requests per ``window`` seconds per key."""

    def __init__(self, limit: int, window: float = 60.0) -> None:
        self.limit = limit
        self.window = window
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str, now: float | None = None) -> float | None:
        """Record a hit. Returns None if allowed, else seconds until a slot frees up."""
        if self.limit <= 0:
            return None
        now = time.monotonic() if now is None else now
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and now - hits[0] >= self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return self.window - (now - hits[0])
            hits.append(now)
            return None


def _matches(candidate: str, keys: frozenset[str]) -> bool:
    return any(hmac.compare_digest(candidate, k) for k in keys)


async def require_api_key(request: Request, api_key: str | None = Security(api_key_header)) -> str:
    settings = request.app.state.settings
    keys: frozenset[str] = settings.api_key_set
    if keys:
        if not api_key or not _matches(api_key, keys):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing or invalid API key (send it in the X-API-Key header)",
            )
        identity = api_key
    else:
        identity = request.client.host if request.client else "anonymous"

    limiter: RateLimiter = request.app.state.rate_limiter
    retry_after = limiter.check(identity)
    if retry_after is not None:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded",
            headers={"Retry-After": str(max(1, math.ceil(retry_after)))},
        )
    return identity
