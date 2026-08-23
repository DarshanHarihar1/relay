from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from google.cloud.firestore_v1 import AsyncClient

from app.auth import CurrentUser, require_current_user
from app.config import Settings
from app.contracts import ApprovalDecisionRequest, ApprovalDecisionResponse, Problem
from app.repositories.actions import ActionRepository, ApprovalVersionConflict, FirestoreActionRepository


router = APIRouter()


async def get_action_repository(request: Request) -> ActionRepository:
    repository = getattr(request.app.state, "action_repository", None)
    if repository is not None:
        return repository

    project_id = Settings.from_env().google_cloud_project
    if project_id is None:
        raise HTTPException(status_code=503)
    repository = FirestoreActionRepository(AsyncClient(project=project_id))
    request.app.state.action_repository = repository
    return repository


@router.post(
    "/v1/approvals/{approval_id}/decision",
    response_model=ApprovalDecisionResponse,
    responses={401: {"model": Problem}, 404: {"model": Problem}, 409: {"model": Problem}},
)
async def decide_approval(
    approval_id: str,
    decision: ApprovalDecisionRequest,
    request: Request,
    current_user: CurrentUser = Depends(require_current_user),
    repository: ActionRepository = Depends(get_action_repository),
) -> ApprovalDecisionResponse:
    if decision.approval_id != approval_id:
        raise HTTPException(status_code=404)
    try:
        return await repository.decide_approval(
            current_user.uid,
            decision,
            getattr(request.state, "correlation_id", "unknown-correlation"),
        )
    except ApprovalVersionConflict as error:
        raise HTTPException(status_code=409) from error
    except LookupError as error:
        raise HTTPException(status_code=404) from error
