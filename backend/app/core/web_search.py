"""
Web search verification source using Tavily API.

Why Tavily (not Serper)?
━━━━━━━━━━━━━━━━━━━━━━━
Our NLI model needs ACTUAL CONTENT to classify entailment/contradiction.

| Feature              | Tavily                        | Serper                      |
|─────────────────────|──────────────────────────────|─────────────────────────────|
| Content returned    | Full page text (cleaned)      | SERP snippets (~160 chars)  |
| AI-optimized        | Yes (built for RAG/LLM)       | No (raw Google SERP)        |
| NLI effectiveness   | ✅ Long evidence = accurate   | ❌ Too short for NLI        |
| Source URLs          | ✅ Always included            | ✅ Always included          |
| Free tier           | 1,000 API credits             | 2,500 queries               |

Serper returns Google search snippets (~160 chars) — way too short for our
DeBERTa NLI model to make accurate entailment/contradiction judgments.
Tavily returns actual page content, cleaned and structured for AI consumption.

Free tier: 1,000 API credits at https://tavily.com
"""

import logging
from typing import Optional

import httpx

from app.config import get_settings
from app.models.detect import EvidencePiece, SourceType

logger = logging.getLogger(__name__)


class WebSearcher:
    """Tavily-powered web search for claim verification."""

    def __init__(self):
        settings = get_settings()
        self.api_key = settings.tavily_api_key
        self.enabled = settings.web_search_enabled

        # Persistent HTTP connection pool — avoids opening a new TCP connection
        # for every Tavily query (which was causing rate-limit spikes and latency
        # when many claims were verified in parallel).
        self._http = httpx.AsyncClient(
            timeout=20.0,
            verify=False,  # also needed for direct URL fetches of citation links
            headers={"User-Agent": "Mozilla/5.0 AI-Verifier/1.0"},
        )

        if self.api_key:
            logger.info("Web search: Tavily configured")
        else:
            logger.warning(
                "Tavily API key not set — web search disabled. "
            )

    @property
    def client(self):
        """Returns truthy if Tavily is configured."""
        return self.api_key is not None

    async def search_for_claim(
        self,
        claim_text: str,
        search_queries: Optional[list[str]] = None,
        max_results: int = 5,
    ) -> list[EvidencePiece]:
        """
        Search the web for evidence related to a claim.

        Args:
            claim_text: The claim to verify.
            search_queries: Optional pre-generated search queries from claim extractor.
            max_results: Maximum number of results to return.

        Returns:
            List of EvidencePiece objects with source URLs and content snippets.
        """
        if not self.api_key or not self.enabled:
            return []

        queries = search_queries if search_queries else [claim_text]

        all_evidence = []
        seen_urls = set()

        for query in queries[:2]:  # Limit to 2 queries per claim for speed
            try:
                response = await self._http.post(
                    "https://api.tavily.com/search",
                    json={
                        "api_key": self.api_key,
                        "query": query,
                        "search_depth": "basic",
                        "max_results": max_results,
                        "include_raw_content": False,
                    },
                )
                response.raise_for_status()
                data = response.json()
                try:
                    from app.core.provider_usage import record_provider_call
                    await record_provider_call("tavily")
                except Exception:
                    pass

                for result in data.get("results", []):
                    url = result.get("url", "")
                    if url in seen_urls:
                        continue
                    seen_urls.add(url)

                    evidence = EvidencePiece(
                        source_type=SourceType.WEB_SEARCH,
                        source_url=url,
                        source_title=result.get("title", ""),
                        snippet=result.get("content", "")[:1000],
                    )
                    all_evidence.append(evidence)

            except Exception as e:
                logger.error(f"Tavily search failed for query '{query}': {e}")
                continue

        logger.info(f"Web search returned {len(all_evidence)} evidence pieces")
        return all_evidence[:max_results]

    async def fetch_url_content(self, url: str) -> Optional[EvidencePiece]:
        """Directly fetch a URL using HTTPX and strip HTML tags."""
        import re
        try:
            response = await self._http.get(url)
            response.raise_for_status()
            text = response.text

            # Rudimentary HTML strip
            clean_text = re.sub(r'<style.*?>.*?</style>', '', text, flags=re.IGNORECASE|re.DOTALL)
            clean_text = re.sub(r'<script.*?>.*?</script>', '', clean_text, flags=re.IGNORECASE|re.DOTALL)
            clean_text = re.sub(r'<[^>]+>', ' ', clean_text).strip()
            clean_text = re.sub(r'\s+', ' ', clean_text)

            if len(clean_text) < 50:
                return None

            return EvidencePiece(
                source_type=SourceType.WEB_SEARCH,
                source_url=url,
                source_title=url.split("//")[-1].split("/")[0],
                snippet=clean_text[:5000],  # Keep first 5000 chars for NLI
            )
        except Exception as e:
            logger.warning(f"Direct URL fetch failed for {url}: {e}")
            return None


# ── Module-level singleton ────────────────────────────────────────────────

_searcher: Optional[WebSearcher] = None


def get_web_searcher() -> WebSearcher:
    """Get or create the web searcher singleton."""
    global _searcher
    if _searcher is None:
        _searcher = WebSearcher()
    return _searcher
