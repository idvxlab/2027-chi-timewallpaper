from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.services.subject_extraction_service import (
    extract_subject_from_wallpaper,
    resolve_person_role,
)


router = APIRouter()


class _BasePayload(BaseModel):
    imageUrl: str = Field(..., description="Source wallpaper URL")
    region: Literal["upper", "lower"]
    viewerRole: Literal["child", "elder"]


class ExtractSubjectV2Request(_BasePayload):
    pointX: float = Field(..., ge=0.0, le=1.0)
    pointY: float = Field(..., ge=0.0, le=1.0)
    dayIndex: Optional[int] = Field(default=None, ge=0, le=2)


class ExtractSubjectV1Request(_BasePayload):
    normalizedX: float = Field(..., ge=0.0, le=1.0)
    normalizedY: float = Field(..., ge=0.0, le=1.0)


async def _run(
    *,
    image_url: str,
    region: Literal["upper", "lower"],
    viewer_role: Literal["child", "elder"],
    point_x: float,
    point_y: float,
) -> dict:
    try:
        result = await run_in_threadpool(
            extract_subject_from_wallpaper,
            image_url=image_url,
            point_x=point_x,
            point_y=point_y,
            region=region,
            viewer_role=viewer_role,
        )
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        print(f"[subject.route] extract failed: {exc!r}")
        raise HTTPException(status_code=500, detail="Subject extraction failed") from exc

    return {
        "cutoutUrl": result.cutout_url,
        "bbox": result.bbox,
        "raw": {
            "method": result.method,
            "region": region,
            "viewerRole": viewer_role,
            "personRole": resolve_person_role(viewer_role, region),
        },
        "fallback": result.method == "soft_crop_fallback",
    }


@router.post("/extract-subject-v2")
async def extract_subject_v2(payload: ExtractSubjectV2Request) -> dict:
    return await _run(
        image_url=payload.imageUrl,
        region=payload.region,
        viewer_role=payload.viewerRole,
        point_x=payload.pointX,
        point_y=payload.pointY,
    )


@router.post("")
async def extract_subject_v1(payload: ExtractSubjectV1Request) -> dict:
    return await _run(
        image_url=payload.imageUrl,
        region=payload.region,
        viewer_role=payload.viewerRole,
        point_x=payload.normalizedX,
        point_y=payload.normalizedY,
    )
