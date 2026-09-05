"""Groq NLI judge test: 6 EN/ZH pairs covering entailment/contradiction/neutral."""
import asyncio
import sys
import time

sys.path.insert(0, ".")

from app.core.nli_model import get_nli_model

CASES = [
    # (evidence, claim, expected_label)
    ("The Eiffel Tower is 330 metres tall and located in Paris.",
     "The Eiffel Tower is 330m tall.", "ENTAILMENT"),
    ("The Eiffel Tower is 330 metres tall.",
     "The Eiffel Tower is 500 meters tall.", "CONTRADICTION"),
    ("The Eiffel Tower is located in Paris.",
     "The Eiffel Tower was completed in 1889.", "NEUTRAL"),
    ("埃菲尔铁塔高330米，位于巴黎。",
     "埃菲尔铁塔高330米。", "ENTAILMENT"),
    ("秦始皇于公元前221年统一六国。",
     "秦始皇在公元前200年统一六国。", "CONTRADICTION"),
    ("长城东起山海关，西至嘉峪关。",
     "长城全长21196公里。", "NEUTRAL"),
]


async def main():
    nli = get_nli_model()
    nli.load()
    print("backend:", nli.describe())

    pairs = [(e, c) for e, c, _ in CASES]
    t0 = time.time()
    results = await nli.predict(pairs)
    dt = time.time() - t0

    ok = 0
    for (e, c, expected), scores in zip(CASES, results):
        label = nli.get_label(scores)
        mark = "OK " if label == expected else "MISS"
        if label == expected:
            ok += 1
        print(f"[{mark}] expect={expected:13s} got={label:13s} "
              f"e={scores['entailment']:.2f} c={scores['contradiction']:.2f} n={scores['neutral']:.2f}")
        print(f"      claim: {c[:40]}")

    print(f"\naccuracy: {ok}/{len(CASES)}, time: {dt:.1f}s for {len(CASES)} pairs")
    # 缓存命中也测一下（第二次应秒回）
    t1 = time.time()
    await nli.predict(pairs)
    print(f"cached rerun: {time.time() - t1:.2f}s")
    return 0 if ok >= 5 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
