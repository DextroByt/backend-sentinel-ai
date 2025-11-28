import logging
import asyncio
import json
from datetime import datetime, timezone
from typing import List, TypedDict, Optional, Any
from uuid import UUID
from langgraph.graph import StateGraph, END, START
from sqlalchemy.ext.asyncio import AsyncSession
import google.generativeai as genai

from app.core.config import settings
# Import Agents
from app.agents import official_checker_agent, media_cross_referencer, debunker_agent
from app.services import synthesizer_service
from app.db import crud

logger = logging.getLogger(__name__)

# --- Configuration ---
MAX_RETRIES = 1 # How many times to self-correct before giving up (Prevents infinite loops)

# Configure Gemini for "Self-Reasoning"
try:
    genai.configure(api_key=settings.GEMINI_API_KEY)
except Exception as e:
    logger.error(f"Failed to configure Gemini API: {e}")

class VerificationState(TypedDict):
    """
    The shared memory for the Agentic Workflow.
    Now includes 'retry_count' and 'search_query' for adaptive behavior.
    """
    claim_text: str         # The original claim
    current_query: str      # The actual query being used (can change!)
    location: Optional[str]
    
    crisis_id: Optional[UUID] 
    adhoc_analysis_id: Optional[UUID] 
    timeline_item_id: Optional[UUID] 
    db_session: Any 
    
    # Evidence gathered
    official_evidence: List[str]
    media_evidence: List[str]
    debunk_evidence: List[str]
    
    # Meta-Cognition
    retry_count: int
    status: str # 'PROCESSING', 'COMPLETE', 'FAILED'

# --- PROMPTS FOR SELF-CORRECTION ---

QUERY_REFINEMENT_PROMPT = """
You are a Search Strategy Expert. The previous search for this rumor yielded NO RESULTS.
Original Claim: "{claim}"
Original Location context: "{location}"

Your goal is to generate a BROAD, KEYWORD-BASED search query to find *any* trace of this event.
RULES:
1. Remove specific numbers (e.g., change "15 dead" to "dead" or remove it).
2. Remove date specifics if they might be wrong.
3. Focus on the *Core Event* (e.g., "Bridge Collapse Mumbai").
4. Return ONLY the new query string. Nothing else.
"""

# --- AGENT NODES ---

async def node_official_checker(state: VerificationState):
    """Checks Gov/Official channels."""
    query = state.get("current_query", state["claim_text"])
    try:
        # Official agent is strict, so we pass the location if possible
        evidence = await official_checker_agent.check_sources(query)
        return {"official_evidence": evidence}
    except Exception as e:
        logger.error(f"[Orchestrator] Official Agent Error: {e}")
        return {"official_evidence": []}

async def node_media_cross_referencer(state: VerificationState):
    """Checks Global/Local News."""
    query = state.get("current_query", state["claim_text"])
    try:
        evidence = await media_cross_referencer.check_media(query)
        return {"media_evidence": evidence}
    except Exception as e:
        logger.error(f"[Orchestrator] Media Agent Error: {e}")
        return {"media_evidence": []}

async def node_debunker(state: VerificationState):
    """Checks Fact-Check databases."""
    query = state.get("current_query", state["claim_text"])
    try:
        evidence = await debunker_agent.find_debunks(query)
        return {"debunk_evidence": evidence}
    except Exception as e:
        logger.error(f"[Orchestrator] Debunker Agent Error: {e}")
        return {"debunk_evidence": []}

async def node_assessor(state: VerificationState):
    """
    CRITICAL STEP: The Agent 'Reflects' on its findings.
    If evidence is empty, it triggers a Retry with a better query.
    """
    off = state.get("official_evidence", [])
    med = state.get("media_evidence", [])
    deb = state.get("debunk_evidence", [])
    
    # Filter out "No result" placeholders from agents
    real_off = [x for x in off if "No " not in x[:10]]
    real_med = [x for x in med if "No " not in x[:10]]
    real_deb = [x for x in deb if "No " not in x[:10]]
    
    total_hits = len(real_off) + len(real_med) + len(real_deb)
    
    logger.info(f"[Orchestrator] Assessment: Found {total_hits} pieces of evidence on Try #{state['retry_count']}")

    if total_hits == 0 and state["retry_count"] < MAX_RETRIES:
        # trigger refinement
        return {"status": "NEEDS_REFINEMENT"}
    
    return {"status": "READY_TO_SYNTHESIZE"}

async def node_query_refiner(state: VerificationState):
    """
    Uses LLM to rewrite the search query for better results.
    """
    logger.info(f"[Orchestrator] 🧠 Self-Correcting: Refining search query for '{state['claim_text']}'")
    
    try:
        model = genai.GenerativeModel("gemini-2.5-flash")
        prompt = QUERY_REFINEMENT_PROMPT.format(
            claim=state["claim_text"], 
            location=state.get("location", "Unknown")
        )
        response = await model.generate_content_async(prompt)
        new_query = response.text.strip().replace('"', '')
        
        logger.info(f"[Orchestrator] 🔄 New Query Strategy: '{new_query}'")
        
        return {
            "current_query": new_query,
            "retry_count": state["retry_count"] + 1,
            # Clear previous empty evidence to avoid pollution
            "official_evidence": [],
            "media_evidence": [],
            "debunk_evidence": []
        }
    except Exception as e:
        logger.error(f"[Orchestrator] Refiner Failed: {e}")
        return {"retry_count": state["retry_count"] + 1} # Just increment to break loop

async def node_synthesizer(state: VerificationState):
    """Final Verdict Generation."""
    try:
        await synthesizer_service.synthesize_evidence(
            db=state["db_session"],
            claim=state["claim_text"],
            official=state.get("official_evidence", []),
            media=state.get("media_evidence", []),
            debunk=state.get("debunk_evidence", []),
            crisis_id=state.get("crisis_id"),
            adhoc_analysis_id=state.get("adhoc_analysis_id"),
            timeline_item_id=state.get("timeline_item_id"),
            location=state.get("location", "Unknown")
        )
    except Exception as e:
        logger.error(f"[Orchestrator] Synthesizer Failed: {e}")
    return state

# --- LOGIC FLOW (ROUTER) ---

def router_logic(state: VerificationState):
    """Decides where to go after Assessment."""
    if state["status"] == "NEEDS_REFINEMENT":
        return "refine"
    return "synthesize"

# --- GRAPH CONSTRUCTION ---

workflow = StateGraph(VerificationState)

# 1. Define Nodes
workflow.add_node("official", node_official_checker)
workflow.add_node("media", node_media_cross_referencer)
workflow.add_node("debunker", node_debunker)
workflow.add_node("assessor", node_assessor)
workflow.add_node("refiner", node_query_refiner)
workflow.add_node("synthesizer", node_synthesizer)

# 2. Define Initial Flow (Parallel)
workflow.add_edge(START, "official")
workflow.add_edge(START, "media")
workflow.add_edge(START, "debunker")

# 3. Converge on Assessor
workflow.add_edge("official", "assessor")
workflow.add_edge("media", "assessor")
workflow.add_edge("debunker", "assessor")

# 4. Conditional Branching (The "Self-Correcting" Loop)
workflow.add_conditional_edges(
    "assessor",
    router_logic,
    {
        "refine": "refiner",
        "synthesize": "synthesizer"
    }
)

# 5. Loop back from Refiner to Agents
workflow.add_edge("refiner", "official")
workflow.add_edge("refiner", "media")
workflow.add_edge("refiner", "debunker")

# 6. End
workflow.add_edge("synthesizer", END)

# Compile
app = workflow.compile()

# --- ENTRY POINT ---

async def run_verification_pipeline(
    db_session: AsyncSession, 
    claim_text: str, 
    crisis_id: Optional[UUID] = None,
    adhoc_analysis_id: Optional[UUID] = None,
    timeline_item_id: Optional[UUID] = None,
    location: Optional[str] = "Unknown"
):
    """
    Master Function: Triggers the Agentic Workflow.
    """
    start_time = datetime.now(timezone.utc)
    logger.info(f"[{start_time.isoformat()}] 🛡️ Pipeline Activated: '{claim_text}'")
    
    if adhoc_analysis_id:
        await crud.update_adhoc_analysis(db_session, adhoc_analysis_id, "PROCESSING")

    # Initialize State with retry_count = 0
    inputs = {
        "claim_text": claim_text,
        "current_query": f"{claim_text} {location if location != 'Unknown' else ''}", # Initial naive query
        "location": location,
        "crisis_id": crisis_id,
        "adhoc_analysis_id": adhoc_analysis_id,
        "timeline_item_id": timeline_item_id,
        "db_session": db_session,
        "official_evidence": [],
        "media_evidence": [],
        "debunk_evidence": [],
        "retry_count": 0,
        "status": "STARTING"
    }

    try:
        # Execute Graph
        await app.ainvoke(inputs)
        
        # Trigger Live Update if part of a Crisis
        if crisis_id:
            try:
                await synthesizer_service.synthesize_crisis_conclusion(db_session, crisis_id)
            except Exception as e:
                logger.error(f"[Real-Time] Update Error: {e}")

    except Exception as e:
        logger.error(f"Pipeline Critical Failure: {e}")
        if adhoc_analysis_id:
             await crud.update_adhoc_analysis(db_session, adhoc_analysis_id, "FAILED")