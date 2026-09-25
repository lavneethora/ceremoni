from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import get_login_url, handle_callback, require_admin
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
from app.services.config_loader import get_session_options
from app.services.roster_import import RosterImportError, import_roster
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
    state = request.session.get("auth_state", "")
    user = await handle_callback(request, state)
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
