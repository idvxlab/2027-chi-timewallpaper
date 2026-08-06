from __future__ import annotations

from scripts.test_visual_mapping_ab import build_case


def test_visual_ab_fixture_separates_environment_and_relationship_extremes() -> None:
    near_plan, near_controls, near_prompt = build_case(
        "near",
        elder_time="白天",
        elder_scene="超市",
        child_time="晚上",
        child_scene="单位",
    )
    far_plan, far_controls, far_prompt = build_case(
        "far",
        elder_time="白天",
        elder_scene="超市",
        child_time="晚上",
        child_scene="单位",
    )

    elder = near_plan["roleVisualState"]["elder"]
    child = near_plan["roleVisualState"]["child"]
    assert elder["sceneType"] == "supermarket"
    assert elder["lighting"]["brightness"] == 0.9
    assert child["sceneType"] == "workplace"
    assert child["lighting"]["brightness"] == 0.3
    assert child["lighting"]["lampState"] == "off"

    assert near_controls["relationshipScore"] > far_controls["relationshipScore"]
    assert near_controls["controls"]["flowerDensity"] > far_controls["controls"]["flowerDensity"]
    assert near_controls["controls"]["bloomRatio"] > far_controls["controls"]["bloomRatio"]
    assert near_controls["controls"]["zoneGapRatio"] < far_controls["controls"]["zoneGapRatio"]
    assert near_controls["controls"]["sharedSpaceRatio"] > far_controls["controls"]["sharedSpaceRatio"]
    near_layout = near_controls["layoutState"]
    far_layout = far_controls["layoutState"]
    assert near_layout["elderAnchor"][0] > far_layout["elderAnchor"][0]
    assert near_layout["elderAnchor"][1] < far_layout["elderAnchor"][1]
    assert near_layout["childAnchor"][0] < far_layout["childAnchor"][0]
    assert near_layout["childAnchor"][1] > far_layout["childAnchor"][1]
    assert near_layout["childAnchor"][0] <= 0.6
    assert far_layout["childAnchor"][0] <= 0.76
    assert near_layout["personGapRatio"] < far_layout["personGapRatio"]
    assert near_layout["sharedSceneRatio"] > far_layout["sharedSceneRatio"]
    assert near_layout["environmentMergeRatio"] > far_layout["environmentMergeRatio"]
    assert near_plan["L2_relational_structure_layer"]["deterministicControls"]["layoutState"] == near_layout

    for prompt in (near_prompt, far_prompt):
        assert "[Overall Scene]" not in prompt
        assert "[Elder Environment]" in prompt
        assert "[Child Environment]" in prompt
        assert "[Shared Transition]" in prompt
        assert "[Relationship Growth]" in prompt
        assert "at least 8% of canvas width" in prompt
