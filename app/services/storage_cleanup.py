"""
Find audio in storage that nothing in the database points at any more.

Deleting a student row does not touch their audio, and nothing else reconciles
the two, so files accumulate: a bucket holding 24 students' recordings was
found with 295 files in it, including ten copies of one recording. This finds
what is unreferenced and, when asked, removes it.

Works off the database as the source of truth: a file is orphaned when no
recording and no session entry names it.
"""

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Recording, SessionEntry
from app.services.storage import storage

PREFIX = "supabase://"


async def _referenced_keys(db: AsyncSession) -> set[str]:
    keys: set[str] = set()
    for recording in (await db.execute(select(Recording))).scalars():
        for path in (
            recording.original_audio_url,
            recording.cleaned_audio_url,
            recording.generated_audio_url,
        ):
            if path and path.startswith(PREFIX):
                keys.add(path[len(PREFIX):])
    for entry in (await db.execute(select(SessionEntry))).scalars():
        path = entry.announcement_audio_path
        if path and path.startswith(PREFIX):
            keys.add(path[len(PREFIX):])
    return keys


def _list(client: httpx.Client, prefix: str) -> list[dict]:
    out: list[dict] = []
    offset = 0
    while True:
        resp = client.post(
            f"{settings.supabase_url.rstrip('/')}/storage/v1/object/list/{settings.supabase_bucket}",
            json={"prefix": prefix, "limit": 1000, "offset": offset},
        )
        resp.raise_for_status()
        batch = resp.json()
        out += batch
        if len(batch) < 1000:
            break
        offset += 1000
    return out


def _stored_keys() -> list[str]:
    headers = {
        "Authorization": f"Bearer {settings.supabase_service_key}",
        "apikey": settings.supabase_service_key,
        "Content-Type": "application/json",
    }
    keys: list[str] = []
    with httpx.Client(headers=headers, timeout=60) as client:
        for folder in [f["name"] for f in _list(client, "") if f.get("id") is None]:
            for item in _list(client, folder + "/"):
                if item.get("id") is not None:
                    keys.append(f"{folder}/{item['name']}")
    return keys


async def find_orphans(db: AsyncSession) -> list[str]:
    """Storage keys that no database row points at."""
    referenced = await _referenced_keys(db)
    return sorted(key for key in _stored_keys() if key not in referenced)


async def delete_orphans(db: AsyncSession, dry_run: bool = True) -> dict:
    """Report unreferenced audio, and delete it unless this is a dry run."""
    orphans = await find_orphans(db)
    deleted = 0
    if not dry_run:
        for key in orphans:
            try:
                if await storage.delete_from_path(PREFIX + key):
                    deleted += 1
            except Exception as e:
                print(f"Storage cleanup: could not delete {key}: {e}")
    return {
        "status": "ok",
        "dry_run": dry_run,
        "orphans": len(orphans),
        "deleted": deleted,
        "files": orphans[:100],
    }
