import asyncio
import base64
import io
import time
from collections import Counter

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

# Readings to take per recording; the most common one wins
IPA_SAMPLES = 3

# Used only to re-space an answer whose sounds are right but whose spacing is
# not. Pinned, like the audio models, so it cannot change underneath a ceremony.
IPA_REGROUP_MODEL = "gpt-5.5-2026-04-23"

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
    "ˈˌ"                      # primary and secondary stress
    " "
)

# Delimiters the model sometimes wraps the answer in, stripped before the
# character check rather than counted against it. Stress marks are not in here:
# Azure wants them, and dropping them is what made every name come out flat.
IPA_STRIP_CHARS = "[]/().'\"`"


def _get_client():
    global _client
    if _client is None:
        _client = OpenAI(api_key=settings.openai_api_key)
    return _client


async def to_ipa(audio_bytes: bytes, typed_name: str, phonetic_hint: str | None = None) -> str:
    return await asyncio.to_thread(_to_ipa_sync, audio_bytes, typed_name, phonetic_hint)


def _to_ipa_sync(audio_bytes: bytes, typed_name: str, phonetic_hint: str | None = None) -> str:
    """The IPA for this recording, or "" if every model and attempt failed.

    Read the recording several times and keep the reading that comes back most
    often. A single reading is occasionally wrong in a way nothing downstream
    can catch: the same audio produced "ʒʊl bədʒɑːdʒ" (zhul) and "ʃɪdʒʊl"
    (shi-jul) for Rijul Bajaj on separate runs, both well-formed IPA, both
    wrong, against four runs that got it right. Validation cannot tell a
    plausible wrong answer from a right one, but a vote can.
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

    samples = [r for r in (_one_reading(payloads, context) for _ in range(IPA_SAMPLES)) if r]
    if not samples:
        print(f"Phonetic converter: no usable IPA for '{typed_name}'")
        return ""

    # Vote on the pronunciation, not on how it was spaced. The same reading
    # comes back as both "deɪv tʊʃɑːr bʌktə" and "d eɪ v t ʊ ʃ ɑː r b ʌ k t ə",
    # and counting those separately splits the vote three ways and defeats it.
    by_sound: dict[str, list[str]] = {}
    for sample in samples:
        by_sound.setdefault(sample.replace(" ", ""), []).append(sample)

    winner = max(by_sound.values(), key=len)
    # Within the winning pronunciation, prefer the spelling that groups into one
    # run per word of the name. Azure treats a space inside ph as a word break,
    # so "r ɪ dʒ ʊ l b ə dʒ ɑː dʒ" is ten words to it and comes out chopped up,
    # while "ˈrɪdʒʊl bəˈdʒɑːdʒ" is the two it should be.
    wanted_groups = len(typed_name.split())
    grouped = [r for r in winner if len(r.split()) == wanted_groups]
    best = Counter(grouped or winner).most_common(1)[0][0]

    if len(by_sound) > 1:
        print(
            f"Phonetic converter: readings disagreed for '{typed_name}' "
            f"({len(winner)} of {len(samples)}), taking the majority"
        )

    best = _regroup(typed_name, best)
    print(f"Phonetic converter: IPA for '{typed_name}' = {best}")
    return best


def _regroup(typed_name: str, ipa: str) -> str:
    """Re-space IPA that has the right sounds grouped wrongly.

    Some names come back split at syllables, "ˈkɑːr sən ˈleɪn ˈkæ fiː" rather
    than "ˈkɑːrsən ˈleɪn ˈkæfiː", and the model does it consistently for those
    names however many times it is asked. Azure reads every space as a word
    break, so the sounds are right but the delivery is chopped up.

    Only a pure re-spacing is accepted: same symbols in the same order, right
    number of runs. Anything else and the original stands, so this can improve
    the spacing or do nothing, never change a pronunciation.
    """
    wanted = len(typed_name.split())
    if not ipa or len(ipa.split()) == wanted:
        return ipa

    try:
        response = _get_client().chat.completions.create(
            model=IPA_REGROUP_MODEL,
            max_completion_tokens=2000,
            messages=[
                {"role": "system", "content": (
                    "You re-space IPA so it has exactly one run per word of the name. "
                    "Do not change, add or remove any symbol, including stress marks. "
                    "Only move spaces. Reply with the IPA and nothing else."
                )},
                {"role": "user", "content": (
                    f"Name: {typed_name}\nIPA: {ipa}\n"
                    f"It must end up as exactly {wanted} space-separated runs."
                )},
            ],
        )
        candidate = (response.choices[0].message.content or "").strip()
    except Exception as e:
        print(f"Phonetic converter: could not re-space '{typed_name}': {e}")
        return ipa

    if (
        candidate.replace(" ", "") == ipa.replace(" ", "")
        and len(candidate.split()) == wanted
        and _looks_like_ipa(candidate)
    ):
        print(f"Phonetic converter: re-spaced '{typed_name}' into {wanted} words")
        return candidate
    return ipa


def _one_reading(payloads: list[tuple[str, str]], context: str) -> str:
    """One reading: each model in turn, retrying with a varied request."""
    for model in [m.strip() for m in settings.ipa_models.split(",") if m.strip()]:
        for attempt in range(IPA_ATTEMPTS):
            fmt, payload = payloads[attempt % len(payloads)]
            temperature = IPA_TEMPERATURES[attempt % len(IPA_TEMPERATURES)]
            result = _one_ipa_attempt(model, payload, context, fmt, temperature)
            if result:
                return result
            if attempt < IPA_ATTEMPTS - 1:
                time.sleep(IPA_RETRY_WAIT_SECONDS * (attempt + 1))
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
                    "- No brackets, no slashes\n"
                    "- Mark the stressed syllable with ˈ before it, and any\n"
                    "  secondary stress with ˌ. Do not leave stress out\n"
                    "- Put ONE space between the words of the name and no spaces\n"
                    "  inside a word. A two word name gets exactly one space\n"
                    "- Do NOT use any IPA symbols not listed above\n"
                    "- Do NOT add any preamble, explanation, or lead-in sentence\n"
                    "- Do NOT write things like \"The IPA transcription is:\"\n"
                    "- Your entire reply must be the IPA transcription and nothing else\n"
                    "- Example output for 'Lavneet Hora': ˈlʌvniːt ˈhɔːrə"
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
