"""
Which students belong to a ceremony session, and in what order.

Legacy sessions (college based, no YAML options) behave exactly as before:
membership is the session's colleges, order is sort_order then college, major
and last name, and played is the global Student.played flag.

Sessions with `roster: true` take their students from session_entries, and
sessions with `order: seating` announce only seated students in seat order.
For both, the played flag lives on the entry, so it is scoped to the session.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import and_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import SessionCollege, SessionEntry, Student
from app.services.config_loader import get_session_options


@dataclass
class SessionMode:
    roster: bool
    seating: bool

    @property
    def uses_entries(self) -> bool:
        return self.roster or self.seating


@dataclass
class QueueRow:
    student: Student
    entry: SessionEntry | None
    played: bool
    seat_position: int | None
    honors_level: str | None
    major: str | None
    in_queue: bool


@dataclass
class SessionRows:
    mode: SessionMode
    rows: list[QueueRow]

    def queue(self) -> list[QueueRow]:
        return [r for r in self.rows if r.in_queue]


def get_session_mode(session_id: str | None) -> SessionMode:
    if not session_id:
        return SessionMode(roster=False, seating=False)
    options = get_session_options(session_id)
    return SessionMode(roster=options["roster"], seating=options["order"] == "seating")


async def _college_order(db: AsyncSession, session_id: str) -> dict[str, int]:
    result = await db.execute(
        select(SessionCollege)
        .where(SessionCollege.session_id == session_id)
        .order_by(SessionCollege.college_order)
    )
    return {sc.college: i for i, sc in enumerate(result.scalars().all())}


def _default_key(row: QueueRow, college_order: dict[str, int] | None) -> tuple:
    student = row.student
    last_name = student.typed_name.split()[-1] if student.typed_name else ""
    c_order = college_order.get(student.college, 999) if college_order is not None else 0
    return (student.sort_order or 99999, c_order, row.major or "", last_name)


def _seating_key(row: QueueRow, college_order: dict[str, int] | None) -> tuple:
    if row.seat_position is not None:
        checked_in = row.entry.checked_in_at if row.entry and row.entry.checked_in_at else datetime.min
        return (0, row.seat_position, checked_in, row.student.id)
    return (1,) + _default_key(row, college_order)


async def load_session_rows(db: AsyncSession, session_id: str | None) -> SessionRows:
    """All students in the session in display order. With no session_id, every student."""
    mode = get_session_mode(session_id)
    college_order = await _college_order(db, session_id) if session_id else None

    if mode.uses_entries:
        on_entry = and_(SessionEntry.student_id == Student.id, SessionEntry.session_id == session_id)
        query = select(Student, SessionEntry).options(selectinload(Student.recordings))
        if mode.roster:
            query = query.join(SessionEntry, on_entry)
        else:
            query = query.outerjoin(SessionEntry, on_entry).where(Student.college.in_(list(college_order)))
        pairs = (await db.execute(query)).all()
    else:
        query = select(Student).options(selectinload(Student.recordings))
        if college_order is not None:
            query = query.where(Student.college.in_(list(college_order)))
        pairs = [(s, None) for s in (await db.execute(query)).scalars().all()]

    rows = []
    for student, entry in pairs:
        if mode.uses_entries:
            played = bool(entry and entry.played)
            seat_position = entry.seat_position if entry else None
            in_queue = not played and (not mode.seating or seat_position is not None)
        else:
            played = student.played
            seat_position = None
            in_queue = not played
        rows.append(
            QueueRow(
                student=student,
                entry=entry,
                played=played,
                seat_position=seat_position,
                honors_level=entry.honors_level if entry else None,
                major=(entry.major if entry and entry.major else student.major),
                in_queue=in_queue,
            )
        )

    key = _seating_key if mode.seating else _default_key
    rows.sort(key=lambda r: key(r, college_order))
    return SessionRows(mode=mode, rows=rows)


async def set_played(db: AsyncSession, session_id: str | None, student_id: str, played: bool) -> Student:
    """Set one student's played state in the right place. Raises LookupError if not allowed."""
    student = await db.get(Student, student_id)
    if not student:
        raise LookupError("Student not found")

    mode = get_session_mode(session_id)
    if not mode.uses_entries:
        student.played = played
        await db.commit()
        return student

    result = await db.execute(
        select(SessionEntry).where(
            SessionEntry.session_id == session_id, SessionEntry.student_id == student_id
        )
    )
    entry = result.scalar_one_or_none()
    if entry is None:
        if mode.roster:
            raise LookupError("Student is not on this session's roster")
        entry = SessionEntry(session_id=session_id, student_id=student_id)
        db.add(entry)
    entry.played = played
    await db.commit()
    return student


async def reset_played(db: AsyncSession, session_id: str) -> None:
    mode = get_session_mode(session_id)
    if mode.uses_entries:
        await db.execute(
            update(SessionEntry).where(SessionEntry.session_id == session_id).values(played=False)
        )
    else:
        college_order = await _college_order(db, session_id)
        await db.execute(
            update(Student).where(Student.college.in_(list(college_order))).values(played=False)
        )
    await db.commit()
