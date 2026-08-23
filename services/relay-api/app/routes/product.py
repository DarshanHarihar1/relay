from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from google.cloud.firestore_v1 import AsyncClient

from app.auth import CurrentUser, require_current_user
from app.config import Settings
from app.domain.product import (
    ActionAuditView,
    DashboardView,
    PickupContactCommand,
    PickupContactResponse,
    RegisterDeviceRequest,
)
from app.repositories.firestore import FirestoreDeviceRepository
from app.repositories.product import FirestoreProductRepository, PickupVersionConflict, ProductRepository
from app.services.audit_projection import AuditProjectionService
from app.services.contact_selection import (
    ContactSelectionService,
    ContactsPermissionRequired,
)
from app.services.dashboard_projection import DashboardProjectionService
from app.services.pickup_commitment import PickupCommitmentService
from app.routes.google import get_contact_selection_service, get_firestore_client
from app.security import FernetFieldCipher
from app.services.notifications import FirebaseNotificationPort, NotificationService


router = APIRouter()


def get_product_repository(request: Request) -> ProductRepository:
    repository = getattr(request.app.state, "product_repository", None)
    if repository is not None:
        return repository
    project_id = Settings.from_env().google_cloud_project
    if project_id is None:
        raise HTTPException(status_code=503)
    repository = FirestoreProductRepository(AsyncClient(project=project_id))
    request.app.state.product_repository = repository
    return repository


def get_dashboard_projection_service(
    repository: ProductRepository = Depends(get_product_repository),
) -> DashboardProjectionService:
    return DashboardProjectionService(repository)


def get_audit_projection_service(
    repository: ProductRepository = Depends(get_product_repository),
) -> AuditProjectionService:
    return AuditProjectionService(repository)


def get_pickup_commitment_service(
    repository: ProductRepository = Depends(get_product_repository),
    selection: ContactSelectionService = Depends(get_contact_selection_service),
) -> PickupCommitmentService:
    return PickupCommitmentService(repository, selection)


def get_notification_service(request: Request) -> NotificationService:
    service = getattr(request.app.state, "notification_service", None)
    if service is not None:
        return service
    encryption_key = Settings.from_env().app_encryption_key
    if encryption_key is None:
        raise HTTPException(status_code=503)
    service = NotificationService(
        repository=FirestoreDeviceRepository(get_firestore_client()),
        cipher=FernetFieldCipher(encryption_key),
        sender=FirebaseNotificationPort(),
    )
    request.app.state.notification_service = service
    return service


@router.get("/v1/dashboard", response_model=DashboardView)
async def get_dashboard(
    current_user: CurrentUser = Depends(require_current_user),
    service: DashboardProjectionService = Depends(get_dashboard_projection_service),
) -> DashboardView:
    return await service.build_dashboard(user_id=current_user.uid)


@router.post("/v1/commitments/{commitment_id}/pickup-contact", response_model=PickupContactResponse)
async def declare_pickup_contact(
    commitment_id: str,
    command: PickupContactCommand,
    request: Request,
    current_user: CurrentUser = Depends(require_current_user),
    service: PickupCommitmentService = Depends(get_pickup_commitment_service),
) -> PickupContactResponse:
    try:
        return await service.submit_pickup_contact(
            user_id=current_user.uid,
            commitment_id=commitment_id,
            command=command,
            correlation_id=getattr(request.state, "correlation_id", "unknown-correlation"),
        )
    except LookupError as error:
        raise HTTPException(status_code=404) from error
    except (PickupVersionConflict, ContactsPermissionRequired, ValueError) as error:
        raise HTTPException(status_code=409, detail="COMMITMENT_VERSION_CONFLICT") from error


@router.get("/v1/actions/{action_id}/audit", response_model=ActionAuditView)
async def get_action_audit(
    action_id: str,
    current_user: CurrentUser = Depends(require_current_user),
    service: AuditProjectionService = Depends(get_audit_projection_service),
) -> ActionAuditView:
    try:
        return await service.get_action_audit(user_id=current_user.uid, action_id=action_id)
    except LookupError as error:
        raise HTTPException(status_code=404) from error


@router.post("/v1/devices", status_code=204, response_class=Response)
async def register_device(
    command: RegisterDeviceRequest,
    current_user: CurrentUser = Depends(require_current_user),
    service: NotificationService = Depends(get_notification_service),
) -> Response:
    await service.register_device(
        user_id=current_user.uid,
        token=command.token,
        platform=command.platform,
    )
    return Response(status_code=204)


__all__ = [
    "get_audit_projection_service",
    "get_dashboard_projection_service",
    "get_pickup_commitment_service",
    "get_product_repository",
    "get_notification_service",
    "router",
]
