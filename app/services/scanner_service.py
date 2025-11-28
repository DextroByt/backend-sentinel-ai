import asyncio
import logging
import json
import time
import re
from datetime import datetime, timezone
from typing import List, Dict
from sqlalchemy.ext.asyncio import AsyncSession
import google.generativeai as genai
from duckduckgo_search import DDGS 

from app.core.config import settings
from app.db import database, crud
from app.services import claim_extraction_service 
from app.services import verification_orchestrator
from app.services import rss_service
from app.services import synthesizer_service 
from app.schemas.schemas import VerificationStatus

logger = logging.getLogger(__name__)

# --- Configuration ---
DISCOVERY_KEYWORDS_REGEX = r"(disaster|accident|emergency|collapse|explosion|riot|earthquake|flood|tsunami|virus|outbreak|leak|bioweapon|conspiracy|coverup|censored|exposed|fake|hoax|rumor|forwarded|viral|whatsapp|audio|warning|alert|death|killed|lethal|radioactive|poison)"

# --- Cycle Timings ---
CYCLE_TOTAL_DURATION = 60 * 60        
DISCOVERY_WINDOW = 2 * 60             

# --- Concurrency ---
MAX_CONCURRENT_SCANS = 5 
HIGH_RISK_SCAN_INTERVAL = 120 

try:
    genai.configure(api_key=settings.GEMINI_API_KEY)
except Exception as e:
    logger.error(f"Failed to configure Gemini: {e}")


# --- PHASE 1: THREAT DISCOVERY ---

def filter_relevant_headlines(articles: List[Dict]) -> List[Dict]:
    relevant = []
    pattern = re.compile(DISCOVERY_KEYWORDS_REGEX, re.IGNORECASE)
    for art in articles:
        text_blob = f"{art.get('title', '')} {art.get('description', '')}"
        if pattern.search(text_blob):
            relevant.append(art)
    return relevant

def _perform_social_listening() -> List[Dict]:
    """
    Aggressive social scanning to fill the pipeline immediately.
    """
    social_queries = [
        '"forwarded as received" site:twitter.com',
        '"forward this message" site:whatsapp.com',
        '"media wont tell you" site:twitter.com',
        '"viral video" "shocking" site:facebook.com',
        '"leaked audio" warning site:youtube.com',
        '"government hiding" disaster site:reddit.com',
        '"urgent alert" site:instagram.com',
        '"don\'t go there" site:twitter.com',
        '"rumor has it" site:twitter.com',
        '"fake news" alert site:twitter.com'
    ]
    results = []
    try:
        with DDGS() as ddgs:
            for q in social_queries:
                hits = list(ddgs.text(q, region="wt-wt", safesearch="off", timelimit="d", max_results=5))
                for h in hits:
                    results.append({
                        "title": h.get('title', 'Social Rumor'),
                        "description": h.get('body', ''),
                        "url": h.get('href', ''),
                        "source": {"name": "Social Signal", "type": "SOCIAL"},
                        "published_at": "Just Now"
                    })
    except Exception as e:
        logger.warning(f"Social listening scan failed: {e}")
    return results

async def analyze_and_assess_threats(db: AsyncSession, articles: List[Dict]):
    """
    Analyzes headlines and creates Crisis entries.
    RETURNS: A list of newly created Crisis objects.
    """
    new_crises = [] # Track newly created items
    
    if not articles: return []
    articles = articles[:80] 

    headlines = []
    for a in articles:
        raw_desc = a.get('description', '')
        clean_desc = re.sub(r'<[^>]+>', '', raw_desc)
        source = a.get('source', {}).get('name', 'Unknown')
        headlines.append(f"- {a['title']} ({source}): {clean_desc[:100]}...")
        
    digest = "\n".join(headlines)
    current_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    prompt = f"""
    You are a Misinformation Threat Intelligence Agent.
    CURRENT DATE: {current_utc}
    
    Analyze these headlines. Identify POTENTIAL RUMORS AND CRISES.
    
    OUTPUT JSON FORMAT:
    [
      {{
        "name": "Short Title (e.g. 'Rumor: Bio-Leak in Hyderabad')",
        "description": "Summary of what the rumor/claim says.",
        "keywords": "viral, video, leak, virus",
        "severity": 85,
        "location": "City, Country"
      }}
    ]
    
    SEVERITY SCORING:
    - 90-100: LETHAL (Medical advice, riots, nuclear panic).
    - 70-89: DANGEROUS (Fake accidents, collapse rumors).
    - 50-69: DISRUPTIVE.
    
    HEADLINES:
    {digest}
    """

    try:
        model = genai.GenerativeModel(settings.GEMINI_EXTRACTION_MODEL)
        response = await model.generate_content_async(prompt)
        if not response.text: return []

        raw_text = response.text.strip().replace("```json", "").replace("```", "")
        try:
            crises_data = json.loads(raw_text)
        except json.JSONDecodeError:
            return []

        for c_data in crises_data:
            name = c_data.get("name")
            if not name: continue
            
            existing = await crud.get_crisis_by_fuzzy_name(db, name)
            if existing: continue 
            
            severity = int(c_data.get("severity", 50))
            loc = c_data.get("location", "Unknown Location")
            
            # PRINT to Console for Visibility
            print(f"🚨 [SCANNER] New Threat Detected: {name} ({loc})")
            logger.info(f"[Discovery] 🚨 NEW CANDIDATE: {name} ({loc})")
            
            new_crisis = await crud.create_crisis(
                db, name=name, description=c_data.get("description", ""),
                keywords=c_data.get("keywords", name), severity=severity, location=loc 
            )
            
            # Add to list for notification logic
            new_crises.append(new_crisis)

            await crud.create_timeline_item(
                db,
                crisis_id=new_crisis.id,
                claim_text=f"Signal Detected: {name}",
                summary=f"Sentinel AI picked up this signal from web chatter. Automated verification agents have been deployed.",
                status=VerificationStatus.UNCONFIRMED,
                sources=[{"title": "Sentinel Watchdog", "url": "#"}],
                location=loc
            )

            asyncio.create_task(
                _background_seed_timeline(new_crisis.id, c_data.get("description", name), loc)
            )
            
        return new_crises

    except Exception as e:
        logger.error(f"[Discovery] Threat analysis failed: {e}")
        return []

async def _background_seed_timeline(crisis_id, text, location):
    """Helper to run verification without blocking the main discovery loop."""
    async with database.AsyncSessionLocal() as db:
        await verification_orchestrator.run_verification_pipeline(
            db_session=db, 
            claim_text=text, 
            crisis_id=crisis_id, 
            location=location 
        )

async def perform_agentic_selection(db: AsyncSession):
    """
    THE BRAIN: Agent reviews ALL candidates and picks the Strategic Top 10.
    """
    print("🧠 [SCANNER] Agentic Brain is prioritizing active threats...")
    logger.info("[Agent Selection] 🧠 Reviewing all candidates for prioritization...")
    
    all_crises = await crud.get_crises(db, limit=100)
    if not all_crises: return

    candidates_text = "\n".join([f"ID: {c.id} | Name: {c.name} | Sev: {c.severity} | Loc: {c.location}" for c in all_crises])
    
    prompt = f"""
    You are the Crisis Supervisor for Sentinel AI.
    We have detected {len(all_crises)} potential threats.
    
    YOUR MISSION: Select exactly 10 items to track for the next hour.
    
    SELECTION CRITERIA:
    1. Select TOP 3 "CATASTROPHIC/REAL" events (Real disasters, verified attacks).
    2. Select TOP 7 "VIRAL RUMORS/MISINFORMATION" (Hoaxes, fake news, panic triggers).
    
    PRIORITIZE UNIQUE LOCATIONS AND HIGH SEVERITY.
    
    CANDIDATES:
    {candidates_text}
    
    OUTPUT JSON:
    {{
      "selected_ids": ["uuid-1", "uuid-2", ...]
    }}
    """
    
    try:
        model = genai.GenerativeModel(settings.GEMINI_EXTRACTION_MODEL)
        response = await model.generate_content_async(prompt)
        clean_json = response.text.strip().replace("```json", "").replace("```", "")
        selection = json.loads(clean_json)
        
        keep_ids = selection.get("selected_ids", [])
        
        if not keep_ids:
            await _fallback_pruning(db)
            return

        count_kept = 0
        count_del = 0
        for c in all_crises:
            if str(c.id) not in keep_ids:
                await db.delete(c)
                count_del += 1
            else:
                count_kept += 1
        
        await db.commit()
        logger.info(f"[Agent Selection] ✅ Brain Decision: Kept {count_kept} items. Deleted {count_del} irrelevant ones.")

    except Exception as e:
        logger.error(f"[Agent Selection] Failed: {e}. Using fallback.")
        await _fallback_pruning(db)

async def _fallback_pruning(db: AsyncSession):
    all_crises = await crud.get_crises(db, limit=100)
    all_crises.sort(key=lambda x: x.severity, reverse=True)
    keep = all_crises[:10]
    keep_ids = {c.id for c in keep}
    for c in all_crises:
        if c.id not in keep_ids: await db.delete(c)
    await db.commit()

async def run_discovery_phase(db: AsyncSession):
    logger.info(">>> PHASE 1: THREAT DISCOVERY (High Throughput) <<<")
    
    # --- CRITICAL FIX: rss_service.fetch_all_rss_feeds IS ALREADY ASYNC ---
    # Do NOT use asyncio.to_thread for async functions.
    rss_coro = rss_service.fetch_all_rss_feeds()
    
    # Social listening IS synchronous, so we use to_thread
    social_task = asyncio.to_thread(_perform_social_listening)
    
    # Run both concurrently
    results = await asyncio.gather(rss_coro, social_task)
    
    # results[0] is RSS list, results[1] is Social list
    all_items = results[0] + results[1] 
    
    print(f"🔍 [SCANNER] Scanned {len(all_items)} raw signals.")
    
    relevant = filter_relevant_headlines(all_items)
    
    if relevant:
        logger.info(f"[Discovery] Processing {len(relevant)} items...")
        return await analyze_and_assess_threats(db, relevant)
    else:
        logger.info("[Discovery] No signals found.")
        return []

# --- PHASE 2: DEEP GATHERING ---

def _perform_hybrid_search(keywords: str) -> List[Dict]:
    results = []
    try:
        with DDGS() as ddgs:
            news = list(ddgs.news(keywords, region="wt-wt", safesearch="off", timelimit="w", max_results=3))
            results.extend(news)
            web = list(ddgs.text(keywords, region="wt-wt", safesearch="off", timelimit="w", max_results=3))
            for w in web:
                results.append({"title": w['title'], "body": w['body'], "url": w['href']})
    except Exception: pass
    return results

async def process_single_crisis_task(crisis_id: str):
    async with database.AsyncSessionLocal() as db:
        try:
            crisis = await crud.get_crisis(db, crisis_id)
            if not crisis: return

            logger.info(f"[Deep Scan] 🚀 Worker: {crisis.name}")
            queries = [crisis.keywords, f"{crisis.keywords} viral hoax"]

            for q in queries:
                articles = await asyncio.to_thread(_perform_hybrid_search, q)
                if not articles: continue

                for art in articles:
                    text = f"{art.get('title','')} {art.get('body','')}"
                    claims_data = await claim_extraction_service.extract_claims(text)
                    
                    for claim_obj in claims_data:
                        claim_text = claim_obj["text"]
                        if await crud.get_timeline_item_by_claim_text(db, claim_text): continue
                        
                        await verification_orchestrator.run_verification_pipeline(
                            db_session=db, claim_text=claim_text, 
                            crisis_id=crisis.id, location=claim_obj["location"] 
                        )
            await synthesizer_service.synthesize_crisis_conclusion(db, crisis.id)
        except Exception as e: logger.error(f"Worker Error: {e}")

async def run_deep_gathering_phase(db: AsyncSession, duration_seconds: int):
    logger.info(f">>> PHASE 2: DEEP GATHERING ({duration_seconds/60:.1f}m) <<<")
    start_time = time.time()
    normal_queue_ptr = 0
    last_scan_times = {} 

    while time.time() < (start_time + duration_seconds):
        all_crises = await crud.get_crises(db, limit=20)
        if not all_crises:
            await asyncio.sleep(10); continue

        high_risk = [c for c in all_crises if c.severity >= 90]
        normal_risk = [c for c in all_crises if c.severity < 90]
        
        batch = []
        now = time.time()

        for c in high_risk:
            if (now - last_scan_times.get(c.id, 0)) > HIGH_RISK_SCAN_INTERVAL:
                batch.append(c)
        
        slots = MAX_CONCURRENT_SCANS - len(batch)
        if slots > 0 and normal_risk:
            for _ in range(slots):
                c = normal_risk[normal_queue_ptr % len(normal_risk)]
                if c not in batch: batch.append(c)
                normal_queue_ptr += 1

        if not batch:
            await asyncio.sleep(5); continue

        logger.info(f"[Deep Scan] ⚡ Batch: {len(batch)} items")
        tasks = [process_single_crisis_task(c.id) for c in batch]
        for c in batch: last_scan_times[c.id] = now
        
        await asyncio.gather(*tasks)
        await asyncio.sleep(2)

# --- MAIN LOOP ---

async def start_monitoring():
    print("\n--- Sentinel AI Supervisor Started (Autonomous Mode) ---\n")
    logger.info("--- Sentinel AI Supervisor Started (Autonomous Mode) ---")
    
    # [LOGIC] Track if this is the first cycle to send a Summary Notification
    is_first_run = True
    
    while True:
        cycle_start = time.time()
        
        # 1. DISCOVERY
        print(">>> STARTING DISCOVERY PHASE")
        logger.info(">>> STARTING DISCOVERY PHASE (2 Mins) <<<")
        
        new_threats = []
        
        async with database.AsyncSessionLocal() as db:
            try:
                # Run Discovery and capture newly created crises
                new_threats = await run_discovery_phase(db)
                
                # --- NOTIFICATION LOGIC ---
                if new_threats:
                    # Filter for High Severity threats (e.g. 75+)
                    high_sev_crises = [c for c in new_threats if c.severity >= 75]
                    
                    if high_sev_crises:
                        if is_first_run:
                            # STARTUP: Send ONE summary notification
                            high_sev_crises.sort(key=lambda x: x.severity, reverse=True)
                            top_picks = high_sev_crises[:3]
                            names = ", ".join([c.name for c in top_picks])
                            
                            msg = f"⚠ SYSTEM ONLINE: Initial Scan Complete. Detected {len(high_sev_crises)} Active Threats. Top Priority: {names}."
                            await crud.create_notification(db, content=msg, type="CATASTROPHIC_ALERT")
                            logger.info(f"[Notifications] Sent Startup Summary: {msg}")
                        
                        else:
                            # CONTINUOUS: Send individual alerts for new items
                            for c in high_sev_crises:
                                msg = f"🚨 NEW THREAT DETECTED: {c.name} (Severity: {c.severity}) detected in {c.location}."
                                await crud.create_notification(db, content=msg, type="CATASTROPHIC_ALERT", crisis_id=c.id)
                                logger.info(f"[Notifications] Sent Individual Alert for {c.name}")

            except Exception as e: 
                logger.error(f"Discovery Error: {e}")
                print(f"❌ Discovery Error: {e}")
        
        # Disable startup flag after first pass
        is_first_run = False
        
        elapsed = time.time() - cycle_start
        if elapsed < DISCOVERY_WINDOW:
            logger.info(f"[Supervisor] Waiting {DISCOVERY_WINDOW - elapsed:.0f}s for discovery window to close...")
            await asyncio.sleep(DISCOVERY_WINDOW - elapsed)

        # 2. AGENTIC SELECTION
        async with database.AsyncSessionLocal() as db:
            await perform_agentic_selection(db)

        # 3. DEEP GATHERING
        gather_len = CYCLE_TOTAL_DURATION - DISCOVERY_WINDOW - 30
        async with database.AsyncSessionLocal() as db:
             await run_deep_gathering_phase(db, duration_seconds=gather_len)
             await crud.delete_old_crises(db) 

        logger.info("[Cycle] Resetting...")
        print("🔄 [Cycle] Resetting...")
        await asyncio.sleep(5)