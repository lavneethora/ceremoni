# Ceremoni

AI-powered graduation name pronunciation system for Texas Tech University.

Students submit their name recordings via Microsoft Forms. The system processes each recording through an AI pipeline to generate ceremony-ready audio that pronounces every graduate's name correctly.

## How It Works

```
Student submits Microsoft Form
  → name, email, college, major, voice recording
      ↓
Backend syncs from OneDrive (Microsoft Graph API)
  → downloads submissions + audio files
      ↓
AI Processing Pipeline
  → audio cleanup (noise reduction, silence trimming)
  → GPT-audio-1.5 listens to recording → IPA transcription
  → Azure Speech Services TTS → ceremony-quality audio
      ↓
Admin Dashboard
  → students organized by session → college → major → name
  → "Play Next" steps through each graduate
  → plays AI-generated pronunciation audio
```

## Pipeline

1. **Audio Cleanup** — Pydub + noisereduce. Trims silence, normalizes volume, reduces background noise. Accepts any audio format (wav, m4a, mp3, mp4, ogg, flac, etc).

2. **Phonetic Extraction** — Sends the cleaned audio to OpenAI's `gpt-audio-1.5` model. The model listens to the student saying their name and produces an IPA transcription using only Azure-compatible phonemes.

3. **Speech Synthesis** — Sends the IPA to Azure Speech Services TTS with SSML `<phoneme>` tags. Generates a clean, ceremony-ready MP3 of the name pronounced correctly.

## Tech Stack

- **Backend:** Python, FastAPI, SQLAlchemy (async), SQLite
- **AI:** OpenAI GPT-audio-1.5 (phonetic extraction), Azure Speech Services (TTS)
- **Audio:** Pydub, FFmpeg, noisereduce
- **Auth:** Microsoft OAuth (MSAL) with TTU Azure AD tenant
- **Data Sync:** Microsoft Graph API (OneDrive/Forms)
- **Frontend:** Vanilla JS, SortableJS, Inter font
- **Task Queue:** Celery + Redis (async processing)

## Setup

### Prerequisites

- Python 3.12+
- FFmpeg (`brew install ffmpeg`)
- Redis (for Celery)

### Installation

```bash
git clone https://github.com/lavneethora/ceremoni.git
cd ceremoni
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

### Configuration

The app requires environment variables for API keys and OAuth credentials. See `app/config.py` for the full list.

### Run

```bash
uvicorn app.api.main:app --reload --port 8000
```

## Project Structure

```
app/
  api/
    main.py              # FastAPI app, lifespan, static files
    routes.py            # Upload/process/audio endpoints
    admin_routes.py      # Auth, sync, ceremony playback endpoints
  models/
    student.py           # Student model
    recording.py         # Recording model (tracks pipeline status)
    ceremony.py          # GraduationEvent, CeremonySession, SessionCollege
  services/
    audio_processor.py   # Noise reduction, trimming, normalization
    phonetic_converter.py # GPT-audio-1.5 → IPA transcription
    tts_generator.py     # Azure TTS with SSML phoneme tags
    pipeline.py          # Orchestrates cleanup → IPA → TTS
    forms_sync.py        # Microsoft Graph API sync from OneDrive
    config_loader.py     # Loads ceremony.yaml into database
    storage.py           # Local file storage abstraction
  templates/
    login.html           # Microsoft OAuth login page
    dashboard.html       # Admin ceremony dashboard
  static/
    dashboard.js         # Session picker, student list, play next
    style.css            # Notion-inspired minimal design
  auth.py                # MSAL OAuth flow
  config.py              # Pydantic settings
  db.py                  # SQLAlchemy async engine + session
ceremony.yaml            # Ceremony setup config
```

## License

MIT
