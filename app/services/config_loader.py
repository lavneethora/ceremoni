"""
Load ceremony setup from ceremony.yaml into the database.

The admin edits ceremony.yaml to define events, sessions, and college walk order.
This module reads the file and syncs it to the DB (idempotent — safe to run repeatedly).
"""

import os
import yaml
from sqlalchemy import select, delete

from app.db import async_session
from app.models import GraduationEvent, CeremonySession, SessionCollege


CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "ceremony.yaml")


async def load_ceremony_config():
    """Read ceremony.yaml and sync to database. Replaces all existing config."""
    path = os.path.abspath(CONFIG_PATH)
    if not os.path.exists(path):
        return {"status": "error", "message": f"ceremony.yaml not found at {path}"}

    with open(path) as f:
        config = yaml.safe_load(f)

    if not config or "events" not in config:
        return {"status": "error", "message": "ceremony.yaml has no events defined"}

    async with async_session() as session:
        # Clear existing config (cascade will handle children)
        # Delete in order: colleges → sessions → events
        await session.execute(delete(SessionCollege))
        await session.execute(delete(CeremonySession))
        await session.execute(delete(GraduationEvent))

        events_created = 0
        sessions_created = 0

        for event_data in config["events"]:
            event = GraduationEvent(
                name=event_data["name"],
                active=event_data.get("active", False),
            )
            session.add(event)
            await session.flush()
            events_created += 1

            for i, session_data in enumerate(event_data.get("sessions", []), start=1):
                ceremony_session = CeremonySession(
                    event_id=event.id,
                    label=session_data["label"],
                    date=session_data["date"],
                    time=session_data["time"],
                    session_order=i,
                )
                session.add(ceremony_session)
                await session.flush()
                sessions_created += 1

                for j, college_name in enumerate(session_data.get("colleges", []), start=1):
                    sc = SessionCollege(
                        session_id=ceremony_session.id,
                        college=college_name,
                        college_order=j,
                    )
                    session.add(sc)

        await session.commit()

    return {
        "status": "ok",
        "events": events_created,
        "sessions": sessions_created,
    }
