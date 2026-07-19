from __future__ import annotations

import re
import uuid
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from PIL import Image, ImageFilter

from app.core.config import settings
from app.db.models import MemoryObjectAsset, MessageLog
from app.db.session import Base, SessionLocal, engine
from app.schemas.agent import MemoryObjectItem
from app.services.pipeline_service import generate_wallpaper_from_prompt


GENERATED_DIR = Path(settings.storage_local_dir) / "generated"
MEMORY_ICON_DIR = GENERATED_DIR / "memory-icons"
ICON_POSTPROCESS_VERSION = "transparent-icon-v3-chroma-key"


class MemoryObjectService:
    """Build a reusable memory-object icon asset library from conversation history."""

    STOP_OBJECTS = {
        "",
        "生活物件",
        "当前事件相关生活物件",
        "物件",
        "东西",
        "事情",
        "事件",
        "日常",
        "生活线索",
        "当前事件",
        "工作事务",
        "身体状态",
        "精神",
    }

    async def list_assets(
        self,
        *,
        relationship_id: str,
        threshold: int = 3,
        limit: int = 12,
        generate_missing: bool = True,
        include_pending: bool = False,
        run_id: str | None = None,
    ) -> list[MemoryObjectItem]:
        Base.metadata.create_all(bind=engine)
        threshold = max(1, min(threshold or 3, 20))
        limit = max(1, min(limit or 12, 36))

        candidates = self.collect_candidates(relationship_id=relationship_id, limit=limit)
        output: list[MemoryObjectItem] = []

        for candidate in candidates:
            if candidate.mention_count < threshold:
                if include_pending:
                    output.append(candidate)
                continue

            asset = self._load_asset(relationship_id, candidate.name)
            if asset and asset.image_url:
                raw = asset.raw or {}
                needs_reprocess = raw.get("postprocessVersion") != ICON_POSTPROCESS_VERSION
                if needs_reprocess:
                    if generate_missing:
                        self._log(
                            run_id,
                            f"Memory Object asset regeneration triggered object={candidate.name} "
                            f"oldPostprocess={raw.get('postprocessVersion')}",
                        )
                        prompt = self.build_icon_prompt(candidate.name, candidate.examples)
                        image = await generate_wallpaper_from_prompt(prompt, run_id=run_id, aspect_ratio="1:1")
                        source_url = image.get("imageUrl") or ""
                        transparent_url = self._make_transparent_icon(source_url, candidate.name, run_id=run_id)
                        if transparent_url:
                            asset = self._update_asset_image_url(
                                asset.asset_id,
                                transparent_url,
                                source_url,
                                prompt=prompt,
                                image_raw=image.get("raw"),
                            )
                    else:
                        source_url = raw.get("sourceImageUrl") or asset.image_url
                        transparent_url = self._make_transparent_icon(source_url, candidate.name, run_id=run_id)
                        if transparent_url:
                            asset = self._update_asset_image_url(asset.asset_id, transparent_url, source_url)
                item = self._item_from_asset(asset, candidate)
                output.append(item)
                continue

            prompt = self.build_icon_prompt(candidate.name, candidate.examples)
            image_url = ""
            raw: dict[str, Any] = {}
            status = "pending"
            if generate_missing:
                self._log(
                    run_id,
                    f"Memory Object asset generation triggered object={candidate.name} "
                    f"mentions={candidate.mention_count} threshold={threshold}",
                )
                image = await generate_wallpaper_from_prompt(prompt, run_id=run_id, aspect_ratio="1:1")
                image_url = image.get("imageUrl") or ""
                transparent_url = self._make_transparent_icon(image_url, candidate.name, run_id=run_id)
                raw = {"image": image.get("raw"), "sourceImageUrl": image_url}
                if transparent_url:
                    image_url = transparent_url
                    raw["transparentImageUrl"] = transparent_url
                    raw["postprocessVersion"] = ICON_POSTPROCESS_VERSION
                status = "ready" if image_url else "failed"

            asset = self._save_asset(
                relationship_id=relationship_id,
                candidate=candidate,
                threshold=threshold,
                image_url=image_url,
                prompt=prompt,
                status=status,
                raw=raw,
            )
            output.append(self._item_from_asset(asset, candidate))

        return output

    def collect_candidates(self, relationship_id: str, limit: int = 12) -> list[MemoryObjectItem]:
        rows = self._load_rows(relationship_id)
        buckets: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"count": 0, "examples": [], "source_message_ids": [], "last_seen_at": None}
        )

        for row in rows:
            objects = self._extract_objects(row.short_term_table)
            if not objects:
                objects = self._extract_objects_from_situation(row.situation)
            message_objects = {self._normalize(name) for name in objects}
            for key in message_objects:
                if not self._is_concrete_object(key):
                    continue
                bucket = buckets[key]
                bucket["count"] += 1
                if row.transcript and len(bucket["examples"]) < 4:
                    bucket["examples"].append(row.transcript)
                if row.message_id and row.message_id not in bucket["source_message_ids"]:
                    bucket["source_message_ids"].append(row.message_id)
                if bucket["last_seen_at"] is None or row.created_at > bucket["last_seen_at"]:
                    bucket["last_seen_at"] = row.created_at

        ranked = sorted(
            buckets.items(),
            key=lambda item: (item[1]["count"], item[1]["last_seen_at"] or datetime.min),
            reverse=True,
        )
        return [
            MemoryObjectItem(
                name=name,
                mention_count=data["count"],
                ready=False,
                examples=data["examples"],
                source_message_ids=data["source_message_ids"][:20],
                last_seen_at=data["last_seen_at"],
            )
            for name, data in ranked[:limit]
        ]

    def build_icon_prompt(self, name: str, examples: list[str] | None = None) -> str:
        example_text = "；".join((examples or [])[:2])
        context = f"它来自这些对话记忆：{example_text}" if example_text else ""
        return f"""
生成一张单个记忆物品小图标：{name}。
{context}

要求：
- 只画这个物品本身，不画人物，不画完整房间或大场景。
- 居中构图，物品完整，轮廓清晰，适合放在手机统计页的小卡片中。
- 风格为温柔手绘水彩/绘本插画，低饱和暖色，可见纸张纹理和柔和笔触。
- 背景必须是纯绿色 chroma key green，颜色 #00FF00，平整纯色，后续会被程序抠掉。
- 禁止物体背后的水彩底盘、圆形色块、淡黄色光晕、纸张斑块、贴纸底色、阴影或大面积投影。
- 物品边缘不要和绿色背景混合，不要绿色描边，不要绿色反光。
- 不要文字、logo、水印、UI、边框、聊天框。
""".strip()

    def _load_rows(self, relationship_id: str) -> list[MessageLog]:
        with SessionLocal() as session:
            return (
                session.query(MessageLog)
                .filter(MessageLog.relationship_id == relationship_id)
                .order_by(MessageLog.created_at.desc())
                .limit(300)
                .all()
            )

    def _load_asset(self, relationship_id: str, object_name: str) -> MemoryObjectAsset | None:
        with SessionLocal() as session:
            return (
                session.query(MemoryObjectAsset)
                .filter(
                    MemoryObjectAsset.relationship_id == relationship_id,
                    MemoryObjectAsset.object_name == object_name,
                )
                .order_by(MemoryObjectAsset.updated_at.desc())
                .first()
            )

    def _save_asset(
        self,
        *,
        relationship_id: str,
        candidate: MemoryObjectItem,
        threshold: int,
        image_url: str,
        prompt: str,
        status: str,
        raw: dict[str, Any],
    ) -> MemoryObjectAsset:
        now = datetime.utcnow()
        with SessionLocal() as session:
            row = (
                session.query(MemoryObjectAsset)
                .filter(
                    MemoryObjectAsset.relationship_id == relationship_id,
                    MemoryObjectAsset.object_name == candidate.name,
                )
                .order_by(MemoryObjectAsset.updated_at.desc())
                .first()
            )
            if row is None:
                row = MemoryObjectAsset(
                    asset_id=uuid.uuid4().hex,
                    relationship_id=relationship_id,
                    object_name=candidate.name,
                    created_at=now,
                )
                session.add(row)
            row.mention_count = candidate.mention_count
            row.threshold = threshold
            row.image_url = image_url
            row.prompt = prompt
            row.status = status
            row.examples = candidate.examples
            row.source_message_ids = candidate.source_message_ids
            row.raw = raw
            row.updated_at = now
            session.commit()
            session.refresh(row)
            return row

    def _update_asset_image_url(
        self,
        asset_id: str,
        transparent_url: str,
        source_url: str,
        prompt: str | None = None,
        image_raw: dict[str, Any] | None = None,
    ) -> MemoryObjectAsset:
        now = datetime.utcnow()
        with SessionLocal() as session:
            row = session.query(MemoryObjectAsset).filter(MemoryObjectAsset.asset_id == asset_id).first()
            if row is None:
                raise RuntimeError(f"Memory object asset not found: {asset_id}")
            row.image_url = transparent_url
            if prompt is not None:
                row.prompt = prompt
            row.raw = {
                **(row.raw or {}),
                **({"image": image_raw} if image_raw is not None else {}),
                "sourceImageUrl": source_url,
                "transparentImageUrl": transparent_url,
                "postprocessVersion": ICON_POSTPROCESS_VERSION,
            }
            row.updated_at = now
            session.commit()
            session.refresh(row)
            return row

    def _item_from_asset(self, asset: MemoryObjectAsset, candidate: MemoryObjectItem) -> MemoryObjectItem:
        return MemoryObjectItem(
            asset_id=asset.asset_id,
            name=asset.object_name,
            mention_count=candidate.mention_count or asset.mention_count,
            threshold=asset.threshold,
            ready=bool(asset.image_url and asset.status == "ready"),
            image_url=asset.image_url,
            prompt=asset.prompt,
            examples=candidate.examples or asset.examples or [],
            source_message_ids=candidate.source_message_ids or asset.source_message_ids or [],
            last_seen_at=candidate.last_seen_at,
        )

    def _extract_objects(self, table: dict[str, Any]) -> list[str]:
        if not isinstance(table, dict):
            return []
        section = table.get("A_situational_semantics", {})
        if not isinstance(section, dict):
            return []
        return self._values_from_cell(section.get("object"))

    def _extract_objects_from_situation(self, situation: dict[str, Any]) -> list[str]:
        if not isinstance(situation, dict):
            return []
        raw = situation.get("object_trace") or situation.get("objects")
        return self._split_values(raw)

    def _values_from_cell(self, cell: Any) -> list[str]:
        if isinstance(cell, dict):
            return self._split_values(cell.get("value"))
        return self._split_values(cell)

    def _split_values(self, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, list):
            output: list[str] = []
            for item in value:
                output.extend(self._split_values(item))
            return output
        if isinstance(value, dict):
            return self._split_values(value.get("value") or value.get("raw") or value.get("name"))
        text = str(value).strip()
        if not text:
            return []
        return [part.strip() for part in re.split(r"[、,，;；/|]", text) if part.strip()]

    def _normalize(self, value: str) -> str:
        return str(value).strip().strip("。.!！?？ \n\r\t\"'“”‘’")

    def _is_concrete_object(self, value: str) -> bool:
        if not value or value in self.STOP_OBJECTS:
            return False
        if len(value) > 12:
            return False
        if any(token in value for token in ("状态", "关系", "氛围", "情绪", "意图", "回应", "分享")):
            return False
        return True

    def _log(self, run_id: str | None, message: str) -> None:
        prefix = f"[agent-run:{run_id}] " if run_id else ""
        print(f"{prefix}{message}", flush=True)

    def _make_transparent_icon(
        self,
        image_url: str,
        object_name: str,
        *,
        run_id: str | None = None,
    ) -> str:
        source_path = self._local_generated_path(image_url)
        if source_path is None or not source_path.exists():
            return ""

        try:
            image = Image.open(source_path).convert("RGBA")
            transparent = self._remove_chroma_key_background(image)
            if transparent is None:
                transparent = self._remove_near_white_border_background(image)
            transparent = self._trim_and_square_icon(transparent)
            MEMORY_ICON_DIR.mkdir(parents=True, exist_ok=True)
            safe_name = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_-]+", "-", object_name).strip("-") or "memory-object"
            output_path = MEMORY_ICON_DIR / f"{safe_name}-{uuid.uuid4().hex}.png"
            transparent.save(output_path, format="PNG")
            self._log(run_id, f"Memory Object transparent icon saved url=/generated/memory-icons/{output_path.name}")
            return f"/generated/memory-icons/{output_path.name}"
        except Exception as exc:
            self._log(run_id, f"Memory Object transparent icon failed ({type(exc).__name__}): {exc}")
            return ""

    def _remove_chroma_key_background(self, image: Image.Image) -> Image.Image | None:
        width, height = image.size
        pixels = image.load()
        matte = Image.new("L", image.size, 0)
        matte_pixels = matte.load()
        green_pixels = 0

        for y in range(height):
            for x in range(width):
                if self._is_chroma_green_pixel(pixels[x, y]):
                    matte_pixels[x, y] = 255
                    green_pixels += 1

        if green_pixels < width * height * 0.08:
            return None

        matte = matte.filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.GaussianBlur(radius=0.8))
        alpha = image.getchannel("A")
        alpha = Image.composite(Image.new("L", image.size, 0), alpha, matte)
        output = image.copy()
        output.putalpha(alpha)
        return output

    def _is_chroma_green_pixel(self, rgba: tuple[int, int, int, int]) -> bool:
        r, g, b, a = rgba
        if a < 12:
            return True
        return g >= 145 and g > r * 1.45 and g > b * 1.45

    def _local_generated_path(self, image_url: str) -> Path | None:
        if not image_url:
            return None
        parsed = urlparse(image_url)
        path = parsed.path if parsed.scheme else image_url
        if not path.startswith("/generated/"):
            return None
        relative = path.removeprefix("/generated/").lstrip("/")
        if not relative or ".." in Path(relative).parts:
            return None
        return GENERATED_DIR / relative

    def _remove_near_white_border_background(self, image: Image.Image) -> Image.Image:
        width, height = image.size
        pixels = image.load()
        visited: set[tuple[int, int]] = set()
        stack: list[tuple[int, int]] = []

        for x in range(width):
            stack.append((x, 0))
            stack.append((x, height - 1))
        for y in range(height):
            stack.append((0, y))
            stack.append((width - 1, y))

        background = Image.new("L", image.size, 0)
        background_pixels = background.load()

        while stack:
            x, y = stack.pop()
            if x < 0 or y < 0 or x >= width or y >= height or (x, y) in visited:
                continue
            visited.add((x, y))
            if not self._is_near_white_background_pixel(pixels[x, y]):
                continue
            background_pixels[x, y] = 255
            stack.extend(((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)))

        background = background.filter(ImageFilter.GaussianBlur(radius=1.2))
        alpha = image.getchannel("A")
        alpha = Image.composite(Image.new("L", image.size, 0), alpha, background)
        output = image.copy()
        output.putalpha(alpha)
        return output

    def _keep_primary_icon_foreground(self, image: Image.Image) -> Image.Image:
        seed = self._foreground_seed_mask(image)
        components = self._connected_components(seed)
        if not components:
            return image

        largest = max(components, key=len)
        keep = Image.new("L", image.size, 0)
        keep_pixels = keep.load()
        for x, y in largest:
            keep_pixels[x, y] = 255

        keep = keep.filter(ImageFilter.MaxFilter(17)).filter(ImageFilter.GaussianBlur(radius=1.4))
        original_alpha = image.getchannel("A")
        alpha = Image.composite(original_alpha, Image.new("L", image.size, 0), keep)
        output = image.copy()
        output.putalpha(alpha)
        return output

    def _foreground_seed_mask(self, image: Image.Image) -> Image.Image:
        width, height = image.size
        grayscale = image.convert("L")
        edges = grayscale.filter(ImageFilter.FIND_EDGES).filter(ImageFilter.GaussianBlur(radius=0.5))
        alpha = image.getchannel("A")
        pixels = image.load()
        edge_pixels = edges.load()
        alpha_pixels = alpha.load()
        seed = Image.new("L", image.size, 0)
        seed_pixels = seed.load()

        for y in range(height):
            for x in range(width):
                if alpha_pixels[x, y] < 24:
                    continue
                r, g, b, _ = pixels[x, y]
                brightness = (r + g + b) / 3
                spread = max(r, g, b) - min(r, g, b)
                blue_object = b > r + 8 and b > g - 2
                dark_ink = brightness < 150
                strong_color = spread > 24
                visible_edge = edge_pixels[x, y] > 14
                if dark_ink or strong_color or blue_object or visible_edge:
                    seed_pixels[x, y] = 255
        return seed

    def _connected_components(self, mask: Image.Image) -> list[list[tuple[int, int]]]:
        width, height = mask.size
        pixels = mask.load()
        visited: set[tuple[int, int]] = set()
        components: list[list[tuple[int, int]]] = []

        for y in range(height):
            for x in range(width):
                if pixels[x, y] == 0 or (x, y) in visited:
                    continue
                stack = [(x, y)]
                component: list[tuple[int, int]] = []
                while stack:
                    cx, cy = stack.pop()
                    if cx < 0 or cy < 0 or cx >= width or cy >= height or (cx, cy) in visited:
                        continue
                    visited.add((cx, cy))
                    if pixels[cx, cy] == 0:
                        continue
                    component.append((cx, cy))
                    stack.extend(((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)))
                if len(component) >= 32:
                    components.append(component)
        return components

    def _is_near_white_background_pixel(self, rgba: tuple[int, int, int, int]) -> bool:
        r, g, b, a = rgba
        if a < 12:
            return True
        brightness = (r + g + b) / 3
        color_spread = max(r, g, b) - min(r, g, b)
        return brightness >= 238 and color_spread <= 45

    def _trim_and_square_icon(self, image: Image.Image) -> Image.Image:
        alpha_bbox = image.getchannel("A").getbbox()
        if alpha_bbox is None:
            return Image.new("RGBA", (512, 512), (255, 255, 255, 0))

        cropped = image.crop(alpha_bbox)
        width, height = cropped.size
        side = max(width, height)
        margin = max(24, int(side * 0.14))
        canvas_side = side + margin * 2
        canvas = Image.new("RGBA", (canvas_side, canvas_side), (255, 255, 255, 0))
        canvas.alpha_composite(cropped, ((canvas_side - width) // 2, (canvas_side - height) // 2))
        return canvas.resize((512, 512), Image.LANCZOS)
