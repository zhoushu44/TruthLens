"""
Domain Source Router — V2 Pipeline Stage 2.

Routes claims to domain-specific free API sources + filtered Tavily/Serper search.
Each claim's domain determines which trusted sources are queried for evidence.

Free API Sources:
- Wikipedia (MediaWiki Action API) — general factual, historical
- Wikidata (REST API) — structured factual data
- Google Fact Check Tools API — fact-checked claims
- arXiv API — scientific/technical papers
- Semantic Scholar API — academic papers (unauthenticated)
- Crossref API — academic metadata/DOIs
- PubMed NCBI E-Utilities — medical/health literature (unauthenticated)
- SEC EDGAR API — financial filings
- World Bank API — economic/statistical data
- GNews — news/current events
- Tavily — domain-filtered web search (include_domains)
- Serper — domain-filtered Google search (site: operator)
"""

import asyncio
import logging
import re
from typing import Optional

import httpx

from app.config import get_settings
from app.models.detect import (
    EvidencePiece, SourceType, SourceTier, ClaimDomain, ExtractedClaim,
)

logger = logging.getLogger(__name__)


# ── Trusted Domain Allowlists ─────────────────────────────────────────────

DOMAIN_ALLOWLISTS: dict[str, list[str]] = {
    "general_factual": [
        "en.wikipedia.org", "www.britannica.com", "www.snopes.com",
        "www.factcheck.org", "www.politifact.com", "www.bbc.com",
        "www.reuters.com", "apnews.com", "www.nationalgeographic.com",
        "education.nationalgeographic.org", "www.smithsonianmag.com",
    ],
    "scientific_technical": [
        "arxiv.org", "scholar.google.com", "www.nature.com",
        "www.science.org", "www.sciencedirect.com", "ieeexplore.ieee.org",
        "dl.acm.org", "pubmed.ncbi.nlm.nih.gov", "en.wikipedia.org",
        "www.britannica.com", "pubs.acs.org", "link.springer.com",
        "www.pnas.org", "www.cell.com", "journals.plos.org",
    ],
    "medical_health": [
        "pubmed.ncbi.nlm.nih.gov", "www.ncbi.nlm.nih.gov",
        "www.who.int", "www.cdc.gov", "www.nih.gov",
        "www.mayoclinic.org", "www.webmd.com", "medlineplus.gov",
        "www.nhs.uk", "www.cochranelibrary.com",
        "www.thelancet.com", "www.nejm.org", "www.bmj.com",
        "www.healthline.com", "my.clevelandclinic.org",
    ],
    "numerical_statistical": [
        "data.worldbank.org", "www.statista.com", "fred.stlouisfed.org",
        "ourworldindata.org", "data.un.org", "www.imf.org",
        "ec.europa.eu", "www.bls.gov", "www.census.gov",
        "databank.worldbank.org", "www.oecd.org",
        "en.wikipedia.org", "www.pewresearch.org",
    ],
    "finance_business": [
        "www.sec.gov", "finance.yahoo.com", "www.bloomberg.com",
        "www.wsj.com", "www.ft.com", "www.reuters.com",
        "www.investopedia.com", "www.marketwatch.com",
        "seekingalpha.com", "www.cnbc.com", "www.fool.com",
        "www.annualreports.com", "www.macrotrends.net",
    ],
    "legal_regulatory": [
        "www.law.cornell.edu", "supreme.justia.com",
        "eur-lex.europa.eu", "www.legislation.gov.uk",
        "www.congress.gov", "www.govinfo.gov",
        "gdpr-info.eu", "www.justice.gov",
        "en.wikipedia.org", "www.nolo.com",
    ],
    "news_current_events": [
        "www.reuters.com", "apnews.com", "www.bbc.com",
        "www.bbc.co.uk", "www.aljazeera.com", "www.npr.org",
        "www.theguardian.com", "www.nytimes.com", "www.washingtonpost.com",
        "www.economist.com", "www.politifact.com",
        "www.snopes.com", "www.factcheck.org",
    ],
    "historical": [
        "en.wikipedia.org", "www.britannica.com",
        "www.history.com", "www.worldhistory.org",
        "www.archives.gov", "www.smithsonianmag.com",
        "www.bbc.co.uk", "www.loc.gov",
    ],
    "causal_relational": [
        "en.wikipedia.org", "www.britannica.com",
        "pubmed.ncbi.nlm.nih.gov", "www.nature.com",
        "www.science.org", "www.who.int",
        "www.cdc.gov", "ourworldindata.org",
    ],
    "opinion_subjective": [
        "en.wikipedia.org", "www.pewresearch.org",
        "news.gallup.com",
    ],
}


GLOBAL_DOMAIN_BLOCKLIST: list[str] = [
    # AI-Generated Content Farms
    "www.thecoldwire.com", "www.yourstatsguru.com",
    "www.thehealthboard.com", "www.zippia.com",
    "www.sportskeeda.com", "www.javatpoint.com",
    "www.geeksforgeeks.org", "www.tutorialspoint.com",
    # Known Misinformation / Low-Quality
    "www.naturalnews.com", "www.infowars.com",
    "www.breitbart.com", "www.thegatewaypundit.com",
    "www.zerohedge.com", "www.newsmax.com",
    "www.oann.com", "www.epochtimes.com",
    # SEO Spam / Content Scrapers
    "www.answers.com", "www.reference.com",
    "www.ask.com", "www.quora.com",
    "www.ehow.com", "www.wikihow.com",
    # Clickbait / Tabloid
    "www.dailymail.co.uk", "www.thesun.co.uk",
    "www.buzzfeed.com", "www.huffpost.com", "www.nypost.com",
    # AI Aggregators (unreliable paraphrasing)
    "www.perplexity.ai", "www.you.com",
    "www.phind.com", "chat.openai.com",
    # Forum / User-Generated (not authoritative for facts)
    "medium.com", "www.linkedin.com",
    "twitter.com", "x.com", "www.reddit.com",
    # Pseudo-science / Alternative Medicine
    "www.mercola.com", "www.greenmedinfo.com",
    "www.globalresearch.ca", "www.collective-evolution.com",
    "www.draxe.com", "www.mindbodygreen.com",
]


# ── Singleton ─────────────────────────────────────────────────────────────

_router: Optional["DomainSourceRouter"] = None


def get_domain_source_router() -> "DomainSourceRouter":
    """Get or create the domain source router singleton."""
    global _router
    if _router is None:
        _router = DomainSourceRouter()
    return _router


class DomainSourceRouter:
    """
    Routes claims to domain-specific evidence sources.

    For each claim, gathers evidence from:
    1. Domain-specific free APIs (Wikipedia, arXiv, PubMed, etc.)
    2. Tavily with include_domains/exclude_domains filtering
    3. Serper with site: operator filtering
    """

    # Map domains → API methods to call
    DOMAIN_API_MAP = {
        ClaimDomain.GENERAL_FACTUAL: ["_search_wikipedia", "_search_wikidata", "_search_factcheck"],
        ClaimDomain.SCIENTIFIC_TECHNICAL: ["_search_arxiv", "_search_semantic_scholar", "_search_crossref"],
        ClaimDomain.MEDICAL_HEALTH: ["_search_pubmed"],
        ClaimDomain.NUMERICAL_STATISTICAL: ["_search_worldbank"],
        ClaimDomain.FINANCE_BUSINESS: ["_search_sec_edgar"],
        ClaimDomain.NEWS_CURRENT_EVENTS: ["_search_gnews", "_search_factcheck"],
        ClaimDomain.HISTORICAL: ["_search_wikipedia"],
        ClaimDomain.CAUSAL_RELATIONAL: ["_search_wikipedia"],
        ClaimDomain.LEGAL_REGULATORY: [],  # Tavily/Serper only
        ClaimDomain.OPINION_SUBJECTIVE: [],  # Minimal verification
    }

    def __init__(self):
        self.settings = get_settings()

    async def gather_evidence(
        self,
        claim: ExtractedClaim,
        search_queries: Optional[list[str]] = None,
    ) -> list[EvidencePiece]:
        """
        Gather evidence from all domain-appropriate sources for a claim.

        Args:
            claim: The extracted claim with domain classification.
            search_queries: Optional pre-generated search queries.

        Returns:
            List of EvidencePiece objects from all sources (pre-NLI).
        """
        domain = claim.domain
        queries = search_queries or claim.search_queries or [claim.text]
        primary_query = queries[0] if queries else claim.text

        tasks = []

        # 1. Domain-specific free APIs
        api_methods = self.DOMAIN_API_MAP.get(domain, [])
        for method_name in api_methods:
            method = getattr(self, method_name, None)
            if method:
                tasks.append(self._safe_call(method, primary_query, claim))

        # 2+3. Web 搜索（Tavily 优先、Serper 兜底，见 _gather_web）
        tasks.append(self._gather_web(primary_query, domain))

        results = await asyncio.gather(*tasks)

        # Flatten and deduplicate
        evidence = []
        seen_snippets = set()
        for result_list in results:
            if not result_list:
                continue
            for piece in result_list:
                # Deduplicate by snippet content (first 100 chars)
                key = piece.snippet[:100].lower().strip()
                if key not in seen_snippets:
                    seen_snippets.add(key)
                    evidence.append(piece)

        logger.info(
            f"Domain router [{domain.value}]: gathered {len(evidence)} evidence "
            f"pieces from {len(tasks)} sources for claim '{claim.id}'"
        )
        return evidence

    async def _safe_call(self, fn, *args) -> list[EvidencePiece]:
        """Call a source function safely, returning empty list on error/timeout."""
        try:
            # 单源超时保护：避免某个慢源拖住整条 claim（借鉴 Haystack/RAGFlow 的 per-retriever 超时做法）
            result = await asyncio.wait_for(fn(*args), timeout=12.0)
            return result or []
        except asyncio.TimeoutError:
            logger.warning(f"Source {fn.__name__} timed out after 12s, skipped")
            return []
        except Exception as e:
            logger.warning(f"Source {fn.__name__} failed: {e}")
            return []

    async def _gather_web(
        self, query: str, domain: ClaimDomain,
    ) -> list[EvidencePiece]:
        """Web 搜索：Tavily 优先（高级搜索+全文），<3 条才补一次 Serper。

        两者串行但共用一个连接（省 TLS 握手），好路径只花一次付费调用。
        """
        if not self.settings.tavily_api_key and not self.settings.serper_api_key:
            return []
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                out: list[EvidencePiece] = []
                if self.settings.tavily_api_key:
                    out.extend(await self._safe_call(
                        self._search_tavily_filtered, query, domain, client
                    ))
                if len(out) < 3 and self.settings.serper_api_key:
                    out.extend(await self._safe_call(
                        self._search_serper_filtered, query, domain, client
                    ))
                return out
        except Exception as e:
            logger.warning(f"Web search block failed: {e}")
            return []

    # ── Wikipedia ─────────────────────────────────────────────────────────

    _WIKI_HEADERS = {
        "User-Agent": "TruthLens-HallucinationDetector/2.0 (contact: research@detector.local)",
        "Accept": "application/json",
    }

    @staticmethod
    def _parse_json_response(resp: "httpx.Response", source: str) -> Optional[dict]:
        """403/HTML/空 body 时返回 None 而不是抛 JSONDecodeError（修复日志里的 Expecting value 报错）。"""
        if resp.status_code != 200:
            logger.warning(f"{source} returned HTTP {resp.status_code}, skipped")
            return None
        ctype = resp.headers.get("content-type", "")
        if "json" not in ctype and "javascript" not in ctype and "text" not in ctype:
            logger.warning(f"{source} unexpected content-type {ctype!r}, skipped")
            return None
        if not resp.content or not resp.content.strip():
            logger.warning(f"{source} returned empty body, skipped")
            return None
        try:
            return resp.json()
        except Exception as e:
            logger.warning(f"{source} JSON parse failed: {e}, skipped")
            return None

    # ── Wikipedia ─────────────────────────────────────────────────────────

    async def _search_wikipedia(
        self, query: str, claim: ExtractedClaim = None,
    ) -> list[EvidencePiece]:
        """Search Wikipedia and extract article introductions (extract 并行取，3 篇串行→并行)。"""
        evidence = []
        async with httpx.AsyncClient(timeout=10.0, headers=self._WIKI_HEADERS) as client:
            # Step 1: Search for relevant articles
            search_resp = await client.get(
                "https://en.wikipedia.org/w/api.php",
                params={
                    "action": "query",
                    "list": "search",
                    "srsearch": query,
                    "format": "json",
                    "srlimit": 3,
                },
            )
            search_data = self._parse_json_response(search_resp, "Wikipedia search")
            if not search_data:
                return []
            results = search_data.get("query", {}).get("search", [])

            async def _fetch_extract(title: str) -> Optional[tuple[str, str]]:
                try:
                    extract_resp = await client.get(
                        "https://en.wikipedia.org/w/api.php",
                        params={
                            "action": "query",
                            "prop": "extracts",
                            "exintro": 1,
                            "explaintext": 1,
                            "titles": title,
                            "format": "json",
                        },
                    )
                    extract_data = self._parse_json_response(extract_resp, "Wikipedia extract")
                    if not extract_data:
                        return None
                    pages = extract_data.get("query", {}).get("pages", {})
                    for page in pages.values():
                        text = page.get("extract", "")[:800]
                        if text:
                            return title, text
                    return None
                except Exception as e:
                    logger.warning(f"Wikipedia extract for {title!r} failed: {e}")
                    return None

            titles = [r.get("title", "") for r in results[:3] if r.get("title")]
            snippets = {r.get("title", ""): re.sub(r"<[^>]+>", "", r.get("snippet", "")) for r in results[:3]}
            # 3 篇简介并行抓取（原来串行 3×RTT，这里 1×RTT）
            fetched = await asyncio.gather(*[_fetch_extract(t) for t in titles])
            fetched_map = {t: txt for item in fetched if item for t, txt in [item]}

            for title in titles:
                extract_text = fetched_map.get(title, "")
                final_snippet = extract_text or snippets.get(title, "")
                if final_snippet:
                    evidence.append(EvidencePiece(
                        source_type=SourceType.DIRECT_API,
                        source_tier=SourceTier.DIRECT_API,
                        source_url=f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}",
                        source_title=f"Wikipedia: {title}",
                        snippet=final_snippet[:1000],
                    ))

        return evidence

    # ── Wikidata ──────────────────────────────────────────────────────────

    async def _search_wikidata(
        self, query: str, claim: ExtractedClaim = None,
    ) -> list[EvidencePiece]:
        """Search Wikidata for structured entity data."""
        evidence = []
        async with httpx.AsyncClient(timeout=10.0, headers=self._WIKI_HEADERS) as client:
            resp = await client.get(
                "https://www.wikidata.org/w/api.php",
                params={
                    "action": "wbsearchentities",
                    "search": query[:100],
                    "language": "en",
                    "format": "json",
                    "limit": 3,
                },
            )
            data = self._parse_json_response(resp, "Wikidata")
            if not data:
                return []
            for entity in data.get("search", [])[:3]:
                desc = entity.get("description", "")
                label = entity.get("label", "")
                entity_id = entity.get("id", "")
                snippet = f"{label}: {desc}" if desc else label
                if snippet:
                    evidence.append(EvidencePiece(
                        source_type=SourceType.DIRECT_API,
                        source_tier=SourceTier.DIRECT_API,
                        source_url=f"https://www.wikidata.org/wiki/{entity_id}",
                        source_title=f"Wikidata: {label}",
                        snippet=snippet[:500],
                    ))
        return evidence

    # ── Google Fact Check ─────────────────────────────────────────────────

    async def _search_factcheck(
        self, query: str, claim: ExtractedClaim = None,
    ) -> list[EvidencePiece]:
        """Search Google Fact Check Tools API for existing fact-checks."""
        if not self.settings.google_factcheck_api_key:
            return []

        evidence = []
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://factchecktools.googleapis.com/v1alpha1/claims:search",
                params={
                    "query": query[:200],
                    "key": self.settings.google_factcheck_api_key,
                    "languageCode": "en",
                },
            )
            if resp.status_code != 200:
                logger.warning(f"Fact Check API returned {resp.status_code}")
                return []

            try:
                from app.core.provider_usage import record_provider_call
                await record_provider_call("factcheck")
            except Exception:
                pass
            data = resp.json()
            for claim_review in data.get("claims", [])[:3]:
                claim_text = claim_review.get("text", "")
                reviews = claim_review.get("claimReview", [])
                for review in reviews[:1]:
                    publisher = review.get("publisher", {}).get("name", "Unknown")
                    rating = review.get("textualRating", "")
                    url = review.get("url", "")
                    snippet = f"Claim: \"{claim_text}\" — Rating: {rating} (by {publisher})"
                    evidence.append(EvidencePiece(
                        source_type=SourceType.DIRECT_API,
                        source_tier=SourceTier.DIRECT_API,
                        source_url=url,
                        source_title=f"Fact Check: {publisher}",
                        snippet=snippet[:500],
                    ))
        return evidence

    # ── arXiv ─────────────────────────────────────────────────────────────

    async def _search_arxiv(
        self, query: str, claim: ExtractedClaim = None,
    ) -> list[EvidencePiece]:
        """Search arXiv for scientific papers."""
        evidence = []
        try:
            import arxiv as arxiv_lib
            search = arxiv_lib.Search(
                query=query,
                max_results=3,
                sort_by=arxiv_lib.SortCriterion.Relevance,
            )
            client = arxiv_lib.Client()

            # Run in thread pool since arxiv lib is synchronous
            loop = asyncio.get_event_loop()
            results = await loop.run_in_executor(
                None, lambda: list(client.results(search))
            )

            for result in results[:3]:
                abstract = result.summary[:600] if result.summary else ""
                title = result.title or "Unknown"
                url = result.entry_id or ""
                snippet = f"{title}. {abstract}"
                evidence.append(EvidencePiece(
                    source_type=SourceType.DIRECT_API,
                    source_tier=SourceTier.DIRECT_API,
                    source_url=url,
                    source_title=f"arXiv: {title[:100]}",
                    snippet=snippet[:1000],
                ))
        except ImportError:
            logger.warning("arxiv library not installed, skipping arXiv search")
        except Exception as e:
            logger.warning(f"arXiv search failed: {e}")
        return evidence

    # ── Semantic Scholar ──────────────────────────────────────────────────

    async def _search_semantic_scholar(
        self, query: str, claim: ExtractedClaim = None,
    ) -> list[EvidencePiece]:
        """Search Semantic Scholar for academic papers (unauthenticated)."""
        evidence = []
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://api.semanticscholar.org/graph/v1/paper/search",
                params={
                    "query": query[:200],
                    "limit": 3,
                    "fields": "title,abstract,url,year",
                },
            )
            if resp.status_code != 200:
                return []

            data = resp.json()
            for paper in data.get("data", [])[:3]:
                title = paper.get("title", "Unknown")
                abstract = paper.get("abstract", "")
                year = paper.get("year", "")
                url = paper.get("url", "")
                snippet = f"[{year}] {title}. {abstract}" if abstract else f"[{year}] {title}"
                if snippet:
                    evidence.append(EvidencePiece(
                        source_type=SourceType.DIRECT_API,
                        source_tier=SourceTier.DIRECT_API,
                        source_url=url,
                        source_title=f"Semantic Scholar: {title[:80]}",
                        snippet=snippet[:1000],
                    ))
        return evidence

    # ── Crossref ──────────────────────────────────────────────────────────

    async def _search_crossref(
        self, query: str, claim: ExtractedClaim = None,
    ) -> list[EvidencePiece]:
        """Search Crossref for academic paper metadata."""
        evidence = []
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://api.crossref.org/works",
                params={
                    "query": query[:200],
                    "rows": 3,
                    "mailto": "hallucination-detector@research.local",
                },
                headers={"User-Agent": "AIHallucinationDetector/2.0 (mailto:hallucination-detector@research.local)"},
            )
            if resp.status_code != 200:
                return []

            data = resp.json()
            for item in data.get("message", {}).get("items", [])[:3]:
                title_list = item.get("title", [])
                title = title_list[0] if title_list else "Unknown"
                doi = item.get("DOI", "")
                publisher = item.get("publisher", "")
                abstract = item.get("abstract", "")
                # Clean HTML from abstract
                abstract_clean = re.sub(r"<[^>]+>", "", abstract)[:400] if abstract else ""
                snippet = f"{title}. {abstract_clean}" if abstract_clean else f"{title} (Published by {publisher})"
                if snippet:
                    evidence.append(EvidencePiece(
                        source_type=SourceType.DIRECT_API,
                        source_tier=SourceTier.DIRECT_API,
                        source_url=f"https://doi.org/{doi}" if doi else None,
                        source_title=f"Crossref: {title[:80]}",
                        snippet=snippet[:1000],
                    ))
        return evidence

    # ── PubMed ────────────────────────────────────────────────────────────

    async def _search_pubmed(
        self, query: str, claim: ExtractedClaim = None,
    ) -> list[EvidencePiece]:
        """Search PubMed for medical/health literature (unauthenticated, 3 req/s)."""
        evidence = []
        base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
        async with httpx.AsyncClient(timeout=15.0) as client:
            # Step 1: Search for PubMed IDs
            search_resp = await client.get(
                f"{base}/esearch.fcgi",
                params={
                    "db": "pubmed",
                    "term": query[:200],
                    "retmode": "json",
                    "retmax": 3,
                },
            )
            search_data = search_resp.json()
            pmids = search_data.get("esearchresult", {}).get("idlist", [])

            if not pmids:
                return []

            # Brief delay for rate limiting (3 req/s unauthenticated)
            await asyncio.sleep(0.4)

            # Step 2: Fetch summaries
            summary_resp = await client.get(
                f"{base}/esummary.fcgi",
                params={
                    "db": "pubmed",
                    "id": ",".join(pmids[:3]),
                    "retmode": "json",
                },
            )
            summary_data = summary_resp.json()
            results = summary_data.get("result", {})

            for pmid in pmids[:3]:
                article = results.get(pmid, {})
                if not isinstance(article, dict):
                    continue
                title = article.get("title", "")
                source = article.get("source", "")
                pubdate = article.get("pubdate", "")
                snippet = f"{title} (Published in {source}, {pubdate})"
                evidence.append(EvidencePiece(
                    source_type=SourceType.DIRECT_API,
                    source_tier=SourceTier.DIRECT_API,
                    source_url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                    source_title=f"PubMed: {title[:80]}",
                    snippet=snippet[:1000],
                ))
        return evidence

    # ── SEC EDGAR ─────────────────────────────────────────────────────────

    async def _search_sec_edgar(
        self, query: str, claim: ExtractedClaim = None,
    ) -> list[EvidencePiece]:
        """Search SEC EDGAR for company filings."""
        evidence = []
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://efts.sec.gov/LATEST/search-index",
                params={
                    "q": query[:100],
                    "dateRange": "custom",
                    "startdt": "2023-01-01",
                    "enddt": "2026-12-31",
                    "forms": "10-K,10-Q,8-K",
                },
                headers={
                    "User-Agent": "AIHallucinationDetector research@detector.local",
                    "Accept": "application/json",
                },
            )
            if resp.status_code != 200:
                return []

            data = resp.json()
            for hit in data.get("hits", {}).get("hits", [])[:3]:
                source = hit.get("_source", {})
                entity = source.get("entity_name", "Unknown")
                form = source.get("form_type", "")
                filed = source.get("file_date", "")
                file_num = source.get("file_num", "")
                snippet = f"{entity}: {form} filing dated {filed}"
                evidence.append(EvidencePiece(
                    source_type=SourceType.DIRECT_API,
                    source_tier=SourceTier.DIRECT_API,
                    source_url=f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&filenum={file_num}",
                    source_title=f"SEC EDGAR: {entity} ({form})",
                    snippet=snippet[:500],
                ))
        return evidence

    # ── World Bank ────────────────────────────────────────────────────────

    async def _search_worldbank(
        self, query: str, claim: ExtractedClaim = None,
    ) -> list[EvidencePiece]:
        """Search World Bank for economic indicators (keyword-based)."""
        evidence = []
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Search indicators by keyword
            resp = await client.get(
                "https://api.worldbank.org/v2/indicator",
                params={
                    "format": "json",
                    "per_page": 3,
                    "qterm": query[:100],
                },
            )
            if resp.status_code != 200:
                return []

            try:
                data = resp.json()
            except Exception as e:
                logger.warning(f"World Bank JSON parse failed: {e}, skipped")
                return []
            if not isinstance(data, list) or len(data) < 2 or not data[1]:
                return []

            for indicator in data[1][:3] if data[1] else []:
                name = indicator.get("name", "Unknown")
                source_note = indicator.get("sourceNote", "")
                ind_id = indicator.get("id", "")
                snippet = f"World Bank Indicator: {name}. {source_note[:400]}"
                evidence.append(EvidencePiece(
                    source_type=SourceType.DIRECT_API,
                    source_tier=SourceTier.DIRECT_API,
                    source_url=f"https://data.worldbank.org/indicator/{ind_id}",
                    source_title=f"World Bank: {name[:80]}",
                    snippet=snippet[:1000],
                ))
        return evidence

    # ── GNews ─────────────────────────────────────────────────────────────

    async def _search_gnews(
        self, query: str, claim: ExtractedClaim = None,
    ) -> list[EvidencePiece]:
        """Search Google News for recent articles."""
        evidence = []
        try:
            from gnews import GNews

            loop = asyncio.get_event_loop()
            g = GNews(max_results=3, period="1y")
            results = await loop.run_in_executor(
                None, g.get_news, query[:100]
            )

            for article in (results or [])[:3]:
                title = article.get("title", "Unknown")
                desc = article.get("description", "")
                url = article.get("url", "")
                publisher = article.get("publisher", {})
                pub_name = publisher.get("title", "") if isinstance(publisher, dict) else str(publisher)
                snippet = f"{title}. {desc}" if desc else title
                evidence.append(EvidencePiece(
                    source_type=SourceType.DIRECT_API,
                    source_tier=SourceTier.DIRECT_API,
                    source_url=url,
                    source_title=f"News ({pub_name}): {title[:60]}",
                    snippet=snippet[:1000],
                ))
        except ImportError:
            logger.warning("gnews library not installed, skipping news search")
        except Exception as e:
            logger.warning(f"GNews search failed: {e}")
        return evidence

    # ── Tavily Domain-Filtered Search ─────────────────────────────────────

    async def _search_tavily_filtered(
        self, query: str, domain: ClaimDomain, client=None,
    ) -> list[EvidencePiece]:
        """Search Tavily with domain-specific include/exclude filters."""
        if client is None:
            async with httpx.AsyncClient(timeout=15.0) as _c:
                return await self._tavily_fetch(_c, query, domain)
        return await self._tavily_fetch(client, query, domain)

    async def _tavily_fetch(
        self, client, query: str, domain: ClaimDomain,
    ) -> list[EvidencePiece]:
        evidence = []
        include_domains = DOMAIN_ALLOWLISTS.get(domain.value, [])
        exclude_domains = GLOBAL_DOMAIN_BLOCKLIST

        payload = {
            "api_key": self.settings.tavily_api_key,
            "query": query[:400],
            "search_depth": "advanced",
            "max_results": 5,
            "include_answer": False,
        }
        if include_domains:
            payload["include_domains"] = include_domains
        if exclude_domains:
            payload["exclude_domains"] = exclude_domains[:150]  # Tavily max 150

        resp = await client.post(
            "https://api.tavily.com/search",
            json=payload,
        )
        if resp.status_code != 200:
            logger.warning(f"Tavily returned {resp.status_code}")
            return []

        try:
            from app.core.provider_usage import record_provider_call
            await record_provider_call("tavily")
        except Exception:
            pass
        data = resp.json()
        for result in data.get("results", [])[:5]:
            title = result.get("title", "")
            content = result.get("content", "")
            url = result.get("url", "")
            # Prefer raw_content if available (full page text)
            raw = result.get("raw_content", "")
            snippet = raw[:1000] if raw else content[:1000]
            if snippet:
                evidence.append(EvidencePiece(
                    source_type=SourceType.WEB_SEARCH,
                    source_tier=SourceTier.TAVILY,
                    source_url=url,
                    source_title=title[:200],
                    snippet=snippet,
                ))
        return evidence

    # ── Serper Domain-Filtered Search ─────────────────────────────────────

    async def _search_serper_filtered(
        self, query: str, domain: ClaimDomain, client=None,
    ) -> list[EvidencePiece]:
        """Search Serper (Google) with site: operator for domain filtering."""
        if client is None:
            async with httpx.AsyncClient(timeout=10.0) as _c:
                return await self._serper_fetch(_c, query, domain)
        return await self._serper_fetch(client, query, domain)

    async def _serper_fetch(
        self, client, query: str, domain: ClaimDomain,
    ) -> list[EvidencePiece]:
        evidence = []
        include_domains = DOMAIN_ALLOWLISTS.get(domain.value, [])

        # Build site-restricted query — use top 3 domains to avoid query length issues
        if include_domains:
            site_filter = " OR ".join(f"site:{d}" for d in include_domains[:3])
            full_query = f"{query} ({site_filter})"
        else:
            full_query = query

        # Add exclusions for common bad domains
        for bad in GLOBAL_DOMAIN_BLOCKLIST[:5]:
            full_query += f" -site:{bad}"

        resp = await client.post(
            "https://google.serper.dev/search",
            json={
                "q": full_query[:500],
                "num": 5,
            },
            headers={
                "X-API-KEY": self.settings.serper_api_key,
                "Content-Type": "application/json",
            },
        )
        if resp.status_code != 200:
            logger.warning(f"Serper returned {resp.status_code}")
            return []

        try:
            from app.core.provider_usage import record_provider_call
            await record_provider_call("serper")
        except Exception:
            pass
        data = resp.json()
        for result in data.get("organic", [])[:5]:
            title = result.get("title", "")
            snippet_text = result.get("snippet", "")
            url = result.get("link", "")
            if snippet_text:
                evidence.append(EvidencePiece(
                    source_type=SourceType.WEB_SEARCH,
                    source_tier=SourceTier.SERPER,
                    source_url=url,
                    source_title=title[:200],
                    snippet=snippet_text[:1000],
                ))
        return evidence
