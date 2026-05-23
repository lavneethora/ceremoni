import asyncio
import os
import sys
import io

import numpy as np
import sounddevice as sd
import soundfile as sf
from pydub import AudioSegment
from pydub.playback import play as pydub_play

from app.db import init_db, async_session
from app.models import Student, Recording
from app.services.storage import storage
from app.services import pipeline, phonetic_converter, tts_generator


SAMPLE_RATE = 16000
CEREMONY_DIR = "./ceremony_audio"


def _record_audio() -> bytes | None:
    import termios, tty

    print("\nPress Enter to START recording...")
    input()

    print("🔴 Recording... press Enter to STOP")
    sys.stdout.flush()

    frames = []
    recording_active = True

    def callback(indata, frame_count, time_info, status):
        if recording_active:
            frames.append(indata.copy())

    stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16", callback=callback)
    stream.start()

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        while True:
            ch = sys.stdin.read(1)
            if ch in ('\r', '\n'):
                break
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

    recording_active = False
    stream.stop()
    stream.close()
    print()

    if not frames:
        print("No audio captured.")
        return None

    audio_data = np.concatenate(frames)
    buf = io.BytesIO()
    sf.write(buf, audio_data, SAMPLE_RATE, format="WAV")
    print(f"Captured {len(audio_data) / SAMPLE_RATE:.1f}s of audio.")
    return buf.getvalue()


async def record_mode():
    os.makedirs(CEREMONY_DIR, exist_ok=True)

    while True:
        print("\n" + "=" * 50)
        typed_name = input("Enter student's full name (or 'q' to quit): ").strip()
        if typed_name.lower() == "q":
            break

        phonetic_hint = input("Optional phonetic hint (press Enter to skip): ").strip() or None

        approved = False
        while not approved:
            raw_bytes = _record_audio()
            if raw_bytes is None:
                break

            async with async_session() as session:
                student = Student(typed_name=typed_name, phonetic_hint=phonetic_hint)
                session.add(student)
                await session.flush()

                rec = Recording(student_id=student.id, processing_status="uploaded")
                session.add(rec)
                await session.flush()

                original_path = await storage.save(student.id, f"{rec.id}_original.wav", raw_bytes)
                rec.original_audio_url = original_path
                await session.commit()

                print("\nProcessing audio (cleanup → transcription → phonetics → TTS)...")
                rec = await pipeline.process(rec.id, session)
                audio_bytes = await pipeline.generate_final_audio(rec.id, session)

                print(f"\n  Name: {typed_name}")
                print("  Playing generated ceremony audio...")
                _play_mp3(audio_bytes)

                choice = input("\nDoes that sound right? [a]pprove, [r]e-record, [s]kip? ").strip().lower()

                if choice == "a":
                    _save_ceremony(typed_name, audio_bytes)
                    print(f"Saved to {CEREMONY_DIR}/{_safe_filename(typed_name)}.mp3")
                    approved = True

                elif choice == "r":
                    print("Re-recording...")

                else:
                    print("Skipped.")
                    break


async def play_mode():
    if not os.path.exists(CEREMONY_DIR):
        print("No ceremony audio found.")
        return

    files = sorted(f for f in os.listdir(CEREMONY_DIR) if f.endswith(".mp3"))
    if not files:
        print("No ceremony audio found.")
        return

    while True:
        print(f"\n{'=' * 50}")
        print("Ceremony Audio Files:")
        for i, f in enumerate(files, 1):
            print(f"  {i}. {f[:-4]}")

        choice = input("\nEnter number to play (or 'q' to quit): ").strip()
        if choice.lower() == "q":
            break

        try:
            idx = int(choice) - 1
            if 0 <= idx < len(files):
                path = os.path.join(CEREMONY_DIR, files[idx])
                print(f"Playing: {files[idx][:-4]}")
                audio = AudioSegment.from_mp3(path)
                pydub_play(audio)
            else:
                print("Invalid number.")
        except ValueError:
            print("Enter a number.")


def _play_mp3(audio_bytes: bytes):
    audio = AudioSegment.from_file(io.BytesIO(audio_bytes), format="mp3")
    pydub_play(audio)


def _safe_filename(name: str) -> str:
    return name.replace(" ", "_").replace("/", "-")


def _save_ceremony(typed_name: str, audio_bytes: bytes):
    os.makedirs(CEREMONY_DIR, exist_ok=True)
    path = os.path.join(CEREMONY_DIR, f"{_safe_filename(typed_name)}.mp3")
    with open(path, "wb") as f:
        f.write(audio_bytes)


async def main():
    await init_db()

    if len(sys.argv) < 2:
        print("Usage: python run.py [record|play]")
        print("  record  - Record and process name pronunciations")
        print("  play    - Browse and play ceremony audio")
        return

    mode = sys.argv[1].lower()
    if mode == "record":
        await record_mode()
    elif mode == "play":
        await play_mode()
    else:
        print(f"Unknown mode: {mode}")
        print("Usage: python run.py [record|play]")


if __name__ == "__main__":
    asyncio.run(main())
