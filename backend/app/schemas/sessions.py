from __future__ import annotations

from typing import Literal, Optional

from pydantic import Field

from app.schemas.onboarding import _CamelModel


RestorableStep = Literal["pairing", "photo", "prelude", "wallpaper"]
ProgressStep = Literal["photo", "prelude", "wallpaper"]


class SessionOut(_CamelModel):
    user_id: str
    counterpart_user_id: str
    relationship_id: str
    relationship_display_name: str
    invite_code: Optional[str]
    relationship_status: Literal["waiting", "connected"]
    viewer_role: Literal["elder", "child"]
    counterpart_role: Literal["elder", "child"]
    family_role: Literal["mother", "father", "daughter", "son"]
    display_name: str
    gender: Literal["male", "female"]
    onboarding_step: RestorableStep
    wallpaper_url: str


class SessionProgressIn(_CamelModel):
    onboarding_step: ProgressStep
    wallpaper_url: Optional[str] = Field(default=None, max_length=512)
