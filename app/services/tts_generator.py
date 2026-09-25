import asyncio
from xml.sax.saxutils import escape, quoteattr

import azure.cognitiveservices.speech as speechsdk

from app.config import settings

SSML_OPEN = '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="en-US">'


def clean_ipa(ipa: str) -> str:
    # Strip any stray brackets/slashes/stress marks
    for ch in "[]/()" "ˈˌ.'\"":
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


def _speak(primary: str | None, fallback: str) -> bytes:
    speech_config = speechsdk.SpeechConfig(
        subscription=settings.azure_speech_key,
        region=settings.azure_speech_region,
    )
    speech_config.set_speech_synthesis_output_format(
        speechsdk.SpeechSynthesisOutputFormat.Audio16Khz32KBitRateMonoMp3
    )
    synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config, audio_config=None)

    if primary:
        result = synthesizer.speak_ssml_async(build_ssml(primary)).get()
        if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
            return result.audio_data

        # If IPA failed, warn and fall back
        cancellation = result.cancellation_details
        print(f"  ⚠ IPA rejected: {cancellation.error_details}")
        print("  Falling back to plain text...")

    result = synthesizer.speak_ssml_async(build_ssml(fallback)).get()
    if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
        return result.audio_data

    cancellation = result.cancellation_details
    raise RuntimeError(f"TTS failed: {cancellation.reason} - {cancellation.error_details}")


async def generate_tts(name: str, ipa: str | None = None) -> bytes:
    return await asyncio.to_thread(_generate_sync, name, ipa)


def _generate_sync(name: str, ipa: str | None = None) -> bytes:
    cleaned = clean_ipa(ipa) if ipa else None
    if cleaned:
        print(f"  IPA for TTS: {cleaned}")
    return _speak(name_markup(name, cleaned) if cleaned else None, escape(name))


async def generate_announcement(
    name: str, ipa: str | None, major: str | None, honors_phrase: str | None
) -> bytes:
    """One clip: the name (with its cached IPA), then the major, then the honors phrase."""
    return await asyncio.to_thread(_generate_announcement_sync, name, ipa, major, honors_phrase)


def _generate_announcement_sync(
    name: str, ipa: str | None, major: str | None, honors_phrase: str | None
) -> bytes:
    cleaned = clean_ipa(ipa) if ipa else None
    primary = announcement_markup(name, cleaned, major, honors_phrase) if cleaned else None
    return _speak(primary, announcement_markup(name, None, major, honors_phrase))
