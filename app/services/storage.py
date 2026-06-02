import os

import aiofiles
import httpx

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

    async def load_from_path(self, path: str) -> bytes:
        async with aiofiles.open(path, "rb") as f:
            return await f.read()

    def get_path(self, student_id: str, filename: str) -> str:
        return self._path(student_id, filename)

    def get_public_url(self, path: str) -> str | None:
        return None  # Local files don't have public URLs


class SupabaseStorage:
    def __init__(self):
        self.url = settings.supabase_url.rstrip("/")
        self.key = settings.supabase_service_key
        self.bucket = settings.supabase_bucket
        self.headers = {
            "Authorization": f"Bearer {self.key}",
            "apikey": self.key,
        }

    def _key(self, student_id: str, filename: str) -> str:
        return f"{student_id}/{filename}"

    async def save(self, student_id: str, filename: str, data: bytes) -> str:
        key = self._key(student_id, filename)
        upload_url = f"{self.url}/storage/v1/object/{self.bucket}/{key}"

        # Detect content type from extension
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        content_type_map = {
            "wav": "audio/wav", "mp3": "audio/mpeg", "m4a": "audio/mp4",
            "mp4": "audio/mp4", "ogg": "audio/ogg", "webm": "audio/webm",
            "flac": "audio/flac", "aac": "audio/aac", "opus": "audio/opus",
        }
        content_type = content_type_map.get(ext, "application/octet-stream")

        async with httpx.AsyncClient(timeout=60) as client:
            # Upsert by deleting first if exists, then uploading
            await client.delete(upload_url, headers=self.headers)
            resp = await client.post(
                upload_url,
                headers={**self.headers, "Content-Type": content_type},
                content=data,
            )
            if resp.status_code not in (200, 201):
                raise Exception(f"Supabase upload failed: {resp.status_code} {resp.text[:200]}")

        # Return the storage key (not full URL) — we'll resolve to URL on read
        return f"supabase://{key}"

    async def load(self, student_id: str, filename: str) -> bytes:
        key = self._key(student_id, filename)
        return await self._download(key)

    async def load_from_path(self, path: str) -> bytes:
        # path looks like "supabase://student_id/filename"
        if path.startswith("supabase://"):
            key = path[len("supabase://"):]
        else:
            key = path
        return await self._download(key)

    async def _download(self, key: str) -> bytes:
        url = f"{self.url}/storage/v1/object/public/{self.bucket}/{key}"
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                raise FileNotFoundError(f"File not found in Supabase: {key} ({resp.status_code})")
            return resp.content

    def get_path(self, student_id: str, filename: str) -> str:
        return f"supabase://{self._key(student_id, filename)}"

    def get_public_url(self, path: str) -> str | None:
        if path.startswith("supabase://"):
            key = path[len("supabase://"):]
            return f"{self.url}/storage/v1/object/public/{self.bucket}/{key}"
        return None


# Pick backend based on whether Supabase is configured
if settings.supabase_url and settings.supabase_service_key:
    storage = SupabaseStorage()
    print(f"Storage: Using Supabase bucket '{settings.supabase_bucket}'")
else:
    storage = LocalStorage()
    print(f"Storage: Using local filesystem at '{settings.storage_path}'")
