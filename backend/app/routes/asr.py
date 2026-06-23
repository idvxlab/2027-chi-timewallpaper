from fastapi import APIRouter, File, UploadFile

from app.services.pipeline_service import recognize_audio

router = APIRouter()


@router.post("")
async def asr(audio: UploadFile = File(...)) -> dict:
    raw = await audio.read()
    return await recognize_audio(
        raw,
        content_type=audio.content_type or "audio/wav",
        filename=audio.filename or "recording.wav",
    )
