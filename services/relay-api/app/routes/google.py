from __future__ import annotations

from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import RedirectResponse

from app.adapters.google_auth import GoogleConnectionRequest, GoogleOAuthService
from app.auth import CurrentUser, require_current_user
from app.security import FernetFieldCipher
from app.settings import GoogleOAuthSettings


router = APIRouter(prefix="/v1/google", tags=["google"])


@lru_cache(maxsize=1)
def get_google_oauth_service() -> GoogleOAuthService:
    settings = GoogleOAuthSettings.from_env()
    from os import getenv

    encryption_key = getenv("APP_ENCRYPTION_KEY")
    if not encryption_key:
        raise RuntimeError("Missing Google OAuth configuration: APP_ENCRYPTION_KEY")
    return GoogleOAuthService(settings=settings, cipher=FernetFieldCipher(encryption_key))


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
) -> RedirectResponse:
    try:
        await service.complete_google_connection(code=code, state=state)
    except ValueError as error:
        raise HTTPException(status_code=400, detail="Google connection could not be completed") from error
    return RedirectResponse(location="/connections/google", status_code=303)


@router.delete("/connection", status_code=204, response_class=Response)
async def disconnect_google(
    current_user: CurrentUser = Depends(require_current_user),
    service: GoogleOAuthService = Depends(get_google_oauth_service),
) -> Response:
    await service.disconnect_google(current_user.uid)
    return Response(status_code=204)
