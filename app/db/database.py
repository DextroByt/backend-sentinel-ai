import asyncio
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool
import asyncpg

from app.core.config import settings

# --- Custom Connection Routine ---
async def get_asyncpg_connection():
    return await asyncpg.connect(
        settings.DATABASE_URL.replace("+asyncpg", ""), 
        statement_cache_size=0 
    )

# 1. Setup the Asynchronous Database Engine
engine = create_async_engine(
    settings.DATABASE_URL,
    # --- CRITICAL CHANGE ---
    # We set echo=False to stop the SQL log spam. 
    # This allows the Agent's logic logs to be visible in the terminal.
    echo=False, 
    future=True,
    poolclass=NullPool,
    connect_args={
        "statement_cache_size": 0,
        "prepared_statement_cache_size": 0,
    }
)

Base = declarative_base()

AsyncSessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False, 
)

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Creates a new async database session for each request.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            pass