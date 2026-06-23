import base64
import json
import re
import uuid
from pathlib import Path

import httpx
from fastapi import HTTPException

from app.core.config import settings
from app.mock.transcripts import MOCK_TRANSCRIPTS


GENERATED_DIR = Path(settings.storage_local_dir) / "generated"


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
请根据这段家庭语音留言生成一张竖版手机壁纸主视觉。

留言内容：
{text}

画面要求：
- 9:16 竖版手机壁纸，不要任何文字、水印、UI、按钮、logo。
- 构图必须是左下角一位老人、右上角一位年轻人，形成明显的对角线关系。
- 左下角：一位中国老人/奶奶坐在温暖的小房间或窗边，身边有台灯、茶杯、旧照片、花、餐桌等生活细节。
- 右上角：一位年轻人/子女坐在高处书桌前或窗边，用电脑、手机或台灯回应，空间更开阔明亮。
- 两个人不要站在一起，不要拥抱，不要触摸，不要面对面近距离同框；他们应被山谷、河流、庭院、岩壁、窗洞或光带自然分隔。
- 中间区域用蜿蜒河流、山谷小路、暖色灯光、星光或窗光连接左下与右上，表达远距离陪伴和思念。
- 画风参考温暖奇幻童话感数字插画：细腻笔触、水彩质感、电影感黄昏光、梦境般山谷、拱形窗、藤蔓、柔和云霞。
- 情绪根据留言调整：温柔、想念、被惦记、安静陪伴；不要恐怖、阴暗、压抑或过度悲伤。
- 上方保留较干净的天空/云霞空间，方便叠加锁屏时间。
- 人物不要太大，整体要有纵深感和环境叙事，适合作为手机动态壁纸背景图。
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


async def _save_image_ref(ref: str, client: httpx.AsyncClient) -> str:
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    ref = ref.strip().strip('"').strip("'")

    if ref.startswith("data:image/"):
        header, encoded = ref.split(",", 1)
        ext = _image_extension(header)
        data = base64.b64decode(encoded)
    elif ref.startswith("http://") or ref.startswith("https://"):
        response = await client.get(ref, timeout=60)
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


async def transcribe_audio(audio: bytes, filename: str, content_type: str) -> dict:
    api_key = settings.audio_api_key
    api_base_url = settings.audio_api_base_url.rstrip("/")
    endpoint = settings.audio_transcription_endpoint.strip()

    if not api_key or not api_base_url:
        idx = len(audio) % len(MOCK_TRANSCRIPTS)
        return {"transcript": MOCK_TRANSCRIPTS[idx], "raw": {"provider": "mock", "size": len(audio)}}

    if not endpoint:
        endpoint = f"{api_base_url}/v1/audio/transcriptions"

    try:
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
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Transcription API HTTP {exc.response.status_code}: {exc.response.text[:500]}",
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Transcription API request failed: {exc}") from exc

    try:
        data = response.json()
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail=f"Transcription API returned non-JSON response: {response.text[:300]}") from exc

    transcript = _response_text(data)
    if not transcript:
        raise HTTPException(status_code=502, detail=f"Transcription API response did not contain text: {json.dumps(data, ensure_ascii=False)[:500]}")
    return {"transcript": transcript, "raw": data}


async def generate_wallpaper(transcript: str) -> dict:
    api_key = settings.image_api_key or settings.audio_api_key
    api_base_url = (settings.image_api_base_url or settings.audio_api_base_url).rstrip("/")
    endpoint = settings.image_chat_endpoint.strip()
    prompt = _build_wallpaper_prompt(transcript)

    if not api_key or not api_base_url:
        return {"imageUrl": "", "raw": {"provider": "mock", "prompt": prompt}}

    if not endpoint:
        endpoint = f"{api_base_url}/v1/chat/completions"

    payload = {
        "model": settings.image_chat_model,
        "stream": False,
        "messages": [{"role": "user", "content": prompt}],
    }

    try:
        async with httpx.AsyncClient(timeout=180) as client:
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
            data = response.json()
            refs = _collect_image_refs(data)
            if not refs:
                raise HTTPException(status_code=502, detail=f"Image API response did not contain image data: {json.dumps(data, ensure_ascii=False)[:600]}")
            image_url = await _save_image_ref(refs[0], client)
            return {"imageUrl": image_url, "raw": data}
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail=f"Image API HTTP {exc.response.status_code}: {exc.response.text[:800]}") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Image API request failed: {exc}") from exc
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=502, detail=f"Image API parse failed: {exc}") from exc


async def recognize_audio(audio: bytes, content_type: str, filename: str = "recording.wav") -> dict:
    asr = await transcribe_audio(audio, filename=filename, content_type=content_type)
    image = await generate_wallpaper(asr["transcript"])
    return {
        "transcript": asr["transcript"],
        "imageUrl": image.get("imageUrl", ""),
        "raw": {"asr": asr.get("raw"), "image": image.get("raw")},
    }
