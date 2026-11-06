"""Recursive, overlap-aware text chunking.

The splitter first breaks text on the coarsest separator that keeps pieces under
``chunk_size`` (paragraphs, then lines, sentences, words, and finally characters),
then greedily packs those pieces back into chunks, carrying ``overlap`` characters
of trailing context from one chunk into the next. Chunks never span pages, so every
chunk maps to exactly one page for citations.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.ingestion.parsers import Page

SEPARATORS: tuple[str, ...] = ("\n\n", "\n", ". ", " ", "")


@dataclass(frozen=True, slots=True)
class Chunk:
    text: str
    index: int
    page: int | None


def normalize(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def split_text(text: str, chunk_size: int = 1000, overlap: int = 150) -> list[str]:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if not 0 <= overlap < chunk_size:
        raise ValueError("overlap must be >= 0 and smaller than chunk_size")

    text = normalize(text)
    if not text:
        return []
    pieces = _split_recursive(text, chunk_size, SEPARATORS)
    return _merge(pieces, chunk_size, overlap)


def _split_recursive(text: str, chunk_size: int, separators: tuple[str, ...]) -> list[str]:
    if len(text) <= chunk_size:
        return [text]
    sep_index = next(
        (i for i, s in enumerate(separators) if s == "" or s in text), len(separators) - 1
    )
    sep = separators[sep_index]
    if sep == "":
        return [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]

    parts = text.split(sep)
    # Keep the separator attached so joining pieces reproduces the original text.
    parts = [p + sep for p in parts[:-1]] + [parts[-1]]
    pieces: list[str] = []
    for part in parts:
        if not part:
            continue
        if len(part) <= chunk_size:
            pieces.append(part)
        else:
            pieces.extend(_split_recursive(part, chunk_size, separators[sep_index + 1 :]))
    return pieces


def _merge(pieces: list[str], chunk_size: int, overlap: int) -> list[str]:
    chunks: list[str] = []
    window: list[str] = []
    window_len = 0
    for piece in pieces:
        if window and window_len + len(piece) > chunk_size:
            chunks.append("".join(window).strip())
            # Drop pieces from the front until only `overlap` chars of context remain
            # and the next piece fits.
            while window and (window_len > overlap or window_len + len(piece) > chunk_size):
                window_len -= len(window.pop(0))
        window.append(piece)
        window_len += len(piece)
    if window:
        chunks.append("".join(window).strip())
    return [c for c in chunks if c]


def chunk_pages(pages: list[Page], chunk_size: int = 1000, overlap: int = 150) -> list[Chunk]:
    chunks: list[Chunk] = []
    for page in pages:
        for text in split_text(page.text, chunk_size, overlap):
            chunks.append(Chunk(text=text, index=len(chunks), page=page.number))
    return chunks
