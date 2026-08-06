from __future__ import annotations

import pytest

from app.services.prompt_compiler import (
    PromptCompilationError,
    PromptCompiler,
)


def _five_layer_plan() -> dict:
    return {
        "L1_environment_layer": {
            "designContent": [
                "黄昏的花园露台，保持柔和暖光",
                "延续水彩纸张纹理",
            ]
        },
        "L2_relational_structure_layer": {
            "designContent": [
                "双方保持独立生活空间",
                "中间石路作为稳定连接",
            ]
        },
        "L3_object_event_layer": {
            "designContent": [
                "说话者正在包饺子",
                "局部使用面皮、馅料和擀面杖",
            ]
        },
        "L4_character_layer": {
            "designContent": [
                "说话者坐姿自然、神情平静",
                "人物不正视镜头",
            ]
        },
        "L5_motion_feedback_layer": {
            "designContent": [
                "路径节点有轻微暖光反馈",
                "不生成按钮或文字",
            ]
        },
    }


def test_shared_region_map_is_viewer_independent() -> None:
    assert PromptCompiler.build_shared_region_map() == {
        "elder": "lower-left",
        "child": "upper-right",
    }
    assert PromptCompiler.region_for_role("mother") == "lower-left"
    assert PromptCompiler.region_for_role("daughter") == "upper-right"


def test_first_pass_uses_reading_preset_for_non_speaking_child() -> None:
    prompt = PromptCompiler.compile_for_region(
        five_layer_plan=_five_layer_plan(),
        semantic_visual_instruction="这句在完整五层存在时不应重复注入。",
        generation_stage="first_voice",
        speaker_role="elder",
        target_role="child",
        target_region="upper_right",
        pass_index=1,
        existing_other_character=False,
    )

    assert "L1 Environmental Layer: 黄昏的花园露台" in prompt
    assert "L2 Relational Structure Layer: 双方保持独立生活空间" in prompt
    assert "quietly reads an open book" in prompt
    assert "eyes on the pages" in prompt
    assert "No current-round feedback is applied to the non-speaker" in prompt
    assert "说话者正在包饺子" not in prompt
    assert "[Non-Speaker Preset — authoritative]" in prompt
    assert "adult son or daughter" in prompt
    assert "upper-right" in prompt
    assert "approximately 45% of the square canvas" in prompt
    assert "full-frame handcrafted layered paper-cut world" in prompt
    assert "one continuous layered paper-cut world" in prompt
    assert "x=256..1279" not in prompt
    assert "[Character Body and Platform Framing — highest priority]" in prompt
    assert "Return one complete 1536 x 1536 square wallpaper" in prompt
    assert "延续水彩纸张纹理" not in prompt
    assert "可见纸纤维与分层剪纸纹理" in prompt
    assert "overrides conflicting character-size" in prompt
    assert "Do not add the older-adult parent yet" in prompt
    assert "not the current speaker" in prompt
    assert "这句在完整五层存在时不应重复注入" not in prompt
    assert "scene_state" not in prompt
    assert "viewer_role" not in prompt


def test_first_pass_keeps_designer_event_for_speaking_child() -> None:
    prompt = PromptCompiler.compile_for_region(
        five_layer_plan=_five_layer_plan(),
        generation_stage="first_voice",
        speaker_role="child",
        target_role="child",
        target_region="upper_right",
        pass_index=1,
        existing_other_character=False,
    )

    assert "L3 Object / Event Layer: 说话者正在包饺子" in prompt
    assert "L4 Character Layer: 说话者坐姿自然" in prompt
    assert "current_round_speaker=yes" in prompt
    assert "[Non-Speaker Preset" not in prompt


def test_second_pass_preserves_first_character() -> None:
    prompt = PromptCompiler.compile_for_region(
        five_layer_plan=_five_layer_plan(),
        generation_stage="first_voice",
        speaker_role="elder",
        target_role="elder",
        target_region="left_bottom",
        pass_index=2,
        existing_other_character=True,
    )

    assert "current_round_speaker=yes" in prompt
    assert "Image 1 is the authoritative saved Pass 1 output" in prompt
    assert "Preserve the existing adult son or daughter in upper-right" in prompt
    assert "Insert exactly one older-adult parent in lower-left" in prompt
    assert "L3 Object / Event Layer: 说话者正在包饺子" in prompt
    assert "[Non-Speaker Preset" not in prompt


def test_second_pass_uses_watering_preset_for_non_speaking_elder() -> None:
    prompt = PromptCompiler.compile_for_region(
        five_layer_plan=_five_layer_plan(),
        generation_stage="first_voice",
        speaker_role="child",
        target_role="elder",
        target_region="left_bottom",
        pass_index=2,
        existing_other_character=True,
    )

    assert "gently waters potted flowers" in prompt
    assert "one small watering can" in prompt
    assert "说话者正在包饺子" not in prompt
    assert "current_round_speaker=no" in prompt


@pytest.mark.parametrize(
    ("speaker_role", "target_region", "preserve_text"),
    [
        ("elder", "lower-left", "adult son or daughter in upper-right"),
        ("child", "upper-right", "older-adult parent in lower-left"),
    ],
)
def test_update_only_changes_speaker_region(
    speaker_role: str,
    target_region: str,
    preserve_text: str,
) -> None:
    prompt = PromptCompiler.compile_for_update(
        five_layer_plan=_five_layer_plan(),
        generation_stage="subsequent_update",
        speaker_role=speaker_role,
    )

    assert f"Edit only the existing speaker in {target_region}" in prompt
    assert preserve_text in prompt
    assert "approximately 45% of the square canvas" in prompt
    assert "one continuous layered paper-cut world" in prompt
    assert "terrain edge or decorative foreground layer must never cut" in prompt
    assert "Preserve the central relationship path" in prompt
    assert "do not redraw the central path" in prompt
    assert "same adult person" in prompt


def test_update_rejects_cross_role_or_wrong_region() -> None:
    with pytest.raises(PromptCompilationError):
        PromptCompiler.compile_for_update(
            five_layer_plan=_five_layer_plan(),
            generation_stage="subsequent_update",
            speaker_role="elder",
            editing_character_role="child",
        )

    with pytest.raises(PromptCompilationError):
        PromptCompiler.compile_for_update(
            five_layer_plan=_five_layer_plan(),
            generation_stage="subsequent_update",
            speaker_role="child",
            editing_region="lower_left",
        )


def test_semantic_summary_is_only_an_incomplete_plan_fallback() -> None:
    incomplete_plan = _five_layer_plan()
    incomplete_plan["L3_object_event_layer"] = {}
    prompt = PromptCompiler.compile_for_update(
        five_layer_plan=incomplete_plan,
        semantic_visual_instruction="补充当前生活事件：正在阳台浇花。",
        generation_stage="subsequent_update",
        speaker_role="child",
    )

    assert "[Designer Summary Fallback]" in prompt
    assert "正在阳台浇花" in prompt
