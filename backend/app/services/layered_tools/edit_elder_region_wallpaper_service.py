"""
Single-pass regional inpaint of the lower-left elder character zone.

Flow
====
User records a voice message on the final WallpaperStage → frontend calls
POST /asr/transcribe → receives transcript → calls this service with the
current wallpaper URL and the transcript.

This service:
1. Resolves the base wallpaper image (same path logic as insert_characters).
2. Creates a mask covering the lower-left elder region (with feather).
3. Calls OpenAI Images Edit with base image + mask + elder-region edit prompt driven by transcript.
4. Composites the edited region back onto the original base image.
5. Saves the result to generated/.

The mask guarantees the upper-right younger character area, sky, valley,
central path, and all other regions stay exactly as they were.
"""

from __future__ import annotations

import base64
import io
import uuid
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image, ImageDraw, ImageFilter

import httpx
from fastapi import HTTPException

from app.core.config import settings
from app.services.openai_image_service import call_openai_image_edit_to_pil


# ============================================================================
# Layout
# ============================================================================

GENERATED_DIR = Path(settings.storage_local_dir) / "generated"
DEBUG_DIR     = Path(settings.storage_local_dir) / "debug_generation_inputs"

# Lower-left (elder edit zone) — slightly wider than the original elder mask
# so edits have room to breathe.
ELDER_EDIT_POLYGON = (
    (0.00, 0.40),
    (0.58, 0.40),
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
# Base image resolution (mirrors insert_characters_into_wallpaper_service.py)
# ============================================================================

def _resolve_base_image(base_image_url: str) -> Path:
    raw = (base_image_url or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="baseImageUrl is required")
    print(f"[wallpaper.elder-edit.service] baseImageUrl = {raw!r}")

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

    print(f"[wallpaper.elder-edit.service] resolved base image = {target}")
    print(f"[wallpaper.elder-edit.service] base image exists = {target.exists()}")
    return target


# ============================================================================
# Image I/O
# ============================================================================

def _image_to_data_uri(img: Image.Image, fmt: str = "PNG") -> str:
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    mime = "image/png" if fmt.upper() != "JPEG" else "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(buf.getvalue()).decode()}"


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


def _save_debug_mask(mask: Image.Image, tag: str, run_id: str) -> Path:
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    path = DEBUG_DIR / f"debug-mask-{tag}-{run_id}.png"
    mask.save(path)
    print(f"[wallpaper.elder-edit.service] mask saved = {path}")
    return path


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
    This guarantees the non-elder regions (sky, valley, upper-right
    character area, central path, etc.) are never changed.
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
# Edit prompt builder
# ============================================================================

def _build_edit_prompt(transcript: str) -> str:
    """
    Build the regional inpaint prompt for the lower-left elder zone.

    The prompt interprets the transcript and modifies the elder region
    accordingly, while being very explicit that nothing outside the mask
    may change.
    """
    return (
        "Edit only the masked lower-left elder region.\n\n"
        "The base image is the current wallpaper. Preserve all unmasked areas exactly.\n"
        "Do not change the sky, mountains, river, buildings, upper-right "
        "younger character area, central path, lighting, or overall composition "
        "outside the mask.\n\n"
        f'Modify the elder region according to this message:\n"{transcript}"\n\n'
        "Create a gentle storybook-style visual response in the lower-left region.\n"
        "The elder character should remain present in this region and the scene "
        "should reflect the message naturally.\n\n"
        "If the message mentions watering flowers, planting flowers, a sunflower, "
        "gardening, or plants:\n"
        "show the elder character gently watering flowers or caring for a small "
        "plant area, with one small sunflower visible nearby.\n"
        "Keep the action calm, warm, and domestic.\n\n"
        "Keep the same hand-painted storybook illustration style, warm lighting, "
        "soft edges, color palette, and painterly texture as the original wallpaper.\n"
        "The edited region must blend seamlessly into the existing image.\n"
        "Do not create a pasted photo effect.\n"
        "Do not add extra people.\n"
        "Do not add text, logo, watermark, or UI."
    )


# ============================================================================
# Public entry point
# ============================================================================

async def edit_elder_region_with_transcript(
    *,
    base_image_url: str,
    transcript: str,
) -> dict:
    """
    Single-pass regional inpaint of the lower-left elder zone.

    Args:
        base_image_url: URL or path of the current wallpaper (generated or fallback).
        transcript:     ASR transcript (or fallback) driving the visual edit.

    Returns:
        {"imageUrl": "/generated/wallpaper-elder-edit-<uuid>.png"}
    On any failure returns {"imageUrl": ""} so the caller keeps the current wallpaper.
    """
    print("[wallpaper.elder-edit.service] edit_elder_region_with_transcript start")
    print(f"[wallpaper.elder-edit.service] baseImageUrl = {base_image_url!r}")
    print(f"[wallpaper.elder-edit.service] transcript   = {transcript!r}")

    # ── Resolve base ──────────────────────────────────────────────────────
    try:
        base_path = _resolve_base_image(base_image_url)
    except HTTPException:
        raise
    except Exception as exc:
        print(f"[wallpaper.elder-edit.service] failed to resolve base image: {exc}")
        return {"imageUrl": ""}

    if not base_path.exists():
        print(f"[wallpaper.elder-edit.service] base image not found: {base_path}")
        return {"imageUrl": ""}

    try:
        base_image = Image.open(base_path).convert("RGB")
    except Exception as exc:
        print(f"[wallpaper.elder-edit.service] failed to open base image: {exc}")
        return {"imageUrl": ""}

    w, h = base_image.size
    print(f"[wallpaper.elder-edit.service] base image size = {w}x{h}")

    run_id = uuid.uuid4().hex[:8]

    # ── Build mask ─────────────────────────────────────────────────────────
    feather = max(10, min(w, h) // 55)
    elder_mask = _polygon_mask((w, h), ELDER_EDIT_POLYGON, feather)
    print(f"[wallpaper.elder-edit.service] feather={feather} mask_size={elder_mask.size}")

    _save_debug_mask(elder_mask, "elder-edit", run_id)

    # ── Build prompt ───────────────────────────────────────────────────────
    prompt = _build_edit_prompt(transcript)
    print(f"[wallpaper.elder-edit.service] prompt len = {len(prompt)} chars")

    # ── OpenAI regional image edit ──────────────────────────────────────────
    try:
        edit_result = await call_openai_image_edit_to_pil(
            prompt=prompt,
            base_image=base_image,
            mask=elder_mask,
        )
    except Exception as exc:
        print(f"[wallpaper.elder-edit.service] image edit call failed: {exc}")
        return {"imageUrl": ""}

    # ── Composite: force non-mask pixels back to original ───────────────────
    print("[wallpaper.elder-edit.service] compositing edited region back to original base")
    final = _composite_region(base_image, edit_result, elder_mask)

    # ── Save ───────────────────────────────────────────────────────────────
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"wallpaper-elder-edit-{run_id}.png"
    out_path = GENERATED_DIR / filename
    final.save(out_path, format="PNG")
    print(f"[wallpaper.elder-edit.service] saved final image = {out_path}")
    print(f"[wallpaper.elder-edit.service] saved exists = {out_path.exists()}")

    rel_path = f"/generated/{filename}"
    image_url = _public_url(rel_path)
    print(f"[wallpaper.elder-edit.service] returning imageUrl = {image_url}")
    return {
        "imageUrl": image_url,
        "raw": {
            "provider": settings.image_chat_model,
            "run_id": run_id,
            "base_path": str(base_path),
            "size": [w, h],
            "feather": feather,
            "transcript": transcript,
        },
    }
