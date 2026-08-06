"""Designer five-layer plan to Seedream regional prompt compiler.

This is the shared-wallpaper variant of the reference project's structured
PromptCompiler. It intentionally has no SceneState or viewer-role input:

* elder/parent is always in the lower-left region;
* child/adult son or daughter is always in the upper-right region;
* the current speaker decides the only region that may change on updates;
* the previous shared wallpaper is the authority for all preserved content.

The compiler is provider-independent. A later integration step passes its
output to Seedream together with the current wallpaper and identity image.
"""

from __future__ import annotations

import re
from typing import Any

from app.services.style_profiles import VINTAGE_PAPER_CRAFT_V1_STYLE


class PromptCompilationError(ValueError):
    """Raised when a regional prompt cannot be compiled safely."""


class PromptCompiler:
    """Compile Designer output into one region-scoped image prompt."""

    SHARED_REGION_MAP = {
        "elder": "lower-left",
        "child": "upper-right",
    }
    OPPOSITE_ROLE = {
        "elder": "child",
        "child": "elder",
    }
    ROLE_LABEL = {
        "elder": "older-adult parent",
        "child": "adult son or daughter",
    }
    FIRST_VOICE_NON_SPEAKER_PRESETS = {
        "child": {
            "event": (
                "The adult child quietly reads an open book in the upper-right "
                "personal zone. The book is the only required action prop."
            ),
            "character": (
                "Keep a natural seated or standing reading posture, with eyes "
                "on the pages and never toward the camera."
            ),
            "summary": "child reading a book in the upper-right",
        },
        "elder": {
            "event": (
                "The older adult parent gently waters potted flowers in the "
                "lower-left personal zone, using one small watering can."
            ),
            "character": (
                "Keep a natural inward-facing gardening posture, with eyes on "
                "the flowers and never toward the camera."
            ),
            "summary": "elder watering flowers in the lower-left",
        },
    }
    LAYER_ORDER = (
        ("L1_environment_layer", "L1 Environmental Layer"),
        ("L2_relational_structure_layer", "L2 Relational Structure Layer"),
        ("L3_object_event_layer", "L3 Object / Event Layer"),
        ("L4_character_layer", "L4 Character Layer"),
        ("L5_motion_feedback_layer", "L5 Motion / Feedback Layer"),
    )

    SOFT_LIMIT = 7_000
    HARD_LIMIT = 9_000
    COMPACT_LAYER_LIMIT = 620

    STYLE_BLOCK = "[Style Lock]\n" + VINTAGE_PAPER_CRAFT_V1_STYLE

    CANVAS_BLOCK = """[Continuous Square Canvas — highest priority]
Return exactly one complete 1:1 wallpaper on the 1536 x 1536 logical canvas.
Treat the entire square as one continuous layered paper-cut world. Never divide
it into a central panel and side strips. Terrain, plants, rivers, paths, shadows,
lighting and paper grain must flow naturally across the whole image without a
vertical seam, tonal jump, texture break or pasted panel. Keep people and
important props comfortably inset from the outer edges. Do not crop, stretch,
mirror, curve or rebuild the supplied square wallpaper."""

    ENVIRONMENT_BLOCK = """[Environment Lock]
Image 1 is the authoritative current shared wallpaper.
Preserve the sky, landscape, garden, terrace, path, architecture, lighting,
perspective, layered paper construction and fibrous paper texture. Add only the target character's minimum
action props inside the target personal zone. Do not build a new room, kitchen,
platform, stage or replacement environment. Background change budget is zero."""

    SPATIAL_BLOCK = """[Spatial Separation — highest priority]
Keep two independent personal living zones inside the continuous square scene.
The upper-right character, body, furniture and props stay fully upper-right.
The lower-left character, body, furniture and props stay fully lower-left.
Keep the central path visible and unoccupied as the separator. Do not merge,
overlap or connect their furniture; no shared table, kitchen or living area."""

    ORIENTATION_BLOCK = """[Body Orientation and Gaze]
Use an inward-facing composition without mutual gaze. The lower-left person
turns generally right/inward and looks at their own local activity. The
upper-right person turns generally left/inward and looks at their own local
activity. Neither person faces the camera or looks directly at the other."""

    CHILD_SCALE_BLOCK = """[Upper-Right Character Scale]
The adult child in the upper-right must be visibly smaller than the elder:
show the complete child figure at approximately 45% of the square canvas height.
Keep the whole child, their seat and minimum action props fully inside the
upper-right zone. Do not enlarge the child into the center or lower half.
This rule overrides conflicting character-size or zone-occupancy instructions
in the Designer plan."""

    BODY_PLATFORM_BLOCK = """[Character Body and Platform Framing — highest priority]
Compose each person as a complete head-to-toe figure with a continuous body.
A meaningful activity prop such as a table, shopping basket, chair or train seat
may naturally cover part of the body below the waist. The living-platform rim,
terrain edge or decorative foreground layer must never cut through the torso or
waist or make a floating half-body. Lower the platform front edge enough to fit
the complete person above/on its floor."""

    OUTPUT_BLOCK = """[Output Safety]
Return one complete 1536 x 1536 square wallpaper. Do not add text, captions, chat
bubbles, buttons, logos, watermarks, UI, duplicate people or extra people."""

    @classmethod
    def normalize_role(cls, role: str | None) -> str:
        normalized = (role or "").strip().lower()
        if normalized in {"child", "daughter", "son"}:
            return "child"
        if normalized in {
            "elder",
            "parent",
            "mother",
            "father",
            "grandmother",
            "grandfather",
        }:
            return "elder"
        raise PromptCompilationError(f"Unsupported character role: {role!r}")

    @classmethod
    def build_shared_region_map(cls) -> dict[str, str]:
        return dict(cls.SHARED_REGION_MAP)

    @classmethod
    def region_for_role(cls, role: str | None) -> str:
        return cls.SHARED_REGION_MAP[cls.normalize_role(role)]

    @classmethod
    def _initial_identity_block(cls, target_role: str) -> str:
        if target_role == "child":
            return """[Identity Lock]
Match the supplied child-role identity reference: same face, apparent adult
age, hairstyle, body proportions and gender presentation. Here “child” means
an adult son or daughter, never a minor. Do not recast or copy the reference
background, text, clothing marks or watermark."""
        return """[Identity Lock]
Match the supplied elder-role identity reference: same face, apparent
older-adult age, hairstyle, body proportions and gender presentation.
Preserve age characteristics. Do not recast or copy the reference background,
text, clothing marks or watermark."""

    @classmethod
    def compile_for_region(
        cls,
        *,
        five_layer_plan: dict[str, Any],
        semantic_visual_instruction: str = "",
        generation_stage: str,
        speaker_role: str,
        target_role: str,
        target_region: str,
        pass_index: int,
        existing_other_character: bool,
        compact: bool = False,
    ) -> str:
        """Compile Pass 1 or Pass 2 of the first shared-wallpaper render."""

        if generation_stage != "first_voice":
            raise PromptCompilationError(
                "compile_for_region requires generation_stage='first_voice'"
            )
        if pass_index not in {1, 2}:
            raise PromptCompilationError("pass_index must be 1 or 2")
        if existing_other_character != (pass_index == 2):
            raise PromptCompilationError(
                "Pass 1 must not have an existing character; "
                "Pass 2 must preserve the Pass 1 character"
            )

        speaker = cls.normalize_role(speaker_role)
        target = cls.normalize_role(target_role)
        expected_region = cls.region_for_role(target)
        normalized_region = cls._normalize_region(target_region)
        if normalized_region != expected_region:
            raise PromptCompilationError(
                f"{target} must be rendered in {expected_region}, "
                f"not {target_region!r}"
            )

        other_role = cls.OPPOSITE_ROLE[target]
        target_label = cls.ROLE_LABEL[target]
        other_label = cls.ROLE_LABEL[other_role]
        target_is_speaker = target == speaker

        identity_block = cls._initial_identity_block(target)
        target_block = f"""[Target]
role={target_label}
region={expected_region}
current_round_speaker={'yes' if target_is_speaker else 'no'}
Use the Designer plan only for this target. Place exactly one matching adult
character fully inside {expected_region}, with all action props in that zone."""

        routing_block = (
            "[Speech Routing]\n"
            + (
                "This target is the current speaker. Apply the current-round "
                "Designer event, action, emotion and objects to this target."
                if target_is_speaker
                else
                "This target is not the current speaker. Ignore the speaker's "
                "current-round event, action, emotion, objects and feedback. "
                "Use the fixed non-speaker preset below instead."
            )
        )
        effective_plan = five_layer_plan
        preset_block = ""
        if not target_is_speaker:
            preset = cls.FIRST_VOICE_NON_SPEAKER_PRESETS[target]
            effective_plan = cls._non_speaker_first_voice_plan(
                five_layer_plan,
                target_role=target,
            )
            preset_block = f"""[Non-Speaker Preset — authoritative]
{preset["event"]}
{preset["character"]}
This preset overrides any current-round L3, L4 or L5 instruction belonging to
the speaker. Do not transfer the speaker's activity to this character."""

        if pass_index == 1:
            preserve_block = f"""[Pass 1 Edit]
Insert exactly one {target_label} in {expected_region}.
Do not add the {other_label} yet.
Preserve the current wallpaper outside {expected_region} as unchanged as possible."""
        else:
            other_region = cls.region_for_role(other_role)
            preserve_block = f"""[Preserve — Pass 2]
Image 1 is the authoritative saved Pass 1 output.
Preserve the existing {other_label} in {other_region}: do not remove,
replace, redraw, relocate, resize, duplicate or change that identity.
Insert exactly one {target_label} in {expected_region}. Preserve all content
outside {expected_region} as unchanged as possible."""

        prompt = cls._compose(
            five_layer_plan=effective_plan,
            semantic_visual_instruction=semantic_visual_instruction,
            fixed_blocks=[
                cls.STYLE_BLOCK,
                cls.CANVAS_BLOCK,
                identity_block,
                target_block,
                routing_block,
                preset_block,
                cls.ENVIRONMENT_BLOCK,
                preserve_block,
                cls.SPATIAL_BLOCK,
                cls.CHILD_SCALE_BLOCK,
                cls.BODY_PLATFORM_BLOCK,
                cls.ORIENTATION_BLOCK,
                cls.OUTPUT_BLOCK,
            ],
            force_compact=compact,
        )
        cls._audit(
            prompt,
            target_role=target,
            target_region=expected_region,
            preserve_required=pass_index == 2,
        )
        return prompt

    @classmethod
    def first_voice_preset_for_role(
        cls,
        role: str | None,
    ) -> str:
        """Return the human-readable first-render preset for one role."""

        normalized = cls.normalize_role(role)
        return cls.FIRST_VOICE_NON_SPEAKER_PRESETS[normalized]["summary"]

    @classmethod
    def _non_speaker_first_voice_plan(
        cls,
        five_layer_plan: dict[str, Any],
        *,
        target_role: str,
    ) -> dict[str, Any]:
        """Keep shared layers but replace speaker-specific layers with a preset."""

        if not isinstance(five_layer_plan, dict):
            raise PromptCompilationError("five_layer_plan must be an object")
        preset = cls.FIRST_VOICE_NON_SPEAKER_PRESETS[target_role]
        return {
            "L1_environment_layer": five_layer_plan.get(
                "L1_environment_layer",
                {"designContent": ["Preserve the supplied base environment."]},
            ),
            "L2_relational_structure_layer": five_layer_plan.get(
                "L2_relational_structure_layer",
                {
                    "designContent": [
                        "Keep the two fixed personal zones and central path."
                    ]
                },
            ),
            "L3_object_event_layer": {
                "designContent": [preset["event"]]
            },
            "L4_character_layer": {
                "designContent": [preset["character"]]
            },
            "L5_motion_feedback_layer": {
                "designContent": [
                    "No current-round feedback is applied to the non-speaker; "
                    "keep this zone calm and stable."
                ]
            },
        }

    @classmethod
    def compile_for_update(
        cls,
        *,
        five_layer_plan: dict[str, Any],
        semantic_visual_instruction: str = "",
        generation_stage: str,
        speaker_role: str,
        editing_character_role: str | None = None,
        editing_region: str | None = None,
        preserve_role: str | None = None,
        compact: bool = False,
    ) -> str:
        """Compile one prompt-only subsequent update of the speaker's region."""

        if generation_stage != "subsequent_update":
            raise PromptCompilationError(
                "compile_for_update requires "
                "generation_stage='subsequent_update'"
            )

        speaker = cls.normalize_role(speaker_role)
        editing_role = cls.normalize_role(editing_character_role or speaker)
        if editing_role != speaker:
            raise PromptCompilationError(
                "The editing character must be the current speaker"
            )

        expected_region = cls.region_for_role(editing_role)
        actual_region = cls._normalize_region(editing_region or expected_region)
        if actual_region != expected_region:
            raise PromptCompilationError(
                f"{editing_role} must be edited in {expected_region}, "
                f"not {editing_region!r}"
            )

        expected_preserve_role = cls.OPPOSITE_ROLE[editing_role]
        actual_preserve_role = cls.normalize_role(
            preserve_role or expected_preserve_role
        )
        if actual_preserve_role != expected_preserve_role:
            raise PromptCompilationError(
                f"Updating {editing_role} must preserve "
                f"{expected_preserve_role}"
            )

        editing_label = cls.ROLE_LABEL[editing_role]
        preserve_label = cls.ROLE_LABEL[actual_preserve_role]
        preserve_region = cls.region_for_role(actual_preserve_role)

        identity_block = f"""[Identity Lock]
Edit the existing {editing_label} in {expected_region}.
Keep the same adult person: face, apparent age, hairstyle, body proportions and identity.
The supplied identity reference may reinforce identity only. Do not recast,
replace, regenerate or swap either person."""

        target_block = f"""[Target]
role={editing_label}
region={expected_region}
current_round_speaker=yes
Apply this round's Designer event, action, emotion and minimum required props
only to the existing speaker inside {expected_region}."""

        preserve_block = f"""[Preserve — highest priority]
Preserve the existing {preserve_label} in {preserve_region} exactly: identity,
face, age, pose, action, emotion, clothing, furniture and props.
Preserve the central relationship path and every non-target region.
Do not remove, replace, redraw, relocate, resize or duplicate either person."""

        edit_block = f"""[Regional Edit Constraint]
Edit only the existing speaker in {expected_region}. Change only that person's
action, pose, expression and minimum local props required by the Designer plan.
Interpret L2 and L5 only as guidance inside the target zone.
Inside this edit, do not redraw the central path or the preserved person's zone.
Keep all other content unchanged."""

        prompt = cls._compose(
            five_layer_plan=five_layer_plan,
            semantic_visual_instruction=semantic_visual_instruction,
            fixed_blocks=[
                cls.STYLE_BLOCK,
                cls.CANVAS_BLOCK,
                identity_block,
                target_block,
                cls.ENVIRONMENT_BLOCK,
                preserve_block,
                cls.SPATIAL_BLOCK,
                cls.CHILD_SCALE_BLOCK,
                cls.BODY_PLATFORM_BLOCK,
                cls.ORIENTATION_BLOCK,
                edit_block,
                cls.OUTPUT_BLOCK,
            ],
            force_compact=compact,
        )
        cls._audit(
            prompt,
            target_role=editing_role,
            target_region=expected_region,
            preserve_required=True,
        )
        return prompt

    @classmethod
    def _compose(
        cls,
        *,
        five_layer_plan: dict[str, Any],
        semantic_visual_instruction: str,
        fixed_blocks: list[str],
        force_compact: bool = False,
    ) -> str:
        plan_block = cls._designer_plan_block(
            five_layer_plan,
            max_chars_per_layer=(
                cls.COMPACT_LAYER_LIMIT if force_compact else None
            ),
        )
        summary_block = cls._designer_summary_fallback(
            five_layer_plan,
            semantic_visual_instruction,
        )
        prompt = "\n\n".join(
            block.strip()
            for block in [
                *fixed_blocks[:4],
                plan_block,
                summary_block,
                *fixed_blocks[4:],
            ]
            if block and block.strip()
        )

        if len(prompt) > cls.SOFT_LIMIT and not force_compact:
            plan_block = cls._designer_plan_block(
                five_layer_plan,
                max_chars_per_layer=cls.COMPACT_LAYER_LIMIT,
            )
            prompt = "\n\n".join(
                block.strip()
                for block in [
                    *fixed_blocks[:4],
                    plan_block,
                    summary_block[:500] if summary_block else "",
                    *fixed_blocks[4:],
                ]
                if block and block.strip()
            )

        if len(prompt) > cls.HARD_LIMIT:
            raise PromptCompilationError(
                f"Compiled prompt exceeds safe limit: {len(prompt)} characters"
            )
        return prompt

    @classmethod
    def _designer_plan_block(
        cls,
        five_layer_plan: dict[str, Any],
        *,
        max_chars_per_layer: int | None = None,
    ) -> str:
        if not isinstance(five_layer_plan, dict):
            raise PromptCompilationError("five_layer_plan must be an object")

        lines = [
            "[Designer Five-Layer Plan — authoritative current-round content]"
        ]
        found_content = False
        for key, label in cls.LAYER_ORDER:
            layer = five_layer_plan.get(key)
            content = cls._extract_layer_content(layer)
            if content:
                found_content = True
                if max_chars_per_layer and len(content) > max_chars_per_layer:
                    content = content[: max_chars_per_layer - 1].rstrip() + "…"
            else:
                content = "(no additional instruction)"
            lines.append(f"{label}: {content}")

        if not found_content:
            raise PromptCompilationError(
                "Designer five-layer plan contains no renderable content"
            )
        lines.append(
            "L5 supplies visual feedback cues only; never render interface controls."
        )
        return "\n".join(lines)

    @staticmethod
    def _extract_layer_content(layer: Any) -> str:
        if isinstance(layer, dict):
            value = (
                layer.get("designContent")
                or layer.get("design_content")
                or layer.get("prompt")
                or layer.get("content")
            )
        else:
            value = layer

        if isinstance(value, list):
            parts = [str(item).strip() for item in value if str(item).strip()]
        elif isinstance(value, dict):
            parts = [
                f"{key}: {item}"
                for key, item in value.items()
                if str(item).strip()
            ]
        elif value is None:
            parts = []
        else:
            parts = [str(value).strip()]

        unique_parts: list[str] = []
        seen: set[str] = set()
        for part in parts:
            normalized = re.sub(r"\s+", " ", part).strip()
            normalized = PromptCompiler._isolate_paper_craft_style(normalized)
            if normalized and normalized not in seen:
                seen.add(normalized)
                unique_parts.append(normalized)
        return "；".join(unique_parts)

    @staticmethod
    def _designer_summary_fallback(
        five_layer_plan: dict[str, Any],
        semantic_visual_instruction: str,
    ) -> str:
        """Use semantic summary only when a five-layer item is incomplete."""

        if not semantic_visual_instruction.strip():
            return ""
        complete = all(
            PromptCompiler._extract_layer_content(five_layer_plan.get(key))
            for key, _ in PromptCompiler.LAYER_ORDER
        )
        if complete:
            return ""
        summary = re.sub(
            r"\s+",
            " ",
            semantic_visual_instruction,
        ).strip()
        summary = PromptCompiler._isolate_paper_craft_style(summary)
        return f"[Designer Summary Fallback]\n{summary}"

    @staticmethod
    def _isolate_paper_craft_style(text: str) -> str:
        """Remove upstream painting-medium instructions from Designer content."""

        replacements = (
            (r"warm watercolor storybook style", "handcrafted layered paper-cut storybook style"),
            (r"watercolor paper texture", "visible fibrous layered paper texture"),
            (r"watercolor texture", "layered fibrous paper texture"),
            (r"watercolor", "layered paper-cut"),
            (r"水彩纸张纹理", "可见纸纤维与分层剪纸纹理"),
            (r"水彩质感", "分层纸雕质感"),
            (r"水彩", "分层剪纸"),
            (r"水粉", "纸艺色块"),
        )
        isolated = text
        for pattern, replacement in replacements:
            isolated = re.sub(pattern, replacement, isolated, flags=re.IGNORECASE)
        return isolated

    @classmethod
    def _audit(
        cls,
        prompt: str,
        *,
        target_role: str,
        target_region: str,
        preserve_required: bool,
    ) -> None:
        required_markers = [
            "[Style Lock]",
            "[Continuous Square Canvas",
            "[Identity Lock]",
            "[Target]",
            "[Designer Five-Layer Plan",
            "L1 Environmental Layer:",
            "L2 Relational Structure Layer:",
            "L3 Object / Event Layer:",
            "L4 Character Layer:",
            "L5 Motion / Feedback Layer:",
            "[Environment Lock]",
            "[Spatial Separation",
            "[Body Orientation and Gaze]",
            "[Output Safety]",
            target_region,
            cls.ROLE_LABEL[target_role],
        ]
        if preserve_required:
            required_markers.append("[Preserve")

        missing = [marker for marker in required_markers if marker not in prompt]
        if missing:
            raise PromptCompilationError(
                "Compiled prompt failed audit; missing: " + ", ".join(missing)
            )

        forbidden_markers = (
            "scene_state",
            "viewer_role",
            "previous_scene_state",
            "[MASK]",
        )
        found_forbidden = [
            marker for marker in forbidden_markers if marker in prompt
        ]
        if found_forbidden:
            raise PromptCompilationError(
                "Compiled prompt contains forbidden legacy input: "
                + ", ".join(found_forbidden)
            )

    @staticmethod
    def _normalize_region(region: str) -> str:
        normalized = (region or "").strip().lower().replace("_", "-")
        aliases = {
            "left-bottom": "lower-left",
            "lower-left": "lower-left",
            "upper-right": "upper-right",
            "right-upper": "upper-right",
        }
        result = aliases.get(normalized)
        if result is None:
            raise PromptCompilationError(f"Unsupported region: {region!r}")
        return result


prompt_compiler = PromptCompiler()
