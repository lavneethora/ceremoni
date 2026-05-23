from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import Student, Recording
from app.services.storage import storage
from app.services import pipeline

router = APIRouter()

AUDIO_EXTENSIONS = {"mp3", "wav", "m4a", "mp4", "ogg", "webm", "flac", "aac", "wma", "opus"}
MAX_SIZE = 25 * 1024 * 1024


@router.post("/upload")
async def upload(
    audio_file: UploadFile = File(...),
    typed_name: str = Form(...),
    phonetic_hint: str = Form(None),
    session: AsyncSession = Depends(get_session),
):
    ext = audio_file.filename.rsplit(".", 1)[-1].lower() if audio_file.filename and "." in audio_file.filename else None
    if not ext or ext not in AUDIO_EXTENSIONS:
        raise HTTPException(400, f"Unsupported audio format. Accepted: {', '.join(sorted(AUDIO_EXTENSIONS))}")

    data = await audio_file.read()
    if len(data) > MAX_SIZE:
        raise HTTPException(400, "File exceeds 25 MB limit")

    student = Student(typed_name=typed_name, phonetic_hint=phonetic_hint)
    session.add(student)
    await session.flush()

    rec = Recording(student_id=student.id, processing_status="uploaded")
    session.add(rec)
    await session.flush()

    path = await storage.save(student.id, f"{rec.id}_original.{ext}", data)
    rec.original_audio_url = path
    await session.commit()

    return {"student_id": student.id, "recording_id": rec.id, "status": "uploaded"}


@router.post("/process/{recording_id}")
async def process_recording(
    recording_id: str,
    session: AsyncSession = Depends(get_session),
):
    try:
        rec = await pipeline.process(recording_id, session)
    except ValueError as e:
        raise HTTPException(404, str(e))

    return {
        "recording_id": rec.id,
        "ipa_representation": rec.ipa_representation,
        "phoneme_representation": rec.phoneme_representation,
        "status": rec.processing_status,
    }


@router.post("/approve/{recording_id}")
async def approve(
    recording_id: str,
    corrected_phonetics: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    rec = await session.get(Recording, recording_id)
    if not rec:
        raise HTTPException(404, "Recording not found")

    if corrected_phonetics:
        rec.ipa_representation = corrected_phonetics

    try:
        await pipeline.generate_final_audio(recording_id, session, ipa_override=corrected_phonetics)
    except ValueError as e:
        raise HTTPException(404, str(e))

    return {
        "recording_id": rec.id,
        "status": rec.processing_status,
        "generated_audio_url": rec.generated_audio_url,
    }


@router.get("/audio/{recording_id}")
async def get_audio(
    recording_id: str,
    session: AsyncSession = Depends(get_session),
):
    rec = await session.get(Recording, recording_id)
    if not rec:
        raise HTTPException(404, "Recording not found")
    if not rec.generated_audio_url:
        raise HTTPException(404, "No generated audio available")

    return FileResponse(rec.generated_audio_url, media_type="audio/mpeg", filename="ceremony_audio.mp3")
