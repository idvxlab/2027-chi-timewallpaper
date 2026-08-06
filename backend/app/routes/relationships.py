import asyncio

from fastapi import (
    APIRouter,
    HTTPException,
    Response,
    WebSocket,
    WebSocketDisconnect,
)

from app.core.relationship_events import relationship_event_hub
from app.schemas.relationships import (
    JoinRelationshipIn,
    JoinRelationshipOut,
    RelationshipInvitationOut,
)
from app.services.session_service import (
    SESSION_COOKIE_NAME,
    SessionAuthenticationError,
    get_current_session,
    issue_device_session,
    set_device_session_cookie,
)
from app.services.user_context import (
    RelationshipInvitationNotFoundError,
    RelationshipJoinConflictError,
    get_relationship_invitation,
    join_relationship_by_invite,
)


router = APIRouter()


@router.websocket("/current/events")
async def current_relationship_events(websocket: WebSocket) -> None:
    try:
        identity = get_current_session(websocket.cookies.get(SESSION_COOKIE_NAME))
    except SessionAuthenticationError:
        await websocket.close(code=4401)
        return

    relationship_id = str(identity["relationship_id"])
    queue = relationship_event_hub.subscribe(relationship_id)
    await websocket.accept()

    async def send_events() -> None:
        while True:
            event = await queue.get()
            await websocket.send_json(event)

    sender = asyncio.create_task(send_events())
    try:
        await websocket.send_json(
            {
                "type": "relationship_status",
                "relationshipId": relationship_id,
                "status": identity["relationship_status"],
            }
        )
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        sender.cancel()
        relationship_event_hub.unsubscribe(relationship_id, queue)


@router.get("/invitations/{invite_code}", response_model=RelationshipInvitationOut)
async def read_invitation(invite_code: str) -> RelationshipInvitationOut:
    try:
        result = get_relationship_invitation(invite_code)
    except RelationshipInvitationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RelationshipInvitationOut(**result)


@router.post("/join", response_model=JoinRelationshipOut)
async def join_relationship(
    body: JoinRelationshipIn,
    response: Response,
) -> JoinRelationshipOut:
    try:
        result = join_relationship_by_invite(
            invite_code=body.invite_code,
            viewer_role=body.viewer_role,
            display_name=body.display_name,
            gender=body.gender,
        )
    except RelationshipInvitationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RelationshipJoinConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    token = issue_device_session(
        user_id=str(result["user_id"]),
        relationship_id=str(result["relationship_id"]),
    )
    set_device_session_cookie(response, token)
    await relationship_event_hub.publish_connected(str(result["relationship_id"]))
    return JoinRelationshipOut(**result)
