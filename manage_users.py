"""Create and list the accounts that sign in with a username and password.

Deliberately a script rather than a page: there will be two or three of these
and no reason to put account creation on the public internet.

    python manage_users.py

Reads DATABASE_URL from the environment or .env, or asks. Nothing is written
to disk, and the password is never echoed.
"""

import asyncio
import getpass
import os
import sys

from sqlalchemy import select

from app.auth import hash_password
from app.models import ROLES, ROLE_READER, User


def _normalise(url: str) -> str:
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+asyncpg://", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


async def main() -> None:
    from app.config import settings

    url = os.environ.get("DATABASE_URL") or settings.database_url
    print(f"Database: {url.split('@')[-1] if '@' in url else url}")
    if input("Is that the one you mean? [y/N] ").strip().lower() != "y":
        url = _normalise(input("DATABASE_URL: ").strip())

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    connect_args = (
        {"ssl": "require", "statement_cache_size": 0}
        if "supabase" in url or "pooler" in url
        else {}
    )
    engine = create_async_engine(_normalise(url), connect_args=connect_args)
    Session = async_sessionmaker(engine)

    from app.db import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with Session() as db:
        existing = (await db.execute(select(User).order_by(User.username))).scalars().all()
        print(f"\n{len(existing)} account(s):")
        for u in existing:
            print(f"  {u.username:20} {u.role}")

        print("\nNew account (blank username to stop):")
        username = input("  username: ").strip().lower()
        if not username:
            await engine.dispose()
            return

        if any(u.username == username for u in existing):
            print(f"  '{username}' already exists. Nothing changed.")
            await engine.dispose()
            return

        print(f"  role {ROLES}")
        role = input(f"  role [{ROLE_READER}]: ").strip().lower() or ROLE_READER
        if role not in ROLES:
            print(f"  '{role}' is not a role. Nothing changed.")
            await engine.dispose()
            return

        password = getpass.getpass("  password (hidden): ")
        if len(password) < 12:
            print("  Too short. Use at least 12 characters. Nothing changed.")
            await engine.dispose()
            return
        if getpass.getpass("  again: ") != password:
            print("  They do not match. Nothing changed.")
            await engine.dispose()
            return

        db.add(User(username=username, password_hash=hash_password(password), role=role))
        await db.commit()
        print(f"\n  Created {username} as {role}.")

    await engine.dispose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped, nothing changed.")
        sys.exit(1)
