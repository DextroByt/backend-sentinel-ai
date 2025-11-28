import logging
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# --- Core Imports ---
from app.core.config import settings
from app.db.database import engine, Base
from app.routers import crisis_router
from app.services import scanner_service

# --- Logging Configuration ---
# Configures a robust logging format for production debugging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [%(name)s] - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("sentinel_ai")

# --- Lifespan Context Manager (Reference §2.1) ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Manages the startup and shutdown lifecycle of the application.
    1. Synchronizes Database Schema.
    2. Launches the Autonomous Scanner Service.
    3. Handles Graceful Shutdown.
    """
    logger.info(">>> Sentinel AI Backend Initializing <<<")
    logger.info("🛡️ Time-Gate Architecture initialized: Strict 24h News Filter & 3-Day Cleanup Policy Active.")

    # 1. Database Initialization
    try:
        # Create tables if they don't exist
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("✅ Database schema synchronized.")
    except Exception as e:
        logger.critical(f"❌ Critical Database Failure on Startup: {e}")
        raise e  # Stop deployment if DB fails

    # 2. Background Scanner Service
    # We store the task in app.state to prevent garbage collection
    try:
        app.state.scanner_task = asyncio.create_task(scanner_service.start_monitoring())
        logger.info("✅ Scanner Service launched in background.")
    except Exception as e:
        logger.error(f"❌ Failed to start Scanner Service: {e}")

    yield  # Application runs here

    # 3. Graceful Shutdown
    logger.info(">>> Shutting down Sentinel AI... <<<")
    
    if hasattr(app.state, "scanner_task"):
        logger.info("Stopping Scanner Service...")
        app.state.scanner_task.cancel()
        try:
            await app.state.scanner_task
        except asyncio.CancelledError:
            logger.info("✅ Scanner Service stopped gracefully.")

# --- FastAPI App Initialization ---
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.VERSION,
    description="Sentinel AI: Verified Crisis Timelines (Backend API)",
    lifespan=lifespan,
    # Disable default docs in prod if needed, keeping enabled for Reference compliance
    docs_url="/docs",
    redoc_url="/redoc"
)

# --- CORS Policy (Reference §1.2 - Decoupled Monolith) ---
# Allows the Vanilla JS frontend (served potentially from a different origin/port)
# to communicate with this backend.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In strict production, replace "*" with specific frontend domains
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

# --- Router Registration ---
app.include_router(crisis_router.router)

# --- Root Endpoint ---
@app.get("/", tags=["System"])
async def root():
    """
    Health check endpoint to verify backend status.
    """
    return {
        "status": "online",
        "service": settings.APP_NAME,
        "version": settings.VERSION,
        "environment": settings.ENVIRONMENT,
        "mode": "backend-only",
        "time_gate": "active" 
    }

@app.get("/api/health", tags=["System"])
async def health_check():
    """
    Lightweight health check for load balancers.
    """
    return {"status": "ok"}

# --- Entry Point for Local Debugging ---
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app", 
        host="0.0.0.0", 
        port=8000, 
        reload=settings.DEBUG
    )