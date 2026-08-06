from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from app.agents.image_generation_agent import ImageGenerationAgent
from app.core.config import settings
from app.providers.image.base import ImageProvider
from app.providers.image.errors import ImageUpstreamError
from app.schemas.agent import SemanticMappingResult
from app.services import seedream_wallpaper_service as service


def _png_bytes(color: tuple[int, int, int]) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (40, 60), color).save(output, format="PNG")
    return output.getvalue()


def _five_layer_plan() -> dict:
    return {
        "L1_environment_layer": {"designContent": ["黄昏暖光花园"]},
        "L2_relational_structure_layer": {
            "designContent": ["两侧独立，中间石路连接"]
        },
        "L3_object_event_layer": {"designContent": ["说话者正在包饺子"]},
        "L4_character_layer": {"designContent": ["自然坐姿，不看镜头"]},
        "L5_motion_feedback_layer": {"designContent": ["局部微光反馈"]},
    }


class FakeSeedreamProvider(ImageProvider):
    provider_name = "fake_seedream"

    def __init__(
        self,
        outputs: list[Image.Image | Exception],
    ) -> None:
        self.outputs = list(outputs)
        self.calls: list[dict] = []

    @property
    def model_name(self) -> str:
        return "fake-seedream-model"

    async def generate(
        self,
        prompt: str,
        *,
        size: str | None = None,
    ) -> Image.Image:
        raise AssertionError("wallpaper flow must use edit")

    async def edit(
        self,
        base_image: Image.Image,
        prompt: str,
        *,
        identity_images: list[Image.Image] | None = None,
        mask: Image.Image | None = None,
        size: str | None = None,
    ) -> Image.Image:
        self.calls.append(
            {
                "base_pixel": base_image.getpixel((0, 0)),
                "prompt": prompt,
                "identity_pixel": (identity_images or [])[0].getpixel((0, 0)),
                "identity_pixels": [
                    image.getpixel((0, 0)) for image in (identity_images or [])
                ],
                "mask": mask,
                "size": size,
            }
        )
        output = self.outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        return output


def _prepare_base(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(service, "GENERATED_DIR", tmp_path)
    Image.new("RGB", (1024, 1536), (10, 10, 10)).save(
        tmp_path / "base.png",
        format="PNG",
    )


@pytest.mark.asyncio
async def test_first_voice_uses_one_call_with_base_elder_child_order(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _prepare_base(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "public_api_base_url", "")
    monkeypatch.setattr(settings, "volcengine_image_pass2_retries", 1)
    provider = FakeSeedreamProvider(
        [Image.new("RGB", (1024, 1536), (30, 30, 30))]
    )

    result = await service.generate_initial_shared_wallpaper(
        base_image_url="/generated/base.png",
        child_identity_bytes=_png_bytes((101, 0, 0)),
        elder_identity_bytes=_png_bytes((202, 0, 0)),
        five_layer_plan=_five_layer_plan(),
        semantic_visual_instruction="完整五层存在时不重复使用。",
        speaker_role="child",
        provider=provider,
    )

    assert result["raw"]["status"] == "completed"
    assert result["raw"]["completed_passes"] == 1
    assert result["raw"]["mask_used"] is False
    assert result["raw"]["local_composite_used"] is False
    assert Path(result["raw"]["final_path"]).is_file()
    assert len(provider.calls) == 1
    assert provider.calls[0]["base_pixel"] == (10, 10, 10)
    assert provider.calls[0]["identity_pixels"] == [
        (202, 0, 0),
        (101, 0, 0),
    ]
    assert all(call["mask"] is None for call in provider.calls)
    assert "Image B is the older adult identity reference" in provider.calls[0]["prompt"]
    assert "Image C is the younger adult identity reference" in provider.calls[0]["prompt"]
    assert "说话者正在包饺子" in provider.calls[0]["prompt"]
    assert "gently waters potted flowers" in provider.calls[0]["prompt"]
    assert result["raw"]["input_order"] == [
        "base",
        "elder_identity",
        "child_identity",
    ]


@pytest.mark.asyncio
async def test_first_voice_retries_same_v2_prompt_and_base(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _prepare_base(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "public_api_base_url", "")
    monkeypatch.setattr(settings, "volcengine_image_pass2_retries", 1)
    provider = FakeSeedreamProvider(
        [
            ImageUpstreamError("temporary upstream failure"),
            Image.new("RGB", (1024, 1536), (30, 30, 30)),
        ]
    )

    result = await service.generate_initial_shared_wallpaper(
        base_image_url="/generated/base.png",
        child_identity_bytes=_png_bytes((101, 0, 0)),
        elder_identity_bytes=_png_bytes((202, 0, 0)),
        five_layer_plan=_five_layer_plan(),
        semantic_visual_instruction="",
        speaker_role="elder",
        provider=provider,
    )

    assert result["raw"]["status"] == "completed"
    assert result["raw"]["retry_count"] == 1
    assert len(provider.calls) == 2
    assert provider.calls[0]["base_pixel"] == (10, 10, 10)
    assert provider.calls[1]["base_pixel"] == (10, 10, 10)
    assert provider.calls[0]["prompt"] == provider.calls[1]["prompt"]
    assert "quietly reads an open book" in provider.calls[0]["prompt"]
    assert "说话者正在包饺子" in provider.calls[0]["prompt"]


@pytest.mark.asyncio
async def test_first_voice_failure_has_no_partial_one_person_wallpaper(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _prepare_base(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "public_api_base_url", "")
    monkeypatch.setattr(settings, "volcengine_image_pass2_retries", 1)
    provider = FakeSeedreamProvider(
        [
            ImageUpstreamError("first failure"),
            ImageUpstreamError("second failure"),
        ]
    )

    result = await service.generate_initial_shared_wallpaper(
        base_image_url="/generated/base.png",
        child_identity_bytes=_png_bytes((101, 0, 0)),
        elder_identity_bytes=_png_bytes((202, 0, 0)),
        five_layer_plan=_five_layer_plan(),
        semantic_visual_instruction="",
        speaker_role="child",
        provider=provider,
    )

    assert result["imageUrl"] == ""
    assert result["raw"]["status"] == "failed"
    assert result["raw"]["failed_pass"] == 1
    assert result["raw"]["errorCode"] == "INITIAL_SHARED_WALLPAPER_FAILED"
    assert result["raw"]["retry_count"] == 1
    assert result["raw"]["retryable"] is True


@pytest.mark.asyncio
async def test_image_agent_first_voice_bypasses_legacy_prompt_builder(
    monkeypatch,
) -> None:
    from app.agents import image_generation_agent as image_agent_module

    monkeypatch.setattr(settings, "layered_image_tools_enabled", True)

    async def fake_resolve_reference_bytes(url: str) -> bytes:
        return _png_bytes((1, 2, 3))

    async def fake_initialize(**kwargs) -> dict:
        assert kwargs["designer_five_layer_plan"] == _five_layer_plan()
        assert kwargs["semantic_visual_instruction"] == "designer summary"
        return {
            "imageUrl": "/generated/new-shared.png",
            "raw": {
                "status": "completed",
                "prompt": "compiled V2 prompt",
                "final_prompt": "compiled V2 prompt",
            },
        }

    monkeypatch.setattr(
        image_agent_module.layered_painter_tools,
        "resolve_reference_bytes",
        fake_resolve_reference_bytes,
    )
    monkeypatch.setattr(
        image_agent_module.layered_painter_tools,
        "initialize_wallpaper_view",
        fake_initialize,
    )

    agent = ImageGenerationAgent()

    def fail_legacy_prompt(*args, **kwargs):
        raise AssertionError("legacy prompt builder must not run for first_voice")

    monkeypatch.setattr(agent, "_build_prompt", fail_legacy_prompt)
    result = await agent.run(
        SemanticMappingResult(
            semantic_visual_instruction="designer summary",
            cognitive_scaffold={"fiveLayerPlan": _five_layer_plan()},
        ),
        previous_image_url="/generated/base.png",
        role_reference_images={
            "child": "/character-assets/child.png",
            "elder": "/character-assets/elder.png",
        },
        generation_stage="first_voice",
        speaker_role="child",
    )

    assert result.wallpaper_url == "/generated/new-shared.png"
    assert result.generation_mode == "seedream_single_pass:first_voice"
    assert result.changed_regions == ["upper_right", "lower_left"]
    assert result.asset_metadata["masks"] == []
    assert result.asset_metadata["prompt"] == "compiled V2 prompt"


@pytest.mark.asyncio
async def test_single_pass_update_edits_speaker_region_without_mask(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _prepare_base(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "public_api_base_url", "")
    monkeypatch.setattr(settings, "volcengine_image_update_retries", 1)
    provider = FakeSeedreamProvider(
        [Image.new("RGB", (1024, 1536), (40, 40, 40))]
    )

    result = await service.update_shared_wallpaper(
        base_image_url="/generated/base.png",
        speaker_identity_bytes=_png_bytes((101, 0, 0)),
        five_layer_plan=_five_layer_plan(),
        semantic_visual_instruction="",
        speaker_role="child",
        provider=provider,
    )

    assert result["raw"]["status"] == "completed"
    assert result["raw"]["speaker_role"] == "child"
    assert result["raw"]["editing_region"] == "upper-right"
    assert result["raw"]["preserve_role"] == "elder"
    assert result["raw"]["preserve_region"] == "lower-left"
    assert result["raw"]["mask_used"] is False
    assert result["raw"]["local_composite_used"] is False
    assert result["raw"]["identity_reference_used"] is True
    assert result["imageUrl"].startswith("/generated/seedream-update-child-")
    assert len(provider.calls) == 1
    assert provider.calls[0]["base_pixel"] == (10, 10, 10)
    assert provider.calls[0]["identity_pixel"] == (101, 0, 0)
    assert provider.calls[0]["mask"] is None
    assert "upper-right personal zone" in provider.calls[0]["prompt"]
    assert "Keep the existing older adult parent recognizable" in (
        provider.calls[0]["prompt"]
    )
    assert "说话者正在包饺子" in (
        provider.calls[0]["prompt"]
    )


@pytest.mark.asyncio
async def test_relationship_reflow_uses_both_identity_references(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _prepare_base(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "public_api_base_url", "")
    monkeypatch.setattr(settings, "volcengine_image_update_retries", 1)
    provider = FakeSeedreamProvider(
        [Image.new("RGB", (1024, 1536), (50, 50, 50))]
    )
    plan = _five_layer_plan()
    layout = {
        "spatialMode": "merged",
        "elderAnchor": [0.375, 0.642],
        "childAnchor": [0.62, 0.393],
        "personGapRatio": 0.228,
        "sharedSceneRatio": 0.65,
        "environmentMergeRatio": 0.675,
    }
    plan["L2_relational_structure_layer"]["layoutState"] = layout
    plan["L2_relational_structure_layer"]["deterministicControls"] = {
        "spatialMode": "merged",
        "layoutState": layout,
    }

    result = await service.reflow_shared_relationship_view(
        base_image_url="/generated/base.png",
        child_identity_bytes=_png_bytes((101, 0, 0)),
        elder_identity_bytes=_png_bytes((202, 0, 0)),
        five_layer_plan=plan,
        semantic_visual_instruction="",
        speaker_role="child",
        provider=provider,
    )

    assert result["raw"]["status"] == "completed"
    assert result["raw"]["generation_stage"] == "relationship_reflow"
    assert result["raw"]["identity_references_used"] == 2
    assert result["raw"]["layout"] == layout
    assert result["imageUrl"].startswith("/generated/seedream-reflow-")
    assert provider.calls[0]["identity_pixels"] == [
        (202, 0, 0),
        (101, 0, 0),
    ]
    assert "[Relationship Reflow]" in provider.calls[0]["prompt"]
    assert "older adult toward normalized anchor [0.375, 0.642]" in (
        provider.calls[0]["prompt"]
    )
    assert provider.calls[0]["mask"] is None


@pytest.mark.asyncio
async def test_single_pass_update_retries_with_same_base_then_fails_cleanly(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _prepare_base(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "public_api_base_url", "")
    monkeypatch.setattr(settings, "volcengine_image_update_retries", 1)
    provider = FakeSeedreamProvider(
        [
            ImageUpstreamError("first failure"),
            ImageUpstreamError("second failure"),
        ]
    )

    result = await service.update_shared_wallpaper(
        base_image_url="/generated/base.png",
        speaker_identity_bytes=_png_bytes((202, 0, 0)),
        five_layer_plan=_five_layer_plan(),
        semantic_visual_instruction="",
        speaker_role="elder",
        provider=provider,
    )

    assert result["imageUrl"] == ""
    assert result["raw"]["status"] == "failed"
    assert result["raw"]["errorCode"] == "SPEAKER_REGION_UPDATE_FAILED"
    assert result["raw"]["editing_region"] == "lower-left"
    assert result["raw"]["preserve_role"] == "child"
    assert result["raw"]["retry_count"] == 1
    assert len(provider.calls) == 2
    assert provider.calls[0]["base_pixel"] == (10, 10, 10)
    assert provider.calls[1]["base_pixel"] == (10, 10, 10)
    assert all(call["mask"] is None for call in provider.calls)
