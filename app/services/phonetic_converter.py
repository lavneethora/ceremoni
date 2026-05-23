import asyncio
import base64
import io

from openai import OpenAI
from pydub import AudioSegment

from app.config import settings

_client = None

# Azure en-US supported IPA phonemes
# Source: https://learn.microsoft.com/en-us/azure/ai-services/speech-service/language-support
AZURE_EN_US_IPA = (
    "Vowels: iː ɪ ʊ uː ə ɛ ɜː ɔː æ ʌ ɑː aɪ aʊ eɪ oʊ ɔɪ\n"
    "Consonants: p b t d k ɡ f v θ ð s z ʃ ʒ h m n ŋ l r j w tʃ dʒ\n"
    "Use ː for long vowels. Use spaces between words only."
)


def _get_client():
    global _client
    if _client is None:
        _client = OpenAI(api_key=settings.openai_api_key)
    return _client


async def to_ipa(audio_bytes: bytes, typed_name: str, phonetic_hint: str | None = None) -> str:
    return await asyncio.to_thread(_to_ipa_sync, audio_bytes, typed_name, phonetic_hint)


def _to_ipa_sync(audio_bytes: bytes, typed_name: str, phonetic_hint: str | None = None) -> str:
    client = _get_client()

    # Ensure audio is wav format for the API (only wav and mp3 supported)
    # The input might already be wav from the cleaning step, but ensure it
    try:
        audio = AudioSegment.from_file(io.BytesIO(audio_bytes))
        buf = io.BytesIO()
        audio.export(buf, format="wav")
        wav_bytes = buf.getvalue()
    except Exception:
        wav_bytes = audio_bytes

    audio_b64 = base64.b64encode(wav_bytes).decode("utf-8")

    context = f"The student's name is spelled: {typed_name}"
    if phonetic_hint:
        context += f"\nThe student provided this phonetic hint: {phonetic_hint}"

    response = client.chat.completions.create(
        model="gpt-audio-1.5",
        modalities=["text"],
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a phonetics expert. Listen to this audio recording of a person saying their name. "
                    "Produce the IPA transcription of exactly how they pronounced it.\n\n"
                    "CRITICAL: You must ONLY use these IPA symbols (Azure TTS compatible):\n"
                    f"{AZURE_EN_US_IPA}\n\n"
                    "Rules:\n"
                    "- Return ONLY the IPA symbols, nothing else\n"
                    "- No brackets, no slashes, no stress marks\n"
                    "- Use spaces between words\n"
                    "- Do NOT use any IPA symbols not listed above\n"
                    "- Example output: lʌvniːt hɔːrə"
                ),
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": context},
                    {
                        "type": "input_audio",
                        "input_audio": {
                            "data": audio_b64,
                            "format": "wav",
                        },
                    },
                ],
            },
        ],
        temperature=0,
    )

    result = response.choices[0].message.content.strip()

    # Clean up — remove any quotes, brackets, slashes
    result = result.strip("\"'/[]")

    # Safety check — if GPT returned a sentence instead of IPA
    if len(result) > 80 or any(ch in result for ch in ".,!?"):
        print(f"Phonetic converter: rejected response (looks like prose): {result[:80]}")
        return ""

    print(f"Phonetic converter: IPA for '{typed_name}' = {result}")
    return result
