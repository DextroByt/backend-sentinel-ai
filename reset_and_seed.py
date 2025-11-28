import asyncio
import logging
from sqlalchemy import text
from app.db.database import engine, Base
# [CRITICAL] Must import crud to register models with Base.metadata
from app.db import crud  


# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def kill_active_connections():
    """
    Forcefully terminates other connections to the database to prevent
    'DROP TABLE' from hanging due to locks held by the running server.
    """
    kill_sql = text("""
    SELECT pg_terminate_backend(pg_stat_activity.pid)
    FROM pg_stat_activity
    WHERE pg_stat_activity.datname = current_database()
      AND pid <> pg_backend_pid();
    """)
   
    async with engine.begin() as conn:
        try:
            logger.info("🔪 Attempting to kill active database connections...")
            await conn.execute(kill_sql)
            logger.info("✅ Active connections terminated.")
        except Exception as e:
            # Some cloud DBs (like Supabase/Neon) might restrict this.
            # If it fails, we just warn the user.
            logger.warning(f"⚠️ Could not kill connections (Permissions/Platform restriction): {e}")
            logger.warning("👉 IF THE SCRIPT HANGS BELOW, MANUALLY STOP YOUR SERVER (Ctrl+C)!")


async def reset_and_seed():
    print("----------------------------------------------------------------")
   
    # 1. Clear Locks
    await kill_active_connections()


    logger.info("🗑️  WIPING DATABASE (Clean Slate)...")
   
    # 2. DROP & RECREATE TABLES
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
   
    logger.info("✅  Schema Re-created.")
    logger.info("🌑  Database is EMPTY.")
    print("----------------------------------------------------------------")
    print("🚀  SYSTEM READY.")
    print("1. Start your server: 'uvicorn app.main:app --reload'")
    print("2. The Agent will auto-populate data shortly after startup.")


if __name__ == "__main__":
    asyncio.run(reset_and_seed())
