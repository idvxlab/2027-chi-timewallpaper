from __future__ import annotations

import base64
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import HTTPException

from app.core.config import settings
from app.services.layered_tools.edit_elder_region_wallpaper_service import (
    edit_elder_region_with_transcript,
)
from app.services.layered_tools.generate_dual_character_wallpaper import (
    generate_dual_character_wallpaper,
)
from app.services.layered_tools.insert_characters_into_wallpaper_service import (
    insert_characters_into_wallpaper,
)


class LayeredPainterTools:
    """Thin tool wrapper around the copied staged wallpaper generation services."""

    async def generate_base_scene(
        self,
        *,
        self_image_bytes: bytes = b"",
        partner_image_bytes: bytes = b"",
    ) -> dict:
        return await generate_dual_character_wallpaper(
            self_image_bytes=self_image_bytes,
            partner_image_bytes=partner_image_bytes,
        )

    async def first_voice_compose(
        self,
        *,
        base_image_url: str,
        younger_image_bytes: bytes,
        elder_image_bytes: bytes,
        transcript: str,
        current_role: str = "parent",
    ) -> dict:
        return await insert_characters_into_wallpaper(
            base_image_url=base_image_url,
            younger_image_bytes=younger_image_bytes,
            elder_image_bytes=elder_image_bytes,
            transcript=transcript,
            current_role=current_role,
        )

    async def update_current_side(
        self,
        *,
        base_image_url: str,
        transcript: str,
        current_side: str = "left_bottom",
    ) -> dict:
        # The copied prototype currently implements the lower-left regional edit.
        # Keep the original tool unchanged and expose the role-neutral wrapper name here.
        if current_side not in {"left_bottom", "elder_lower_left", "current_side"}:
            return {
                "imageUrl": "",
                "raw": {
                    "error": f"layered current-side edit only supports left_bottom for now, got {current_side!r}",
                    "currentSide": current_side,
                },
            }
        return await edit_elder_region_with_transcript(
            base_image_url=base_image_url,
            transcript=transcript,
        )

    async def resolve_reference_bytes(self, image_url: str | None) -> bytes:
        if not image_url:
            raise HTTPException(status_code=400, detail="reference image url is required")
        clean = image_url.strip()
        if clean.startswith("data:image/"):
            return base64.b64decode(clean.split(",", 1)[1])
        local = self._local_static_path(clean)
        if local and local.exists():
            return local.read_bytes()
        if clean.startswith(("http://", "https://")):
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.get(clean)
                response.raise_for_status()
                return response.content
        raise HTTPException(status_code=400, detail=f"Unsupported reference image url: {image_url}")

    def _local_static_path(self, image_url: str) -> Path | None:
        parsed = urlparse(image_url)
        path = parsed.path or image_url
        if path.startswith("/references/"):
            return Path(settings.storage_local_dir) / "references" / path.removeprefix("/references/")
        if path.startswith("/generated/"):
            return Path(settings.storage_local_dir) / "generated" / path.removeprefix("/generated/")
        if path.startswith("/character-assets/files/"):
            relative = path.removeprefix("/character-assets/files/")
            parts = Path(relative).parts
            if len(parts) != 2 or any(part in {"", ".", ".."} for part in parts):
                return None
            return Path(settings.storage_local_dir) / "character-assets" / parts[0] / parts[1]
        return None


layered_painter_tools = LayeredPainterTools()
