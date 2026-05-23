import io
import asyncio

import numpy as np
import noisereduce as nr
from pydub import AudioSegment
from pydub.silence import detect_leading_silence


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

    silence_thresh = audio.dBFS - 16
    start_trim = detect_leading_silence(audio, silence_threshold=silence_thresh)
    end_trim = detect_leading_silence(audio.reverse(), silence_threshold=silence_thresh)
    audio = audio[start_trim:len(audio) - end_trim]

    audio = audio.apply_gain(-audio.max_dBFS)

    samples = np.array(audio.get_array_of_samples(), dtype=np.float32)
    reduced = nr.reduce_noise(y=samples, sr=16000, prop_decrease=0.6)
    reduced_int = np.int16(reduced)

    cleaned = audio._spawn(reduced_int.tobytes())

    buf = io.BytesIO()
    cleaned.export(buf, format="wav")
    return buf.getvalue()
