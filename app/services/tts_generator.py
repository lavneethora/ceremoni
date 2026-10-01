import asyncio
from xml.sax.saxutils import escape, quoteattr

import azure.cognitiveservices.speech as speechsdk

from app.config import settings

SSML_OPEN = '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="en-US">'


def clean_ipa(ipa: str) -> str:
    # Strip stray delimiters. Stress marks (ˈ primary, ˌ secondary) are kept:
    # Azure uses them to decide which syllable to lean on, and without them
    # every name comes out flat, which is most of what made these sound robotic.
    for ch in "[]/()" ".'\"":
        ipa = ipa.replace(ch, "")
    return ipa.strip()


def name_markup(name: str, ipa: str | None) -> str:
    if ipa:
        return f"<phoneme alphabet=\"ipa\" ph={quoteattr(ipa)}>{escape(name)}</phoneme>"
    return escape(name)


def announcement_markup(name: str, ipa: str | None, major: str | None, honors_phrase: str | None) -> str:
    parts = [name_markup(name, ipa)]
    if major:
        parts.append('<break time="350ms"/>' + escape(major))
    if honors_phrase:
        parts.append('<break time="250ms"/>' + escape(honors_phrase))
    return "".join(parts)


def build_ssml(inner: str) -> str:
    return f'{SSML_OPEN}<voice name="{settings.tts_voice}">{inner}</voice></speak>'


class NoPronunciation(Exception):
    """We have no recorded pronunciation for this name, so we will not say it.

    There is deliberately no spelling-based fallback anywhere in this module.
    Reading a name off its spelling is the failure this system exists to
    prevent, and when it happened silently it reached three ceremonies
    unnoticed. A name we cannot pronounce is reported as "recording could not
    be processed" and a human deals with it.
    """


def _speak(markup: str | None) -> bytes:
    if not markup:
        raise NoPronunciation("Recording could not be processed")

    speech_config = speechsdk.SpeechConfig(
        subscription=settings.azure_speech_key,
        region=settings.azure_speech_region,
    )
    speech_config.set_speech_synthesis_output_format(
        speechsdk.SpeechSynthesisOutputFormat.Audio16Khz32KBitRateMonoMp3
    )
    synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config, audio_config=None)

    result = synthesizer.speak_ssml_async(build_ssml(markup)).get()
    if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
        return result.audio_data

    cancellation = result.cancellation_details
    raise NoPronunciation(
        f"Recording could not be processed: Azure rejected it ({cancellation.error_details})"
    )


async def generate_tts(name: str, ipa: str | None = None) -> bytes:
    return await asyncio.to_thread(_generate_sync, name, ipa)


def _generate_sync(name: str, ipa: str | None = None) -> bytes:
    cleaned = clean_ipa(ipa) if ipa else None
    if not cleaned:
        raise NoPronunciation("Recording could not be processed: no pronunciation available")
    print(f"  IPA for TTS: {cleaned}")
    return _speak(name_markup(name, cleaned))


async def generate_announcement(
    name: str, ipa: str | None, major: str | None, honors_phrase: str | None
) -> bytes:
    """One clip: the name (with its recorded IPA), then the major, then the honors phrase."""
    return await asyncio.to_thread(_generate_announcement_sync, name, ipa, major, honors_phrase)


def _generate_announcement_sync(
    name: str, ipa: str | None, major: str | None, honors_phrase: str | None
) -> bytes:
    cleaned = clean_ipa(ipa) if ipa else None
    if not cleaned:
        raise NoPronunciation("Recording could not be processed: no pronunciation available")
    return _speak(announcement_markup(name, cleaned, major, honors_phrase))
