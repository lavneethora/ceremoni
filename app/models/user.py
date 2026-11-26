import uuid
from datetime import datetime

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

ROLE_ADMIN = "admin"
ROLE_READER = "reader"
ROLES = (ROLE_ADMIN, ROLE_READER)


class User(Base):
    """An account that signs in with a username and a password.

    Separate from the Microsoft sign-in, which stays for the one person who
    needs a Graph token to sync the Form. These exist so the machine at the
    stage can be handed to Honors College staff with access to Reader and
    nothing else, without sharing anyone's university account.

    A reader is not read-only: announcing marks students played and scanning
    a card writes a position, so the role is about which endpoints are
    reachable, not about whether they write.
    """

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default=ROLE_READER)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
