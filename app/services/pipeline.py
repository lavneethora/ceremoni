from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy import select

from app.models import Recording, Student
from app.services import audio_processor, phonetic_converter, tts_generator
from app.services.storage import storage


async def process(recording_id: str, session: AsyncSession) -> Recording:
    result = await session.execute(
        select(Recording).options(selectinload(Recording.student)).where(Recording.id == recording_id)
    )
    recording = result.scalar_one_or_none()
    if not recording:
        raise ValueError(f"Recording {recording_id} not found")

    recording.processing_status = "processing"
    await session.commit()

    student = recording.student

    # Find the original audio file (could be .wav, .m4a, .mp3, etc.)
    original_bytes = None
    found_ext = "wav"
    for ext in [".wav", ".m4a", ".mp3", ".mp4", ".ogg", ".webm", ".flac", ".aac", ".wma", ".opus"]:
        try:
            original_bytes = await storage.load(student.id, f"{recording.id}_original{ext}")
            found_ext = ext.lstrip(".")
            break
        except (FileNotFoundError, Exception):
            continue

    if not original_bytes:
        raise ValueError(f"Original audio file not found for recording {recording.id}")

    # Map file extensions to pydub format names
    format_map = {"m4a": "m4a", "mp3": "mp3", "wav": "wav", "ogg": "ogg", "webm": "webm", "mp4": "mp4"}
    file_format = format_map.get(found_ext, found_ext)

    cleaned_bytes = await audio_processor.clean_audio(original_bytes, file_format=file_format)
    cleaned_path = await storage.save(student.id, f"{recording.id}_cleaned.wav", cleaned_bytes)
    recording.cleaned_audio_url = cleaned_path

    ipa = await phonetic_converter.to_ipa(cleaned_bytes, student.typed_name, student.phonetic_hint)
    recording.ipa_representation = ipa

    recording.processing_status = "awaiting_approval"
    await session.commit()
    return recording


async def generate_final_audio(recording_id: str, session: AsyncSession, ipa_override: str | None = None) -> bytes:
    result = await session.execute(
        select(Recording).options(selectinload(Recording.student)).where(Recording.id == recording_id)
    )
    recording = result.scalar_one_or_none()
    if not recording:
        raise ValueError(f"Recording {recording_id} not found")

    student = recording.student
    ipa = ipa_override or recording.ipa_representation

    audio_bytes = await tts_generator.generate_tts(student.typed_name, ipa=ipa)

    final_path = await storage.save(student.id, f"{recording.id}_final.mp3", audio_bytes)
    recording.generated_audio_url = final_path
    recording.processing_status = "complete"
    recording.pronunciation_approved = True
    await session.commit()

    return audio_bytes
