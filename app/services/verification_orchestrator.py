import asyncio
from app.services.synthesizer_service import synthesize_verdict
from app.db.database import SessionLocal
from app.db.models import TimelineItem

# --- MOCK AGENTS (For testing flow without external scraping) ---
async def mock_official_check(claim):
    if "flood" in claim.lower():
        return ["Gov Report: No floods detected."]
    return []

async def mock_media_check(claim):
    return [] # Simulate no media coverage

async def mock_debunk_check(claim):
    if "alien" in claim.lower():
        return ["FactCheck.org: No aliens found."]
    return []
# ----------------------------------------------------------------

async def run_verification_pipeline(claim_text: str):
    """
    Orchestrates the 3-agent check and synthesis.
    Ref: Blueprint Section 3.2 (Parallel Execution)
    """
    print(f"Orchestrator: Verifying '{claim_text}'...")

    # 1. PARALLEL EXECUTION: Run all agents at once
    # In the real version, we will import real agents here.
    official_task = mock_official_check(claim_text)
    media_task = mock_media_check(claim_text)
    debunk_task = mock_debunk_check(claim_text)

    official_res, media_res, debunk_res = await asyncio.gather(
        official_task, media_task, debunk_task
    )

    evidence_package = {
        "official": official_res,
        "media": media_res,
        "debunk": debunk_res
    }

    # 2. SYNTHESIS: Ask Gemini for the verdict
    verdict_data = await synthesize_verdict(claim_text, evidence_package)

    # 3. DB PERSISTENCE: Save result
    async with SessionLocal() as db:
        new_item = TimelineItem(
            claim_text=claim_text,
            status=verdict_data['status'],
            summary=verdict_data['summary'],
            sources=verdict_data['sources']
        )
        db.add(new_item)
        await db.commit()
        print(f"Orchestrator: Saved verdict {verdict_data['status']}")