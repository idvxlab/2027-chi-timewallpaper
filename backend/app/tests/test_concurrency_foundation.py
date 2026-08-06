from __future__ import annotations

import asyncio
import uuid

from app.agents.orchestrator import MultiAgentOrchestrator
from app.db.models import RelationshipProfile, RelationshipState
from app.db.session import SessionLocal
from app.schemas.agent import MemoryRelationResult
from app.services.relationship_wallpaper_service import RelationshipWallpaperService
from app.services.voice_event_service import VoiceEventService


def _memory(trend: str) -> MemoryRelationResult:
    return MemoryRelationResult(
        longitudinal={"stability_fluctuation": "状态稳定"},
        relational={"relationship_trend": trend},
        long_term_table={"trend": trend},
        memory_card={"frontHint": "", "backSummary": ""},
        history_summary={"trend": trend},
    )


def test_voice_request_id_is_idempotent_and_family_sequence_is_shared() -> None:
    suffix = uuid.uuid4().hex
    relationship_id = f"relationship-concurrency-{suffix}"
    request_id = f"request-{suffix}"
    service = VoiceEventService()

    first = service.begin(
        request_id=request_id,
        run_id=f"run-1-{suffix}",
        relationship_id=relationship_id,
        user_id=f"parent-{suffix}",
        device_session_id=f"device-parent-{suffix}",
        speaker_role="elder",
        input_type="wallpaper_audio",
    )
    duplicate = service.begin(
        request_id=request_id,
        run_id=f"run-duplicate-{suffix}",
        relationship_id=relationship_id,
        user_id=f"parent-{suffix}",
        device_session_id=f"device-parent-{suffix}",
        speaker_role="elder",
        input_type="wallpaper_audio",
    )
    child = service.begin(
        request_id=f"request-child-{suffix}",
        run_id=f"run-2-{suffix}",
        relationship_id=relationship_id,
        user_id=f"child-{suffix}",
        device_session_id=f"device-child-{suffix}",
        speaker_role="child",
        input_type="wallpaper_audio",
    )

    assert first.created is True
    assert duplicate.created is False
    assert duplicate.event_id == first.event_id
    assert duplicate.event_seq == first.event_seq
    assert child.event_seq == first.event_seq + 1


def test_older_relationship_state_cannot_overwrite_newer_event() -> None:
    suffix = uuid.uuid4().hex
    relationship_id = f"relationship-state-{suffix}"
    orchestrator = MultiAgentOrchestrator()

    newer_saved = asyncio.run(
        orchestrator._save_relationship_state(
            relationship_id,
            _memory("靠近"),
            event_seq=2,
        )
    )
    older_saved = asyncio.run(
        orchestrator._save_relationship_state(
            relationship_id,
            _memory("疏远"),
            event_seq=1,
        )
    )

    assert newer_saved is True
    assert older_saved is False
    with SessionLocal() as session:
        state = (
            session.query(RelationshipState)
            .filter(RelationshipState.relationship_id == relationship_id)
            .one()
        )
        assert state.relational["relationship_trend"] == "靠近"
        assert state.last_applied_event_seq == 2
        assert state.version == 1


def test_older_wallpaper_revision_cannot_replace_newer_shared_image() -> None:
    suffix = uuid.uuid4().hex
    relationship_id = f"relationship-wallpaper-{suffix}"
    service = RelationshipWallpaperService()

    with SessionLocal() as session:
        session.add(
            RelationshipProfile(
                relationship_id=relationship_id,
                parent_user_id=f"parent-{suffix}",
                child_user_id=f"child-{suffix}",
            )
        )
        session.commit()

    service.reserve_event_seq(relationship_id)
    service.reserve_event_seq(relationship_id)
    assert service.save_shared_revision(
        relationship_id,
        image_url=f"/generated/new-{suffix}.png",
        run_id=f"new-run-{suffix}",
        event_seq=2,
    )
    assert not service.save_shared_revision(
        relationship_id,
        image_url=f"/generated/old-{suffix}.png",
        run_id=f"old-run-{suffix}",
        event_seq=1,
    )

    state = service.get(relationship_id)
    assert state is not None
    assert state.version == 2
    assert state.child_view_url == f"/generated/new-{suffix}.png"
    assert state.elder_view_url == f"/generated/new-{suffix}.png"


def test_old_failure_does_not_mark_newer_family_event_failed() -> None:
    suffix = uuid.uuid4().hex
    relationship_id = f"relationship-failure-{suffix}"
    service = RelationshipWallpaperService()

    first_seq = service.reserve_event_seq(relationship_id)
    second_seq = service.reserve_event_seq(relationship_id)
    service.mark_wallpaper_update_failed(
        relationship_id,
        "old task failed",
        event_seq=first_seq,
    )

    state = service.get(relationship_id)
    assert state is not None
    assert second_seq > first_seq
    assert state.status != "failed"
    assert state.last_error == ""
