"""OpenAI Whisper 远程 ASR Provider (占位实现)。"""
from app.providers.asr.base import ASRProvider


class WhisperProvider(ASRProvider):
    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    async def transcribe(self, audio: bytes, content_type: str) -> dict:
        # 真实实现: 调用 OpenAI Audio Transcriptions API
        # 这里仅返回占位结果,避免在缺少依赖时崩溃
        return {
            "transcript": "[whisper] (未连接真实服务)",
            "raw": {"provider": "whisper", "size": len(audio), "content_type": content_type},
        }
