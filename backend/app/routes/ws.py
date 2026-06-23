import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.scene_store import get_store

router = APIRouter()


@router.websocket("/ws/scene")
async def ws_scene(ws: WebSocket) -> None:
    await ws.accept()
    store = get_store()
    queue = store.subscribe()

    async def pump() -> None:
        try:
            while True:
                scene = await queue.get()
                await ws.send_text(scene.model_dump_json())
        except Exception:
            return

    pump_task = asyncio.create_task(pump())

    try:
        # 推送当前场景
        current = await store.get()
        await ws.send_text(json.dumps({"scene": current.model_dump()}, ensure_ascii=False))
        while True:
            # 等待客户端消息(心跳),保持连接
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        pump_task.cancel()
        store.unsubscribe(queue)
