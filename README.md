# Ceremoni

AI-powered graduation name pronunciation system for universities.

Every graduation, name readers stumble through hundreds of names, especially international, non-Western, or unusually spelled ones. Ceremoni eliminates that. Students record themselves saying their name, the system extracts the exact phonetic pronunciation, and generates ceremony-ready audio that says each name *the way the student said it*.

Built to be deployed at any institution. The first deployment is with the **Texas Tech Honors College**, for their Honors Medallion ceremony, with wider commencement to follow.

## The one rule

**The system never reads a name off its spelling.** Guessing from letters is the problem it exists to solve, so a name that cannot be pronounced from a real recording is reported as "recording could not be processed" for a human to handle. There is no spelling fallback anywhere in the codebase.

## How it works

### Before the ceremony

```
Student submits a form
  name, email, college, major, voice recording
      |
Sync submissions and audio
      |
Pronunciation pipeline
  normalise the audio, read it for IPA, synthesise the announcement
      |
Print one card per student, each with a QR code
```

An announcement is assembled from whatever the institution wants read out: the
name, and optionally a programme and a distinction. A clip can be as simple as a
name, or as full as "name, major, with highest honors".

### At the ceremony

Students carry their card in line. The announcer opens Reader mode, starts the
camera, and scans each card as the student reaches the stage, which plays their
announcement. The order is simply whoever is standing there, so nothing has to
be arranged in advance and a late arrival is not a special case.

A student with no recording still gets a card, without a QR, showing their
details for the announcer to read aloud.

## The pronunciation pipeline

**1. Audio normalisation.** Converts to mono 16 kHz, normalises gain and reduces
background noise. Accepts whatever format the student's phone produced.

It deliberately does not trim silence. A threshold relative to each clip's own
loudness cut into real speech on quieter recordings, removing 57% and 60% of two
real clips from the edges of actual words.

**2. Phonetic extraction.** The cleaned audio goes to an audio model that listens
and returns an IPA transcription using only phonemes the speech synthesiser can
produce. A single answer is not reliable enough to trust on its own, so:

- **Several models are tried in order**, configurable and pinned to dated
  snapshots so one cannot change under a running ceremony. Measured over the same
  recordings, each model handled clips the other could not.
- **Retries vary the request.** Repeating an identical call tends to return the
  same non-answer, so each attempt changes the temperature and the audio
  container.
- **Each recording is read three times and the majority wins.** Validation can
  catch a malformed answer but not a plausible wrong one: the same audio once
  produced two different, well-formed, wrong readings of one name against four
  correct ones.
- **Every character is checked** against the symbol set the synthesiser supports,
  rejecting prose, garbage, and symbols that would fail at synthesis.

**3. Speech synthesis.** SSML `<phoneme>` tags, with stress marks preserved,
since dropping them makes every name come out flat. Clip filenames carry a hash
of the pronunciation, so a regenerated clip gets a new URL and a CDN cannot serve
the old one.

## Admin

- **Dashboard** lists a session's students, their announcement status, and the
  roster tools.
- **Cards** renders the printable sheet, one card per student.
- **Reader** is the announcer's screen: scan mode, or a sequential queue.
- **Check-in** (legacy) offers earlier methods of fixing an announcing order
  ahead of time: ushers tapping names, a shared queue QR, and per-seat QR codes.
  Superseded by card scanning, kept working.

Ceremonies are configured in `ceremony.yaml`: events, sessions, which colleges
walk in which session and in what order, which event is currently `active`, and
whether a session draws its students from an imported roster rather than from
college membership.

## Tech stack

- **Backend:** Python, FastAPI, SQLAlchemy (async)
- **Database:** Postgres in production, SQLite locally
- **AI:** OpenAI audio models for phonetic extraction, Azure Speech Services for synthesis
- **Audio:** Pydub, FFmpeg, noisereduce
- **Storage:** Supabase Storage, or the local filesystem
- **Auth:** Microsoft OAuth (MSAL), works with any institution's Azure AD tenant
- **Data sync:** Microsoft Graph API (OneDrive and Forms)
- **Frontend:** Jinja templates and vanilla JS, no build step

Processing runs inline in the request. `app/worker.py` and `app/tasks.py` hold
optional Celery task definitions, but Celery is not a declared dependency and is
not deployed, so those imports fall through.

## Setup

### Prerequisites

- Python 3.12+
- FFmpeg (`brew install ffmpeg`)

### Installation

```bash
git clone https://github.com/lavneethora/ceremoni.git
cd ceremoni
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

### Configuration

Set through environment variables; see `app/config.py` for the full list. The
ones without usable defaults are the OpenAI and Azure keys, the Microsoft app
registration, `SESSION_SECRET`, and Supabase credentials if you want hosted
storage. `SESSION_SECRET` has no fallback and the app refuses to start without
it, because it signs both admin cookies and every QR code.

### Run

```bash
uvicorn app.api.main:app --reload --port 8000
```

## Project structure

```
app/
  api/
    main.py               # FastAPI app, lifespan, pages, static files
    routes.py             # Upload, process and audio endpoints
    admin_routes.py       # Auth, sync, roster, seating, cards, playback
    checkin_public.py     # The only unauthenticated routes (legacy check-in)
  models/
    student.py            # Student
    recording.py          # Recording and its pipeline status
    ceremony.py           # GraduationEvent, CeremonySession, SessionCollege
    entry.py              # A student's place in one session
  services/
    audio_processor.py    # Normalisation before extraction
    phonetic_converter.py # Audio to IPA, with retries, several models and a vote
    tts_generator.py      # Synthesis, and the refusal when there is no IPA
    pipeline.py           # Orchestrates normalise, extract, synthesise
    announcements.py      # Per ceremony clips
    roster_import.py      # CSV import and clearing
    session_queue.py      # Who is in a session and in what order
    seating.py            # Announcing order assignment
    checkin_links.py      # Signed card codes and check-in links
    storage.py            # Supabase or local filesystem
    storage_cleanup.py    # Finds audio no database row points at
    forms_sync.py         # Microsoft Graph sync
    config_loader.py      # Loads ceremony.yaml into the database
  templates/              # login, dashboard, reader, cards, check-in
  static/                 # One js and css file per page
  auth.py                 # MSAL OAuth
  config.py               # Pydantic settings
  db.py                   # SQLAlchemy async engine and session
ceremony.yaml             # Events, sessions, college order, roster options
```

## License

Copyright Lavneet Hora 2026 <br>
All rights reserved. <br>
This software is not licensed for distribution, modification, or commercial use without explicit written permission from the author.
