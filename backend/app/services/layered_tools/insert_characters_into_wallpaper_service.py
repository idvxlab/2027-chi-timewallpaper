"""
Two-pass masked character insertion into a pre-generated landscape wallpaper.

Flow
====
Step 1 (Pass 1): insert the other-side character (upper-right mask)
    base landscape + upper-right mask + other-side cartoon asset → OpenAI Images Edit → composite → intermediate

Step 2 (Pass 2): insert the current speaker character (lower-left mask)
    intermediate + lower-left mask + current speaker cartoon asset → OpenAI Images Edit → composite → final

The composite is the critical safety layer: for each pass, the region outside
the mask is forced back to the original so the environment is never
rewritten by the image model — only the masked character zones can change.

API
===
POST /generate-wallpaper/insert-characters
    baseImageUrl:    URL of the landscape wallpaper produced by Step 1
    younger_image:    UploadFile  — reusable cartoon asset for the younger/child character
    elder_image:     UploadFile  — reusable cartoon asset for the elder/parent character
    transcript:      str         — drives the current speaker's lower-left activity

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
from app.services.openai_image_service import call_openai_image_edit_to_pil


# ============================================================================
# Layout
# ============================================================================

GENERATED_DIR = Path(settings.storage_local_dir) / "generated"
DEBUG_DIR     = Path(settings.storage_local_dir) / "debug_generation_inputs"

# Upper-right (other-side character) mask — x: 48%–100%, y: 0%–58%
YOUNG_POLYGON = (
    (0.48, 0.00),
    (1.00, 0.00),
    (1.00, 0.58),
    (0.58, 0.58),
    (0.48, 0.45),
)

# Lower-left (current speaker character) mask — x: 0%–58%, y: 42%–100%
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
    is_upper_right = tag.startswith("young") or tag.startswith("upper-right")
    pts = [(int(x * size[0]), int(y * size[1]))
           for x, y in (YOUNG_POLYGON if is_upper_right else ELDER_POLYGON)]
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

INSERTION_STYLE_BLOCK = (
    "画风必须延续参考图1的经典儿童绘本线描水彩：使用清楚、圆润、略带手绘抖动的深棕色墨线，"
    "配合大块透明水彩和轻薄水粉色块；保留纸张纹理与少量自然晕染。人物和关键物件轮廓清楚，"
    "背景花草用宽松色块概括。不要碎线、毛躁线、密集短笔触、照片写实、3D、日漫或塑料皮肤。\n"
)


def _character_asset_block(character_label: str, has_character_asset: bool) -> str:
    if has_character_asset:
        return (
            f"参考图2是已经定稿并可重复使用的{character_label}卡通人物资产，不是待重新设计的照片。\n"
            "直接继承参考图2的角色身份和造型语言：年龄感、脸型、五官比例、发型发色、体态、"
            "深棕轮廓线、圆润造型和服装主色。允许根据动作调整姿态与衣褶，但不要换脸、改变年龄、"
            "重做发型或另创角色。不要把参考图2的奶油色背景带入壁纸，也不要把人物照片化。\n"
        )
    return (
        f"生成一个与参考图1画风一致的{character_label}绘本角色。角色应有清楚、稳定的身份特征，"
        "避免通用人脸。\n"
    )


def _build_upper_right_other_prompt(character_label: str, has_portrait: bool) -> str:
    character_asset_block = _character_asset_block(character_label, has_portrait)
    return (
        "只编辑遮罩内的右上区域。参考图1是当前完整壁纸底图，遮罩外必须原样保留。\n"
        + INSERTION_STYLE_BLOCK
        + character_asset_block
        + "把另一方角色自然放入右上生活空间。人物在自己的环境中安静进行日常活动，"
        "例如阅读、照料植物、喝茶或休息；不要复制当前说话者本次事件。人物保持三分之四侧身，"
        "视线看向手中物件或场景内部，不直视镜头。人物大小服从右上空间，不占满区域，并保留可读的"
        "窗、植物或少量生活环境。透视、光线、色彩和纸面质感必须与底图连续。\n"
        "禁止改变遮罩外的天空、路径、建筑、植物和整体构图；禁止增加其他人物、分屏、边框、"
        "文字、logo、水印或UI。"
    )


def _build_lower_left_current_prompt(transcript: str, character_label: str, has_portrait: bool) -> str:
    character_asset_block = _character_asset_block(character_label, has_portrait)

    # ── Assembly ───────────────────────────────────────────────────────────
    return (
        "只编辑遮罩内的左下区域。参考图1是当前完整壁纸底图，遮罩外必须原样保留。\n"
        + INSERTION_STYLE_BLOCK
        + character_asset_block
        + f'只在左下当前说话者一侧表现本次内容：\n"{transcript}"\n'
        + "当前说话者正在进行上述内容所表达的具体日常活动。只加入上述内容能够支持的事件物件，"
        "不要用固定活动模板替换本次内容。\n"
        "人物保持三分之四侧身，视线自然看向正在进行的活动或相关物件，不直视镜头。"
        "人物、事件物件和局部生活环境应共同可读，透视、光线、色彩、轮廓线和纸面质感与底图连续。\n"
        "禁止把本次事件放到右上另一方；禁止改变遮罩外的路径、天空、建筑、植物和整体构图；"
        "禁止增加其他人物、分屏、边框、文字、logo、水印或UI。"
    )


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
    current_role: str = "parent",
) -> dict:
    """
    Two-pass masked character insertion.

    Pass 1: insert the other-side character into the upper-right mask.
    Pass 2: insert the current speaker character into the lower-left mask.

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
    print(f"[wallpaper.insert.service] current_role  = {current_role!r}")

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

    # ── Load reusable cartoon character assets ─────────────────────────────
    try:
        young_img = _load_avatar(younger_image_bytes)
        elder_img = _load_avatar(elder_image_bytes)
    except Exception as exc:
        msg = f"Failed to load avatar images: {exc}"
        print(f"[wallpaper.insert.service] {msg}")
        return {"imageUrl": "", "raw": {"provider": "fallback", "error": msg}}

    normalized_role = (current_role or "parent").lower()
    if normalized_role in {"child", "daughter", "son", "young", "younger"}:
        current_img = young_img
        current_label = "younger child/current speaker"
        other_img = elder_img
        other_label = "elder parent/other side"
    else:
        current_img = elder_img
        current_label = "elder parent/current speaker"
        other_img = young_img
        other_label = "younger child/other side"

    # ── Pass 1: other-side character (upper-right) ──────────────────────────
    feather = max(8, min(w, h) // 60)
    young_mask = _polygon_mask((w, h), YOUNG_POLYGON, feather)
    print(f"[wallpaper.insert.service] upper-right feather={feather} mask_size={young_mask.size} other={other_label}")

    _save_debug_mask(young_mask, (w, h), "upper-right-other", run_id)

    other_prompt = _build_upper_right_other_prompt(other_label, has_portrait=True)
    print(f"[wallpaper.insert.service] upper_right_prompt_len={len(other_prompt)}")

    try:
        model_pass1 = await call_openai_image_edit_to_pil(
            prompt=other_prompt,
            base_image=base_image,
            mask=young_mask,
            identity_image=other_img,
        )
    except Exception as exc:
        msg = f"Pass 1 (upper-right other side) image edit call failed: {exc}"
        print(f"[wallpaper.insert.service] {msg}")
        return {"imageUrl": "", "raw": {"provider": "fallback", "error": msg}}

    # Composite: enforce the mask — environment outside stays as base_image.
    intermediate = _composite_region(base_image, model_pass1, young_mask)
    print(f"[wallpaper.insert.service] Pass 1 complete, intermediate size = {intermediate.size}")

    # ── Pass 2: current speaker character (lower-left) ──────────────────────
    elder_mask = _polygon_mask((w, h), ELDER_POLYGON, feather)
    print(f"[wallpaper.insert.service] lower-left feather={feather} mask_size={elder_mask.size} current={current_label}")

    _save_debug_mask(elder_mask, (w, h), "lower-left-current", run_id)

    current_prompt = _build_lower_left_current_prompt(transcript, current_label, has_portrait=True)
    print(f"[wallpaper.insert.service] lower_left_prompt_len={len(current_prompt)}")

    try:
        model_pass2 = await call_openai_image_edit_to_pil(
            prompt=current_prompt,
            base_image=intermediate,
            mask=elder_mask,
            identity_image=current_img,
        )
    except Exception as exc:
        msg = f"Pass 2 (lower-left current speaker) image edit call failed: {exc}"
        print(f"[wallpaper.insert.service] {msg}")
        return {"imageUrl": "", "raw": {"provider": "fallback", "error": msg}}

    # Final composite: enforce the elder mask against the intermediate.
    final = _composite_region(intermediate, model_pass2, elder_mask)
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
            "provider": settings.image_chat_model,
            "run_id": run_id,
            "base_path": str(base_path),
            "size": [w, h],
            "feather": feather,
            "current_role": normalized_role,
            "lower_left_role": current_label,
            "upper_right_role": other_label,
        },
    }
