from __future__ import annotations

import itertools

import pytest

from app.ingestion.chunking import chunk_pages, normalize, split_text
from app.ingestion.parsers import Page


def test_empty_text_produces_no_chunks() -> None:
    assert split_text("") == []
    assert split_text("   \n\n  \t ") == []


def test_short_text_is_a_single_chunk() -> None:
    assert split_text("Hello world.", chunk_size=100, overlap=10) == ["Hello world."]


def test_normalize_collapses_whitespace() -> None:
    assert normalize("a  \t b\r\n\n\n\nc ") == "a b\n\nc"


@pytest.mark.parametrize("chunk_size,overlap", [(50, 0), (120, 20), (300, 60)])
def test_chunks_respect_size_limit(chunk_size: int, overlap: int) -> None:
    text = " ".join(f"Sentence number {i} talks about topic {i % 7}." for i in range(200))
    chunks = split_text(text, chunk_size=chunk_size, overlap=overlap)
    assert len(chunks) > 1
    assert all(0 < len(c) <= chunk_size for c in chunks)


def test_chunks_cover_all_content() -> None:
    words = [f"word{i}" for i in range(500)]
    chunks = split_text(" ".join(words), chunk_size=100, overlap=0)
    seen = set(" ".join(chunks).split())
    assert seen == set(words)


def test_consecutive_chunks_overlap() -> None:
    text = " ".join(f"token{i}" for i in range(300))
    chunks = split_text(text, chunk_size=120, overlap=40)
    for prev, nxt in itertools.pairwise(chunks):
        tail = prev.split()[-1]
        assert tail in nxt.split(), "the last word of a chunk should reappear in the next one"


def test_prefers_paragraph_boundaries() -> None:
    para_a = "Alpha " * 30
    para_b = "Beta " * 30
    chunks = split_text(f"{para_a.strip()}\n\n{para_b.strip()}", chunk_size=200, overlap=0)
    assert len(chunks) == 2
    assert set(chunks[0].split()) == {"Alpha"}
    assert set(chunks[1].split()) == {"Beta"}


def test_hard_splits_very_long_words() -> None:
    chunks = split_text("x" * 250, chunk_size=100, overlap=0)
    assert [len(c) for c in chunks] == [100, 100, 50]


@pytest.mark.parametrize("overlap", [-1, 100, 150])
def test_invalid_overlap_rejected(overlap: int) -> None:
    with pytest.raises(ValueError):
        split_text("abc", chunk_size=100, overlap=overlap)


def test_chunk_pages_keeps_page_numbers_and_global_index() -> None:
    pages = [Page("First page text. " * 20, 1), Page("", 2), Page("Third page. " * 5, 3)]
    chunks = chunk_pages(pages, chunk_size=120, overlap=10)
    assert [c.index for c in chunks] == list(range(len(chunks)))
    assert {c.page for c in chunks} == {1, 3}
    assert chunks[-1].page == 3
