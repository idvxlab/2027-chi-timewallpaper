from __future__ import annotations

import base64
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import HTTPException

from app.core.config import settings
from app.services.layered_tools.generate_dual_character_wallpaper import (
    generate_dual_character_wallpaper,
)
from app.services.seedream_wallpaper_service import (
    generate_initial_shared_wallpaper,
    reflow_shared_relationship_view as render_reflow_shared_relationship_view,
    update_shared_wallpaper,
)


class LayeredPainterTools:
    """Thin wrapper around staged shared-wallpaper rendering services."""

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

    async def initialize_wallpaper_view(
        self,
        *,
        base_image_url: str,
        younger_image_bytes: bytes,
        elder_image_bytes: bytes,
        designer_five_layer_plan: dict,
        semantic_visual_instruction: str,
        speaker_role: str,
    ) -> dict:
        """Initialize both people in the relationship's shared fixed-layout view."""
        return await generate_initial_shared_wallpaper(
            base_image_url=base_image_url,
            child_identity_bytes=younger_image_bytes,
            elder_identity_bytes=elder_image_bytes,
            five_layer_plan=designer_five_layer_plan,
            semantic_visual_instruction=semantic_visual_instruction,
            speaker_role=speaker_role,
        )

    async def update_current_side(
        self,
        *,
        base_image_url: str,
        speaker_image_bytes: bytes = b"",
        designer_five_layer_plan: dict,
        semantic_visual_instruction: str,
        speaker_role: str,
    ) -> dict:
        """Update the speaker's fixed region in the shared wallpaper."""
        return await update_shared_wallpaper(
            base_image_url=base_image_url,
            speaker_identity_bytes=speaker_image_bytes,
            five_layer_plan=designer_five_layer_plan,
            semantic_visual_instruction=semantic_visual_instruction,
            speaker_role=speaker_role,
        )

    async def reflow_shared_relationship_view(
        self,
        *,
        base_image_url: str,
        younger_image_bytes: bytes,
        elder_image_bytes: bytes,
        designer_five_layer_plan: dict,
        semantic_visual_instruction: str,
        speaker_role: str,
    ) -> dict:
        """Move both identities into one deterministic shared-space layout."""
        return await render_reflow_shared_relationship_view(
            base_image_url=base_image_url,
            child_identity_bytes=younger_image_bytes,
            elder_identity_bytes=elder_image_bytes,
            five_layer_plan=designer_five_layer_plan,
            semantic_visual_instruction=semantic_visual_instruction,
            speaker_role=speaker_role,
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
