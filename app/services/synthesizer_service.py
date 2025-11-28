import json
import logging
import re
from uuid import UUID
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold

from app.core.config import settings
from app.db import crud

logger = logging.getLogger(__name__)

# Configure Gemini
try:
    genai.configure(api_key=settings.GEMINI_API_KEY)
except Exception as e:
    logger.error(f"Failed to configure Gemini API: {e}")

# --- Prompts ---

# [UPDATED] Focused on Truth vs. Fiction & Harm Potential
SYNTHESIS_PROMPT_TEMPLATE = """
You are the Chief Misinformation Analyst for Sentinel AI. 
Your task is to analyze a specific claim based ONLY on the provided evidence and determine its veracity and potential for harm.

CURRENT TIME: {current_time}

CLAIM TO ANALYZE: "{claim}"

EVIDENCE COLLECTED:
1. Official Government Sources:
{official_evidence}

2. Media Reports:
{media_evidence}

3. Fact-Check & OSINT Databases (Prior Debunks):
{debunk_evidence}

CRITICAL INSTRUCTIONS:
1. **DETECT ZOMBIE RUMORS:** Check if the evidence mentions this is an "old video", "recycled image", or "out of context" content from a previous year.
2. **ASSESS HARM:** If the claim is false, is it dangerous? (e.g., fake cures, inciting violence, nuclear panic).
3. **VERDICT LOGIC:**
   - Status "VERIFIED": Multiple credible sources confirm the event is REAL and CURRENT.
   - Status "DEBUNKED": Official sources deny it, OR fact-checkers label it fake, OR it is proven to be old footage.
   - Status "UNCONFIRMED": Conflicting reports or lack of credible evidence.

OUTPUT REQUIREMENT:
Return a single, valid JSON object with this exact structure:
{{
  "status": "VERIFIED" | "DEBUNKED" | "UNCONFIRMED",
  "summary": "A concise 2-sentence explanation. If DEBUNKED, explain WHY (e.g., 'This is a 2018 video from Syria, not Mumbai.').",
  "sources": [
    {{ "title": "Source Name", "url": "URL" }}
  ]
}}
"""

# [UPDATED] Master Conclusion now distinguishes Real Disasters vs. Lethal Hoaxes
CRISIS_CONCLUSION_PROMPT = """
You are the Strategic Threat Analyst for Sentinel AI.
Your task is to generate a "Live Threat Assessment" for an active narrative.
You must distinguish between Real Crises and Viral Misinformation Campaigns.

CURRENT SYSTEM TIME: {current_time}
NARRATIVE / CRISIS NAME: "{crisis_name}"

VERIFIED FACTS (Truth):
{verified_items}

DEBUNKED RUMORS (Lies/Hoaxes):
{debunked_items}

UNCONFIRMED REPORTS (Noise):
{unconfirmed_items}

INSTRUCTIONS:
1. **LETHALITY CHECK:** Does this narrative pose a threat to life? (e.g., Riots, Floods, Medical Panics).
2. **DETERMINE MASTER VERDICT:**
   - "CATASTROPHIC EMERGENCY": A REAL, verified event with mass casualties or destruction (e.g., Actual Earthquake).
   - "LETHAL MISINFORMATION": A FAKE narrative causing mass panic (e.g., "Fake Nuclear Leak", "False Riot Alarm").
   - "CONFIRMED SITUATION": A real but contained event (e.g., Traffic Blockade, Weather Alert).
   - "DEVELOPING NARRATIVE": Too much noise/unconfirmed data to judge yet.
3. **WRITE THE SUMMARY:**
   - If MISINFORMATION: Start with "DO NOT PANIC. This is a FALSE ALARM." Explain the origin of the lie.
   - If REAL EMERGENCY: Start with "URGENT SAFETY ALERT." Give the confirmed status.

OUTPUT REQUIREMENT:
Return a single, valid JSON object with this exact structure:
{{
  "verdict_status": "CATASTROPHIC EMERGENCY" | "LETHAL MISINFORMATION" | "CONFIRMED SITUATION" | "DEVELOPING NARRATIVE",
  "verdict_summary": "Detailed strategic summary for the public..."
}}
"""

# --- Helper ---

def _clean_json_text(raw_text: str) -> str:
    """Helper to strip Markdown code blocks often returned by LLMs."""
    cleaned = re.sub(r"^```json\s*", "", raw_text, flags=re.MULTILINE)
    cleaned = re.sub(r"^```\s*", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"\s*```$", "", cleaned, flags=re.MULTILINE)
    return cleaned.strip()

# --- Core Functions ---

async def synthesize_evidence(
    db: AsyncSession,
    claim: str,
    official: List[str],
    media: List[str],
    debunk: List[str],
    crisis_id: Optional[UUID] = None,
    adhoc_analysis_id: Optional[UUID] = None,
    timeline_item_id: Optional[UUID] = None,
    location: Optional[str] = "Unknown"
) -> Dict[str, Any]:
    """
    Aggregates evidence and synthesizes a verdict for a single claim.
    """
    logger.info(f"Synthesizing evidence for: '{claim}'")
    
    fmt_official = "\n".join([f"- {item}" for item in official]) if official else "No direct official confirmation found."
    fmt_media = "\n".join([f"- {item}" for item in media]) if media else "No relevant media reports found."
    fmt_debunk = "\n".join([f"- {item}" for item in debunk]) if debunk else "No prior fact-checks found."

    current_time_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    prompt = SYNTHESIS_PROMPT_TEMPLATE.format(
        current_time=current_time_str,
        claim=claim,
        official_evidence=fmt_official,
        media_evidence=fmt_media,
        debunk_evidence=fmt_debunk
    )

    try:
        model = genai.GenerativeModel(settings.GEMINI_SYNTHESIS_MODEL)
        response = await model.generate_content_async(
            prompt,
            generation_config={"response_mime_type": "application/json", "temperature": 0.1},
            safety_settings={
                HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_ONLY_HIGH,
                HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_ONLY_HIGH,
            }
        )

        raw_output = response.text
        try:
            result = json.loads(_clean_json_text(raw_output))
        except json.JSONDecodeError:
            result = {
                "status": "UNCONFIRMED",
                "summary": "System error during verification synthesis.",
                "sources": []
            }

        # --- BRANCHING LOGIC ---
        if adhoc_analysis_id:
            await crud.update_adhoc_analysis(db, adhoc_analysis_id, status="COMPLETED", verdict=result)
            return result

        if timeline_item_id:
            # Update existing item (Re-verification)
            await crud.update_timeline_item(
                db=db,
                item_id=timeline_item_id,
                status=result.get("status", "UNCONFIRMED"),
                summary=result.get("summary", ""),
                sources=result.get("sources", [])
            )
            logger.info(f"Updated existing TimelineItem {timeline_item_id} with status {result.get('status')}")
            return result

        if crisis_id:
            # Create new timeline item with location
            await crud.create_timeline_item(
                db=db,
                crisis_id=crisis_id,
                claim_text=claim,
                summary=result.get("summary", ""),
                status=result.get("status", "UNCONFIRMED"),
                sources=result.get("sources", []),
                location=location
            )
            return result

    except Exception as e:
        logger.error(f"Error in Synthesizer: {e}")
        if adhoc_analysis_id:
            await crud.update_adhoc_analysis(db, adhoc_analysis_id, status="FAILED")
        return {"status": "UNCONFIRMED", "summary": "Internal Error", "sources": []}

async def synthesize_crisis_conclusion(db: AsyncSession, crisis_id: UUID):
    """
    Aggregates ALL timeline items for a crisis and generates a MASTER CONCLUSION.
    Called incrementally whenever new info is added.
    """
    try:
        # 1. Fetch Data
        crisis = await crud.get_crisis(db, crisis_id)
        items = await crud.get_timeline_items(db, crisis_id)
        
        if not crisis or not items:
            return

        # 2. Segregate Items for Context
        verified = [f"- {i.claim_text}: {i.summary}" for i in items if i.status == "VERIFIED"]
        debunked = [f"- {i.claim_text}: {i.summary}" for i in items if i.status == "DEBUNKED"]
        unconfirmed = [f"- {i.claim_text}" for i in items if i.status == "UNCONFIRMED"]

        # Inject Current Time for accurate Live Reporting
        current_time_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        # 3. Construct Prompt
        prompt = CRISIS_CONCLUSION_PROMPT.format(
            current_time=current_time_str,
            crisis_name=crisis.name,
            verified_items="\n".join(verified) if verified else "None yet.",
            debunked_items="\n".join(debunked) if debunked else "None yet.",
            unconfirmed_items="\n".join(unconfirmed) if unconfirmed else "None yet."
        )

        # 4. Call LLM
        model = genai.GenerativeModel(settings.GEMINI_SYNTHESIS_MODEL)
        response = await model.generate_content_async(
            prompt,
            generation_config={"response_mime_type": "application/json", "temperature": 0.2}
        )
        
        data = json.loads(_clean_json_text(response.text))
        
        verdict_status = data.get("verdict_status", "DEVELOPING NARRATIVE")
        verdict_summary = data.get("verdict_summary", "Analysis ongoing.")

        # 5. Update Database
        logger.info(f"[Conclusion] Updated Live Verdict for {crisis.name}: {verdict_status}")
        await crud.update_crisis_verdict(db, crisis_id, verdict_status, verdict_summary)

    except Exception as e:
        logger.error(f"[Conclusion] Failed to generate crisis conclusion: {e}")