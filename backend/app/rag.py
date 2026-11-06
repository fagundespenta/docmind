"""Ingestion and retrieval-augmented generation pipeline."""

from __future__ import annotations

from dataclasses import dataclass

from app.embeddings import Embedder
from app.ingestion.chunking import chunk_pages
from app.ingestion.parsers import parse_document
from app.llm import ChatMessage
from app.vectorstore import SearchHit, StoredChunk, VectorStore, collection_name

SYSTEM_PROMPT = """\
You are DocMind, an assistant that answers questions using only the document excerpts \
provided below.

Rules:
- Base every statement on the excerpts. If they do not contain the answer, say so plainly.
- Cite the excerpts you used with bracketed numbers, e.g. [1] or [2][3], right after the \
sentence they support.
- Be concise. Use short paragraphs or bullet points.
- Answer in the same language as the question.

Excerpts:
{context}
"""

NO_CONTEXT_ANSWER = (
    "I couldn't find anything relevant in this workspace's documents. "
    "Upload a document or try a different question."
)


@dataclass(frozen=True, slots=True)
class IngestResult:
    page_count: int
    chunk_count: int


class RagPipeline:
    def __init__(
        self,
        embedder: Embedder,
        store: VectorStore,
        chunk_size: int = 1000,
        chunk_overlap: int = 150,
        min_score: float = 0.05,
    ) -> None:
        self.embedder = embedder
        self.store = store
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.min_score = min_score

    def collection(self, workspace_id: int) -> str:
        return collection_name(workspace_id, self.embedder.slug)

    # ---------------------------------------------------------------- ingestion

    def ingest(
        self, workspace_id: int, document_id: int, filename: str, data: bytes
    ) -> IngestResult:
        pages = parse_document(filename, data)
        chunks = chunk_pages(pages, self.chunk_size, self.chunk_overlap)
        if not chunks:
            raise ValueError("No extractable text found (is this a scanned PDF?)")
        vectors = self.embedder.embed([c.text for c in chunks])
        self.store.add(
            self.collection(workspace_id),
            [
                StoredChunk(
                    id=f"doc{document_id}-{c.index}",
                    text=c.text,
                    embedding=vec,
                    document_id=document_id,
                    filename=filename,
                    chunk_index=c.index,
                    page=c.page,
                )
                for c, vec in zip(chunks, vectors, strict=True)
            ],
        )
        return IngestResult(page_count=len(pages), chunk_count=len(chunks))

    def delete_document(self, workspace_id: int, document_id: int) -> None:
        self.store.delete_document(self.collection(workspace_id), document_id)

    def drop_workspace(self, workspace_id: int) -> None:
        self.store.drop_collection(self.collection(workspace_id))

    # ---------------------------------------------------------------- retrieval

    def retrieve(self, workspace_id: int, query: str, k: int) -> list[SearchHit]:
        [vector] = self.embedder.embed([query])
        hits = self.store.query(self.collection(workspace_id), vector, k)
        return [h for h in hits if h.score >= self.min_score]

    # --------------------------------------------------------------- generation

    @staticmethod
    def build_system_prompt(sources: list[SearchHit]) -> str:
        blocks = []
        for n, hit in enumerate(sources, start=1):
            where = f"{hit.filename}, page {hit.page}" if hit.page else hit.filename
            blocks.append(f"[{n}] ({where})\n{hit.text}")
        return SYSTEM_PROMPT.format(context="\n\n".join(blocks))

    @staticmethod
    def build_messages(history: list[ChatMessage], question: str) -> list[ChatMessage]:
        return [*history, ChatMessage(role="user", content=question)]


def source_payload(n: int, hit: SearchHit) -> dict[str, object]:
    return {
        "index": n,
        "document_id": hit.document_id,
        "filename": hit.filename,
        "page": hit.page,
        "chunk_index": hit.chunk_index,
        "score": round(hit.score, 4),
        "text": hit.text,
    }
