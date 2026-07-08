"""
Initial wallpaper generation for the onboarding pipeline.

This service produces the "empty landscape" — a pure-scene wallpaper with
no characters — that serves as the default background when the user first
enters the WallpaperStage via PreludeStep.

The generated image is later regional-inpainted (Step 3) after the user
records a voice memo and ASR returns a transcript.  Future steps will
compose character portraits into the pre-defined upper-right and lower-left
zones of this base landscape.

IMPORTANT: the two avatar images passed as positional arguments are accepted
for backward compatibility with the route signature only.  They are
deliberately not used in the generation — no character portraits are
included in the initial wallpaper regardless of what the user uploaded.

Two API paths are supported:

1. Gemini native (default when OPENAI_API_KEY is set):
   {GEMINI_BASE_URL}/v1beta/models/{GEMINI_IMAGE_MODEL}:generateContent?key={OPENAI_API_KEY}

2. OpenAI-compatible chat completions (fallback):
   {IMAGE_API_BASE_URL}/v1/chat/completions  (model=IMAGE_CHAT_MODEL)
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import re
import uuid
from pathlib import Path

from PIL import Image

import httpx
from fastapi import HTTPException

from app.core.config import settings


# ============================================================================
# Debug
# ============================================================================

DEBUG_SAVE_INPUTS = True
DEBUG_DIR = Path(settings.storage_local_dir) / "debug_generation_inputs"


# ============================================================================
# Filesystem layout
# ============================================================================

GENERATED_DIR = Path(settings.storage_local_dir) / "generated"


def _public_url(path: str) -> str:
    base = settings.public_api_base_url.rstrip("/")
    return f"{base}{path}"


def _image_extension(content_type: str) -> str:
    if "jpeg" in content_type or "jpg" in content_type:
        return "jpg"
    if "webp" in content_type:
        return "webp"
    if "png" in content_type:
        return "png"
    return "jpg"


# ============================================================================
# Image fetch / save helpers
# ============================================================================

async def _fetch_and_save_image(url_or_b64: str, client: httpx.AsyncClient) -> str:
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    ref = url_or_b64.strip().strip('"').strip("'")

    if ref.startswith("data:image/"):
        header, encoded = ref.split(",", 1)
        ext = _image_extension(header)
        data = base64.b64decode(encoded)
    elif ref.startswith("http://") or ref.startswith("https://"):
        response = await client.get(ref, timeout=120)
        response.raise_for_status()
        ext = _image_extension(response.headers.get("content-type", ""))
        data = response.content
    else:
        ext = "jpg"
        data = base64.b64decode(re.sub(r"\s+", "", ref))

    filename = f"wallpaper-{uuid.uuid4().hex}.{ext}"
    path = GENERATED_DIR / filename
    path.write_bytes(data)
    return f"/generated/{filename}"


def _save_data_uri(data_uri: str) -> str:
    """Decode a data URI, save to GENERATED_DIR, return the URL path."""
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    header, encoded = data_uri.split(",", 1)
    ext = _image_extension(header)
    data = base64.b64decode(encoded)
    filename = f"wallpaper-{uuid.uuid4().hex}.{ext}"
    path = GENERATED_DIR / filename
    path.write_bytes(data)
    return f"/generated/{filename}"


# ============================================================================
# Gemini call
# ============================================================================

_sync_http = httpx.Client(timeout=200)


def _call_gemini_native(
    prompt: str,
    aspect_ratio: str = "9:16",
) -> str:
    """
    Call the Gemini native generateContent endpoint and return a data URI.
    """
    api_key = settings.openai_api_key
    base_url = settings.gemini_base_url.rstrip("/").removesuffix("/v1")
    model = settings.gemini_image_model

    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY not configured. "
            "Set it in .env or as an environment variable."
        )

    url = f"{base_url}/v1beta/models/{model}:generateContent?key={api_key}"

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseModalities": ["TEXT", "IMAGE"],
            "imageConfig": {"aspectRatio": aspect_ratio},
        },
    }

    response = _sync_http.post(url, json=payload)
    if response.status_code >= 400:
        raise RuntimeError(f"Gemini API error {response.status_code}: {response.text}")

    data = response.json()

    if "error" in data:
        raise RuntimeError(
            f"Gemini API error [{data['error'].get('code', 'unknown')}]: "
            f"{data['error'].get('message', 'Unknown error')}"
        )

    if "promptFeedback" in data and data["promptFeedback"].get("blockReason"):
        raise RuntimeError(
            f"Gemini blocked the request: {data['promptFeedback'].get('blockReason')}"
        )

    candidates = data.get("candidates", [])
    if not candidates:
        raise RuntimeError("Gemini returned empty candidates")

    for part in (candidates[0].get("content", {}) or {}).get("parts", []):
        inline = part.get("inlineData") or part.get("inline_data")
        if inline and inline.get("data"):
            mime_type = inline.get("mimeType", "image/png")
            return f"data:{mime_type};base64,{inline['data']}"

    raise RuntimeError("Gemini did not return inline image data")


# ============================================================================
# OpenAI-compatible chat completions path (fallback)
# ============================================================================

def _collect_image_refs(payload) -> list[str]:
    refs: list[str] = []

    if isinstance(payload, str):
        text = payload.strip()
        if not text:
            return refs
        refs.extend(re.findall(r"!\[[^\]]*]\(([^)]+)\)", text))
        refs.extend(
            re.findall(r"(data:image/[a-zA-Z0-9.+-]+;base64,[A-Za-z0-9+/=\n\r]+)", text)
        )
        refs.extend(re.findall(r"https?://[^\s)'\"<>]+", text))
        try:
            refs.extend(_collect_image_refs(json.loads(text)))
        except (json.JSONDecodeError, ValueError):
            if len(text) > 500 and re.fullmatch(r"[A-Za-z0-9+/=\s]+", text):
                refs.append(text)
        return refs

    if isinstance(payload, list):
        for item in payload:
            refs.extend(_collect_image_refs(item))
        return refs

    if isinstance(payload, dict):
        for key in ("url", "image_url", "b64_json", "base64", "data"):
            if key in payload:
                refs.extend(_collect_image_refs(payload[key]))
        for item in payload.values():
            refs.extend(_collect_image_refs(item))
        return refs

    return refs


# ============================================================================
# The landscape prompt
# ============================================================================

INITIAL_WALLPAPER_PROMPT = (
    "Create a full-bleed vertical 9:16 illustrated wallpaper.\n\n"
    "This must be a single complete seamless scene that fills the entire "
    "canvas edge-to-edge like a real phone wallpaper.\n"
    "Do NOT generate white borders, paper edges, postcard frames, polaroid "
    "margins, printed card layouts, or split-page illustration blocks.\n\n"
    "Do not include any people or human figures.\n\n"
    "Design one coherent environment with three spatial parts:\n"
    "1) A larger and more open upper-right area reserved for a future "
    "younger character — visually spacious, elevated, open, and calm. "
    "Include environmental details such as a desk, a lamp, books, plants, "
    "a window opening, railing, or a quiet workspace-like arrangement, "
    "but no person.\n\n"
    "2) A smaller but still clear and cozy lower-left area reserved for "
    "a future elder character — warm, intimate, restful, and stable. "
    "Include details such as an armchair, a small tea table, a window, "
    "a bookshelf, a floor lamp, or teaware, but no person.\n\n"
    "3) A gentle central connecting area between them.\n"
    "The central area must NOT be a huge cliff, deep canyon, empty vertical "
    "void, or dramatic ravine.\n"
    "Instead create a softer connection such as:\n"
    "  - a small valley,\n"
    "  - a gentle slope,\n"
    "  - a winding path,\n"
    "  - a narrow stream,\n"
    "  - a small river,\n"
    "  - or a subtle natural passage between the two spaces.\n"
    "The scene must feel like one continuous world, not two separate images.\n\n"
    "Visual style: warm, gentle, painterly, storybook-like, dreamy but "
    "coherent, hand-painted illustration, watercolor or gouache-like "
    "softness, soft warm light, whimsical cinematic environment, "
    "elegant composition.\n\n"
    "IMPORTANT CONSTRAINTS:\n"
    "  - Full wallpaper image only.\n"
    "  - No white border.\n"
    "  - No paper frame.\n"
    "  - No collage.\n"
    "  - No separate mini scenes.\n"
    "  - No humans.\n"
    "  - No text.\n"
    "  - No watermark.\n"
    "  - No UI.\n"
    "  - No giant dramatic ravine.\n"
    "  - No overly steep vertical drop.\n"
    "  - No framed print or postcard.\n\n"
    "The final result should be a clean, complete, visually coherent wallpaper "
    "prepared for future character insertion."
)


# ============================================================================
# Public entry point
# ============================================================================

async def generate_dual_character_wallpaper(
    self_image_bytes: bytes,
    partner_image_bytes: bytes,
) -> dict:
    """
    Generate the initial empty-landscape wallpaper.

    The two avatar image arguments are accepted only for backward
    compatibility with the existing route signature.  They are not used
    in any part of the generation — no characters appear in the output.

    Returns ``{"imageUrl": "/generated/wallpaper-<uuid>.png"}`` on success.
    When no API key is configured the service returns ``{"imageUrl": ""}``
    so the caller falls back to the static today-bg.jpg shipped with the
    project.
    """
    # ── Provider checks ───────────────────────────────────────────────────────
    use_gemini = bool(settings.openai_api_key)
    use_openai = bool(settings.image_api_key or settings.audio_api_key)

    openai_key_preview = ""
    if settings.openai_api_key:
        k = settings.openai_api_key
        openai_key_preview = f"{k[:4]}...{k[-4:]}" if len(k) > 8 else "***"

    print("[wallpaper.service] start generate_landscape_wallpaper")
    print(f"[wallpaper.service] has_openai_key = {bool(settings.openai_api_key)}")
    print(f"[wallpaper.service] openai_key_preview = {openai_key_preview}")
    print(f"[wallpaper.service] gemini_base_url = {settings.gemini_base_url}")
    print(f"[wallpaper.service] gemini_image_model = {settings.gemini_image_model}")

    if not use_gemini and not use_openai:
        print("[wallpaper.service] provider = fallback_no_key")
        print("[wallpaper.service] no usable api key, returning empty imageUrl")
        return {
            "imageUrl": "",
            "raw": {
                "provider": "fallback",
                "note": "No image API credentials configured; frontend will use today-bg.jpg",
            },
        }

    # ── Log ignored inputs ────────────────────────────────────────────────────
    # Avatar bytes are accepted but not forwarded to the model.
    print(
        f"[wallpaper.service] self_image_bytes bytes received = "
        f"{len(self_image_bytes)} (IGNORED — landscape generation uses no avatars)"
    )
    print(
        f"[wallpaper.service] partner_image_bytes bytes received = "
        f"{len(partner_image_bytes)} (IGNORED — landscape generation uses no avatars)"
    )

    if DEBUG_SAVE_INPUTS:
        DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        run_id = uuid.uuid4().hex[:8]
        debug_self_path = DEBUG_DIR / f"debug-ignored-self-{run_id}.jpg"
        debug_partner_path = DEBUG_DIR / f"debug-ignored-partner-{run_id}.jpg"
        # Write the ignored avatars so developers can inspect what was uploaded.
        debug_self_path.write_bytes(self_image_bytes)
        debug_partner_path.write_bytes(partner_image_bytes)
        print(f"[wallpaper.service] debug saved ignored self     = {debug_self_path}")
        print(f"[wallpaper.service] debug saved ignored partner  = {debug_partner_path}")

    print(f"[wallpaper.service] prompt len = {len(INITIAL_WALLPAPER_PROMPT)} chars")

    # ── Gemini native path ────────────────────────────────────────────────────
    if use_gemini:
        print("[wallpaper.service] provider = gemini")
        print("[wallpaper.service] sending request to image API...")

        try:
            data_uri: str = await asyncio.to_thread(
                _call_gemini_native, INITIAL_WALLPAPER_PROMPT
            )
        except Exception as exc:
            print(f"[wallpaper.service] image API failed: {exc}")
            raise HTTPException(
                status_code=502,
                detail=f"Gemini generation failed: {exc}",
            ) from exc

        rel_path = _save_data_uri(data_uri)
        path = GENERATED_DIR / Path(rel_path).name
        print(f"[wallpaper.service] saved to path = {path}")
        print(f"[wallpaper.service] saved exists = {path.exists()}")
        image_url = _public_url(rel_path)
        print(f"[wallpaper.service] returning imageUrl = {image_url}")
        return {
            "imageUrl": image_url,
            "raw": {
                "provider": "gemini",
                "model": settings.gemini_image_model,
                "type": "landscape_only",
            },
        }

    # ── OpenAI-compatible chat completions path ────────────────────────────────
    print("[wallpaper.service] provider = openai-compatible")

    api_key = settings.image_api_key or settings.audio_api_key
    api_base = (settings.image_api_base_url or settings.audio_api_base_url).rstrip("/")
    endpoint = settings.image_chat_endpoint.strip() or f"{api_base}/v1/chat/completions"

    payload = {
        "model": settings.image_chat_model,
        "stream": False,
        "messages": [{"role": "user", "content": INITIAL_WALLPAPER_PROMPT}],
    }

    try:
        async with httpx.AsyncClient(timeout=180) as client:
            print(f"[wallpaper.service] sending request to {endpoint}")
            response = await client.post(
                endpoint,
                json=payload,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
            print(f"[wallpaper.service] response status = {response.status_code}")
            response.raise_for_status()
            data = response.json()
            print(f"[wallpaper.service] response keys = {list(data.keys()) if isinstance(data, dict) else type(data).__name__}")
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Image API HTTP {exc.response.status_code}: {exc.response.text[:800]}",
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502, detail=f"Image API request failed: {exc}"
        ) from exc

    refs = _collect_image_refs(data)
    print(f"[wallpaper.service] image refs count = {len(refs)}")
    if not refs:
        raise HTTPException(
            status_code=502,
            detail=f"Image API response did not contain image data: {json.dumps(data, ensure_ascii=False)[:600]}",
        )

    async with httpx.AsyncClient(timeout=180) as client:
        image_url = _public_url(await _fetch_and_save_image(refs[0], client))

    return {"imageUrl": image_url, "raw": {"type": "landscape_only", **data}}
