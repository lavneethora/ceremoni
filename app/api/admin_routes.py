from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.checkin_public import complete_student_signin, student_signin_failed
from app.auth import (
    MODE_STUDENT,
    get_login_url,
    handle_callback,
    handle_student_callback,
    require_admin,
    take_flow,
)
from app.db import get_session
from app.models import (
    Student, Recording, GraduationEvent,
)
from app.services.announcements import (
    AnnouncementNotReady,
    announcement_state,
    announcement_status,
    get_ready_entry,
    start_generation,
)
from app.services.checkin_links import (
    line_signature,
    make_card_code,
    parse_card_code,
    seat_signature,
)
from app.services.config_loader import get_session_options
from app.services.roster_import import RosterImportError, clear_roster, import_roster
from app.services.storage_cleanup import delete_orphans
from app.services.seating import (
    SeatingConflict,
    clear_seating,
    is_checkin_open,
    reorder_seating,
    seat_student,
    set_checkin_open,
    unseat_student,
)
from app.services.session_queue import get_session_mode, load_session_rows, reset_played, set_played

router = APIRouter(prefix="/admin")

MAX_ROSTER_BYTES = 2 * 1024 * 1024


def _announcement(row, roster: bool) -> str | None:
    return announcement_state(row.entry, row.student) if roster and row.entry else None


def _student_payload(row, roster: bool = False) -> dict:
    student = row.student
    recordings = student.recordings
    return {
        "id": student.id,
        "typed_name": student.typed_name,
        "college": student.college,
        "major": row.major,
        "degree_level": student.degree_level,
        "played": row.played,
        "sort_order": student.sort_order,
        "seat_position": row.seat_position,
        "honors_level": row.honors_level,
        "status": recordings[0].processing_status if recordings else "no_recording",
        "has_audio": bool(recordings and recordings[0].generated_audio_url),
        "has_recording": bool(recordings),
        "announcement": _announcement(row, roster),
    }


def _queue_payload(row, roster: bool = False) -> dict:
    student = row.student
    return {
        "id": student.id,
        "typed_name": student.typed_name,
        "college": student.college,
        "major": row.major,
        "seat_position": row.seat_position,
        "honors_level": row.honors_level,
        "has_audio": bool(student.recordings and student.recordings[0].generated_audio_url),
        "announcement": _announcement(row, roster),
    }


# --- Auth routes ---

@router.get("/login")
async def login(request: Request):
    redirect_uri = str(request.url_for("auth_callback"))
    url, state = get_login_url(redirect_uri)
    request.session["auth_state"] = state
    return RedirectResponse(url)


@router.get("/auth/callback", name="auth_callback")
async def auth_callback(request: Request):
    """Microsoft sends both admins and students back here, so it is registered once in Azure."""
    state = request.query_params.get("state", "")
    record = take_flow(state)
    if not record:
        raise HTTPException(400, "Invalid or expired auth session. Please try logging in again.")

    if record["mode"] == MODE_STUDENT:
        # Students never get a cookie or an admin session, only a page with their own check-in
        try:
            identity = handle_student_callback(request, record)
        except HTTPException:
            return student_signin_failed(request)
        return await complete_student_signin(request, record, identity)

    # An admin login must finish in the browser that started it
    if request.session.get("auth_state") != state:
        raise HTTPException(400, "Invalid or expired auth session. Please try logging in again.")
    user = await handle_callback(request, record)
    request.session["user"] = user
    request.session.pop("auth_state", None)
    return RedirectResponse("/admin/dashboard")


@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/admin")


# --- Graduation Events (read-only, configured via ceremony.yaml) ---

@router.get("/api/events")
async def list_events(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    require_admin(request)
    result = await session.execute(
        select(GraduationEvent).options(selectinload(GraduationEvent.sessions))
    )
    events = result.scalars().all()
    return [
        {
            "id": e.id,
            "name": e.name,
            "active": e.active,
            "sessions": [
                {
                    "id": s.id,
                    "label": s.label,
                    "date": s.date,
                    "time": s.time,
                    "session_order": s.session_order,
                    **get_session_options(s.id),
                }
                for s in e.sessions
            ],
        }
        for e in events
    ]


@router.post("/api/reload-config")
async def reload_config(request: Request):
    """Reload ceremony.yaml into the database."""
    require_admin(request)
    from app.services.config_loader import load_ceremony_config
    result = await load_ceremony_config()
    return result


# --- Students ---

@router.get("/api/students")
async def list_students(
    request: Request,
    session_id: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    require_admin(request)

    loaded = await load_session_rows(session, session_id)
    return [_student_payload(row, loaded.mode.roster) for row in loaded.rows]


@router.patch("/api/students/reorder")
async def reorder_students(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    require_admin(request)
    body = await request.json()
    for item in body["order"]:
        student = await session.get(Student, item["id"])
        if student:
            student.sort_order = item["sort_order"]
    await session.commit()
    return {"status": "ok"}


@router.post("/api/students/{student_id}/reprocess")
async def reprocess_student(student_id: str, request: Request):
    """Re-download this student's audio from OneDrive and re-run the full pipeline."""
    require_admin(request)
    access_token = request.session.get("user", {}).get("access_token")
    if not access_token:
        raise HTTPException(401, "No access token, please log out and log back in")

    from app.services.forms_sync import reprocess_single_student
    try:
        return await reprocess_single_student(access_token, student_id)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"status": "error", "message": str(e)}


# --- Ceremony Playback ---

@router.get("/api/ceremony/next")
async def next_student(
    request: Request,
    session_id: str,
    session: AsyncSession = Depends(get_session),
):
    require_admin(request)

    loaded = await load_session_rows(session, session_id)
    queue = loaded.queue()
    if not queue:
        return {"done": True}

    return _queue_payload(queue[0], loaded.mode.roster)


@router.get("/api/ceremony/upcoming")
async def upcoming_students(
    request: Request,
    session_id: str,
    limit: int = 4,
    session: AsyncSession = Depends(get_session),
):
    """Return the next N unplayed students for a session (for Reader Mode preview)."""
    require_admin(request)

    if limit < 1:
        limit = 1
    if limit > 50:
        limit = 50

    loaded = await load_session_rows(session, session_id)
    queue = loaded.queue()

    return {
        "queue": [_queue_payload(row, loaded.mode.roster) for row in queue[:limit]],
        "total_remaining": len(queue),
    }


@router.post("/api/ceremony/play/{student_id}")
async def play_student(
    student_id: str,
    request: Request,
    session_id: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    require_admin(request)

    # A roster session only ever plays a current announcement clip, checked before anything is marked played
    entry = None
    if get_session_mode(session_id).roster:
        try:
            entry = await get_ready_entry(session, session_id, student_id)
        except LookupError as e:
            raise HTTPException(404, str(e))
        except AnnouncementNotReady as e:
            raise HTTPException(409, str(e))

    try:
        student = await set_played(session, session_id, student_id, True)
    except LookupError as e:
        raise HTTPException(404, str(e))

    if entry is not None:
        return {
            "id": student.id,
            "typed_name": student.typed_name,
            "audio_url": f"/audio/announcement/{entry.id}",
        }

    result = await session.execute(
        select(Recording).where(Recording.student_id == student_id, Recording.generated_audio_url.isnot(None))
    )
    recording = result.scalar_one_or_none()

    return {
        "id": student.id,
        "typed_name": student.typed_name,
        "audio_url": f"/audio/{recording.id}" if recording else None,
    }


@router.post("/api/ceremony/unplay/{student_id}")
async def unplay_student(
    student_id: str,
    request: Request,
    session_id: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    require_admin(request)
    try:
        student = await set_played(session, session_id, student_id, False)
    except LookupError as e:
        raise HTTPException(404, str(e))

    return {"id": student.id, "typed_name": student.typed_name, "played": False}


@router.post("/api/ceremony/reset")
async def reset_ceremony(
    request: Request,
    session_id: str,
    session: AsyncSession = Depends(get_session),
):
    require_admin(request)
    await reset_played(session, session_id)
    return {"status": "reset"}


# --- Roster sessions: import and announcement clips ---

def _require_roster_session(session_id: str) -> None:
    if not get_session_mode(session_id).roster:
        raise HTTPException(400, "This session does not use a roster")


@router.post("/api/sessions/{session_id}/roster/import")
async def import_session_roster(
    session_id: str,
    request: Request,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
):
    require_admin(request)
    _require_roster_session(session_id)

    data = await file.read()
    if len(data) > MAX_ROSTER_BYTES:
        raise HTTPException(400, "The file is larger than 2 MB")
    try:
        return await import_roster(session, session_id, data)
    except RosterImportError as e:
        raise HTTPException(400, str(e))


@router.delete("/api/sessions/{session_id}/roster")
async def clear_session_roster(
    session_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Empty the roster, keeping the students, their recordings and their IPA."""
    require_admin(request)
    _require_roster_session(session_id)
    return await clear_roster(session, session_id)


@router.get("/api/sessions/{session_id}/cards")
async def session_cards(
    session_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Every student on the roster, with a card code for those we can announce.

    A student with no usable announcement still appears, without a code, so the
    printed sheet covers the whole roster and the announcer reads those names
    from the card instead of scanning them.
    """
    require_admin(request)
    _require_roster_session(session_id)

    loaded = await load_session_rows(session, session_id)
    cards = []
    for row in loaded.rows:
        ready = _announcement(row, True) == "ready"
        cards.append({
            "student_id": row.student.id,
            "typed_name": row.student.typed_name,
            "major": row.major,
            "honors_level": row.honors_level,
            "ready": ready,
            "code": make_card_code(session_id, row.student.id) if ready else None,
        })
    return {"session_id": session_id, "cards": cards}


@router.post("/api/ceremony/scan")
async def scan_card(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Announce whoever the scanned card names.

    The session comes from the code rather than the caller, so this cannot fall
    through to the legacy per student audio the way /play does when it is given
    no session_id.
    """
    require_admin(request)
    body = await request.json()
    parsed = parse_card_code(body.get("code") if isinstance(body, dict) else None)
    if parsed is None:
        raise HTTPException(400, "That is not a valid Ceremoni card")
    session_id, student_id = parsed

    if not get_session_mode(session_id).roster:
        raise HTTPException(400, "This card is not for a session that uses a roster")

    try:
        entry = await get_ready_entry(session, session_id, student_id)
    except LookupError:
        raise HTTPException(404, "This student is not on the roster for that ceremony")
    except AnnouncementNotReady as e:
        raise HTTPException(409, str(e))

    already_played = entry.played
    student = entry.student  # get_ready_entry eager loads this; seat_student does not

    # seat_position doubles as the order actually announced. There are no
    # migrations, so no column can be added for it, and seat_student already
    # handles finding the next free number and losing a race for it.
    position = entry.seat_position
    if position is None:
        try:
            seated, _new = await seat_student(session, session_id, student_id)
            position = seated.seat_position
        except (SeatingConflict, LookupError) as e:
            raise HTTPException(409, str(e))

    if not already_played:
        await set_played(session, session_id, student_id, True)

    return {
        "id": student.id,
        "typed_name": student.typed_name,
        "major": entry.major or student.major,
        "honors_level": entry.honors_level,
        "position": position,
        "already_played": already_played,
        "audio_url": f"/audio/announcement/{entry.id}",
    }


@router.post("/api/sessions/{session_id}/announcements/generate")
async def generate_announcements(session_id: str, request: Request):
    require_admin(request)
    _require_roster_session(session_id)
    started = start_generation(session_id)
    return {"status": "started" if started else "already_running"}


@router.get("/api/sessions/{session_id}/announcements/status")
async def session_announcement_status(
    session_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    require_admin(request)
    _require_roster_session(session_id)
    return await announcement_status(session, session_id)


# --- Seating: usher check-in ---

def _require_seating_session(session_id: str) -> None:
    if not get_session_mode(session_id).seating:
        raise HTTPException(400, "This session does not use seating order")


@router.get("/api/sessions/{session_id}/seating")
async def seating_state(
    session_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Everyone in the session with their seat (or none), plus whether student check-in is open."""
    require_admin(request)
    _require_seating_session(session_id)

    loaded = await load_session_rows(session, session_id)
    return {
        "checkin_open": await is_checkin_open(session, session_id),
        "students": [_student_payload(row, loaded.mode.roster) for row in loaded.rows],
    }


@router.get("/api/sessions/{session_id}/checkin/links")
async def checkin_links(session_id: str, request: Request):
    """The addresses to put in QR codes for this session."""
    require_admin(request)
    _require_seating_session(session_id)
    line_url = str(request.url_for("start_line_checkin", session_id=session_id))
    return {"line_url": f"{line_url}?k={line_signature(session_id)}"}


MAX_SEAT_SHEET = 500


@router.get("/api/sessions/{session_id}/checkin/seat-links")
async def checkin_seat_links(session_id: str, request: Request, seat_from: int, seat_to: int):
    """The signed addresses to print as one QR per seat, for seats seat_from..seat_to."""
    require_admin(request)
    _require_seating_session(session_id)
    if seat_from < 1 or seat_to < seat_from:
        raise HTTPException(400, "Enter a valid seat range")
    if seat_to - seat_from + 1 > MAX_SEAT_SHEET:
        raise HTTPException(400, f"A sheet can hold at most {MAX_SEAT_SHEET} seats at a time")

    base = str(request.url_for("start_seat_checkin", session_id=session_id, seat_position=0))[:-1]
    return {
        "seats": [
            {"seat": n, "url": f"{base}{n}?k={seat_signature(session_id, n)}"}
            for n in range(seat_from, seat_to + 1)
        ]
    }


@router.post("/api/sessions/{session_id}/seating/{student_id}")
async def seat_one_student(
    session_id: str,
    student_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Seat a student at the end of the line. Seating someone already seated changes nothing."""
    require_admin(request)
    _require_seating_session(session_id)
    try:
        entry, newly_seated = await seat_student(session, session_id, student_id)
    except LookupError as e:
        raise HTTPException(404, str(e))
    except SeatingConflict as e:
        raise HTTPException(409, str(e))
    return {"seat_position": entry.seat_position, "newly_seated": newly_seated}


@router.delete("/api/sessions/{session_id}/seating/{student_id}")
async def unseat_one_student(
    session_id: str,
    student_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    require_admin(request)
    _require_seating_session(session_id)
    try:
        await unseat_student(session, session_id, student_id)
    except SeatingConflict as e:
        raise HTTPException(409, str(e))
    return {"status": "ok"}


@router.put("/api/sessions/{session_id}/seating/order")
async def reorder_session_seating(
    session_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Body: {"order": [student ids]}, exactly the seated students in their new order."""
    require_admin(request)
    _require_seating_session(session_id)
    body = await request.json()
    order = body.get("order") if isinstance(body, dict) else None
    if not isinstance(order, list) or not all(isinstance(i, str) for i in order):
        raise HTTPException(400, "Expected a list of student ids in 'order'")
    try:
        await reorder_seating(session, session_id, order)
    except SeatingConflict as e:
        raise HTTPException(409, str(e))
    return {"status": "ok"}


@router.delete("/api/sessions/{session_id}/seating")
async def clear_session_seating(
    session_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Remove every seat and reset played state for the session."""
    require_admin(request)
    _require_seating_session(session_id)
    await clear_seating(session, session_id)
    return {"status": "ok"}


@router.put("/api/sessions/{session_id}/checkin")
async def set_session_checkin(
    session_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Body: {"open": true or false}. Controls whether students can check themselves in."""
    require_admin(request)
    _require_seating_session(session_id)
    body = await request.json()
    open_ = body.get("open") if isinstance(body, dict) else None
    if not isinstance(open_, bool):
        raise HTTPException(400, "Expected true or false in 'open'")
    await set_checkin_open(session, session_id, open_)
    return {"checkin_open": open_}


# --- Storage housekeeping ---

@router.post("/api/storage/cleanup")
async def cleanup_storage(
    request: Request,
    apply: bool = False,
    session: AsyncSession = Depends(get_session),
):
    """Report audio no database row points at. Pass apply=true to delete it.

    Deleting a student does not remove their recordings from storage, and
    nothing else reconciles the two, so orphans build up over time.
    """
    require_admin(request)
    return await delete_orphans(session, dry_run=not apply)


# --- Debug: raw Excel data ---

@router.get("/api/debug/excel")
async def debug_excel(request: Request):
    """Show raw Excel data so we can debug voice recording URLs."""
    require_admin(request)
    access_token = request.session.get("user", {}).get("access_token")
    if not access_token:
        raise HTTPException(401, "No access token")

    import httpx
    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient(timeout=60) as client:
        workbook_name = "Ceremoni — Graduation Name Pronunciation.xlsx"
        resp = await client.get(
            f"https://graph.microsoft.com/v1.0/me/drive/root:/{workbook_name}",
            headers=headers,
        )
        if resp.status_code != 200:
            return {"error": f"Workbook not found: {resp.status_code}"}

        workbook_id = resp.json()["id"]
        range_url = (
            f"https://graph.microsoft.com/v1.0/me/drive/items/{workbook_id}"
            f"/workbook/worksheets('Sheet1')/usedRange"
        )
        resp = await client.get(range_url, headers=headers)
        if resp.status_code != 200:
            return {"error": f"Could not read: {resp.status_code}"}

        data = resp.json()
        rows = data.get("values", [])
        return {"headers": rows[0] if rows else [], "rows": rows[1:] if len(rows) > 1 else []}


# --- Forms Sync ---

@router.post("/api/sync")
async def sync_forms(request: Request):
    require_admin(request)
    access_token = request.session.get("user", {}).get("access_token")
    if not access_token:
        raise HTTPException(401, "No access token — please log out and log back in")

    from app.services.forms_sync import sync
    try:
        result = await sync(access_token)
        return result
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"status": "error", "message": str(e)}
