from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from test_visual_mapping_ab import (
    DEFAULT_USERNAMES,
    build_case,
    character_references,
    render_case,
    resolve_relationship,
    role_users,
    write_preview,
)
from app.db.models import WallpaperRevision
from app.db.session import SessionLocal


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "tmp" / "five-relationship-wallpapers"
PRESET_ORDER = ("closest", "near", "middle", "far", "farthest")


def persisted_revision_ids(
    *,
    relationship_id: str,
    event_seq: int,
    image_url: str,
) -> dict[str, str]:
    """Require one append-only revision per family view for this generated level."""

    with SessionLocal() as session:
        rows = (
            session.query(WallpaperRevision)
            .filter(
                WallpaperRevision.relationship_id == relationship_id,
                WallpaperRevision.event_seq == event_seq,
            )
            .all()
        )
    by_role = {row.view_role: row for row in rows}
    missing = {"elder", "child"} - set(by_role)
    if missing:
        raise RuntimeError(
            f"Generated event {event_seq} was not retained for both views; "
            f"missing={sorted(missing)}"
        )
    wrong_urls = {
        role: row.image_url
        for role, row in by_role.items()
        if row.image_url != image_url
    }
    if wrong_urls:
        raise RuntimeError(
            f"Generated event {event_seq} revision URLs do not match: {wrong_urls}"
        )
    return {role: row.revision_id for role, row in by_role.items()}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate the five relationship-distance wallpaper levels from the "
            "same clean base and publish them to both family members' wallpaper "
            "revision histories in deterministic order."
        )
    )
    parser.add_argument(
        "--usernames",
        nargs=2,
        default=DEFAULT_USERNAMES,
        metavar=("USER_A", "USER_B"),
        help="Exact display names of the two users; default: 5 6",
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
        "--yes",
        action="store_true",
        help=(
            "Actually make five paid Seedream calls and publish five wallpaper "
            "revisions. Without this flag the script only writes previews."
        ),
    )
    return parser.parse_args()


async def render_and_publish_all(
    *,
    relationship: Any,
    roles: dict[str, Any],
    cases: dict[str, dict[str, Any]],
    references: dict[str, str],
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for index, preset in enumerate(PRESET_ORDER, start=1):
        controls = cases[preset]["controls"]
        print(
            f"[{index}/5] generating {preset}: "
            f"mode={controls['spatialMode']} "
            f"centralFeature={controls['layoutState']['centralFeature']}"
        )
        rendered = await render_case(
            preset=preset,
            relationship=relationship,
            roles=roles,
            five_layer_plan=cases[preset]["plan"],
            references=references,
        )
        revision_ids = persisted_revision_ids(
            relationship_id=relationship.relationship_id,
            event_seq=rendered["eventSeq"],
            image_url=rendered["wallpaperUrl"],
        )
        results.append(
            {
                **rendered,
                "revisionIds": revision_ids,
                "relationshipScore": controls["relationshipScore"],
                "spatialMode": controls["spatialMode"],
                "centralFeature": controls["layoutState"]["centralFeature"],
                "personGapRatio": controls["layoutState"]["personGapRatio"],
                "platformGapRatio": controls["layoutState"]["platformGapRatio"],
            }
        )
        print(
            f"[{index}/5] retained {preset}: eventSeq={rendered['eventSeq']} "
            f"revisionIds={revision_ids} url={rendered['wallpaperUrl']}"
        )
    return results


def main() -> int:
    args = parse_args()
    relationship, users_by_name = resolve_relationship(tuple(args.usernames))
    roles = role_users(relationship, users_by_name)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    cases: dict[str, dict[str, Any]] = {}
    preview_items: list[dict[str, Any]] = []
    for preset in PRESET_ORDER:
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
        cases[preset] = {"plan": plan, "controls": controls}
        preview_items.append(
            {
                "preset": preset,
                "relationshipScore": controls["relationshipScore"],
                "spatialMode": controls["spatialMode"],
                "centralFeature": controls["layoutState"]["centralFeature"],
                "elderAnchor": controls["layoutState"]["elderAnchor"],
                "childAnchor": controls["layoutState"]["childAnchor"],
                "elderPlatformAnchor": controls["layoutState"]["elderPlatformAnchor"],
                "childPlatformAnchor": controls["layoutState"]["childPlatformAnchor"],
                "personGapRatio": controls["layoutState"]["personGapRatio"],
                "platformGapRatio": controls["layoutState"]["platformGapRatio"],
                "previewPath": str(json_path),
                "promptPath": str(prompt_path),
            }
        )

    preview_summary = {
        "relationshipId": relationship.relationship_id,
        "users": {
            role: {
                "username": user.display_name,
                "userId": user.user_id,
            }
            for role, user in roles.items()
        },
        "order": list(PRESET_ORDER),
        "sameCleanBaseForEveryLevel": True,
        "publishToBothViews": True,
        "latestAfterPublish": "farthest",
        "items": preview_items,
    }
    preview_path = args.output_dir / "preview-summary.json"
    preview_path.write_text(
        json.dumps(preview_summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(preview_summary, ensure_ascii=False, indent=2))
    print(f"Preview summary: {preview_path}")

    if not args.yes:
        print(
            "Preview only: no Seedream request or wallpaper revision was created. "
            "Rerun with --yes after reviewing the five prompts."
        )
        return 0

    references = character_references(relationship.relationship_id)
    missing = {"elder", "child"} - set(references)
    if missing:
        raise RuntimeError(
            "Both active character assets are required; missing roles="
            f"{sorted(missing)}"
        )

    results = asyncio.run(
        render_and_publish_all(
            relationship=relationship,
            roles=roles,
            cases=cases,
            references=references,
        )
    )
    retained_event_seqs = {item["eventSeq"] for item in results}
    if len(retained_event_seqs) != len(PRESET_ORDER):
        raise RuntimeError(
            "Five-level test did not retain five distinct revision events: "
            f"{sorted(retained_event_seqs)}"
        )
    result_path = args.output_dir / "render-results.json"
    result_path.write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(results, ensure_ascii=False, indent=2))
    print(f"Render results: {result_path}")
    print(
        "Five distinct append-only revisions were retained for both views. "
        "The current/latest wallpaper is the farthest level; swipe backward "
        "to inspect the previous four levels."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
