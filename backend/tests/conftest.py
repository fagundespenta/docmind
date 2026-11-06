from __future__ import annotations

import io
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

API_KEY = "test-key"


def make_pdf(pages: list[str]) -> bytes:
    """Build a minimal, valid multi-page PDF with one line of Helvetica text per page."""
    objects: list[bytes] = []
    n_pages = len(pages)
    font_id = 3 + 2 * n_pages
    page_ids = [3 + 2 * i for i in range(n_pages)]

    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>".encode())
    for i, text in enumerate(pages):
        safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream = f"BT /F1 12 Tf 72 720 Td ({safe}) Tj ET".encode()
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {page_ids[i] + 1} 0 R "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> >>".encode()
        )
        objects.append(
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
        )
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for num, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{num} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return out.getvalue()


def make_docx(paragraphs: list[str]) -> bytes:
    import docx

    document = docx.Document()
    for p in paragraphs:
        document.add_paragraph(p)
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


HANDBOOK = """\
Acme Corp Employee Handbook

Vacation policy. Full-time employees receive 25 days of paid vacation per year. \
Unused vacation days can be carried over until March 31 of the following year.

Remote work. Employees may work remotely up to three days per week, \
with manager approval. Core collaboration hours are 10:00 to 15:00.

Equipment. Every new hire receives a laptop and a 400 dollar budget for home office equipment.
"""


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        vector_store="memory",
        llm_provider="fake",
        embedding_provider="fake",
        fake_llm_delay=0,
        api_keys=API_KEY,
        rate_limit_per_minute=1000,
    )


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    app = create_app(settings)
    with TestClient(app, headers={"X-API-Key": API_KEY}) as c:
        yield c
    app.state.engine.dispose()


@pytest.fixture
def workspace_id(client: TestClient) -> int:
    resp = client.post("/api/workspaces", json={"name": "Handbook"})
    assert resp.status_code == 201
    ws_id: int = resp.json()["id"]
    return ws_id


@pytest.fixture
def indexed_workspace(client: TestClient, workspace_id: int) -> int:
    resp = client.post(
        f"/api/workspaces/{workspace_id}/documents",
        files={"file": ("handbook.txt", HANDBOOK.encode(), "text/plain")},
    )
    assert resp.status_code == 201, resp.text
    return workspace_id
