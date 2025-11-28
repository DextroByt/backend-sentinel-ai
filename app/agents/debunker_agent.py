import httpx
from bs4 import BeautifulSoup
from typing import Dict, List, Set
from app.agents.state import VerificationState, Evidence
from app.core.config import settings
from app.core.logger import setup_logger

logger = setup_logger("agent.debunker")

class DebunkerAgent:
    def __init__(self):
        self.headers = {"User-Agent": "SentinelAI/1.0 (Fact Check Scraper)"}

    def _get_jaccard_similarity(self, str1: str, str2: str) -> float:
        """
        Calculates Jaccard similarity coefficient.
        Intersection over Union of word sets[cite: 105, 106].
        """
        a = set(str1.lower().split())
        b = set(str2.lower().split())
        
        intersection = len(a.intersection(b))
        union = len(a.union(b))
        
        return intersection / union if union > 0 else 0.0

    async def _scrape_fact_check_site(self, domain: str, claim: str) -> List[Evidence]:
        """
        Custom scrapers tailored to specific HTML structures[cite: 104].
        """
        found = []
        # In production, this would use a site-specific search URL or Google Custom Search
        # For the blueprint, we simulate the scraping logic on a hypothetical search results page.
        search_url = f"https://{domain}/?s={claim.replace(' ', '+')}"
        
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(search_url, headers=self.headers)
                soup = BeautifulSoup(response.content, "html.parser")
                
                # Targeting specific tags as per blueprint [cite: 104]
                # E.g., <h2 class="entry-title"> for WordPress based sites like AltNews
                articles = soup.select("h2.entry-title a")
                
                for art in articles:
                    title = art.get_text()
                    url = art.get('href')
                    
                    # Fuzzy Matching 
                    score = self._get_jaccard_similarity(claim, title)
                    
                    # Threshold check [cite: 107]
                    if score >= settings.DEBUNK_SIMILARITY_THRESHOLD:
                        found.append({
                            "source_url": url,
                            "title": title,
                            "snippet": "Explicit fact-check article found via fuzzy matching.",
                            "published_date": None,
                            "confidence_score": score
                        })
        except Exception as e:
            logger.warning(f"Could not scrape {domain}: {e}")
            
        return found

    async def run(self, state: VerificationState) -> Dict[str, List[Evidence]]:
        logger.info(f"Debunker scanning for: {state['claim_text']}")
        
        all_evidence = []
        for domain in settings.FACT_CHECK_DOMAINS:
            site_evidence = await self._scrape_fact_check_site(domain, state['claim_text'])
            all_evidence.extend(site_evidence)
            
        return {"debunk_evidence": all_evidence}