from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from google.cloud.firestore_v1 import AsyncClient

from app.auth import CurrentUser, require_current_user
from app.config import Settings
from app.contracts import ApprovalDecisionRequest, ApprovalDecisionResponse, HandoffResponse, Problem
from app.providers.uber import UberDeepLinkBuilder
from app.repositories.actions import ActionRepository, ApprovalVersionConflict, FirestoreActionRepository
from app.services.approval_service import ApprovalService


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
    try:
        return await ApprovalService(repository).decide(
            approval_id,
            user_id=current_user.uid,
            request=decision,
            correlation_id=getattr(request.state, "correlation_id", "unknown-correlation"),
        )
    except ApprovalVersionConflict as error:
        raise HTTPException(status_code=409) from error
    except LookupError as error:
        raise HTTPException(status_code=404) from error


@router.post(
    "/v1/actions/{action_id}/open-handoff",
    response_model=HandoffResponse,
    responses={401: {"model": Problem}, 404: {"model": Problem}, 409: {"model": Problem}},
)
async def open_uber_handoff(
    action_id: str,
    request: Request,
    current_user: CurrentUser = Depends(require_current_user),
    repository: ActionRepository = Depends(get_action_repository),
) -> HandoffResponse:
    action = await repository.get(current_user.uid, action_id)
    if action is None:
        raise HTTPException(status_code=404)
    try:
        client_id = Settings.from_env().uber_client_id or "relay-client"
        url = UberDeepLinkBuilder(client_id=client_id).build(action)
        return await repository.open_handoff(
            current_user.uid,
            action_id,
            url,
            getattr(request.state, "correlation_id", "unknown-correlation"),
        )
    except ApprovalVersionConflict as error:
        raise HTTPException(status_code=409) from error
    except ValueError as error:
        raise HTTPException(status_code=409) from error
