from __future__ import annotations

from typing import Optional

from app.schemas.scene import Hotspot, Scene


# 共享热点(可被多个 scene 复用)
HOTSPOT_LAMP = Hotspot(id="lamp", label="小夜灯", x=0.78, y=0.32, r=0.07)
HOTSPOT_CLOUD = Hotspot(id="cloud", label="云朵", x=0.28, y=0.22, r=0.08)
HOTSPOT_BEAR = Hotspot(id="bear", label="小熊", x=0.5, y=0.66, r=0.09)
HOTSPOT_STAR = Hotspot(id="star", label="星星", x=0.18, y=0.42, r=0.05)
HOTSPOT_BOOK = Hotspot(id="book", label="绘本", x=0.7, y=0.7, r=0.07)


def _scene(demo_id: str, glow: bool, hotspots: list[Hotspot], ambient: Optional[str] = None) -> Scene:
    return Scene(
        demo_id=demo_id,
        glow=glow,
        glow_opacity=0.6 if glow else None,
        hotspots=hotspots,
        ambient=ambient,
    )


# V1 四种状态预设
# calm        : 平静,默认进入态,无环境音、无光晕
# longing     : 思念(爸爸妈妈),点亮小夜灯 + 窗外余晖,放 ambient
# fatigue     : 疲倦,灯亮、星星可见,放 ambient
# await_response : 等待回应(绘本翻页后等家长回),灯亮,放 ambient
CALM_PRESET = _scene("calm", glow=False, hotspots=[HOTSPOT_LAMP, HOTSPOT_CLOUD, HOTSPOT_BEAR])
LONGING_PRESET = _scene(
    "longing",
    glow=True,
    hotspots=[HOTSPOT_LAMP, HOTSPOT_STAR, HOTSPOT_CLOUD],
    ambient="/audio/ambient.mp3",
)
FATIGUE_PRESET = _scene(
    "fatigue",
    glow=True,
    hotspots=[HOTSPOT_LAMP, HOTSPOT_STAR, HOTSPOT_BEAR],
    ambient="/audio/ambient.mp3",
)
AWAIT_RESPONSE_PRESET = _scene(
    "await_response",
    glow=True,
    hotspots=[HOTSPOT_BOOK, HOTSPOT_LAMP, HOTSPOT_BEAR],
    ambient="/audio/ambient.mp3",
)


# 入口预设
DEFAULT_PRESET: Scene = CALM_PRESET

PRESETS: dict[str, Scene] = {
    "calm": CALM_PRESET,
    "longing": LONGING_PRESET,
    "fatigue": FATIGUE_PRESET,
    "await_response": AWAIT_RESPONSE_PRESET,
}
