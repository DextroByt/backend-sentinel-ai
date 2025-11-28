import os
from typing import List, Literal
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    # Application Info
    PROJECT_NAME: str = "Sentinel AI"
    API_V1_STR: str = "/api/v1"
    
    # Security & Database
    DATABASE_URL: str
    SECRET_KEY: str
    
    # External APIs
    GEMINI_API_KEY: str
    NEWS_API_KEY: str
    
    # AI Model Configuration [cite: 75, 76]
    # Optimized for speed and context window
    GEMINI_EXTRACTION_MODEL: str = "gemini-2.5-flash"
    GEMINI_SYNTHESIS_MODEL: str = "gemini-2.5-flash"

    # Agent Configurations
    # List of trusted domains for Official Agent [cite: 90]
    OFFICIAL_DOMAINS: List[str] = [
        "ndrf.gov.in", 
        "mumbaipolice.gov.in", 
        "pib.gov.in", 
        "who.int",
        "imd.gov.in"
    ]
    
    # Fact Check Repositories [cite: 103]
    FACT_CHECK_DOMAINS: List[str] = [
        "altnews.in",
        "boomlive.in",
        "snopes.com"
    ]

    # Algorithm Thresholds [cite: 107, 144]
    DEBUNK_SIMILARITY_THRESHOLD: float = 0.2
    DEBUNK_DEEP_GATHERING_THRESHOLD: float = 0.15

    model_config = SettingsConfigDict(
        env_file=".env", 
        case_sensitive=True,
        extra="ignore"
    )

settings = Settings()