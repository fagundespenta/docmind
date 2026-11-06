from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.security import RateLimiter
from tests.conftest import API_KEY, make_docx, make_pdf


def parse_sse(body: str) -> list[tuple[str, dict[str, object]]]:
    events = []
    for block in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((lines["event"], json.loads(lines["data"])))
    return events


# ------------------------------------------------------------------ meta / auth


def test_health_is_public(client: TestClient) -> None:
    resp = client.get("/api/health", headers={"X-API-Key": ""})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["llm_provider"] == "fake"
    assert body["auth_enabled"] is True


def test_openapi_docs_available(client: TestClient) -> None:
    spec = client.get("/api/openapi.json").json()
    assert "/api/workspaces/{workspace_id}/ask/stream" in spec["paths"]
    assert "APIKeyHeader" in spec["components"]["securitySchemes"]
    assert client.get("/api/docs").status_code == 200


def test_requires_api_key(client: TestClient) -> None:
    assert client.get("/api/workspaces", headers={"X-API-Key": ""}).status_code == 401
    assert client.get("/api/workspaces", headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.get("/api/workspaces").status_code == 200


def test_rate_limit(settings: Settings) -> None:
    settings.rate_limit_per_minute = 3
    with TestClient(create_app(settings), headers={"X-API-Key": API_KEY}) as c:
        codes = [c.get("/api/workspaces").status_code for _ in range(4)]
        assert codes == [200, 200, 200, 429]
        assert int(c.get("/api/workspaces").headers["Retry-After"]) >= 1


def test_rate_limiter_window_slides() -> None:
    limiter = RateLimiter(limit=2, window=10)
    assert limiter.check("k", now=0) is None
    assert limiter.check("k", now=1) is None
    assert limiter.check("k", now=2) == 8
    assert limiter.check("other", now=2) is None
    assert limiter.check("k", now=10.5) is None


# ------------------------------------------------------------------- workspaces


def test_workspace_crud(client: TestClient) -> None:
    created = client.post("/api/workspaces", json={"name": "Legal", "description": "Contracts"})
    assert created.status_code == 201
    ws = created.json()
    assert client.post("/api/workspaces", json={"name": "Legal"}).status_code == 409
    assert [w["name"] for w in client.get("/api/workspaces").json()] == ["Legal"]
    assert client.get(f"/api/workspaces/{ws['id']}").json()["description"] == "Contracts"
    assert client.delete(f"/api/workspaces/{ws['id']}").status_code == 204
    assert client.get(f"/api/workspaces/{ws['id']}").status_code == 404


# -------------------------------------------------------------------- documents


def test_upload_pdf_docx_txt(client: TestClient, workspace_id: int) -> None:
    files = {
        "report.pdf": (make_pdf(["Quarterly revenue grew 12 percent", "Costs fell"]), 2),
        "notes.docx": (make_docx(["Meeting notes", "Ship the beta in May."]), 1),
        "readme.txt": (b"Plain text document about onboarding.", 1),
    }
    for name, (data, pages) in files.items():
        resp = client.post(
            f"/api/workspaces/{workspace_id}/documents", files={"file": (name, data)}
        )
        assert resp.status_code == 201, resp.text
        doc = resp.json()
        assert doc["status"] == "ready"
        assert doc["page_count"] == pages
        assert doc["chunk_count"] >= 1

    listed = client.get(f"/api/workspaces/{workspace_id}/documents").json()
    assert [d["filename"] for d in listed] == list(files)
    assert client.get(f"/api/workspaces/{workspace_id}").json()["document_count"] == 3


def test_upload_rejects_bad_files(client: TestClient, workspace_id: int) -> None:
    url = f"/api/workspaces/{workspace_id}/documents"
    assert client.post(url, files={"file": ("x.exe", b"MZ")}).status_code == 415
    assert client.post(url, files={"file": ("empty.txt", b"")}).status_code == 400
    resp = client.post(url, files={"file": ("blank.txt", b"   \n  ")})
    assert resp.status_code == 422
    assert client.get(url).json()[0]["status"] == "failed"


def test_upload_too_large(client: TestClient, settings: Settings, workspace_id: int) -> None:
    big = b"a" * (settings.max_upload_mb * 1024 * 1024 + 1)
    resp = client.post(
        f"/api/workspaces/{workspace_id}/documents", files={"file": ("big.txt", big)}
    )
    assert resp.status_code == 413


def test_delete_document_removes_it_from_search(client: TestClient, indexed_workspace: int) -> None:
    [doc] = client.get(f"/api/workspaces/{indexed_workspace}/documents").json()
    search = f"/api/workspaces/{indexed_workspace}/search"
    assert client.get(search, params={"q": "vacation days"}).json()["results"]
    assert client.delete(f"/api/documents/{doc['id']}").status_code == 204
    assert client.get(search, params={"q": "vacation days"}).json()["results"] == []


# ------------------------------------------------------------------------- chat


def test_ask_returns_cited_answer(client: TestClient, indexed_workspace: int) -> None:
    resp = client.post(
        f"/api/workspaces/{indexed_workspace}/ask",
        json={"question": "How many vacation days do employees get?"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "25 days" in body["answer"]
    assert "[1]" in body["answer"]
    assert body["sources"][0]["filename"] == "handbook.txt"
    assert body["sources"][0]["index"] == 1


def test_ask_without_documents(client: TestClient, workspace_id: int) -> None:
    resp = client.post(f"/api/workspaces/{workspace_id}/ask", json={"question": "Anything?"})
    assert resp.status_code == 200
    assert resp.json()["sources"] == []
    assert "couldn't find" in resp.json()["answer"]


def test_ask_stream_emits_sse_events(client: TestClient, indexed_workspace: int) -> None:
    with client.stream(
        "POST",
        f"/api/workspaces/{indexed_workspace}/ask/stream",
        json={"question": "Can I work remotely?"},
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        body = "".join(resp.iter_text())

    events = parse_sse(body)
    kinds = [kind for kind, _ in events]
    assert kinds[0] == "sources"
    assert kinds[-1] == "done"
    assert kinds.count("token") > 3
    answer = "".join(str(data["text"]) for kind, data in events if kind == "token")
    assert "remotely" in answer

    conv_id = events[-1][1]["conversation_id"]
    detail = client.get(f"/api/conversations/{conv_id}").json()
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]
    assert detail["messages"][1]["content"] == answer
    assert detail["messages"][1]["sources"][0]["filename"] == "handbook.txt"


def test_conversation_history_continues(client: TestClient, indexed_workspace: int) -> None:
    url = f"/api/workspaces/{indexed_workspace}/ask"
    first = client.post(url, json={"question": "How many vacation days?"}).json()
    second = client.post(
        url,
        json={"question": "What about remote work?", "conversation_id": first["conversation_id"]},
    ).json()
    assert second["conversation_id"] == first["conversation_id"]

    convs = client.get(f"/api/workspaces/{indexed_workspace}/conversations").json()
    assert len(convs) == 1
    assert convs[0]["title"] == "How many vacation days?"
    messages = client.get(f"/api/conversations/{first['conversation_id']}").json()["messages"]
    assert len(messages) == 4

    assert client.delete(f"/api/conversations/{first['conversation_id']}").status_code == 204
    assert client.get(f"/api/workspaces/{indexed_workspace}/conversations").json() == []


def test_conversation_must_belong_to_workspace(client: TestClient, indexed_workspace: int) -> None:
    conv = client.post(
        f"/api/workspaces/{indexed_workspace}/ask", json={"question": "vacation?"}
    ).json()["conversation_id"]
    other = client.post("/api/workspaces", json={"name": "Other"}).json()["id"]
    resp = client.post(
        f"/api/workspaces/{other}/ask", json={"question": "x", "conversation_id": conv}
    )
    assert resp.status_code == 404


def test_search_endpoint(client: TestClient, indexed_workspace: int) -> None:
    resp = client.get(
        f"/api/workspaces/{indexed_workspace}/search", params={"q": "laptop budget", "k": 2}
    )
    assert resp.status_code == 200
    results = resp.json()["results"]
    assert 1 <= len(results) <= 2
    assert "laptop" in results[0]["text"]


def test_validation_errors(client: TestClient, workspace_id: int) -> None:
    assert client.post("/api/workspaces", json={"name": ""}).status_code == 422
    url = f"/api/workspaces/{workspace_id}/ask"
    assert client.post(url, json={"question": ""}).status_code == 422
    assert client.post(url, json={"question": "x", "top_k": 99}).status_code == 422
    assert client.post("/api/workspaces/999/ask", json={"question": "x"}).status_code == 404
