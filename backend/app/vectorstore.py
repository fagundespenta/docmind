"""Vector stores: Chroma (persistent) and an in-memory store (tests / quick demos).

Each workspace gets its own collection, namespaced by the embedder's slug so that
switching embedding providers never mixes vectors of different dimensions.
"""

from __future__ import annotations

import contextlib
import math
import threading
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class StoredChunk:
    id: str
    text: str
    embedding: list[float]
    document_id: int
    filename: str
    chunk_index: int
    page: int | None


@dataclass(frozen=True, slots=True)
class SearchHit:
    text: str
    document_id: int
    filename: str
    chunk_index: int
    page: int | None
    score: float  # cosine similarity, higher is better


class VectorStore(Protocol):
    def add(self, collection: str, chunks: list[StoredChunk]) -> None: ...

    def query(self, collection: str, embedding: list[float], k: int) -> list[SearchHit]: ...

    def delete_document(self, collection: str, document_id: int) -> None: ...

    def drop_collection(self, collection: str) -> None: ...


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


@dataclass
class InMemoryVectorStore:
    _data: dict[str, dict[str, StoredChunk]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def add(self, collection: str, chunks: list[StoredChunk]) -> None:
        with self._lock:
            bucket = self._data.setdefault(collection, {})
            for chunk in chunks:
                bucket[chunk.id] = chunk

    def query(self, collection: str, embedding: list[float], k: int) -> list[SearchHit]:
        with self._lock:
            chunks = list(self._data.get(collection, {}).values())
        scored = sorted(
            ((c, _cosine(embedding, c.embedding)) for c in chunks),
            key=lambda pair: pair[1],
            reverse=True,
        )
        return [
            SearchHit(c.text, c.document_id, c.filename, c.chunk_index, c.page, score)
            for c, score in scored[:k]
        ]

    def delete_document(self, collection: str, document_id: int) -> None:
        with self._lock:
            bucket = self._data.get(collection, {})
            for key in [k for k, c in bucket.items() if c.document_id == document_id]:
                del bucket[key]

    def drop_collection(self, collection: str) -> None:
        with self._lock:
            self._data.pop(collection, None)


class ChromaVectorStore:
    def __init__(self, path: str | None = None) -> None:
        import chromadb
        from chromadb.config import Settings as ChromaSettings

        chroma_settings = ChromaSettings(anonymized_telemetry=False, allow_reset=True)
        if path:
            self._client = chromadb.PersistentClient(path=path, settings=chroma_settings)
        else:
            self._client = chromadb.EphemeralClient(settings=chroma_settings)

    def _collection(self, name: str) -> Any:
        return self._client.get_or_create_collection(
            name=name, metadata={"hnsw:space": "cosine"}, embedding_function=None
        )

    def add(self, collection: str, chunks: list[StoredChunk]) -> None:
        if not chunks:
            return
        self._collection(collection).add(
            ids=[c.id for c in chunks],
            embeddings=[c.embedding for c in chunks],
            documents=[c.text for c in chunks],
            metadatas=[
                {
                    "document_id": c.document_id,
                    "filename": c.filename,
                    "chunk_index": c.chunk_index,
                    # Chroma metadata cannot hold None; -1 means "no page".
                    "page": c.page if c.page is not None else -1,
                }
                for c in chunks
            ],
        )

    def query(self, collection: str, embedding: list[float], k: int) -> list[SearchHit]:
        coll = self._collection(collection)
        count = coll.count()
        if count == 0:
            return []
        result = coll.query(
            query_embeddings=[embedding],
            n_results=min(k, count),
            include=["documents", "metadatas", "distances"],
        )
        hits: list[SearchHit] = []
        for text, meta, dist in zip(
            result["documents"][0], result["metadatas"][0], result["distances"][0], strict=True
        ):
            page = int(meta["page"])
            hits.append(
                SearchHit(
                    text=text,
                    document_id=int(meta["document_id"]),
                    filename=str(meta["filename"]),
                    chunk_index=int(meta["chunk_index"]),
                    page=page if page >= 0 else None,
                    score=1.0 - float(dist),
                )
            )
        return hits

    def delete_document(self, collection: str, document_id: int) -> None:
        self._collection(collection).delete(where={"document_id": document_id})

    def drop_collection(self, collection: str) -> None:
        with contextlib.suppress(Exception):  # collection may not exist yet
            self._client.delete_collection(name=collection)


def collection_name(workspace_id: int, embedder_slug: str) -> str:
    return f"ws{workspace_id}-{embedder_slug}"
