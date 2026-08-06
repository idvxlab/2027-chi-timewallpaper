from __future__ import annotations

from datetime import datetime
from typing import Literal

from app.schemas.onboarding import _CamelModel


class CurrentWallpaperOut(_CamelModel):
    relationship_id: str
    viewer_role: Literal["elder", "child"]
    stage: str
    status: str
    version: int = 0
    base_scene_url: str = ""
    wallpaper_url: str = ""
    latest_run_id: str = ""
    error: str = ""


class WallpaperRevisionOut(_CamelModel):
    revision_id: str
    event_seq: int
    image_url: str
    created_at: datetime


class WallpaperRevisionListOut(_CamelModel):
    relationship_id: str
    viewer_role: Literal["elder", "child"]
    items: list[WallpaperRevisionOut]
