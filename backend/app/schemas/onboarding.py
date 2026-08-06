from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


def _to_camel(value: str) -> str:
    parts = value.split("_")
    return parts[0] + "".join(part.title() for part in parts[1:])


class _CamelModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=_to_camel)


class OnboardingProfileIn(_CamelModel):
    viewer_role: Literal["elder", "child"]
    display_name: str = Field(min_length=1, max_length=128)
    gender: Literal["male", "female"]
    user_id: Optional[str] = Field(default=None, min_length=1, max_length=64)
    relationship_id: Optional[str] = Field(default=None, min_length=1, max_length=64)


class OnboardingProfileOut(_CamelModel):
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
