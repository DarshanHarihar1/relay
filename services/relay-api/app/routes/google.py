from __future__ import annotations

import logging
from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import RedirectResponse

from app.adapters.gmail import GmailAdapter, GmailRetryableError, GmailTerminalError
from app.adapters.google_auth import GoogleConnectionRequest, GoogleOAuthService
from app.adapters.google_people import GooglePeopleAdapter
from app.auth import CurrentUser, require_current_user
from app.domain.context import PickerContact
from app.security import FernetFieldCipher
from app.services.gmail_ingestion import (
    GmailWatchService,
    InMemoryGmailIngestionRepository,
)
from app.services.contact_selection import (
    ContactChoice,
    ContactSelectionService,
    ContactsPermissionRequired,
    InMemorySelectedContactStore,
)
from app.settings import GoogleOAuthSettings


logger = logging.getLogger("relay.google")

router = APIRouter(prefix="/v1/google", tags=["google"])
pickup_router = APIRouter(prefix="/v1/commitments", tags=["commitments"])


@lru_cache(maxsize=1)
def get_google_oauth_service() -> GoogleOAuthService:
    settings = GoogleOAuthSettings.from_env()
    from os import getenv

    encryption_key = getenv("APP_ENCRYPTION_KEY")
    if not encryption_key:
        raise RuntimeError("Missing Google OAuth configuration: APP_ENCRYPTION_KEY")
    return GoogleOAuthService(settings=settings, cipher=FernetFieldCipher(encryption_key))


@lru_cache(maxsize=1)
def get_gmail_watch_service() -> GmailWatchService:
    settings = GoogleOAuthSettings.from_env()
    oauth = get_google_oauth_service()
    return GmailWatchService(
        repository=InMemoryGmailIngestionRepository(oauth),
        gmail=GmailAdapter(
            client_id=settings.client_id,
            client_secret=settings.client_secret,
            topic=settings.gmail_topic,
            refresh_token_reader=oauth.decrypt_refresh_token,
        ),
    )


@lru_cache(maxsize=1)
def get_contact_selection_service() -> ContactSelectionService:
    settings = GoogleOAuthSettings.from_env()
    from os import getenv

    encryption_key = getenv("APP_ENCRYPTION_KEY")
    if not encryption_key:
        raise RuntimeError("Missing Google OAuth configuration: APP_ENCRYPTION_KEY")
    oauth = get_google_oauth_service()
    return ContactSelectionService(
        connections=oauth,
        people=GooglePeopleAdapter(
            client_id=settings.client_id,
            client_secret=settings.client_secret,
            refresh_token_reader=oauth.decrypt_refresh_token,
        ),
        selections=InMemorySelectedContactStore(),
        cipher=FernetFieldCipher(encryption_key),
    )


@router.get("/connect")
async def begin_google_connection(
    request: GoogleConnectionRequest = Depends(),
    current_user: CurrentUser = Depends(require_current_user),
    service: GoogleOAuthService = Depends(get_google_oauth_service),
) -> Response:
    location = await service.begin_google_connection(current_user.uid, request)
    disclosure = "Relay will only use Contacts so you can choose one person for a pickup."
    return Response(
        content=disclosure if request.enable_contacts_picker else "",
        status_code=307,
        headers={"Location": location},
    )


@router.get("/callback")
async def complete_google_connection(
    code: str,
    state: str,
    service: GoogleOAuthService = Depends(get_google_oauth_service),
    watches: GmailWatchService = Depends(get_gmail_watch_service),
) -> RedirectResponse:
    try:
        connection = await service.complete_google_connection(code=code, state=state)
    except ValueError as error:
        raise HTTPException(status_code=400, detail="Google connection could not be completed") from error
    try:
        await watches.register_gmail_watch(connection.user_id)
    except (GmailRetryableError, GmailTerminalError):
        # The account is connected. Watch registration is retried by renewal.
        logger.warning("gmail_watch_registration_deferred")
    return RedirectResponse(location="/connections/google", status_code=303)


@router.delete("/connection", status_code=204, response_class=Response)
async def disconnect_google(
    current_user: CurrentUser = Depends(require_current_user),
    service: GoogleOAuthService = Depends(get_google_oauth_service),
) -> Response:
    await service.disconnect_google(current_user.uid)
    return Response(status_code=204)


@router.get("/contacts", response_model=list[PickerContact])
async def search_google_contacts(
    query: str,
    current_user: CurrentUser = Depends(require_current_user),
    service: ContactSelectionService = Depends(get_contact_selection_service),
) -> list[PickerContact]:
    try:
        return await service.search_picker_contacts(user_id=current_user.uid, query=query)
    except ContactsPermissionRequired as error:
        raise HTTPException(status_code=409, detail="CONTACTS_PERMISSION_REQUIRED") from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail="Invalid contact picker request") from error


@pickup_router.put("/{commitment_id}/pickup-contact", status_code=204, response_class=Response)
async def select_pickup_contact(
    commitment_id: str,
    choice: ContactChoice,
    current_user: CurrentUser = Depends(require_current_user),
    service: ContactSelectionService = Depends(get_contact_selection_service),
) -> Response:
    try:
        await service.select_pickup_contact(
            user_id=current_user.uid, commitment_id=commitment_id, choice=choice
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail="Invalid pickup contact") from error
    return Response(status_code=204)


@pickup_router.delete("/{commitment_id}/pickup-contact", status_code=204, response_class=Response)
async def remove_pickup_contact(
    commitment_id: str,
    current_user: CurrentUser = Depends(require_current_user),
    service: ContactSelectionService = Depends(get_contact_selection_service),
) -> Response:
    try:
        await service.remove_pickup_contact(user_id=current_user.uid, commitment_id=commitment_id)
    except ValueError as error:
        raise HTTPException(status_code=422, detail="Invalid pickup contact") from error
    return Response(status_code=204)
