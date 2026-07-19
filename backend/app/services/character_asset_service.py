from __future__ import annotations

import io
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import HTTPException
from PIL import Image, ImageOps

from app.core.config import settings
from app.db.models import CharacterAsset
from app.db.session import SessionLocal
from app.services.openai_image_service import call_openai_image_edit_to_pil


CHARACTER_STYLE_VERSION = "classic-picturebook-line-watercolor-v1"
DEFAULT_STYLE_REFERENCE_NAME = "character-watercolor-reference.png"
CHARACTER_ASSET_DIR = Path(settings.storage_local_dir) / "character-assets"
STYLE_REFERENCE_DIR = Path(settings.storage_local_dir) / "style-references"


class CharacterAssetService:
    async def create(
        self,
        *,
        user_id: str,
        role: str,
        source_bytes: bytes,
        source_content_type: str,
        source_filename: Optional[str] = None,
        relationship_id: Optional[str] = None,
        style_reference_bytes: Optional[bytes] = None,
        style_reference_content_type: str = "image/png",
        style_reference_filename: Optional[str] = None,
    ) -> CharacterAsset:
        if role not in {"elder", "child"}:
            raise HTTPException(status_code=400, detail="role must be elder or child")
        if not source_bytes:
            raise HTTPException(status_code=400, detail="character source image is empty")
        if source_content_type and not source_content_type.startswith("image/"):
            raise HTTPException(status_code=400, detail="character source must be an image")

        asset_id = f"char-{uuid.uuid4().hex}"
        asset_dir = CHARACTER_ASSET_DIR / asset_id
        asset_dir.mkdir(parents=True, exist_ok=True)

        source_image = self._open_image(source_bytes, "source image")
        source_ext = self._image_ext(source_content_type, source_filename)
        source_path = asset_dir / f"source.{source_ext}"
        self._save_image(source_image, source_path)

        style_image, style_url = self._resolve_style_reference(
            style_reference_bytes=style_reference_bytes,
            content_type=style_reference_content_type,
            filename=style_reference_filename,
            asset_dir=asset_dir,
        )
        prompt = self._build_prompt(role)
        now = datetime.utcnow()
        row = CharacterAsset(
            asset_id=asset_id,
            user_id=user_id,
            relationship_id=relationship_id,
            role=role,
            source_image_url=self._asset_url(asset_id, source_path.name),
            style_reference_url=style_url,
            style_version=CHARACTER_STYLE_VERSION,
            status="processing",
            is_active=False,
            prompt=prompt,
            raw={"sourceFilename": source_filename or "", "styleReferenceFilename": style_reference_filename or ""},
            created_at=now,
            updated_at=now,
        )
        with SessionLocal() as session:
            session.add(row)
            session.commit()
            session.refresh(row)

        try:
            master = await call_openai_image_edit_to_pil(
                prompt=prompt,
                base_image=source_image,
                identity_image=style_image,
            )
            urls = self._save_variants(asset_id, master)
        except Exception as exc:
            with SessionLocal() as session:
                failed = session.query(CharacterAsset).filter(CharacterAsset.asset_id == asset_id).one()
                failed.status = "failed"
                failed.raw = {**(failed.raw or {}), "error": str(exc)[:1000]}
                failed.updated_at = datetime.utcnow()
                session.commit()
            raise HTTPException(status_code=502, detail=f"Character stylization failed: {exc}") from exc

        with SessionLocal() as session:
            session.query(CharacterAsset).filter(
                CharacterAsset.user_id == user_id,
                CharacterAsset.role == role,
            ).update({CharacterAsset.is_active: False}, synchronize_session=False)
            ready = session.query(CharacterAsset).filter(CharacterAsset.asset_id == asset_id).one()
            ready.master_image_url = urls["master"]
            ready.portrait_image_url = urls["portrait"]
            ready.half_body_image_url = urls["halfBody"]
            ready.full_body_image_url = urls["fullBody"]
            ready.status = "ready"
            ready.is_active = True
            ready.updated_at = datetime.utcnow()
            ready.raw = {
                **(ready.raw or {}),
                "variantStrategy": "one generated master plus deterministic crops",
                "styleReferenceUsed": bool(style_image),
            }
            session.commit()
            session.refresh(ready)
            session.expunge(ready)
        return ready

    def get_latest(self, *, user_id: str, role: Optional[str] = None) -> CharacterAsset:
        with SessionLocal() as session:
            query = session.query(CharacterAsset).filter(
                CharacterAsset.user_id == user_id,
                CharacterAsset.is_active.is_(True),
            )
            if role:
                query = query.filter(CharacterAsset.role == role)
            row = query.order_by(CharacterAsset.updated_at.desc()).first()
            if row is None:
                raise HTTPException(status_code=404, detail="Active character asset not found")
            session.expunge(row)
            return row

    def list_for_relationship(self, relationship_id: str) -> list[CharacterAsset]:
        with SessionLocal() as session:
            rows = (
                session.query(CharacterAsset)
                .filter(
                    CharacterAsset.relationship_id == relationship_id,
                    CharacterAsset.is_active.is_(True),
                    CharacterAsset.status == "ready",
                )
                .order_by(CharacterAsset.updated_at.desc())
                .all()
            )
            for row in rows:
                session.expunge(row)
            return rows

    def _resolve_style_reference(
        self,
        *,
        style_reference_bytes: Optional[bytes],
        content_type: str,
        filename: Optional[str],
        asset_dir: Path,
    ) -> tuple[Optional[Image.Image], str]:
        if style_reference_bytes:
            style_image = self._open_image(style_reference_bytes, "style reference")
            ext = self._image_ext(content_type, filename)
            path = asset_dir / f"style-reference.{ext}"
            self._save_image(style_image, path)
            return style_image, self._asset_url(asset_dir.name, path.name)

        default_path = STYLE_REFERENCE_DIR / DEFAULT_STYLE_REFERENCE_NAME
        if default_path.exists():
            return Image.open(default_path).convert("RGB"), f"/style-references/{default_path.name}"
        return None, ""

    def _save_variants(self, asset_id: str, master: Image.Image) -> dict[str, str]:
        asset_dir = CHARACTER_ASSET_DIR / asset_id
        normalized = ImageOps.exif_transpose(master).convert("RGB")
        master_path = asset_dir / "master.png"
        normalized.save(master_path, format="PNG")

        width, height = normalized.size
        portrait = normalized.crop((0, 0, width, max(1, int(height * 0.56))))
        half_body = normalized.crop((0, 0, width, max(1, int(height * 0.76))))
        portrait_path = asset_dir / "portrait.png"
        half_body_path = asset_dir / "half-body.png"
        portrait.save(portrait_path, format="PNG")
        half_body.save(half_body_path, format="PNG")
        return {
            "master": self._asset_url(asset_id, master_path.name),
            "portrait": self._asset_url(asset_id, portrait_path.name),
            "halfBody": self._asset_url(asset_id, half_body_path.name),
            "fullBody": self._asset_url(asset_id, master_path.name),
        }

    @staticmethod
    def _build_prompt(role: str) -> str:
        identity = "老人或父母角色" if role == "elder" else "年轻子女角色"
        return (
            f"把第一张照片中的人物转绘为一张可重复使用的{identity}标准角色资产。"
            "第二张图若存在，只作为画风参考，绝不复制其中的人物、麦田、构图、衣服或背景。\n"
            "必须保留第一张照片人物的身份特征：年龄感、脸型、五官比例、发型发色、肤色、"
            "体态和独特气质，让家人仍能认出是同一个人。\n"
            "画风与产品底图统一：经典温暖儿童绘本的手绘墨线水彩。使用干净、圆润、略带手绘抖动的"
            "深棕色轮廓线；用大块透明水彩和轻薄水粉色块上色；纸张纹理可见。人物造型圆润、亲切、"
            "有动画角色感，表情自然克制。线条清楚简洁，不要碎线、毛躁线、密集短笔触或写实皮肤纹理。\n"
            "输出单个完整人物，竖向全身或接近全身，身体和四肢不要被裁断；三分之四侧身，视线自然看向"
            "手中物件或画面侧方，不直视镜头。使用朴素、无文字、适合日常生活场景的衣着。"
            "背景保持极简的奶油色水彩纸，只留很淡的水彩晕染，不生成房间、家具、麦田、花园或叙事情节，"
            "便于后续把角色放入不同场景。\n"
            "禁止：多人、重复人物、分镜、角色设定表格、文字、logo、水印、照片写实、3D、日漫风、"
            "塑料皮肤、复杂背景、正脸证件照、夸张大眼和过度幼态化。"
        )

    @staticmethod
    def _open_image(raw: bytes, label: str) -> Image.Image:
        try:
            return ImageOps.exif_transpose(Image.open(io.BytesIO(raw))).convert("RGB")
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Invalid {label}: {exc}") from exc

    @staticmethod
    def _save_image(image: Image.Image, path: Path) -> None:
        if path.suffix.lower() in {".jpg", ".jpeg"}:
            image.convert("RGB").save(path, format="JPEG", quality=95)
        elif path.suffix.lower() == ".webp":
            image.save(path, format="WEBP", quality=95)
        else:
            image.save(path, format="PNG")

    @staticmethod
    def _image_ext(content_type: str, filename: Optional[str]) -> str:
        if "jpeg" in content_type or "jpg" in content_type:
            return "jpg"
        if "webp" in content_type:
            return "webp"
        suffix = Path(filename or "").suffix.lower().strip(".")
        return suffix if suffix in {"png", "jpg", "jpeg", "webp"} else "png"

    @staticmethod
    def _asset_url(asset_id: str, filename: str) -> str:
        return f"/character-assets/files/{asset_id}/{filename}"


character_asset_service = CharacterAssetService()
