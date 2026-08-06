"""Five-layer design output to the migrated single-pass Seedream prompts.

The fixed rendering text in this module is migrated from the reference
project's ``prompt_compiler_v2.py``.  This project does not import or recreate
the reference Planner: the existing ``SemanticMappingAgent`` remains the only
semantic mapper and its five-layer output is read directly here.
"""

from __future__ import annotations

import re
from typing import Any

from app.services.style_profiles import VINTAGE_PAPER_CRAFT_V1_STYLE


PROHIBITED_TOKENS = frozenset(
    [
        "activity_key",
        "activity_profile",
        "supplied mask",
        "outside the mask",
        "mask pixels",
        "add mask",
        "insert mask",
        "safe_truncate",
        "compact_activity",
        "prompt compression",
        "five-layer plan",
        "five layer plan",
        "five layer",
        "layered plan",
        "Planner",
        "planner",
        "cognitive scaffold",
        "cognitive_scaffold",
        "semantic mapping",
        "semantic_mapping",
        "visual dials",
        "visual_dials",
        "generation stage",
        "generation_stage",
        "warm watercolor storybook style",
        "paper texture, lighting and perspective",
        "线描水彩",
        "environment_mode",
        "background_change_budget",
        "existing_base_environment_lock",
    ]
)


CANVAS_CONTRACT = """[Continuous Square Canvas — highest priority]
Return exactly one complete 1:1 wallpaper on the 1536 x 1536 logical canvas.
Treat the entire square as one continuous layered paper-cut world. Terrain,
mountains, plants, rivers, paths, shadows, lighting and paper grain must flow
naturally across the whole image. Never divide the canvas into a central panel
and side strips. No vertical divider, tonal jump, texture break, pasted panel or
cut-off branch anywhere. Keep people and important activity props comfortably
inset from the outer canvas edges. Do not crop, stretch, mirror, curve or rebuild
Image A."""


RELATIONSHIP_REFLOW_CONTRACT = """[Continuous Canvas and Relationship-Reflow Contract — highest priority]
Return exactly one complete 1:1 wallpaper on the 1536 x 1536 logical canvas.
Treat the entire square as one continuous layered paper-cut world. Never divide
the image into a central panel and side strips. Keep people and important props
comfortably inset from the outer canvas edges.

Image A is the continuity reference, but its central layout is not locked. You
must recompose the two existing living platforms, platform edges, floors,
nearby furniture, plants and people together across the continuous scene according
to the requested relationship spatial mode. Move the complete platforms, not
only the people. Replace Image A's former central river, road, path, bridge or
empty band whenever it conflicts with the requested mode's exact central
feature. Preserve the surrounding paper-craft world, identities and overall palette;
do not crop, stretch, mirror or curve Image A. No vertical seam or visible panel
border."""


CARTOON_CHARACTER_CONTRACT = """[Character Cartoon Form — high priority]
Render both family members with warm, simplified children's storybook cartoon
anatomy inside the layered paper-cut medium, never as semi-realistic portraits
with a paper surface applied on top. Preserve identity through apparent age,
overall face silhouette, hairstyle silhouette and color, skin tone, body build
and a few broad recognizable cues; stylize those cues instead of reproducing
the references' exact photographic facial ratios or bone structure.

Give each person a softly rounded face and jaw, simple readable eyes and brows,
a small minimally defined nose and a simple gentle mouth. Build facial features
from clean, broad cut-paper shapes with restrained linework. Do not render a
sculpted nose bridge, sharp cheekbones, modeled lips, visible nostrils, dense
eyelashes, skin pores, realistic wrinkles or realistic facial planes.

Simplify hair into a coherent rounded silhouette made from a few broad layered
paper locks. Do not draw individual hair strands, fine strand texture or
photographic hair highlights. Keep the older adult visibly older and the
younger adult visibly adult, but use friendly storybook proportions rather
than realistic portrait anatomy or an over-infantilized chibi mascot."""


CHARACTER_FRAMING_CONTRACT = """[Character Body and Platform Framing — highest priority]
Compose both people as complete head-to-toe figures with anatomically continuous
bodies. When not naturally covered by an activity prop, show the full torso,
both legs and both feet with visible floor beneath them. A table, shopping
basket, chair, train seat or other meaningful activity prop may naturally hide
part of the body below the waist; preserve the body's believable continuation
behind that prop.

The raised living-platform edge, terrain rim or decorative foreground layer is
not an activity prop and must never cut through a person's torso or waist, erase
the lower body, or make a floating half-body emerge from the platform. Lower the
platform and its front rim enough to fit the complete person above/on its floor.
Keep the platform top surface behind or beneath the person rather than across the
body. Do not use waist-up or bust framing unless a meaningful activity prop is
actually providing the natural occlusion."""


CHARACTER_DISPLAY_SAFE_ZONE_CONTRACT = """[Character Display Safe Zone and Scale — highest priority]
Keep both family members fully inside the central display-safe composition. The
older-adult body center and the younger-adult body center must remain inside
normalized x=0.30-0.70, and every visible part of both silhouettes -- head,
hair, face, shoulders, hands, clothing and feet -- must remain inside
x=0.20-0.80. Do not place either person against an outer edge or partially
outside the square canvas.

Keep each complete head-to-foot character at 34%-40% of the full square canvas
height; neither character may exceed 42%. Keep the combined person plus primary
activity-prop group compact enough to fit inside its side of the central safe
composition. Relationship modes change the horizontal gap between the two body
centers and the relationship between their platforms; they must never change
character scale, camera zoom or crop. Empty space, a short path, a river or a
large desk is not a reason to enlarge either person. Numeric character anchors
and this safe-zone contract override qualitative words such as left, right,
upper or lower."""


FULL_FRAME_LANDSCAPE_CONTRACT = """[Full-frame Landscape — highest priority]
Fill the complete square canvas edge-to-edge with one continuous environmental
landscape. The words "living platform" mean a naturally integrated terrace,
floor or activity area inside that landscape; they never mean a freestanding
tray, boxed stage, tabletop model, pedestal, framed display or isolated floating
island.

Scene content, terrain, sky, foliage or architecture must physically reach every
pixel edge: top, bottom, left and right.
There must be zero outer margin and zero visible backing sheet.
Do not shrink the world into a miniature model surrounded
by blank cream paper. Do not generate any picture frame, matte, mount, border,
white rim, cream rim, torn-paper outer contour, shadowed outer contour, nested
canvas, rectangular tray, circular tray or polygonal outer boundary around the
whole scene. The camera is inside the paper-cut world, never looking at a separate
paper artwork placed on a table or backing sheet.

Rivers and paths must flow naturally through the full-frame terrain and must not
become the border of an inset model. Preserve Image A's edge-to-edge landscape
continuity while reflowing only the relationship layout requested here. If any
outer blank paper would remain, extend the nearest sky, terrain and foliage all
the way to the image pixels instead.

Reserve normalized horizontal bands x=0.00-0.20 and x=0.80-1.00 for continuous
environment only: sky, distant hills, layered terrain, flowers, shrubs, trees and
foliage. No living-platform floor, raised platform rim, primary furniture or
person body center may occupy those outer bands. Keep both complete living
platform footprints inside x=0.20-0.80; use vegetation to continue naturally
from their outer edges to the image boundaries."""


_NON_SPEAKER_PRESETS = {
    "child": (
        "The adult child quietly reads an open book in the inward, right-of-center "
        "activity area. Keep a natural seated or standing reading posture, "
        "with eyes on the pages and never toward the camera. Keep the complete "
        "silhouette inset from the right canvas edge."
    ),
    "elder": (
        "The older adult parent gently waters potted flowers in the "
        "inward, left-of-center activity area, using one small watering can. Keep a "
        "natural inward-facing gardening posture, with eyes on the flowers "
        "and never toward the camera."
    ),
}


def normalize_role(role: str | None) -> str:
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
    raise ValueError(f"Unsupported character role: {role!r}")


def audit_prompt(prompt: str) -> list[str]:
    """Return internal or legacy prompt tokens found in a final prompt."""

    lower_prompt = prompt.lower()
    return [
        token
        for token in PROHIBITED_TOKENS
        if token.lower() in lower_prompt
    ]


def _paper_craft_text(value: Any) -> str:
    if isinstance(value, list):
        raw = "\n".join(str(item).strip() for item in value if str(item).strip())
    elif isinstance(value, dict):
        raw = "\n".join(
            f"{key}: {item}" for key, item in value.items() if str(item).strip()
        )
    elif value is None:
        raw = ""
    else:
        raw = str(value).strip()

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
    for pattern, replacement in replacements:
        raw = re.sub(pattern, replacement, raw, flags=re.IGNORECASE)
    return re.sub(r"\n{3,}", "\n\n", raw).strip()


def _layer_content(five_layer_plan: dict[str, Any], key: str) -> str:
    layer = five_layer_plan.get(key) if isinstance(five_layer_plan, dict) else None
    if isinstance(layer, dict):
        value = (
            layer.get("designContent")
            or layer.get("design_content")
            or layer.get("prompt")
            or layer.get("content")
        )
    else:
        value = layer
    return _paper_craft_text(value)


def _short_phrase(value: Any, max_chars: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip(" ，。；;、")
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip(" ，。；;、")


def _role_visual_states(five_layer_plan: dict[str, Any]) -> dict[str, Any]:
    state = five_layer_plan.get("roleVisualState")
    if isinstance(state, dict):
        return state
    l1 = five_layer_plan.get("L1_environment_layer")
    if isinstance(l1, dict) and isinstance(l1.get("roleStates"), dict):
        return l1["roleStates"]
    return {}


def _local_time_lighting_contract(five_layer_plan: dict[str, Any]) -> str:
    """Compile visible local sky treatment instead of relying on a symbol alone."""

    states = _role_visual_states(five_layer_plan)
    labels = {"elder": "older-adult left-side", "child": "younger-adult right-side"}
    clauses: list[str] = []
    has_day = False
    has_night = False
    for role in ("elder", "child"):
        state = states.get(role)
        if not isinstance(state, dict) or state.get("available") is False:
            continue
        lighting = state.get("lighting")
        if not isinstance(lighting, dict):
            continue
        sky_state = str(lighting.get("skyState") or "").strip().lower()
        label = labels[role]
        if sky_state in {"night", "deep_night"}:
            has_night = True
            darkness = "very dark near-black navy" if sky_state == "deep_night" else "visibly dark deep navy-blue"
            clauses.append(
                f"The {label} local sky and the atmosphere directly behind that person's "
                f"scene must use {darkness} layered paper, with clearly dimmer terrain and "
                "cool night shadows. A moon icon alone is not sufficient evidence of night. "
                "Do not leave this night region ivory, cream, beige, white or daylight-bright."
            )
        elif sky_state in {"bright_day", "pale_dawn", "soft_afternoon"}:
            has_day = True
            clauses.append(
                f"The {label} local sky must remain visibly bright daytime paper with warm or "
                "neutral daylight and a readable sun; do not darken it into night."
            )
        elif sky_state == "sunset":
            clauses.append(
                f"The {label} local sky must show a readable orange-pink sunset gradient and "
                "a low setting sun, not plain cream daylight."
            )

    if not clauses:
        return "[Local Time Lighting — high priority]\nPreserve Image A's established time and lighting."
    if has_day and has_night:
        clauses.append(
            "Both local time states must remain simultaneously readable. Blend them through an "
            "organic diagonal transition made from overlapping hills, foliage, cloud layers and "
            "gradual color change; do not use a straight vertical divider, split-screen edge or seam."
        )
    return "[Local Time Lighting — high priority]\n" + "\n".join(clauses)


def _compact_role_environment(
    state: dict[str, Any],
    *,
    label: str,
    fallback: str,
) -> str:
    if not state:
        compact_fallback = _short_phrase(fallback, 80)
        if compact_fallback:
            return f"{label}: {compact_fallback}. Keep this local scene compact."
        return f"{label}: preserve the established local scene and lighting in Image A."
    if state.get("available") is False:
        return f"{label}: preserve the established local scene and lighting in Image A."
    time_value = _short_phrase(state.get("timeRaw"), 12) or "既有时段"
    scene_value = _short_phrase(state.get("sceneRaw"), 20) or _short_phrase(fallback, 20) or "生活空间"
    lighting = state.get("lighting") if isinstance(state.get("lighting"), dict) else {}
    brightness = lighting.get("brightness")
    brightness_text = (
        f"brightness {brightness}"
        if isinstance(brightness, (int, float))
        else "preserve prior brightness"
    )
    lamp_state = _short_phrase(lighting.get("lampState"), 18) or "contextual"
    sky_state = _short_phrase(lighting.get("skyState"), 24) or "preserve_previous"
    celestial_marker = (
        _short_phrase(lighting.get("celestialMarker"), 72) or "preserve_previous"
    )
    anchors = [
        _short_phrase(item, 14)
        for item in state.get("sceneAnchors", [])[:2]
        if _short_phrase(item, 14)
    ]
    anchor_text = f" Anchors: {', '.join(anchors)}." if anchors else ""
    return (
        f"{label}: {time_value}, {scene_value}; {brightness_text}; "
        f"lamp {lamp_state}; sky {sky_state}; time marker: {celestial_marker}."
        f"{anchor_text} Keep this as one compact local paper-cut scene."
    )


def _relationship_layout_state(five_layer_plan: dict[str, Any]) -> dict[str, Any]:
    l2 = five_layer_plan.get("L2_relational_structure_layer")
    if not isinstance(l2, dict):
        return {}
    if isinstance(l2.get("layoutState"), dict):
        return l2["layoutState"]
    controls = l2.get("deterministicControls")
    if isinstance(controls, dict) and isinstance(controls.get("layoutState"), dict):
        return controls["layoutState"]
    return {}


def _layout_anchor(layout: dict[str, Any], role: str) -> list[float]:
    fallback = [0.375, 0.642] if role == "elder" else [0.57, 0.393]
    value = layout.get("elderAnchor" if role == "elder" else "childAnchor")
    if not isinstance(value, list) or len(value) != 2:
        return fallback
    try:
        return [round(float(value[0]), 3), round(float(value[1]), 3)]
    except (TypeError, ValueError):
        return fallback


def _platform_anchor(layout: dict[str, Any], role: str) -> list[float]:
    fallback = [0.405, 0.6] if role == "elder" else [0.595, 0.44]
    value = layout.get("elderPlatformAnchor" if role == "elder" else "childPlatformAnchor")
    if not isinstance(value, list) or len(value) != 2:
        return fallback
    try:
        return [round(float(value[0]), 3), round(float(value[1]), 3)]
    except (TypeError, ValueError):
        return fallback


def _merged_non_speaker_preset(role: str, anchor: list[float]) -> str:
    if role == "elder":
        activity = "gently tending a small shared group of flowers"
        label = "older adult parent"
    else:
        activity = "quietly reading an open book"
        label = "adult son or daughter"
    return (
        f"The {label} is {activity} inside the same shared living environment, "
        f"near normalized anchor {anchor}. Preserve a natural inward-facing posture "
        "and keep the eyes on the activity rather than toward the camera."
    )


def _relationship_growth(five_layer_plan: dict[str, Any]) -> str:
    l2 = five_layer_plan.get("L2_relational_structure_layer")
    controls = l2.get("deterministicControls") if isinstance(l2, dict) else None
    if not isinstance(controls, dict):
        return _short_phrase(_layer_content(five_layer_plan, "L2_relational_structure_layer"), 220)
    values = controls.get("controls") if isinstance(controls.get("controls"), dict) else {}
    layout = _relationship_layout_state(five_layer_plan)
    palette = ", ".join(
        _short_phrase(item, 16)
        for item in controls.get("flowerColorPalette", [])[:3]
        if _short_phrase(item, 16)
    )
    return (
        f"Relationship score {controls.get('relationshipScore', 0.5)}. "
        f"Spatial mode {controls.get('spatialMode', 'connected')}. "
        f"Flower density {values.get('flowerDensity', 0.5)}, bloom {values.get('bloomRatio', 0.5)}, "
        f"zone gap {values.get('zoneGapRatio', 0.25)}, shared space {values.get('sharedSpaceRatio', 0.15)}, "
        f"path length {values.get('pathLength', 0.55)}, width {values.get('pathWidth', 0.07)}, "
        f"brightness {values.get('connectionBrightness', 0.55)}."
        f" Character layout: elder anchor {_layout_anchor(layout, 'elder')}, "
        f"child anchor {_layout_anchor(layout, 'child')}, person gap {layout.get('personGapRatio', 0.228)}, "
        f"shared scene {layout.get('sharedSceneRatio', 0.65)}, "
        f"environment merge {layout.get('environmentMergeRatio', 0.675)}. "
        f"Platform layout: elder platform anchor {_platform_anchor(layout, 'elder')}, "
        f"child platform anchor {_platform_anchor(layout, 'child')}, "
        f"platform gap {layout.get('platformGapRatio', 0.0)}, "
        f"platform overlap {layout.get('platformOverlapRatio', 0.165)}, "
        f"shared ground {layout.get('sharedGroundRatio', 0.688)}, "
        f"central feature {layout.get('centralFeature', 'river_and_path')}."
        + (f" Flowers use only {palette}." if palette else "")
        + (
            " The two complete living platforms must move inward, touch or slightly overlap, "
            "and become one shared ground plane. Remove every existing road, path, river, "
            "stream, bridge, ravine or empty separator from between the two people."
            if controls.get("spatialMode") == "merged"
            else ""
        )
    )


def _relationship_spatial_mode(five_layer_plan: dict[str, Any]) -> str:
    l2 = five_layer_plan.get("L2_relational_structure_layer")
    controls = l2.get("deterministicControls") if isinstance(l2, dict) else None
    if not isinstance(controls, dict):
        return "connected"
    return str(controls.get("spatialMode") or "connected")


def _central_feature_rule(spatial_mode: str) -> str:
    return {
        "merged": (
            "Use one continuous shared ground plane. Remove every road, path, river, "
            "stream, bridge, ravine and empty separator between the people."
        ),
        "path_only": (
            "Keep exactly one short, narrow everyday footpath between the two close "
            "platforms. Remove all river water, streams, bridges and ravines."
        ),
        "river_and_path": (
            "Keep both a readable river and a modest footpath in the middle-distance "
            "relationship zone; neither element may disappear. Let both features weave "
            "naturally through the same edge-to-edge landscape. They must not enclose the "
            "scene, form an outer model rim or turn either living area into a boxed island."
        ),
        "river_only": (
            "Keep river water as the only central distance element. Remove every "
            "connecting road, footpath and bridge."
        ),
        "peripheral": (
            "Keep one broad, clearly visible river as the central distance element. Push both "
            "platforms apart while both people remain inside the central display-safe composition. "
            "Remove every direct road, footpath and bridge across the river."
        ),
    }.get(
        spatial_mode,
        "Keep both a readable river and a modest footpath in the central relationship zone.",
    )


def _shared_transition(five_layer_plan: dict[str, Any]) -> str:
    spatial_mode = _relationship_spatial_mode(five_layer_plan)
    if spatial_mode == "merged":
        return (
            "Move both complete living platforms, their floors, edges, furniture, plants and "
            "people inward into one continuous shared paper-cut space. Make the platform edges "
            "touch or slightly overlap. Remove the existing central road, path, river, stream, "
            "bridge, ravine and empty separator. Replace that entire divider with one continuous "
            "shared courtyard floor, flowers and overlapping paper ground layers; no trench, "
            "split screen, panel edge or abrupt texture seam."
        )
    if spatial_mode == "path_only":
        return (
            "Bring both living platforms clearly closer. Join them only with one short, "
            "narrow paper footpath. Remove all river water, streams, bridges, ravines and "
            "wide landscape gaps. Continue flowers and paper terrain softly around the path."
        )
    if spatial_mode == "river_only":
        return (
            "Pull both living platforms farther apart. Keep only a calm paper river between "
            "them; remove all connecting roads, footpaths and bridges. Blend both banks into "
            "the same paper world without making the platforms appear close."
        )
    if spatial_mode == "peripheral":
        return (
            "Keep both living platforms at their widest relationship separation, but keep both "
            "people fully inside the central display-safe composition. Keep one broad, clearly "
            "visible paper river between them as the distance separator, but no direct road, "
            "footpath or bridge."
        )
    return (
        "Keep both a river and a readable modest footpath between the two living platforms. "
        "Let the river and path weave naturally through the continuous full-frame terrain; "
        "do not wrap them around an inset tray or isolated model base. Use overlapping hills "
        "and paper layers to keep one continuous paper world; no vertical day-night divider, "
        "split screen, panel edge or abrupt texture seam."
    )


def _five_layer_values(
    *,
    five_layer_plan: dict[str, Any],
    semantic_visual_instruction: str,
    speaker_role: str,
) -> dict[str, Any]:
    """Read the current five-layer design directly; no mapper or LLM call."""

    speaker = normalize_role(speaker_role)
    environment = _layer_content(five_layer_plan, "L1_environment_layer")
    relationship = _layer_content(
        five_layer_plan,
        "L2_relational_structure_layer",
    )
    relationship = "\n".join(
        line
        for line in relationship.splitlines()
        if not line.strip().startswith("关系结构使用确定性参数")
    ).strip()
    event = _layer_content(five_layer_plan, "L3_object_event_layer")
    character = _layer_content(five_layer_plan, "L4_character_layer")
    feedback = _layer_content(five_layer_plan, "L5_motion_feedback_layer")
    fallback = _paper_craft_text(semantic_visual_instruction)
    role_states = _role_visual_states(five_layer_plan)
    layout = _relationship_layout_state(five_layer_plan)

    speaker_description = "\n".join(
        value for value in (event, character) if value
    ) or fallback
    spatial_mode = _relationship_spatial_mode(five_layer_plan)
    merged_relationship_space = spatial_mode == "merged"
    dynamic_relationship_layout = spatial_mode in {
        "merged",
        "path_only",
        "river_and_path",
        "river_only",
        "peripheral",
    }
    elder_anchor = _layout_anchor(layout, "elder")
    child_anchor = _layout_anchor(layout, "child")
    person_gap = layout.get("personGapRatio", 0.228)
    shared_scene = layout.get("sharedSceneRatio", 0.65)
    environment_merge = layout.get("environmentMergeRatio", 0.675)
    elder_platform_anchor = _platform_anchor(layout, "elder")
    child_platform_anchor = _platform_anchor(layout, "child")
    platform_gap = layout.get("platformGapRatio", 0.0)
    platform_overlap = layout.get("platformOverlapRatio", 0.165)
    shared_ground = layout.get("sharedGroundRatio", 0.688)
    central_connection = (
        "Move both complete living platforms inward until their edges touch or slightly "
        "overlap and become one continuous shared ground plane. Remove the existing road, "
        "path, river, stream, bridge, ravine and empty band from between the two people. "
        "Replace the former divider with continuous courtyard floor, flowers and overlapping "
        "paper ground layers."
        + (f"\n{feedback}" if feedback else "")
        if merged_relationship_space
        else (
            _central_feature_rule(spatial_mode)
            + (f"\n{feedback}" if feedback else "")
            if dynamic_relationship_layout
            else "\n".join(value for value in (relationship, feedback) if value)
        )
    )
    elder_description = speaker_description if speaker == "elder" else (
        _merged_non_speaker_preset("elder", elder_anchor)
        if merged_relationship_space
        else _NON_SPEAKER_PRESETS["elder"]
    )
    child_description = speaker_description if speaker == "child" else (
        _merged_non_speaker_preset("child", child_anchor)
        if merged_relationship_space
        else _NON_SPEAKER_PRESETS["child"]
    )

    if merged_relationship_space:
        first_composition_rule = (
            "Place both family members inside one continuous shared living environment, not in "
            "separate corner zones. Use normalized anchors as soft position targets: older adult "
            f"near {elder_anchor}, younger adult near {child_anchor}. Keep their visible separation "
            f"near personGapRatio={person_gap}. The shared environment occupies about "
            f"sharedSceneRatio={shared_scene}, with environmentMergeRatio={environment_merge}. "
            f"Move the entire older-adult platform toward {elder_platform_anchor} and the entire "
            f"younger-adult platform toward {child_platform_anchor}; platformGapRatio={platform_gap}, "
            f"platformOverlapRatio={platform_overlap}, sharedGroundRatio={shared_ground}. "
            "Platform means its floor, raised edge, nearby furniture, plants, activity props and "
            "person. The two platform edges must touch or overlap; they cannot remain two islands. "
            "Keep the older adult slightly left and lower than the younger adult for role stability, "
            "while moving both inward toward the shared activity space."
        )
        update_composition_rule = (
            "Reflow the existing two people into one shared continuous living environment. Move "
            f"the older adult toward normalized anchor {elder_anchor} and the younger adult toward "
            f"{child_anchor}; target personGapRatio={person_gap}. Their local scene details must "
            f"overlap into one shared scene at ratio {shared_scene}. Do not preserve their former "
            f"distant corner positions. Move the entire older-adult platform toward {elder_platform_anchor} "
            f"and the entire younger-adult platform toward {child_platform_anchor}; target platformGapRatio="
            f"{platform_gap}, platformOverlapRatio={platform_overlap}, sharedGroundRatio={shared_ground}. "
            "Move each floor, raised edge, nearby furniture, plants and activity props with its person. "
            "Join both platform edges into one ground plane and erase the river, stream, road, path, "
            "bridge or ravine between them. Preserve identities, activities and relative elder-left/lower "
            "and child-right/upper order while allowing both bodies and nearby props to move inward."
        )
        target_anchor = elder_anchor if speaker == "elder" else child_anchor
        target_location_rule = (
            f"The target character is the {speaker} family member inside the shared living space, "
            f"moving toward normalized anchor {target_anchor}."
        )
        other_continuity_rule = (
            "Preserve the other family member's identity and established main activity, but allow "
            "that person and nearby props to move inward toward the dynamic shared-space anchor. "
            "Do not lock the person to the previous distant corner or isolated personal zone."
        )
        orientation_rule = (
            "Both figures inhabit the same shared scene and orient naturally toward their own "
            "activities or the shared activity area. Do not arrange them as a posed reunion photo, "
            "but do make their physical proximity clearly closer than in separated mode."
        )
        position_negative_rule = (
            "- No isolated lower-left and upper-right personal rooms.\n"
            "- No large empty band, path zone or landscape gap between the people.\n"
            "- No river, stream, road, path, bridge or ravine between the people.\n"
            "- No two detached or cliff-separated living platforms.\n"
            "- Do not keep either person pinned to an outer corner.\n"
            "- Do not swap their relative elder-left/lower and child-right/upper order."
        )
        elder_environment_label = "Older adult contribution to the shared environment"
        child_environment_label = "Younger adult contribution to the shared environment"
    elif dynamic_relationship_layout:
        feature_rule = _central_feature_rule(spatial_mode)
        first_composition_rule = (
            f"Use relationship spatial mode {spatial_mode}. Place the older adult near "
            f"{elder_anchor} and the younger adult near {child_anchor}; target personGapRatio="
            f"{person_gap}. Move the complete older-adult platform toward {elder_platform_anchor} "
            f"and the complete younger-adult platform toward {child_platform_anchor}; target "
            f"platformGapRatio={platform_gap}, platformOverlapRatio={platform_overlap}, "
            f"sharedGroundRatio={shared_ground}. Platform includes its floor, raised edge, nearby "
            f"furniture, plants, activity props and person. {feature_rule}"
        )
        update_composition_rule = (
            f"Reflow both existing people and both complete living platforms into spatial mode "
            f"{spatial_mode}. Move the older adult toward {elder_anchor} and younger adult toward "
            f"{child_anchor}; target personGapRatio={person_gap}. Move the complete platforms toward "
            f"{elder_platform_anchor} and {child_platform_anchor}; platformGapRatio={platform_gap}, "
            f"platformOverlapRatio={platform_overlap}, sharedGroundRatio={shared_ground}. Move each "
            f"floor, raised edge, furniture, plants and activity props with its person. {feature_rule}"
        )
        target_anchor = elder_anchor if speaker == "elder" else child_anchor
        target_location_rule = (
            f"The target character is the {speaker} family member moving toward normalized "
            f"anchor {target_anchor} in spatial mode {spatial_mode}."
        )
        other_continuity_rule = (
            "Preserve the other family member's identity and main activity, while moving that "
            "person, platform, nearby furniture and props to the specified relationship layout."
        )
        orientation_rule = (
            "Keep both figures naturally oriented toward their own activities or the relationship "
            "space, never posed toward the camera."
        )
        mode_negative = {
            "path_only": "- No river, stream, bridge or ravine between the people.\n- No long or wide path.",
            "river_and_path": "- Do not omit either the river or the modest footpath.",
            "river_only": "- No road, footpath or bridge connecting the platforms.",
            "peripheral": "- Do not merge the two platforms or omit the broad central river.\n- Do not push either person outside the central display-safe composition.\n- No direct road, footpath or bridge.",
        }.get(spatial_mode, "")
        position_negative_rule = (
            mode_negative
            + "\n- Do not ignore the platform anchors or move only the people.\n"
            "- Do not swap their relative elder-left/lower and child-right/upper order."
        ).strip()
        elder_environment_label = "Older adult living platform"
        child_environment_label = "Younger adult living platform"
    else:
        first_composition_rule = (
            "Place the older adult in the lower-left personal zone.\n"
            "Place the younger adult in the upper-right personal zone.\n"
            "Keep the upper-right personal zone slightly larger, around 1.1-1.2 times the lower-left zone.\n"
            "Keep both people at comparable and clearly readable visual scales.\n"
            "Maintain one visible central route, stream, paper path or other continuous connection between their personal zones."
        )
        update_composition_rule = (
            "Keep the older adult associated with the lower-left personal zone and the younger "
            "adult associated with the upper-right personal zone.\nMaintain one visible central "
            "route, stream, paper path or other continuous connection between their personal zones."
        )
        target_location_rule = (
            "The target character is located in the lower-left personal zone."
            if speaker == "elder"
            else "The target character is located in the upper-right personal zone."
        )
        other_continuity_rule = (
            "Preserve the other family member exactly as visibly established in Image A. Keep the "
            "same approximate personal zone and the same main activity."
        )
        orientation_rule = (
            "Keep both characters naturally oriented toward the shared center or their current "
            "activity props. Do not point either character toward the outside border. Do not "
            "arrange them back-to-back."
        )
        position_negative_rule = (
            "- No elder in the upper-right as the primary figure.\n"
            "- No child in the lower-left as the primary figure."
        )
        elder_environment_label = "Older adult lower-left environment"
        child_environment_label = "Younger adult upper-right environment"

    character_anchor_priority_rule = (
        " Character body anchors are authoritative and higher priority than platform-center "
        "anchors. A platform may extend farther outward, but place each person on its inward "
        "portion so the person's body center remains at the specified elder/child anchor. "
        "Never drag a person back toward an outer edge merely to center them on a platform. "
        "Keep both body centers within x=0.30-0.70 even when a platform extends farther outward."
    )
    first_composition_rule += character_anchor_priority_rule
    update_composition_rule += character_anchor_priority_rule

    child_inset_rule = (
        "Treat the younger-adult anchor as the center of the body, not the right edge. "
        "Keep the complete head, hair, shoulders, arms and activity props inside the canvas, "
        "with at least 8% of canvas width as visible environmental breathing room between "
        "the younger-adult silhouette and the right canvas edge."
    )
    first_composition_rule += " " + child_inset_rule
    update_composition_rule += " " + child_inset_rule
    position_negative_rule = (
        position_negative_rule
        + "\n- No younger-adult hair, face, shoulder, arm or body touching or clipped by the right canvas edge."
    )

    return {
        "elder_environment": _compact_role_environment(
            role_states.get("elder", {}),
            label=elder_environment_label,
            fallback=environment or fallback,
        ),
        "child_environment": _compact_role_environment(
            role_states.get("child", {}),
            label=child_environment_label,
            fallback=environment or fallback,
        ),
        "shared_transition": _shared_transition(five_layer_plan),
        "relationship_growth": _relationship_growth(five_layer_plan),
        "elder_description": elder_description,
        "child_description": child_description,
        "orientation_and_visual_flow": character,
        "central_connection": central_connection,
        "first_composition_rule": first_composition_rule,
        "update_composition_rule": update_composition_rule,
        "target_location_rule": target_location_rule,
        "other_continuity_rule": other_continuity_rule,
        "orientation_rule": orientation_rule,
        "position_negative_rule": position_negative_rule,
        "composition_connection_rule": (
            "Make the lower-left and upper-right personal zones directly touch and merge. "
            "Leave no empty central gap. A road, river, stream or bridge is optional, not required."
            if merged_relationship_space
            else (
                _central_feature_rule(spatial_mode)
                if dynamic_relationship_layout
                else "Maintain one visible central route, stream, paper path or other continuous connection between their personal zones."
            )
        ),
        "central_connection_suffix": (
            "Keep the shared center continuous and filled; do not reintroduce separation with a river, road, trench or blank strip."
            if merged_relationship_space
            else (
                "Render exactly the central feature required by the selected spatial mode."
                if dynamic_relationship_layout
                else "Keep the connection visually readable and continuous."
            )
        ),
        "target_character_update": speaker_description,
        "other_character_continuity": (
            "Preserve the other family member exactly as visibly established "
            "in Image A."
        ),
    }


def _environment_section(environment_adjustment: str, *, first_voice: bool) -> str:
    environment_adjustment = environment_adjustment.strip()
    if environment_adjustment:
        suffix = (
            "Use Image A as the existing environmental and compositional foundation. "
            "Integrate both people and supported scene changes into it rather than replacing it."
            if first_voice
            else ""
        )
        return (
            "[Environment Adjustment]\n"
            + environment_adjustment
            + "\n\nApply only the environment changes explicitly described in "
            "[Environment Adjustment]. Do not invent new weather, lighting, "
            "architecture or time-of-day changes unless they are described in "
            "that section. "
            + suffix
        ).strip()
    if first_voice:
        return (
            "[Environment Adjustment]\n"
            "No additional environment change is required.\n"
            "Maintain visual continuity with Image A.\n\n"
            "Use Image A as the existing environmental and compositional foundation. "
            "Integrate both people into it rather than replacing the existing scene."
        )
    return (
        "[Environment Adjustment]\n"
        "No additional environment change is required.\n"
        "Maintain visual continuity with Image A."
    )


def compile_for_both(
    *,
    five_layer_plan: dict[str, Any],
    semantic_visual_instruction: str,
    speaker_role: str,
) -> str:
    """Compile the migrated First Voice prompt from the current five layers."""

    values = _five_layer_values(
        five_layer_plan=five_layer_plan,
        semantic_visual_instruction=semantic_visual_instruction,
        speaker_role=speaker_role,
    )
    spatial_mode = _relationship_spatial_mode(five_layer_plan)
    canvas_contract = (
        RELATIONSHIP_REFLOW_CONTRACT
        if spatial_mode in {"merged", "path_only", "river_and_path", "river_only", "peripheral"}
        else CANVAS_CONTRACT
    )
    parts = [
        "[Operation]\n"
        "Edit Image A and create one complete TimeWallpaper shared scene.\n"
        "Image A is the environmental base image.\n"
        "Image B is the older adult identity reference.\n"
        "Image C is the younger adult identity reference.\n"
        "Insert exactly two family members and integrate both into the "
        "existing scene as layered paper-cut characters.",
        "[Style]\n" + VINTAGE_PAPER_CRAFT_V1_STYLE,
        canvas_contract,
        "[Identity]\n"
        "Preserve the recognizable identity, apparent age, overall face "
        "silhouette, hairstyle silhouette and color, general body build and "
        "core clothing cues of both references. Do not copy their exact "
        "photographic facial geometry or facial ratios.\n"
        "Image B corresponds only to the older adult.\n"
        "Image C corresponds only to the younger adult.\n"
        "Translate both people into the same layered cut-paper visual "
        "language as Image A.\n"
        "Use the references only for identity, never for photographic "
        "style, background, lighting or original pose.",
        CARTOON_CHARACTER_CONTRACT,
        CHARACTER_FRAMING_CONTRACT,
        CHARACTER_DISPLAY_SAFE_ZONE_CONTRACT,
        FULL_FRAME_LANDSCAPE_CONTRACT,
        _local_time_lighting_contract(five_layer_plan),
        "[Elder Environment]\n" + values["elder_environment"],
        "[Child Environment]\n" + values["child_environment"],
        "[Shared Transition]\n" + values["shared_transition"],
        "[Relationship Growth]\n" + values["relationship_growth"],
        "[Composition]\n"
        + values["first_composition_rule"]
        + "\nDo not treat these as rigid masks or exact coordinate boxes.",
        "[Older Adult]\n" + values["elder_description"],
        "[Younger Adult]\n" + values["child_description"],
        "[Body Orientation and Visual Flow]\n"
        + values["orientation_and_visual_flow"]
        + "\n"
        + values["orientation_rule"]
        + "\n"
        "No forced direct eye contact is required.",
        "[Central Connection]\n"
        + values["central_connection"]
        + "\n"
        + values["central_connection_suffix"],
        "[Negative]\n"
        "- No additional humans.\n"
        "- No duplicated identities.\n"
        "- No swapped identity references.\n"
        + values["position_negative_rule"]
        + "\n"
        "- No outward-facing or back-to-back arrangement.\n"
        "- No forced direct eye contact.\n"
        "- No oversized person above 42% of full-canvas height and no zoomed portrait crop.\n"
        "- No platform rim, terrain edge or decorative foreground layer cutting a person at the torso or waist.\n"
        "- No floating half-body emerging from a living platform.\n"
        "- No photorealistic faces.\n"
        "- No semi-realistic portrait faces or realistic facial anatomy.\n"
        "- No sharp sculpted facial planes, modeled lips or visible nostrils.\n"
        "- No skin pores, dense eyelashes or realistic wrinkles.\n"
        "- No individual hair strands or photographic hair highlights.\n"
        "- No realistic skin shading.\n"
        "- No watercolor as the main medium.\n"
        "- No clay or plastic texture.\n"
        "- No isolated miniature diorama, freestanding tray, boxed stage, pedestal or framed display.\n"
        "- No blank cream-paper margin surrounding a shrunken scene.\n"
        "- No outer picture frame, matte, mount, white rim, torn-paper outline, backing sheet or nested canvas.\n"
        "- No living platform, platform rim, primary furniture or person body center in the outer 20% bands.\n"
        "- No unrelated full-background reconstruction.\n"
        "- No phone call or video-call interface unless explicitly "
        "required by the current event.\n"
        "- No text, labels, UI or watermark.",
    ]
    prompt = "\n\n".join(parts)
    issues = audit_prompt(prompt)
    if issues:
        raise ValueError("Compiled V2 prompt contains prohibited tokens: " + ", ".join(issues))
    return prompt


def compile_for_update(
    *,
    five_layer_plan: dict[str, Any],
    semantic_visual_instruction: str,
    speaker_role: str,
) -> str:
    """Compile the migrated Subsequent Update prompt from the five layers."""

    speaker = normalize_role(speaker_role)
    if speaker == "elder":
        speaker_label = "older adult parent"
        other_label = "adult son or daughter"
    else:
        speaker_label = "adult son or daughter"
        other_label = "older adult parent"

    values = _five_layer_values(
        five_layer_plan=five_layer_plan,
        semantic_visual_instruction=semantic_visual_instruction,
        speaker_role=speaker,
    )
    parts = [
        "[Operation]\n"
        "Edit Image A, the latest successful TimeWallpaper shared wallpaper.\n"
        f"Image B is the identity reference for the existing {speaker_label}.\n"
        f"Update the existing {speaker_label} and the scene details "
        "according to the visual changes described in this prompt.\n"
        "Do not insert a new copy of this person.",
        "[Style]\n" + VINTAGE_PAPER_CRAFT_V1_STYLE,
        CANVAS_CONTRACT,
        "[Identity]\n"
        f"Use Image B only to preserve the existing {speaker_label} "
        "identity: the same recognizable person, apparent age, overall face "
        "silhouette, hairstyle silhouette and color, general body build and "
        "core clothing cues. Do not copy the reference's exact photographic "
        "facial geometry or facial ratios.\n"
        f"Keep this person visually consistent with the existing "
        "layered paper-crafted character in Image A.\n"
        "Do not copy Image B's photographic texture, pose, background "
        "or lighting.",
        CARTOON_CHARACTER_CONTRACT,
        CHARACTER_FRAMING_CONTRACT,
        CHARACTER_DISPLAY_SAFE_ZONE_CONTRACT,
        FULL_FRAME_LANDSCAPE_CONTRACT,
        _local_time_lighting_contract(five_layer_plan),
        "[Elder Environment]\n" + values["elder_environment"],
        "[Child Environment]\n" + values["child_environment"],
        "[Shared Transition]\n" + values["shared_transition"],
        "[Relationship Growth]\n" + values["relationship_growth"],
        "[Current Character Update]\n"
        + values["target_location_rule"]
        + "\n"
        + values["target_character_update"]
        + "\nThe latest current action replaces the character's previous "
        "main action.\n"
        "Do not retain completed or abandoned previous activity props "
        "as the main visual focus.",
        "[Other Family Member Continuity]\n"
        + values["other_continuity_rule"]
        + f"\nKeep the existing {other_label} recognizable.\n"
        "Do not replace, duplicate, remove or give this person a new "
        "unrelated activity.",
        "[Composition and Body Orientation]\n"
        + values["update_composition_rule"]
        + "\n"
        + values["orientation_rule"],
        "[Central Connection]\n"
        + values["central_connection"]
        + "\n"
        + values["central_connection_suffix"],
        "[Negative]\n"
        "- No additional humans.\n"
        "- No duplicated target character.\n"
        "- No swapped character roles.\n"
        + values["position_negative_rule"]
        + "\n"
        "- No disappearance or replacement of the other family member.\n"
        "- No unrelated new activity for the other family member.\n"
        "- No outward-facing or back-to-back arrangement.\n"
        "- No platform rim, terrain edge or decorative foreground layer cutting a person at the torso or waist.\n"
        "- No floating half-body emerging from a living platform.\n"
        "- No photorealistic people.\n"
        "- No semi-realistic portrait faces or realistic facial anatomy.\n"
        "- No sharp sculpted facial planes, modeled lips or visible nostrils.\n"
        "- No skin pores, dense eyelashes or realistic wrinkles.\n"
        "- No individual hair strands or photographic hair highlights.\n"
        "- No realistic skin shading.\n"
        "- No watercolor as the main style.\n"
        "- No clay, plastic or glossy 3D texture.\n"
        "- No isolated miniature diorama, freestanding tray, boxed stage, pedestal or framed display.\n"
        "- No blank cream-paper margin surrounding a shrunken scene.\n"
        "- No outer picture frame, matte, mount, white rim, torn-paper outline, backing sheet or nested canvas.\n"
        "- No living platform, platform rim, primary furniture or person body center in the outer 20% bands.\n"
        "- No unrelated complete scene replacement.\n"
        "- No text, labels, UI or watermark.",
    ]
    prompt = "\n\n".join(parts)
    issues = audit_prompt(prompt)
    if issues:
        raise ValueError("Compiled V2 prompt contains prohibited tokens: " + ", ".join(issues))
    return prompt


def compile_for_reflow(
    *,
    five_layer_plan: dict[str, Any],
    semantic_visual_instruction: str,
    speaker_role: str,
) -> str:
    """Compile a two-identity update that can move both existing people."""

    speaker = normalize_role(speaker_role)
    values = _five_layer_values(
        five_layer_plan=five_layer_plan,
        semantic_visual_instruction=semantic_visual_instruction,
        speaker_role=speaker,
    )
    speaker_label = (
        "older adult parent" if speaker == "elder" else "adult son or daughter"
    )
    parts = [
        "[Operation]\n"
        "Edit Image A, the latest successful TimeWallpaper shared wallpaper.\n"
        "Image B is the older adult identity reference.\n"
        "Image C is the younger adult identity reference.\n"
        "Re-layout the two existing family members, both complete living platforms and "
        "their nearby activity props according to the requested relationship spatial mode. "
        "Do not insert replacement copies.",
        "[Style]\n" + VINTAGE_PAPER_CRAFT_V1_STYLE,
        RELATIONSHIP_REFLOW_CONTRACT,
        "[Identity]\n"
        "Preserve both existing identities, apparent ages, rounded face and hairstyle "
        "silhouettes, general body builds and core clothing cues. Image B corresponds "
        "only to the older adult; Image C corresponds only to the younger adult. Use "
        "both references only for identity, never for photographic style, background, "
        "lighting or pose. Keep exactly one instance of each person.",
        CARTOON_CHARACTER_CONTRACT,
        CHARACTER_FRAMING_CONTRACT,
        CHARACTER_DISPLAY_SAFE_ZONE_CONTRACT,
        FULL_FRAME_LANDSCAPE_CONTRACT,
        _local_time_lighting_contract(five_layer_plan),
        "[Elder Environment]\n" + values["elder_environment"],
        "[Child Environment]\n" + values["child_environment"],
        "[Shared Transition]\n" + values["shared_transition"],
        "[Relationship Growth]\n" + values["relationship_growth"],
        "[Relationship Reflow]\n"
        + values["update_composition_rule"]
        + "\nMove both complete platforms and both existing figures when needed; never "
        "move only the bodies while leaving their floors and scene boundaries behind. "
        "Treat normalized anchors as soft layout targets, not visible boxes or masks.",
        "[Current Speaker Event]\n"
        f"The current speaker is the {speaker_label}.\n"
        + values["target_character_update"]
        + "\nThe current action replaces that person's previous main action. Remove "
        "completed or abandoned activity props from the main visual focus.",
        "[Other Family Member Continuity]\n"
        + values["other_continuity_rule"]
        + "\nKeep the other person's established main activity unless the shared-space "
        "reflow requires a small natural pose adjustment.",
        "[Body Orientation and Visual Flow]\n"
        + values["orientation_and_visual_flow"]
        + "\n"
        + values["orientation_rule"],
        "[Central Connection]\n"
        + values["central_connection"]
        + "\n"
        + values["central_connection_suffix"],
        "[Negative]\n"
        "- No additional humans or duplicated identities.\n"
        "- No swapped identity references.\n"
        + values["position_negative_rule"]
        + "\n"
        "- No disappearance or replacement of either family member.\n"
        "- No posed reunion photo or forced direct eye contact.\n"
        "- No platform rim, terrain edge or decorative foreground layer cutting a person at the torso or waist.\n"
        "- No floating half-body emerging from a living platform.\n"
        "- No photorealistic or semi-realistic portrait faces.\n"
        "- No sharp facial planes, modeled lips, visible nostrils or skin pores.\n"
        "- No individual hair strands or photographic hair highlights.\n"
        "- No watercolor as the main style.\n"
        "- No clay, plastic or glossy 3D texture.\n"
        "- No isolated miniature diorama, freestanding tray, boxed stage, pedestal or framed display.\n"
        "- No blank cream-paper margin surrounding a shrunken scene.\n"
        "- No outer picture frame, matte, mount, white rim, torn-paper outline, backing sheet or nested canvas.\n"
        "- No living platform, platform rim, primary furniture or person body center in the outer 20% bands.\n"
        "- No unrelated complete scene replacement.\n"
        "- No text, labels, UI or watermark.",
    ]
    prompt = "\n\n".join(parts)
    issues = audit_prompt(prompt)
    if issues:
        raise ValueError("Compiled V2 prompt contains prohibited tokens: " + ", ".join(issues))
    return prompt


__all__ = [
    "CANVAS_CONTRACT",
    "CARTOON_CHARACTER_CONTRACT",
    "CHARACTER_DISPLAY_SAFE_ZONE_CONTRACT",
    "RELATIONSHIP_REFLOW_CONTRACT",
    "PROHIBITED_TOKENS",
    "audit_prompt",
    "compile_for_both",
    "compile_for_reflow",
    "compile_for_update",
    "normalize_role",
]
