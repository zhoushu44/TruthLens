"""Router timing probe: one real claim through gather_evidence."""
import asyncio
import sys
import time

sys.path.insert(0, ".")

from app.core.domain_source_router import get_domain_source_router
from app.models.detect import ClaimDomain, ExtractedClaim


async def main():
    router = get_domain_source_router()
    claim = ExtractedClaim(
        id="probe1", text="The Eiffel Tower is 330 metres tall.",
        text_en="The Eiffel Tower is 330 metres tall.",
        domain=ClaimDomain.GENERAL_FACTUAL, importance=0.7,
        confidence_needs_checking=0.7,
        search_queries=["Eiffel Tower height 330 metres"],
    )
    t0 = time.time()
    ev = await router.gather_evidence(claim)
    dt = time.time() - t0
    from collections import Counter
    tiers = Counter((e.source_tier.value if hasattr(e.source_tier, "value") else e.source_tier) for e in ev)
    print(f"gather_evidence: {len(ev)} pieces in {dt:.1f}s, tiers={dict(tiers)}")


if __name__ == "__main__":
    asyncio.run(main())
