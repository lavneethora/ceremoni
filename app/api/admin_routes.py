from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import get_login_url, handle_callback, require_admin
from app.db import get_session
from app.models import (
    Student, Recording, GraduationEvent, CeremonySession, SessionCollege,
)

router = APIRouter(prefix="/admin")


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
                {"id": s.id, "label": s.label, "date": s.date, "time": s.time, "session_order": s.session_order}
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

    query = select(Student).options(selectinload(Student.recordings))

    if session_id:
        # Get colleges for this session
        result = await session.execute(
            select(SessionCollege).where(SessionCollege.session_id == session_id).order_by(SessionCollege.college_order)
        )
        session_colleges = result.scalars().all()
        college_names = [sc.college for sc in session_colleges]
        college_order = {name: i for i, name in enumerate(college_names)}

        query = query.where(Student.college.in_(college_names))

    result = await session.execute(query)
    students = result.scalars().all()

    # Sort: college order -> major -> last name
    def sort_key(s):
        c_order = college_order.get(s.college, 999) if session_id else 0
        last_name = s.typed_name.split()[-1] if s.typed_name else ""
        return (s.sort_order or 99999, c_order, s.major or "", last_name)

    students.sort(key=sort_key)

    return [
        {
            "id": s.id,
            "typed_name": s.typed_name,
            "college": s.college,
            "major": s.major,
            "degree_level": s.degree_level,
            "played": s.played,
            "sort_order": s.sort_order,
            "status": s.recordings[0].processing_status if s.recordings else "no_recording",
            "has_audio": bool(s.recordings and s.recordings[0].generated_audio_url),
        }
        for s in students
    ]


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


# --- Ceremony Playback ---

@router.get("/api/ceremony/next")
async def next_student(
    request: Request,
    session_id: str,
    session: AsyncSession = Depends(get_session),
):
    require_admin(request)

    result = await session.execute(
        select(SessionCollege).where(SessionCollege.session_id == session_id).order_by(SessionCollege.college_order)
    )
    college_names = [sc.college for sc in result.scalars().all()]
    college_order = {name: i for i, name in enumerate(college_names)}

    result = await session.execute(
        select(Student)
        .options(selectinload(Student.recordings))
        .where(Student.college.in_(college_names), Student.played == False)
    )
    students = result.scalars().all()

    if not students:
        return {"done": True}

    def sort_key(s):
        c_order = college_order.get(s.college, 999)
        last_name = s.typed_name.split()[-1] if s.typed_name else ""
        return (s.sort_order or 99999, c_order, s.major or "", last_name)

    students.sort(key=sort_key)
    next_student = students[0]

    return {
        "id": next_student.id,
        "typed_name": next_student.typed_name,
        "college": next_student.college,
        "major": next_student.major,
        "has_audio": bool(next_student.recordings and next_student.recordings[0].generated_audio_url),
    }


@router.post("/api/ceremony/play/{student_id}")
async def play_student(
    student_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    require_admin(request)
    student = await session.get(Student, student_id)
    if not student:
        raise HTTPException(404, "Student not found")

    student.played = True
    await session.commit()

    result = await session.execute(
        select(Recording).where(Recording.student_id == student_id, Recording.generated_audio_url.isnot(None))
    )
    recording = result.scalar_one_or_none()

    return {
        "id": student.id,
        "typed_name": student.typed_name,
        "audio_url": f"/audio/{recording.id}" if recording else None,
    }


@router.post("/api/ceremony/reset")
async def reset_ceremony(
    request: Request,
    session_id: str,
    session: AsyncSession = Depends(get_session),
):
    require_admin(request)

    result = await session.execute(
        select(SessionCollege).where(SessionCollege.session_id == session_id)
    )
    college_names = [sc.college for sc in result.scalars().all()]

    await session.execute(
        update(Student).where(Student.college.in_(college_names)).values(played=False)
    )
    await session.commit()
    return {"status": "reset"}


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
