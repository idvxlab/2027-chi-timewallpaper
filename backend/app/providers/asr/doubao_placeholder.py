"""豆包 ASR 占位。"""
from app.providers.asr.base import ASRProvider


class DoubaoProvider(ASRProvider):
    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    async def transcribe(self, audio: bytes, content_type: str) -> dict:
        return {
            "transcript": "[doubao] (未连接真实服务)",
            "raw": {"provider": "doubao", "size": len(audio), "content_type": content_type},
        }
