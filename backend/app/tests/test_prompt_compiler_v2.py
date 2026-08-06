from __future__ import annotations

from app.services.prompt_compiler_v2 import (
    audit_prompt,
    compile_for_both,
    compile_for_reflow,
    compile_for_update,
)
from app.services.style_profiles import VINTAGE_PAPER_CRAFT_V1_STYLE


def _five_layer_plan() -> dict:
    return {
        "roleVisualState": {
            "elder": {
                "available": True,
                "timeRaw": "白天",
                "sceneRaw": "超市里一个非常冗长但不应该完整进入最终提示词的生活场景描述",
                "lighting": {
                    "brightness": 0.9,
                    "colorTemperature": "neutral_warm_daylight",
                    "skyState": "bright_day",
                    "celestialMarker": "one clearly visible warm sun in the local daytime sky",
                    "lampState": "off",
                },
                "sceneAnchors": ["简化纸雕货架", "购物篮", "连续过道灯"],
            },
            "child": {
                "available": True,
                "timeRaw": "晚上",
                "sceneRaw": "单位",
                "lighting": {
                    "brightness": 0.3,
                    "colorTemperature": "cool_blue_night",
                    "skyState": "night",
                    "celestialMarker": "one clearly visible gentle moon in the local night sky",
                    "lampState": "on",
                },
                "sceneAnchors": ["办公桌", "电脑"],
            },
        },
        "L1_environment_layer": {
            "designContent": ["黄昏暖光花园", "延续水彩纸张纹理"]
        },
        "L2_relational_structure_layer": {
            "designContent": ["两侧独立，中间石路连接"],
            "deterministicControls": {
                "relationshipScore": 0.8,
                "controls": {
                    "flowerDensity": 0.71,
                    "bloomRatio": 0.72,
                    "zoneGapRatio": 0.2,
                    "sharedSpaceRatio": 0.21,
                    "pathLength": 0.4,
                    "pathWidth": 0.09,
                    "connectionBrightness": 0.76,
                },
                "flowerColorPalette": ["soft_peach", "warm_yellow"],
            },
        },
        "L3_object_event_layer": {
            "designContent": ["说话者正在包饺子", "使用面皮和擀面杖"]
        },
        "L4_character_layer": {
            "designContent": ["自然坐姿，视线看向手中的面皮"]
        },
        "L5_motion_feedback_layer": {
            "designContent": ["石路边缘有轻微暖光反馈"]
        },
    }


def test_canonical_style_prompt_is_full_frame_paper_cut() -> None:
    assert VINTAGE_PAPER_CRAFT_V1_STYLE == (
        "The image must look like a full-frame handcrafted layered paper-cut world: "
        "cut-paper characters, layered paper scenery, rounded paper edges, "
        "visible fibrous paper texture, carefully arranged paper shapes and soft "
        "dimensional shadows between layers.\n\n"
        "Use a gentle low-saturation palette of cream paper tones, sage green, "
        "dusty blue, muted floral colors and restrained warm accents.\n\n"
        "Every person, animal, object, plant, architectural element and newly "
        "generated environmental detail must be constructed from the same "
        "layered paper vocabulary.\n\n"
        "The result should feel like an immersive warm handmade storybook environment "
        "that continues beyond all four image edges, not a standard painted landscape "
        "and not a separate craft object photographed on a backing sheet.\n\n"
        "Do not use photorealistic rendering.\n"
        "Do not use realistic skin shading.\n"
        "Do not use watercolor texture as the main style.\n"
        "Do not use clay, plastic, glossy 3D or photographic texture.\n"
        "Do not flatten new characters or objects into stickers."
    )


def test_first_voice_reads_current_five_layers_without_another_mapper() -> None:
    prompt = compile_for_both(
        five_layer_plan=_five_layer_plan(),
        semantic_visual_instruction="不应覆盖完整五层内容",
        speaker_role="child",
    )

    assert "[Overall Scene]" not in prompt
    assert "[Elder Environment]" in prompt
    assert "白天" in prompt.split("[Elder Environment]", 1)[1].split("[Child Environment]", 1)[0]
    assert "[Child Environment]" in prompt
    assert "晚上, 单位" in prompt
    assert "one clearly visible warm sun" in prompt
    assert "one clearly visible gentle moon" in prompt
    assert "visibly dark deep navy-blue" in prompt
    assert "A moon icon alone is not sufficient evidence of night" in prompt
    assert "Do not leave this night region ivory, cream, beige, white or daylight-bright" in prompt
    assert "organic diagonal transition" in prompt
    assert "[Shared Transition]" in prompt
    assert "no vertical day-night divider" in prompt
    assert "[Relationship Growth]" in prompt
    assert "Flower density 0.71" in prompt
    elder_environment = prompt.split("[Elder Environment]", 1)[1].split("[Child Environment]", 1)[0]
    assert len(elder_environment) < 260
    assert "[Central Connection]\n两侧独立，中间石路连接" in prompt
    assert "石路边缘有轻微暖光反馈" in prompt.split("[Central Connection]", 1)[1]
    assert "[Younger Adult]\n说话者正在包饺子" in prompt
    assert "自然坐姿，视线看向手中的面皮" in prompt
    assert "[Older Adult]\nThe older adult parent gently waters" in prompt
    assert "Image B is the older adult identity reference" in prompt
    assert "Image C is the younger adult identity reference" in prompt
    assert "1.1-1.2 times the lower-left zone" in prompt
    assert "comparable and clearly readable visual scales" in prompt
    assert "one continuous layered paper-cut world" in prompt
    assert "x=256..1279" not in prompt
    assert "[Character Body and Platform Framing — highest priority]" in prompt
    assert "[Character Display Safe Zone and Scale — highest priority]" in prompt
    assert "normalized x=0.30-0.70" in prompt
    assert "34%-40% of the full square canvas" in prompt
    assert "neither character may exceed 42%" in prompt
    assert "may naturally hide" in prompt
    assert "below the waist" in prompt
    assert "must never cut through a person's torso or waist" in prompt
    assert "[Full-frame Landscape — highest priority]" in prompt
    assert "never mean a freestanding" in prompt
    assert "No isolated miniature diorama" in prompt
    assert "No blank cream-paper margin" in prompt
    assert "zero outer margin and zero visible backing sheet" in prompt
    assert "No outer picture frame, matte, mount, white rim" in prompt
    assert "Character body anchors are authoritative and higher priority" in prompt
    assert "Reserve normalized horizontal bands x=0.00-0.20 and x=0.80-1.00" in prompt
    assert "[Character Cartoon Form — high priority]" in prompt
    assert "softly rounded face and jaw" in prompt
    assert "small minimally defined nose" in prompt
    assert "Do not draw individual hair strands" in prompt
    assert "No semi-realistic portrait faces" in prompt
    assert audit_prompt(prompt) == []


def test_update_routes_all_current_round_character_content_to_speaker() -> None:
    prompt = compile_for_update(
        five_layer_plan=_five_layer_plan(),
        semantic_visual_instruction="",
        speaker_role="mother",
    )

    assert "Image B is the identity reference for the existing older adult parent" in prompt
    assert "[Elder Environment]" in prompt
    assert "[Child Environment]" in prompt
    assert "[Shared Transition]" in prompt
    assert "[Relationship Growth]" in prompt
    assert "located in the lower-left personal zone" in prompt
    assert "说话者正在包饺子" in prompt
    assert "石路边缘有轻微暖光反馈" in prompt.split("[Central Connection]", 1)[1]
    assert "Keep the existing adult son or daughter recognizable" in prompt
    assert "Do not insert a new copy of this person" in prompt
    assert "[Character Cartoon Form — high priority]" in prompt
    assert "softly rounded face and jaw" in prompt
    assert "Do not draw individual hair strands" in prompt
    assert "No semi-realistic portrait faces" in prompt
    assert audit_prompt(prompt) == []


def test_merged_relationship_uses_dynamic_shared_space_layout() -> None:
    plan = _five_layer_plan()
    controls = plan["L2_relational_structure_layer"]["deterministicControls"]
    controls["spatialMode"] = "merged"
    controls["connectionPolicy"] = "optional"
    controls["controls"]["zoneGapRatio"] = 0.0
    controls["controls"]["pathLength"] = 0.0
    controls["controls"]["pathWidth"] = 0.0
    layout = {
        "spatialMode": "merged",
        "layoutIntent": "one_shared_living_space",
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
    controls["layoutState"] = layout
    plan["L2_relational_structure_layer"]["layoutState"] = layout

    first_prompt = compile_for_both(
        five_layer_plan=plan,
        semantic_visual_instruction="",
        speaker_role="child",
    )
    update_prompt = compile_for_update(
        five_layer_plan=plan,
        semantic_visual_instruction="",
        speaker_role="child",
    )
    reflow_prompt = compile_for_reflow(
        five_layer_plan=plan,
        semantic_visual_instruction="",
        speaker_role="child",
    )

    for prompt in (first_prompt, update_prompt, reflow_prompt):
        assert "Spatial mode merged" in prompt
        assert "elder anchor [0.375, 0.642]" in prompt
        assert "child anchor [0.62, 0.393]" in prompt
        assert "person gap 0.228" in prompt
        assert "shared scene 0.65" in prompt
        assert "elder platform anchor [0.405, 0.6]" in prompt
        assert "platform gap 0.0" in prompt
        assert "shared ground 0.688" in prompt
        assert "Remove" in prompt
        assert "river" in prompt
        assert "两侧独立，中间石路连接" not in prompt
        assert "lower-left personal zone" not in prompt
        assert "upper-right personal zone" not in prompt
        assert "Keep the same approximate personal zone" not in prompt
        assert "No elder in the upper-right" not in prompt
        assert "No child in the lower-left" not in prompt
        assert "living environment" in prompt

    assert "older adult near [0.375, 0.642]" in first_prompt
    assert "younger adult near [0.62, 0.393]" in first_prompt
    assert "Do not preserve their former distant corner positions" in update_prompt
    assert "Do not lock the person to the previous distant corner" in update_prompt
    assert "[Relationship Reflow]" in reflow_prompt
    assert "Image B is the older adult identity reference" in reflow_prompt
    assert "Image C is the younger adult identity reference" in reflow_prompt
    assert "Move both complete platforms and both existing figures" in reflow_prompt
    assert audit_prompt(reflow_prompt) == []


def test_reflow_prompt_enforces_each_of_five_central_features() -> None:
    required_phrases = {
        "merged": "one continuous shared ground plane",
        "path_only": "Keep exactly one short, narrow everyday footpath",
        "river_and_path": "Keep both a readable river and a modest footpath",
        "river_only": "Keep river water as the only central distance element",
        "peripheral": "Keep one broad, clearly visible river as the central distance element",
    }
    central_features = {
        "merged": "shared_ground",
        "path_only": "short_path",
        "river_and_path": "river_and_path",
        "river_only": "river",
        "peripheral": "river",
    }

    for mode, required_phrase in required_phrases.items():
        plan = _five_layer_plan()
        controls = plan["L2_relational_structure_layer"]["deterministicControls"]
        controls["spatialMode"] = mode
        layout = {
            "spatialMode": mode,
            "elderAnchor": [0.35, 0.65],
            "childAnchor": [0.65, 0.35],
            "elderPlatformAnchor": [0.34, 0.64],
            "childPlatformAnchor": [0.66, 0.36],
            "personGapRatio": 0.3,
            "platformGapRatio": 0.2,
            "platformOverlapRatio": 0.05,
            "sharedGroundRatio": 0.5,
            "sharedSceneRatio": 0.5,
            "environmentMergeRatio": 0.5,
            "centralFeature": central_features[mode],
        }
        controls["layoutState"] = layout
        plan["L2_relational_structure_layer"]["layoutState"] = layout

        prompt = compile_for_reflow(
            five_layer_plan=plan,
            semantic_visual_instruction="",
            speaker_role="child",
        )

        assert required_phrase in prompt
        assert "Move both complete platforms" in prompt
        assert f"central feature {central_features[mode]}" in prompt
        assert audit_prompt(prompt) == []
