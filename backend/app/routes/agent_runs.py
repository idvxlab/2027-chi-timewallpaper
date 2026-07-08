from __future__ import annotations

import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.agents import MultiAgentOrchestrator
from app.core.config import settings
from app.db.models import ReferenceImageLog
from app.db.session import SessionLocal
from app.schemas.agent import AgentRunCreateOut, AgentRunResult, AgentRunTextIn
from app.services.user_context import normalize_user_context

router = APIRouter()
orchestrator = MultiAgentOrchestrator()
REFERENCE_DIR = Path(settings.storage_local_dir) / "references"
REFERENCE_EXTS = ("png", "jpg", "jpeg", "webp")


def _reference_image_url(filename: str) -> str:
    return f"/references/{filename}"


def _find_default_reference(role: str) -> Optional[str]:
    aliases = {
        "elder": ("elder", "old", "parent", "grandma", "grandmother", "mother", "father"),
        "child": ("child", "young", "kid", "daughter", "son", "youth"),
    }.get(role, (role,))
    if not REFERENCE_DIR.exists():
        return None
    candidates: list[Path] = []
    for alias in aliases:
        for ext in REFERENCE_EXTS:
            candidates.extend(REFERENCE_DIR.glob(f"{alias}.{ext}"))
            candidates.extend(REFERENCE_DIR.glob(f"{alias}-*.{ext}"))
            candidates.extend(REFERENCE_DIR.glob(f"{alias}_*.{ext}"))
    candidates = [path for path in candidates if path.is_file()]
    if not candidates:
        return None
    candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return _reference_image_url(candidates[0].name)


def _resolve_role_references(
    elder_reference_url: Optional[str] = None,
    child_reference_url: Optional[str] = None,
) -> dict[str, str | None]:
    return {
        "elder": elder_reference_url or _find_default_reference("elder"),
        "child": child_reference_url or _find_default_reference("child"),
    }


def _image_ext(content_type: str, filename: str | None) -> str:
    if "jpeg" in content_type or "jpg" in content_type:
        return "jpg"
    if "webp" in content_type:
        return "webp"
    if "png" in content_type:
        return "png"
    suffix = Path(filename or "").suffix.lower().strip(".")
    if suffix in {"png", "jpg", "jpeg", "webp"}:
        return "jpg" if suffix == "jpeg" else suffix
    return "png"


@router.post("/reference-images")
async def upload_reference_image(
    role: str = Form(...),
    image: UploadFile = File(...),
    user_id: Optional[str] = Form(default=None, alias="userId"),
    relationship_id: Optional[str] = Form(default=None, alias="relationshipId"),
) -> dict[str, str]:
    if role not in {"elder", "child"}:
        raise HTTPException(status_code=400, detail="role must be elder or child")
    content_type = image.content_type or ""
    if content_type and not content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="reference image must be an image file")
    raw = await image.read()
    if not raw:
        raise HTTPException(status_code=400, detail="reference image is empty")
    REFERENCE_DIR.mkdir(parents=True, exist_ok=True)
    ext = _image_ext(content_type, image.filename)
    filename = f"{role}-{uuid.uuid4().hex}.{ext}"
    (REFERENCE_DIR / filename).write_bytes(raw)
    url = _reference_image_url(filename)
    resolved_user_id, resolved_relationship_id = normalize_user_context(user_id, relationship_id)
    with SessionLocal() as session:
        session.add(
            ReferenceImageLog(
                reference_id=uuid.uuid4().hex,
                user_id=resolved_user_id,
                relationship_id=resolved_relationship_id,
                role=role,
                image_url=url,
            )
        )
        session.commit()
    return {"role": role, "url": url, "userId": resolved_user_id, "relationshipId": resolved_relationship_id}


@router.post("/audio", response_model=AgentRunCreateOut)
async def create_audio_agent_run(
    audio: UploadFile = File(...),
    user_id: Optional[str] = Form(default=None, alias="userId"),
    relationship_id: Optional[str] = Form(default=None, alias="relationshipId"),
    previous_image_url: Optional[str] = Form(default=None, alias="previousImageUrl"),
    elder_reference_url: Optional[str] = Form(default=None, alias="elderReferenceUrl"),
    child_reference_url: Optional[str] = Form(default=None, alias="childReferenceUrl"),
) -> AgentRunCreateOut:
    raw = await audio.read()
    result = await orchestrator.run_audio(
        raw,
        content_type=audio.content_type or "audio/wav",
        filename=audio.filename or "recording.wav",
        user_id=user_id,
        relationship_id=relationship_id,
        previous_image_url=previous_image_url,
        role_reference_images=_resolve_role_references(elder_reference_url, child_reference_url),
    )
    return AgentRunCreateOut(run_id=result.run_id, status=result.status, result=result)


@router.post("/text", response_model=AgentRunCreateOut)
async def create_text_agent_run(body: AgentRunTextIn) -> AgentRunCreateOut:
    result = await orchestrator.run_text(
        body.transcript,
        user_id=body.user_id,
        relationship_id=body.relationship_id,
        previous_image_url=body.previous_image_url,
        role_reference_images=_resolve_role_references(body.elder_reference_url, body.child_reference_url),
    )
    return AgentRunCreateOut(run_id=result.run_id, status=result.status, result=result)


@router.get("/{run_id}", response_model=AgentRunResult)
async def get_agent_run(run_id: str) -> AgentRunResult:
    return await orchestrator.get_run(run_id)
