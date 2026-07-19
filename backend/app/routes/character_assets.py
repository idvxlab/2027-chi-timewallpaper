from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, File, Form, UploadFile

from app.schemas.agent import CharacterAssetListOut, CharacterAssetOut
from app.services.character_asset_service import character_asset_service


router = APIRouter()


def _to_output(row) -> CharacterAssetOut:
    return CharacterAssetOut(
        asset_id=row.asset_id,
        user_id=row.user_id,
        relationship_id=row.relationship_id,
        role=row.role,
        status=row.status,
        style_version=row.style_version,
        source_image_url=row.source_image_url,
        style_reference_url=row.style_reference_url,
        master_image_url=row.master_image_url,
        portrait_image_url=row.portrait_image_url,
        half_body_image_url=row.half_body_image_url,
        full_body_image_url=row.full_body_image_url,
        is_active=row.is_active,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.post("", response_model=CharacterAssetOut)
async def create_character_asset(
    image: UploadFile = File(...),
    user_id: str = Form(..., alias="userId"),
    role: str = Form(...),
    relationship_id: Optional[str] = Form(default=None, alias="relationshipId"),
    style_reference: Optional[UploadFile] = File(default=None, alias="styleReference"),
) -> CharacterAssetOut:
    source_bytes = await image.read()
    style_bytes = await style_reference.read() if style_reference else None
    row = await character_asset_service.create(
        user_id=user_id,
        role=role,
        relationship_id=relationship_id,
        source_bytes=source_bytes,
        source_content_type=image.content_type or "image/png",
        source_filename=image.filename,
        style_reference_bytes=style_bytes,
        style_reference_content_type=(style_reference.content_type if style_reference else "image/png") or "image/png",
        style_reference_filename=style_reference.filename if style_reference else None,
    )
    return _to_output(row)


@router.get("/by-user/{user_id}", response_model=CharacterAssetOut)
async def get_character_asset(user_id: str, role: Optional[str] = None) -> CharacterAssetOut:
    return _to_output(character_asset_service.get_latest(user_id=user_id, role=role))


@router.get("/by-relationship/{relationship_id}", response_model=CharacterAssetListOut)
async def list_relationship_character_assets(relationship_id: str) -> CharacterAssetListOut:
    rows = character_asset_service.list_for_relationship(relationship_id)
    return CharacterAssetListOut(items=[_to_output(row) for row in rows])
