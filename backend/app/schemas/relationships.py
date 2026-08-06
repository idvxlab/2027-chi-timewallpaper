from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.schemas.onboarding import OnboardingProfileOut, _CamelModel


class RelationshipInvitationOut(_CamelModel):
    invite_code: str
    relationship_id: str
    relationship_display_name: str
    creator_role: Literal["elder", "child"]
    required_role: Literal["elder", "child"]
    status: Literal["waiting"]


class JoinRelationshipIn(_CamelModel):
    invite_code: str = Field(min_length=4, max_length=4, pattern=r"^\d{4}$")
    viewer_role: Literal["elder", "child"]
    display_name: str = Field(min_length=1, max_length=128)
    gender: Literal["male", "female"]


class JoinRelationshipOut(OnboardingProfileOut):
    pass
