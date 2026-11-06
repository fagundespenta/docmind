"""Turn uploaded files into page-level text."""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import PurePath

SUPPORTED_EXTENSIONS = frozenset({".pdf", ".docx", ".txt", ".md"})


class UnsupportedFileError(ValueError):
    """Raised for file types DocMind cannot read."""


@dataclass(frozen=True, slots=True)
class Page:
    text: str
    number: int | None  # 1-based page number, None for formats without pages


def extension_of(filename: str) -> str:
    return PurePath(filename).suffix.lower()


def parse_document(filename: str, data: bytes) -> list[Page]:
    ext = extension_of(filename)
    if ext == ".pdf":
        return _parse_pdf(data)
    if ext == ".docx":
        return _parse_docx(data)
    if ext in {".txt", ".md"}:
        return _parse_text(data)
    raise UnsupportedFileError(
        f"Unsupported file type '{ext or filename}'. "
        f"Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
    )


def _parse_pdf(data: bytes) -> list[Page]:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    pages = []
    for i, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        pages.append(Page(text=text, number=i))
    return pages


def _parse_docx(data: bytes) -> list[Page]:
    import docx

    document = docx.Document(io.BytesIO(data))
    blocks: list[str] = [p.text for p in document.paragraphs if p.text.strip()]
    for table in document.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                blocks.append(" | ".join(cells))
    return [Page(text="\n\n".join(blocks), number=None)]


def _parse_text(data: bytes) -> list[Page]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    return [Page(text=text, number=None)]
