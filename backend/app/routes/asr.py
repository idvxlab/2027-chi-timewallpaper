from fastapi import APIRouter, File, HTTPException, UploadFile

from app.services.pipeline_service import recognize_audio, transcribe_audio

router = APIRouter()

# Demo-only fallback transcript returned by /asr/transcribe when the
# upstream speech-to-text provider fails. Keeps the demo loop unblocked
# for the regional-inpaint step later.
TRANSCRIBE_FALLBACK_TEXT = "我在工作"


@router.post("")
async def asr(audio: UploadFile = File(...)) -> dict:
    raw = await audio.read()
    return await recognize_audio(
        raw,
        content_type=audio.content_type or "audio/wav",
        filename=audio.filename or "recording.wav",
    )


@router.post("/transcribe")
async def asr_transcribe(audio: UploadFile = File(...)) -> dict:
    """Pure ASR endpoint for the PreludeStep flow.

    Only transcribes the uploaded audio — does NOT touch image generation.
    The legacy ``POST /asr`` keeps its old (recognize_audio → wallpaper)
    behaviour to preserve V1 callers.

    On any error from ``transcribe_audio`` we log the real upstream
    detail and return a fallback transcript so the front-end demo loop
    never stalls on a transient ASR failure. ``raw.provider`` is set to
    ``"fallback_after_error"`` so callers can tell the response was
    fabricated rather than transcribed.
    """
    filename = audio.filename or "recording.webm"
    content_type = audio.content_type or "audio/webm"
    raw = await audio.read()

    print(f"[asr.route] POST /asr/transcribe received")
    print(
        f"[asr.route] audio filename={filename!r} "
        f"content_type={content_type!r} bytes={len(raw)}"
    )

    try:
        result = await transcribe_audio(
            audio=raw,
            filename=filename,
            content_type=content_type,
        )
    except HTTPException as exc:
        print(
            f"[asr.route] transcribe failed "
            f"status={exc.status_code} detail={exc.detail}"
        )
        print(
            f"[asr.route] returning fallback transcript = "
            f"{TRANSCRIBE_FALLBACK_TEXT!r}"
        )
        return {
            "transcript": TRANSCRIBE_FALLBACK_TEXT,
            "raw": {
                "provider": "fallback_after_error",
                "error_status": exc.status_code,
                "error": str(exc.detail),
            },
        }
    except Exception as exc:  # noqa: BLE001 — surface every failure
        print(f"[asr.route] transcribe unexpected error: {exc!r}")
        print(
            f"[asr.route] returning fallback transcript = "
            f"{TRANSCRIBE_FALLBACK_TEXT!r}"
        )
        return {
            "transcript": TRANSCRIBE_FALLBACK_TEXT,
            "raw": {
                "provider": "fallback_after_error",
                "error_status": 500,
                "error": repr(exc),
            },
        }

    print(f"[asr.route] transcript = {result.get('transcript')!r}")
    return {
        "transcript": result.get("transcript", ""),
        "raw": result.get("raw"),
    }