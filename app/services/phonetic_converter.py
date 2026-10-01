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

# Repeating an identical request tends to get the same non-answer back, so each
# attempt varies the temperature and the container the audio is sent in. Both
# were measured recovering recordings that four identical retries could not.
IPA_TEMPERATURES = (0.0, 0.4, 0.8, 1.0)

# Azure en-US supported IPA phonemes
# Source: https://learn.microsoft.com/en-us/azure/ai-services/speech-service/language-support
AZURE_EN_US_IPA = (
    "Vowels: iː ɪ ʊ uː ə ɛ ɜː ɔː æ ʌ ɑː aɪ aʊ eɪ oʊ ɔɪ\n"
    "Consonants: p b t d k ɡ f v θ ð s z ʃ ʒ h m n ŋ l r j w tʃ dʒ\n"
    "Use ː for long vowels. Use spaces between words only."
)

# Every character the symbols above are built from, plus the space between
# words. Note this has no ASCII "g" (Azure wants ɡ, U+0261) and no c/q/x/y,
# which is what makes it a usable test for "this is IPA, not a sentence".
ALLOWED_IPA_CHARS = frozenset(
    "iːɪʊuəɛɜɔæʌɑaeo"      # vowels and the length mark
    "pbtdkɡfvθðszʃʒhmnŋlrjw"  # consonants
    " "
)

# Marks the model sometimes adds that Azure does not want, stripped before
# the character check rather than counted against it
IPA_STRIP_CHARS = "[]/()ˈˌ.'\"`"


def _get_client():
    global _client
    if _client is None:
        _client = OpenAI(api_key=settings.openai_api_key)
    return _client


async def to_ipa(audio_bytes: bytes, typed_name: str, phonetic_hint: str | None = None) -> str:
    return await asyncio.to_thread(_to_ipa_sync, audio_bytes, typed_name, phonetic_hint)


def _to_ipa_sync(audio_bytes: bytes, typed_name: str, phonetic_hint: str | None = None) -> str:
    """The IPA for this recording, or "" if every model and attempt failed.

    Two things make a single answer unreliable. These models return an empty
    completion for a minority of calls on audio they read correctly the next
    time, so one empty answer means very little. And they differ on which
    recordings they can handle at all: measured over the same clips,
    gpt-audio-2025-08-28 transcribed three that gpt-audio-1.5 never managed.

    So every model in settings.ipa_models is tried in order, each with retries.
    The caller treats "" as "we have no pronunciation for this person", which
    stops the ceremony for them, so it has to mean we genuinely exhausted the
    options rather than got unlucky once.
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

    # Same audio, two containers, so retries can vary what is sent
    payloads = [("wav", audio_b64)]
    mp3_b64 = _as_mp3_b64(wav_bytes)
    if mp3_b64:
        payloads.append(("mp3", mp3_b64))

    models = [m.strip() for m in settings.ipa_models.split(",") if m.strip()]
    for model in models:
        for attempt in range(IPA_ATTEMPTS):
            fmt, payload = payloads[attempt % len(payloads)]
            temperature = IPA_TEMPERATURES[attempt % len(IPA_TEMPERATURES)]
            result = _one_ipa_attempt(model, payload, context, fmt, temperature)
            if result:
                print(f"Phonetic converter: IPA for '{typed_name}' = {result}  [{model}]")
                return result
            if attempt < IPA_ATTEMPTS - 1:
                time.sleep(IPA_RETRY_WAIT_SECONDS * (attempt + 1))
        print(f"Phonetic converter: {model} gave nothing usable for '{typed_name}'")

    print(f"Phonetic converter: no usable IPA for '{typed_name}' from any of {models}")
    return ""


def _as_mp3_b64(wav_bytes: bytes) -> str | None:
    try:
        buf = io.BytesIO()
        AudioSegment.from_file(io.BytesIO(wav_bytes)).export(buf, format="mp3")
        return base64.b64encode(buf.getvalue()).decode("utf-8")
    except Exception:
        return None


def _one_ipa_attempt(model: str, audio_b64: str, context: str, fmt: str, temperature: float) -> str:
    client = _get_client()

    response = client.chat.completions.create(
        model=model,
        modalities=["text"],
        max_completion_tokens=300,
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
                            "format": fmt,
                        },
                    },
                ],
            },
        ],
        temperature=temperature,
    )

    raw = (response.choices[0].message.content or "").strip()
    result = _extract_ipa(raw)
    if not result:
        reason = "empty response" if not raw else f"unusable response: {raw[:80]}"
        print(f"Phonetic converter: {reason}")
    return result


def _looks_like_ipa(text: str) -> bool:
    """Whether every character is one Azure can actually speak in a phoneme tag.

    A length-and-punctuation guess is not enough: it accepted
    '{"audio_data": "placeholder_for_audio_data"}' as a pronunciation, which
    would have been stored and read out as a student's name. Checking the
    character set rejects that, rejects English prose (c, g, q, x, y and
    capitals never appear in this symbol set), and rejects IPA symbols outside
    what Azure supports, which would fail at synthesis time anyway.
    """
    if not text or len(text) > 80:
        return False
    return all(ch in ALLOWED_IPA_CHARS for ch in text)


def _extract_ipa(raw: str) -> str:
    """The IPA in a model response, even when it ignored the "no preamble" rule.

    Despite the prompt, gpt-audio-1.5 sometimes wraps the answer in a sentence
    like "The IPA transcription is: <ipa>" or puts an explanation on its own
    line before the answer. Try the whole response first; if that looks like
    prose, fall back to the text after the last colon, then the last line.
    """
    candidates = [raw]
    if ":" in raw:
        candidates.append(raw.rsplit(":", 1)[-1])
    if "\n" in raw:
        candidates.append(raw.rsplit("\n", 1)[-1])

    for candidate in candidates:
        cleaned = "".join(ch for ch in candidate if ch not in IPA_STRIP_CHARS).strip()
        # Models often capitalise the first letter out of name-writing habit
        # ("Deɪv tʊʃɑːr bʌktə"). The Azure en-US set has no uppercase symbol at
        # all, so lowercasing cannot turn one valid symbol into a different one.
        cleaned = cleaned.lower()
        if _looks_like_ipa(cleaned):
            return cleaned
    return ""
