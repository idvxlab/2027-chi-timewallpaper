import asyncio
from datetime import datetime
import uuid

from fastapi.testclient import TestClient
import pytest

from app.db.models import (
    RelationshipWallpaperState,
    VoiceGenerationPlan,
    WallpaperRenderTask,
    WallpaperRevision,
)
from app.db.session import SessionLocal
from app.main import app
from app.routes import agent_runs
from app.core.wallpaper_events import wallpaper_event_hub
from app.services.wallpaper_generation_service import WallpaperGenerationService
from app.schemas.agent import (
    AgentRunResult,
    ImageGenerationResult,
    SemanticMappingResult,
)


STATIC_BASE_SCENE_URL = (
    "/generated/wallpaper-main-square-1536.png"
)


def _create_connected_pair() -> tuple[TestClient, TestClient, dict]:
    child_client = TestClient(app)
    elder_client = TestClient(app)
    profile = child_client.post(
        "/onboarding/profile",
        json={
            "viewerRole": "child",
            "displayName": "Shared Scene Child",
            "gender": "female",
        },
    ).json()
    joined = elder_client.post(
        "/relationships/join",
        json={
            "inviteCode": profile["inviteCode"],
            "viewerRole": "elder",
            "displayName": "Shared Scene Parent",
            "gender": "female",
        },
    )
    assert joined.status_code == 200
    return child_client, elder_client, profile


def test_current_wallpaper_requires_session() -> None:
    with TestClient(app) as client:
        response = client.get("/wallpapers/current")

    assert response.status_code == 401


def test_revision_history_is_scoped_to_current_viewer_role() -> None:
    child_client, elder_client, profile = _create_connected_pair()
    relationship_id = profile["relationshipId"]
    suffix = uuid.uuid4().hex
    with SessionLocal() as session:
        for role in ("child", "elder"):
            session.add(
                WallpaperRevision(
                    revision_id=f"revision-{role}-{suffix}",
                    task_id=f"task-{role}-{suffix}",
                    plan_id=f"plan-{suffix}",
                    run_id=f"run-{suffix}",
                    relationship_id=relationship_id,
                    event_seq=1,
                    view_role=role,
                    image_url=f"/generated/{role}-{suffix}.png",
                )
            )
        session.commit()

    try:
        child_response = child_client.get("/wallpapers/revisions")
        elder_response = elder_client.get("/wallpapers/revisions")
    finally:
        child_client.close()
        elder_client.close()

    assert child_response.status_code == 200
    assert elder_response.status_code == 200
    assert child_response.json()["items"][-1]["imageUrl"].endswith(
        f"child-{suffix}.png"
    )
    assert elder_response.json()["items"][-1]["imageUrl"].endswith(
        f"elder-{suffix}.png"
    )


def test_fixed_base_scene_is_hidden_until_both_characters_are_ready(monkeypatch) -> None:
    child_client, elder_client, _ = _create_connected_pair()
    monkeypatch.setattr(
        agent_runs.relationship_wallpaper_service,
        "characters_ready",
        lambda relationship_id: False,
    )
    try:
        response = child_client.get("/wallpapers/current")
        removed_endpoint = child_client.post("/agent-runs/base-scene", json={})
    finally:
        child_client.close()
        elder_client.close()

    assert response.status_code == 200
    assert response.json()["stage"] == "awaiting_characters"
    assert response.json()["baseSceneUrl"] == ""
    assert removed_endpoint.status_code in {404, 405}


def test_relationship_uses_public_base_scene_without_persisting_it(monkeypatch) -> None:
    child_client, elder_client, profile = _create_connected_pair()

    monkeypatch.setattr(
        agent_runs.relationship_wallpaper_service,
        "characters_ready",
        lambda relationship_id: True,
    )

    try:
        child_current = child_client.get("/wallpapers/current")
        elder_current = elder_client.get("/wallpapers/current")
    finally:
        child_client.close()
        elder_client.close()

    for response, role in ((child_current, "child"), (elder_current, "elder")):
        assert response.status_code == 200
        payload = response.json()
        assert payload["relationshipId"] == profile["relationshipId"]
        assert payload["viewerRole"] == role
        assert payload["stage"] == "characters_ready"
        assert payload["baseSceneUrl"] == STATIC_BASE_SCENE_URL
        assert payload["wallpaperUrl"] == STATIC_BASE_SCENE_URL

    with SessionLocal() as session:
        state = (
            session.query(RelationshipWallpaperState)
            .filter(
                RelationshipWallpaperState.relationship_id
                == profile["relationshipId"]
            )
            .one_or_none()
        )
        assert state is None


def test_first_voice_generates_once_and_publishes_one_shared_url(monkeypatch) -> None:
    child_client, elder_client, profile = _create_connected_pair()
    relationship_id = profile["relationshipId"]
    monkeypatch.setattr(
        agent_runs,
        "_resolve_role_references",
        lambda **kwargs: {
            "child": "/character-assets/child.png",
            "elder": "/character-assets/elder.png",
        },
    )

    async def fake_run_audio(*args, **kwargs) -> AgentRunResult:
        assert args[0] == b"first-voice-audio"
        assert kwargs["user_id"] == profile["userId"]
        assert kwargs["relationship_id"] == relationship_id
        assert kwargs["generation_stage"] == "first_voice"
        assert kwargs["speaker_role"] == "child"
        assert kwargs["analyze_only"] is True
        now = datetime.utcnow()
        return AgentRunResult(
            run_id="first-voice-run",
            status="done",
            user_id=kwargs["user_id"],
            relationship_id=kwargs["relationship_id"],
            steps=[],
            semantic_mapping=SemanticMappingResult(
                semantic_visual_instruction="first voice scene",
                cognitive_scaffold={
                    "fiveLayerPlan": {
                        "L1_environment_layer": {"prompt": "warm room"},
                        "L4_character_layer": {"prompt": "child speaking"},
                    }
                },
            ),
            created_at=now,
            updated_at=now,
        )

    monkeypatch.setattr(agent_runs.orchestrator, "run_audio", fake_run_audio)

    image_calls: list[str] = []

    async def fake_generate_view(*args, **kwargs) -> ImageGenerationResult:
        assert kwargs["generation_stage"] == "first_voice"
        assert kwargs["speaker_role"] == "child"
        image_calls.append(kwargs["speaker_role"])
        return ImageGenerationResult(
            wallpaper_url="/generated/shared-first.png",
            asset_metadata={"prompt": "shared-plan + fixed-role-routing"},
        )

    monkeypatch.setattr(
        agent_runs.orchestrator.image_generation_agent,
        "run",
        fake_generate_view,
    )
    try:
        generated = child_client.post(
            "/agent-runs/current/audio",
            files={
                "audio": (
                    "first-voice.webm",
                    b"first-voice-audio",
                    "audio/webm",
                )
            },
        )
        child_current = child_client.get("/wallpapers/current")
        elder_current = elder_client.get("/wallpapers/current")
        child_session = child_client.get("/sessions/me")
        elder_session = elder_client.get("/sessions/me")
    finally:
        child_client.close()
        elder_client.close()

    assert generated.status_code == 200
    views = generated.json()["result"]["wallpaperViews"]
    assert views == {
        "childViewUrl": "/generated/shared-first.png",
        "elderViewUrl": "/generated/shared-first.png",
        "speakerRole": "child",
    }
    assert image_calls == ["child"]
    assert child_current.json()["wallpaperUrl"] == "/generated/shared-first.png"
    assert elder_current.json()["wallpaperUrl"] == "/generated/shared-first.png"
    assert child_current.json()["stage"] == "wallpaper_active"
    assert elder_current.json()["stage"] == "wallpaper_active"
    assert child_session.json()["onboardingStep"] == "wallpaper"
    assert child_session.json()["wallpaperUrl"] == "/generated/shared-first.png"
    assert elder_session.json()["onboardingStep"] == "wallpaper"
    assert elder_session.json()["wallpaperUrl"] == "/generated/shared-first.png"
    with SessionLocal() as session:
        plan = (
            session.query(VoiceGenerationPlan)
            .filter(VoiceGenerationPlan.relationship_id == relationship_id)
            .one()
        )
        assert plan.event_seq == 1
        assert plan.designer_five_layer_plan["L4_character_layer"]["prompt"] == "child speaking"
        tasks = (
            session.query(WallpaperRenderTask)
            .filter(WallpaperRenderTask.plan_id == plan.plan_id)
            .all()
        )
        assert len(tasks) == 2
        assert {task.render_mode for task in tasks} == {
            "initialize",
            "mirror_no_provider_call",
        }
        revisions = (
            session.query(WallpaperRevision)
            .filter(WallpaperRevision.plan_id == plan.plan_id)
            .order_by(WallpaperRevision.view_role)
            .all()
        )
        assert len(revisions) == 2
        assert {row.view_role for row in revisions} == {"child", "elder"}
        assert {row.image_url for row in revisions} == {
            "/generated/shared-first.png"
        }
        assert all(row.final_prompt for row in revisions)
        assert all(
            row.designer_five_layer_plan == plan.designer_five_layer_plan
            for row in revisions
        )


def test_subsequent_voice_updates_both_views_as_one_version(monkeypatch) -> None:
    child_client, elder_client, profile = _create_connected_pair()
    relationship_id = profile["relationshipId"]
    monkeypatch.setattr(
        agent_runs,
        "_resolve_role_references",
        lambda **kwargs: {
            "child": "/character-assets/child.png",
            "elder": "/character-assets/elder.png",
        },
    )
    assert (
        agent_runs.relationship_wallpaper_service.claim_first_voice(
            relationship_id
        ).action
        == "generate"
    )
    agent_runs.relationship_wallpaper_service.save_shared_revision(
        relationship_id,
        image_url="/generated/shared-v1.png",
        run_id="initial-run",
        event_seq=1,
    )

    async def fake_run_audio(*args, **kwargs) -> AgentRunResult:
        assert args[0] == b"elder-update-audio"
        assert kwargs["generation_stage"] == "subsequent_update"
        assert kwargs["speaker_role"] == "elder"
        assert kwargs["analyze_only"] is True
        now = datetime.utcnow()
        return AgentRunResult(
            run_id="update-run",
            status="done",
            user_id=kwargs["user_id"],
            relationship_id=kwargs["relationship_id"],
            steps=[],
            semantic_mapping=SemanticMappingResult(
                semantic_visual_instruction="elder update",
                cognitive_scaffold={
                    "fiveLayerPlan": {
                        "L4_character_layer": {"prompt": "elder speaking"}
                    }
                },
            ),
            created_at=now,
            updated_at=now,
        )

    monkeypatch.setattr(agent_runs.orchestrator, "run_audio", fake_run_audio)

    image_calls: list[str] = []

    async def fake_generate_view(*args, **kwargs) -> ImageGenerationResult:
        image_calls.append(kwargs["speaker_role"])
        assert kwargs["generation_stage"] == "subsequent_update"
        assert kwargs["speaker_role"] == "elder"
        assert kwargs["previous_image_url"] == "/generated/shared-v1.png"
        return ImageGenerationResult(
            wallpaper_url="/generated/shared-v2.png",
            asset_metadata={"prompt": "elder update + fixed-role-routing"},
        )

    monkeypatch.setattr(
        agent_runs.orchestrator.image_generation_agent,
        "run",
        fake_generate_view,
    )
    try:
        updated = elder_client.post(
            "/agent-runs/current/wallpaper-audio",
            files={
                "audio": (
                    "wallpaper-voice.webm",
                    b"elder-update-audio",
                    "audio/webm",
                )
            },
        )
        child_current = child_client.get("/wallpapers/current")
        elder_current = elder_client.get("/wallpapers/current")
    finally:
        child_client.close()
        elder_client.close()

    assert updated.status_code == 200
    assert image_calls == ["elder"]
    assert child_current.json()["wallpaperUrl"] == "/generated/shared-v2.png"
    assert elder_current.json()["wallpaperUrl"] == "/generated/shared-v2.png"
    assert child_current.json()["version"] == 2
    assert elder_current.json()["version"] == 2


def test_render_tasks_are_fifo_per_relationship_across_speakers() -> None:
    relationship_id = f"fifo-{uuid.uuid4().hex}"
    service = WallpaperGenerationService()

    def analyzed_result(run_id: str) -> AgentRunResult:
        now = datetime.utcnow()
        return AgentRunResult(
            run_id=run_id,
            status="analyzed",
            user_id="fifo-user",
            relationship_id=relationship_id,
            steps=[],
            semantic_mapping=SemanticMappingResult(
                semantic_visual_instruction="shared visual plan",
                cognitive_scaffold={
                    "fiveLayerPlan": {
                        "L4_character_layer": {"prompt": run_id}
                    }
                },
            ),
            created_at=now,
            updated_at=now,
        )

    first = service.create_tasks(
        analyzed_result("fifo-run-1"),
        speaker_role="child",
        generation_stage="first_voice",
        role_reference_images={"child": "/child.png", "elder": "/elder.png"},
    )
    second = service.create_tasks(
        analyzed_result("fifo-run-2"),
        speaker_role="elder",
        generation_stage="subsequent_update",
        role_reference_images={"child": "/child.png", "elder": "/elder.png"},
    )

    pending = service.pending_tasks_through(second.primary_task_id)
    assert [task.event_seq for task in pending] == [first.event_seq, second.event_seq]
    assert [task.view_role for task in pending] == ["child", "elder"]


def test_subsequent_plan_persists_both_role_states_and_updates_speaker_only() -> None:
    relationship_id = f"role-state-{uuid.uuid4().hex}"
    previous_plan = {
        "roleVisualState": {
            "elder": {
                "available": True,
                "timeRaw": "白天",
                "sceneRaw": "家里",
                "lighting": {"brightness": 0.9, "lampState": "off"},
            },
            "child": {
                "available": True,
                "timeRaw": "下午",
                "sceneRaw": "学校",
                "lighting": {"brightness": 0.7, "lampState": "off"},
            },
        }
    }
    with SessionLocal() as session:
        session.add(
            WallpaperRevision(
                revision_id=f"role-state-revision-{uuid.uuid4().hex}",
                task_id=f"role-state-task-{uuid.uuid4().hex}",
                plan_id=f"role-state-old-plan-{uuid.uuid4().hex}",
                run_id=f"role-state-old-run-{uuid.uuid4().hex}",
                relationship_id=relationship_id,
                event_seq=1,
                view_role="child",
                image_url="/generated/role-state-v1.png",
                designer_five_layer_plan=previous_plan,
            )
        )
        session.commit()

    incoming_states = {
        "elder": {
            "available": True,
            "timeRaw": "深夜",
            "sceneRaw": "不应覆盖父母一侧",
            "lighting": {"brightness": 0.18, "lampState": "off"},
        },
        "child": {
            "available": True,
            "timeRaw": "晚上",
            "sceneRaw": "单位",
            "lighting": {"brightness": 0.3, "lampState": "on"},
        },
    }
    now = datetime.utcnow()
    result = AgentRunResult(
        run_id=f"role-state-new-run-{uuid.uuid4().hex}",
        status="analyzed",
        user_id="role-state-child",
        relationship_id=relationship_id,
        steps=[],
        semantic_mapping=SemanticMappingResult(
            semantic_visual_instruction="role state update",
            cognitive_scaffold={
                "fiveLayerPlan": {
                    "L1_environment_layer": {"roleStates": incoming_states},
                    "L2_relational_structure_layer": {"designContent": []},
                }
            },
        ),
        created_at=now,
        updated_at=now,
    )
    service = WallpaperGenerationService()

    try:
        tasks = service.create_tasks(
            result,
            speaker_role="child",
            generation_stage="subsequent_update",
            role_reference_images={"child": "/child.png", "elder": "/elder.png"},
            event_seq=2,
        )
        with SessionLocal() as session:
            plan = (
                session.query(VoiceGenerationPlan)
                .filter(VoiceGenerationPlan.plan_id == tasks.plan_id)
                .one()
            )
            state = plan.designer_five_layer_plan["roleVisualState"]
            assert state["updatePolicy"] == "update_current_speaker_only"
            assert state["activeRole"] == "child"
            assert state["elder"]["timeRaw"] == "白天"
            assert state["elder"]["sceneRaw"] == "家里"
            assert state["child"]["timeRaw"] == "晚上"
            assert state["child"]["sceneRaw"] == "单位"

        bundle = service.get_task(tasks.primary_task_id)
        bundled_plan = bundle.semantic_mapping.cognitive_scaffold["fiveLayerPlan"]
        assert bundled_plan["roleVisualState"] == state
        assert bundled_plan["L1_environment_layer"]["roleStates"]["elder"]["sceneRaw"] == "家里"
    finally:
        with SessionLocal() as session:
            session.query(WallpaperRenderTask).filter(
                WallpaperRenderTask.relationship_id == relationship_id
            ).delete(synchronize_session=False)
            session.query(VoiceGenerationPlan).filter(
                VoiceGenerationPlan.relationship_id == relationship_id
            ).delete(synchronize_session=False)
            session.query(WallpaperRevision).filter(
                WallpaperRevision.relationship_id == relationship_id
            ).delete(synchronize_session=False)
            session.commit()


@pytest.mark.asyncio
async def test_shared_wallpaper_event_is_single_relationship_notification() -> None:
    relationship_id = f"events-{uuid.uuid4().hex}"
    child_queue = wallpaper_event_hub.subscribe(relationship_id)
    elder_queue = wallpaper_event_hub.subscribe(relationship_id)

    try:
        await wallpaper_event_hub.publish(
            relationship_id,
            status="wallpaper_revision_ready",
            version=4,
            image_url="/generated/shared-v4.png",
        )
        child_event = child_queue.get_nowait()
        elder_event = elder_queue.get_nowait()
    finally:
        wallpaper_event_hub.unsubscribe(relationship_id, child_queue)
        wallpaper_event_hub.unsubscribe(relationship_id, elder_queue)

    assert child_event == elder_event
    assert child_event == {
        "type": "wallpaper_state_changed",
        "relationshipId": relationship_id,
        "status": "wallpaper_revision_ready",
        "version": 4,
        "imageUrl": "/generated/shared-v4.png",
    }
    assert child_queue.empty()
    assert elder_queue.empty()


@pytest.mark.asyncio
async def test_relationship_queue_serializes_concurrent_events(monkeypatch) -> None:
    child_client, elder_client, profile = _create_connected_pair()
    relationship_id = profile["relationshipId"]
    child_client.close()
    elder_client.close()

    assert (
        agent_runs.relationship_wallpaper_service.claim_first_voice(
            relationship_id
        ).action
        == "generate"
    )
    agent_runs.relationship_wallpaper_service.save_shared_revision(
        relationship_id,
        image_url="/generated/queue-v1.png",
        run_id="queue-initial",
        event_seq=1,
    )

    def analyzed_result(run_id: str) -> AgentRunResult:
        now = datetime.utcnow()
        return AgentRunResult(
            run_id=run_id,
            status="analyzed",
            user_id=profile["userId"],
            relationship_id=relationship_id,
            steps=[],
            semantic_mapping=SemanticMappingResult(
                semantic_visual_instruction=f"shared update {run_id}",
            ),
            created_at=now,
            updated_at=now,
        )

    first = agent_runs.wallpaper_generation_service.create_tasks(
        analyzed_result("queue-run-1"),
        speaker_role="child",
        generation_stage="subsequent_update",
        role_reference_images={"child": "/child.png", "elder": "/elder.png"},
    )
    second = agent_runs.wallpaper_generation_service.create_tasks(
        analyzed_result("queue-run-2"),
        speaker_role="elder",
        generation_stage="subsequent_update",
        role_reference_images={"child": "/child.png", "elder": "/elder.png"},
    )

    active = 0
    max_active = 0
    calls: list[tuple[str, str]] = []

    async def fake_generate(*args, **kwargs) -> ImageGenerationResult:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        calls.append((kwargs["run_id"], kwargs["previous_image_url"]))
        await asyncio.sleep(0.01)
        active -= 1
        return ImageGenerationResult(
            wallpaper_url=f"/generated/{kwargs['run_id']}.png",
            asset_metadata={"prompt": kwargs["run_id"]},
        )

    monkeypatch.setattr(
        agent_runs.wallpaper_render_worker.image_agent,
        "run",
        fake_generate,
    )

    original_publish = wallpaper_event_hub.publish
    published_versions: list[int] = []

    async def assert_synced_before_publish(
        published_relationship_id: str,
        **event,
    ) -> None:
        version = int(event["version"])
        image_url = str(event["image_url"])
        with SessionLocal() as session:
            tasks = (
                session.query(WallpaperRenderTask)
                .filter(
                    WallpaperRenderTask.relationship_id
                    == published_relationship_id,
                    WallpaperRenderTask.event_seq == version,
                )
                .all()
            )
            state = (
                session.query(RelationshipWallpaperState)
                .filter(
                    RelationshipWallpaperState.relationship_id
                    == published_relationship_id
                )
                .one()
            )
            assert len(tasks) == 2
            assert {task.status for task in tasks} == {"completed"}
            assert {task.output_image_url for task in tasks} == {image_url}
            assert state.child_view_url == image_url
            assert state.elder_view_url == image_url
        published_versions.append(version)
        await original_publish(published_relationship_id, **event)

    monkeypatch.setattr(
        wallpaper_event_hub,
        "publish",
        assert_synced_before_publish,
    )

    child_events = wallpaper_event_hub.subscribe(relationship_id)
    elder_events = wallpaper_event_hub.subscribe(relationship_id)

    async def render_concurrently() -> None:
        await asyncio.gather(
            agent_runs.wallpaper_render_worker.render_through(
                second.primary_task_id
            ),
            agent_runs.wallpaper_render_worker.render_through(
                first.primary_task_id
            ),
        )

    try:
        await render_concurrently()
    finally:
        wallpaper_event_hub.unsubscribe(relationship_id, child_events)
        wallpaper_event_hub.unsubscribe(relationship_id, elder_events)

    assert max_active == 1
    assert published_versions == [2, 3]
    assert calls == [
        ("queue-run-1", "/generated/queue-v1.png"),
        ("queue-run-2", "/generated/queue-run-1.png"),
    ]
    assert child_events.qsize() == 2
    assert elder_events.qsize() == 2
    assert [child_events.get_nowait()["version"] for _ in range(2)] == [2, 3]
    assert [elder_events.get_nowait()["version"] for _ in range(2)] == [2, 3]

    state = agent_runs.relationship_wallpaper_service.get(relationship_id)
    assert state is not None
    assert state.child_view_url == "/generated/queue-run-2.png"
    assert state.elder_view_url == "/generated/queue-run-2.png"
