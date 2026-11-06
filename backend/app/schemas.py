"""Pydantic request/response models (these also drive the OpenAPI docs)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120, examples=["Employee handbook"])
    description: str = Field(default="", max_length=2000)


class WorkspaceOut(ORMModel):
    id: int
    name: str
    description: str
    created_at: datetime
    document_count: int = 0


class DocumentOut(ORMModel):
    id: int
    workspace_id: int
    filename: str
    content_type: str
    size_bytes: int
    page_count: int
    chunk_count: int
    status: str
    error: str | None
    created_at: datetime


class Source(BaseModel):
    index: int = Field(description="The [n] marker used in the answer")
    document_id: int
    filename: str
    page: int | None
    chunk_index: int
    score: float
    text: str


class MessageOut(ORMModel):
    id: int
    role: str
    content: str
    sources: list[Source]
    created_at: datetime


class ConversationOut(ORMModel):
    id: int
    workspace_id: int
    title: str
    created_at: datetime


class ConversationDetail(ConversationOut):
    messages: list[MessageOut]


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000, examples=["How many vacation days?"])
    conversation_id: int | None = Field(
        default=None, description="Continue an existing conversation; omit to start a new one"
    )
    top_k: int | None = Field(default=None, ge=1, le=20)


class AskResponse(BaseModel):
    conversation_id: int
    message_id: int
    answer: str
    sources: list[Source]


class SearchResponse(BaseModel):
    query: str
    results: list[Source]


class HealthOut(BaseModel):
    status: str
    version: str
    llm_provider: str
    embedding_provider: str
    vector_store: str
    auth_enabled: bool
