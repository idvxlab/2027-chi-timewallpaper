from __future__ import annotations

from datetime import timezone
from typing import Optional

import asyncio

from fastapi import APIRouter, Cookie, HTTPException, Query, WebSocket, WebSocketDisconnect

from app.core.wallpaper_events import wallpaper_event_hub
from app.core.config import settings
from app.db.models import WallpaperRevision
from app.db.session import SessionLocal
from app.schemas.wallpapers import (
    CurrentWallpaperOut,
    WallpaperRevisionListOut,
    WallpaperRevisionOut,
)
from app.services.relationship_wallpaper_service import relationship_wallpaper_service
from app.services.session_service import (
    SESSION_COOKIE_NAME,
    SessionAuthenticationError,
    get_current_session,
)


router = APIRouter()


def _session_identity(session_token: Optional[str]) -> dict:
    try:
        return get_current_session(session_token)
    except SessionAuthenticationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@router.websocket("/current/events")
async def current_wallpaper_events(websocket: WebSocket) -> None:
    session_token = websocket.cookies.get(SESSION_COOKIE_NAME)
    try:
        identity = get_current_session(session_token)
    except SessionAuthenticationError:
        await websocket.close(code=4401)
        return

    relationship_id = str(identity["relationship_id"])
    queue = wallpaper_event_hub.subscribe(relationship_id)
    await websocket.accept()

    async def send_events() -> None:
        while True:
            event = await queue.get()
            await websocket.send_json(event)

    sender = asyncio.create_task(send_events())
    try:
        await websocket.send_json(
            {
                "type": "wallpaper_state_changed",
                "relationshipId": relationship_id,
                "status": "connected",
            }
        )
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        sender.cancel()
        wallpaper_event_hub.unsubscribe(relationship_id, queue)


@router.get("/current", response_model=CurrentWallpaperOut)
async def get_current_wallpaper(
    session_token: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> CurrentWallpaperOut:
    identity = _session_identity(session_token)

    relationship_id = str(identity["relationship_id"])
    viewer_role = str(identity["viewer_role"])
    state = relationship_wallpaper_service.get(relationship_id)
    if state is None or state.stage != "wallpaper_active":
        ready = relationship_wallpaper_service.characters_ready(relationship_id)
        return CurrentWallpaperOut(
            relationship_id=relationship_id,
            viewer_role=viewer_role,
            stage="characters_ready" if ready else "awaiting_characters",
            status=state.status if state is not None else "idle",
            base_scene_url=settings.static_base_scene_url if ready else "",
            wallpaper_url=settings.static_base_scene_url if ready else "",
        )

    shared_url = state.child_view_url or state.elder_view_url
    base_scene_url = settings.static_base_scene_url
    return CurrentWallpaperOut(
        relationship_id=relationship_id,
        viewer_role=viewer_role,
        stage=state.stage,
        status=state.status,
        version=state.version,
        base_scene_url=base_scene_url,
        wallpaper_url=shared_url or base_scene_url,
        latest_run_id=state.latest_run_id,
        error=state.last_error,
    )


@router.get("/revisions", response_model=WallpaperRevisionListOut)
async def get_wallpaper_revisions(
    limit: int = Query(default=100, ge=1, le=200),
    session_token: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> WallpaperRevisionListOut:
    identity = _session_identity(session_token)
    relationship_id = str(identity["relationship_id"])
    viewer_role = str(identity["viewer_role"])

    with SessionLocal() as session:
        rows = (
            session.query(WallpaperRevision)
            .filter(
                WallpaperRevision.relationship_id == relationship_id,
                WallpaperRevision.view_role == viewer_role,
            )
            .order_by(
                WallpaperRevision.event_seq.desc(),
                WallpaperRevision.created_at.desc(),
            )
            .limit(limit)
            .all()
        )

    items = [
        WallpaperRevisionOut(
            revision_id=row.revision_id,
            event_seq=row.event_seq,
            image_url=row.image_url,
            created_at=row.created_at.replace(tzinfo=timezone.utc),
        )
        for row in reversed(rows)
    ]
    return WallpaperRevisionListOut(
        relationship_id=relationship_id,
        viewer_role=viewer_role,
        items=items,
    )
