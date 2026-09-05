"""Risk scorer: UNVERIFIABLE_SOURCE must not inflate the overall score."""
import sys

sys.path.insert(0, ".")

from app.core.risk_scorer import get_risk_scorer
from app.models.detect import (
    ClaimStatus,
    ClaimVerificationResult,
    ExtractedClaim,
)


def res(cid, status, risk, importance=0.7):
    return ClaimVerificationResult(
        claim=ExtractedClaim(id=cid, text=f"claim {cid}", importance=importance),
        risk_score=risk,
        status=status,
    )


scorer = get_risk_scorer()

# 2 verified (risk 10) + 1 unreachable-source (risk 60): overall must stay low
mixed = [res("c1", ClaimStatus.VERIFIED, 10.0),
         res("c2", ClaimStatus.VERIFIED, 10.0),
         res("c3", ClaimStatus.UNVERIFIABLE_SOURCE, 60.0)]
overall = scorer.compute_overall_risk(mixed)
warns = scorer.generate_warnings(mixed)
print(f"mixed overall={overall} (must be <= 20), warnings={[w.type for w in warns]}")
assert overall <= 20.0, "unverifiable claim inflated the score!"
assert any(w.type == "unverifiable_source" for w in warns), "missing warning!"

# all unreachable -> neutral 55 with warnings, not 0 and not 60+
allbad = [res("c1", ClaimStatus.UNVERIFIABLE_SOURCE, 60.0)]
overall2 = scorer.compute_overall_risk(allbad)
print(f"all-unverifiable overall={overall2} (must be 55.0)")
assert overall2 == 55.0

# empty -> 0
assert scorer.compute_overall_risk([]) == 0.0
print("RISK-TEST PASS")
