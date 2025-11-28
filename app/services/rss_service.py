import asyncio
import logging
import feedparser
import json
import os
import time
from datetime import datetime, timedelta, timezone
from typing import List, Dict
from duckduckgo_search import DDGS

logger = logging.getLogger(__name__)

# --- Configuration ---
RSS_FILE = "rss_feeds.json"
MAX_ARTICLE_AGE_HOURS = 48  # Extended to 48h to catch viral rumors that persist

# [UPDATED] THE "RUMOR MILL" LIST
# Instead of just "News", we now target Fact-Checkers who document viral lies.
DEFAULT_RSS_FEEDS = [
    # --- INDIA SPECIFIC (High Volume Misinfo) ---
    "https://www.altnews.in/feed/",                 # Premier India Fact Check
    "https://www.boomlive.in/feed/",                # Verified IFCN Signatory
    "https://newschecker.in/feed/",                 # Regional India Misinfo
    "https://factly.in/feed/",                      # Data-backed debunking
    "https://www.indiatoday.in/rss/1206550",        # India Today Fact Check
    "https://vishvasnews.com/feed/",                # Hindi/English Fact Check

    # --- GLOBAL / VIRAL HOAXES ---
    "https://factcheck.afp.com/rss/all",            # AFP Global
    "https://www.snopes.com/feed/",                 # classic urban legends
    "https://checkyourfact.com/feed/",              # Political/Viral
    "https://healthfeedback.org/feed/",             # MEDICAL/LETHAL Misinfo (Critical)
    "https://fullfact.org/feed/all/",               # UK/Global
    
    # --- REAL-TIME DISASTER ALERTS (Control Group) ---
    # We keep these to distinguish Real vs Fake
    "https://sachet.ndma.gov.in/CapFeed/rss/all",   
    "https://www.gdacs.org/xml/rss.xml"
]

# --- Persistence Logic ---

def _load_feeds() -> List[str]:
    """Loads the feed list from disk or returns default."""
    if os.path.exists(RSS_FILE):
        try:
            with open(RSS_FILE, "r") as f:
                data = json.load(f)
                # Merge defaults to ensure we always have the Truth Squad
                return list(set(data.get("feeds", []) + DEFAULT_RSS_FEEDS))
        except Exception as e:
            logger.error(f"Error loading RSS file: {e}")
            return DEFAULT_RSS_FEEDS
    return DEFAULT_RSS_FEEDS

def _save_feeds(feeds: List[str]):
    try:
        with open(RSS_FILE, "w") as f:
            json.dump({"feeds": list(set(feeds)), "updated_at": time.time()}, f, indent=2)
    except Exception as e:
        logger.error(f"Error saving RSS file: {e}")

# --- Discovery & Maintenance ---

def _verify_feed(url: str) -> bool:
    try:
        d = feedparser.parse(url)
        if d.bozo == 0 and (len(d.entries) > 0 or d.feed.get('title')):
            return True
        if len(d.entries) > 0: 
            return True
    except:
        pass
    return False

def discover_new_feeds() -> List[str]:
    """
    Uses DDGS to find NEW sources of debunking.
    """
    logger.info("🔎 Searching for new Misinformation/Fact-Check RSS feeds...")
    potential_urls = []
    queries = [
        "fact check rss feed india",
        "misinformation alert rss xml",
        "viral hoax buster feed",
        "medical myth buster rss"
    ]
    
    try:
        with DDGS() as ddgs:
            for q in queries:
                results = ddgs.text(q, max_results=5)
                for r in results:
                    url = r.get('href', '')
                    if any(x in url for x in ['.rss', '.xml', '/feed', '/rss']):
                        potential_urls.append(url)
    except Exception as e:
        logger.error(f"Feed discovery failed: {e}")

    new_verified = []
    for url in set(potential_urls):
        if _verify_feed(url):
            new_verified.append(url)
            
    return new_verified

async def manage_feeds_daily():
    logger.info("🛠️ Starting Daily Feed Maintenance...")
    current_feeds = _load_feeds()
    valid_feeds = []
    loop = asyncio.get_event_loop()
    
    for feed in current_feeds:
        is_valid = await loop.run_in_executor(None, _verify_feed, feed)
        if is_valid:
            valid_feeds.append(feed)
    
    new_feeds = await loop.run_in_executor(None, discover_new_feeds)
    if new_feeds:
        valid_feeds.extend(new_feeds)
        
    _save_feeds(valid_feeds)
    logger.info(f"📝 Feed list updated. Total sources: {len(valid_feeds)}")

# --- Fetching Logic ---

def is_article_fresh(entry) -> bool:
    try:
        struct_time = entry.get('published_parsed') or entry.get('updated_parsed')
        if not struct_time: return False 
        pub_date = datetime.fromtimestamp(time.mktime(struct_time), tz=timezone.utc)
        now = datetime.now(timezone.utc)
        age = now - pub_date
        
        if age > timedelta(hours=MAX_ARTICLE_AGE_HOURS): return False
        if age < timedelta(minutes=-10): return False # Future dates check
        return True
    except Exception:
        return False

def _parse_single_feed(url: str) -> List[Dict]:
    """Worker to parse a single feed."""
    articles = []
    try:
        feed = feedparser.parse(url)
        source_name = feed.feed.get('title', 'Unknown Source')
        
        # Tag Logic: If it's a known Fact Checker, mark it!
        is_fact_checker = any(x in url for x in ['altnews', 'boomlive', 'fact', 'snopes', 'check'])
        source_type = "FACT_CHECKER" if is_fact_checker else "NEWS"

        for entry in feed.entries:
            if not is_article_fresh(entry):
                continue

            pub_date_str = "Recent"
            if hasattr(entry, 'published'): pub_date_str = entry.published
            elif hasattr(entry, 'updated'): pub_date_str = entry.updated
            
            articles.append({
                "title": entry.get('title', 'No Title'),
                "description": entry.get('summary') or entry.get('description') or '',
                "url": entry.get('link', ''),
                "source": {"name": source_name, "type": source_type}, # Pass type for Agent logic
                "published_at": pub_date_str
            })
            
            if len(articles) >= 15: break

    except Exception as e:
        logger.warning(f"Error reading feed {url}: {e}")
    return articles

async def fetch_all_rss_feeds() -> List[Dict]:
    feeds = _load_feeds()
    loop = asyncio.get_event_loop()
    tasks = [loop.run_in_executor(None, _parse_single_feed, url) for url in feeds]
    results = await asyncio.gather(*tasks)
    
    all_articles = []
    for res in results:
        all_articles.extend(res)
        
    # Deduplicate
    seen_urls = set()
    unique = []
    for art in all_articles:
        if art['url'] not in seen_urls:
            seen_urls.add(art['url'])
            unique.append(art)
            
    return unique