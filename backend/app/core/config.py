from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

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


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
