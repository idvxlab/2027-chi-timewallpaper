from __future__ import annotations

import asyncio
from collections import defaultdict

from app.agents.image_generation_agent import ImageGenerationAgent
from app.core.config import settings
from app.core.wallpaper_events import wallpaper_event_hub
from app.schemas.agent import ImageGenerationResult
from app.services.relationship_wallpaper_service import relationship_wallpaper_service
from app.services.wallpaper_generation_service import (
    RenderTaskBundle,
    wallpaper_generation_service,
)
from app.services.voice_event_service import voice_event_service


class WallpaperRenderWorker:
    """Runs one real render per event and mirrors it to the relationship's other view."""

    def __init__(self) -> None:
        self.image_agent = ImageGenerationAgent()
        self._relationship_locks: defaultdict[str, asyncio.Lock] = defaultdict(
            asyncio.Lock
        )

    async def render_through(self, task_id: str) -> ImageGenerationResult:
        target = wallpaper_generation_service.get_task(task_id)
        lock = self._relationship_locks[target.relationship_id]
        async with lock:
            pending = wallpaper_generation_service.pending_tasks_through(task_id)
            latest_result: ImageGenerationResult | None = None
            for bundle in pending:
                latest_result = await self._render_one(bundle)

            if target.render_mode == "mirror_no_provider_call":
                source = wallpaper_generation_service.get_task_by_plan_and_role(
                    target.plan_id,
                    target.speaker_role,
                )
                source_url = wallpaper_generation_service.task_output(source.task_id)
                if not source_url:
                    raise RuntimeError(
                        f"Shared source task {source.task_id} did not produce an image"
                    )
                wallpaper_generation_service.complete_mirror(
                    target,
                    source=source,
                    image_url=source_url,
                )

            output_url = wallpaper_generation_service.task_output(task_id)
            if not output_url:
                raise RuntimeError(f"Render task {task_id} did not produce an image")
            if latest_result is not None and latest_result.wallpaper_url == output_url:
                return latest_result
            return ImageGenerationResult(
                wallpaper_url=output_url,
                generation_mode="versioned_render_task:reused",
            )

    async def _render_one(self, bundle: RenderTaskBundle) -> ImageGenerationResult:
        if bundle.render_mode == "mirror_no_provider_call":
            raise RuntimeError("Mirror tasks must not call the image provider")
        parent_image_url, parent_revision_id = wallpaper_generation_service.current_image(
            bundle.relationship_id,
            bundle.view_role,
        )
        if bundle.render_mode == "initialize":
            parent_image_url = settings.static_base_scene_url
            parent_revision_id = ""
        if not parent_image_url:
            raise RuntimeError(
                f"No parent wallpaper is ready for {bundle.view_role} event {bundle.event_seq}"
            )

        wallpaper_generation_service.mark_running(
            bundle.task_id,
            parent_image_url,
        )
        generation_stage = (
            "first_voice"
            if bundle.render_mode == "initialize"
            else "subsequent_update"
        )
        try:
            image = await self.image_agent.run(
                bundle.semantic_mapping,
                previous_image_url=parent_image_url,
                role_reference_images=bundle.role_reference_images,
                run_id=bundle.run_id,
                generation_stage=generation_stage,
                speaker_role=bundle.speaker_role,
            )
            if not image.wallpaper_url or image.wallpaper_url == parent_image_url:
                raise RuntimeError(
                    f"Render task did not update the {bundle.view_role} view"
                )
            final_prompt = str(image.asset_metadata.get("prompt") or "")
            wallpaper_generation_service.complete(
                bundle,
                parent_image_url=parent_image_url,
                parent_revision_id=parent_revision_id,
                image_url=image.wallpaper_url,
                final_prompt=final_prompt,
            )
            mirror_role = "elder" if bundle.view_role == "child" else "child"
            mirror = wallpaper_generation_service.get_task_by_plan_and_role(
                bundle.plan_id,
                mirror_role,
            )
            wallpaper_generation_service.complete_mirror(
                mirror,
                source=bundle,
                image_url=image.wallpaper_url,
            )
            published = relationship_wallpaper_service.save_shared_revision(
                bundle.relationship_id,
                image_url=image.wallpaper_url,
                run_id=bundle.run_id,
                event_seq=bundle.event_seq,
            )
            voice_event_service.mark_render_completed_by_run(
                bundle.run_id,
                image_url=image.wallpaper_url,
                speaker_role=bundle.speaker_role,
            )
            if published:
                await wallpaper_event_hub.publish(
                    bundle.relationship_id,
                    status="wallpaper_revision_ready",
                    version=bundle.event_seq,
                    image_url=image.wallpaper_url,
                )
            return image
        except Exception as exc:
            wallpaper_generation_service.fail(bundle.task_id, str(exc))
            raise


wallpaper_render_worker = WallpaperRenderWorker()
