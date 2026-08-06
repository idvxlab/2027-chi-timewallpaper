from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    app_port: int = 8000
    app_cors_origins: str = "http://localhost:3000"
    database_url: str = "sqlite:///./data/app.db"
    redis_url: str = ""
    render_queue_enabled: bool = False
    render_queue_name: str = "arq:wallpaper"
    render_job_timeout_seconds: int = 420
    render_job_max_tries: int = 3
    render_worker_max_jobs: int = 2
    render_family_lock_seconds: int = 480
    render_retry_delays: str = "5,20,60"
    wallpaper_event_channel_prefix: str = "timewallpaper:wallpaper"

    storage_local_dir: str = "./data/uploads"
    public_api_base_url: str = "http://127.0.0.1:8000"
    static_base_scene_url: str = (
        "/generated/wallpaper-main-square-1536.png"
    )

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
    doubao_asr_api_key: str = ""
    doubao_asr_resource_id: str = "volc.bigasr.auc_turbo"
    doubao_streaming_asr_endpoint: str = (
        "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_nostream"
    )
    doubao_streaming_asr_resource_id: str = "volc.seedasr.sauc.duration"
    doubao_streaming_asr_ca_bundle: str = "/etc/ssl/cert.pem"
    doubao_streaming_asr_connect_timeout_seconds: float = 10
    doubao_streaming_asr_result_timeout_seconds: float = 30
    doubao_streaming_asr_max_audio_bytes: int = 2_000_000

    # Text LLM can use a provider independent from ASR and image generation.
    # Empty values preserve the legacy shared PROVIDER_* configuration.
    llm_api_base_url: str = ""
    llm_api_key: str = ""
    llm_chat_endpoint: str = ""
    llm_chat_model: str = "deepseek-v4-pro"
    llm_temperature: float = 0.2

    # ChatBot can be tuned or migrated independently from the semantic agents.
    # Empty provider fields inherit the shared text-LLM configuration.
    chatbot_llm_api_base_url: str = ""
    chatbot_llm_api_key: str = ""
    chatbot_llm_chat_endpoint: str = ""
    chatbot_llm_model: str = ""
    chatbot_llm_temperature: float = 0.2
    chatbot_enable_thinking: bool = False
    chatbot_timeout_seconds: float = 12

    image_chat_endpoint: str = ""
    image_edit_endpoint: str = ""
    image_chat_model: str = "gpt-image-2"
    image_aspect_ratio: str = "1:1"
    image_size: str = "1536x1536"
    image_request_timeout_seconds: float = 420
    image_request_retries: int = 0
    image_download_timeout_seconds: float = 60
    layered_image_tools_enabled: bool = True

    # Volcengine Seedream shared-wallpaper generation and regional editing.
    volcengine_image_api_key: str = ""
    volcengine_image_base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    volcengine_image_model: str = "doubao-seedream-5-0-260128"
    volcengine_image_size: str = "1536x1536"
    volcengine_image_response_format: str = "b64_json"
    volcengine_image_timeout_seconds: float = 300
    volcengine_image_max_download_bytes: int = 50_000_000
    volcengine_image_pass2_retries: int = 1
    volcengine_image_update_retries: int = 1

    # Character assets use Seedream too, but keep their output contract
    # independent from the square shared-wallpaper canvas.
    character_image_size: str = "768x1024"
    character_image_timeout_seconds: float = 300
    character_image_retries: int = 1

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
        return self.llm_api_base_url or self.provider_api_base_url

    @property
    def effective_llm_api_key(self) -> str:
        return self.llm_api_key or self.provider_api_key

    @property
    def effective_chatbot_llm_api_base_url(self) -> str:
        return self.chatbot_llm_api_base_url or self.effective_llm_api_base_url

    @property
    def effective_chatbot_llm_api_key(self) -> str:
        return self.chatbot_llm_api_key or self.effective_llm_api_key

    @property
    def effective_chatbot_llm_chat_endpoint(self) -> str:
        return self.chatbot_llm_chat_endpoint or self.llm_chat_endpoint

    @property
    def effective_chatbot_llm_model(self) -> str:
        return self.chatbot_llm_model or self.llm_chat_model

    @property
    def effective_image_api_base_url(self) -> str:
        return self.provider_api_base_url

    @property
    def effective_image_api_key(self) -> str:
        return self.provider_api_key

    @property
    def parsed_render_retry_delays(self) -> tuple[int, ...]:
        values: list[int] = []
        for item in self.render_retry_delays.split(","):
            try:
                values.append(max(1, int(item.strip())))
            except ValueError:
                continue
        return tuple(values or (5, 20, 60))


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
