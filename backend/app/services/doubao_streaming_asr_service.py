from __future__ import annotations

import asyncio
import gzip
import json
import ssl
import struct
import uuid
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from websockets.asyncio.client import ClientConnection, connect

from app.core.config import settings


FULL_CLIENT_REQUEST = 0x1
AUDIO_ONLY_REQUEST = 0x2
FULL_SERVER_RESPONSE = 0x9
SERVER_ACK = 0xB
SERVER_ERROR = 0xF

NO_SEQUENCE = 0x0
LAST_PACKET_NO_SEQUENCE = 0x2
JSON_SERIALIZATION = 0x1
NO_SERIALIZATION = 0x0
GZIP_COMPRESSION = 0x1

DOUBAO_EMOTION_TO_SHORT_TERM = {
    "happy": "愉悦",
    "sad": "悲伤",
    "neutral": "平静",
    "angry": "生气",
    "surprise": "惊讶",
}
DOUBAO_EMOTION_FUSION_CONFIDENCE = {
    "happy": 0.65,
    "sad": 0.65,
    "neutral": 0.55,
    "angry": 0.65,
    "surprise": 0.65,
}


class DoubaoStreamingASRSession:
    """One Doubao ``bigmodel_nostream`` session owned by ChatBotAgent.

    Browser audio arrives as 16 kHz, mono, signed 16-bit PCM. The service keeps
    the provider credential on the backend and forwards each PCM packet through
    Doubao's documented binary WebSocket protocol.
    """

    def __init__(
        self,
        *,
        uid: str,
        end_window_size_ms: int,
        run_id: str | None = None,
    ) -> None:
        self.uid = uid
        self.end_window_size_ms = end_window_size_ms
        self.run_id = run_id or f"chatbot-stream-{uuid.uuid4().hex}"
        self.request_id = str(uuid.uuid4())
        self._connection: ClientConnection | None = None
        self._closed = False
        self._audio_bytes = 0

    async def start(self) -> None:
        api_key = settings.doubao_asr_api_key.strip()
        if not api_key:
            raise HTTPException(
                status_code=503,
                detail="DOUBAO_ASR_API_KEY is not configured",
            )

        endpoint = settings.doubao_streaming_asr_endpoint.strip()
        resource_id = settings.doubao_streaming_asr_resource_id.strip()
        headers = {
            "X-Api-Key": api_key,
            "X-Api-Resource-Id": resource_id,
            "X-Api-Request-Id": self.request_id,
            "X-Api-Connect-Id": self.request_id,
            "X-Api-Sequence": "-1",
        }
        ca_bundle = settings.doubao_streaming_asr_ca_bundle.strip()
        ssl_context = (
            ssl.create_default_context(cafile=ca_bundle)
            if ca_bundle and Path(ca_bundle).is_file()
            else ssl.create_default_context()
        )
        try:
            self._connection = await connect(
                endpoint,
                additional_headers=headers,
                open_timeout=settings.doubao_streaming_asr_connect_timeout_seconds,
                close_timeout=3,
                max_size=8 * 1024 * 1024,
                proxy=None,
                ssl=ssl_context,
            )
            await self._connection.send(
                _encode_client_frame(
                    FULL_CLIENT_REQUEST,
                    self._request_payload(),
                    serialization=JSON_SERIALIZATION,
                    compression=GZIP_COMPRESSION,
                )
            )
        except HTTPException:
            raise
        except Exception as exc:
            await self.close()
            response = getattr(exc, "response", None)
            status_code = getattr(response, "status_code", None)
            if status_code == 403:
                detail = (
                    "Doubao streaming ASR HTTP 403: enable resource "
                    f"{resource_id} for this speech API key"
                )
            else:
                detail = f"Doubao streaming ASR connection failed ({type(exc).__name__}): {exc}"
            raise HTTPException(
                status_code=502,
                detail=detail,
            ) from exc

    async def send_audio(self, pcm: bytes) -> None:
        if not pcm:
            return
        connection = self._require_connection()
        self._audio_bytes += len(pcm)
        if self._audio_bytes > settings.doubao_streaming_asr_max_audio_bytes:
            raise HTTPException(status_code=413, detail="streaming audio is too large")
        await connection.send(
            _encode_client_frame(
                AUDIO_ONLY_REQUEST,
                pcm,
                serialization=NO_SERIALIZATION,
                compression=GZIP_COMPRESSION,
            )
        )

    async def finish(self) -> dict[str, Any]:
        connection = self._require_connection()
        if self._audio_bytes == 0:
            raise HTTPException(status_code=400, detail="streaming audio is empty")

        await connection.send(
            _encode_client_frame(
                AUDIO_ONLY_REQUEST,
                b"",
                flags=LAST_PACKET_NO_SEQUENCE,
                serialization=NO_SERIALIZATION,
                compression=GZIP_COMPRESSION,
            )
        )

        deadline = asyncio.get_running_loop().time() + settings.doubao_streaming_asr_result_timeout_seconds
        latest: dict[str, Any] | None = None
        while asyncio.get_running_loop().time() < deadline:
            remaining = max(0.1, deadline - asyncio.get_running_loop().time())
            try:
                message = await asyncio.wait_for(connection.recv(), timeout=remaining)
            except asyncio.TimeoutError as exc:
                raise HTTPException(status_code=504, detail="Doubao streaming ASR result timed out") from exc
            if isinstance(message, str):
                try:
                    decoded = json.loads(message)
                except json.JSONDecodeError:
                    continue
                if isinstance(decoded, dict):
                    latest = decoded
            else:
                frame = _decode_server_frame(bytes(message))
                if frame["message_type"] == SERVER_ERROR:
                    payload = frame.get("payload")
                    raise HTTPException(status_code=502, detail=f"Doubao streaming ASR failed: {payload}")
                if frame["message_type"] == SERVER_ACK:
                    continue
                payload = frame.get("payload")
                if frame["message_type"] == FULL_SERVER_RESPONSE and isinstance(payload, dict):
                    latest = payload

            if latest and _transcript_from_response(latest):
                result = _normalize_streaming_result(latest, self.request_id, self._audio_bytes)
                await self.close()
                return result

        raise HTTPException(status_code=502, detail="Doubao streaming ASR returned no final result")

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        connection = self._connection
        self._connection = None
        if connection is not None:
            try:
                await connection.close()
            except Exception:
                pass

    def _require_connection(self) -> ClientConnection:
        if self._connection is None:
            raise HTTPException(status_code=409, detail="streaming ASR session has not started")
        return self._connection

    def _request_payload(self) -> dict[str, Any]:
        return {
            "user": {"uid": self.uid},
            "audio": {
                "format": "pcm",
                "codec": "raw",
                "rate": 16000,
                "bits": 16,
                "channel": 1,
            },
            "request": {
                "model_name": "bigmodel",
                "enable_itn": True,
                "enable_punc": True,
                "enable_ddc": True,
                "show_utterances": True,
                "result_type": "full",
                "end_window_size": self.end_window_size_ms,
                "enable_emotion_detection": True,
                "show_volume": True,
                "show_speech_rate": True,
            },
        }


def streaming_profile(viewer_role: str) -> dict[str, int]:
    if viewer_role == "elder":
        return {
            "chunk_ms": 200,
            "end_silence_ms": 1200,
            "min_speech_ms": 400,
            "max_recording_ms": 30000,
            "no_speech_timeout_ms": 5000,
        }
    return {
        "chunk_ms": 200,
        "end_silence_ms": 750,
        "min_speech_ms": 300,
        "max_recording_ms": 20000,
        "no_speech_timeout_ms": 3000,
    }


def _encode_client_frame(
    message_type: int,
    payload: dict[str, Any] | bytes,
    *,
    flags: int = NO_SEQUENCE,
    serialization: int,
    compression: int,
) -> bytes:
    if isinstance(payload, dict):
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    else:
        body = payload
    if compression == GZIP_COMPRESSION:
        body = gzip.compress(body)
    header = bytes(
        [
            0x11,
            ((message_type & 0x0F) << 4) | (flags & 0x0F),
            ((serialization & 0x0F) << 4) | (compression & 0x0F),
            0x00,
        ]
    )
    return header + struct.pack(">I", len(body)) + body


def _decode_server_frame(data: bytes) -> dict[str, Any]:
    if len(data) < 4:
        raise HTTPException(status_code=502, detail="Doubao streaming ASR returned a short frame")
    header_size = (data[0] & 0x0F) * 4
    message_type = data[1] >> 4
    flags = data[1] & 0x0F
    serialization = data[2] >> 4
    compression = data[2] & 0x0F
    offset = header_size

    sequence: int | None = None
    if flags & 0x01:
        if len(data) < offset + 4:
            raise HTTPException(status_code=502, detail="Doubao streaming ASR frame lacks sequence")
        sequence = struct.unpack(">i", data[offset : offset + 4])[0]
        offset += 4

    if message_type == SERVER_ERROR:
        if len(data) < offset + 8:
            return {"message_type": message_type, "flags": flags, "payload": "unknown provider error"}
        error_code = struct.unpack(">I", data[offset : offset + 4])[0]
        offset += 4
        payload_size = struct.unpack(">I", data[offset : offset + 4])[0]
        offset += 4
        body = data[offset : offset + payload_size]
        return {
            "message_type": message_type,
            "flags": flags,
            "sequence": sequence,
            "payload": {"code": error_code, "message": body.decode("utf-8", errors="replace")},
        }

    if len(data) < offset + 4:
        return {"message_type": message_type, "flags": flags, "sequence": sequence, "payload": None}
    payload_size = struct.unpack(">I", data[offset : offset + 4])[0]
    offset += 4
    body = data[offset : offset + payload_size]
    if compression == GZIP_COMPRESSION and body:
        body = gzip.decompress(body)
    payload: Any = body
    if serialization == JSON_SERIALIZATION and body:
        payload = json.loads(body.decode("utf-8"))
    return {
        "message_type": message_type,
        "flags": flags,
        "sequence": sequence,
        "payload": payload,
    }


def _transcript_from_response(data: dict[str, Any]) -> str:
    result = data.get("result")
    if not isinstance(result, dict):
        return ""
    return str(result.get("text") or "").strip()


def _normalize_streaming_result(
    data: dict[str, Any],
    request_id: str,
    audio_bytes: int,
) -> dict[str, Any]:
    result = data.get("result") if isinstance(data.get("result"), dict) else {}
    utterances = result.get("utterances") if isinstance(result.get("utterances"), list) else []
    affect_utterances: list[dict[str, Any]] = []
    for utterance in utterances:
        if not isinstance(utterance, dict):
            continue
        additions = _coerce_additions(utterance.get("additions"))
        item = {
            "text": str(utterance.get("text") or ""),
            "startTime": utterance.get("start_time"),
            "endTime": utterance.get("end_time"),
        }
        provider_emotion = str(additions.get("emotion") or "").strip().lower()
        mapped_emotion = DOUBAO_EMOTION_TO_SHORT_TERM.get(provider_emotion)
        if mapped_emotion:
            item.update(
                {
                    "providerEmotion": provider_emotion,
                    "emotion": mapped_emotion,
                    "confidence": DOUBAO_EMOTION_FUSION_CONFIDENCE[provider_emotion],
                    "confidenceSource": "system_fusion_weight_not_provider_confidence",
                }
            )
        for source, target in (
            ("volume", "volume"),
            ("speech_rate", "speechRate"),
            ("language", "language"),
        ):
            if additions.get(source) not in (None, ""):
                item[target] = additions[source]
        affect_utterances.append(item)

    latest_affect = next(
        (item for item in reversed(affect_utterances) if item.get("emotion")),
        affect_utterances[-1] if affect_utterances else {},
    )
    voice_affect = {
        key: latest_affect[key]
        for key in (
            "emotion",
            "providerEmotion",
            "confidence",
            "confidenceSource",
            "volume",
            "speechRate",
            "language",
        )
        if key in latest_affect
    }
    voice_affect.update(
        {
            "source": "doubao_seed_asr_streaming",
            "utterances": affect_utterances,
        }
    )
    return {
        "transcript": _transcript_from_response(data),
        "voiceAffect": voice_affect,
        "raw": {
            "provider": "doubao_seed_asr_2_0_streaming_input",
            "requestId": request_id,
            "audioBytes": audio_bytes,
            "response": data,
        },
    }


def _coerce_additions(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}
