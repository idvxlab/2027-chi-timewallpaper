from app.mock.presets import PRESETS
from app.schemas.scene import Scene


def apply_demo(demo_id: str) -> Scene:
    if demo_id not in PRESETS:
        raise KeyError(demo_id)
    return PRESETS[demo_id].model_copy(deep=True)
