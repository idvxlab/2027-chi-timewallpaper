"""Seedream rendering for one shared fixed-layout relationship wallpaper."""

from __future__ import annotations

import hashlib
import io
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from PIL import Image

from app.core.config import settings
from app.providers.image.base import ImageProvider
from app.providers.image.errors import ImageProviderError, safe_error_message
from app.providers.image.volcengine_seedream import VolcengineSeedreamProvider
from app.services.prompt_compiler import PromptCompiler
from app.services.prompt_compiler_v2 import (
    compile_for_both as compile_v2_for_both,
    compile_for_reflow as compile_v2_for_reflow,
    compile_for_update as compile_v2_for_update,
    normalize_role as normalize_v2_role,
)


GENERATED_DIR = Path(settings.storage_local_dir) / "generated"


def _public_url(relative_url: str) -> str:
    base_url = settings.public_api_base_url.rstrip("/")
    return f"{base_url}{relative_url}" if base_url else relative_url


def _resolve_frontend_public_dir() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        candidate = parent / "frontend" / "public"
        if candidate.is_dir():
            return candidate
    raise FileNotFoundError("Could not locate frontend/public")


def _resolve_base_image(base_image_url: str) -> Path:
    raw_url = (base_image_url or "").strip()
    if not raw_url:
        raise ValueError("base_image_url is required")

    parsed = urlparse(raw_url)
    url_path = parsed.path or raw_url
    if url_path.startswith("/generated/"):
        relative = url_path.removeprefix("/generated/")
        root = GENERATED_DIR
    elif url_path.startswith("/wallpaper/"):
        relative = url_path.removeprefix("/wallpaper/")
        root = _resolve_frontend_public_dir() / "wallpaper"
    else:
        raise ValueError(
            "base_image_url must point to /generated/... or /wallpaper/..."
        )

    if not relative or ".." in Path(relative).parts:
        raise ValueError("base_image_url contains an invalid relative path")
    resolved_root = root.resolve()
    resolved_path = (root / relative).resolve()
    if resolved_path.parent != resolved_root:
        raise ValueError("base_image_url escapes its static image directory")
    return resolved_path


def _load_image_bytes(image_bytes: bytes, *, label: str) -> Image.Image:
    if not image_bytes:
        raise ValueError(f"{label} identity image is required")
    try:
        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        image.load()
    except Exception as exc:
        raise ValueError(f"{label} identity image is invalid") from exc

    if max(image.size) > 1024:
        ratio = 1024 / max(image.size)
        image = image.resize(
            (max(1, int(image.width * ratio)), max(1, int(image.height * ratio))),
            Image.Resampling.LANCZOS,
        )
    return image


def _save_png(image: Image.Image, filename: str) -> Path:
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    destination = GENERATED_DIR / filename
    temporary = GENERATED_DIR / f".{filename}.{uuid.uuid4().hex}.tmp"
    image.convert("RGB").save(temporary, format="PNG")
    temporary.replace(destination)
    return destination


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _error_metadata(error: Exception) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "error": safe_error_message(error),
        "errorType": "unknown",
        "errorCode": None,
        "statusCode": None,
        "retryable": True,
        "stage": "image_edit",
    }
    if isinstance(error, ImageProviderError):
        metadata.update(
            {
                "errorType": error.error_type,
                "errorCode": error.error_code,
                "statusCode": error.status_code,
                "retryable": error.retryable,
                "stage": error.stage,
            }
        )
    return metadata


async def _generate_initial_shared_wallpaper_two_pass_legacy(
    *,
    base_image_url: str,
    child_identity_bytes: bytes,
    elder_identity_bytes: bytes,
    five_layer_plan: dict[str, Any],
    semantic_visual_instruction: str,
    speaker_role: str,
    provider: ImageProvider | None = None,
) -> dict[str, Any]:
    """Render child upper-right, then elder lower-left, using one shared base.

    Pass 1 is saved before Pass 2. Pass 2 reopens that saved file and verifies
    its SHA-256 so it cannot accidentally use the original base or an in-memory
    image from another job. Seedream's full output is accepted directly; there
    is no provider mask and no local mask composite.
    """

    run_id = uuid.uuid4().hex
    normalized_speaker = PromptCompiler.normalize_role(speaker_role)
    preset_role = PromptCompiler.OPPOSITE_ROLE[normalized_speaker]
    preset_metadata = {
        "non_speaker_preset_role": preset_role,
        "non_speaker_preset_scene": (
            PromptCompiler.first_voice_preset_for_role(preset_role)
        ),
    }

    try:
        base_path = _resolve_base_image(base_image_url)
        if not base_path.is_file():
            raise FileNotFoundError(f"Base wallpaper not found: {base_path}")
        base_image = Image.open(base_path).convert("RGB")
        base_image.load()
        child_identity = _load_image_bytes(
            child_identity_bytes,
            label="child",
        )
        elder_identity = _load_image_bytes(
            elder_identity_bytes,
            label="elder",
        )
        image_provider = provider or VolcengineSeedreamProvider()

        pass1_prompt = PromptCompiler.compile_for_region(
            five_layer_plan=five_layer_plan,
            semantic_visual_instruction=semantic_visual_instruction,
            generation_stage="first_voice",
            speaker_role=normalized_speaker,
            target_role="child",
            target_region="upper_right",
            pass_index=1,
            existing_other_character=False,
        )
    except Exception as exc:
        return {
            "imageUrl": "",
            "raw": {
                "provider": "volcengine_seedream",
                "run_id": run_id,
                "status": "failed",
                "failed_pass": 1,
                **preset_metadata,
                **_error_metadata(exc),
            },
        }

    try:
        pass1_image = await image_provider.edit(
            base_image,
            pass1_prompt,
            identity_images=[child_identity],
            mask=None,
            size=settings.volcengine_image_size,
        )
    except Exception as exc:
        return {
            "imageUrl": "",
            "raw": {
                "provider": image_provider.provider_name,
                "model": image_provider.model_name,
                "run_id": run_id,
                "status": "failed",
                "failed_pass": 1,
                "pass1_prompt": pass1_prompt,
                **preset_metadata,
                **_error_metadata(exc),
            },
        }

    pass1_path = _save_png(
        pass1_image,
        f"seedream-pass1-{run_id}.png",
    )
    pass1_output_sha = _sha256_path(pass1_path)
    pass1_url = _public_url(f"/generated/{pass1_path.name}")

    pass1_bytes = pass1_path.read_bytes()
    pass2_base_sha = hashlib.sha256(pass1_bytes).hexdigest()
    if pass2_base_sha != pass1_output_sha:
        error = RuntimeError("Pass 2 base SHA does not match saved Pass 1 output")
        return {
            "imageUrl": pass1_url,
            "raw": {
                "provider": image_provider.provider_name,
                "model": image_provider.model_name,
                "run_id": run_id,
                "status": "partial_success",
                "completed_passes": 1,
                "failed_pass": 2,
                "pass1_output_sha256": pass1_output_sha,
                "pass2_base_sha256": pass2_base_sha,
                **preset_metadata,
                **_error_metadata(error),
                "errorCode": "PASS2_INPUT_CHAIN_MISMATCH",
            },
        }

    try:
        pass2_base = Image.open(io.BytesIO(pass1_bytes)).convert("RGB")
        pass2_base.load()
        pass2_prompt = PromptCompiler.compile_for_region(
            five_layer_plan=five_layer_plan,
            semantic_visual_instruction=semantic_visual_instruction,
            generation_stage="first_voice",
            speaker_role=normalized_speaker,
            target_role="elder",
            target_region="left_bottom",
            pass_index=2,
            existing_other_character=True,
        )
    except Exception as exc:
        return {
            "imageUrl": pass1_url,
            "raw": {
                "provider": image_provider.provider_name,
                "model": image_provider.model_name,
                "run_id": run_id,
                "status": "partial_success",
                "completed_passes": 1,
                "failed_pass": 2,
                "pass1_prompt": pass1_prompt,
                "pass1_output_sha256": pass1_output_sha,
                "pass2_base_sha256": pass2_base_sha,
                **preset_metadata,
                **_error_metadata(exc),
            },
        }

    retry_limit = max(0, settings.volcengine_image_pass2_retries)
    pass2_image: Image.Image | None = None
    last_error: Exception | None = None
    attempts = 0
    prompt_used = pass2_prompt

    for attempt_index in range(retry_limit + 1):
        attempts = attempt_index + 1
        if attempt_index > 0:
            prompt_used = PromptCompiler.compile_for_region(
                five_layer_plan=five_layer_plan,
                semantic_visual_instruction=semantic_visual_instruction,
                generation_stage="first_voice",
                speaker_role=normalized_speaker,
                target_role="elder",
                target_region="left_bottom",
                pass_index=2,
                existing_other_character=True,
                compact=True,
            )
        try:
            pass2_image = await image_provider.edit(
                pass2_base,
                prompt_used,
                identity_images=[elder_identity],
                mask=None,
                size=settings.volcengine_image_size,
            )
            break
        except Exception as exc:
            last_error = exc
            if isinstance(exc, ImageProviderError) and not exc.retryable:
                break

    if pass2_image is None:
        error = last_error or RuntimeError("Pass 2 returned no image")
        return {
            "imageUrl": pass1_url,
            "raw": {
                "provider": image_provider.provider_name,
                "model": image_provider.model_name,
                "run_id": run_id,
                "status": "partial_success",
                "completed_passes": 1,
                "failed_pass": 2,
                "pass1_prompt": pass1_prompt,
                "pass2_prompt": prompt_used,
                "pass1_path": str(pass1_path),
                "pass1_output_sha256": pass1_output_sha,
                "pass2_base_sha256": pass2_base_sha,
                "sha_match": pass1_output_sha == pass2_base_sha,
                "retry_count": max(0, attempts - 1),
                **preset_metadata,
                **_error_metadata(error),
                "errorCode": "SECOND_CHARACTER_INSERT_FAILED",
            },
        }

    final_path = _save_png(
        pass2_image,
        f"seedream-wallpaper-{run_id}.png",
    )
    final_url = _public_url(f"/generated/{final_path.name}")
    return {
        "imageUrl": final_url,
        "raw": {
            "provider": image_provider.provider_name,
            "model": image_provider.model_name,
            "run_id": run_id,
            "status": "completed",
            "completed_passes": 2,
            "speaker_role": normalized_speaker,
            **preset_metadata,
            "layout": {
                "child": "upper-right",
                "elder": "lower-left",
            },
            "source_mode": "designer_five_layer_seedream",
            "mask_used": False,
            "local_composite_used": False,
            "base_path": str(base_path),
            "pass1_path": str(pass1_path),
            "final_path": str(final_path),
            "pass1_prompt": pass1_prompt,
            "pass2_prompt": prompt_used,
            "final_prompt": prompt_used,
            "pass1_output_sha256": pass1_output_sha,
            "pass2_base_sha256": pass2_base_sha,
            "sha_match": pass1_output_sha == pass2_base_sha,
            "retry_count": max(0, attempts - 1),
        },
    }


async def generate_initial_shared_wallpaper(
    *,
    base_image_url: str,
    child_identity_bytes: bytes,
    elder_identity_bytes: bytes,
    five_layer_plan: dict[str, Any],
    semantic_visual_instruction: str,
    speaker_role: str,
    provider: ImageProvider | None = None,
) -> dict[str, Any]:
    """Create both paper-crafted people in one migrated V2 Seedream call.

    The image order matches the migrated prompt exactly: Image A is the base,
    Image B is the elder identity and Image C is the child identity. The
    current SemanticMappingAgent's five-layer output is consumed directly by
    the compiler, without another mapper or LLM call.
    """

    run_id = uuid.uuid4().hex
    try:
        normalized_speaker = normalize_v2_role(speaker_role)
        base_path = _resolve_base_image(base_image_url)
        if not base_path.is_file():
            raise FileNotFoundError(f"Base wallpaper not found: {base_path}")
        base_image = Image.open(base_path).convert("RGB")
        base_image.load()
        elder_identity = _load_image_bytes(elder_identity_bytes, label="elder")
        child_identity = _load_image_bytes(child_identity_bytes, label="child")
        image_provider = provider or VolcengineSeedreamProvider()
        prompt = compile_v2_for_both(
            five_layer_plan=five_layer_plan,
            semantic_visual_instruction=semantic_visual_instruction,
            speaker_role=normalized_speaker,
        )
    except Exception as exc:
        return {
            "imageUrl": "",
            "raw": {
                "provider": "volcengine_seedream",
                "run_id": run_id,
                "status": "failed",
                "failed_pass": 1,
                "prompt_version": "migrated_prompt_compiler_v2",
                **_error_metadata(exc),
            },
        }

    retry_limit = max(0, settings.volcengine_image_pass2_retries)
    output_image: Image.Image | None = None
    last_error: Exception | None = None
    attempts = 0
    for attempt_index in range(retry_limit + 1):
        attempts = attempt_index + 1
        try:
            output_image = await image_provider.edit(
                base_image,
                prompt,
                identity_images=[elder_identity, child_identity],
                mask=None,
                size=settings.volcengine_image_size,
            )
            break
        except Exception as exc:
            last_error = exc
            if isinstance(exc, ImageProviderError) and not exc.retryable:
                break

    if output_image is None:
        error = last_error or RuntimeError("Seedream V2 first voice returned no image")
        return {
            "imageUrl": "",
            "raw": {
                "provider": image_provider.provider_name,
                "model": image_provider.model_name,
                "run_id": run_id,
                "status": "failed",
                "failed_pass": 1,
                "prompt_version": "migrated_prompt_compiler_v2",
                "prompt": prompt,
                "retry_count": max(0, attempts - 1),
                **_error_metadata(error),
                "errorCode": "INITIAL_SHARED_WALLPAPER_FAILED",
            },
        }

    final_path = _save_png(output_image, f"seedream-wallpaper-{run_id}.png")
    return {
        "imageUrl": _public_url(f"/generated/{final_path.name}"),
        "raw": {
            "provider": image_provider.provider_name,
            "model": image_provider.model_name,
            "run_id": run_id,
            "status": "completed",
            "completed_passes": 1,
            "speaker_role": normalized_speaker,
            "prompt_version": "migrated_prompt_compiler_v2",
            "layout": {
                "elder": "lower-left",
                "child": "upper-right",
                "upper_right_relative_area": "1.1-1.2x_lower_left",
                "character_scale": "comparable_and_readable",
            },
            "source_mode": "five_layer_prompt_v2_seedream",
            "mask_used": False,
            "local_composite_used": False,
            "base_path": str(base_path),
            "final_path": str(final_path),
            "prompt": prompt,
            "final_prompt": prompt,
            "input_order": ["base", "elder_identity", "child_identity"],
            "identity_references_used": 2,
            "retry_count": max(0, attempts - 1),
        },
    }


async def reflow_shared_relationship_view(
    *,
    base_image_url: str,
    child_identity_bytes: bytes,
    elder_identity_bytes: bytes,
    five_layer_plan: dict[str, Any],
    semantic_visual_instruction: str,
    speaker_role: str,
    provider: ImageProvider | None = None,
) -> dict[str, Any]:
    """Move both existing people into the deterministic shared-space layout."""

    run_id = uuid.uuid4().hex
    try:
        normalized_speaker = normalize_v2_role(speaker_role)
        base_path = _resolve_base_image(base_image_url)
        if not base_path.is_file():
            raise FileNotFoundError(f"Base wallpaper not found: {base_path}")
        base_image = Image.open(base_path).convert("RGB")
        base_image.load()
        base_sha = _sha256_path(base_path)
        elder_identity = _load_image_bytes(elder_identity_bytes, label="elder")
        child_identity = _load_image_bytes(child_identity_bytes, label="child")
        image_provider = provider or VolcengineSeedreamProvider()
        prompt = compile_v2_for_reflow(
            five_layer_plan=five_layer_plan,
            semantic_visual_instruction=semantic_visual_instruction,
            speaker_role=normalized_speaker,
        )
    except Exception as exc:
        return {
            "imageUrl": "",
            "raw": {
                "provider": "volcengine_seedream",
                "run_id": run_id,
                "status": "failed",
                "generation_stage": "relationship_reflow",
                **_error_metadata(exc),
            },
        }

    retry_limit = max(0, settings.volcengine_image_update_retries)
    output_image: Image.Image | None = None
    last_error: Exception | None = None
    attempts = 0
    prompt_used = prompt
    for attempt_index in range(retry_limit + 1):
        attempts = attempt_index + 1
        if attempt_index > 0:
            prompt_used = compile_v2_for_reflow(
                five_layer_plan=five_layer_plan,
                semantic_visual_instruction=semantic_visual_instruction,
                speaker_role=normalized_speaker,
            )
        try:
            output_image = await image_provider.edit(
                base_image,
                prompt_used,
                identity_images=[elder_identity, child_identity],
                mask=None,
                size=settings.volcengine_image_size,
            )
            break
        except Exception as exc:
            last_error = exc
            if isinstance(exc, ImageProviderError) and not exc.retryable:
                break

    if output_image is None:
        error = last_error or RuntimeError("Seedream relationship reflow returned no image")
        return {
            "imageUrl": "",
            "raw": {
                "provider": image_provider.provider_name,
                "model": image_provider.model_name,
                "run_id": run_id,
                "status": "failed",
                "generation_stage": "relationship_reflow",
                "speaker_role": normalized_speaker,
                "base_path": str(base_path),
                "base_sha256": base_sha,
                "prompt": prompt_used,
                "retry_count": max(0, attempts - 1),
                **_error_metadata(error),
                "errorCode": "RELATIONSHIP_REFLOW_FAILED",
            },
        }

    final_path = _save_png(output_image, f"seedream-reflow-{run_id}.png")
    layout_state: dict[str, Any] = {}
    l2 = five_layer_plan.get("L2_relational_structure_layer")
    if isinstance(l2, dict):
        if isinstance(l2.get("layoutState"), dict):
            layout_state = l2["layoutState"]
        else:
            controls = l2.get("deterministicControls")
            if isinstance(controls, dict) and isinstance(controls.get("layoutState"), dict):
                layout_state = controls["layoutState"]
    return {
        "imageUrl": _public_url(f"/generated/{final_path.name}"),
        "raw": {
            "provider": image_provider.provider_name,
            "model": image_provider.model_name,
            "run_id": run_id,
            "status": "completed",
            "generation_stage": "relationship_reflow",
            "source_mode": "five_layer_relationship_reflow_seedream",
            "prompt_version": "migrated_prompt_compiler_v2",
            "speaker_role": normalized_speaker,
            "layout": layout_state,
            "base_path": str(base_path),
            "base_sha256": base_sha,
            "final_path": str(final_path),
            "prompt": prompt_used,
            "final_prompt": prompt_used,
            "input_order": ["base", "elder_identity", "child_identity"],
            "identity_references_used": 2,
            "retry_count": max(0, attempts - 1),
            "mask_used": False,
            "local_composite_used": False,
        },
    }


async def update_shared_wallpaper(
    *,
    base_image_url: str,
    speaker_identity_bytes: bytes,
    five_layer_plan: dict[str, Any],
    semantic_visual_instruction: str,
    speaker_role: str,
    provider: ImageProvider | None = None,
) -> dict[str, Any]:
    """Update only the current speaker's fixed region in one Seedream pass.

    The current shared wallpaper is the authoritative base. Seedream receives
    that image first and the speaker identity reference second. Region
    isolation is prompt-controlled; no provider mask or local composite is
    used.
    """

    run_id = uuid.uuid4().hex
    try:
        normalized_speaker = normalize_v2_role(speaker_role)
        editing_region = (
            "upper-right" if normalized_speaker == "child" else "lower-left"
        )
        preserve_role = "elder" if normalized_speaker == "child" else "child"
        preserve_region = (
            "lower-left" if preserve_role == "elder" else "upper-right"
        )

        base_path = _resolve_base_image(base_image_url)
        if not base_path.is_file():
            raise FileNotFoundError(f"Base wallpaper not found: {base_path}")
        base_image = Image.open(base_path).convert("RGB")
        base_image.load()
        base_sha = _sha256_path(base_path)
        speaker_identity = _load_image_bytes(
            speaker_identity_bytes,
            label=normalized_speaker,
        )
        image_provider = provider or VolcengineSeedreamProvider()
        prompt = compile_v2_for_update(
            five_layer_plan=five_layer_plan,
            semantic_visual_instruction=semantic_visual_instruction,
            speaker_role=normalized_speaker,
        )
    except Exception as exc:
        return {
            "imageUrl": "",
            "raw": {
                "provider": "volcengine_seedream",
                "run_id": run_id,
                "status": "failed",
                "generation_stage": "subsequent_update",
                **_error_metadata(exc),
            },
        }

    retry_limit = max(0, settings.volcengine_image_update_retries)
    output_image: Image.Image | None = None
    last_error: Exception | None = None
    attempts = 0
    prompt_used = prompt

    for attempt_index in range(retry_limit + 1):
        attempts = attempt_index + 1
        if attempt_index > 0:
            prompt_used = compile_v2_for_update(
                five_layer_plan=five_layer_plan,
                semantic_visual_instruction=semantic_visual_instruction,
                speaker_role=normalized_speaker,
            )
        try:
            output_image = await image_provider.edit(
                base_image,
                prompt_used,
                identity_images=[speaker_identity],
                mask=None,
                size=settings.volcengine_image_size,
            )
            break
        except Exception as exc:
            last_error = exc
            if isinstance(exc, ImageProviderError) and not exc.retryable:
                break

    if output_image is None:
        error = last_error or RuntimeError("Seedream update returned no image")
        return {
            "imageUrl": "",
            "raw": {
                "provider": image_provider.provider_name,
                "model": image_provider.model_name,
                "run_id": run_id,
                "status": "failed",
                "generation_stage": "subsequent_update",
                "speaker_role": normalized_speaker,
                "editing_region": editing_region,
                "preserve_role": preserve_role,
                "preserve_region": preserve_region,
                "base_path": str(base_path),
                "base_sha256": base_sha,
                "prompt": prompt_used,
                "retry_count": max(0, attempts - 1),
                **_error_metadata(error),
                "errorCode": "SPEAKER_REGION_UPDATE_FAILED",
            },
        }

    final_path = _save_png(
        output_image,
        f"seedream-update-{normalized_speaker}-{run_id}.png",
    )
    return {
        "imageUrl": _public_url(f"/generated/{final_path.name}"),
        "raw": {
            "provider": image_provider.provider_name,
            "model": image_provider.model_name,
            "run_id": run_id,
            "status": "completed",
            "generation_stage": "subsequent_update",
            "source_mode": "five_layer_prompt_v2_seedream",
            "prompt_version": "migrated_prompt_compiler_v2",
            "speaker_role": normalized_speaker,
            "editing_region": editing_region,
            "preserve_role": preserve_role,
            "preserve_region": preserve_region,
            "base_path": str(base_path),
            "base_sha256": base_sha,
            "final_path": str(final_path),
            "prompt": prompt_used,
            "retry_count": max(0, attempts - 1),
            "identity_reference_used": True,
            "mask_used": False,
            "local_composite_used": False,
        },
    }
