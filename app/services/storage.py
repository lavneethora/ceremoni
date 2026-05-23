import os

import aiofiles

from app.config import settings


class LocalStorage:
    def __init__(self):
        self.root = settings.storage_path

    def _path(self, student_id: str, filename: str) -> str:
        directory = os.path.join(self.root, student_id)
        os.makedirs(directory, exist_ok=True)
        return os.path.join(directory, filename)

    async def save(self, student_id: str, filename: str, data: bytes) -> str:
        path = self._path(student_id, filename)
        async with aiofiles.open(path, "wb") as f:
            await f.write(data)
        return path

    async def load(self, student_id: str, filename: str) -> bytes:
        path = self._path(student_id, filename)
        async with aiofiles.open(path, "rb") as f:
            return await f.read()

    def get_path(self, student_id: str, filename: str) -> str:
        return self._path(student_id, filename)


storage = LocalStorage()
