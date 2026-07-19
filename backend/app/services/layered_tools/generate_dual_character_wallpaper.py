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

OpenAI Images API:
   {PROVIDER_API_BASE_URL}/v1/images/generations  (model=IMAGE_CHAT_MODEL)
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
from app.services.openai_image_service import (
    build_images_generation_payload,
    collect_image_refs,
    resolve_image_endpoint,
    save_image_ref,
)


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
# Image response helpers
# ============================================================================

def _collect_image_refs(payload) -> list[str]:
    return collect_image_refs(payload)


# ============================================================================
# The landscape prompt
# ============================================================================

INITIAL_WALLPAPER_PROMPT = (
    "生成一张竖版 9:16 手机壁纸底图，只生成环境，不要人物。\n\n"
    "整体画风必须更接近经典儿童绘本线描水彩，而不是精细风景水彩："
    "先用干净、明确、略微抖动的深棕或黑色手绘墨线勾勒主要形体，"
    "再用大块透明水彩或轻薄水粉色块上色。线条要清楚、圆润、克制，"
    "不要碎线、毛躁线、密集短笔触或工笔式植物纹理。画面像纸本家庭绘本中的一页，"
    "有生活化叙事感，而不是照片、3D、动漫、精细场景概念图或商业海报。\n\n"
    "空间结构必须是一个连续世界，不是分屏、拼贴或两张小图。保留三段式空间：\n"
    "1) 右上区域约占画面一半，是较大的未来人物生活空间。它可以是窗边书桌、"
    "阳台、花园房间、厨房角落或明亮工作台，放置桌子、书、台灯、植物、窗帘、"
    "杯子等生活物件，但绝对不要出现人。\n"
    "2) 左下区域约占画面三成，是较小但清楚的未来人物生活空间。它可以是扶手椅、"
    "茶几、书架、落地灯、窗边小桌、花盆或安静客厅角落，同样不要出现人。\n"
    "3) 中间区域约占画面两成，用真实空间路径自然连接两侧，例如花园小径、石阶、"
    "走廊、桥、河岸、窗与窗之间的视线通道或庭院路径。路径要融入场景，不能是白色斜线、"
    "几何切割、发光丝带、气泡或纯粒子连接。\n\n"
    "虚实层级必须明确：\n"
    "- 右上和左下未来人物落位附近保留中等细节，让桌面、椅子、窗、灯、茶杯等少量关键物件可读。\n"
    "- 中间路径保持清楚可读，用简洁轮廓和大色块表达，不要铺满碎叶、碎花和细小纹理。\n"
    "- 远景、天空、边缘花丛和大面积背景应接近概括的水彩色块：湿画法扩散、颜色自然渗化、"
    "柔和边缘、保留纸面留白。背景可以平面化，不追求真实透视和复杂空间细节。\n"
    "- 细节密度要有节奏：少量关键物件有干净线稿，大部分背景用宽松色块概括，"
    "不要全画面均匀精细、满屏小花小叶。\n\n"
    "画面风格细节：暖粉、鼠尾草绿、麦黄色、奶油纸色、浅橘光；柔和漫射日光，"
    "没有硬阴影；背景以透明水彩罩染、湿画法晕染、干笔飞白和纸面留白营造氛围。"
    "家具、窗框、路径和少量关键植物使用干净手绘墨线；花草、树丛、远景用大块松散颜色。构图丰富但有呼吸感，"
    "上方和远景保留适合锁屏时间显示的干净、轻盈空间。\n\n"
    "严格禁止：人物、人脸、动物主角、文字、水印、logo、UI、边框、白边、明信片框、"
    "漫画分格、上下分屏、左右分屏、白色斜线、巨大峡谷、悬崖断裂、照片写实、3D渲染、"
    "日漫风、矢量扁平风、过度锐利数字厚涂、全画面高密度装饰、每片叶子都清晰的工笔式背景、"
    "稀碎毛躁的实线笔触、复杂写实透视、精细风景水彩。\n\n"
    "最终结果应是一张完整、连续、可后续插入角色的复古儿童绘本水彩场景底图。"
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
    api_key = settings.effective_image_api_key
    api_base = settings.effective_image_api_base_url.rstrip("/")

    print("[wallpaper.service] start generate_landscape_wallpaper")
    print(f"[wallpaper.service] has_provider_image_key = {bool(api_key)}")
    print(f"[wallpaper.service] image_model = {settings.image_chat_model}")

    if not api_key or not api_base:
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

    # ── OpenAI Images path ────────────────────────────────────────────────────
    print("[wallpaper.service] provider = openai-images")

    endpoint = resolve_image_endpoint(api_base, settings.image_chat_endpoint, "/v1/images/generations")
    payload = build_images_generation_payload(INITIAL_WALLPAPER_PROMPT, [], api_base, endpoint)

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
        image_url = _public_url(await save_image_ref(refs[0], client))

    return {"imageUrl": image_url, "raw": {"type": "landscape_only", **data}}
