"""Fallback threshold sweep: find best (ent_verify, con_contra, ent_partial).

13 hand-made verdict cases. Small sample — better than gut feel, revisit with
production logs later. Tie-break prefers current values (conservative).
"""
import sys

sys.path.insert(0, ".")

# (max_ent, max_con, expected_status)
CASES = [
    (0.85, 0.05, "VERIFIED"), (0.95, 0.02, "VERIFIED"), (0.82, 0.10, "VERIFIED"),
    (0.65, 0.10, "PARTIALLY_VERIFIED"), (0.55, 0.20, "PARTIALLY_VERIFIED"),
    (0.60, 0.15, "PARTIALLY_VERIFIED"),
    (0.10, 0.85, "CONTRADICTED"), (0.20, 0.75, "CONTRADICTED"),
    (0.05, 0.90, "CONTRADICTED"),
    (0.30, 0.20, "UNVERIFIED"), (0.40, 0.40, "UNVERIFIED"),
    (0.10, 0.10, "UNVERIFIED"), (0.45, 0.45, "UNVERIFIED"),
]

CURRENT = (0.80, 0.70, 0.50)


def decide(ent, con, t_verify, t_contra, t_partial):
    if ent >= t_verify:
        return "VERIFIED"
    if con > t_contra:
        return "CONTRADICTED"
    if ent > t_partial:
        return "PARTIALLY_VERIFIED"
    return "UNVERIFIED"


best = None
for t_verify in [0.70, 0.75, 0.80, 0.85, 0.90]:
    for t_contra in [0.60, 0.65, 0.70, 0.75, 0.80]:
        for t_partial in [0.40, 0.50, 0.60]:
            ok = sum(decide(e, c, t_verify, t_contra, t_partial) == exp
                     for e, c, exp in CASES)
            key = (ok, -abs(t_verify - CURRENT[0]) - abs(t_contra - CURRENT[1])
                   - abs(t_partial - CURRENT[2]))
            if best is None or key > best[0]:
                best = (key, (t_verify, t_contra, t_partial), ok)

_, winner, ok = best
cur_ok = sum(decide(e, c, *CURRENT) == exp for e, c, exp in CASES)
print(f"current {CURRENT}: {cur_ok}/{len(CASES)}")
print(f"winner  {winner}: {ok}/{len(CASES)}")
print("APPLY" if winner != CURRENT else "KEEP-CURRENT")
