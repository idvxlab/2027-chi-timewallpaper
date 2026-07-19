from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    app_port: int = 8000
    app_cors_origins: str = "http://localhost:3000"

    storage_local_dir: str = "./data/uploads"
    public_api_base_url: str = "http://127.0.0.1:8000"

    provider_api_base_url: str = ""
    provider_api_key: str = ""

    # Compatibility fields for copied layered wallpaper tools.
    # Prefer PROVIDER_* in normal use; these names let the original tool code run unchanged.
    openai_api_key: str = ""
    gemini_base_url: str = ""
    gemini_image_model: str = "gemini-2.5-flash-image"
    audio_api_base_url: str = ""
    audio_api_key: str = ""
    image_api_base_url: str = ""
    image_api_key: str = ""

    audio_transcription_endpoint: str = ""
    audio_transcription_model: str = "whisper-1"
    audio_transcription_language: str = "zh"

    llm_chat_endpoint: str = ""
    llm_chat_model: str = "gpt-4o-mini"
    llm_temperature: float = 0.2

    image_chat_endpoint: str = ""
    image_edit_endpoint: str = ""
    image_chat_model: str = "gpt-image-2"
    image_aspect_ratio: str = "9:16"
    image_size: str = "1024x1536"
    image_request_timeout_seconds: float = 420
    image_request_retries: int = 0
    image_download_timeout_seconds: float = 60
    layered_image_tools_enabled: bool = True

    def model_post_init(self, __context) -> None:
        if not self.openai_api_key:
            self.openai_api_key = self.provider_api_key
        if not self.gemini_base_url:
            self.gemini_base_url = self.provider_api_base_url
        if not self.audio_api_base_url:
            self.audio_api_base_url = self.provider_api_base_url
        if not self.audio_api_key:
            self.audio_api_key = self.provider_api_key
        if not self.image_api_base_url:
            self.image_api_base_url = self.provider_api_base_url
        if not self.image_api_key:
            self.image_api_key = self.provider_api_key

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
