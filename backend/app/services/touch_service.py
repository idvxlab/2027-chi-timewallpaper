from app.core.scene_store import SceneStore
from app.db.session import SessionLocal
from app.db.models import TouchLog
from app.mock.messages import CUE_BY_HOTSPOT, MESSAGE_BY_HOTSPOT
from app.schemas.api import TouchIn, TouchOut


def _persist_touch(body: TouchIn) -> None:
    """同步写 SQLite 日志。失败不影响主流程。"""
    try:
        with SessionLocal() as s:
            s.add(
                TouchLog(
                    hotspot_id=body.hotspot_id,
                    demo_id=body.demo_id,
                    payload=body.payload or {},
                )
            )
            s.commit()
    except Exception:
        # 进程内 store 仍是真实状态,日志失败不回滚用户感知
        pass


async def handle_touch(body: TouchIn, store: SceneStore) -> TouchOut:
    scene = await store.get()

    msg = MESSAGE_BY_HOTSPOT.get(body.hotspot_id, "已收到你的触碰～")
    cue = CUE_BY_HOTSPOT.get(body.hotspot_id)

    _persist_touch(body)

    return TouchOut(
        ok=True,
        scene=scene.model_dump(by_alias=True),
        event={
            "toast": msg,
            "cue": cue,
        },
    )
