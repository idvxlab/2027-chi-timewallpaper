from __future__ import annotations

import pytest

from app.agents.image_generation_agent import ImageGenerationAgent
from app.core.config import settings
from app.schemas.agent import SemanticMappingResult


def _merged_plan() -> dict:
    layout = {
        "spatialMode": "merged",
        "elderAnchor": [0.375, 0.642],
        "childAnchor": [0.62, 0.393],
        "elderPlatformAnchor": [0.405, 0.6],
        "childPlatformAnchor": [0.595, 0.44],
        "personGapRatio": 0.228,
        "platformGapRatio": 0.0,
        "platformOverlapRatio": 0.165,
        "sharedGroundRatio": 0.688,
        "sharedSceneRatio": 0.65,
        "environmentMergeRatio": 0.675,
        "centralFeature": "shared_ground",
    }
    return {
        "L2_relational_structure_layer": {
            "layoutState": layout,
            "deterministicControls": {
                "spatialMode": "merged",
                "layoutState": layout,
            },
        }
    }


def test_merged_layout_uses_one_dynamic_shared_region_for_all_stages() -> None:
    agent = ImageGenerationAgent()

    for stage in (None, "first_voice", "subsequent_update"):
        regions = agent._plan_regions(
            "关系近，双方进入同一个空间",
            five_layer_plan=_merged_plan(),
            generation_stage=stage,
            speaker_role="child",
        )

        assert len(regions) == 1
        region = regions[0]
        assert region["region_id"] == "shared_relationship_space"
        assert region["bbox"] == [0.176, 0.181, 0.819, 0.855]
        assert region["role_anchors"] == {
            "elder": [0.375, 0.642],
            "child": [0.62, 0.393],
        }
        assert region["person_gap_ratio"] == 0.228
        assert region["platform_anchors"] == {
            "elder": [0.405, 0.6],
            "child": [0.595, 0.44],
        }
        assert region["platform_gap_ratio"] == 0.0
        assert region["shared_ground_ratio"] == 0.688
        assert region["shared_scene_ratio"] == 0.65
        assert region["environment_merge_ratio"] == 0.675
        assert "两块完整生活平台" in region["reason"]


def test_non_merged_generation_stages_keep_existing_region_behavior() -> None:
    agent = ImageGenerationAgent()
    connected_plan = {
        "L2_relational_structure_layer": {
            "layoutState": {"spatialMode": "connected"}
        }
    }

    assert agent._plan_regions(
        "",
        five_layer_plan=connected_plan,
        generation_stage="first_voice",
        speaker_role="child",
    ) == [{"region_id": "upper_right"}, {"region_id": "lower_left"}]
    assert agent._plan_regions(
        "",
        five_layer_plan=connected_plan,
        generation_stage="subsequent_update",
        speaker_role="elder",
    ) == [{"region_id": "left_bottom"}]


def test_all_five_spatial_modes_select_joint_reflow() -> None:
    agent = ImageGenerationAgent()
    refs = {"elder": "/elder.png", "child": "/child.png"}

    for mode in agent.RELATIONSHIP_REFLOW_MODES:
        plan = {
            "L2_relational_structure_layer": {
                "layoutState": {"spatialMode": mode}
            }
        }
        assert agent._select_layered_tool(
            "/generated/current.png",
            refs,
            generation_stage="subsequent_update",
            speaker_role="child",
            five_layer_plan=plan,
        ) == "reflow_shared_relationship_view"


def test_layout_state_can_be_read_from_nested_deterministic_controls() -> None:
    agent = ImageGenerationAgent()
    layout = _merged_plan()["L2_relational_structure_layer"]["layoutState"]
    plan = {
        "L2_relational_structure_layer": {
            "deterministicControls": {"layoutState": layout}
        }
    }

    regions = agent._plan_regions("", five_layer_plan=plan)
    assert regions[0]["region_id"] == "shared_relationship_space"


@pytest.mark.asyncio
async def test_merged_update_selects_two_identity_reflow_tool(monkeypatch) -> None:
    from app.agents import image_generation_agent as image_agent_module

    monkeypatch.setattr(settings, "layered_image_tools_enabled", True)

    async def fake_resolve_reference_bytes(url: str) -> bytes:
        return url.encode()

    async def fake_reflow(**kwargs) -> dict:
        assert kwargs["younger_image_bytes"] == b"/child.png"
        assert kwargs["elder_image_bytes"] == b"/elder.png"
        assert kwargs["designer_five_layer_plan"] == _merged_plan()
        return {
            "imageUrl": "/generated/reflow.png",
            "raw": {
                "status": "completed",
                "generation_stage": "relationship_reflow",
                "final_prompt": "two identity reflow prompt",
            },
        }

    monkeypatch.setattr(
        image_agent_module.layered_painter_tools,
        "resolve_reference_bytes",
        fake_resolve_reference_bytes,
    )
    monkeypatch.setattr(
        image_agent_module.layered_painter_tools,
        "reflow_shared_relationship_view",
        fake_reflow,
    )

    result = await ImageGenerationAgent().run(
        SemanticMappingResult(
            semantic_visual_instruction="关系近，进入共同空间",
            cognitive_scaffold={"fiveLayerPlan": _merged_plan()},
        ),
        previous_image_url="/generated/latest.png",
        role_reference_images={
            "child": "/child.png",
            "elder": "/elder.png",
        },
        generation_stage="subsequent_update",
        speaker_role="child",
    )

    assert result.wallpaper_url == "/generated/reflow.png"
    assert result.generation_mode == "seedream_single_pass:relationship_reflow"
    assert result.changed_regions == ["shared_relationship_space"]
    assert result.asset_metadata["layered_tool"] == "reflow_shared_relationship_view"
    assert result.asset_metadata["quality"]["mode"] == (
        "seedream_prompt_v2_relationship_reflow"
    )
