from __future__ import annotations

import pytest

from app.ingestion.parsers import UnsupportedFileError, parse_document
from tests.conftest import make_docx, make_pdf


def test_pdf_pages_are_numbered() -> None:
    data = make_pdf(["Hello from page one", "Second page here"])
    pages = parse_document("report.PDF", data)
    assert [p.number for p in pages] == [1, 2]
    assert "page one" in pages[0].text
    assert "Second page" in pages[1].text


def test_docx_paragraphs_are_extracted() -> None:
    data = make_docx(["Title", "Body paragraph with details."])
    [page] = parse_document("notes.docx", data)
    assert page.number is None
    assert "Body paragraph with details." in page.text


def test_txt_handles_bom_and_latin1() -> None:
    [page] = parse_document("a.txt", "﻿olá mundo".encode())
    assert page.text == "olá mundo"
    [page] = parse_document("b.txt", "café".encode("latin-1"))
    assert page.text == "café"


def test_unsupported_extension() -> None:
    with pytest.raises(UnsupportedFileError):
        parse_document("image.png", b"\x89PNG")
