from __future__ import annotations

import uuid
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime

from app.db.models import (
    RelationshipWallpaperState,
    VoiceGenerationPlan,
    WallpaperRenderTask,
    WallpaperRevision,
    WallpaperViewHead,
    VoiceEvent,
)
from app.db.session import Base, SessionLocal, engine
from app.schemas.agent import AgentRunResult, SemanticMappingResult
from app.services.relationship_wallpaper_service import relationship_wallpaper_service


@dataclass(frozen=True)
class GenerationTasks:
    plan_id: str
    event_seq: int
    primary_task_id: str


@dataclass(frozen=True)
class RenderTaskBundle:
    task_id: str
    plan_id: str
    run_id: str
    relationship_id: str
    event_seq: int
    speaker_role: str
    view_role: str
    render_mode: str
    role_reference_images: dict[str, str | None]
    semantic_mapping: SemanticMappingResult


class WallpaperGenerationService:
    def create_tasks(
        self,
        result: AgentRunResult,
        *,
        speaker_role: str,
        generation_stage: str,
        role_reference_images: dict[str, str | None],
        event_seq: int | None = None,
    ) -> GenerationTasks:
        if result.semantic_mapping is None or not result.relationship_id:
            raise ValueError("Analyzed semantic mapping and relationship are required")

        Base.metadata.create_all(bind=engine)
        plan_id = uuid.uuid4().hex
        relationship_id = result.relationship_id
        semantic_payload = result.semantic_mapping.model_dump(mode="json")
        five_layer_plan = deepcopy(
            result.semantic_mapping.cognitive_scaffold.get(
                "fiveLayerPlan",
                {},
            )
        )

        with SessionLocal() as session:
            if event_seq is None:
                event_seq = relationship_wallpaper_service.reserve_event_seq(
                    relationship_id
                )
            five_layer_plan = self._with_persisted_role_visual_state(
                session,
                relationship_id=relationship_id,
                event_seq=event_seq,
                speaker_role=speaker_role,
                generation_stage=generation_stage,
                five_layer_plan=five_layer_plan,
            )
            scaffold = deepcopy(semantic_payload.get("cognitive_scaffold") or {})
            scaffold["fiveLayerPlan"] = five_layer_plan
            scaffold["roleEnvironmentStates"] = deepcopy(
                five_layer_plan.get("roleVisualState") or {}
            )
            semantic_payload["cognitive_scaffold"] = scaffold
            session.add(
                VoiceGenerationPlan(
                    plan_id=plan_id,
                    run_id=result.run_id,
                    relationship_id=relationship_id,
                    event_seq=event_seq,
                    speaker_role=speaker_role,
                    generation_stage=generation_stage,
                    designer_five_layer_plan=five_layer_plan,
                    semantic_mapping=semantic_payload,
                )
            )

            primary_task_id = ""
            partner_role = "elder" if speaker_role == "child" else "child"
            for view_role in (speaker_role, partner_role):
                task_id = uuid.uuid4().hex
                speaker_is_local = speaker_role == view_role
                if speaker_is_local:
                    primary_task_id = task_id
                session.add(
                    WallpaperRenderTask(
                        task_id=task_id,
                        plan_id=plan_id,
                        run_id=result.run_id,
                        relationship_id=relationship_id,
                        event_seq=event_seq,
                        speaker_role=speaker_role,
                        view_role=view_role,
                        render_mode=(
                            (
                                "initialize"
                                if generation_stage == "first_voice"
                                else "patch"
                            )
                            if speaker_is_local
                            else "mirror_no_provider_call"
                        ),
                        priority=100 if speaker_is_local else 10,
                        speaker_region=(
                            "left_bottom"
                            if speaker_role == "elder"
                            else "upper_right"
                        ),
                        preserved_region=(
                            "upper_right"
                            if speaker_role == "elder"
                            else "left_bottom"
                        ),
                        role_reference_images=role_reference_images,
                    )
                )
            session.commit()

        return GenerationTasks(
            plan_id=plan_id,
            event_seq=event_seq,
            primary_task_id=primary_task_id,
        )

    def _with_persisted_role_visual_state(
        self,
        session,
        *,
        relationship_id: str,
        event_seq: int,
        speaker_role: str,
        generation_stage: str,
        five_layer_plan: dict,
    ) -> dict:
        plan = deepcopy(five_layer_plan or {})
        speaker = self._normalize_role(speaker_role)
        incoming = self._extract_role_visual_state(plan)
        previous = self._latest_role_visual_state(
            session,
            relationship_id,
            before_event_seq=event_seq,
        )

        if generation_stage == "first_voice" or not previous:
            merged_roles = {
                role: deepcopy(incoming.get(role) or previous.get(role) or {})
                for role in ("elder", "child")
            }
            update_policy = "initialize_both_roles"
        else:
            other = "elder" if speaker == "child" else "child"
            merged_roles = {
                other: deepcopy(previous.get(other) or incoming.get(other) or {}),
                speaker: self._merge_active_role_state(
                    previous.get(speaker) or {},
                    incoming.get(speaker) or {},
                ),
            }
            update_policy = "update_current_speaker_only"

        role_visual_state = {
            "elder": merged_roles.get("elder", {}),
            "child": merged_roles.get("child", {}),
            "activeRole": speaker,
            "updatePolicy": update_policy,
            "eventSeq": event_seq,
        }
        plan["roleVisualState"] = role_visual_state
        l1 = plan.get("L1_environment_layer")
        if not isinstance(l1, dict):
            l1 = {}
            plan["L1_environment_layer"] = l1
        l1["roleStates"] = {
            "elder": deepcopy(role_visual_state["elder"]),
            "child": deepcopy(role_visual_state["child"]),
        }
        l1["activeRole"] = speaker
        l1["updatePolicy"] = update_policy
        return plan

    def _latest_role_visual_state(
        self,
        session,
        relationship_id: str,
        *,
        before_event_seq: int,
    ) -> dict:
        # A previous voice may already be queued while its wallpaper is still
        # rendering. Prefer that persisted plan so rapid consecutive turns do
        # not fall back to an older rendered revision and lose one role's state.
        queued_plan = (
            session.query(VoiceGenerationPlan)
            .filter(
                VoiceGenerationPlan.relationship_id == relationship_id,
                VoiceGenerationPlan.event_seq < before_event_seq,
            )
            .order_by(
                VoiceGenerationPlan.event_seq.desc(),
                VoiceGenerationPlan.created_at.desc(),
                VoiceGenerationPlan.id.desc(),
            )
            .first()
        )
        if queued_plan is not None:
            state = self._extract_role_visual_state(
                queued_plan.designer_five_layer_plan or {}
            )
            if state:
                return state

        revision = (
            session.query(WallpaperRevision)
            .filter(
                WallpaperRevision.relationship_id == relationship_id,
                WallpaperRevision.event_seq < before_event_seq,
            )
            .order_by(
                WallpaperRevision.event_seq.desc(),
                WallpaperRevision.created_at.desc(),
                WallpaperRevision.id.desc(),
            )
            .first()
        )
        if revision is None:
            return {}
        return self._extract_role_visual_state(
            revision.designer_five_layer_plan or {}
        )

    def _extract_role_visual_state(self, five_layer_plan: dict) -> dict:
        state = five_layer_plan.get("roleVisualState")
        if isinstance(state, dict):
            return {
                role: deepcopy(state.get(role) or {})
                for role in ("elder", "child")
            }
        l1 = five_layer_plan.get("L1_environment_layer")
        role_states = l1.get("roleStates") if isinstance(l1, dict) else None
        if not isinstance(role_states, dict):
            return {}
        return {
            role: deepcopy(role_states.get(role) or {})
            for role in ("elder", "child")
        }

    def _merge_active_role_state(self, previous: dict, incoming: dict) -> dict:
        if not incoming:
            return deepcopy(previous)
        merged = {**deepcopy(previous), **deepcopy(incoming)}
        lighting = incoming.get("lighting") if isinstance(incoming.get("lighting"), dict) else {}
        if lighting.get("updatePolicy") == "preserve_previous_exact_time" and previous:
            merged["timeEvidenceRaw"] = incoming.get("timeRaw") or ""
            merged["timeRaw"] = previous.get("timeRaw") or incoming.get("timeRaw")
            merged["timeBucket"] = previous.get("timeBucket") or incoming.get("timeBucket")
            merged["lighting"] = deepcopy(previous.get("lighting") or lighting)
        return merged

    def _normalize_role(self, role: str) -> str:
        normalized = (role or "").strip().lower()
        if normalized in {"elder", "parent", "mother", "father"}:
            return "elder"
        if normalized in {"child", "daughter", "son"}:
            return "child"
        raise ValueError(f"Unsupported speaker role: {role!r}")

    def get_task(self, task_id: str) -> RenderTaskBundle:
        Base.metadata.create_all(bind=engine)
        with SessionLocal() as session:
            task = (
                session.query(WallpaperRenderTask)
                .filter(WallpaperRenderTask.task_id == task_id)
                .one()
            )
            plan = (
                session.query(VoiceGenerationPlan)
                .filter(VoiceGenerationPlan.plan_id == task.plan_id)
                .one()
            )
            return self._bundle(task, plan)

    def get_task_by_plan_and_role(
        self,
        plan_id: str,
        view_role: str,
    ) -> RenderTaskBundle:
        Base.metadata.create_all(bind=engine)
        with SessionLocal() as session:
            task = (
                session.query(WallpaperRenderTask)
                .filter(
                    WallpaperRenderTask.plan_id == plan_id,
                    WallpaperRenderTask.view_role == view_role,
                )
                .one()
            )
            plan = (
                session.query(VoiceGenerationPlan)
                .filter(VoiceGenerationPlan.plan_id == plan_id)
                .one()
            )
            return self._bundle(task, plan)

    def pending_tasks_through(self, task_id: str) -> list[RenderTaskBundle]:
        target = self.get_task(task_id)
        with SessionLocal() as session:
            rows = (
                session.query(WallpaperRenderTask, VoiceGenerationPlan)
                .join(
                    VoiceGenerationPlan,
                    VoiceGenerationPlan.plan_id == WallpaperRenderTask.plan_id,
                )
                .filter(
                    WallpaperRenderTask.relationship_id == target.relationship_id,
                    WallpaperRenderTask.event_seq <= target.event_seq,
                    WallpaperRenderTask.render_mode != "mirror_no_provider_call",
                    WallpaperRenderTask.status.in_(("pending", "failed")),
                )
                .order_by(
                    WallpaperRenderTask.event_seq.asc(),
                    WallpaperRenderTask.priority.desc(),
                )
                .all()
            )
            return [self._bundle(task, plan) for task, plan in rows]

    def pending_primary_task_ids(self, limit: int = 500) -> list[str]:
        """Recover DB-committed tasks if the API stopped before Redis enqueue."""
        Base.metadata.create_all(bind=engine)
        with SessionLocal() as session:
            rows = (
                session.query(WallpaperRenderTask.task_id)
                .join(VoiceEvent, VoiceEvent.run_id == WallpaperRenderTask.run_id)
                .filter(
                    WallpaperRenderTask.status == "pending",
                    WallpaperRenderTask.render_mode != "mirror_no_provider_call",
                    VoiceEvent.status.in_(("analyzed", "queued")),
                )
                .order_by(
                    WallpaperRenderTask.created_at.asc(),
                    WallpaperRenderTask.priority.desc(),
                )
                .limit(limit)
                .all()
            )
            return [row[0] for row in rows]

    def current_image(self, relationship_id: str, view_role: str) -> tuple[str, str]:
        with SessionLocal() as session:
            state = (
                session.query(RelationshipWallpaperState)
                .filter(RelationshipWallpaperState.relationship_id == relationship_id)
                .one_or_none()
            )
            if state is not None:
                shared_url = state.child_view_url or state.elder_view_url
                if shared_url:
                    head = (
                        session.query(WallpaperViewHead)
                        .filter(
                            WallpaperViewHead.relationship_id == relationship_id,
                            WallpaperViewHead.view_role == view_role,
                        )
                        .one_or_none()
                    )
                    revision_id = (
                        head.current_revision_id
                        if head is not None and head.current_image_url == shared_url
                        else ""
                    )
                    return shared_url, revision_id

            head = (
                session.query(WallpaperViewHead)
                .filter(
                    WallpaperViewHead.relationship_id == relationship_id,
                    WallpaperViewHead.view_role == view_role,
                )
                .one_or_none()
            )
            if head is not None and head.current_image_url:
                return head.current_image_url, head.current_revision_id
            if state is None:
                return "", ""
            return state.child_view_url or state.elder_view_url, ""

    def mark_running(self, task_id: str, parent_image_url: str) -> None:
        with SessionLocal() as session:
            task = (
                session.query(WallpaperRenderTask)
                .filter(WallpaperRenderTask.task_id == task_id)
                .one()
            )
            task.status = "running"
            task.parent_image_url = parent_image_url
            task.started_at = datetime.utcnow()
            task.error = ""
            session.commit()

    def complete(
        self,
        bundle: RenderTaskBundle,
        *,
        parent_image_url: str,
        parent_revision_id: str,
        image_url: str,
        final_prompt: str,
    ) -> str:
        revision_id = uuid.uuid4().hex
        now = datetime.utcnow()
        with SessionLocal() as session:
            task = (
                session.query(WallpaperRenderTask)
                .filter(WallpaperRenderTask.task_id == bundle.task_id)
                .one()
            )
            plan = (
                session.query(VoiceGenerationPlan)
                .filter(VoiceGenerationPlan.plan_id == bundle.plan_id)
                .one()
            )
            task.status = "completed"
            task.parent_image_url = parent_image_url
            task.output_image_url = image_url
            task.final_prompt = final_prompt
            task.error = ""
            task.completed_at = now
            session.add(
                WallpaperRevision(
                    revision_id=revision_id,
                    task_id=bundle.task_id,
                    plan_id=bundle.plan_id,
                    run_id=bundle.run_id,
                    relationship_id=bundle.relationship_id,
                    event_seq=bundle.event_seq,
                    view_role=bundle.view_role,
                    parent_revision_id=parent_revision_id,
                    parent_image_url=parent_image_url,
                    image_url=image_url,
                    designer_five_layer_plan=plan.designer_five_layer_plan,
                    final_prompt=final_prompt,
                )
            )
            head = (
                session.query(WallpaperViewHead)
                .filter(
                    WallpaperViewHead.relationship_id == bundle.relationship_id,
                    WallpaperViewHead.view_role == bundle.view_role,
                )
                .one_or_none()
            )
            if head is None:
                head = WallpaperViewHead(
                    relationship_id=bundle.relationship_id,
                    view_role=bundle.view_role,
                )
                session.add(head)
            if bundle.event_seq >= (head.current_event_seq or 0):
                head.current_event_seq = bundle.event_seq
                head.current_revision_id = revision_id
                head.current_image_url = image_url
                head.updated_at = now
            session.commit()
        return revision_id

    def complete_mirror(
        self,
        bundle: RenderTaskBundle,
        *,
        source: RenderTaskBundle,
        image_url: str,
    ) -> str:
        """Complete the second view without making another image-provider call."""
        revision_id = uuid.uuid4().hex
        now = datetime.utcnow()
        final_prompt = (
            f"Shared wallpaper mirror of task {source.task_id}; "
            "no image provider call."
        )
        with SessionLocal() as session:
            task = (
                session.query(WallpaperRenderTask)
                .filter(WallpaperRenderTask.task_id == bundle.task_id)
                .one()
            )
            if task.status == "completed":
                return task.output_image_url
            plan = (
                session.query(VoiceGenerationPlan)
                .filter(VoiceGenerationPlan.plan_id == bundle.plan_id)
                .one()
            )
            task.status = "completed"
            task.parent_image_url = image_url
            task.output_image_url = image_url
            task.final_prompt = final_prompt
            task.error = ""
            task.started_at = task.started_at or now
            task.completed_at = now
            session.add(
                WallpaperRevision(
                    revision_id=revision_id,
                    task_id=bundle.task_id,
                    plan_id=bundle.plan_id,
                    run_id=bundle.run_id,
                    relationship_id=bundle.relationship_id,
                    event_seq=bundle.event_seq,
                    view_role=bundle.view_role,
                    parent_revision_id="",
                    parent_image_url=image_url,
                    image_url=image_url,
                    designer_five_layer_plan=plan.designer_five_layer_plan,
                    final_prompt=final_prompt,
                )
            )
            head = (
                session.query(WallpaperViewHead)
                .filter(
                    WallpaperViewHead.relationship_id == bundle.relationship_id,
                    WallpaperViewHead.view_role == bundle.view_role,
                )
                .one_or_none()
            )
            if head is None:
                head = WallpaperViewHead(
                    relationship_id=bundle.relationship_id,
                    view_role=bundle.view_role,
                )
                session.add(head)
            if bundle.event_seq >= (head.current_event_seq or 0):
                head.current_event_seq = bundle.event_seq
                head.current_revision_id = revision_id
                head.current_image_url = image_url
                head.updated_at = now
            session.commit()
        return image_url

    def fail(self, task_id: str, error: str) -> None:
        with SessionLocal() as session:
            task = (
                session.query(WallpaperRenderTask)
                .filter(WallpaperRenderTask.task_id == task_id)
                .one_or_none()
            )
            if task is None:
                return
            task.status = "failed"
            task.retry_count += 1
            task.error = error[:1000]
            task.completed_at = datetime.utcnow()
            session.commit()

    def task_output(self, task_id: str) -> str:
        with SessionLocal() as session:
            task = (
                session.query(WallpaperRenderTask)
                .filter(WallpaperRenderTask.task_id == task_id)
                .one()
            )
            return task.output_image_url

    @staticmethod
    def _bundle(
        task: WallpaperRenderTask,
        plan: VoiceGenerationPlan,
    ) -> RenderTaskBundle:
        semantic_payload = deepcopy(plan.semantic_mapping or {})
        scaffold = deepcopy(semantic_payload.get("cognitive_scaffold") or {})
        scaffold["fiveLayerPlan"] = deepcopy(plan.designer_five_layer_plan or {})
        semantic_payload["cognitive_scaffold"] = scaffold
        return RenderTaskBundle(
            task_id=task.task_id,
            plan_id=task.plan_id,
            run_id=task.run_id,
            relationship_id=task.relationship_id,
            event_seq=task.event_seq,
            speaker_role=task.speaker_role,
            view_role=task.view_role,
            render_mode=task.render_mode,
            role_reference_images=task.role_reference_images or {},
            semantic_mapping=SemanticMappingResult(**semantic_payload),
        )


wallpaper_generation_service = WallpaperGenerationService()
