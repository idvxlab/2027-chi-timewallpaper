from __future__ import annotations

import base64
import io
import json
import re
import uuid
from pathlib import Path

import httpx
from fastapi import HTTPException
from PIL import Image

from app.core.config import settings


GENERATED_DIR = Path(settings.storage_local_dir) / "generated"


def resolve_image_endpoint(api_base_url: str, endpoint: str, default_path: str) -> str:
    endpoint = (endpoint or "").strip()
    if endpoint.startswith(("http://", "https://")):
        return endpoint
    api_base_url = api_base_url.rstrip("/")
    if endpoint.startswith("/"):
        return f"{api_base_url}{endpoint}"
    return f"{api_base_url}{default_path}"


def image_size_for_openai(aspect_ratio: str | None = None, image_size: str | None = None) -> str:
    size = (image_size or settings.image_size or "").strip()
    if re.fullmatch(r"\d+x\d+", size):
        return size
    aspect = (aspect_ratio or settings.image_aspect_ratio or "").strip()
    if aspect == "9:16":
        return "1024x1536"
    if aspect == "16:9":
        return "1536x1024"
    if aspect in {"1:1", ""}:
        return "1024x1024"
    return "1024x1536"


def build_images_generation_payload(
    prompt: str,
    resolved_refs: list[str],
    api_base_url: str,
    endpoint: str,
    aspect_ratio: str | None = None,
    image_size: str | None = None,
) -> dict:
    resolved_aspect_ratio = (aspect_ratio or settings.image_aspect_ratio or "").strip()
    payload: dict = {
        "model": settings.image_chat_model,
        "prompt": prompt,
        "size": image_size_for_openai(resolved_aspect_ratio, image_size),
    }
    # Some OpenAI-compatible aggregators support image references on generations.
    # Official OpenAI generations does not use this field; edits handles image files.
    if resolved_refs and "api.openai.com" not in f"{api_base_url} {endpoint}".lower():
        payload["image"] = resolved_refs
        payload["aspect_ratio"] = resolved_aspect_ratio
    elif "api.openai.com" not in f"{api_base_url} {endpoint}".lower() and resolved_aspect_ratio:
        payload["aspect_ratio"] = resolved_aspect_ratio
    return payload


async def call_openai_image_edit_to_pil(
    *,
    prompt: str,
    base_image: Image.Image,
    mask: Image.Image | None = None,
    identity_image: Image.Image | None = None,
) -> Image.Image:
    api_key = settings.effective_image_api_key
    api_base_url = settings.effective_image_api_base_url.rstrip("/")
    if not api_key or not api_base_url:
        raise RuntimeError("PROVIDER_API_BASE_URL / PROVIDER_API_KEY not configured for image edits.")

    endpoint = resolve_image_endpoint(api_base_url, settings.image_edit_endpoint, "/v1/images/edits")
    data = {
        "model": settings.image_chat_model,
        "prompt": prompt,
        "size": image_size_for_openai(),
    }
    image_field = "image[]" if identity_image is not None else "image"
    files: list[tuple[str, tuple[str, bytes, str]]] = [
        (image_field, ("base.png", _pil_to_png_bytes(base_image), "image/png")),
    ]
    if identity_image is not None:
        files.append((image_field, ("identity.png", _pil_to_png_bytes(identity_image), "image/png")))
    if mask is not None:
        files.append(("mask", ("mask.png", _openai_mask_png_bytes(mask), "image/png")))

    timeout_seconds = settings.image_request_timeout_seconds
    timeout = httpx.Timeout(timeout_seconds, connect=30, read=timeout_seconds, write=120, pool=30)
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            endpoint,
            data=data,
            files=files,
            headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(f"OpenAI image edit HTTP {exc.response.status_code}: {exc.response.text[:800]}") from exc
        payload = response.json()
        refs = collect_image_refs(payload)
        if not refs:
            raise RuntimeError(f"OpenAI image edit returned no image data: {json.dumps(payload, ensure_ascii=False)[:600]}")
        raw = await image_ref_to_bytes(refs[0], client)
    return Image.open(io.BytesIO(raw)).convert("RGB")


def collect_image_refs(value) -> list[str]:
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
            refs.extend(collect_image_refs(json.loads(text)))
        except (json.JSONDecodeError, ValueError):
            pass
        return refs
    if isinstance(value, list):
        for item in value:
            refs.extend(collect_image_refs(item))
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
                refs.extend(collect_image_refs(value[key]))
        for item in value.values():
            refs.extend(collect_image_refs(item))
    return refs


async def image_ref_to_bytes(ref: str, client: httpx.AsyncClient) -> bytes:
    ref = ref.strip().strip('"').strip("'")
    if ref.startswith("data:image/"):
        return base64.b64decode(ref.split(",", 1)[1])
    if ref.startswith(("http://", "https://")):
        response = await client.get(ref, timeout=settings.image_download_timeout_seconds)
        response.raise_for_status()
        return response.content
    return base64.b64decode(re.sub(r"\s+", "", ref))


async def save_image_ref(ref: str, client: httpx.AsyncClient) -> str:
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    data = await image_ref_to_bytes(ref, client)
    filename = f"wallpaper-{uuid.uuid4().hex}.png"
    path = GENERATED_DIR / filename
    path.write_bytes(data)
    return f"/generated/{filename}"


def _pil_to_png_bytes(image: Image.Image) -> bytes:
    buf = io.BytesIO()
    image.convert("RGBA").save(buf, format="PNG")
    return buf.getvalue()


def _openai_mask_png_bytes(mask: Image.Image) -> bytes:
    # OpenAI edits use transparent pixels as the editable region.
    alpha = Image.eval(mask.convert("L"), lambda p: 255 - p)
    transparent_edit_mask = Image.new("RGBA", mask.size, (255, 255, 255, 255))
    transparent_edit_mask.putalpha(alpha)
    return _pil_to_png_bytes(transparent_edit_mask)
