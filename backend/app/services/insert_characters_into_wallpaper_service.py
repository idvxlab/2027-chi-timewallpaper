"""
Two-pass masked character insertion into a pre-generated landscape wallpaper.

Flow
====
Step 1 (Pass 1): insert the younger character (upper-right mask)
    base landscape + young mask + young portrait → Gemini → composite → intermediate

Step 2 (Pass 2): insert the elder character (lower-left mask)
    intermediate + elder mask + elder portrait → Gemini → composite → final

The composite is the critical safety layer: for each pass, the region outside
the mask is forced back to the original so the environment is never
rewritten by Gemini — only the masked character zones can change.

API
===
POST /generate-wallpaper/insert-characters
    baseImageUrl:    URL of the landscape wallpaper produced by Step 1
    younger_image:    UploadFile  — portrait of the younger character
    elder_image:     UploadFile  — portrait of the elder character
    transcript:      str         — drives the younger character's activity

Returns
=======
{"imageUrl": "http://localhost:8000/generated/wallpaper-final-<id>.png", "raw": {...}}
On any failure: {"imageUrl": ""}  (caller keeps the landscape-only wallpaper)
"""

from __future__ import annotations

import asyncio
import base64
import io
import os
import uuid
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image, ImageDraw, ImageFilter

import httpx
from fastapi import HTTPException

from app.core.config import settings


# ============================================================================
# Layout
# ============================================================================

GENERATED_DIR = Path(settings.storage_local_dir) / "generated"
DEBUG_DIR     = Path(settings.storage_local_dir) / "debug_generation_inputs"

# Upper-right (younger character) mask — x: 48%–100%, y: 0%–58%
YOUNG_POLYGON = (
    (0.48, 0.00),
    (1.00, 0.00),
    (1.00, 0.58),
    (0.58, 0.58),
    (0.48, 0.45),
)

# Lower-left (elder character) mask — x: 0%–58%, y: 42%–100%
ELDER_POLYGON = (
    (0.00, 0.42),
    (0.58, 0.42),
    (0.58, 1.00),
    (0.00, 1.00),
)


# ============================================================================
# Public URL helpers
# ============================================================================

def _public_url(path: str) -> str:
    base = settings.public_api_base_url.rstrip("/")
    return f"{base}{path}"


def _resolve_frontend_public_dir() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        candidate = parent / "frontend" / "public"
        if candidate.is_dir():
            return candidate
    raise FileNotFoundError(
        "Could not locate frontend/public/ relative to this service."
    )


# ============================================================================
# Mask utilities
# ============================================================================

def _polygon_mask(
    size: tuple[int, int],
    polygon_fracs: tuple[tuple[float, float], ...],
    feather_radius: int,
) -> Image.Image:
    w, h = size
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    pts = [(int(x * w), int(y * h)) for x, y in polygon_fracs]
    draw.polygon(pts, fill=255)
    if feather_radius > 0:
        mask = mask.filter(ImageFilter.GaussianBlur(radius=feather_radius))
    return mask


def _save_debug_mask(
    mask: Image.Image,
    size: tuple[int, int],
    tag: str,
    run_id: str,
) -> Path:
    """Save a binary mask PNG and a red-overlay preview."""
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)

    path = DEBUG_DIR / f"debug-mask-{tag}-{run_id}.png"
    mask.save(path)

    # Red overlay on the mask so devs can visually confirm the region.
    overlay = Image.new("RGBA", size, (255, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    pts = [(int(x * size[0]), int(y * size[1]))
           for x, y in (YOUNG_POLYGON if tag.startswith("young") else ELDER_POLYGON)]
    overlay_rgba = Image.new("RGBA", size, (255, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay_rgba)
    overlay_draw.polygon(pts, fill=(255, 0, 0, 110))
    return path


# ============================================================================
# Base image resolution
# ============================================================================

def _resolve_base_image(base_image_url: str) -> Path:
    raw = (base_image_url or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="baseImageUrl is required")
    print(f"[wallpaper.insert.service] baseImageUrl = {raw!r}")

    parsed = urlparse(raw)
    path_str = parsed.path or raw

    if path_str.startswith("/generated/"):
        rel = path_str[len("/generated/"):]
        filename = Path(rel).name
        if not filename or ".." in rel:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid /generated/ path: {path_str!r}",
            )
        target = GENERATED_DIR / filename
        target_r = target.resolve()
        if not str(target_r).startswith(str(GENERATED_DIR.resolve())):
            raise HTTPException(status_code=400, detail="Path escapes GENERATED_DIR")
    elif path_str.startswith("/wallpaper/"):
        rel = path_str[len("/wallpaper/"):]
        filename = Path(rel).name
        if not filename or ".." in rel:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid /wallpaper/ path: {path_str!r}",
            )
        target = _resolve_frontend_public_dir() / "wallpaper" / filename
    else:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported baseImageUrl: {path_str!r}. "
                "Expected /generated/... or /wallpaper/..."
            ),
        )

    print(f"[wallpaper.insert.service] resolved base image = {target}")
    print(f"[wallpaper.insert.service] base image exists = {target.exists()}")
    return target


# ============================================================================
# Image I/O
# ============================================================================

def _image_to_data_uri(img: Image.Image, fmt: str = "PNG") -> str:
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    mime = "image/png" if fmt.upper() != "JPEG" else "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(buf.getvalue()).decode()}"


def _image_extension(mime_or_path: str) -> str:
    s = mime_or_path.lower()
    if "jpeg" in s or "jpg" in s:
        return "jpg"
    if "webp" in s:
        return "webp"
    return "png"


# ============================================================================
# Composite helper — the environment safety net
# ============================================================================

def _composite_region(
    original: Image.Image,
    edited: Image.Image,
    mask: Image.Image,
) -> Image.Image:
    """
    Force pixels outside the mask back to the original.
    This is what guarantees the landscape environment is never rewritten.
    """
    w, h = original.size
    if edited.size != (w, h):
        edited = edited.resize((w, h), Image.LANCZOS)
    if mask.size != (w, h):
        mask = mask.resize((w, h), Image.LANCZOS)

    return Image.composite(
        edited.convert("RGBA"),
        original.convert("RGBA"),
        mask.convert("L"),
    ).convert("RGB")


# ============================================================================
# Prompt builders
# ============================================================================

WORKING_KEYWORDS = (
    "工作", "上班", "开会", "写代码", "忙", "办公",
    "work", "office", "coding", "busy",
)


def _build_young_prompt(transcript: str, has_portrait: bool) -> str:
    is_working = any(
        kw.lower() in (transcript or "").lower() for kw in WORKING_KEYWORDS
    )

    # ── Identity section ────────────────────────────────────────────────────
    if has_portrait:
        identity_block = (
            "Reference Image 2 is the younger character's identity reference photo.\n\n"
            "CRITICAL IDENTITY REQUIREMENT:\n"
            "The inserted younger character must clearly resemble the provided "
            "younger identity reference. Preserve the person's recognizable "
            "identity features as much as possible, especially:\n"
            "  - face shape\n"
            "  - hairstyle\n"
            "  - facial proportions\n"
            "  - age impression\n"
            "  - overall appearance and likeness\n"
            "Do NOT paste the photo directly. Repaint the person using the "
            "face, hairstyle, and general appearance from Reference Image 2, "
            "but render the figure entirely in the warm hand-painted storybook "
            "style of the wallpaper. Keep the same person recognizable after "
            "stylization. Prioritize identity consistency over novelty.\n"
        )
    else:
        identity_block = (
            "Create an illustrated younger character that fits naturally into "
            "the hand-painted storybook environment. "
            "The character should have a clear, distinct identity — "
            "do not generate a generic or interchangeable figure.\n"
        )

    # ── Activity section ───────────────────────────────────────────────────
    if is_working:
        activity_block = (
            "The younger character is working quietly at a desk, focused on a "
            "laptop or a desk task. "
            "Add only subtle work-related details such as a laptop, papers, "
            "books, a desk lamp, or a small desk surface."
        )
    else:
        activity_block = (
            "The younger character is working quietly at a desk, "
            "focused on a laptop or a desk task. "
            "Add only subtle work-related details if needed."
        )

    # ── Assembly ───────────────────────────────────────────────────────────
    return (
        "Edit only the masked upper-right region.\n\n"
        "Reference Image 1 is the base wallpaper environment. "
        "Preserve all unmasked areas exactly — do not redesign, repaint, "
        "or alter any part of Reference Image 1 outside this mask.\n\n"
        + identity_block + "\n"
        + activity_block + "\n\n"
        "The character must feel naturally embedded into the scene, with matching:\n"
        "  - perspective\n"
        "  - lighting\n"
        "  - colour palette\n"
        "  - painterly texture\n"
        "  - softness of edges\n"
        "  - scene atmosphere\n\n"
        "CRITICAL ENVIRONMENT CONSTRAINTS:\n"
        "  - Preserve all unmasked areas exactly as they appear in Reference Image 1.\n"
        "  - Do NOT change the sky, valley, cliffs, architecture, lighting, "
        "plants, or any background outside the upper-right mask.\n"
        "  - Do not redesign the environment.\n"
        "  - Do not alter the overall composition.\n"
        "  - Do not add extra people.\n\n"
        "No text, no logo, no watermark, no UI."
    )


ELDER_PROMPT = (
    "Edit only the masked lower-left region.\n\n"
    "Reference Image 1 is the base wallpaper environment. "
    "Preserve all unmasked areas exactly — do not redesign, repaint, "
    "or alter any part of Reference Image 1 outside this mask.\n\n"
    "Reference Image 2 is the elder character's identity reference photo.\n\n"
    "CRITICAL IDENTITY REQUIREMENT:\n"
    "The inserted elder character must clearly resemble the provided elder "
    "identity reference. Preserve the person's recognizable identity features "
    "as much as possible, especially:\n"
    "  - face shape\n"
    "  - hairstyle\n"
    "  - age impression\n"
    "  - key facial features\n"
    "  - gentle overall likeness\n"
    "Do NOT paste the photo directly. Repaint the person using the face, "
    "hairstyle, and general appearance from Reference Image 2, but render "
    "the figure entirely in the warm hand-painted storybook style of the "
    "wallpaper. Keep the same person recognizable after stylization. "
    "Prioritize identity consistency over novelty.\n\n"
    "The elder character is sitting calmly, drinking tea or holding a teacup, "
    "with a peaceful and warm companion-like presence. "
    "The character must feel naturally embedded into the scene, with matching:\n"
    "  - perspective\n"
    "  - lighting\n"
    "  - colour palette\n"
    "  - painterly texture\n"
    "  - softness of edges\n"
    "  - scene atmosphere\n\n"
    "CRITICAL ENVIRONMENT CONSTRAINTS:\n"
    "  - Preserve all unmasked areas exactly as they appear in Reference Image 1.\n"
    "  - Do NOT change the sky, valley, cliffs, architecture, lighting, "
    "plants, or any background outside the lower-left mask.\n"
    "  - Do not redesign the environment.\n"
    "  - Do not alter the overall composition.\n"
    "  - Do not add extra people.\n\n"
    "No text, no logo, no watermark, no UI."
)


# ============================================================================
# Gemini call — mirrors existing pattern, accepts optional identity portrait
# ============================================================================

_sync_http = httpx.Client(timeout=200)


def _call_gemini_inpaint(
    prompt: str,
    base_image: Image.Image,
    mask: Image.Image,
    identity_image: Image.Image | None = None,
    aspect_ratio: str = "9:16",
) -> Image.Image:
    """
    Call Gemini with base image + mask + optional identity portrait.
    Returns a PIL image.
    """
    api_key = settings.openai_api_key
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not configured.")
    base_url = settings.gemini_base_url.rstrip("/").removesuffix("/v1")
    model = settings.gemini_image_model
    url = f"{base_url}/v1beta/models/{model}:generateContent?key={api_key}"

    base_uri = _image_to_data_uri(base_image, fmt="PNG")
    mask_uri = _image_to_data_uri(mask.convert("L"), fmt="PNG")

    parts: list[dict] = [
        {"text": prompt},
        {
            "inlineData": {
                "mimeType": "image/png",
                "data": base_uri.split(",", 1)[1],
            }
        },
        {
            "inlineData": {
                "mimeType": "image/png",
                "data": mask_uri.split(",", 1)[1],
            }
        },
    ]
    if identity_image is not None:
        id_uri = _image_to_data_uri(identity_image, fmt="PNG")
        parts.append({
            "inlineData": {
                "mimeType": "image/png",
                "data": id_uri.split(",", 1)[1],
            }
        })

    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {
            "responseModalities": ["TEXT", "IMAGE"],
            "imageConfig": {"aspectRatio": aspect_ratio},
        },
    }

    response = _sync_http.post(url, json=payload)
    if response.status_code >= 400:
        raise RuntimeError(f"Gemini inpaint error {response.status_code}: {response.text}")

    data = response.json()
    if "error" in data:
        raise RuntimeError(
            f"Gemini inpaint error [{data['error'].get('code', 'unknown')}]: "
            f"{data['error'].get('message', 'Unknown error')}"
        )
    if "promptFeedback" in data and data["promptFeedback"].get("blockReason"):
        raise RuntimeError(
            f"Gemini blocked: {data['promptFeedback'].get('blockReason')}"
        )

    candidates = data.get("candidates", [])
    if not candidates:
        raise RuntimeError("Gemini inpaint returned empty candidates")

    for part in (candidates[0].get("content", {}) or {}).get("parts", []):
        inline = part.get("inlineData") or part.get("inline_data")
        if inline and inline.get("data"):
            raw = base64.b64decode(inline["data"])
            return Image.open(io.BytesIO(raw)).convert("RGB")

    raise RuntimeError("Gemini inpaint did not return inline image data")


# ============================================================================
# Avatar loading helpers
# ============================================================================

def _load_avatar(file_bytes: bytes) -> Image.Image:
    """Convert raw bytes to a PIL RGB image, stripping alpha."""
    img = Image.open(io.BytesIO(file_bytes)).convert("RGB")
    w, h = img.size
    if max(w, h) > 1024:
        ratio = 1024 / max(w, h)
        img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
    return img


# ============================================================================
# Public entry point
# ============================================================================

async def insert_characters_into_wallpaper(
    *,
    base_image_url: str,
    younger_image_bytes: bytes,
    elder_image_bytes: bytes,
    transcript: str,
) -> dict:
    """
    Two-pass masked character insertion.

    Pass 1: insert the younger character into the upper-right mask.
    Pass 2: insert the elder character into the lower-left mask.

    Both passes use a composite safety layer so the landscape outside
    each mask is strictly preserved from the original base image.

    Returns ``{"imageUrl": "...", "raw": {...}}`` on success and
    ``{"imageUrl": ""}`` on any recoverable failure so the caller can
    fall back to the landscape-only wallpaper without crashing.
    """
    print("[wallpaper.insert.service] insert_characters_into_wallpaper start")
    print(f"[wallpaper.insert.service] baseImageUrl = {base_image_url!r}")
    print(f"[wallpaper.insert.service] transcript   = {transcript!r}")
    print(f"[wallpaper.insert.service] younger_bytes = {len(younger_image_bytes)}")
    print(f"[wallpaper.insert.service] elder_bytes   = {len(elder_image_bytes)}")

    # ── Resolve base ──────────────────────────────────────────────────────
    base_path = _resolve_base_image(base_image_url)
    if not base_path.exists():
        msg = f"Base image not found: {base_path}"
        print(f"[wallpaper.insert.service] {msg}")
        return {"imageUrl": "", "raw": {"provider": "fallback", "error": msg}}

    try:
        base_image = Image.open(base_path).convert("RGB")
    except Exception as exc:
        msg = f"Failed to open base image: {exc}"
        print(f"[wallpaper.insert.service] {msg}")
        return {"imageUrl": "", "raw": {"provider": "fallback", "error": msg}}

    w, h = base_image.size
    print(f"[wallpaper.insert.service] base image size = {w}x{h}")

    run_id = uuid.uuid4().hex[:8]

    # ── Load avatar portraits ───────────────────────────────────────────────
    try:
        young_img = _load_avatar(younger_image_bytes)
        elder_img = _load_avatar(elder_image_bytes)
    except Exception as exc:
        msg = f"Failed to load avatar images: {exc}"
        print(f"[wallpaper.insert.service] {msg}")
        return {"imageUrl": "", "raw": {"provider": "fallback", "error": msg}}

    # ── Pass 1: younger character (upper-right) ─────────────────────────────
    feather = max(8, min(w, h) // 60)
    young_mask = _polygon_mask((w, h), YOUNG_POLYGON, feather)
    print(f"[wallpaper.insert.service] young feather={feather} mask_size={young_mask.size}")

    _save_debug_mask(young_mask, (w, h), "young", run_id)

    young_prompt = _build_young_prompt(transcript, has_portrait=True)
    print(f"[wallpaper.insert.service] young_prompt_len={len(young_prompt)}")

    try:
        gemini_pass1 = await asyncio.to_thread(
            _call_gemini_inpaint,
            young_prompt,
            base_image,
            young_mask,
            young_img,
        )
    except Exception as exc:
        msg = f"Pass 1 (young) Gemini call failed: {exc}"
        print(f"[wallpaper.insert.service] {msg}")
        return {"imageUrl": "", "raw": {"provider": "fallback", "error": msg}}

    # Composite: enforce the mask — environment outside stays as base_image.
    intermediate = _composite_region(base_image, gemini_pass1, young_mask)
    print(f"[wallpaper.insert.service] Pass 1 complete, intermediate size = {intermediate.size}")

    # ── Pass 2: elder character (lower-left) ────────────────────────────────
    elder_mask = _polygon_mask((w, h), ELDER_POLYGON, feather)
    print(f"[wallpaper.insert.service] elder feather={feather} mask_size={elder_mask.size}")

    _save_debug_mask(elder_mask, (w, h), "elder", run_id)

    print(f"[wallpaper.insert.service] elder_prompt_len={len(ELDER_PROMPT)}")

    try:
        gemini_pass2 = await asyncio.to_thread(
            _call_gemini_inpaint,
            ELDER_PROMPT,
            intermediate,
            elder_mask,
            elder_img,
        )
    except Exception as exc:
        msg = f"Pass 2 (elder) Gemini call failed: {exc}"
        print(f"[wallpaper.insert.service] {msg}")
        return {"imageUrl": "", "raw": {"provider": "fallback", "error": msg}}

    # Final composite: enforce the elder mask against the intermediate.
    final = _composite_region(intermediate, gemini_pass2, elder_mask)
    print(f"[wallpaper.insert.service] Pass 2 complete, final size = {final.size}")

    # ── Save ────────────────────────────────────────────────────────────────
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"wallpaper-final-{run_id}.png"
    out_path = GENERATED_DIR / filename
    final.save(out_path, format="PNG")
    print(f"[wallpaper.insert.service] saved to path = {out_path}")
    print(f"[wallpaper.insert.service] saved exists = {out_path.exists()}")

    rel_path = f"/generated/{filename}"
    image_url = _public_url(rel_path)
    print(f"[wallpaper.insert.service] returning imageUrl = {image_url}")
    return {
        "imageUrl": image_url,
        "raw": {
            "provider": settings.gemini_image_model,
            "run_id": run_id,
            "base_path": str(base_path),
            "size": [w, h],
            "feather": feather,
            "is_working": any(
                kw.lower() in (transcript or "").lower()
                for kw in WORKING_KEYWORDS
            ),
        },
    }
