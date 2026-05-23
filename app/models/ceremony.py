import uuid
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


TTU_COLLEGES = [
    "College of Arts & Sciences",
    "College of Education",
    "College of Health & Human Sciences",
    "Edward E. Whitacre Jr. College of Engineering",
    "Honors College",
    "J.T. & Margaret Talkington College of Visual & Performing Arts",
    "Jerry S. Rawls College of Business Administration",
    "Huckabee College of Architecture",
    "School of Professional Studies",
    "College of Media & Communication",
    "Davis College of Agricultural Sciences & Natural Resources",
]


class GraduationEvent(Base):
    __tablename__ = "graduation_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(Text, nullable=False)  # e.g. "Spring 2026 Undergraduate"
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)

    sessions: Mapped[list["CeremonySession"]] = relationship(back_populates="event", order_by="CeremonySession.session_order")
    students: Mapped[list["Student"]] = relationship(back_populates="graduation_event")


class CeremonySession(Base):
    __tablename__ = "ceremony_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    event_id: Mapped[str] = mapped_column(String(36), ForeignKey("graduation_events.id"), nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)  # e.g. "Friday 9:00 AM"
    date: Mapped[str] = mapped_column(Text, nullable=False)  # e.g. "2026-05-15"
    time: Mapped[str] = mapped_column(Text, nullable=False)  # e.g. "9:00 AM"
    session_order: Mapped[int] = mapped_column(Integer, nullable=False)  # 1, 2, 3, 4

    event: Mapped["GraduationEvent"] = relationship(back_populates="sessions")
    colleges: Mapped[list["SessionCollege"]] = relationship(back_populates="session", order_by="SessionCollege.college_order")


class SessionCollege(Base):
    __tablename__ = "session_colleges"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("ceremony_sessions.id"), nullable=False)
    college: Mapped[str] = mapped_column(Text, nullable=False)
    college_order: Mapped[int] = mapped_column(Integer, nullable=False)

    session: Mapped["CeremonySession"] = relationship(back_populates="colleges")
