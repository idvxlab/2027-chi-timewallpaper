from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import (
    APIRouter,
    Cookie,
    File,
    Form,
    HTTPException,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)

from app.core.character_asset_events import character_asset_event_hub
from app.schemas.agent import (
    CharacterAssetListOut,
    CharacterAssetOut,
    RelationshipCharacterAssetsOut,
)
from app.services.character_asset_service import character_asset_service
from app.services.session_service import (
    SESSION_COOKIE_NAME,
    SessionAuthenticationError,
    get_current_session,
)


router = APIRouter()


@router.websocket("/current-relationship/events")
async def current_relationship_character_asset_events(websocket: WebSocket) -> None:
    session_token = websocket.cookies.get(SESSION_COOKIE_NAME)
    try:
        identity = get_current_session(session_token)
    except SessionAuthenticationError:
        await websocket.close(code=4401)
        return

    relationship_id = str(identity["relationship_id"])
    queue = character_asset_event_hub.subscribe(relationship_id)
    await websocket.accept()

    async def send_events() -> None:
        while True:
            event = await queue.get()
            await websocket.send_json(event)

    sender = asyncio.create_task(send_events())
    try:
        await websocket.send_json(
            {
                "type": "character_asset_changed",
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
        character_asset_event_hub.unsubscribe(relationship_id, queue)


def _to_output(row) -> CharacterAssetOut:
    return CharacterAssetOut(
        asset_id=row.asset_id,
        user_id=row.user_id,
        relationship_id=row.relationship_id,
        role=row.role,
        status=row.status,
        style_version=row.style_version,
        source_image_url=row.source_image_url,
        style_reference_url=row.style_reference_url,
        master_image_url=row.master_image_url,
        portrait_image_url=row.portrait_image_url,
        half_body_image_url=row.half_body_image_url,
        full_body_image_url=row.full_body_image_url,
        is_active=row.is_active,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _current_identity(session_token: Optional[str]) -> dict[str, object]:
    try:
        return get_current_session(session_token)
    except SessionAuthenticationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@router.post("/me", response_model=CharacterAssetOut)
async def create_my_character_asset(
    image: UploadFile = File(...),
    session_token: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> CharacterAssetOut:
    identity = _current_identity(session_token)
    source_bytes = await image.read()
    row = await character_asset_service.create(
        user_id=str(identity["user_id"]),
        role=str(identity["viewer_role"]),
        relationship_id=str(identity["relationship_id"]),
        source_bytes=source_bytes,
        source_content_type=image.content_type or "image/png",
        source_filename=image.filename,
    )
    return _to_output(row)


@router.get(
    "/current-relationship",
    response_model=RelationshipCharacterAssetsOut,
)
async def list_current_relationship_character_assets(
    session_token: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> RelationshipCharacterAssetsOut:
    identity = _current_identity(session_token)
    relationship_id = str(identity["relationship_id"])
    rows = character_asset_service.list_latest_for_relationship(relationship_id)
    by_role = {row.role: _to_output(row) for row in rows}
    elder = by_role.get("elder")
    child = by_role.get("child")
    return RelationshipCharacterAssetsOut(
        relationship_id=relationship_id,
        ready=bool(
            elder
            and child
            and elder.status == "ready"
            and child.status == "ready"
        ),
        elder=elder,
        child=child,
    )


@router.post("", response_model=CharacterAssetOut)
async def create_character_asset(
    image: UploadFile = File(...),
    user_id: str = Form(..., alias="userId"),
    role: str = Form(...),
    relationship_id: Optional[str] = Form(default=None, alias="relationshipId"),
    style_reference: Optional[UploadFile] = File(default=None, alias="styleReference"),
) -> CharacterAssetOut:
    source_bytes = await image.read()
    style_bytes = await style_reference.read() if style_reference else None
    row = await character_asset_service.create(
        user_id=user_id,
        role=role,
        relationship_id=relationship_id,
        source_bytes=source_bytes,
        source_content_type=image.content_type or "image/png",
        source_filename=image.filename,
        style_reference_bytes=style_bytes,
        style_reference_content_type=(style_reference.content_type if style_reference else "image/png") or "image/png",
        style_reference_filename=style_reference.filename if style_reference else None,
    )
    return _to_output(row)


@router.get("/by-user/{user_id}", response_model=CharacterAssetOut)
async def get_character_asset(user_id: str, role: Optional[str] = None) -> CharacterAssetOut:
    return _to_output(character_asset_service.get_latest(user_id=user_id, role=role))


@router.get("/by-relationship/{relationship_id}", response_model=CharacterAssetListOut)
async def list_relationship_character_assets(relationship_id: str) -> CharacterAssetListOut:
    rows = character_asset_service.list_for_relationship(relationship_id)
    return CharacterAssetListOut(items=[_to_output(row) for row in rows])
