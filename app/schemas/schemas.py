from pydantic import BaseModel, Field, field_validator
from typing import List, Optional
from datetime import datetime
from uuid import UUID
from app.db.models import VerificationStatusEnum

# 1. Source Evidence Schema
# This handles the JSON structure for the "sources" column[cite: 83].
class SourceMetadata(BaseModel):
    url: str
    source_name: str  # e.g., "NDTV", "Mumbai Police"
    type: str         # "official", "media", "fact_check"
    date_published: Optional[str] = None

# 2. Response Schema (What the UI receives)
# This powers the "Veritas" dashboard cards[cite: 182].
class TimelineItemResponse(BaseModel):
    id: UUID
    claim_text: str
    status: VerificationStatusEnum  # VERIFIED, DEBUNKED, UNCONFIRMED
    summary: Optional[str] = None
    sources: List[SourceMetadata] = []
    timestamp: datetime

    class Config:
        from_attributes = True

# 3. Manual Submission Schema
# Used when a user clicks "VERIFY NOW" on the frontend[cite: 217].
class ClaimSubmission(BaseModel):
    claim_text: str

    # STRICT VALIDATION: The blueprint requires a minimum of 10 characters
    # to prevent spam (e.g., "fake news").
    @field_validator('claim_text')
    def check_length(cls, v):
        if len(v.strip()) < 10:
            raise ValueError('Claim must be at least 10 characters long.')
        return v