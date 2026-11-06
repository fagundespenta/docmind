from __future__ import annotations

from pathlib import Path

import pytest

from app.embeddings import HashingEmbedder
from app.llm import ChatMessage, FakeLLM
from app.rag import RagPipeline
from app.vectorstore import ChromaVectorStore, InMemoryVectorStore, SearchHit, VectorStore
from tests.conftest import HANDBOOK

DOCS = {
    "pets.txt": "Cats are independent animals that sleep most of the day. "
    "Dogs need daily walks and enjoy playing fetch in the park.",
    "space.txt": "The Moon orbits the Earth roughly every 27 days. "
    "Mars has two small moons called Phobos and Deimos.",
    "cooking.txt": "To bake bread you need flour, water, yeast and salt. "
    "Knead the dough for ten minutes and let it rise for an hour.",
}


@pytest.fixture(params=["memory", "chroma"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> VectorStore:
    if request.param == "memory":
        return InMemoryVectorStore()
    return ChromaVectorStore(str(tmp_path / "chroma"))


@pytest.fixture
def pipeline(store: VectorStore) -> RagPipeline:
    p = RagPipeline(HashingEmbedder(), store, chunk_size=200, chunk_overlap=20)
    for doc_id, (name, text) in enumerate(DOCS.items(), start=1):
        p.ingest(workspace_id=1, document_id=doc_id, filename=name, data=text.encode())
    return p


def test_embedder_is_deterministic_and_normalized() -> None:
    emb = HashingEmbedder(dim=64)
    a, b = emb.embed(["hello world", "hello world"])
    assert a == b
    assert abs(sum(x * x for x in a) - 1.0) < 1e-9
    assert emb.embed([""])[0] == [0.0] * 64


@pytest.mark.parametrize(
    "question,expected",
    [
        ("How many moons does Mars have?", "space.txt"),
        ("What ingredients do I need to bake bread?", "cooking.txt"),
        ("Do dogs need walks?", "pets.txt"),
    ],
)
def test_retrieves_the_relevant_document(
    pipeline: RagPipeline, question: str, expected: str
) -> None:
    hits = pipeline.retrieve(1, question, k=3)
    assert hits, "expected at least one hit"
    assert hits[0].filename == expected
    assert hits == sorted(hits, key=lambda h: h.score, reverse=True)


def test_workspaces_are_isolated(pipeline: RagPipeline) -> None:
    assert pipeline.retrieve(2, "Mars moons", k=3) == []


def test_delete_document_removes_its_chunks(pipeline: RagPipeline) -> None:
    pipeline.delete_document(1, document_id=2)  # space.txt
    hits = pipeline.retrieve(1, "How many moons does Mars have?", k=5)
    assert all(h.filename != "space.txt" for h in hits)


def test_drop_workspace(pipeline: RagPipeline) -> None:
    pipeline.drop_workspace(1)
    assert pipeline.retrieve(1, "bread", k=3) == []


def test_system_prompt_numbers_sources_with_pages() -> None:
    hits = [
        SearchHit("alpha", 1, "a.pdf", 0, 4, 0.9),
        SearchHit("beta", 2, "b.txt", 0, None, 0.8),
    ]
    prompt = RagPipeline.build_system_prompt(hits)
    assert "[1] (a.pdf, page 4)\nalpha" in prompt
    assert "[2] (b.txt)\nbeta" in prompt


async def test_fake_llm_cites_the_best_source() -> None:
    p = RagPipeline(HashingEmbedder(), InMemoryVectorStore())
    p.ingest(1, 1, "handbook.txt", HANDBOOK.encode())
    hits = p.retrieve(1, "How many vacation days do employees get?", k=3)
    llm = FakeLLM()
    tokens = [
        t async for t in llm.stream("", [ChatMessage("user", "How many vacation days?")], hits)
    ]
    answer = "".join(tokens)
    assert "25 days of paid vacation" in answer
    assert "[1]" in answer
