from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Cookie, HTTPException, Response, status

from app.schemas.sessions import SessionOut, SessionProgressIn
from app.services.session_service import (
    SESSION_COOKIE_NAME,
    SessionAuthenticationError,
    get_current_session,
    revoke_device_session,
    update_session_progress,
)


router = APIRouter()


@router.get("/me", response_model=SessionOut)
async def read_current_session(
    session_token: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> SessionOut:
    try:
        return SessionOut(**get_current_session(session_token))
    except SessionAuthenticationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@router.patch("/me/progress", response_model=SessionOut)
async def update_current_progress(
    body: SessionProgressIn,
    session_token: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> SessionOut:
    try:
        result = update_session_progress(
            session_token,
            onboarding_step=body.onboarding_step,
            wallpaper_url=body.wallpaper_url,
        )
    except SessionAuthenticationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    return SessionOut(**result)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout_current_session(
    response: Response,
    session_token: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> Response:
    revoke_device_session(session_token)
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    response.status_code = status.HTTP_204_NO_CONTENT
    return response
