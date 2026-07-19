from __future__ import annotations

import json
import uuid
from typing import Optional

from fastapi import APIRouter, File, Form, UploadFile, WebSocket, WebSocketDisconnect

from app.services.audio_understanding_tool import analyze_audio_affect
from app.services.chatbox_asr_service import transcribe_chatbox_audio


router = APIRouter()


@router.post("/chatbox/asr-file")
async def asr_file(audio: UploadFile = File(...)) -> dict:
    raw = await audio.read()
    result = await transcribe_chatbox_audio(
        raw,
        filename=audio.filename or "recording.wav",
        content_type=audio.content_type or "audio/wav",
        run_id=f"chatbox-asr-{uuid.uuid4().hex}",
    )
    return {
        "transcript": result.get("transcript", ""),
        "raw": result.get("raw", {}),
    }


@router.post("/chatbox/audio-understanding")
async def audio_understanding(
    audio: UploadFile = File(...),
    transcript: Optional[str] = Form(default=""),
) -> dict:
    raw = await audio.read()
    result = await analyze_audio_affect(
        raw,
        filename=audio.filename or "recording.wav",
        content_type=audio.content_type or "audio/wav",
        transcript=transcript or "",
        run_id=f"audio-affect-{uuid.uuid4().hex}",
    )
    return {
        "voiceAffect": result.get("voiceAffect", {}),
        "semanticTone": result.get("semanticTone", ""),
        "raw": result.get("raw", {}),
    }


@router.websocket("/ws/chatbox/asr")
async def ws_chatbox_asr(ws: WebSocket) -> None:
    await ws.accept()
    run_id = f"chatbox-ws-asr-{uuid.uuid4().hex}"
    chunks = bytearray()
    filename = "recording.webm"
    content_type = "audio/webm"

    await ws.send_json({"type": "ready", "runId": run_id})
    try:
        while True:
            message = await ws.receive()

            if message.get("bytes") is not None:
                chunk = message["bytes"]
                chunks.extend(chunk)
                await ws.send_json(
                    {
                        "type": "ack",
                        "runId": run_id,
                        "chunkBytes": len(chunk),
                        "totalBytes": len(chunks),
                    }
                )
                continue

            text = message.get("text")
            if text is None:
                continue

            try:
                event = json.loads(text)
            except json.JSONDecodeError:
                await ws.send_json({"type": "error", "runId": run_id, "detail": "text message must be JSON"})
                continue

            event_type = event.get("type")
            if event_type == "start":
                chunks.clear()
                filename = str(event.get("filename") or filename)
                content_type = str(event.get("contentType") or content_type)
                await ws.send_json({"type": "started", "runId": run_id})
            elif event_type == "end":
                if event.get("filename"):
                    filename = str(event["filename"])
                if event.get("contentType"):
                    content_type = str(event["contentType"])
                await ws.send_json({"type": "transcribing", "runId": run_id, "totalBytes": len(chunks)})
                result = await transcribe_chatbox_audio(
                    bytes(chunks),
                    filename=filename,
                    content_type=content_type,
                    run_id=run_id,
                )
                await ws.send_json(
                    {
                        "type": "final",
                        "runId": run_id,
                        "transcript": result.get("transcript", ""),
                        "raw": result.get("raw", {}),
                    }
                )
            elif event_type == "cancel":
                chunks.clear()
                await ws.send_json({"type": "cancelled", "runId": run_id})
            elif event_type == "ping":
                await ws.send_json({"type": "pong", "runId": run_id})
            else:
                await ws.send_json({"type": "error", "runId": run_id, "detail": f"unknown event type: {event_type}"})
    except WebSocketDisconnect:
        return
