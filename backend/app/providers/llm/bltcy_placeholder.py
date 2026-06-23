"""豆包/字节 LLM 占位。"""
from app.providers.llm.base import LLMProvider


class BltcyLLM(LLMProvider):
    def __init__(self, api_key: str = "") -> None:
        self.api_key = api_key

    async def chat(self, prompt: str, **kwargs) -> str:
        return f"[bltcy-placeholder] echo: {prompt[:32]}"
