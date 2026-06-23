from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


def _to_camel(s: str) -> str:
    parts = s.split("_")
    return parts[0] + "".join(p.title() for p in parts[1:])


class _CamelModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=_to_camel)


class Hotspot(_CamelModel):
    id: str
    label: str
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    r: float = Field(gt=0, le=0.5)


class Scene(_CamelModel):
    demo_id: str
    glow: bool = False
    glow_opacity: Optional[float] = None
    hotspots: list[Hotspot] = []
    ambient: Optional[str] = None


class SceneTouchResponse(_CamelModel):
    scene: Scene
    event: Optional["SceneEvent"] = None


class TouchIn(_CamelModel):  # 重新导出,保持向后兼容
    hotspot_id: str
    demo_id: Optional[str] = None
    payload: dict = Field(default_factory=dict)


# 解决前向引用
from app.schemas.events import SceneEvent  # noqa: E402

SceneTouchResponse.model_rebuild()

__all__ = ["Hotspot", "Scene", "SceneTouchResponse", "TouchIn"]
