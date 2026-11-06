"""Embedding providers.

``HashingEmbedder`` is a dependency-free, deterministic embedder (signed feature
hashing over word unigrams and bigrams). It needs no API key and gives sensible
keyword-overlap retrieval, which keeps tests and offline demos meaningful.
"""

from __future__ import annotations

import hashlib
import itertools
import math
import re
from typing import Protocol

from app.config import Settings

_TOKEN_RE = re.compile(r"[a-z0-9À-ɏ]+")
_STOPWORDS = frozenset(
    {
        "a",
        "about",
        "all",
        "any",
        "can",
        "could",
        "get",
        "if",
        "me",
        "my",
        "not",
        "should",
        "so",
        "we",
        "would",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "do",
        "does",
        "for",
        "from",
        "has",
        "have",
        "how",
        "i",
        "in",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "our",
        "that",
        "the",
        "their",
        "this",
        "to",
        "was",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "will",
        "with",
        "you",
        "your",
    }
)


class Embedder(Protocol):
    @property
    def slug(self) -> str:
        """Short identifier, used to keep vector collections per embedding space."""
        ...

    def embed(self, texts: list[str]) -> list[list[float]]: ...


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS]


class HashingEmbedder:
    def __init__(self, dim: int = 512) -> None:
        self.dim = dim

    @property
    def slug(self) -> str:
        return f"hash{self.dim}"

    def _bucket(self, feature: str) -> tuple[int, float]:
        digest = hashlib.blake2b(feature.encode(), digest_size=8).digest()
        value = int.from_bytes(digest, "little")
        return value % self.dim, 1.0 if (value >> 63) & 1 else -1.0

    def embed_one(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        tokens = tokenize(text)
        features = tokens + [f"{a}_{b}" for a, b in itertools.pairwise(tokens)]
        for feature in features:
            idx, sign = self._bucket(feature)
            vec[idx] += sign
        norm = math.sqrt(sum(v * v for v in vec))
        return [v / norm for v in vec] if norm else vec

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_one(t) for t in texts]


class OpenAIEmbedder:
    def __init__(self, api_key: str, model: str, batch_size: int = 64) -> None:
        from openai import OpenAI

        self._client = OpenAI(api_key=api_key)
        self.model = model
        self.batch_size = batch_size

    @property
    def slug(self) -> str:
        return "openai-" + re.sub(r"[^a-z0-9]", "", self.model.lower())[:24]

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i : i + self.batch_size]
            resp = self._client.embeddings.create(model=self.model, input=batch)
            out.extend(item.embedding for item in resp.data)
        return out


class GeminiEmbedder:
    def __init__(self, api_key: str, model: str, batch_size: int = 64) -> None:
        from google import genai

        self._client = genai.Client(api_key=api_key)
        self.model = model
        self.batch_size = batch_size

    @property
    def slug(self) -> str:
        return "gemini-" + re.sub(r"[^a-z0-9]", "", self.model.lower())[:24]

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i : i + self.batch_size]
            resp = self._client.models.embed_content(model=self.model, contents=batch)
            out.extend(list(e.values or []) for e in resp.embeddings or [])
        return out


def build_embedder(settings: Settings) -> Embedder:
    if settings.embedding_provider == "openai":
        _require(settings.openai_api_key, "OPENAI_API_KEY", "EMBEDDING_PROVIDER=openai")
        return OpenAIEmbedder(settings.openai_api_key, settings.openai_embedding_model)
    if settings.embedding_provider == "gemini":
        _require(settings.gemini_api_key, "GEMINI_API_KEY", "EMBEDDING_PROVIDER=gemini")
        return GeminiEmbedder(settings.gemini_api_key, settings.gemini_embedding_model)
    return HashingEmbedder()


def _require(value: str, env_name: str, context: str) -> None:
    if not value:
        raise RuntimeError(f"{env_name} must be set when {context}")
