from __future__ import annotations

import json
from typing import Any, Optional

import httpx

from app.core.config import settings
from app.providers.llm.base import LLMProvider


class OpenAICompatibleLLM(LLMProvider):
    def __init__(
        self,
        api_key: str,
        api_base_url: str,
        model: str,
        chat_endpoint: str = "",
    ) -> None:
        self.api_key = api_key
        self.api_base_url = api_base_url.rstrip("/")
        self.model = model
        self.chat_endpoint = chat_endpoint.strip()

    @property
    def configured(self) -> bool:
        return bool(self.api_key and (self.chat_endpoint or self.api_base_url))

    async def chat(self, prompt: str, **kwargs) -> str:
        if not self.configured:
            raise RuntimeError("LLM provider is not configured")

        endpoint = self.chat_endpoint or f"{self.api_base_url}/v1/chat/completions"
        system = kwargs.get("system", "你是一个严格按照指令输出的中文 AI 助手。")
        temperature = kwargs.get("temperature", settings.llm_temperature)
        response_format = kwargs.get("response_format")
        enable_thinking = kwargs.get("enable_thinking")
        max_tokens = kwargs.get("max_tokens")
        payload: dict[str, Any] = {
            "model": kwargs.get("model") or self.model,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        }
        if response_format:
            payload["response_format"] = response_format
        if enable_thinking is not None:
            payload["enable_thinking"] = bool(enable_thinking)
        if max_tokens is not None:
            payload["max_tokens"] = int(max_tokens)

        async with httpx.AsyncClient(timeout=kwargs.get("timeout", 90)) as client:
            response = await client.post(
                endpoint,
                json=payload,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
            response.raise_for_status()
            data = response.json()

        content = self._extract_content(data)
        if not content:
            raise RuntimeError(f"LLM response did not contain message content: {json.dumps(data, ensure_ascii=False)[:500]}")
        return content

    def _extract_content(self, data: Any) -> str:
        if isinstance(data, dict):
            choices = data.get("choices")
            if isinstance(choices, list) and choices:
                message = choices[0].get("message") if isinstance(choices[0], dict) else None
                if isinstance(message, dict):
                    content = message.get("content")
                    if isinstance(content, str):
                        return content.strip()
            for key in ("content", "text", "output_text"):
                value = data.get(key)
                if isinstance(value, str):
                    return value.strip()
        return ""


def get_llm_provider() -> Optional[OpenAICompatibleLLM]:
    provider = OpenAICompatibleLLM(
        api_key=settings.effective_llm_api_key,
        api_base_url=settings.effective_llm_api_base_url,
        model=settings.llm_chat_model,
        chat_endpoint=settings.llm_chat_endpoint,
    )
    return provider if provider.configured else None
