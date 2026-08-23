from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import Header, HTTPException, status
from pydantic import BaseModel

from app.config import Settings


class CurrentUser(BaseModel):
    uid: str
    email: str | None


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid Firebase ID token",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _verify_firebase_id_token(token: str, project_id: str) -> Mapping[str, Any]:
    import firebase_admin
    from firebase_admin import auth

    try:
        app = firebase_admin.get_app()
    except ValueError:
        app = firebase_admin.initialize_app(options={"projectId": project_id})

    return auth.verify_id_token(token, app=app, check_revoked=False)


async def require_current_user(authorization: str | None = Header(default=None)) -> CurrentUser:
    if authorization is None or not authorization.startswith("Bearer "):
        raise _unauthorized()

    token = authorization.removeprefix("Bearer ").strip()
    if not token or " " in token:
        raise _unauthorized()

    project_id = Settings.from_env().firebase_project_id
    if project_id is None:
        raise _unauthorized()

    try:
        claims = _verify_firebase_id_token(token, project_id)
    except Exception as error:
        raise _unauthorized() from error

    if claims.get("aud") != project_id:
        raise _unauthorized()
    uid = claims.get("uid") or claims.get("sub")
    if not isinstance(uid, str) or not uid:
        raise _unauthorized()
    email = claims.get("email")
    if email is not None and not isinstance(email, str):
        raise _unauthorized()
    return CurrentUser(uid=uid, email=email)
