"""
Per ceremony announcement clips: name, major and honors level as one audio file.

A clip is ready when its stored key matches the current inputs (IPA, name,
major, honors level, voice, template version). Changing any of them makes the
clip stale, and a stale or missing clip is never played.
"""

import asyncio
import hashlib
import traceback

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.services.config_loader import get_session_options
from app.db import async_session
from app.models import SessionEntry, Student
from app.services import tts_generator
from app.services.storage import storage

TEMPLATE_VERSION = 1
HONORS_PHRASES = {"honors": "with honors", "highest_honors": "with highest honors"}

# About 15 requests a minute, under the Azure free tier limit
THROTTLE_SECONDS = 4.0
RETRY_WAITS = (15, 30, 60)

_running: set[str] = set()
_progress: dict[str, dict] = {}
_tasks: set[asyncio.Task] = set()


class AnnouncementNotReady(Exception):
    pass


def _ipa_for(student: Student) -> str | None:
    for recording in sorted(student.recordings, key=lambda r: r.id):
        if recording.ipa_representation:
            return recording.ipa_representation
    return None


def effective_major(entry: SessionEntry, student: Student) -> str | None:
    return entry.major or student.major


def announcement_key(entry: SessionEntry, student: Student) -> str:
    raw = "|".join(
        [
            str(TEMPLATE_VERSION),
            settings.tts_voice,
            _ipa_for(student) or "",
            student.typed_name,
            effective_major(entry, student) or "",
            entry.honors_level or "",
        ]
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


FIELD_LABELS = {"major": "a major", "honors": "an honors level"}


def missing_required(entry: SessionEntry, student: Student, required) -> list[str]:
    """Which parts this session insists on that the entry does not have.

    An announcement with a gap in it still sounds perfectly fine, which is the
    problem: a Medallion student read out without their distinction is wrong
    in a way nobody notices until it has already happened. So a session can
    say the parts are not optional, and an entry missing one is left without a
    clip rather than given a shortened one.
    """
    gaps = []
    if "major" in required and not effective_major(entry, student):
        gaps.append("major")
    # Covers both a blank cell and a value the roster importer did not
    # recognise, since an unrecognised distinction lands as None too.
    if "honors" in required and entry.honors_level not in HONORS_PHRASES:
        gaps.append("honors")
    return gaps


def announcement_state(entry: SessionEntry, student: Student) -> str:
    """One of "ready", "stale" or "missing"."""
    if not entry.announcement_audio_path or not entry.announcement_key:
        return "missing"
    return "ready" if entry.announcement_key == announcement_key(entry, student) else "stale"


async def _load_entries(db: AsyncSession, session_id: str) -> list[SessionEntry]:
    result = await db.execute(
        select(SessionEntry)
        .where(SessionEntry.session_id == session_id)
        .options(selectinload(SessionEntry.student).selectinload(Student.recordings))
    )
    return list(result.scalars().all())


async def get_ready_entry(db: AsyncSession, session_id: str, student_id: str) -> SessionEntry:
    """The entry to announce. LookupError if not on the roster, AnnouncementNotReady if no current clip."""
    result = await db.execute(
        select(SessionEntry)
        .where(SessionEntry.session_id == session_id, SessionEntry.student_id == student_id)
        .options(selectinload(SessionEntry.student).selectinload(Student.recordings))
    )
    entry = result.scalar_one_or_none()
    if entry is None:
        raise LookupError("Student is not on this session's roster")

    state = announcement_state(entry, entry.student)
    if state != "ready":
        reason = "out of date" if state == "stale" else "not generated"
        raise AnnouncementNotReady(
            f"The announcement for {entry.student.typed_name} is {reason}. Generate announcements first."
        )

    # Checked again at the moment of announcing, not only when the clip was
    # made. A clip generated before this session required a distinction, or
    # before the rule existed, would otherwise still be read out with the
    # distinction missing, which is the one thing this must not do.
    gaps = missing_required(entry, entry.student, get_session_options(session_id)["requires"])
    if gaps:
        raise AnnouncementNotReady(
            f"{entry.student.typed_name} needs "
            + " and ".join(FIELD_LABELS[g] for g in gaps)
            + " on the roster before they can be announced."
        )
    return entry


async def announcement_status(db: AsyncSession, session_id: str) -> dict:
    entries = await _load_entries(db, session_id)
    counts = {"ready": 0, "stale": 0, "missing": 0}
    no_recording = 0
    required = get_session_options(session_id)["requires"]
    incomplete = []
    for entry in entries:
        counts[announcement_state(entry, entry.student)] += 1
        if _ipa_for(entry.student) is None:
            no_recording += 1
        gaps = missing_required(entry, entry.student, required)
        if gaps:
            incomplete.append(
                {"name": entry.student.typed_name, "missing": gaps}
            )
    return {
        "total": len(entries),
        **counts,
        "no_recording": no_recording,
        "incomplete": incomplete,
        "running": session_id in _running,
        "failed": list(_progress.get(session_id, {}).get("failed", [])),
    }


def _is_throttle(error: Exception) -> bool:
    text = str(error).lower()
    return "429" in text or "too many requests" in text or "throttl" in text


async def _generate_one(db: AsyncSession, entry: SessionEntry) -> None:
    student = entry.student
    key = announcement_key(entry, student)

    last_error = None
    for wait in (0,) + tuple(RETRY_WAITS):
        if wait:
            await asyncio.sleep(wait)
        try:
            audio = await tts_generator.generate_announcement(
                student.typed_name,
                _ipa_for(student),
                effective_major(entry, student),
                HONORS_PHRASES.get(entry.honors_level),
            )
            break
        except Exception as e:
            if not _is_throttle(e):
                raise
            last_error = e
    else:
        raise last_error

    # The key is in the file name so a changed clip gets a new URL and is never served from a cache
    path = await storage.save(student.id, f"{entry.id}_{key[:10]}_announce.mp3", audio)

    # That new name also means the old clip is now unreferenced, so remove it
    # rather than leaving a copy behind on every regeneration
    previous = entry.announcement_audio_path
    if previous and previous != path:
        try:
            await storage.delete_from_path(previous)
        except Exception as e:
            print(f"Announcements: could not remove the previous clip {previous}: {e}")

    entry.announcement_key = key
    entry.announcement_audio_path = path
    await db.commit()


async def _run(session_id: str) -> None:
    try:
        required = get_session_options(session_id)["requires"]
        async with async_session() as db:
            todo = []
            for e in await _load_entries(db, session_id):
                if announcement_state(e, e.student) == "ready":
                    continue
                gaps = missing_required(e, e.student, required)
                if gaps:
                    # No clip at all, rather than one that quietly leaves a
                    # part out. It surfaces here so the roster can be fixed
                    # before the ceremony instead of during it.
                    _progress[session_id]["failed"].append(
                        {
                            "name": e.student.typed_name,
                            "message": "needs "
                            + " and ".join(FIELD_LABELS[g] for g in gaps)
                            + " on the roster",
                        }
                    )
                    continue
                todo.append((e.id, e.student.typed_name))
        # Each entry gets its own session so one failure cannot affect the others
        for i, (entry_id, name) in enumerate(todo):
            try:
                async with async_session() as db:
                    result = await db.execute(
                        select(SessionEntry)
                        .where(SessionEntry.id == entry_id)
                        .options(selectinload(SessionEntry.student).selectinload(Student.recordings))
                    )
                    await _generate_one(db, result.scalar_one())
            except Exception as e:
                _progress[session_id]["failed"].append({"name": name, "message": str(e)})
            if i < len(todo) - 1:
                await asyncio.sleep(THROTTLE_SECONDS)
    except Exception:
        traceback.print_exc()
    finally:
        _running.discard(session_id)


def start_generation(session_id: str) -> bool:
    """Start a background run for the session. False if one is already running."""
    if session_id in _running:
        return False
    _running.add(session_id)
    _progress[session_id] = {"failed": []}
    task = asyncio.create_task(_run(session_id))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return True
