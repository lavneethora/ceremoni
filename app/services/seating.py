"""
Seating for sessions with `order: seating`.

A student's seat is SessionEntry.seat_position (1, 2, 3, ...). Seats are appended
in the order students sit, so the announcer reads them in that order. Removing a
seat leaves a gap, which keeps every other student's number stable.
"""

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SessionCollege, SessionEntry, SessionState, Student
from app.services.session_queue import get_session_mode

MAX_APPEND_ATTEMPTS = 5


class SeatingConflict(Exception):
    """The request cannot be applied to the current seating."""


async def _entry_for(db: AsyncSession, session_id: str, student_id: str) -> SessionEntry | None:
    result = await db.execute(
        select(SessionEntry).where(
            SessionEntry.session_id == session_id, SessionEntry.student_id == student_id
        )
    )
    return result.scalar_one_or_none()


async def _next_position(db: AsyncSession, session_id: str) -> int:
    result = await db.execute(
        select(SessionEntry.seat_position)
        .where(SessionEntry.session_id == session_id, SessionEntry.seat_position.is_not(None))
        .order_by(SessionEntry.seat_position.desc())
        .limit(1)
    )
    return (result.scalar_one_or_none() or 0) + 1


async def _entry_or_new(db: AsyncSession, session_id: str, student_id: str) -> SessionEntry:
    """The student's entry, created for college based seating sessions. Raises LookupError."""
    entry = await _entry_for(db, session_id, student_id)
    if entry is not None:
        return entry
    if get_session_mode(session_id).roster:
        raise LookupError("Student is not on this session's roster")

    student = await db.get(Student, student_id)
    colleges = (
        await db.execute(select(SessionCollege.college).where(SessionCollege.session_id == session_id))
    ).scalars().all()
    if not student or student.college not in colleges:
        raise LookupError("Student is not part of this session")
    entry = SessionEntry(session_id=session_id, student_id=student_id)
    db.add(entry)
    return entry


async def seat_student(db: AsyncSession, session_id: str, student_id: str) -> tuple[SessionEntry, bool]:
    """Seat a student at the end of the line. Returns (entry, newly_seated).

    Seating someone who already has a seat changes nothing, so a double tap is harmless.
    Two ushers seating at the same moment may pick the same number, so the unique
    constraint decides and the loser retries with a fresh number.
    """
    for _ in range(MAX_APPEND_ATTEMPTS):
        entry = await _entry_or_new(db, session_id, student_id)
        if entry.seat_position is not None:
            return entry, False

        entry.seat_position = await _next_position(db, session_id)
        entry.checked_in_at = datetime.utcnow()
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            continue
        return entry, True

    raise SeatingConflict("Could not find a free seat number, try again")


async def unseat_student(db: AsyncSession, session_id: str, student_id: str) -> None:
    entry = await _entry_for(db, session_id, student_id)
    if entry is None or entry.seat_position is None:
        return
    if entry.played:
        raise SeatingConflict("This student has already been announced")
    entry.seat_position = None
    entry.checked_in_at = None
    await db.commit()


async def reorder_seating(db: AsyncSession, session_id: str, student_ids: list[str]) -> None:
    """Give the seated students positions 1..n in the order given.

    The list must be exactly the students seated right now, otherwise the page
    is stale and someone's seat would be lost or duplicated.
    """
    if len(set(student_ids)) != len(student_ids):
        raise SeatingConflict("The order lists a student twice")

    result = await db.execute(
        select(SessionEntry).where(
            SessionEntry.session_id == session_id, SessionEntry.seat_position.is_not(None)
        )
    )
    seated = {e.student_id: e for e in result.scalars().all()}
    if set(seated) != set(student_ids):
        raise SeatingConflict("The seating changed, reload and try again")

    # Negate first so no row ever collides with another row's old or new number
    await db.execute(
        update(SessionEntry)
        .where(SessionEntry.session_id == session_id, SessionEntry.seat_position.is_not(None))
        .values(seat_position=-SessionEntry.seat_position)
    )
    # Plain UPDATEs, because the loaded objects still hold the old numbers and an
    # unchanged-looking value would be skipped
    for position, student_id in enumerate(student_ids, start=1):
        await db.execute(
            update(SessionEntry)
            .where(SessionEntry.id == seated[student_id].id)
            .values(seat_position=position)
        )
    await db.commit()


async def clear_seating(db: AsyncSession, session_id: str) -> None:
    """Remove every seat and reset the played state so the session can start over."""
    await db.execute(
        update(SessionEntry)
        .where(SessionEntry.session_id == session_id)
        .values(seat_position=None, checked_in_at=None, played=False)
    )
    await db.commit()


async def is_checkin_open(db: AsyncSession, session_id: str) -> bool:
    state = await db.get(SessionState, session_id)
    return bool(state and state.checkin_open)


async def set_checkin_open(db: AsyncSession, session_id: str, open_: bool) -> None:
    state = await db.get(SessionState, session_id)
    if state is None:
        db.add(SessionState(session_id=session_id, checkin_open=open_))
    else:
        state.checkin_open = open_
    await db.commit()
