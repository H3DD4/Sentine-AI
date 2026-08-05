from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from contextlib import asynccontextmanager
from app.config import settings

engine = create_async_engine(settings.DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)

# Base is defined in models.py to ensure all models inherit from the same Base
# Re-export for convenience
from app.models import Base

async def get_session():
    async with AsyncSessionLocal() as session:
        yield session

@asynccontextmanager
async def get_session_context():
    async with AsyncSessionLocal() as session:
        yield session