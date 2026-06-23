"""进程内的当前场景状态。生产可替换为 Redis / DB。"""
from __future__ import annotations

import asyncio
from typing import Optional

from app.schemas.scene import Scene
from app.mock.presets import DEFAULT_PRESET


class SceneStore:
    def __init__(self) -> None:
        self._scene: Scene = DEFAULT_PRESET
        self._lock = asyncio.Lock()
        self._subscribers: list[asyncio.Queue] = []

    async def get(self) -> Scene:
        async with self._lock:
            return self._scene.model_copy(deep=True)

    async def set(self, scene: Scene) -> Scene:
        async with self._lock:
            self._scene = scene.model_copy(deep=True)
        await self._broadcast(scene)
        return self._scene

    async def update(self, **patch) -> Scene:
        async with self._lock:
            data = self._scene.model_dump()
            data.update(patch)
            self._scene = Scene.model_validate(data)
        await self._broadcast(self._scene)
        return self._scene

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=32)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        if q in self._subscribers:
            self._subscribers.remove(q)

    async def _broadcast(self, scene: Scene) -> None:
        for q in list(self._subscribers):
            try:
                q.put_nowait(scene.model_copy(deep=True))
            except asyncio.QueueFull:
                # 慢消费者,丢弃
                pass


store = SceneStore()


def get_store() -> SceneStore:
    return store


__all__ = ["SceneStore", "store", "get_store"]
