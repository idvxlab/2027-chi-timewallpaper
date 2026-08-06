from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any


class RelationshipEventHub:
    """In-process notifications for relationship pairing changes."""

    def __init__(self) -> None:
        self._subscribers: dict[str, list[asyncio.Queue[dict[str, Any]]]] = (
            defaultdict(list)
        )

    def subscribe(self, relationship_id: str) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=4)
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

    async def publish_connected(self, relationship_id: str) -> None:
        event = {
            "type": "relationship_connected",
            "relationshipId": relationship_id,
            "status": "connected",
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


relationship_event_hub = RelationshipEventHub()
