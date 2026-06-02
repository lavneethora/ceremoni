from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

# Supabase pooler needs SSL and prepared statement cache disabled
connect_args = {}
if "supabase" in settings.database_url or "pooler" in settings.database_url:
    connect_args = {"ssl": "require", "statement_cache_size": 0}

engine = create_async_engine(settings.database_url, echo=False, connect_args=connect_args)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def init_db():
    from app.models import Student, Recording  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncSession:
    async with async_session() as session:
        yield session
