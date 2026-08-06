from __future__ import annotations

import asyncio
import uuid

from arq import Retry
from arq.connections import RedisSettings

from app.core.config import settings
from app.core.wallpaper_events import wallpaper_event_hub
from app.services.relationship_wallpaper_service import relationship_wallpaper_service
from app.services.voice_event_service import voice_event_service
from app.services.wallpaper_generation_service import wallpaper_generation_service
from app.services.wallpaper_render_worker import wallpaper_render_worker


async def render_wallpaper(ctx: dict, task_id: str) -> None:
    bundle = wallpaper_generation_service.get_task(task_id)
    redis = ctx["redis"]
    lock_key = f"timewallpaper:render-lock:{bundle.relationship_id}"
    lock_token = uuid.uuid4().hex
    acquired = await redis.set(
        lock_key,
        lock_token,
        nx=True,
        ex=settings.render_family_lock_seconds,
    )
    if not acquired:
        raise Retry(defer=2)

    try:
        await asyncio.wait_for(
            wallpaper_render_worker.render_through(task_id),
            timeout=settings.render_job_timeout_seconds,
        )
    except Exception as exc:
        job_try = int(ctx.get("job_try") or 1)
        if job_try < settings.render_job_max_tries:
            delays = settings.parsed_render_retry_delays
            delay = delays[min(job_try - 1, len(delays) - 1)]
            await wallpaper_event_hub.publish(
                bundle.relationship_id,
                status="wallpaper_revision_retrying",
                version=bundle.event_seq,
                error=str(exc),
            )
            raise Retry(defer=delay) from exc

        relationship_wallpaper_service.mark_wallpaper_update_failed(
            bundle.relationship_id,
            str(exc),
            event_seq=bundle.event_seq,
        )
        voice_event_service.mark_failed_by_run(bundle.run_id, str(exc))
        await wallpaper_event_hub.publish(
            bundle.relationship_id,
            status="wallpaper_revision_failed",
            version=bundle.event_seq,
            error=str(exc),
        )
        raise
    finally:
        # Release only our own lock; an expired lock may already belong to a new job.
        await redis.eval(
            "if redis.call('get', KEYS[1]) == ARGV[1] then "
            "return redis.call('del', KEYS[1]) else return 0 end",
            1,
            lock_key,
            lock_token,
        )


async def recover_pending_jobs(ctx: dict) -> None:
    redis = ctx["redis"]
    for task_id in wallpaper_generation_service.pending_primary_task_ids():
        await redis.enqueue_job(
            "render_wallpaper",
            task_id,
            _job_id=f"wallpaper:{task_id}",
            _queue_name=settings.render_queue_name,
        )


class WorkerSettings:
    functions = [render_wallpaper]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    queue_name = settings.render_queue_name
    max_jobs = settings.render_worker_max_jobs
    job_timeout = settings.render_job_timeout_seconds
    max_tries = settings.render_job_max_tries
    keep_result = 3600
    on_startup = recover_pending_jobs
