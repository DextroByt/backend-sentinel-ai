import json
import google.generativeai as genai
from app.core.config import get_settings
from app.db.models import VerificationStatusEnum

settings = get_settings()

# Configure Gemini
genai.configure(api_key=settings.GEMINI_API_KEY)
model = genai.GenerativeModel(settings.GEMINI_SYNTHESIS_MODEL)

async def synthesize_verdict(claim_text: str, evidence: dict) -> dict:
    """
    Decides the final verdict based on evidence from agents.
    Ref: Blueprint Section 3.3
    """
    
    prompt = f"""
    You are the Chief Verification Officer for Sentinel AI. 
    Analyze this claim based ONLY on the provided evidence.

    CLAIM: "{claim_text}"

    EVIDENCE:
    1. Official Sources: {evidence.get('official', 'None')}
    2. Media Reports: {evidence.get('media', 'None')}
    3. Fact Checks: {evidence.get('debunk', 'None')}

    RULES:
    - Return "VERIFIED" ONLY if official sources confirm it.
    - Return "DEBUNKED" if official sources deny it OR a fact-check refutes it.
    - Return "UNCONFIRMED" if evidence is weak or conflicting.

    OUTPUT FORMAT (JSON ONLY):
    {{
        "status": "VERIFIED" | "DEBUNKED" | "UNCONFIRMED",
        "summary": "1-2 sentence explanation.",
        "sources": [{{"url": "...", "source_name": "...", "type": "official"}}]
    }}
    """

    try:
        response = model.generate_content(prompt)
        # Clean up code blocks if Gemini adds them
        clean_json = response.text.replace("```json", "").replace("```", "")
        return json.loads(clean_json)
    except Exception as e:
        print(f"LLM Error: {e}")
        # Fallback for safety
        return {
            "status": "UNCONFIRMED", 
            "summary": "AI could not process evidence.", 
            "sources": []
        }