import os
from typing import Optional
from pydantic_settings import BaseSettings

# Calculate the root directory (backend-sentinel-ai/)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

class Settings(BaseSettings):
    """
    Application Settings Configuration.
    Managed via pydantic_settings to ensure strict typing and validation.
    Adheres to the 12-Factor App methodology (Reference §2.4).
    """

    # --- Application Metadata ---
    APP_NAME: str = "Sentinel AI"
    ENVIRONMENT: str = "development"
    DEBUG: bool = True
    VERSION: str = "1.0.0"

    # --- Database Configuration (Reference §2.2) ---
    # The connection string for the PostgreSQL database (AsyncPG).
    # Critical for the "Decoupled Monolith" architecture.
    DATABASE_URL: str

    # --- External API Keys (Reference §2.4) ---
    # These secrets are injected via environment variables (.env) for security.
    GEMINI_API_KEY: str
    
    # NOTE: NewsAPI and NewsDataAPI keys have been removed in favor of free RSS/DDGS services.

    # --- AI Model Configuration (Reference §2.4) ---
    # "Gemini 2.5 Flash" is selected for its high efficiency and large context window,
    # essential for parsing lengthy news articles during the extraction phase.
    GEMINI_EXTRACTION_MODEL: str = "gemini-2.5-flash"
    GEMINI_SYNTHESIS_MODEL: str = "gemini-2.5-flash"

    # --- Pydantic Config ---
    class Config:
        # Load variables from the .env file in the root directory
        env_file = os.path.join(BASE_DIR, ".env")
        env_file_encoding = 'utf-8'
        # Ensure environment variables match the case defined above
        case_sensitive = True
        # Ignore extra environment variables to prevent runtime crashes
        extra = "ignore"

# Instantiate the settings object to be imported by other modules
settings = Settings()