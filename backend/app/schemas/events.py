from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict


def _to_camel(s: str) -> str:
    parts = s.split("_")
    return parts[0] + "".join(p.title() for p in parts[1:])


class _CamelModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=_to_camel)


class SceneEvent(_CamelModel):
    toast: Optional[str] = None
    cue: Optional[str] = None
    ambient_start: Optional[str] = None
    ambient_stop: Optional[str] = None
    meta: Optional[dict] = None
