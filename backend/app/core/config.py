from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


# Resolve project root so the backend can share the root-level .env with the
# frontend regardless of the directory uvicorn is launched from.
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_ENV_CANDIDATES = (_PROJECT_ROOT / ".env", Path(__file__).resolve().parents[1] / ".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=[str(p) for p in _ENV_CANDIDATES],
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"
    app_port: int = 8000
    app_cors_origins: str = "http://localhost:3000"

    storage_local_dir: str = "./data/uploads"

    audio_api_base_url: str = ""
    audio_api_key: str = ""
    audio_transcription_endpoint: str = ""
    audio_transcription_model: str = "whisper-1"
    audio_transcription_language: str = "zh"

    image_api_base_url: str = ""
    image_api_key: str = ""
    image_chat_endpoint: str = ""
    image_chat_model: str = "gpt-4o-image"

    # Gemini native generation path. When openai_api_key is set the
    # pipeline uses Gemini instead of the OpenAI-compatible image endpoint.
    openai_api_key: str = ""
    gemini_base_url: str = "https://api.bltcy.ai"
    gemini_image_model: str = "gemini-2.5-flash-image"

    # Public origin the *frontend* uses to fetch static assets served by
    # this backend. Defaults to the same dev port as the API so the URL
    # returned by /generate-wallpaper works for browsers hitting
    # localhost:3000 (which would otherwise resolve "/generated/..." to
    # localhost:3000 and 404). Override in production (e.g. behind a CDN).
    public_api_base_url: str = "http://localhost:8000"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
