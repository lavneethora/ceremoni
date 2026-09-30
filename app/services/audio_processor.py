import io
import asyncio

import numpy as np
import noisereduce as nr
from pydub import AudioSegment


async def clean_audio(raw_bytes: bytes, file_format: str = "wav") -> bytes:
    return await asyncio.to_thread(_clean_audio_sync, raw_bytes, file_format)


def _clean_audio_sync(raw_bytes: bytes, file_format: str) -> bytes:
    # Let pydub/ffmpeg auto-detect if format fails
    try:
        audio = AudioSegment.from_file(io.BytesIO(raw_bytes), format=file_format)
    except Exception:
        # Fallback: let ffmpeg figure it out
        audio = AudioSegment.from_file(io.BytesIO(raw_bytes))

    audio = audio.set_channels(1).set_frame_rate(16000).set_sample_width(2)

    # No silence trim here: a relative dBFS threshold cuts into real speech on
    # quieter recordings, not just dead air (confirmed on real recordings: one
    # cut 57% of the clip, another 60%, both from the edges of actual words).
    # gpt-audio-1.5 transcribes leading/trailing silence fine on its own.
    audio = audio.apply_gain(-audio.max_dBFS)

    samples = np.array(audio.get_array_of_samples(), dtype=np.float32)
    reduced = nr.reduce_noise(y=samples, sr=16000, prop_decrease=0.6)
    reduced_int = np.int16(reduced)

    cleaned = audio._spawn(reduced_int.tobytes())

    buf = io.BytesIO()
    cleaned.export(buf, format="wav")
    return buf.getvalue()
