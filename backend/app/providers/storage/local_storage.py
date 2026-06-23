import os
from pathlib import Path

from app.providers.storage.base import Storage


class LocalStorage(Storage):
    def __init__(self, base_dir: str) -> None:
        self.base_dir = Path(base_dir).resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    async def save(self, key: str, data: bytes) -> str:
        target = self.base_dir / key
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "wb") as f:
            f.write(data)
        return str(target.relative_to(self.base_dir.parent))
