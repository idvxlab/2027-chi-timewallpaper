from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    app_port: int = 8000
    app_cors_origins: str = "http://localhost:3000"

    storage_local_dir: str = "./data/uploads"

    provider_api_base_url: str = ""
    provider_api_key: str = ""

    audio_transcription_endpoint: str = ""
    audio_transcription_model: str = "whisper-1"
    audio_transcription_language: str = "zh"

    llm_chat_endpoint: str = ""
    llm_chat_model: str = "gpt-4o-mini"
    llm_temperature: float = 0.2

    image_chat_endpoint: str = ""
    image_chat_model: str = "gpt-4o-image"
    image_aspect_ratio: str = "9:16"
    image_size: str = "1K"
    image_request_timeout_seconds: float = 420
    image_request_retries: int = 0
    image_download_timeout_seconds: float = 60

    @property
    def effective_audio_api_base_url(self) -> str:
        return self.provider_api_base_url

    @property
    def effective_audio_api_key(self) -> str:
        return self.provider_api_key

    @property
    def effective_llm_api_base_url(self) -> str:
        return self.provider_api_base_url

    @property
    def effective_llm_api_key(self) -> str:
        return self.provider_api_key

    @property
    def effective_image_api_base_url(self) -> str:
        return self.provider_api_base_url

    @property
    def effective_image_api_key(self) -> str:
        return self.provider_api_key


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
