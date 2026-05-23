import asyncio

import azure.cognitiveservices.speech as speechsdk

from app.config import settings


async def generate_tts(name: str, ipa: str | None = None) -> bytes:
    return await asyncio.to_thread(_generate_sync, name, ipa)


def _generate_sync(name: str, ipa: str | None = None) -> bytes:
    speech_config = speechsdk.SpeechConfig(
        subscription=settings.azure_speech_key,
        region=settings.azure_speech_region,
    )
    speech_config.set_speech_synthesis_output_format(
        speechsdk.SpeechSynthesisOutputFormat.Audio16Khz32KBitRateMonoMp3
    )

    synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config, audio_config=None)

    if ipa:
        # Strip any stray brackets/slashes/stress marks
        clean_ipa = ipa
        for ch in "[]/()" "ˈˌ.'\"":
            clean_ipa = clean_ipa.replace(ch, "")
        clean_ipa = clean_ipa.strip()

        print(f"  IPA for TTS: {clean_ipa}")

        ssml = (
            '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="en-US">'
            f'<voice name="{settings.tts_voice}">'
            f'<phoneme alphabet="ipa" ph="{clean_ipa}">{name}</phoneme>'
            '</voice></speak>'
        )
        result = synthesizer.speak_ssml_async(ssml).get()

        if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
            return result.audio_data

        # If IPA failed, warn and fall back
        cancellation = result.cancellation_details
        print(f"  ⚠ IPA rejected: {cancellation.error_details}")
        print(f"  Falling back to plain text...")

    # Plain text fallback
    ssml = (
        '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="en-US">'
        f'<voice name="{settings.tts_voice}">{name}</voice></speak>'
    )
    result = synthesizer.speak_ssml_async(ssml).get()

    if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
        return result.audio_data

    cancellation = result.cancellation_details
    raise RuntimeError(f"TTS failed: {cancellation.reason} — {cancellation.error_details}")
