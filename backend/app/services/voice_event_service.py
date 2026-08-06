from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.exc import IntegrityError

from app.db.models import RelationshipState, VoiceEvent
from app.db.session import Base, SessionLocal, engine
from app.schemas.agent import AgentRunResult, ImageGenerationResult, WallpaperViewsResult
from app.services.relationship_wallpaper_service import relationship_wallpaper_service


@dataclass(frozen=True)
class VoiceEventClaim:
    created: bool
    event_id: str
    request_id: str
    run_id: str
    event_seq: int
    status: str
    result_payload: dict


class VoiceEventService:
    def begin(
        self,
        *,
        request_id: str | None,
        run_id: str,
        relationship_id: str,
        user_id: str,
        device_session_id: str,
        speaker_role: str,
        input_type: str,
    ) -> VoiceEventClaim:
        Base.metadata.create_all(bind=engine)
        normalized_request_id = (request_id or uuid.uuid4().hex).strip()[:64]
        if not normalized_request_id:
            normalized_request_id = uuid.uuid4().hex

        existing = self._find(user_id, normalized_request_id)
        if existing is not None:
            return self._claim(existing, created=False)

        event_seq = relationship_wallpaper_service.reserve_event_seq(relationship_id)
        with SessionLocal() as session:
            state = (
                session.query(RelationshipState)
                .filter(RelationshipState.relationship_id == relationship_id)
                .one_or_none()
            )
            row = VoiceEvent(
                event_id=uuid.uuid4().hex,
                request_id=normalized_request_id,
                run_id=run_id,
                relationship_id=relationship_id,
                user_id=user_id,
                device_session_id=device_session_id,
                speaker_role=speaker_role,
                input_type=input_type,
                event_seq=event_seq,
                base_state_version=state.version if state else 0,
                status="accepted",
            )
            session.add(row)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                existing = self._find(user_id, normalized_request_id)
                if existing is None:
                    raise
                return self._claim(existing, created=False)
            session.refresh(row)
            return self._claim(row, created=True)

    def mark_analyzed(self, event_id: str, result: AgentRunResult) -> None:
        now = datetime.utcnow()
        with SessionLocal() as session:
            row = self._get(session, event_id)
            row.status = "analyzed"
            row.transcript = (
                result.language_emotion.transcript[:4096]
                if result.language_emotion is not None
                else ""
            )
            row.short_term_table = (
                result.language_emotion.short_term_table
                if result.language_emotion is not None
                else {}
            )
            row.long_term_table = (
                result.memory_relation.long_term_table
                if result.memory_relation is not None
                else {}
            )
            row.semantic_mapping = (
                result.semantic_mapping.model_dump(mode="json")
                if result.semantic_mapping is not None
                else {}
            )
            row.result_payload = result.model_dump(mode="json", by_alias=True)
            row.analyzed_at = now
            row.updated_at = now
            session.commit()

    def mark_completed(self, event_id: str, result: AgentRunResult) -> None:
        now = datetime.utcnow()
        with SessionLocal() as session:
            row = self._get(session, event_id)
            row.status = "completed"
            row.result_payload = result.model_dump(mode="json", by_alias=True)
            row.completed_at = now
            row.updated_at = now
            row.error = ""
            session.commit()

    def mark_queued(self, event_id: str, result: AgentRunResult) -> None:
        now = datetime.utcnow()
        result.status = "queued"
        if result.steps:
            result.steps[-1].status = "queued"
        result.updated_at = now
        with SessionLocal() as session:
            row = self._get(session, event_id)
            row.status = "queued"
            row.result_payload = result.model_dump(mode="json", by_alias=True)
            row.updated_at = now
            session.commit()

    def mark_render_completed_by_run(
        self,
        run_id: str,
        *,
        image_url: str,
        speaker_role: str,
    ) -> None:
        now = datetime.utcnow()
        with SessionLocal() as session:
            row = (
                session.query(VoiceEvent)
                .filter(VoiceEvent.run_id == run_id)
                .one_or_none()
            )
            if row is None or row.status == "completed":
                return
            result = AgentRunResult(**(row.result_payload or {}))
            result.image_generation = ImageGenerationResult(
                wallpaper_url=image_url,
                generation_mode="versioned_render_task:queued",
            )
            result.wallpaper_views = WallpaperViewsResult(
                child_view_url=image_url,
                elder_view_url=image_url,
                speaker_role=speaker_role,
            )
            result.status = "done"
            if result.steps:
                result.steps[-1].status = "done"
            result.updated_at = now
            row.status = "completed"
            row.result_payload = result.model_dump(mode="json", by_alias=True)
            row.completed_at = now
            row.updated_at = now
            row.error = ""
            session.commit()

    def mark_failed_by_run(self, run_id: str, error: str) -> None:
        with SessionLocal() as session:
            row = (
                session.query(VoiceEvent)
                .filter(VoiceEvent.run_id == run_id)
                .one_or_none()
            )
            if row is None:
                return
            event_id = row.event_id
        self.mark_failed(event_id, error)

    def mark_failed(self, event_id: str, error: str) -> None:
        now = datetime.utcnow()
        with SessionLocal() as session:
            row = self._get(session, event_id)
            if row.status == "completed":
                return
            row.status = "failed"
            row.error = error[:1000]
            row.completed_at = now
            row.updated_at = now
            session.commit()

    def _find(self, user_id: str, request_id: str) -> VoiceEvent | None:
        with SessionLocal() as session:
            row = (
                session.query(VoiceEvent)
                .filter(
                    VoiceEvent.user_id == user_id,
                    VoiceEvent.request_id == request_id,
                )
                .one_or_none()
            )
            if row is not None:
                session.expunge(row)
            return row

    @staticmethod
    def _get(session, event_id: str) -> VoiceEvent:
        return (
            session.query(VoiceEvent)
            .filter(VoiceEvent.event_id == event_id)
            .one()
        )

    @staticmethod
    def _claim(row: VoiceEvent, *, created: bool) -> VoiceEventClaim:
        return VoiceEventClaim(
            created=created,
            event_id=row.event_id,
            request_id=row.request_id,
            run_id=row.run_id,
            event_seq=row.event_seq,
            status=row.status,
            result_payload=row.result_payload or {},
        )


voice_event_service = VoiceEventService()
