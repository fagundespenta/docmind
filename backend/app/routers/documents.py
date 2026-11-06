from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import Document, Workspace
from app.deps import get_pipeline, get_session, get_settings, get_workspace
from app.ingestion.parsers import SUPPORTED_EXTENSIONS, extension_of
from app.rag import RagPipeline
from app.schemas import DocumentOut

logger = logging.getLogger(__name__)
router = APIRouter(tags=["documents"])


@router.post(
    "/workspaces/{workspace_id}/documents",
    response_model=DocumentOut,
    status_code=status.HTTP_201_CREATED,
    summary="Upload and index a document (PDF, DOCX, TXT or MD)",
)
def upload_document(
    file: UploadFile = File(...),
    workspace: Workspace = Depends(get_workspace),
    session: Session = Depends(get_session),
    pipeline: RagPipeline = Depends(get_pipeline),
    settings: Settings = Depends(get_settings),
) -> Document:
    filename = (file.filename or "upload").replace("\\", "/").rsplit("/", 1)[-1][:255]
    if extension_of(filename) not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            f"Unsupported file type. Allowed: {', '.join(sorted(SUPPORTED_EXTENSIONS))}",
        )
    limit = settings.max_upload_mb * 1024 * 1024
    data = file.file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, f"File exceeds {settings.max_upload_mb} MB")
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "File is empty")

    document = Document(
        workspace_id=workspace.id,
        filename=filename,
        content_type=file.content_type or "",
        size_bytes=len(data),
    )
    session.add(document)
    session.commit()

    try:
        result = pipeline.ingest(workspace.id, document.id, filename, data)
    except Exception as exc:
        logger.exception("Failed to ingest %s", filename)
        pipeline.delete_document(workspace.id, document.id)
        document.status = "failed"
        document.error = str(exc)[:500] or exc.__class__.__name__
        session.commit()
        raise HTTPException(422, f"Could not process document: {document.error}") from exc

    document.status = "ready"
    document.page_count = result.page_count
    document.chunk_count = result.chunk_count
    session.commit()
    return document


@router.get(
    "/workspaces/{workspace_id}/documents",
    response_model=list[DocumentOut],
    summary="List documents in a workspace",
)
def list_documents(
    workspace: Workspace = Depends(get_workspace), session: Session = Depends(get_session)
) -> list[Document]:
    return list(
        session.scalars(
            select(Document).where(Document.workspace_id == workspace.id).order_by(Document.id)
        )
    )


@router.delete(
    "/documents/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a document and its indexed chunks",
)
def delete_document(
    document_id: int,
    session: Session = Depends(get_session),
    pipeline: RagPipeline = Depends(get_pipeline),
) -> Response:
    document = session.get(Document, document_id)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    pipeline.delete_document(document.workspace_id, document.id)
    session.delete(document)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
