from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


def _to_camel(s: str) -> str:
    parts = s.split("_")
    return parts[0] + "".join(p.title() for p in parts[1:])


class _CamelModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=_to_camel)


class TouchIn(_CamelModel):
    hotspot_id: str
    demo_id: Optional[str] = None
    payload: dict = Field(default_factory=dict)


class TouchOut(_CamelModel):
    ok: bool = True
    scene: dict
    event: Optional[dict] = None
