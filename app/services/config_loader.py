"""
Load ceremony setup from ceremony.yaml into the database.

Events are matched by name and sessions by (event name, session label), so
their IDs stay stable across restarts and reloads. A session removed from the
YAML is deleted only when no entries reference it.
"""

import os

import yaml
from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload

from app.db import async_session
from app.models import (
    CeremonySession,
    GraduationEvent,
    SessionCollege,
    SessionEntry,
    SessionState,
)


CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "ceremony.yaml")

VALID_ORDERS = (None, "seating")

_session_options: dict[str, dict] = {}


def get_session_options(session_id: str) -> dict:
    """Per-session YAML options ("roster", "order"), rebuilt on every config load."""
    return _session_options.get(session_id, {"roster": False, "order": None})


def _validate(config: dict) -> str | None:
    seen_events = set()
    for event_data in config["events"]:
        name = event_data.get("name")
        if not name:
            return "Every event needs a name"
        if name in seen_events:
            return f"Duplicate event name: {name}"
        seen_events.add(name)

        seen_labels = set()
        for session_data in event_data.get("sessions", []):
            label = session_data.get("label")
            if not label:
                return f"A session in '{name}' has no label"
            if label in seen_labels:
                return f"Duplicate session label '{label}' in '{name}'"
            seen_labels.add(label)
            if session_data.get("order") not in VALID_ORDERS:
                return f"Session '{label}' has an invalid order (allowed: seating)"
    return None


async def load_ceremony_config():
    """Read ceremony.yaml and sync it to the database."""
    path = os.path.abspath(CONFIG_PATH)
    if not os.path.exists(path):
        return {"status": "error", "message": f"ceremony.yaml not found at {path}"}

    with open(path) as f:
        config = yaml.safe_load(f)

    if not config or "events" not in config:
        return {"status": "error", "message": "ceremony.yaml has no events defined"}

    error = _validate(config)
    if error:
        return {"status": "error", "message": error}

    options: dict[str, dict] = {}
    keep_event_ids: set[str] = set()
    keep_session_ids: set[str] = set()
    sessions_total = 0
    removed = 0

    async with async_session() as session:
        result = await session.execute(
            select(GraduationEvent).options(selectinload(GraduationEvent.sessions))
        )
        events_by_name = {e.name: e for e in result.scalars().all()}

        for event_data in config["events"]:
            event = events_by_name.get(event_data["name"])
            if event is None:
                event = GraduationEvent(name=event_data["name"])
                session.add(event)
                sessions_by_label = {}
            else:
                sessions_by_label = {s.label: s for s in event.sessions}
            event.active = event_data.get("active", False)
            await session.flush()
            keep_event_ids.add(event.id)

            for i, session_data in enumerate(event_data.get("sessions", []), start=1):
                ceremony_session = sessions_by_label.get(session_data["label"])
                if ceremony_session is None:
                    ceremony_session = CeremonySession(
                        event_id=event.id,
                        label=session_data["label"],
                        date=session_data["date"],
                        time=session_data["time"],
                        session_order=i,
                    )
                    session.add(ceremony_session)
                else:
                    ceremony_session.date = session_data["date"]
                    ceremony_session.time = session_data["time"]
                    ceremony_session.session_order = i
                await session.flush()
                keep_session_ids.add(ceremony_session.id)
                sessions_total += 1

                await session.execute(
                    delete(SessionCollege).where(SessionCollege.session_id == ceremony_session.id)
                )
                for j, college_name in enumerate(session_data.get("colleges", []), start=1):
                    session.add(
                        SessionCollege(
                            session_id=ceremony_session.id,
                            college=college_name,
                            college_order=j,
                        )
                    )

                options[ceremony_session.id] = {
                    "roster": bool(session_data.get("roster", False)),
                    "order": session_data.get("order"),
                }

        stale = await session.execute(
            select(CeremonySession).where(CeremonySession.id.notin_(keep_session_ids))
        )
        for stale_session in stale.scalars().all():
            has_entries = await session.execute(
                select(SessionEntry.id).where(SessionEntry.session_id == stale_session.id).limit(1)
            )
            if has_entries.first() is not None:
                print(f"Config: keeping session '{stale_session.label}' (not in YAML, but it has entries)")
                continue
            await session.execute(delete(SessionCollege).where(SessionCollege.session_id == stale_session.id))
            await session.execute(delete(SessionState).where(SessionState.session_id == stale_session.id))
            await session.execute(delete(CeremonySession).where(CeremonySession.id == stale_session.id))
            removed += 1

        stale_events = await session.execute(
            select(GraduationEvent).where(GraduationEvent.id.notin_(keep_event_ids))
        )
        for stale_event in stale_events.scalars().all():
            has_sessions = await session.execute(
                select(CeremonySession.id).where(CeremonySession.event_id == stale_event.id).limit(1)
            )
            if has_sessions.first() is None:
                await session.execute(delete(GraduationEvent).where(GraduationEvent.id == stale_event.id))

        await session.commit()

    _session_options.clear()
    _session_options.update(options)

    return {
        "status": "ok",
        "events": len(keep_event_ids),
        "sessions": sessions_total,
        "removed": removed,
    }
