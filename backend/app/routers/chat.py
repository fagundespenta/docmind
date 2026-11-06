"""Question answering (JSON and Server-Sent Events), search, and conversation history."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import Conversation, Message, Workspace
from app.deps import get_llm, get_pipeline, get_session, get_settings, get_workspace
from app.llm import ChatMessage, LLMProvider
from app.rag import NO_CONTEXT_ANSWER, RagPipeline, source_payload
from app.schemas import (
    AskRequest,
    AskResponse,
    ConversationDetail,
    ConversationOut,
    SearchResponse,
    Source,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["chat"])


# ------------------------------------------------------------------ helpers


def _conversation_for(
    session: Session, workspace: Workspace, body: AskRequest
) -> tuple[Conversation, list[ChatMessage]]:
    if body.conversation_id is None:
        title = body.question.strip().replace("\n", " ")
        conversation = Conversation(
            workspace_id=workspace.id, title=title[:80] + ("…" if len(title) > 80 else "")
        )
        session.add(conversation)
        session.flush()
        return conversation, []

    conversation_or_none = session.get(Conversation, body.conversation_id)
    if conversation_or_none is None or conversation_or_none.workspace_id != workspace.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found in this workspace")
    history = [
        ChatMessage(role="assistant" if m.role == "assistant" else "user", content=m.content)
        for m in conversation_or_none.messages
    ]
    return conversation_or_none, history


class _Turn:
    """Everything prepared before generation starts (shared by JSON and SSE endpoints)."""

    def __init__(
        self,
        conversation: Conversation,
        system: str,
        messages: list[ChatMessage],
        sources: list[dict[str, Any]],
        hits: list[Any],
    ) -> None:
        self.conversation = conversation
        self.system = system
        self.messages = messages
        self.sources = sources
        self.hits = hits


async def _prepare_turn(
    body: AskRequest,
    workspace: Workspace,
    session: Session,
    pipeline: RagPipeline,
    settings: Settings,
) -> _Turn:
    conversation, history = _conversation_for(session, workspace, body)
    history = history[-settings.history_turns * 2 :] if settings.history_turns else []
    k = body.top_k or settings.top_k
    hits = await run_in_threadpool(pipeline.retrieve, workspace.id, body.question, k)
    sources = [source_payload(n, h) for n, h in enumerate(hits, start=1)]
    session.add(Message(conversation_id=conversation.id, role="user", content=body.question))
    session.commit()
    return _Turn(
        conversation=conversation,
        system=pipeline.build_system_prompt(hits),
        messages=pipeline.build_messages(history, body.question),
        sources=sources,
        hits=hits,
    )


def _save_answer(session: Session, turn: _Turn, answer: str) -> Message:
    message = Message(
        conversation_id=turn.conversation.id,
        role="assistant",
        content=answer,
        sources=turn.sources,
    )
    session.add(message)
    session.commit()
    return message


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# ---------------------------------------------------------------- endpoints


@router.post(
    "/workspaces/{workspace_id}/ask",
    response_model=AskResponse,
    summary="Ask a question and get the full answer with cited sources",
)
async def ask(
    body: AskRequest,
    workspace: Workspace = Depends(get_workspace),
    session: Session = Depends(get_session),
    pipeline: RagPipeline = Depends(get_pipeline),
    llm: LLMProvider = Depends(get_llm),
    settings: Settings = Depends(get_settings),
) -> AskResponse:
    turn = await _prepare_turn(body, workspace, session, pipeline, settings)
    if turn.hits:
        parts = [t async for t in llm.stream(turn.system, turn.messages, turn.hits)]
        answer = "".join(parts)
    else:
        answer = NO_CONTEXT_ANSWER
    message = _save_answer(session, turn, answer)
    return AskResponse(
        conversation_id=turn.conversation.id,
        message_id=message.id,
        answer=answer,
        sources=[Source.model_validate(s) for s in turn.sources],
    )


@router.post(
    "/workspaces/{workspace_id}/ask/stream",
    summary="Ask a question and stream the answer as Server-Sent Events",
    description=(
        "Emits `sources` (the cited chunks, sent first), then many `token` events with text "
        "deltas, and finally `done` with the conversation and message ids. On failure an "
        "`error` event is sent instead of `done`."
    ),
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def ask_stream(
    body: AskRequest,
    workspace: Workspace = Depends(get_workspace),
    session: Session = Depends(get_session),
    pipeline: RagPipeline = Depends(get_pipeline),
    llm: LLMProvider = Depends(get_llm),
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    turn = await _prepare_turn(body, workspace, session, pipeline, settings)
    # The request-scoped session may be closed before the stream finishes, so the
    # generator persists the answer with its own session.
    engine = session.get_bind()

    async def events() -> AsyncIterator[str]:
        yield _sse("sources", {"conversation_id": turn.conversation.id, "sources": turn.sources})
        parts: list[str] = []
        try:
            if turn.hits:
                async for token in llm.stream(turn.system, turn.messages, turn.hits):
                    parts.append(token)
                    yield _sse("token", {"text": token})
            else:
                parts.append(NO_CONTEXT_ANSWER)
                yield _sse("token", {"text": NO_CONTEXT_ANSWER})
        except Exception as exc:
            logger.exception("LLM streaming failed")
            yield _sse("error", {"detail": f"{exc.__class__.__name__}: {exc}"[:500]})
            return
        with Session(bind=engine, expire_on_commit=False) as own_session:
            message = _save_answer(own_session, turn, "".join(parts))
        yield _sse("done", {"conversation_id": turn.conversation.id, "message_id": message.id})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get(
    "/workspaces/{workspace_id}/search",
    response_model=SearchResponse,
    summary="Semantic search over a workspace (retrieval only, no LLM)",
)
async def search(
    q: str = Query(min_length=1, max_length=1000),
    k: int = Query(default=5, ge=1, le=20),
    workspace: Workspace = Depends(get_workspace),
    pipeline: RagPipeline = Depends(get_pipeline),
) -> SearchResponse:
    hits = await run_in_threadpool(pipeline.retrieve, workspace.id, q, k)
    return SearchResponse(
        query=q,
        results=[Source.model_validate(source_payload(n, h)) for n, h in enumerate(hits, 1)],
    )


@router.get(
    "/workspaces/{workspace_id}/conversations",
    response_model=list[ConversationOut],
    summary="List conversations in a workspace (newest first)",
)
def list_conversations(
    workspace: Workspace = Depends(get_workspace), session: Session = Depends(get_session)
) -> list[Conversation]:
    return list(
        session.scalars(
            select(Conversation)
            .where(Conversation.workspace_id == workspace.id)
            .order_by(Conversation.id.desc())
        )
    )


@router.get(
    "/conversations/{conversation_id}",
    response_model=ConversationDetail,
    summary="Get a conversation with its full message history",
)
def get_conversation(conversation_id: int, session: Session = Depends(get_session)) -> Conversation:
    conversation = session.get(Conversation, conversation_id)
    if conversation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    return conversation


@router.delete(
    "/conversations/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a conversation",
)
def delete_conversation(conversation_id: int, session: Session = Depends(get_session)) -> Response:
    conversation = session.get(Conversation, conversation_id)
    if conversation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    session.delete(conversation)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
