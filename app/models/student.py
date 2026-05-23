import uuid
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Student(Base):
    __tablename__ = "students"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    typed_name: Mapped[str] = mapped_column(Text, nullable=False)
    phonetic_hint: Mapped[str | None] = mapped_column(Text, nullable=True)
    email: Mapped[str | None] = mapped_column(Text, nullable=True)
    r_number: Mapped[str | None] = mapped_column(Text, nullable=True)
    degree_level: Mapped[str | None] = mapped_column(Text, nullable=True)  # "undergraduate" or "graduate"
    college: Mapped[str | None] = mapped_column(Text, nullable=True)
    major: Mapped[str | None] = mapped_column(Text, nullable=True)
    graduation_event_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("graduation_events.id"), nullable=True)
    sort_order: Mapped[int | None] = mapped_column(Integer, nullable=True)
    played: Mapped[bool] = mapped_column(Boolean, default=False)
    ms_form_response_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)

    recordings: Mapped[list["Recording"]] = relationship(back_populates="student")
    graduation_event: Mapped["GraduationEvent | None"] = relationship(back_populates="students")
