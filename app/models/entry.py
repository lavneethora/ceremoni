import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint, false
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

if TYPE_CHECKING:
    from app.models.ceremony import CeremonySession
    from app.models.student import Student


class SessionEntry(Base):
    __tablename__ = "session_entries"
    __table_args__ = (
        UniqueConstraint("session_id", "student_id", name="uq_session_entry_student"),
        UniqueConstraint("session_id", "seat_position", name="uq_session_entry_seat"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("ceremony_sessions.id", ondelete="CASCADE"), nullable=False
    )
    student_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True
    )
    honors_level: Mapped[str | None] = mapped_column(Text, nullable=True)  # "honors" or "highest_honors"
    major: Mapped[str | None] = mapped_column(Text, nullable=True)  # overrides Student.major when set
    seat_position: Mapped[int | None] = mapped_column(Integer, nullable=True)  # starts at 1
    checked_in_at: Mapped[datetime | None] = mapped_column(nullable=True)
    played: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    announcement_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    announcement_audio_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)

    session: Mapped["CeremonySession"] = relationship()
    student: Mapped["Student"] = relationship()


class SessionState(Base):
    __tablename__ = "session_state"

    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("ceremony_sessions.id", ondelete="CASCADE"), primary_key=True
    )
    checkin_open: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
