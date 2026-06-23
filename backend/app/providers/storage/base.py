from abc import ABC, abstractmethod


class Storage(ABC):
    @abstractmethod
    async def save(self, key: str, data: bytes) -> str:
        raise NotImplementedError
