from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageOps

from test_visual_mapping_ab import (
    DEFAULT_USERNAMES,
    RELATIONSHIP_PRESETS,
    build_case,
    character_references,
    resolve_relationship,
    role_users,
    write_preview,
)

from app.core.config import settings
from app.services.layered_painter_tools import layered_painter_tools


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "tmp" / "five-relationship-layouts"
PRESET_ORDER = ("closest", "near", "middle", "far", "farthest")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Preview or render the five deterministic relationship layouts from "
            "one clean base image. Rendering does not update wallpaper revisions "
            "or the current wallpaper page."
        )
    )
    parser.add_argument(
        "--usernames",
        nargs=2,
        default=DEFAULT_USERNAMES,
        metavar=("USER_A", "USER_B"),
        help="Exact user display names; default: 5 6",
    )
    parser.add_argument(
        "--presets",
        nargs="+",
        choices=PRESET_ORDER,
        default=PRESET_ORDER,
        help="Relationship levels to test; default: all five levels",
    )
    parser.add_argument("--elder-time", default="白天")
    parser.add_argument("--elder-scene", default="超市")
    parser.add_argument("--child-time", default="晚上")
    parser.add_argument("--child-scene", default="单位")
    parser.add_argument(
        "--base-image-url",
        default=settings.static_base_scene_url,
        help=(
            "Shared clean base for every level; default: "
            f"{settings.static_base_scene_url}"
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
    )
    parser.add_argument(
        "--render",
        action="store_true",
        help="Make one paid Seedream request per selected relationship level",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Required with --render to confirm the paid image requests",
    )
    return parser.parse_args()


def relationship_instruction(preset: str) -> str:
    return (
        f"五档关系视觉测试-{preset}：两端时间与场景、人物身份、纸雕风格和底图输入"
        "保持一致；只允许关系档位改变人物与平台间距、中央连接方式及花木数量。"
    )


async def render_all(
    *,
    presets: tuple[str, ...],
    cases: dict[str, dict[str, Any]],
    references: dict[str, str],
    base_image_url: str,
) -> list[dict[str, Any]]:
    missing = {"elder", "child"} - set(references)
    if missing:
        raise RuntimeError(
            "Cannot render without active character assets for roles="
            f"{sorted(missing)}"
        )

    # Resolve once so every request receives the exact same two identity images.
    elder_bytes, child_bytes = await asyncio.gather(
        layered_painter_tools.resolve_reference_bytes(references["elder"]),
        layered_painter_tools.resolve_reference_bytes(references["child"]),
    )

    results: list[dict[str, Any]] = []
    for index, preset in enumerate(presets, start=1):
        controls = cases[preset]["controls"]
        print(
            f"[{index}/{len(presets)}] rendering {preset}: "
            f"mode={controls['spatialMode']} "
            f"centralFeature={controls['layoutState']['centralFeature']}"
        )
        # This calls the image service directly. It deliberately bypasses the
        # wallpaper task/revision service, so test output cannot replace today's
        # latest wallpaper and every level starts from the same clean base.
        rendered = await layered_painter_tools.initialize_wallpaper_view(
            base_image_url=base_image_url,
            younger_image_bytes=child_bytes,
            elder_image_bytes=elder_bytes,
            designer_five_layer_plan=cases[preset]["plan"],
            semantic_visual_instruction=relationship_instruction(preset),
            speaker_role="elder",
        )
        raw = rendered.get("raw") or {}
        if raw.get("status") != "completed" or not rendered.get("imageUrl"):
            raise RuntimeError(
                f"Seedream render failed for {preset}: "
                f"{json.dumps(raw, ensure_ascii=False)}"
            )
        results.append(
            {
                "preset": preset,
                "relationshipScore": controls["relationshipScore"],
                "spatialMode": controls["spatialMode"],
                "centralFeature": controls["layoutState"]["centralFeature"],
                "baseImageUrl": base_image_url,
                "imageUrl": rendered["imageUrl"],
                "finalPath": raw.get("final_path"),
                "runId": raw.get("run_id"),
                "provider": raw.get("provider"),
                "model": raw.get("model"),
            }
        )
    return results


def create_contact_sheet(results: list[dict[str, Any]], output_path: Path) -> Path:
    cell_size = 384
    label_height = 44
    canvas = Image.new(
        "RGB",
        (cell_size * len(results), cell_size + label_height),
        (244, 240, 231),
    )
    draw = ImageDraw.Draw(canvas)
    for index, result in enumerate(results):
        final_path = Path(result["finalPath"] or "")
        if not final_path.is_file():
            raise FileNotFoundError(
                f"Rendered image is missing for {result['preset']}: {final_path}"
            )
        with Image.open(final_path) as source:
            preview = ImageOps.fit(
                source.convert("RGB"),
                (cell_size, cell_size),
                method=Image.Resampling.LANCZOS,
            )
        x = index * cell_size
        canvas.paste(preview, (x, label_height))
        label = (
            f"{index + 1}. {result['preset']} | {result['spatialMode']} | "
            f"{result['centralFeature']}"
        )
        draw.text((x + 10, 15), label, fill=(45, 43, 39))
        if index:
            draw.line((x, 0, x, canvas.height), fill=(210, 202, 188), width=2)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path, format="PNG")
    return output_path


def main() -> int:
    args = parse_args()
    presets = tuple(dict.fromkeys(args.presets))
    unknown = set(presets) - set(RELATIONSHIP_PRESETS)
    if unknown:
        raise RuntimeError(f"Unsupported relationship presets: {sorted(unknown)}")
    if args.render and not args.yes:
        raise RuntimeError(
            "--render makes paid Seedream calls. Review the preview first, then "
            "rerun with --render --yes."
        )
    relationship, users_by_name = resolve_relationship(tuple(args.usernames))
    roles = role_users(relationship, users_by_name)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    cases: dict[str, dict[str, Any]] = {}
    summary: dict[str, Any] = {
        "relationshipId": relationship.relationship_id,
        "users": {
            role: {
                "username": user.display_name,
                "userId": user.user_id,
            }
            for role, user in roles.items()
        },
        "baseImageUrl": args.base_image_url,
        "isolatedRender": True,
        "updatesCurrentWallpaper": False,
        "presets": [],
    }

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
        cases[preset] = {"plan": plan, "controls": controls}
        item = {
            "preset": preset,
            "relationshipScore": controls["relationshipScore"],
            "spatialMode": controls["spatialMode"],
            "centralFeature": controls["layoutState"]["centralFeature"],
            "personGapRatio": controls["layoutState"]["personGapRatio"],
            "platformGapRatio": controls["layoutState"]["platformGapRatio"],
            "flowerDensity": controls["controls"]["flowerDensity"],
            "previewPath": str(json_path),
            "promptPath": str(prompt_path),
        }
        summary["presets"].append(item)
        print(
            f"[{preset}] score={item['relationshipScore']} "
            f"mode={item['spatialMode']} central={item['centralFeature']} "
            f"personGap={item['personGapRatio']} "
            f"platformGap={item['platformGapRatio']}"
        )

    preview_summary_path = args.output_dir / "five-layout-preview.json"
    preview_summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Preview summary: {preview_summary_path}")

    if not args.render:
        print("Preview only: no image API calls were made and no wallpaper page changed.")
        return 0

    references = character_references(relationship.relationship_id)
    render_results = asyncio.run(
        render_all(
            presets=presets,
            cases=cases,
            references=references,
            base_image_url=args.base_image_url,
        )
    )
    render_results_path = args.output_dir / "five-layout-render-results.json"
    render_results_path.write_text(
        json.dumps(render_results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    contact_sheet_path = create_contact_sheet(
        render_results,
        args.output_dir / "five-layout-comparison.png",
    )
    print(f"Render results: {render_results_path}")
    print(f"Comparison image: {contact_sheet_path}")
    print("The generated test files were not published as wallpaper revisions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
