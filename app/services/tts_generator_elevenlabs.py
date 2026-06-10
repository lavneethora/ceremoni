import asyncio
import html

import httpx

from app.config import settings

# ElevenLabs API
ELEVENLABS_BASE = "https://api.elevenlabs.io/v1"

# Default voice — Rachel (built-in free voice)
DEFAULT_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"

# Multilingual v2 is best for unusual names
DEFAULT_MODEL = "eleven_multilingual_v2"


async def generate_tts(name: str, ipa: str | None = None) -> bytes:
    """Generate TTS audio using ElevenLabs.

    If IPA is provided, wraps the name in an SSML phoneme tag for accurate pronunciation.
    Otherwise, sends the plain typed name and lets ElevenLabs pronounce it natively.
    """
    voice_id = getattr(settings, "elevenlabs_voice_id", "") or DEFAULT_VOICE_ID
    model_id = getattr(settings, "elevenlabs_model", "") or DEFAULT_MODEL
    api_key = settings.elevenlabs_api_key

    if not api_key:
        raise RuntimeError("ELEVENLABS_API_KEY is not configured")

    # Build the text input
    if ipa:
        clean_ipa = ipa
        for ch in "[]/()" "ˈˌ.'\"":
            clean_ipa = clean_ipa.replace(ch, "")
        clean_ipa = clean_ipa.strip()
        print(f"  IPA for TTS: {clean_ipa}")
        text = f'<phoneme alphabet="ipa" ph="{html.escape(clean_ipa)}">{html.escape(name)}</phoneme>'
    else:
        text = name

    url = f"{ELEVENLABS_BASE}/text-to-speech/{voice_id}"
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    payload = {
        "text": text,
        "model_id": model_id,
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75,
            "style": 0.0,
            "use_speaker_boost": True,
        },
        "output_format": "mp3_44100_128",
    }

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(url, headers=headers, json=payload)

        if resp.status_code == 200:
            return resp.content

        # If IPA SSML failed, fall back to plain name
        if ipa:
            print(f"  ⚠ ElevenLabs rejected IPA payload ({resp.status_code}): {resp.text[:200]}")
            print(f"  Falling back to plain name...")
            payload["text"] = name
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code == 200:
                return resp.content

        raise RuntimeError(f"ElevenLabs TTS failed: {resp.status_code} — {resp.text[:300]}")
