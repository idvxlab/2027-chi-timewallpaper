from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
sys.path.insert(0, str(BACKEND_ROOT))

from app.agents.semantic_mapping_agent import SemanticMappingAgent
from app.db.models import CharacterAsset, RelationshipProfile, UserProfile
from app.db.session import SessionLocal
from app.schemas.agent import AgentRunResult, SemanticMappingResult
from app.services.prompt_compiler_v2 import audit_prompt, compile_for_both
from app.services.semantic_graph import load_semantic_graph
from app.services.wallpaper_generation_service import wallpaper_generation_service
from app.services.wallpaper_render_worker import wallpaper_render_worker


DEFAULT_USERNAMES = ("5", "6")
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "tmp" / "visual-mapping-ab"

RELATIONSHIP_PRESETS: dict[str, dict[str, str]] = {
    "closest": {
        "intimacy_distance": "最近",
        "emotional_warmth": "温暖",
        "interaction_frequency": "经常联系",
        "reciprocity": "双向",
        "relationship_trend": "靠近",
    },
    "near": {
        "intimacy_distance": "近",
        "emotional_warmth": "温暖",
        "interaction_frequency": "经常联系",
        "reciprocity": "双向",
        "relationship_trend": "靠近",
    },
    "middle": {
        "intimacy_distance": "不近不远",
        "emotional_warmth": "稳定",
        "interaction_frequency": "偶尔联系",
        "reciprocity": "逐渐恢复",
        "relationship_trend": "稳定",
    },
    "far": {
        "intimacy_distance": "比较远",
        "emotional_warmth": "疏离",
        "interaction_frequency": "联系减少",
        "reciprocity": "单向",
        "relationship_trend": "暂时疏远",
    },
    "farthest": {
        "intimacy_distance": "最远",
        "emotional_warmth": "冷淡",
        "interaction_frequency": "长时间未联系",
        "reciprocity": "不平衡",
        "relationship_trend": "疏远",
    },
}


def cell(value: Any) -> dict[str, Any]:
    return {
        "value": value,
        "confidence": 1.0,
        "evidence": "visual A/B test fixture",
    }


def short_table(
    *,
    time: str,
    scene: str,
    event: str,
    objects: list[str],
    affect: str,
) -> dict[str, Any]:
    return {
        "tableName": "short_term_semantic_table",
        "agentRole": "Visual A/B Test Fixture",
        "A_situational_semantics": {
            "time": cell(time),
            "scene": cell(scene),
            "event": cell(event),
            "subject": cell(["当前说话者"]),
            "object": cell(objects),
        },
        "B_affective_semantics": {
            "momentary_affect": cell(affect),
            "affective_intensity": cell("轻微"),
            "affective_ambiguity": cell("明确"),
        },
        "C_communicative_semantics": {
            "intent_type": cell("分享生活"),
            "desired_response": cell("看见即可"),
            "disclosure_depth": cell("日常分享"),
        },
    }


def long_table(preset: str) -> dict[str, Any]:
    values = RELATIONSHIP_PRESETS[preset]
    return {
        "tableName": "long_term_relation_table",
        "agentRole": "Visual A/B Test Fixture",
        "E_relational_semantics": {
            field: cell(value) for field, value in values.items()
        },
    }


def resolve_relationship(
    usernames: tuple[str, str],
) -> tuple[RelationshipProfile, dict[str, UserProfile]]:
    with SessionLocal() as session:
        users = (
            session.query(UserProfile)
            .filter(UserProfile.display_name.in_(usernames))
            .all()
        )
        by_name: dict[str, list[UserProfile]] = {name: [] for name in usernames}
        for user in users:
            if user.display_name in by_name:
                by_name[user.display_name].append(user)
        invalid = {
            name: len(matches)
            for name, matches in by_name.items()
            if len(matches) != 1
        }
        if invalid:
            raise RuntimeError(
                "Each username must match exactly one user profile; "
                f"received counts={invalid}"
            )

        resolved = {name: matches[0] for name, matches in by_name.items()}
        user_ids = {user.user_id for user in resolved.values()}
        relationships = (
            session.query(RelationshipProfile)
            .filter(
                RelationshipProfile.parent_user_id.in_(user_ids),
                RelationshipProfile.child_user_id.in_(user_ids),
            )
            .all()
        )
        relationships = [
            row
            for row in relationships
            if {row.parent_user_id, row.child_user_id} == user_ids
        ]
        if len(relationships) != 1:
            raise RuntimeError(
                "The two usernames must share exactly one complete relationship; "
                f"matched={len(relationships)}"
            )

        relationship = relationships[0]
        session.expunge(relationship)
        for user in resolved.values():
            session.expunge(user)
        return relationship, resolved


def role_users(
    relationship: RelationshipProfile,
    users_by_name: dict[str, UserProfile],
) -> dict[str, UserProfile]:
    users_by_id = {user.user_id: user for user in users_by_name.values()}
    return {
        "elder": users_by_id[relationship.parent_user_id],
        "child": users_by_id[relationship.child_user_id],
    }


def character_references(relationship_id: str) -> dict[str, str]:
    with SessionLocal() as session:
        rows = (
            session.query(CharacterAsset)
            .filter(
                CharacterAsset.relationship_id == relationship_id,
                CharacterAsset.status == "ready",
                CharacterAsset.is_active.is_(True),
            )
            .order_by(CharacterAsset.updated_at.desc())
            .all()
        )
        references: dict[str, str] = {}
        for row in rows:
            if row.role in {"elder", "child"} and row.role not in references:
                references[row.role] = row.master_image_url
        return references


def build_case(
    preset: str,
    *,
    elder_time: str,
    elder_scene: str,
    child_time: str,
    child_scene: str,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    agent = SemanticMappingAgent()
    graph = load_semantic_graph()
    role_tables = {
        "elder": short_table(
            time=elder_time,
            scene=elder_scene,
            event="在超市挑选蔬菜并使用购物篮",
            objects=["购物篮", "蔬菜"],
            affect="平静",
        ),
        "child": short_table(
            time=child_time,
            scene=child_scene,
            event="下班离开办公室并关灯",
            objects=["电脑", "文件"],
            affect="疲惫",
        ),
    }
    role_sources = {
        role: {"source": "visual_ab_test_fixture", "usernameRole": role}
        for role in ("elder", "child")
    }
    role_states = agent._build_role_environment_states(
        graph=graph,
        role_short_tables=role_tables,
        role_short_table_sources=role_sources,
    )
    relationship_controls = agent._build_deterministic_l2_parameters(
        graph=graph,
        long_table=long_table(preset),
    )
    five_layer_plan = {
        "roleVisualState": {
            "elder": role_states["elder"],
            "child": role_states["child"],
            "activeRole": "elder",
            "updatePolicy": "visual_ab_initialize_both",
        },
        "L1_environment_layer": {
            "roleStates": role_states,
            "designContent": agent._role_environment_design_content(role_states),
        },
        "L2_relational_structure_layer": {
            "deterministicControls": relationship_controls,
            "designContent": [
                agent._deterministic_l2_design_content(relationship_controls)
            ],
        },
        "L3_object_event_layer": {
            "designContent": ["父母在超市挑选蔬菜，手持简化纸雕购物篮"]
        },
        "L4_character_layer": {
            "designContent": ["父母保持自然站姿，子女保持办公室中的自然生活状态"]
        },
        "L5_motion_feedback_layer": {
            "designContent": ["只依据L2确定性参数调整花木、共享空间和连接路径"]
        },
    }
    semantic_instruction = (
        f"视觉A/B测试-{preset}：保持相同人物、底图、两端时间与场景，"
        "只对比关系参数导致的花木、空间距离和连接路径变化。"
    )
    prompt = compile_for_both(
        five_layer_plan=five_layer_plan,
        semantic_visual_instruction=semantic_instruction,
        speaker_role="elder",
    )
    issues = audit_prompt(prompt)
    if issues:
        raise RuntimeError(f"Compiled prompt audit failed: {issues}")
    return five_layer_plan, relationship_controls, prompt


def write_preview(
    *,
    output_dir: Path,
    preset: str,
    relationship: RelationshipProfile,
    roles: dict[str, UserProfile],
    five_layer_plan: dict[str, Any],
    relationship_controls: dict[str, Any],
    prompt: str,
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{preset}.json"
    prompt_path = output_dir / f"{preset}-prompt.txt"
    payload = {
        "preset": preset,
        "relationshipId": relationship.relationship_id,
        "users": {
            role: {
                "username": user.display_name,
                "userId": user.user_id,
            }
            for role, user in roles.items()
        },
        "fiveLayerPlan": five_layer_plan,
        "relationshipControls": relationship_controls,
    }
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    prompt_path.write_text(prompt + "\n", encoding="utf-8")
    return json_path, prompt_path


async def render_case(
    *,
    preset: str,
    relationship: RelationshipProfile,
    roles: dict[str, UserProfile],
    five_layer_plan: dict[str, Any],
    references: dict[str, str],
) -> dict[str, Any]:
    missing = {"elder", "child"} - set(references)
    if missing:
        raise RuntimeError(
            f"Cannot render without ready character assets for roles={sorted(missing)}"
        )
    now = datetime.utcnow()
    run_id = f"visual-ab-{preset}-{uuid.uuid4().hex}"
    semantic = SemanticMappingResult(
        semantic_visual_instruction=(
            f"视觉A/B测试-{preset}：保持相同人物、底图、两端时间与场景，"
            "只对比关系参数导致的花木、空间距离和连接路径变化。"
        ),
        graph_version=load_semantic_graph().version,
        cognitive_scaffold={
            "fiveLayerPlan": five_layer_plan,
            "testMode": "visual_mapping_ab",
            "preset": preset,
        },
    )
    result = AgentRunResult(
        run_id=run_id,
        status="analyzed",
        user_id=roles["elder"].user_id,
        relationship_id=relationship.relationship_id,
        steps=[],
        semantic_mapping=semantic,
        created_at=now,
        updated_at=now,
    )
    tasks = wallpaper_generation_service.create_tasks(
        result,
        speaker_role="elder",
        generation_stage="first_voice",
        role_reference_images=references,
    )
    rendered = await wallpaper_render_worker.render_through(tasks.primary_task_id)
    return {
        "preset": preset,
        "runId": run_id,
        "eventSeq": tasks.event_seq,
        "taskId": tasks.primary_task_id,
        "wallpaperUrl": rendered.wallpaper_url,
        "generationMode": rendered.generation_mode,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Preview or render deterministic five-level L1+L2 relationship layouts "
            "for two users resolved by exact display name."
        )
    )
    parser.add_argument(
        "--usernames",
        nargs=2,
        default=DEFAULT_USERNAMES,
        metavar=("USER_A", "USER_B"),
        help="Exact user display names; default: 3 4",
    )
    parser.add_argument(
        "--preset",
        choices=("closest", "near", "middle", "far", "farthest", "both", "all"),
        default="all",
    )
    parser.add_argument("--elder-time", default="白天")
    parser.add_argument("--elder-scene", default="超市")
    parser.add_argument("--child-time", default="晚上")
    parser.add_argument("--child-scene", default="单位")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
    )
    parser.add_argument(
        "--render",
        action="store_true",
        help="Call Seedream and publish new wallpaper revisions (costs provider calls)",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Required with --render to confirm provider calls and current-page updates",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    usernames = tuple(args.usernames)
    relationship, users_by_name = resolve_relationship(usernames)
    roles = role_users(relationship, users_by_name)
    if args.preset == "all":
        presets = ("closest", "near", "middle", "far", "farthest")
    elif args.preset == "both":
        presets = ("near", "far")
    else:
        presets = (args.preset,)

    if args.render and not args.yes:
        raise RuntimeError(
            "--render makes paid Seedream calls and updates the current wallpaper; "
            "rerun with --render --yes after reviewing preview files."
        )

    print(
        json.dumps(
            {
                "relationshipId": relationship.relationship_id,
                "elder": {
                    "username": roles["elder"].display_name,
                    "userId": roles["elder"].user_id,
                },
                "child": {
                    "username": roles["child"].display_name,
                    "userId": roles["child"].user_id,
                },
                "presets": presets,
                "render": args.render,
            },
            ensure_ascii=False,
            indent=2,
        )
    )

    cases: dict[str, dict[str, Any]] = {}
    for preset in presets:
        plan, controls, prompt = build_case(
            preset,
            elder_time=args.elder_time,
            elder_scene=args.elder_scene,
            child_time=args.child_time,
            child_scene=args.child_scene,
        )
        json_path, prompt_path = write_preview(
            output_dir=args.output_dir,
            preset=preset,
            relationship=relationship,
            roles=roles,
            five_layer_plan=plan,
            relationship_controls=controls,
            prompt=prompt,
        )
        cases[preset] = {
            "plan": plan,
            "controls": controls,
            "jsonPath": str(json_path),
            "promptPath": str(prompt_path),
        }
        print(
            f"[{preset}] relationshipScore={controls['relationshipScore']} "
            f"spatialMode={controls['spatialMode']} "
            f"centralFeature={controls['layoutState']['centralFeature']} "
            f"flowerDensity={controls['controls']['flowerDensity']} "
            f"zoneGapRatio={controls['controls']['zoneGapRatio']} "
            f"preview={json_path}"
        )

    if args.render:
        references = character_references(relationship.relationship_id)
        render_results = []
        for preset in presets:
            render_results.append(
                asyncio.run(
                    render_case(
                        preset=preset,
                        relationship=relationship,
                        roles=roles,
                        five_layer_plan=cases[preset]["plan"],
                        references=references,
                    )
                )
            )
        result_path = args.output_dir / "render-results.json"
        result_path.write_text(
            json.dumps(render_results, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(json.dumps(render_results, ensure_ascii=False, indent=2))
        print(f"Render results saved to {result_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
