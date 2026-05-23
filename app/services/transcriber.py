import asyncio
import tempfile
import os

from app.config import settings

_model = None


def _get_model():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        _model = WhisperModel(settings.whisper_model, device="cpu", compute_type="int8")
    return _model


async def transcribe(audio_bytes: bytes) -> str:
    return await asyncio.to_thread(_transcribe_sync, audio_bytes)


def _transcribe_sync(audio_bytes: bytes) -> str:
    model = _get_model()

    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    try:
        tmp.write(audio_bytes)
        tmp.close()
        segments, _ = model.transcribe(tmp.name, beam_size=5)
        return " ".join(seg.text.strip() for seg in segments).strip()
    finally:
        os.unlink(tmp.name)
