from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import httpx
from fastapi import HTTPException

from app.core.config import settings
from app.mock.transcripts import MOCK_TRANSCRIPTS


AUDIO_INPUTS_DIR = Path(settings.storage_local_dir) / "audio-inputs"


async def transcribe_chatbox_audio(
    audio: bytes,
    filename: str,
    content_type: str,
    run_id: str | None = None,
) -> dict[str, Any]:
    api_key = settings.effective_audio_api_key
    api_base_url = settings.effective_audio_api_base_url.rstrip("/")
    endpoint = settings.audio_transcription_endpoint.strip()
    model = settings.audio_transcription_model.strip()

    if not audio:
        raise HTTPException(status_code=400, detail="audio is empty")

    if not api_key or not api_base_url:
        idx = len(audio) % len(MOCK_TRANSCRIPTS)
        return {
            "transcript": MOCK_TRANSCRIPTS[idx],
            "raw": {"provider": "mock_chatbox_asr", "size": len(audio)},
        }

    if _use_doubao_chat_audio(endpoint, model):
        return await _transcribe_with_doubao_chat_audio(
            audio=audio,
            filename=filename,
            content_type=content_type,
            api_key=api_key,
            api_base_url=api_base_url,
            endpoint=endpoint,
            model=model,
            run_id=run_id,
        )

    return await _transcribe_with_multipart_asr(
        audio=audio,
        filename=filename,
        content_type=content_type,
        api_key=api_key,
        api_base_url=api_base_url,
        endpoint=endpoint,
        model=model,
        run_id=run_id,
    )


def _use_doubao_chat_audio(endpoint: str, model: str) -> bool:
    text = f"{endpoint} {model}".lower()
    return "doubao" in text or "seed" in text or "chat/completions" in text


async def _transcribe_with_doubao_chat_audio(
    *,
    audio: bytes,
    filename: str,
    content_type: str,
    api_key: str,
    api_base_url: str,
    endpoint: str,
    model: str,
    run_id: str | None,
) -> dict[str, Any]:
    endpoint = _resolve_endpoint(api_base_url, endpoint, "/api/v3/chat/completions")
    audio_url, audio_format = _save_audio_input_for_url(audio, filename, content_type, prefix="chatbox-asr")
    if _is_local_public_base_url():
        _log(
            run_id,
            "ChatBox ASR warning: PUBLIC_API_BASE_URL is local; Ark/Doubao must be able to fetch the audio URL.",
        )

    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_audio",
                        "input_audio": {
                            "url": audio_url,
                            "format": audio_format,
                        },
                    },
                    {
                        "type": "text",
                        "text": "请识别音频中的中文内容，只返回完整转写文本，不要解释，不要添加标点外的内容。",
                    },
                ],
            }
        ],
    }

    try:
        _log(run_id, f"ChatBox ASR request model={model} endpoint={endpoint} audioUrl={audio_url}")
        async with httpx.AsyncClient(timeout=75) as client:
            response = await client.post(
                endpoint,
                json=payload,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
            response.raise_for_status()
            _log(run_id, f"ChatBox ASR response status={response.status_code}")
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"ChatBox ASR HTTP {exc.response.status_code}: {exc.response.text[:800]}",
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"ChatBox ASR request failed ({type(exc).__name__}): {repr(exc)}",
        ) from exc

    try:
        data = response.json()
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail=f"ChatBox ASR returned non-JSON response: {response.text[:300]}") from exc

    transcript = _response_text(data)
    if not transcript:
        raise HTTPException(status_code=502, detail=f"ChatBox ASR response did not contain text: {json.dumps(data, ensure_ascii=False)[:800]}")
    return {
        "transcript": transcript,
        "raw": {
            "provider": "doubao_chatbox_asr",
            "audioUrl": audio_url,
            "audioFormat": audio_format,
            "response": data,
        },
    }


async def _transcribe_with_multipart_asr(
    *,
    audio: bytes,
    filename: str,
    content_type: str,
    api_key: str,
    api_base_url: str,
    endpoint: str,
    model: str,
    run_id: str | None,
) -> dict[str, Any]:
    endpoint = _resolve_endpoint(api_base_url, endpoint, "/v1/audio/transcriptions")
    try:
        _log(run_id, f"ChatBox ASR multipart request model={model} endpoint={endpoint}")
        async with httpx.AsyncClient(timeout=45) as client:
            response = await client.post(
                endpoint,
                data={
                    "model": model,
                    "language": settings.audio_transcription_language,
                    "response_format": "json",
                    "temperature": "0",
                },
                files={"file": (filename, audio, content_type or "audio/wav")},
                headers={"Authorization": f"Bearer {api_key}"},
            )
            response.raise_for_status()
            _log(run_id, f"ChatBox ASR multipart response status={response.status_code}")
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"ChatBox ASR HTTP {exc.response.status_code}: {exc.response.text[:500]}",
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"ChatBox ASR request failed ({type(exc).__name__}): {repr(exc)}",
        ) from exc

    try:
        data = response.json()
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail=f"ChatBox ASR returned non-JSON response: {response.text[:300]}") from exc

    transcript = _response_text(data)
    if not transcript:
        raise HTTPException(status_code=502, detail=f"ChatBox ASR response did not contain text: {json.dumps(data, ensure_ascii=False)[:500]}")
    return {"transcript": transcript, "raw": {"provider": "multipart_chatbox_asr", "response": data}}


def _response_text(payload: Any) -> str:
    if isinstance(payload, str):
        return payload.strip()
    if isinstance(payload, list):
        for item in payload:
            text = _response_text(item)
            if text:
                return text
        return ""
    if not isinstance(payload, dict):
        return ""

    for key in ("text", "content", "utterance", "transcript", "sentence"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return _strip_json_text(value.strip())

    choices = payload.get("choices")
    if isinstance(choices, list):
        for choice in choices:
            text = _response_text(choice)
            if text:
                return text

    message = payload.get("message")
    if isinstance(message, dict):
        text = _response_text(message.get("content"))
        if text:
            return text

    for key in ("result", "results", "utterances", "payload_msg", "data"):
        text = _response_text(payload.get(key))
        if text:
            return text
    return ""


def _strip_json_text(text: str) -> str:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return text
    if isinstance(parsed, dict):
        for key in ("transcript", "text", "content"):
            value = parsed.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return text


def _save_audio_input_for_url(audio: bytes, filename: str, content_type: str, prefix: str) -> tuple[str, str]:
    AUDIO_INPUTS_DIR.mkdir(parents=True, exist_ok=True)
    ext = _audio_extension(content_type, filename)
    saved_name = f"{prefix}-{uuid.uuid4().hex}.{ext}"
    path = AUDIO_INPUTS_DIR / saved_name
    path.write_bytes(audio)
    public_base = settings.public_api_base_url.rstrip("/")
    return f"{public_base}/audio-inputs/{saved_name}", ext


def _resolve_endpoint(api_base_url: str, endpoint: str, default_path: str) -> str:
    endpoint = (endpoint or "").strip()
    if endpoint.startswith(("http://", "https://")):
        return endpoint
    base = api_base_url.rstrip("/")
    path = endpoint or default_path
    if not path.startswith("/"):
        path = f"/{path}"
    return f"{base}{path}"


def _audio_extension(content_type: str, filename: str) -> str:
    suffix = Path(filename or "").suffix.lower().strip(".")
    if suffix in {"mp3", "wav", "m4a", "aac", "ogg", "flac", "webm"}:
        return suffix
    content_type = (content_type or "").lower()
    if "mpeg" in content_type or "mp3" in content_type:
        return "mp3"
    if "wav" in content_type:
        return "wav"
    if "webm" in content_type:
        return "webm"
    if "ogg" in content_type:
        return "ogg"
    if "flac" in content_type:
        return "flac"
    if "aac" in content_type:
        return "aac"
    if "mp4" in content_type or "m4a" in content_type:
        return "m4a"
    return "wav"


def _is_local_public_base_url() -> bool:
    base = settings.public_api_base_url.lower()
    return "127.0.0.1" in base or "localhost" in base


def _log(run_id: str | None, message: str) -> None:
    prefix = f"[agent-run:{run_id}] " if run_id else ""
    print(f"{prefix}{message}", flush=True)
