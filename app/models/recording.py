import uuid

from sqlalchemy import Boolean, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Recording(Base):
    __tablename__ = "recordings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    student_id: Mapped[str] = mapped_column(String(36), ForeignKey("students.id"), nullable=False)
    original_audio_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    cleaned_audio_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    ipa_representation: Mapped[str | None] = mapped_column(Text, nullable=True)
    phoneme_representation: Mapped[str | None] = mapped_column(Text, nullable=True)
    pronunciation_approved: Mapped[bool] = mapped_column(Boolean, default=False)
    generated_audio_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    processing_status: Mapped[str] = mapped_column(Text, default="uploaded")

    student: Mapped["Student"] = relationship(back_populates="recordings")
