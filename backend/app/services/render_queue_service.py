from __future__ import annotations

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.core.config import settings


class RenderQueueService:
    def __init__(self) -> None:
        self._pool: ArqRedis | None = None

    async def enqueue(self, task_id: str) -> bool:
        if not settings.render_queue_enabled:
            return False
        pool = await self._get_pool()
        job = await pool.enqueue_job(
            "render_wallpaper",
            task_id,
            _job_id=f"wallpaper:{task_id}",
            _queue_name=settings.render_queue_name,
        )
        # None means the same durable job id is already queued or running.
        return job is not None

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.aclose()
            self._pool = None

    async def _get_pool(self) -> ArqRedis:
        if self._pool is None:
            if not settings.redis_url:
                raise RuntimeError("REDIS_URL is required when render queue is enabled")
            self._pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
        return self._pool


render_queue_service = RenderQueueService()
