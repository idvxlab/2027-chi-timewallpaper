from abc import ABC, abstractmethod


class LLMProvider(ABC):
    @abstractmethod
    async def chat(self, prompt: str, **kwargs) -> str:
        raise NotImplementedError
