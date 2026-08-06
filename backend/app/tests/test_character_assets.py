from datetime import datetime
from io import BytesIO
from types import SimpleNamespace

from fastapi.testclient import TestClient
from PIL import Image
import pytest

from app.agents.image_generation_agent import ImageGenerationAgent
from app.core.character_asset_events import CharacterAssetEventHub
from app.core.config import settings
from app.main import app
from app.providers.image.errors import ImageTimeoutError
from app.routes import character_assets
from app.schemas.agent import SemanticMappingResult
from app.services.character_asset_service import (
    CHARACTER_STYLE_VERSION,
    CharacterAssetService,
)
from app.services.layered_painter_tools import layered_painter_tools


def _png_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (32, 48), (235, 220, 196)).save(buffer, format="PNG")
    return buffer.getvalue()


def test_character_prompt_matches_base_picturebook_style() -> None:
    prompt = CharacterAssetService._build_prompt("elder")

    assert "儿童绘本" in prompt
    assert "手绘墨线水彩" in prompt
    assert "大块透明水彩" in prompt
    assert "不要碎线" in prompt
    assert "保留第一张照片人物的可辨识身份特征" in prompt
    assert "圆润的儿童绘本卡通结构" in prompt
    assert "小而简洁的鼻子" in prompt
    assert "禁止逐根真实发丝" in prompt
    assert "不能画成写实肖像" in prompt
    assert "不生成房间" in prompt
    assert CHARACTER_STYLE_VERSION == "classic-picturebook-rounded-cartoon-v2"


@pytest.mark.asyncio
async def test_character_generation_uses_seedream_with_small_portrait_size(
    monkeypatch,
) -> None:
    captured: dict = {}

    class FakeSeedreamProvider:
        def __init__(self, **kwargs):
            captured["provider"] = kwargs

        async def edit(self, base_image, prompt, **kwargs):
            captured["baseSize"] = base_image.size
            captured["prompt"] = prompt
            captured["edit"] = kwargs
            return Image.new("RGB", (768, 1024), (210, 200, 180))

    from app.services import character_asset_service as service_module

    monkeypatch.setattr(
        service_module,
        "VolcengineSeedreamProvider",
        FakeSeedreamProvider,
    )
    monkeypatch.setattr(settings, "character_image_size", "768x1024")
    monkeypatch.setattr(settings, "character_image_timeout_seconds", 123)

    source = Image.new("RGB", (48, 64), (1, 2, 3))
    style = Image.new("RGB", (24, 32), (4, 5, 6))
    result = await CharacterAssetService()._generate_master(
        prompt="人物风格迁移",
        source_image=source,
        style_image=style,
    )

    assert result.size == (768, 1024)
    assert captured["provider"] == {
        "logical_size": "768x1024",
        "timeout_seconds": 123,
    }
    assert captured["edit"]["size"] == "768x1024"
    assert captured["edit"]["identity_images"] == [style]


@pytest.mark.asyncio
async def test_character_generation_retries_retryable_seedream_error(
    monkeypatch,
) -> None:
    calls = 0

    class FlakySeedreamProvider:
        def __init__(self, **kwargs):
            pass

        async def edit(self, base_image, prompt, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise ImageTimeoutError("temporary timeout")
            return Image.new("RGB", (768, 1024))

    async def no_sleep(_seconds):
        return None

    from app.services import character_asset_service as service_module

    monkeypatch.setattr(
        service_module,
        "VolcengineSeedreamProvider",
        FlakySeedreamProvider,
    )
    monkeypatch.setattr(service_module.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(settings, "character_image_retries", 1)

    result = await CharacterAssetService()._generate_master(
        prompt="人物风格迁移",
        source_image=Image.new("RGB", (48, 64)),
        style_image=None,
    )

    assert result.size == (768, 1024)
    assert calls == 2


def test_character_asset_upload_contract(monkeypatch) -> None:
    now = datetime.utcnow()

    async def fake_create(**kwargs):
        assert kwargs["user_id"] == "child-demo"
        assert kwargs["role"] == "child"
        assert kwargs["relationship_id"] == "memory-demo"
        assert kwargs["source_bytes"]
        assert kwargs["style_reference_bytes"]
        return SimpleNamespace(
            asset_id="char-test",
            user_id="child-demo",
            relationship_id="memory-demo",
            role="child",
            status="ready",
            style_version="classic-picturebook-line-watercolor-v1",
            source_image_url="/character-assets/files/char-test/source.png",
            style_reference_url="/character-assets/files/char-test/style-reference.png",
            master_image_url="/character-assets/files/char-test/master.png",
            portrait_image_url="/character-assets/files/char-test/portrait.png",
            half_body_image_url="/character-assets/files/char-test/half-body.png",
            full_body_image_url="/character-assets/files/char-test/master.png",
            is_active=True,
            created_at=now,
            updated_at=now,
        )

    monkeypatch.setattr(character_assets.character_asset_service, "create", fake_create)

    with TestClient(app) as client:
        response = client.post(
            "/character-assets",
            data={"userId": "child-demo", "relationshipId": "memory-demo", "role": "child"},
            files={
                "image": ("child.png", _png_bytes(), "image/png"),
                "styleReference": ("style.png", _png_bytes(), "image/png"),
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["assetId"] == "char-test"
    assert payload["masterImageUrl"].endswith("/master.png")
    assert payload["role"] == "child"


def test_current_user_upload_uses_session_identity(monkeypatch) -> None:
    now = datetime.utcnow()
    with TestClient(app) as child_client, TestClient(app) as elder_client:
        profile_response = child_client.post(
            "/onboarding/profile",
            json={
                "viewerRole": "child",
                "displayName": "Yang",
                "gender": "female",
            },
        )
        profile = profile_response.json()
        join_response = elder_client.post(
            "/relationships/join",
            json={
                "inviteCode": profile["inviteCode"],
                "viewerRole": "elder",
                "displayName": "Wang",
                "gender": "female",
            },
        )
        assert join_response.status_code == 200

        async def fake_create(**kwargs):
            assert kwargs["user_id"] == profile["userId"]
            assert kwargs["role"] == "child"
            assert kwargs["relationship_id"] == profile["relationshipId"]
            return SimpleNamespace(
                asset_id="char-session-child",
                user_id=kwargs["user_id"],
                relationship_id=kwargs["relationship_id"],
                role=kwargs["role"],
                status="ready",
                style_version="classic-picturebook-line-watercolor-v1",
                source_image_url="/character-assets/files/char-session-child/source.png",
                style_reference_url="",
                master_image_url="/character-assets/files/char-session-child/master.png",
                portrait_image_url="/character-assets/files/char-session-child/portrait.png",
                half_body_image_url="/character-assets/files/char-session-child/half-body.png",
                full_body_image_url="/character-assets/files/char-session-child/master.png",
                is_active=True,
                created_at=now,
                updated_at=now,
            )

        monkeypatch.setattr(character_assets.character_asset_service, "create", fake_create)
        response = child_client.post(
            "/character-assets/me",
            files={"image": ("child.png", _png_bytes(), "image/png")},
        )

    assert response.status_code == 200
    assert response.json()["userId"] == profile["userId"]
    assert response.json()["role"] == "child"


def test_current_relationship_assets_returns_fixed_role_slots(monkeypatch) -> None:
    now = datetime.utcnow()
    with TestClient(app) as child_client, TestClient(app) as elder_client:
        profile = child_client.post(
            "/onboarding/profile",
            json={
                "viewerRole": "child",
                "displayName": "Yang",
                "gender": "female",
            },
        ).json()
        elder = elder_client.post(
            "/relationships/join",
            json={
                "inviteCode": profile["inviteCode"],
                "viewerRole": "elder",
                "displayName": "Wang",
                "gender": "female",
            },
        ).json()

        def asset(user_id: str, role: str) -> SimpleNamespace:
            return SimpleNamespace(
                asset_id=f"char-{role}",
                user_id=user_id,
                relationship_id=profile["relationshipId"],
                role=role,
                status="ready",
                style_version="classic-picturebook-line-watercolor-v1",
                source_image_url=f"/character-assets/files/char-{role}/source.png",
                style_reference_url="",
                master_image_url=f"/character-assets/files/char-{role}/master.png",
                portrait_image_url="",
                half_body_image_url="",
                full_body_image_url=f"/character-assets/files/char-{role}/master.png",
                is_active=True,
                created_at=now,
                updated_at=now,
            )

        monkeypatch.setattr(
            character_assets.character_asset_service,
            "list_latest_for_relationship",
            lambda relationship_id: [
                asset(elder["userId"], "elder"),
                asset(profile["userId"], "child"),
            ],
        )
        response = child_client.get("/character-assets/current-relationship")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ready"] is True
    assert payload["elder"]["userId"] == elder["userId"]
    assert payload["child"]["userId"] == profile["userId"]


def test_current_character_assets_requires_session() -> None:
    with TestClient(app) as client:
        response = client.get("/character-assets/current-relationship")

    assert response.status_code == 401


def test_current_character_asset_events_use_session_relationship() -> None:
    with TestClient(app) as client:
        profile = client.post(
            "/onboarding/profile",
            json={
                "viewerRole": "child",
                "displayName": "Socket Child",
                "gender": "female",
            },
        ).json()

        with client.websocket_connect(
            "/character-assets/current-relationship/events"
        ) as websocket:
            event = websocket.receive_json()

    assert event["type"] == "character_asset_changed"
    assert event["relationshipId"] == profile["relationshipId"]
    assert event["status"] == "connected"


@pytest.mark.asyncio
async def test_character_asset_events_are_relationship_scoped() -> None:
    hub = CharacterAssetEventHub()
    first = hub.subscribe("relationship-one")
    second = hub.subscribe("relationship-two")

    await hub.publish(
        "relationship-one",
        asset_id="char-one",
        role="elder",
        status="ready",
    )

    event = first.get_nowait()
    assert event == {
        "type": "character_asset_changed",
        "relationshipId": "relationship-one",
        "assetId": "char-one",
        "role": "elder",
        "status": "ready",
    }
    assert second.empty()


@pytest.mark.asyncio
async def test_subsequent_update_selects_fixed_side_from_speaker_role(
    monkeypatch,
) -> None:
    calls: list[tuple[str, str]] = []

    async def fake_update_current_side(**kwargs):
        calls.append((kwargs["base_image_url"], kwargs["speaker_role"]))
        assert kwargs["speaker_image_bytes"] == b"speaker-reference"
        assert kwargs["designer_five_layer_plan"]
        return {
            "imageUrl": f"/generated/{kwargs['speaker_role']}.png",
            "raw": {
                "status": "completed",
                "prompt": "compiled update prompt",
            },
        }

    monkeypatch.setattr(settings, "layered_image_tools_enabled", True)
    from app.agents import image_generation_agent as image_agent_module

    monkeypatch.setattr(
        image_agent_module.layered_painter_tools,
        "update_current_side",
        fake_update_current_side,
    )
    async def fake_resolve_reference_bytes(url):
        assert url in {
            "/character-assets/child.png",
            "/character-assets/elder.png",
        }
        return b"speaker-reference"

    monkeypatch.setattr(
        image_agent_module.layered_painter_tools,
        "resolve_reference_bytes",
        fake_resolve_reference_bytes,
    )
    agent = ImageGenerationAgent()
    semantic = SemanticMappingResult(
        semantic_visual_instruction="保持关系路径，只更新说话者当前活动。",
        cognitive_scaffold={
            "fiveLayerPlan": {
                "L1_environment_layer": {"designContent": ["暖光室内"]},
                "L2_relational_structure_layer": {"designContent": ["路径保持"]},
                "L3_object_event_layer": {"designContent": ["说话者散步"]},
                "L4_character_layer": {"designContent": ["自然侧身"]},
                "L5_motion_feedback_layer": {"designContent": ["轻微光点"]},
            }
        },
    )

    child_speaker = await agent.run(
        semantic,
        previous_image_url="/generated/shared-v1.png",
        generation_stage="subsequent_update",
        speaker_role="child",
        role_reference_images={"child": "/character-assets/child.png"},
    )
    elder_speaker = await agent.run(
        semantic,
        previous_image_url="/generated/shared-v2.png",
        generation_stage="subsequent_update",
        speaker_role="elder",
        role_reference_images={"elder": "/character-assets/elder.png"},
    )

    assert calls == [
        ("/generated/shared-v1.png", "child"),
        ("/generated/shared-v2.png", "elder"),
    ]
    assert child_speaker.changed_regions == ["upper_right"]
    assert elder_speaker.changed_regions == ["left_bottom"]
    assert child_speaker.generation_mode == "seedream_single_pass:subsequent_update"
    assert child_speaker.asset_metadata["masks"] == []


def test_layered_painter_resolves_character_asset_static_url() -> None:
    path = layered_painter_tools._local_static_path(
        "/character-assets/files/char-test/master.png"
    )

    assert path is not None
    assert path.as_posix().endswith(
        "data/uploads/character-assets/char-test/master.png"
    )
