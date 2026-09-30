import asyncio
import base64
import io
import time

from openai import OpenAI
from pydub import AudioSegment

from app.config import settings

_client = None

# gpt-audio-1.5 returns an empty completion for a minority of calls, on audio it
# reads fine on a later attempt, so one empty answer is not evidence of anything
IPA_ATTEMPTS = 4
IPA_RETRY_WAIT_SECONDS = 2

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
    """The IPA for this recording, or "" if every attempt came back unusable.

    gpt-audio-1.5 returns an empty completion for a sizeable minority of calls
    on audio it handles fine on the next try, so a single empty answer means
    very little. Retry before believing it: the caller treats "" as "we have no
    pronunciation for this person", which must never be a coin flip.
    """
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

    for attempt in range(1, IPA_ATTEMPTS + 1):
        result = _one_ipa_attempt(audio_b64, context)
        if result:
            if attempt > 1:
                print(f"Phonetic converter: attempt {attempt} succeeded for '{typed_name}'")
            print(f"Phonetic converter: IPA for '{typed_name}' = {result}")
            return result
        if attempt < IPA_ATTEMPTS:
            time.sleep(IPA_RETRY_WAIT_SECONDS * attempt)

    print(f"Phonetic converter: no usable IPA for '{typed_name}' after {IPA_ATTEMPTS} attempts")
    return ""


def _one_ipa_attempt(audio_b64: str, context: str) -> str:
    client = _get_client()

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
                    "- Do NOT add any preamble, explanation, or lead-in sentence\n"
                    "- Do NOT write things like \"The IPA transcription is:\"\n"
                    "- Your entire reply must be the IPA transcription and nothing else\n"
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

    raw = (response.choices[0].message.content or "").strip()
    result = _extract_ipa(raw)
    if not result:
        reason = "empty response" if not raw else f"unusable response: {raw[:80]}"
        print(f"Phonetic converter: {reason}")
    return result


def _looks_like_ipa(text: str) -> bool:
    return bool(text) and len(text) <= 80 and not any(ch in text for ch in ".,!?")


def _extract_ipa(raw: str) -> str:
    """The IPA in a model response, even when it ignored the "no preamble" rule.

    Despite the prompt, gpt-audio-1.5 sometimes wraps the answer in a sentence
    like "The IPA transcription is: <ipa>" or puts an explanation on its own
    line before the answer. Try the whole response first; if that looks like
    prose, fall back to the text after the last colon, then the last line.
    """
    candidates = [raw.strip("\"'/[]")]
    if ":" in raw:
        candidates.append(raw.rsplit(":", 1)[-1].strip().strip("\"'/[]"))
    if "\n" in raw:
        candidates.append(raw.rsplit("\n", 1)[-1].strip().strip("\"'/[]"))

    for candidate in candidates:
        if _looks_like_ipa(candidate):
            return candidate
    return ""
