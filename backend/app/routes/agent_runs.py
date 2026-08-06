from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Cookie, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect

from app.agents import MultiAgentOrchestrator
from app.agents.chatbot_agent import ChatBotAudioContext
from app.core.config import settings
from app.core.wallpaper_events import wallpaper_event_hub
from app.db.models import CharacterAsset, ReferenceImageLog
from app.db.session import SessionLocal
from app.schemas.agent import (
    AgentRunCreateOut,
    AgentRunResult,
    AgentRunTextIn,
    ComfortReplyIn,
    ComfortReplyOut,
    MemoryObjectsIn,
    MemoryObjectsOut,
    ImageGenerationResult,
    WallpaperViewsResult,
)
from app.services.user_context import normalize_user_context
from app.services.relationship_wallpaper_service import relationship_wallpaper_service
from app.services.wallpaper_generation_service import wallpaper_generation_service
from app.services.wallpaper_render_worker import wallpaper_render_worker
from app.services.render_queue_service import render_queue_service
from app.services.session_service import (
    SESSION_COOKIE_NAME,
    SessionAuthenticationError,
    get_current_session,
)
from app.services.doubao_streaming_asr_service import (
    DoubaoStreamingASRSession,
    streaming_profile,
)
from app.services.voice_event_service import VoiceEventClaim, voice_event_service

router = APIRouter()
orchestrator = MultiAgentOrchestrator()
wallpaper_render_worker.image_agent = orchestrator.image_generation_agent
REFERENCE_DIR = Path(settings.storage_local_dir) / "references"
REFERENCE_EXTS = ("png", "jpg", "jpeg", "webp")


def _attach_primary_view(
    result: AgentRunResult,
    *,
    speaker_role: str,
    image_url: str,
    image_result: ImageGenerationResult,
) -> None:
    result.image_generation = image_result
    result.wallpaper_views = WallpaperViewsResult(
        child_view_url=image_url,
        elder_view_url=image_url,
        speaker_role=speaker_role,
    )
    result.status = "done"
    if result.steps:
        result.steps[-1].status = "done"
    result.updated_at = datetime.utcnow()


def _existing_voice_response(claim: VoiceEventClaim) -> AgentRunCreateOut | None:
    if claim.created:
        return None
    if claim.result_payload:
        result = AgentRunResult(**claim.result_payload)
        return AgentRunCreateOut(
            run_id=claim.run_id,
            status=claim.status,
            result=result,
            request_id=claim.request_id,
            event_id=claim.event_id,
            event_seq=claim.event_seq,
        )
    raise HTTPException(
        status_code=409,
        detail={
            "message": "This voice request is already being processed",
            "requestId": claim.request_id,
            "eventId": claim.event_id,
            "eventSeq": claim.event_seq,
            "status": claim.status,
        },
    )


def _voice_response(
    result: AgentRunResult,
    claim: VoiceEventClaim,
) -> AgentRunCreateOut:
    return AgentRunCreateOut(
        run_id=result.run_id,
        status=result.status,
        result=result,
        request_id=claim.request_id,
        event_id=claim.event_id,
        event_seq=claim.event_seq,
    )


async def _analyze_and_render_current_wallpaper(
    *,
    audio: bytes,
    content_type: str,
    filename: str,
    identity: dict[str, object],
    voice_claim: VoiceEventClaim,
    prepared_audio_context: ChatBotAudioContext | None = None,
) -> AgentRunResult:
    user_id = str(identity["user_id"])
    relationship_id = str(identity["relationship_id"])
    speaker_role = str(identity["viewer_role"])
    state = relationship_wallpaper_service.get(relationship_id)
    speaker_view_url = (
        state.child_view_url or state.elder_view_url if state is not None else ""
    )
    if state is None or state.stage != "wallpaper_active" or not speaker_view_url:
        raise HTTPException(
            status_code=409,
            detail="The relationship's shared wallpaper must be ready before updating",
        )

    references = _resolve_role_references(relationship_id=relationship_id)
    if not references.get(speaker_role):
        raise HTTPException(
            status_code=409,
            detail="The speaker character reference must be ready",
        )

    result = await orchestrator.run_audio(
        audio,
        content_type=content_type,
        filename=filename,
        user_id=user_id,
        relationship_id=relationship_id,
        role_reference_images=references,
        generation_stage="subsequent_update",
        speaker_role=speaker_role,
        analyze_only=True,
        prepared_audio_context=prepared_audio_context,
        run_id=voice_claim.run_id,
        event_seq=voice_claim.event_seq,
    )
    voice_event_service.mark_analyzed(voice_claim.event_id, result)
    tasks = wallpaper_generation_service.create_tasks(
        result,
        speaker_role=speaker_role,
        generation_stage="subsequent_update",
        role_reference_images=references,
        event_seq=voice_claim.event_seq,
    )
    if settings.render_queue_enabled:
        await _enqueue_wallpaper_render(
            result=result,
            voice_claim=voice_claim,
            relationship_id=relationship_id,
            task_id=tasks.primary_task_id,
        )
        return result
    primary_image = await wallpaper_render_worker.render_through(
        tasks.primary_task_id
    )
    _attach_primary_view(
        result,
        speaker_role=speaker_role,
        image_url=primary_image.wallpaper_url,
        image_result=primary_image,
    )
    voice_event_service.mark_completed(voice_claim.event_id, result)
    return result


async def _enqueue_wallpaper_render(
    *,
    result: AgentRunResult,
    voice_claim: VoiceEventClaim,
    relationship_id: str,
    task_id: str,
) -> None:
    """Commit user-visible queued state before returning the voice response."""
    relationship_wallpaper_service.mark_render_queued(
        relationship_id,
        run_id=result.run_id,
        event_seq=voice_claim.event_seq,
    )
    voice_event_service.mark_queued(voice_claim.event_id, result)
    await wallpaper_event_hub.publish(
        relationship_id,
        status="wallpaper_revision_queued",
        version=voice_claim.event_seq,
    )
    try:
        await render_queue_service.enqueue(task_id)
    except Exception as exc:
        voice_event_service.mark_failed(voice_claim.event_id, str(exc))
        relationship_wallpaper_service.mark_wallpaper_update_failed(
            relationship_id,
            str(exc),
            event_seq=voice_claim.event_seq,
        )
        await wallpaper_event_hub.publish(
            relationship_id,
            status="wallpaper_revision_failed",
            version=voice_claim.event_seq,
            error=str(exc),
        )
        raise


def _reference_image_url(filename: str) -> str:
    return f"/references/{filename}"


def _find_default_reference(role: str) -> Optional[str]:
    aliases = {
        "elder": ("elder", "old", "parent", "grandma", "grandmother", "mother", "father"),
        "child": ("child", "young", "kid", "daughter", "son", "youth"),
    }.get(role, (role,))
    if not REFERENCE_DIR.exists():
        return None
    candidates: list[Path] = []
    for alias in aliases:
        for ext in REFERENCE_EXTS:
            candidates.extend(REFERENCE_DIR.glob(f"{alias}.{ext}"))
            candidates.extend(REFERENCE_DIR.glob(f"{alias}-*.{ext}"))
            candidates.extend(REFERENCE_DIR.glob(f"{alias}_*.{ext}"))
    candidates = [path for path in candidates if path.is_file()]
    if not candidates:
        return None
    candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return _reference_image_url(candidates[0].name)


def _resolve_role_references(
    elder_reference_url: Optional[str] = None,
    child_reference_url: Optional[str] = None,
    relationship_id: Optional[str] = None,
) -> dict[str, str | None]:
    generated_assets: dict[str, Optional[str]] = {"elder": None, "child": None}
    if relationship_id:
        with SessionLocal() as session:
            rows = (
                session.query(CharacterAsset)
                .filter(
                    CharacterAsset.relationship_id == relationship_id,
                    CharacterAsset.status == "ready",
                    CharacterAsset.is_active.is_(True),
                )
                .order_by(CharacterAsset.updated_at.desc())
                .all()
            )
            for row in rows:
                if row.role in generated_assets and not generated_assets[row.role]:
                    generated_assets[row.role] = row.master_image_url
    return {
        # Once onboarding has produced a stable cartoon identity, the normal
        # agent flow must not bypass it with a one-off raw portrait URL.
        "elder": generated_assets["elder"] or elder_reference_url or _find_default_reference("elder"),
        "child": generated_assets["child"] or child_reference_url or _find_default_reference("child"),
    }


def _image_ext(content_type: str, filename: str | None) -> str:
    if "jpeg" in content_type or "jpg" in content_type:
        return "jpg"
    if "webp" in content_type:
        return "webp"
    if "png" in content_type:
        return "png"
    suffix = Path(filename or "").suffix.lower().strip(".")
    if suffix in {"png", "jpg", "jpeg", "webp"}:
        return "jpg" if suffix == "jpeg" else suffix
    return "png"


@router.post("/reference-images")
async def upload_reference_image(
    role: str = Form(...),
    image: UploadFile = File(...),
    user_id: Optional[str] = Form(default=None, alias="userId"),
    relationship_id: Optional[str] = Form(default=None, alias="relationshipId"),
) -> dict[str, str]:
    if role not in {"elder", "child"}:
        raise HTTPException(status_code=400, detail="role must be elder or child")
    content_type = image.content_type or ""
    if content_type and not content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="reference image must be an image file")
    raw = await image.read()
    if not raw:
        raise HTTPException(status_code=400, detail="reference image is empty")
    REFERENCE_DIR.mkdir(parents=True, exist_ok=True)
    ext = _image_ext(content_type, image.filename)
    filename = f"{role}-{uuid.uuid4().hex}.{ext}"
    (REFERENCE_DIR / filename).write_bytes(raw)
    url = _reference_image_url(filename)
    resolved_user_id, resolved_relationship_id = normalize_user_context(user_id, relationship_id)
    with SessionLocal() as session:
        session.add(
            ReferenceImageLog(
                reference_id=uuid.uuid4().hex,
                user_id=resolved_user_id,
                relationship_id=resolved_relationship_id,
                role=role,
                image_url=url,
            )
        )
        session.commit()
    return {"role": role, "url": url, "userId": resolved_user_id, "relationshipId": resolved_relationship_id}


@router.post("/audio", response_model=AgentRunCreateOut)
async def create_audio_agent_run(
    audio: UploadFile = File(...),
    user_id: Optional[str] = Form(default=None, alias="userId"),
    relationship_id: Optional[str] = Form(default=None, alias="relationshipId"),
    previous_image_url: Optional[str] = Form(default=None, alias="previousImageUrl"),
    elder_reference_url: Optional[str] = Form(default=None, alias="elderReferenceUrl"),
    child_reference_url: Optional[str] = Form(default=None, alias="childReferenceUrl"),
) -> AgentRunCreateOut:
    raw = await audio.read()
    result = await orchestrator.run_audio(
        raw,
        content_type=audio.content_type or "audio/wav",
        filename=audio.filename or "recording.wav",
        user_id=user_id,
        relationship_id=relationship_id,
        previous_image_url=previous_image_url,
        role_reference_images=_resolve_role_references(
            elder_reference_url,
            child_reference_url,
            relationship_id=relationship_id,
        ),
    )
    return AgentRunCreateOut(run_id=result.run_id, status=result.status, result=result)


@router.post("/current/audio", response_model=AgentRunCreateOut)
async def create_current_audio_agent_run(
    audio: UploadFile = File(...),
    request_id: Optional[str] = Form(default=None, alias="requestId"),
    session_token: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> AgentRunCreateOut:
    try:
        identity = get_current_session(session_token)
    except SessionAuthenticationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    raw = await audio.read()
    if not raw:
        raise HTTPException(status_code=400, detail="audio is empty")

    user_id = str(identity["user_id"])
    relationship_id = str(identity["relationship_id"])
    speaker_role = str(identity["viewer_role"])
    voice_claim = voice_event_service.begin(
        request_id=request_id,
        run_id=uuid.uuid4().hex,
        relationship_id=relationship_id,
        user_id=user_id,
        device_session_id=str(identity["session_id"]),
        speaker_role=speaker_role,
        input_type="first_voice",
    )
    existing = _existing_voice_response(voice_claim)
    if existing is not None:
        return existing
    claim = relationship_wallpaper_service.claim_first_voice(relationship_id)
    if claim.action == "invalid":
        voice_event_service.mark_failed(voice_claim.event_id, "shared base scene is not ready")
        raise HTTPException(
            status_code=409,
            detail="The shared base scene must be ready before the first voice",
        )
    if claim.action == "complete":
        voice_event_service.mark_failed(voice_claim.event_id, "first voice is already complete")
        raise HTTPException(
            status_code=409,
            detail="The first-voice wallpapers have already been generated",
        )
    if claim.action == "wait":
        voice_event_service.mark_failed(voice_claim.event_id, "wallpaper generation is already in progress")
        raise HTTPException(
            status_code=409,
            detail="Wallpaper generation is already in progress",
        )

    references = _resolve_role_references(relationship_id=relationship_id)
    if not references.get("child") or not references.get("elder"):
        voice_event_service.mark_failed(
            voice_claim.event_id,
            "Both generated character assets are required",
        )
        relationship_wallpaper_service.mark_first_voice_failed(
            relationship_id,
            "Both generated character assets are required",
            event_seq=voice_claim.event_seq,
        )
        raise HTTPException(
            status_code=409,
            detail="Both generated character assets must be ready",
        )

    try:
        result = await orchestrator.run_audio(
            raw,
            content_type=audio.content_type or "audio/webm",
            filename=audio.filename or "recording.webm",
            user_id=user_id,
            relationship_id=relationship_id,
            role_reference_images=references,
            generation_stage="first_voice",
            speaker_role=speaker_role,
            analyze_only=True,
            run_id=voice_claim.run_id,
            event_seq=voice_claim.event_seq,
        )
        voice_event_service.mark_analyzed(voice_claim.event_id, result)
        tasks = wallpaper_generation_service.create_tasks(
            result,
            speaker_role=speaker_role,
            generation_stage="first_voice",
            role_reference_images=references,
            event_seq=voice_claim.event_seq,
        )
        if settings.render_queue_enabled:
            await _enqueue_wallpaper_render(
                result=result,
                voice_claim=voice_claim,
                relationship_id=relationship_id,
                task_id=tasks.primary_task_id,
            )
            return _voice_response(result, voice_claim)
        primary_image = await wallpaper_render_worker.render_through(
            tasks.primary_task_id
        )
        _attach_primary_view(
            result,
            speaker_role=speaker_role,
            image_url=primary_image.wallpaper_url,
            image_result=primary_image,
        )
        voice_event_service.mark_completed(voice_claim.event_id, result)
    except Exception as exc:
        voice_event_service.mark_failed(voice_claim.event_id, str(exc))
        relationship_wallpaper_service.mark_first_voice_failed(
            relationship_id,
            str(exc),
            event_seq=voice_claim.event_seq,
        )
        raise

    return _voice_response(result, voice_claim)


@router.post("/text", response_model=AgentRunCreateOut)
async def create_text_agent_run(body: AgentRunTextIn) -> AgentRunCreateOut:
    result = await orchestrator.run_text(
        body.transcript,
        user_id=body.user_id,
        relationship_id=body.relationship_id,
        previous_image_url=body.previous_image_url,
        role_reference_images=_resolve_role_references(
            body.elder_reference_url,
            body.child_reference_url,
            relationship_id=body.relationship_id,
        ),
    )
    return AgentRunCreateOut(run_id=result.run_id, status=result.status, result=result)


@router.post("/current/wallpaper-audio", response_model=AgentRunCreateOut)
async def update_current_wallpaper_from_audio(
    audio: UploadFile = File(...),
    request_id: Optional[str] = Form(default=None, alias="requestId"),
    session_token: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> AgentRunCreateOut:
    try:
        identity = get_current_session(session_token)
    except SessionAuthenticationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    raw = await audio.read()
    if not raw:
        raise HTTPException(status_code=400, detail="audio is empty")

    relationship_id = str(identity["relationship_id"])
    voice_claim = voice_event_service.begin(
        request_id=request_id,
        run_id=uuid.uuid4().hex,
        relationship_id=relationship_id,
        user_id=str(identity["user_id"]),
        device_session_id=str(identity["session_id"]),
        speaker_role=str(identity["viewer_role"]),
        input_type="wallpaper_audio",
    )
    existing = _existing_voice_response(voice_claim)
    if existing is not None:
        return existing
    try:
        result = await _analyze_and_render_current_wallpaper(
            audio=raw,
            content_type=audio.content_type or "audio/webm",
            filename=audio.filename or "wallpaper-voice.webm",
            identity=identity,
            voice_claim=voice_claim,
        )
    except Exception as exc:
        voice_event_service.mark_failed(voice_claim.event_id, str(exc))
        relationship_wallpaper_service.mark_wallpaper_update_failed(
            relationship_id,
            str(exc),
            event_seq=voice_claim.event_seq,
        )
        raise

    return _voice_response(result, voice_claim)


@router.websocket("/ws/current/wallpaper-audio")
async def stream_current_wallpaper_from_audio(ws: WebSocket) -> None:
    session_token = ws.cookies.get(SESSION_COOKIE_NAME)
    try:
        identity = get_current_session(session_token)
    except SessionAuthenticationError as exc:
        await ws.close(code=4401, reason=str(exc))
        return

    await ws.accept()
    viewer_role = str(identity["viewer_role"])
    relationship_id = str(identity["relationship_id"])
    profile = streaming_profile(viewer_role)
    provider_session: DoubaoStreamingASRSession | None = None
    started = False
    finished = False
    chunk_count = 0
    request_id = ""
    voice_claim: VoiceEventClaim | None = None
    try:
        await ws.send_json(
            {
                "type": "ready",
                "viewerRole": viewer_role,
                "profile": profile,
            }
        )
        while True:
            message = await ws.receive()
            if message.get("type") == "websocket.disconnect":
                return
            if message.get("bytes") is not None:
                if not started or provider_session is None:
                    await ws.send_json({"type": "error", "detail": "send start before audio"})
                    continue
                chunk = message["bytes"]
                await provider_session.send_audio(chunk)
                chunk_count += 1
                if chunk_count % 4 == 0:
                    await ws.send_json({"type": "ack", "chunks": chunk_count})
                continue

            text = message.get("text")
            if text is None:
                continue
            try:
                event = json.loads(text)
            except json.JSONDecodeError:
                await ws.send_json({"type": "error", "detail": "text message must be JSON"})
                continue

            event_type = event.get("type")
            if event_type == "start":
                if started:
                    await ws.send_json({"type": "error", "detail": "stream already started"})
                    continue
                request_id = str(event.get("requestId") or uuid.uuid4().hex)[:64]
                anonymous_uid = hashlib.sha256(
                    f"{identity['session_id']}:{request_id}:{relationship_id}".encode("utf-8")
                ).hexdigest()[:24]
                provider_session = orchestrator.chatbot_agent.create_streaming_asr_session(
                    uid=anonymous_uid,
                    end_window_size_ms=profile["end_silence_ms"],
                )
                await provider_session.start()
                started = True
                await ws.send_json({"type": "started", "profile": profile})
            elif event_type == "end":
                if not started or provider_session is None:
                    await ws.send_json({"type": "error", "detail": "stream has not started"})
                    continue
                await ws.send_json({"type": "transcribing", "chunks": chunk_count})
                streamed = await provider_session.finish()
                transcript = str(streamed.get("transcript") or "").strip()
                if not transcript:
                    raise HTTPException(status_code=422, detail="no speech was recognized")
                audio_context = orchestrator.chatbot_agent.context_from_streaming_result(streamed)
                voice_affect = audio_context.voice_affect
                await ws.send_json(
                    {
                        "type": "asr_final",
                        "transcript": transcript,
                        "voiceAffect": voice_affect,
                    }
                )
                voice_claim = voice_event_service.begin(
                    request_id=request_id,
                    run_id=uuid.uuid4().hex,
                    relationship_id=relationship_id,
                    user_id=str(identity["user_id"]),
                    device_session_id=str(identity["session_id"]),
                    speaker_role=viewer_role,
                    input_type="wallpaper_audio_stream",
                )
                try:
                    existing = _existing_voice_response(voice_claim)
                except HTTPException as exc:
                    await ws.send_json(
                        {
                            "type": "error",
                            "detail": exc.detail,
                            "fallbackAllowed": False,
                        }
                    )
                    voice_claim = None
                    return
                if existing is not None:
                    await ws.send_json(
                        {
                            "type": "final",
                            "payload": existing.model_dump(mode="json", by_alias=True),
                        }
                    )
                    finished = True
                    return
                result = await _analyze_and_render_current_wallpaper(
                    audio=b"",
                    content_type="audio/pcm;rate=16000",
                    filename="wallpaper-voice-stream.pcm",
                    identity=identity,
                    voice_claim=voice_claim,
                    prepared_audio_context=audio_context,
                )
                output = _voice_response(result, voice_claim)
                await ws.send_json(
                    {
                        "type": "final",
                        "payload": output.model_dump(mode="json", by_alias=True),
                    }
                )
                finished = True
                return
            elif event_type == "cancel":
                await ws.send_json({"type": "cancelled"})
                return
            elif event_type == "ping":
                await ws.send_json({"type": "pong"})
            else:
                await ws.send_json({"type": "error", "detail": f"unknown event type: {event_type}"})
    except WebSocketDisconnect:
        return
    except Exception as exc:
        if voice_claim is not None:
            voice_event_service.mark_failed(voice_claim.event_id, str(exc))
        relationship_wallpaper_service.mark_wallpaper_update_failed(
            relationship_id,
            str(exc),
            event_seq=voice_claim.event_seq if voice_claim is not None else None,
        )
        detail = exc.detail if isinstance(exc, HTTPException) else f"{type(exc).__name__}: {exc}"
        try:
            await ws.send_json(
                {
                    "type": "error",
                    "detail": str(detail),
                    "fallbackAllowed": not finished,
                }
            )
        except Exception:
            pass
    finally:
        if provider_session is not None:
            await provider_session.close()


@router.post("/comfort-reply", response_model=ComfortReplyOut)
async def create_comfort_reply(body: ComfortReplyIn) -> ComfortReplyOut:
    return await orchestrator.run_comfort_reply(
        body.transcript,
        user_id=body.user_id,
        relationship_id=body.relationship_id,
        persist=body.persist,
    )


@router.post("/memory-objects", response_model=MemoryObjectsOut)
async def create_memory_objects(body: MemoryObjectsIn) -> MemoryObjectsOut:
    return await orchestrator.run_memory_objects(
        user_id=body.user_id,
        relationship_id=body.relationship_id,
        limit=body.limit,
        threshold=body.threshold,
        generate_missing=body.generate_missing,
    )


@router.get("/{run_id}", response_model=AgentRunResult)
async def get_agent_run(run_id: str) -> AgentRunResult:
    return await orchestrator.get_run(run_id)
