from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from typing import Any

from redis.asyncio import Redis

from app.core.config import settings

logger = logging.getLogger(__name__)


class WallpaperEventHub:
    """Relationship events bridged through Redis for API and worker processes."""

    def __init__(self) -> None:
        self._subscribers: dict[str, list[asyncio.Queue[dict[str, Any]]]] = defaultdict(list)
        self._redis: Redis | None = None
        self._listener_task: asyncio.Task[None] | None = None
        self._stopping = False

    async def start(self) -> None:
        if not settings.render_queue_enabled or not settings.redis_url or self._listener_task:
            return
        pubsub = None
        try:
            redis = Redis.from_url(settings.redis_url, decode_responses=True)
            await redis.ping()
            self._redis = redis
            self._stopping = False
            pubsub = redis.pubsub()
            await pubsub.psubscribe(f"{settings.wallpaper_event_channel_prefix}:*")
            self._listener_task = asyncio.create_task(self._listen(pubsub))
        except Exception as exc:
            logger.warning("Wallpaper Redis event bridge unavailable: %s", exc)
            if self._listener_task is not None:
                self._listener_task.cancel()
                await asyncio.gather(self._listener_task, return_exceptions=True)
                self._listener_task = None
            if self._redis is not None:
                await self._redis.aclose()
                self._redis = None
            if pubsub is not None:
                await pubsub.aclose()

    async def stop(self) -> None:
        self._stopping = True
        if self._listener_task is not None:
            self._listener_task.cancel()
            await asyncio.gather(self._listener_task, return_exceptions=True)
            self._listener_task = None
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None

    def subscribe(self, relationship_id: str) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=8)
        self._subscribers[relationship_id].append(queue)
        return queue

    def unsubscribe(self, relationship_id: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
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
        status: str,
        version: int | None = None,
        image_url: str | None = None,
        error: str | None = None,
    ) -> None:
        event: dict[str, Any] = {
            "type": "wallpaper_state_changed",
            "relationshipId": relationship_id,
            "status": status,
        }
        if version is not None:
            event["version"] = version
        if image_url is not None:
            event["imageUrl"] = image_url
        if error is not None:
            event["error"] = error[:1000]

        if settings.render_queue_enabled and settings.redis_url:
            try:
                redis = await self._publisher()
                await redis.publish(self._channel(relationship_id), json.dumps(event))
                return
            except Exception as exc:
                logger.warning("Wallpaper Redis publish failed; using local event: %s", exc)
        self._dispatch(event)

    async def _publisher(self) -> Redis:
        if self._redis is None:
            self._redis = Redis.from_url(settings.redis_url, decode_responses=True)
            await self._redis.ping()
        return self._redis

    async def _listen(self, pubsub) -> None:
        try:
            while not self._stopping:
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if not message:
                    await asyncio.sleep(0.05)
                    continue
                try:
                    event = json.loads(message["data"])
                except (KeyError, TypeError, json.JSONDecodeError):
                    continue
                self._dispatch(event)
        finally:
            await pubsub.aclose()

    def _dispatch(self, event: dict[str, Any]) -> None:
        relationship_id = str(event.get("relationshipId") or "")
        for queue in list(self._subscribers.get(relationship_id, ())):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                queue.put_nowait(event)

    def _channel(self, relationship_id: str) -> str:
        return f"{settings.wallpaper_event_channel_prefix}:{relationship_id}"


wallpaper_event_hub = WallpaperEventHub()
