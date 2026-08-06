from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any


class CharacterAssetEventHub:
    """In-process relationship-scoped character asset notifications."""

    def __init__(self) -> None:
        self._subscribers: dict[str, list[asyncio.Queue[dict[str, Any]]]] = (
            defaultdict(list)
        )

    def subscribe(self, relationship_id: str) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=8)
        self._subscribers[relationship_id].append(queue)
        return queue

    def unsubscribe(
        self,
        relationship_id: str,
        queue: asyncio.Queue[dict[str, Any]],
    ) -> None:
        subscribers = self._subscribers.get(relationship_id)
        if not subscribers:
            return
        if queue in subscribers:
            subscribers.remove(queue)
        if not subscribers:
            self._subscribers.pop(relationship_id, None)

    async def publish(
        self,
        relationship_id: str,
        *,
        asset_id: str,
        role: str,
        status: str,
    ) -> None:
        event: dict[str, Any] = {
            "type": "character_asset_changed",
            "relationshipId": relationship_id,
            "assetId": asset_id,
            "role": role,
            "status": status,
        }
        for queue in list(self._subscribers.get(relationship_id, ())):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                queue.put_nowait(event)


character_asset_event_hub = CharacterAssetEventHub()
