from __future__ import annotations

import base64
import json
import re
import uuid
from pathlib import Path

import httpx
from fastapi import HTTPException

from app.core.config import settings
from app.mock.transcripts import MOCK_TRANSCRIPTS
from app.services.chatbox_asr_service import transcribe_chatbox_audio
from app.services.openai_image_service import (
    build_images_generation_payload,
    collect_image_refs,
    resolve_image_endpoint,
    save_image_ref,
)


GENERATED_DIR = Path(settings.storage_local_dir) / "generated"
REFERENCES_DIR = Path(settings.storage_local_dir) / "references"
AUDIO_INPUTS_DIR = Path(settings.storage_local_dir) / "audio-inputs"


def _response_text(payload) -> str:
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

    for key in ("text", "utterance", "transcript", "sentence"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    for key in ("result", "results", "utterances", "payload_msg", "data"):
        text = _response_text(payload.get(key))
        if text:
            return text
    return ""


def _build_wallpaper_prompt(text: str) -> str:
    return f"""
请根据这段家庭语音留言生成一张1:1方形1536×1536关系壁纸主视觉。

留言内容：
{text}

画面要求：
- 整个1536×1536画布是同一个连续纸雕世界，不划分中央面板和左右边条，不出现垂直接缝、色差边界或拼贴分区。
- 不要任何文字、水印、UI、按钮、logo。
- 构图必须是左下角一位老人、右上角一位年轻人，形成明显的对角线关系。
- 左下角：一位中国老人/奶奶坐在温暖的小房间或窗边，身边有台灯、茶杯、旧照片、花、餐桌等生活细节。
- 右上角：一位年轻人/子女坐在高处书桌前或窗边，用电脑、手机或台灯回应，空间更开阔明亮。
- 两个人不要站在一起，不要拥抱，不要触摸，不要面对面近距离同框；他们应被山谷、河流、庭院、岩壁、窗洞或光带自然分隔。
- 中间区域用蜿蜒河流、山谷小路、暖色灯光、星光或窗光连接左下与右上，表达远距离陪伴和思念。
- 画风参考温暖奇幻童话感数字插画：细腻笔触、水彩质感、电影感黄昏光、梦境般山谷、拱形窗、藤蔓、柔和云霞。
- 情绪根据留言调整：温柔、想念、被惦记、安静陪伴；不要恐怖、阴暗、压抑或过度悲伤。
- 上方保留较干净的天空/云霞空间，方便叠加锁屏时间。
- 人物不要太大，整体要有纵深感和环境叙事，适合作为手机动态壁纸背景图。
- 两个人物采用完整头身构图，平台或地形边缘不得从腰部或躯干截断人物；桌子、购物篮、座椅等有意义的活动道具可以自然遮挡腰部以下。
""".strip()


def _collect_image_refs(value) -> list[str]:
    refs: list[str] = []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return refs
        refs.extend(re.findall(r"!\[[^\]]*]\(([^)]+)\)", text))
        refs.extend(re.findall(r"(data:image/[a-zA-Z0-9.+-]+;base64,[A-Za-z0-9+/=\n\r]+)", text))
        refs.extend(re.findall(r"https?://[^\s)'\"<>]+", text))
        if len(text) > 500 and re.fullmatch(r"[A-Za-z0-9+/=\s]+", text):
            refs.append(text)
        try:
            refs.extend(_collect_image_refs(json.loads(text)))
        except json.JSONDecodeError:
            pass
        return refs
    if isinstance(value, list):
        for item in value:
            refs.extend(_collect_image_refs(item))
        return refs
    if isinstance(value, dict):
        inline_data = value.get("inlineData") or value.get("inline_data")
        if isinstance(inline_data, dict):
            data = inline_data.get("data")
            mime_type = inline_data.get("mimeType") or inline_data.get("mime_type") or "image/png"
            if isinstance(data, str) and data.strip():
                refs.append(f"data:{mime_type};base64,{data.strip()}")
        for key in ("url", "image_url", "b64_json", "base64", "data"):
            if key in value:
                refs.extend(_collect_image_refs(value[key]))
        for item in value.values():
            refs.extend(_collect_image_refs(item))
        return refs
    return refs


def _image_extension(content_type: str, fallback: str = "png") -> str:
    if "jpeg" in content_type or "jpg" in content_type:
        return "jpg"
    if "webp" in content_type:
        return "webp"
    if "gif" in content_type:
        return "gif"
    if "png" in content_type:
        return "png"
    return fallback


def _image_mime_from_path(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".webp":
        return "image/webp"
    if suffix == ".gif":
        return "image/gif"
    return "image/png"


def _local_static_image_path(ref: str) -> Path | None:
    clean_ref = ref.split("?", 1)[0].strip()
    if clean_ref.startswith("/generated/"):
        return GENERATED_DIR / clean_ref.removeprefix("/generated/")
    if clean_ref.startswith("/references/"):
        return REFERENCES_DIR / clean_ref.removeprefix("/references/")
    return None


def _image_ref_for_upstream(ref: str) -> str:
    ref = ref.strip()
    if not ref:
        return ref
    if ref.startswith(("data:image/", "http://", "https://")):
        return ref
    local_path = _local_static_image_path(ref)
    if local_path and local_path.exists():
        encoded = base64.b64encode(local_path.read_bytes()).decode("ascii")
        return f"data:{_image_mime_from_path(local_path)};base64,{encoded}"
    return ref


def _inline_image_part(ref: str) -> dict | None:
    ref = ref.strip()
    if ref.startswith("data:image/") and ";base64," in ref:
        header, encoded = ref.split(",", 1)
        mime_type = header.removeprefix("data:").split(";", 1)[0] or "image/png"
        return {"inlineData": {"mimeType": mime_type, "data": re.sub(r"\s+", "", encoded)}}
    if len(ref) > 500 and re.fullmatch(r"[A-Za-z0-9+/=\s]+", ref):
        return {"inlineData": {"mimeType": "image/png", "data": re.sub(r"\s+", "", ref)}}
    return None


async def _save_image_ref(ref: str, client: httpx.AsyncClient) -> str:
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    ref = ref.strip().strip('"').strip("'")

    if ref.startswith("data:image/"):
        header, encoded = ref.split(",", 1)
        ext = _image_extension(header)
        data = base64.b64decode(encoded)
    elif ref.startswith("http://") or ref.startswith("https://"):
        response = await client.get(ref, timeout=settings.image_download_timeout_seconds)
        response.raise_for_status()
        ext = _image_extension(response.headers.get("content-type", ""))
        data = response.content
    else:
        ext = "png"
        data = base64.b64decode(re.sub(r"\s+", "", ref))

    filename = f"wallpaper-{uuid.uuid4().hex}.{ext}"
    path = GENERATED_DIR / filename
    path.write_bytes(data)
    return f"/generated/{filename}"


async def transcribe_audio(audio: bytes, filename: str, content_type: str, run_id: str | None = None) -> dict:
    return await transcribe_chatbox_audio(audio, filename=filename, content_type=content_type, run_id=run_id)


async def _legacy_transcribe_audio(audio: bytes, filename: str, content_type: str, run_id: str | None = None) -> dict:
    api_key = settings.effective_audio_api_key
    api_base_url = settings.effective_audio_api_base_url.rstrip("/")
    endpoint = settings.audio_transcription_endpoint.strip()

    if not api_key or not api_base_url:
        idx = len(audio) % len(MOCK_TRANSCRIPTS)
        return {"transcript": MOCK_TRANSCRIPTS[idx], "raw": {"provider": "mock", "size": len(audio)}}

    if _use_doubao_chat_asr(endpoint, settings.audio_transcription_model):
        return await _transcribe_audio_with_doubao_chat(
            audio=audio,
            filename=filename,
            content_type=content_type,
            api_key=api_key,
            api_base_url=api_base_url,
            endpoint=endpoint,
            run_id=run_id,
        )

    if not endpoint:
        endpoint = f"{api_base_url}/v1/audio/transcriptions"

    try:
        _log(run_id, f"ASR upstream request model={settings.audio_transcription_model} endpoint={endpoint}")
        async with httpx.AsyncClient(timeout=45) as client:
            response = await client.post(
                endpoint,
                data={
                    "model": settings.audio_transcription_model,
                    "language": settings.audio_transcription_language,
                    "response_format": "json",
                    "temperature": "0",
                },
                files={"file": (filename, audio, content_type or "audio/wav")},
                headers={"Authorization": f"Bearer {api_key}"},
            )
            response.raise_for_status()
            _log(run_id, f"ASR upstream response status={response.status_code}")
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Transcription API HTTP {exc.response.status_code}: {exc.response.text[:500]}",
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Transcription API request failed ({type(exc).__name__}): {repr(exc)}",
        ) from exc

    try:
        data = response.json()
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail=f"Transcription API returned non-JSON response: {response.text[:300]}") from exc

    transcript = _response_text(data)
    if not transcript:
        raise HTTPException(status_code=502, detail=f"Transcription API response did not contain text: {json.dumps(data, ensure_ascii=False)[:500]}")
    return {"transcript": transcript, "raw": data}


def _use_doubao_chat_asr(endpoint: str, model: str) -> bool:
    text = f"{endpoint} {model}".lower()
    return "doubao" in text and ("chat/completions" in text or "seed" in text)


async def _transcribe_audio_with_doubao_chat(
    *,
    audio: bytes,
    filename: str,
    content_type: str,
    api_key: str,
    api_base_url: str,
    endpoint: str,
    run_id: str | None,
) -> dict:
    endpoint = endpoint or f"{api_base_url}/api/v3/chat/completions"
    audio_url, audio_format = _save_audio_input_for_url(audio, filename, content_type)
    if _is_local_public_base_url():
        _log(
            run_id,
            "ASR Doubao audio URL is local; Ark must be able to fetch it. "
            "Set PUBLIC_API_BASE_URL to a public tunnel/domain if upstream returns audio fetch errors.",
        )
    payload = {
        "model": settings.audio_transcription_model,
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
                        "text": "请识别音频中的中文内容，只返回转写文本，不要解释。",
                    },
                ],
            }
        ],
    }
    try:
        _log(run_id, f"ASR Doubao chat request model={settings.audio_transcription_model} endpoint={endpoint} audioUrl={audio_url}")
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
            _log(run_id, f"ASR Doubao chat response status={response.status_code}")
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Doubao ASR HTTP {exc.response.status_code}: {exc.response.text[:800]}",
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Doubao ASR request failed ({type(exc).__name__}): {repr(exc)}",
        ) from exc

    try:
        data = response.json()
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail=f"Doubao ASR returned non-JSON response: {response.text[:300]}") from exc

    transcript = _response_text(data)
    if not transcript:
        raise HTTPException(status_code=502, detail=f"Doubao ASR response did not contain text: {json.dumps(data, ensure_ascii=False)[:800]}")
    return {
        "transcript": transcript,
        "raw": {
            "provider": "doubao_chat_audio",
            "audioUrl": audio_url,
            "response": data,
        },
    }


def _save_audio_input_for_url(audio: bytes, filename: str, content_type: str) -> tuple[str, str]:
    AUDIO_INPUTS_DIR.mkdir(parents=True, exist_ok=True)
    ext = _audio_extension(content_type, filename)
    saved_name = f"asr-{uuid.uuid4().hex}.{ext}"
    path = AUDIO_INPUTS_DIR / saved_name
    path.write_bytes(audio)
    public_base = settings.public_api_base_url.rstrip("/")
    return f"{public_base}/audio-inputs/{saved_name}", ext


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


async def generate_wallpaper(transcript: str) -> dict:
    return await generate_wallpaper_from_prompt(_build_wallpaper_prompt(transcript))


async def generate_wallpaper_from_prompt(
    prompt: str,
    run_id: str | None = None,
    reference_image_urls: list[str] | None = None,
    aspect_ratio: str | None = None,
    image_size: str | None = None,
) -> dict:
    api_key = settings.effective_image_api_key
    api_base_url = settings.effective_image_api_base_url.rstrip("/")
    endpoint = settings.image_chat_endpoint.strip()

    if not api_key or not api_base_url:
        return {"imageUrl": "", "raw": {"provider": "mock", "prompt": prompt}}

    resolved_refs = [_image_ref_for_upstream(url) for url in (reference_image_urls or []) if url]
    is_gemini_generate_content = "generateContent" in endpoint or settings.image_chat_model.startswith("gemini")
    if is_gemini_generate_content:
        endpoint = resolve_image_endpoint(api_base_url, endpoint, f"/v1beta/models/{settings.image_chat_model}:generateContent")
        payload = _build_gemini_image_payload(prompt, resolved_refs, aspect_ratio=aspect_ratio, image_size=image_size)
    else:
        endpoint = resolve_image_endpoint(api_base_url, endpoint, "/v1/images/generations")
        payload = build_images_generation_payload(
            prompt,
            resolved_refs,
            api_base_url,
            endpoint,
            aspect_ratio=aspect_ratio,
            image_size=image_size,
        )

    timeout_seconds = settings.image_request_timeout_seconds
    attempts = max(1, settings.image_request_retries + 1)
    timeout = httpx.Timeout(timeout_seconds, connect=30, read=timeout_seconds, write=60, pool=30)
    for attempt in range(1, attempts + 1):
        try:
            _log(
                run_id,
                f"Image upstream request model={settings.image_chat_model} endpoint={endpoint} prompt_chars={len(prompt)} refs={len(resolved_refs)} protocol={'gemini_generate_content' if is_gemini_generate_content else 'images_generations'} timeout={timeout_seconds}s attempt={attempt}/{attempts}",
            )
            async with httpx.AsyncClient(timeout=timeout) as client:
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
                _log(run_id, f"Image upstream response status={response.status_code}")
                data = response.json()
                refs = collect_image_refs(data)
                _log(run_id, f"Image refs collected count={len(refs)}")
                if not refs:
                    raise HTTPException(status_code=502, detail=f"Image API response did not contain image data: {json.dumps(data, ensure_ascii=False)[:600]}")
                image_url = await save_image_ref(refs[0], client)
                _log(run_id, f"Image saved url={image_url}")
                return {"imageUrl": image_url, "raw": data}
        except httpx.ReadTimeout as exc:
            _log(run_id, f"Image upstream read timeout attempt={attempt}/{attempts} timeout={timeout_seconds}s")
            if attempt < attempts:
                continue
            raise HTTPException(
                status_code=504,
                detail=(
                    f"Image API timed out after {timeout_seconds}s. "
                    "The upstream image model may still be queued or overloaded. "
                    "You can increase IMAGE_REQUEST_TIMEOUT_SECONDS, retry later, or switch to a dedicated SD/ComfyUI image provider."
                ),
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise HTTPException(status_code=502, detail=f"Image API HTTP {exc.response.status_code}: {exc.response.text[:800]}") from exc
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Image API request failed ({type(exc).__name__}): {repr(exc)}",
            ) from exc
        except (ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=502, detail=f"Image API parse failed: {exc}") from exc


def _build_gemini_image_payload(
    prompt: str,
    resolved_refs: list[str],
    aspect_ratio: str | None = None,
    image_size: str | None = None,
) -> dict:
    parts: list[dict] = []
    for ref in resolved_refs:
        part = _inline_image_part(ref)
        if part:
            parts.append(part)
    parts.append({"text": prompt})
    return {
        "contents": [
            {
                "role": "user",
                "parts": parts,
            }
        ],
        "generationConfig": {
            "imageConfig": {
                "aspectRatio": aspect_ratio or settings.image_aspect_ratio,
                "imageSize": image_size or settings.image_size,
            },
            "responseModalities": ["IMAGE"],
        },
    }


async def recognize_audio(audio: bytes, content_type: str, filename: str = "recording.wav") -> dict:
    asr = await transcribe_audio(audio, filename=filename, content_type=content_type)
    image = await generate_wallpaper(asr["transcript"])
    return {
        "transcript": asr["transcript"],
        "imageUrl": image.get("imageUrl", ""),
        "raw": {"asr": asr.get("raw"), "image": image.get("raw")},
    }


def _log(run_id: str | None, message: str) -> None:
    prefix = f"[agent-run:{run_id}]" if run_id else "[agent-run]"
    print(f"{prefix} {message}", flush=True)
