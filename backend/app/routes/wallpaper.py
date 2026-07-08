"""
POST /generate-wallpaper — pure-landscape initial wallpaper (Step 1).
POST /generate-wallpaper/insert-characters — two-pass masked character insertion (Step 3).

Step 1 (POST ""): no longer uses uploaded avatars. Calls
  generate_dual_character_wallpaper(...) which generates a pure landscape
  wallpaper with no characters. The two avatar files are accepted only
  for backward compatibility with the existing API shape; they are not
  forwarded to the model.

Step 3 (POST "/insert-characters"): after the user records a voice memo
  and ASR returns a transcript, this route inserts both character portraits
  into the pre-generated landscape using two masked inpaint passes.
  The non-masked landscape areas are strictly preserved.
"""

from fastapi import APIRouter, Body, File, HTTPException, UploadFile

from app.services.generate_dual_character_wallpaper import generate_dual_character_wallpaper
from app.services.insert_characters_into_wallpaper_service import (
    insert_characters_into_wallpaper,
)
from app.services.edit_elder_region_wallpaper_service import (
    edit_elder_region_with_transcript,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# Step 1: landscape wallpaper
# ---------------------------------------------------------------------------

@router.post("")
async def generate_wallpaper(
    self_image: UploadFile = File(..., description="(ignored) Avatar for younger character"),
    partner_image: UploadFile = File(..., description="(ignored) Avatar for elder character"),
) -> dict:
    """
    Generate the initial pure-landscape wallpaper.

    The two uploaded avatar files are accepted only for backward compatibility
    with the existing frontend API shape.  They are not forwarded to the
    model — the generated image is a character-free landscape designed to
    serve as the base for later masked character insertion.

    Returns ``{"imageUrl": "/generated/wallpaper-<uuid>.png"}`` on success.
    Falls back to an empty imageUrl when no API key is configured.
    """
    print("[wallpaper.route] POST /generate-wallpaper received")
    self_bytes = await self_image.read()
    partner_bytes = await partner_image.read()

    print(
        f"[wallpaper.route] self filename={self_image.filename!r} "
        f"content_type={self_image.content_type!r} bytes={len(self_bytes)}"
    )
    print(
        f"[wallpaper.route] partner filename={partner_image.filename!r} "
        f"content_type={partner_image.content_type!r} bytes={len(partner_bytes)}"
    )
    print("[wallpaper.route] calling service...")

    try:
        result = await generate_dual_character_wallpaper(
            self_image_bytes=self_bytes,
            partner_image_bytes=partner_bytes,
        )
    except Exception as exc:
        print(f"[wallpaper.route] error = {exc}")
        raise

    print(f"[wallpaper.route] result imageUrl = {result.get('imageUrl', '')}")
    return {"imageUrl": result.get("imageUrl", "")}


# ---------------------------------------------------------------------------
# Step 3: two-pass masked character insertion
# ---------------------------------------------------------------------------

@router.post("/insert-characters")
async def insert_characters(
    baseImageUrl: str = File(..., description="URL of the landscape wallpaper from Step 1"),
    transcript: str = File(..., description="ASR transcript driving the younger character's activity"),
    younger_image: UploadFile = File(
        ..., description="Portrait photo of the younger character"
    ),
    elder_image: UploadFile = File(
        ..., description="Portrait photo of the elder character"
    ),
) -> dict:
    """
    Two-pass masked character insertion.

    Pass 1: insert the younger character into the upper-right masked region.
    Pass 2: insert the elder character into the lower-left masked region.

    Both passes composite the Gemini output back onto the original base
    so the non-masked landscape areas are strictly preserved.

    Always returns 200 with an empty imageUrl on any failure so the
    frontend can keep the landscape-only wallpaper without crashing.
    """
    print("[wallpaper.insert.route] POST /generate-wallpaper/insert-characters received")
    print(f"[wallpaper.insert.route] baseImageUrl = {baseImageUrl!r}")
    print(f"[wallpaper.insert.route] transcript   = {transcript!r}")
    younger_bytes = await younger_image.read()
    elder_bytes = await elder_image.read()
    print(
        f"[wallpaper.insert.route] younger_image filename={younger_image.filename!r} "
        f"bytes={len(younger_bytes)}"
    )
    print(
        f"[wallpaper.insert.route] elder_image filename={elder_image.filename!r} "
        f"bytes={len(elder_bytes)}"
    )

    try:
        result = await insert_characters_into_wallpaper(
            base_image_url=baseImageUrl,
            younger_image_bytes=younger_bytes,
            elder_image_bytes=elder_bytes,
            transcript=transcript,
        )
    except HTTPException:
        raise
    except Exception as exc:
        print(f"[wallpaper.insert.route] unexpected error: {exc!r}")
        return {"imageUrl": "", "raw": {"error": str(exc)}}

    image_url = result.get("imageUrl", "")
    print(f"[wallpaper.insert.route] result imageUrl = {image_url!r}")
    return {"imageUrl": image_url, "raw": result.get("raw", {})}


# ---------------------------------------------------------------------------
# Final WallpaperStage: edit lower-left elder region with transcript
# ---------------------------------------------------------------------------

@router.post("/edit-elder-region")
async def edit_elder_region(body: dict) -> dict:
    """
    Single-pass regional inpaint of the lower-left elder zone.

    The frontend records audio → /asr/transcribe → receives transcript → calls
    this endpoint with the current wallpaper URL and the transcript.

    The edit interprets the transcript and visually updates the elder region
    (e.g. elder watering flowers / planting a sunflower).  All other areas
    are strictly preserved by a composite safety layer.

    Request JSON:
        baseImageUrl:  str  — URL of the current wallpaper
        transcript:     str  — ASR result or fallback

    Returns:
        {"imageUrl": "/generated/wallpaper-elder-edit-<uuid>.png"}
    Always returns 200 with an empty imageUrl on failure so the frontend keeps
    the current wallpaper without crashing.
    """
    base_image_url: str = body.get("baseImageUrl", "")
    transcript: str = body.get("transcript", "")

    print("[wallpaper.elder-edit.route] request received")
    print("[wallpaper.elder-edit.route] baseImageUrl =", base_image_url)
    print("[wallpaper.elder-edit.route] transcript   =", transcript)

    try:
        result = await edit_elder_region_with_transcript(
            base_image_url=base_image_url,
            transcript=transcript,
        )
    except HTTPException:
        raise
    except Exception as exc:
        print(f"[wallpaper.elder-edit.route] unexpected error: {exc!r}")
        return {"imageUrl": "", "raw": {"error": str(exc)}}

    image_url = result.get("imageUrl", "")
    print(f"[wallpaper.elder-edit.route] result imageUrl = {image_url!r}")
    return {"imageUrl": image_url, "raw": result.get("raw", {})}
