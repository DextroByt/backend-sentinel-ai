import httpx
from datetime import datetime, timedelta
from typing import Dict, List
from app.agents.state import VerificationState, Evidence
from app.core.config import settings
from app.core.logger import setup_logger

logger = setup_logger("agent.media")

class MediaCrossReferencerAgent:
    def __init__(self):
        self.api_key = settings.NEWS_API_KEY
        self.base_url = "https://newsapi.org/v2/everything"

    def _generate_boolean_query(self, claim: str) -> str:
        """
        Converts 'Andheri Bridge Collapse' -> 'Andheri AND Bridge AND Collapse'
        to optimize search relevance[cite: 98].
        """
        # Remove special chars and split
        words = [w for w in claim.split() if w.isalnum()]
        return " AND ".join(words)

    async def run(self, state: VerificationState) -> Dict[str, List[Evidence]]:
        query = self._generate_boolean_query(state['claim_text'])
        # Recency Filtering: Last 7 days [cite: 99]
        from_date = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
        
        params = {
            "q": query,
            "from": from_date,
            "sortBy": "relevance",
            "language": "en",
            "apiKey": self.api_key
        }

        logger.info(f"Querying NewsAPI: {query}")
        
        evidence_list = []
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(self.base_url, params=params)
                data = response.json()

                if data.get("status") == "ok":
                    articles = data.get("articles", [])
                    # Deduplication logic implicitly handled by set of URLs if needed
                    # but NewsAPI usually handles this well.
                    
                    for art in articles[:5]: # Top 5 results
                        evidence_list.append({
                            "source_url": art.get("url"),
                            "title": art.get("title"),
                            "snippet": art.get("description") or "",
                            "published_date": art.get("publishedAt"),
                            "confidence_score": 0.9 # High confidence if found in top results
                        })
        except Exception as e:
            logger.error(f"NewsAPI Error: {e}")

        return {"media_evidence": evidence_list}