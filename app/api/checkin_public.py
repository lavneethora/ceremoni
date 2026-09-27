"""
Student self check-in. These routes are public on purpose and sit outside the
admin router: a student proves who they are with a Microsoft sign-in, gets a
short lived signed token, and can do exactly one thing with it, take their own
place in the seating. No cookie or admin session is ever created here.
"""

import os

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import MODE_STUDENT, get_login_url
from app.db import async_session, get_session
from app.models import SessionEntry, Student
from app.services.checkin_links import (
    line_signature_ok,
    make_confirm_token,
    read_confirm_token,
    seat_signature_ok,
)
from app.services.seating import (
    SeatingConflict,
    is_checkin_open,
    seat_student,
    seat_student_at,
)
from app.services.session_queue import get_session_mode

router = APIRouter(prefix="/checkin")

templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "..", "templates"))

SEE_AN_USHER = "Please see an usher and they will seat you."


def _page(request: Request, state: str, message: str = "", student=None, entry=None, token: str = "",
          status_code: int = 200):
    response = templates.TemplateResponse(
        request,
        "checkin_student.html",
        {"state": state, "message": message, "student": student, "entry": entry, "token": token},
        status_code=status_code,
    )
    response.headers["Cache-Control"] = "no-store"
    return response


async def _find_roster_student(db: AsyncSession, session_id: str, emails: list[str]) -> Student | None:
    """The one roster student whose email matches any of these addresses, or None."""
    if not emails:
        return None
    result = await db.execute(
        select(Student)
        .join(SessionEntry, SessionEntry.student_id == Student.id)
        .where(SessionEntry.session_id == session_id, func.lower(Student.email).in_(emails))
    )
    matches = result.scalars().all()
    # Two students sharing an address cannot be told apart, so send them to an usher
    return matches[0] if len(matches) == 1 else None


def _require_seating(session_id: str) -> None:
    mode = get_session_mode(session_id)
    if not (mode.seating and mode.roster):
        raise HTTPException(404, "Check-in is not available for this session")


@router.get("/line/{session_id}")
async def start_line_checkin(
    session_id: str,
    request: Request,
    k: str = "",
    db: AsyncSession = Depends(get_session),
):
    """Where the line QR code points. Signed, so it only works for the session it was made for."""
    if not line_signature_ok(session_id, k):
        return _page(request, "message", "This code is not valid. Please see an usher.", status_code=400)
    _require_seating(session_id)
    if not await is_checkin_open(db, session_id):
        return _page(request, "message", "Check-in is not open yet. Please wait for an usher to open it.")

    url, _state = get_login_url(
        str(request.url_for("auth_callback")),
        mode=MODE_STUDENT,
        intent={"session_id": session_id, "method": "line"},
    )
    return RedirectResponse(url)


@router.get("/seat/{session_id}/{seat_position}")
async def start_seat_checkin(
    session_id: str,
    seat_position: int,
    request: Request,
    k: str = "",
    db: AsyncSession = Depends(get_session),
):
    """Where a seat's QR code points. Signed per seat, so one seat's code cannot seat someone else."""
    if seat_position < 1 or not seat_signature_ok(session_id, seat_position, k):
        return _page(request, "message", "This code is not valid. Please see an usher.", status_code=400)
    _require_seating(session_id)
    if not await is_checkin_open(db, session_id):
        return _page(request, "message", "Check-in is not open yet. Please wait for an usher to open it.")

    url, _state = get_login_url(
        str(request.url_for("auth_callback")),
        mode=MODE_STUDENT,
        intent={"session_id": session_id, "method": "seat", "seat_position": seat_position},
    )
    return RedirectResponse(url)


def student_signin_failed(request: Request):
    return _page(request, "message", "Sign-in did not complete. Please scan the code and try again.", status_code=400)


async def complete_student_signin(request: Request, record: dict, identity: dict):
    """Called by the Microsoft callback once a student has signed in."""
    intent = record["intent"]
    session_id = intent.get("session_id", "")
    async with async_session() as db:
        student = await _find_roster_student(db, session_id, identity["emails"])
    if student is None:
        return _page(
            request, "message",
            f"We could not match {identity['name'] or 'this account'} to the honors list. {SEE_AN_USHER}",
        )
    payload = {"sid": session_id, "stu": student.id, "m": intent.get("method", "line")}
    if intent.get("seat_position") is not None:
        payload["seat"] = intent["seat_position"]
    token = make_confirm_token(payload)
    return RedirectResponse(f"/checkin/confirm?c={token}", status_code=303)


async def _load_confirm(db: AsyncSession, token: str):
    data = read_confirm_token(token)
    if not data:
        return None
    result = await db.execute(
        select(Student, SessionEntry)
        .join(SessionEntry, SessionEntry.student_id == Student.id)
        .where(SessionEntry.session_id == data.get("sid"), Student.id == data.get("stu"))
    )
    row = result.first()
    return (data, row[0], row[1]) if row else None


EXPIRED = "This link has expired. Please scan the code again."


def _target_seat(data: dict) -> int | None:
    return data.get("seat")


@router.get("/confirm")
async def confirm_page(request: Request, c: str = "", db: AsyncSession = Depends(get_session)):
    loaded = await _load_confirm(db, c)
    if not loaded:
        return _page(request, "message", EXPIRED, status_code=400)
    data, student, entry = loaded
    target = _target_seat(data)

    if entry.seat_position is not None:
        if target is not None and entry.seat_position != target:
            return _page(
                request, "message",
                f"You are already checked in at seat {entry.seat_position}. {SEE_AN_USHER}",
            )
        return _page(request, "done", student=student, entry=entry)
    if not await is_checkin_open(db, entry.session_id):
        return _page(request, "message", "Check-in is closed right now. " + SEE_AN_USHER)
    if target is not None and await _seat_taken_by_other(db, entry.session_id, target, student.id):
        return _page(request, "message", f"Seat {target} is already taken. {SEE_AN_USHER}", status_code=409)
    return _page(request, "confirm", student=student, entry=entry, token=c)


async def _seat_taken_by_other(db: AsyncSession, session_id: str, seat_position: int, student_id: str) -> bool:
    result = await db.execute(
        select(SessionEntry.student_id).where(
            SessionEntry.session_id == session_id, SessionEntry.seat_position == seat_position
        )
    )
    holder = result.scalar_one_or_none()
    return holder is not None and holder != student_id


@router.post("/confirm")
async def confirm_checkin(request: Request, c: str = Form(""), db: AsyncSession = Depends(get_session)):
    """Seat the student. Repeating it (a refresh or a double tap) changes nothing."""
    loaded = await _load_confirm(db, c)
    if not loaded:
        return _page(request, "message", EXPIRED, status_code=400)
    data, student, entry = loaded
    target = _target_seat(data)

    if entry.seat_position is None:
        if not await is_checkin_open(db, entry.session_id):
            return _page(request, "message", "Check-in is closed right now. " + SEE_AN_USHER)
        try:
            if target is not None:
                entry, _new = await seat_student_at(db, entry.session_id, student.id, target)
            else:
                entry, _new = await seat_student(db, entry.session_id, student.id)
        except SeatingConflict as e:
            return _page(request, "message", f"{e} {SEE_AN_USHER}", status_code=409)
        except LookupError:
            return _page(request, "message", "Something went wrong. " + SEE_AN_USHER, status_code=409)
    elif target is not None and entry.seat_position != target:
        return _page(
            request, "message",
            f"You are already checked in at seat {entry.seat_position}. {SEE_AN_USHER}",
        )
    return _page(request, "done", student=student, entry=entry)
