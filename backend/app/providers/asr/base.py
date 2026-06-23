from abc import ABC, abstractmethod


class ASRProvider(ABC):
    @abstractmethod
    async def transcribe(self, audio: bytes, content_type: str) -> dict:
        """返回 {"transcript": str, "raw": dict}"""
        raise NotImplementedError
