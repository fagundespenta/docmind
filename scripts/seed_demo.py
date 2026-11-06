"""Create a demo workspace and upload the files in ./samples through the public API.

Usage:  python scripts/seed_demo.py [--url http://localhost:8000] [--key dev-key]
Uses only the standard library, so it runs without installing anything.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import urllib.error
import urllib.request
import uuid
from pathlib import Path

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


def call(
    url: str,
    key: str,
    method: str = "GET",
    body: bytes | None = None,
    content_type: str = "application/json",
) -> object:
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("X-API-Key", key)
    if body is not None:
        req.add_header("Content-Type", content_type)
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read() or b"null")


def multipart(path: Path) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    body = (
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
            f'filename="{path.name}"\r\nContent-Type: {mime}\r\n\r\n'
        ).encode()
        + path.read_bytes()
        + f"\r\n--{boundary}--\r\n".encode()
    )
    return body, f"multipart/form-data; boundary={boundary}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--key", default="dev-key")
    parser.add_argument("--workspace", default="Northwind Labs")
    args = parser.parse_args()
    api = args.url.rstrip("/") + "/api"

    workspaces = call(f"{api}/workspaces", args.key)
    assert isinstance(workspaces, list)
    ws = next((w for w in workspaces if w["name"] == args.workspace), None)
    if ws is None:
        payload = json.dumps(
            {"name": args.workspace, "description": "Demo HR & security docs"}
        )
        ws = call(f"{api}/workspaces", args.key, "POST", payload.encode())
    assert isinstance(ws, dict)
    print(f"Workspace #{ws['id']}: {ws['name']}")

    for path in sorted(SAMPLES.iterdir()):
        body, ctype = multipart(path)
        try:
            doc = call(
                f"{api}/workspaces/{ws['id']}/documents", args.key, "POST", body, ctype
            )
            assert isinstance(doc, dict)
            print(f"  uploaded {path.name}: {doc['chunk_count']} chunks")
        except urllib.error.HTTPError as exc:
            print(f"  failed {path.name}: {exc.code} {exc.read().decode()}")


if __name__ == "__main__":
    main()
