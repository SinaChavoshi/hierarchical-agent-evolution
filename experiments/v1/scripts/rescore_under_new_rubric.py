"""Re-scores every archived firm under the rebuilt fitness function.

Combines two things that already exist and need no new inference:

  * the per-dimension judge scores archived in each scorecard, and
  * the executed gate results from `experiments/v1/execution_grounded_fitness.json`.

The result answers the question the legacy numbers could not: **what would the
leaderboard have looked like if the score had ever been coupled to whether the
code ran?**

Usage:
    PYTHONPATH=. python3 scripts/rescore_under_new_rubric.py
"""

import glob
import json
import os
import statistics as st
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.evaluator import (  # noqa: E402
    JUDGED_DIMENSIONS,
    composite_score,
    execution_integrity,
)

EXPERIMENTS = [
    ("Gen 5", "experiments/v1/exp-007-parallel-gen5"),
    ("Gen 6", "experiments/v1/exp-008-parallel-gen6"),
    ("Gen 7", "experiments/v1/exp-009-parallel-gen7"),
    ("Gen 8", "experiments/v1/exp-010-parallel-gen8"),
    ("Gen 9", "experiments/v1/exp-011-parallel-gen9"),
    ("Gen 10", "experiments/v1/exp-012-parallel-gen10"),
]

EXEC_PATH = "experiments/v1/execution_grounded_fitness.json"
OUT_PATH = "experiments/v1/rubric_rescore.json"

# Scorecards store actionability under a shortened key.
KEY_ALIASES = {"actionability_and_synthesis": ("actionability_and_synthesis",
                                               "actionability")}


def judged_dims(card):
    """Pulls the five judged dimensions out of a scorecard."""
    fitness = card.get("evaluation", {}).get("fitness", {}) or {}
    out = {}
    for dim in JUDGED_DIMENSIONS:
        value = None
        for key in KEY_ALIASES.get(dim, (dim,)):
            if key in fitness:
                value = fitness[key]
                break
            if key in card:
                value = card[key]
                break
        out[dim] = float(value) if value is not None else 0.0
    return out


def load_gate_status():
    with open(EXEC_PATH) as fh:
        data = json.load(fh)
    return {f["company_id"]: f["harness_gates"]
            for gen in data for f in gen["firms"]}


def pearson(xs, ys):
    mx, my = st.mean(xs), st.mean(ys)
    num = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    den = (sum((a - mx) ** 2 for a in xs) * sum((b - my) ** 2 for b in ys)) ** 0.5
    return num / den if den else 0.0


def main():
    gates_by_firm = load_gate_status()
    generations = []
    all_legacy, all_new = [], []

    for label, root in EXPERIMENTS:
        cards = []
        for path in sorted(glob.glob(os.path.join(root, "scorecards", "*.json"))):
            with open(path) as fh:
                cards.append(json.load(fh))
        if not cards:
            print(f"  (no scorecards under {root})")
            continue

        firms = []
        for card in cards:
            cid = card.get("company_id")
            gate_status = gates_by_firm.get(cid)
            judged = judged_dims(card)
            exec_score = execution_integrity(gate_status)
            new_score = composite_score(judged, exec_score)
            legacy = float(card.get("overall_score") or 0.0)
            firms.append({
                "company_id": cid,
                "legacy_net": round(legacy, 2),
                "legacy_gross": round(float(card.get("gross_score") or 0.0), 2),
                "judged": {k: round(v, 2) for k, v in judged.items()},
                "execution_integrity": exec_score,
                "rubric_score": new_score,
                "delta": round(new_score - legacy, 2),
            })
            all_legacy.append(legacy)
            all_new.append(new_score)

        firms.sort(key=lambda f: f["rubric_score"], reverse=True)
        legacy_rank = sorted(firms, key=lambda f: f["legacy_net"], reverse=True)
        for pos, firm in enumerate(firms, 1):
            firm["rubric_rank"] = pos
        for pos, firm in enumerate(legacy_rank, 1):
            firm["legacy_rank"] = pos

        champ_legacy = legacy_rank[0]
        champ_new = firms[0]
        generations.append({
            "generation": label,
            "firm_count": len(firms),
            "legacy_champion": champ_legacy["company_id"],
            "legacy_champion_score": champ_legacy["legacy_net"],
            "rubric_champion": champ_new["company_id"],
            "rubric_champion_score": champ_new["rubric_score"],
            "champion_changed": champ_legacy["company_id"] != champ_new["company_id"],
            "rubric_mean": round(st.mean(f["rubric_score"] for f in firms), 2),
            "legacy_mean": round(st.mean(f["legacy_net"] for f in firms), 2),
            "firms": firms,
        })

        flag = "  <-- CHAMPION CHANGES" if generations[-1]["champion_changed"] else ""
        print(f"{label}: legacy champion {champ_legacy['company_id']} "
              f"({champ_legacy['legacy_net']}) -> rubric champion "
              f"{champ_new['company_id']} ({champ_new['rubric_score']}){flag}")

    print()
    print(f"corr(legacy_net, rubric_score) over {len(all_legacy)} firms = "
          f"{pearson(all_legacy, all_new):+.3f}")
    changed = sum(1 for g in generations if g["champion_changed"])
    print(f"champions that change under the new rubric: {changed}/{len(generations)}")

    with open(OUT_PATH, "w") as fh:
        json.dump(generations, fh, indent=2)
    print(f"wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
