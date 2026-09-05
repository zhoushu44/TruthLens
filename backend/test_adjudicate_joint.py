"""Joint vs per-claim adjudication comparison (real Zen LLM calls).

Decision rule: flip default to joint iff status agreement >= 3/4 AND joint faster.
NLI scores kept moderate (<0.90/<0.85) so the LLM path is actually exercised.
"""
import asyncio
import sys
import time

sys.path.insert(0, ".")

from app.config import get_settings
from app.core.claim_adjudicator import get_claim_adjudicator
from app.models.detect import (
    ClaimDomain,
    EvidencePiece,
    ExtractedClaim,
    SourceTier,
    SourceType,
)


def ev(title, snippet, ent, con, zh=False):
    neu = max(0.0, 1.0 - ent - con)
    return EvidencePiece(
        source_type=SourceType.WEB_SEARCH,
        source_tier=SourceTier.TAVILY,
        source_url="https://example.com/" + title.replace(" ", "_"),
        source_title=title,
        snippet=snippet,
        nli_scores={"entailment": ent, "contradiction": con, "neutral": round(neu, 3)},
    )


def claim(cid, text, domain):
    return ExtractedClaim(
        id=cid, text=text, domain=domain, importance=0.7,
        confidence_needs_checking=0.7,
    )


CASES = [
    (claim("c1", "The Eiffel Tower is 330 metres tall.", ClaimDomain.GENERAL_FACTUAL),
     [ev("Wiki: Eiffel Tower",
         "The Eiffel Tower is a wrought-iron tower 330 metres tall in Paris, completed in 1889.", 0.75, 0.05),
      ev("Britannica: Eiffel Tower",
         "Eiffel Tower, Parisian landmark completed 1889, height about 330 metres including antennas.", 0.70, 0.05),
      ev("Travel guide Paris",
         "Visitors can ascend the 330-metre Eiffel Tower for panoramic views of Paris.", 0.65, 0.05)]),
    (claim("c2", "秦始皇在公元前200年统一了六国。", ClaimDomain.HISTORICAL),
     [ev("Wiki: 秦始皇",
         "秦始皇于公元前221年建立秦朝，统一六国。", 0.10, 0.70),
      ev("史记记载",
         "始皇二十六年（公元前221年），秦灭齐，天下归一。", 0.08, 0.68),
      ev("通史",
         "公元前221年秦统一是公认的年份。", 0.10, 0.60)]),
    (claim("c3", "Drinking coffee extends human lifespan by five years.",
           ClaimDomain.MEDICAL_HEALTH),
     [ev("Health blog",
         "Some observational studies link coffee to longevity, but evidence is inconclusive.", 0.25, 0.10),
      ev("Nutrition review",
         "Coffee contains antioxidants; no trial proves a five-year lifespan gain.", 0.15, 0.35),
      ev("News brief",
         "Researchers caution against overstating coffee benefits.", 0.10, 0.30)]),
    (claim("c4", "Python was created in the 1990s by Guido van Rossum.",
           ClaimDomain.GENERAL_FACTUAL),
     [ev("Wiki: Python",
         "Python was conceived in the late 1980s by Guido van Rossum and first released in 1991.", 0.70, 0.10),
      ev("Bio: van Rossum",
         "Guido van Rossum began implementing Python in December 1989; version 1.0 came in 1994.", 0.55, 0.10),
      ev("Forum post",
         "I think Python came out sometime in the nineties.", 0.40, 0.05)]),
]


async def run_once(adj, mode):
    get_settings().adjudication_mode = mode
    t0 = time.time()
    outs = await adj.adjudicate_batch(CASES)
    return outs, time.time() - t0


async def main():
    adj = get_claim_adjudicator()
    if adj.client is None:
        print("No adjudicator LLM configured, abort")
        return 1
    print("adjudicator backend:", adj.backend, adj.model, flush=True)

    per, t_per = await run_once(adj, "perclaim")
    jnt, t_jnt = await run_once(adj, "joint")

    print(f"\n{'claim':<5} {'perclaim':<18} {'joint':<18} agree")
    agree = 0
    for (c, _), p, j in zip(CASES, per, jnt):
        ok = p.status == j.status and abs(p.risk_score - j.risk_score) <= 15
        agree += ok
        print(f"{c.id:<5} {p.status.value}/{p.risk_score:<6.1f} {j.status.value}/{j.risk_score:<6.1f} "
              f"{'OK' if ok else 'DIFF'}")
        print(f"      per: {(p.reasoning or '')[:90]}")
        print(f"      jnt: {(j.reasoning or '')[:90]}")

    print(f"\nagreement: {agree}/4 | time: perclaim {t_per:.1f}s vs joint {t_jnt:.1f}s")
    if agree >= 3 and t_jnt < t_per:
        print("DECISION: joint wins -> flip default")
        return 2
    print("DECISION: keep perclaim")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
