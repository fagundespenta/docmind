from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import Document, Workspace
from app.deps import get_pipeline, get_session, get_workspace
from app.rag import RagPipeline
from app.schemas import WorkspaceCreate, WorkspaceOut

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


def _out(workspace: Workspace, document_count: int) -> WorkspaceOut:
    out = WorkspaceOut.model_validate(workspace)
    out.document_count = document_count
    return out


@router.get("", response_model=list[WorkspaceOut], summary="List workspaces")
def list_workspaces(session: Session = Depends(get_session)) -> list[WorkspaceOut]:
    rows = session.execute(
        select(Document.workspace_id, func.count(Document.id)).group_by(Document.workspace_id)
    ).all()
    counts = {ws_id: n for ws_id, n in rows}
    workspaces = session.scalars(select(Workspace).order_by(Workspace.id)).all()
    return [_out(w, counts.get(w.id, 0)) for w in workspaces]


@router.post(
    "", response_model=WorkspaceOut, status_code=status.HTTP_201_CREATED, summary="Create workspace"
)
def create_workspace(
    body: WorkspaceCreate, session: Session = Depends(get_session)
) -> WorkspaceOut:
    name = body.name.strip()
    if session.scalar(select(Workspace).where(Workspace.name == name)):
        raise HTTPException(status.HTTP_409_CONFLICT, f"Workspace '{name}' already exists")
    workspace = Workspace(name=name, description=body.description.strip())
    session.add(workspace)
    session.commit()
    return _out(workspace, 0)


@router.get("/{workspace_id}", response_model=WorkspaceOut, summary="Get workspace")
def get_workspace_detail(workspace: Workspace = Depends(get_workspace)) -> WorkspaceOut:
    return _out(workspace, len(workspace.documents))


@router.delete(
    "/{workspace_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete workspace with all its documents and conversations",
)
def delete_workspace(
    workspace: Workspace = Depends(get_workspace),
    session: Session = Depends(get_session),
    pipeline: RagPipeline = Depends(get_pipeline),
) -> Response:
    pipeline.drop_workspace(workspace.id)
    session.delete(workspace)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
