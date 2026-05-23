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

### Environment Variables

Copy `.env.example` to `.env` and fill in:

```
OPENAI_API_KEY=           # OpenAI API key (for GPT-audio-1.5)
AZURE_SPEECH_KEY=         # Azure Speech Services key
AZURE_SPEECH_REGION=      # Azure region (e.g. southcentralus)
MS_CLIENT_ID=             # Azure AD app client ID
MS_TENANT_ID=             # Azure AD tenant ID
MS_CLIENT_SECRET=         # Azure AD app client secret
ADMIN_EMAILS=             # Comma-separated admin emails
```

### Ceremony Configuration

Edit `ceremony.yaml` to define graduation events, sessions, and college walk order:

```yaml
events:
  - name: Spring 2026 Undergraduate
    active: true
    sessions:
      - label: "Friday 9:00 AM"
        date: "2026-05-15"
        time: "9:00 AM"
        colleges:
          - College of Arts & Sciences
          - College of Education
```

The config is loaded into the database on server startup.

### Run

```bash
uvicorn app.api.main:app --reload --port 8000
```

Dashboard: `http://localhost:8000/admin`

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
