from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.db.models import (
    CharacterAsset,
    RelationshipProfile,
    RelationshipWallpaperState,
    UserProfile,
    VoiceGenerationPlan,
)
from app.db.session import Base, SessionLocal, engine


@dataclass(frozen=True)
class BaseSceneClaim:
    action: str
    image_url: str = ""


class RelationshipWallpaperService:
    def reserve_event_seq(self, relationship_id: str) -> int:
        """Atomically reserve the next ordered event number for one family."""
        Base.metadata.create_all(bind=engine)
        self._ensure_state(relationship_id)
        with SessionLocal() as session:
            row = (
                session.query(RelationshipWallpaperState)
                .filter(RelationshipWallpaperState.relationship_id == relationship_id)
                .with_for_update()
                .one()
            )
            max_plan_seq = (
                session.query(func.max(VoiceGenerationPlan.event_seq))
                .filter(VoiceGenerationPlan.relationship_id == relationship_id)
                .scalar()
                or 0
            )
            event_seq = max(row.next_event_seq, row.version, max_plan_seq) + 1
            row.next_event_seq = event_seq
            row.updated_at = datetime.utcnow()
            session.commit()
            return event_seq

    def characters_ready(self, relationship_id: str) -> bool:
        with SessionLocal() as session:
            rows = (
                session.query(CharacterAsset.role)
                .filter(
                    CharacterAsset.relationship_id == relationship_id,
                    CharacterAsset.status == "ready",
                    CharacterAsset.is_active.is_(True),
                )
                .distinct()
                .all()
            )
        return {row[0] for row in rows} >= {"elder", "child"}

    def get(self, relationship_id: str) -> RelationshipWallpaperState | None:
        Base.metadata.create_all(bind=engine)
        with SessionLocal() as session:
            row = (
                session.query(RelationshipWallpaperState)
                .filter(RelationshipWallpaperState.relationship_id == relationship_id)
                .one_or_none()
            )
            if row is not None:
                session.expunge(row)
            return row

    def claim_first_voice(self, relationship_id: str) -> BaseSceneClaim:
        Base.metadata.create_all(bind=engine)
        self._ensure_state(relationship_id)
        with SessionLocal() as session:
            row = (
                session.query(RelationshipWallpaperState)
                .filter(RelationshipWallpaperState.relationship_id == relationship_id)
                .one_or_none()
            )
            if row.stage == "wallpaper_active":
                return BaseSceneClaim("complete")
            if row.status == "generating":
                return BaseSceneClaim("wait")

            claimed = (
                session.query(RelationshipWallpaperState)
                .filter(
                    RelationshipWallpaperState.relationship_id == relationship_id,
                    RelationshipWallpaperState.stage.in_(
                        ("characters_ready", "base_scene_ready")
                    ),
                    RelationshipWallpaperState.status.in_(("idle", "failed")),
                )
                .update(
                    {
                        RelationshipWallpaperState.status: "generating",
                        RelationshipWallpaperState.last_error: "",
                        RelationshipWallpaperState.updated_at: datetime.utcnow(),
                    },
                    synchronize_session=False,
                )
            )
            session.commit()
            return BaseSceneClaim(
                "generate" if claimed else "wait",
                settings.static_base_scene_url,
            )

    def save_shared_revision(
        self,
        relationship_id: str,
        *,
        image_url: str,
        run_id: str,
        event_seq: int,
    ) -> bool:
        """Publish one rendered image as the relationship's shared wallpaper."""
        Base.metadata.create_all(bind=engine)
        self._ensure_state(relationship_id)
        with SessionLocal() as session:
            row = (
                session.query(RelationshipWallpaperState)
                .filter(RelationshipWallpaperState.relationship_id == relationship_id)
                .one()
            )
            if event_seq < row.version:
                return False
            row.stage = "wallpaper_active"
            row.status = "idle"
            row.child_view_url = image_url
            row.elder_view_url = image_url
            row.version = event_seq
            row.next_event_seq = max(row.next_event_seq, event_seq)
            row.latest_run_id = run_id
            row.last_error = ""
            row.updated_at = datetime.utcnow()
            self._sync_user_wallpapers(
                session,
                relationship_id,
                wallpaper_url=image_url,
            )
            session.commit()
            return True

    def mark_render_queued(
        self,
        relationship_id: str,
        *,
        run_id: str,
        event_seq: int,
    ) -> None:
        """Expose durable queue state without replacing a newer family event."""
        self._ensure_state(relationship_id)
        with SessionLocal() as session:
            row = (
                session.query(RelationshipWallpaperState)
                .filter(RelationshipWallpaperState.relationship_id == relationship_id)
                .with_for_update()
                .one()
            )
            if event_seq < row.version:
                return
            row.status = "generating"
            row.next_event_seq = max(row.next_event_seq, event_seq)
            row.latest_run_id = run_id
            row.last_error = ""
            row.updated_at = datetime.utcnow()
            session.commit()

    def mark_first_voice_failed(
        self,
        relationship_id: str,
        error: str,
        *,
        event_seq: int | None = None,
    ) -> None:
        self._mark_generation_failed(relationship_id, error, event_seq=event_seq)

    def mark_wallpaper_update_failed(
        self,
        relationship_id: str,
        error: str,
        *,
        event_seq: int | None = None,
    ) -> None:
        self._mark_generation_failed(relationship_id, error, event_seq=event_seq)

    def _mark_generation_failed(
        self,
        relationship_id: str,
        error: str,
        *,
        event_seq: int | None,
    ) -> None:
        with SessionLocal() as session:
            row = (
                session.query(RelationshipWallpaperState)
                .filter(RelationshipWallpaperState.relationship_id == relationship_id)
                .one_or_none()
            )
            if row is None:
                return
            if event_seq is not None and row.next_event_seq > event_seq:
                return
            row.status = "failed"
            row.last_error = error[:1000]
            row.updated_at = datetime.utcnow()
            session.commit()

    def _ensure_state(self, relationship_id: str) -> None:
        with SessionLocal() as session:
            exists = (
                session.query(RelationshipWallpaperState.id)
                .filter(RelationshipWallpaperState.relationship_id == relationship_id)
                .first()
            )
            if exists:
                return
            session.add(RelationshipWallpaperState(relationship_id=relationship_id))
            try:
                session.commit()
            except IntegrityError:
                session.rollback()

    @staticmethod
    def _sync_user_wallpapers(
        session,
        relationship_id: str,
        *,
        wallpaper_url: str,
    ) -> None:
        relationship = (
            session.query(RelationshipProfile)
            .filter(RelationshipProfile.relationship_id == relationship_id)
            .one()
        )
        for user_id in (
            relationship.child_user_id,
            relationship.parent_user_id,
        ):
            user = (
                session.query(UserProfile)
                .filter(UserProfile.user_id == user_id)
                .one_or_none()
            )
            if user is None:
                continue
            user.profile = {
                **(user.profile or {}),
                "onboardingStep": "wallpaper",
                "latestWallpaperUrl": wallpaper_url,
            }
            user.updated_at = datetime.utcnow()

relationship_wallpaper_service = RelationshipWallpaperService()
