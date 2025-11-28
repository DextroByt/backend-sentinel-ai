import httpx
from bs4 import BeautifulSoup
from typing import List
from app.agents.state import VerificationState, Evidence
from app.core.config import settings
from app.core.logger import setup_logger

logger = setup_logger("agent.official")

class OfficialCheckerAgent:
    def __init__(self):
        self.headers = {"User-Agent": "SentinelAI/1.0 (Crisis Verification Bot)"}
        # Common stop words to strip [cite: 92]
        self.stop_words = {"the", "is", "at", "which", "on", "in", "a", "an", "and", "or", "of", "to"}

    def _extract_keywords(self, text: str) -> set[str]:
        """Strips stop words to isolate unique entities (e.g., 'bridge', 'collapsed')."""
        words = text.lower().replace(".", "").replace(",", "").split()
        return {w for w in words if w not in self.stop_words}

    async def _scrape_and_match(self, url: str, claim_keywords: set[str]) -> float:
        """Fetches page content and calculates intersection match ratio."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(url, headers=self.headers)
                if response.status_code != 200:
                    return 0.0
                
                soup = BeautifulSoup(response.content, "html.parser")
                page_text = soup.get_text().lower()
                
                # Check for keyword presence
                matches = [k for k in claim_keywords if k in page_text]
                match_count = len(matches)
                total_keywords = len(claim_keywords)

                # Heuristic: >50% keywords OR >3 strong matches [cite: 94]
                if total_keywords > 0 and (match_count / total_keywords > 0.5):
                    return 1.0
                if match_count >= 3:
                    return 0.8
                return 0.0
                
        except Exception as e:
            logger.error(f"Failed to scrape {url}: {str(e)}")
            return 0.0

    async def run(self, state: VerificationState) -> Dict[str, List[Evidence]]:
        """
        Orchestration method for LangGraph.
        """
        logger.info(f"Official Agent scanning for: {state['claim_text']}")
        claim_keywords = self._extract_keywords(state['claim_text'])
        found_evidence = []

        # In a real scenario, we would use Google Custom Search API restricted 
        # to settings.OFFICIAL_DOMAINS to find specific URLs first. 
        # For this blueprint, we simulate scanning known endpoints or results.
        
        # Placeholder: Assume we found potential URLs via a search tool
        potential_urls = [f"https://{domain}/press-releases" for domain in settings.OFFICIAL_DOMAINS]

        for url in potential_urls:
            score = await self._scrape_and_match(url, claim_keywords)
            if score > 0.6:
                found_evidence.append({
                    "source_url": url,
                    "title": "Official Government Source",
                    "snippet": "Matched keywords on official domain.",
                    "published_date": None,
                    "confidence_score": score
                })

        return {"official_evidence": found_evidence}