"""Recomputes honest artifact counts for every archived generation.

Reported "files authored on disk" figures were derived from
`run_output["workspace_files"]`, which `company.run()` returned **unfiltered**:
`.pytest_cache/` entries, `__pycache__` byproducts, and paths where markdown
formatting leaked into the filename were all counted as agent deliverables.

This script re-derives the counts from the archived bundles using the canonical
rules in `src/artifacts.py`. Because every scorecard retains its full
path->content map, this requires no re-runs and no inference cost.

Usage:
    PYTHONPATH=. python3 scripts/audit_artifact_integrity.py
"""

import glob
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.artifacts import partition_bundle  # noqa: E402

EXPERIMENTS = [
    ("Gen 5", "experiments/v1/exp-007-parallel-gen5"),
    ("Gen 6", "experiments/v1/exp-008-parallel-gen6"),
    ("Gen 7", "experiments/v1/exp-009-parallel-gen7"),
    ("Gen 8", "experiments/v1/exp-010-parallel-gen8"),
    ("Gen 9", "experiments/v1/exp-011-parallel-gen9"),
    ("Gen 10", "experiments/v1/exp-012-parallel-gen10"),
]

OUT_PATH = "experiments/v1/artifact_integrity_audit.json"


def audit_generation(gen_id, exp_dir):
    paths = sorted(glob.glob(os.path.join(exp_dir, "scorecards", "*.json")))
    if not paths:
        return None

    firms = []
    for p in paths:
        card = json.load(open(p))
        bundle = card.get("run_output", {}).get("workspace_files", {}) or {}
        authored, generated, malformed = partition_bundle(bundle)
        firms.append({
            "company_id": card.get("company_id"),
            "net_score": card.get("overall_score"),
            "reported_files": len(bundle),
            "authored_files": len(authored),
            "generated_files": len(generated),
            "malformed_files": len(malformed),
            "authored_py": sum(1 for k in authored if k.endswith(".py")),
            "authored_tests": sum(
                1 for k in authored
                if k.endswith(".py") and ("test" in os.path.basename(k).lower())
            ),
        })

    n = len(firms)
    agg = lambda key: sum(f[key] for f in firms)  # noqa: E731
    return {
        "generation": gen_id,
        "firm_count": n,
        "reported_avg": round(agg("reported_files") / n, 2),
        "authored_avg": round(agg("authored_files") / n, 2),
        "authored_py_avg": round(agg("authored_py") / n, 2),
        "authored_tests_avg": round(agg("authored_tests") / n, 2),
        "generated_avg": round(agg("generated_files") / n, 2),
        "malformed_avg": round(agg("malformed_files") / n, 2),
        "reported_max": max(f["reported_files"] for f in firms),
        "authored_max": max(f["authored_files"] for f in firms),
        "inflation_pct": round(
            100.0 * (agg("reported_files") - agg("authored_files")) / max(agg("reported_files"), 1), 1
        ),
        "firms": sorted(firms, key=lambda f: -(f["net_score"] or 0)),
    }


def main():
    results = [r for r in (audit_generation(g, d) for g, d in EXPERIMENTS) if r]

    with open(OUT_PATH, "w") as fh:
        json.dump(results, fh, indent=2)
    print(f"Saved artifact integrity audit to {OUT_PATH}\n")

    hdr = (f"{'Gen':<8}{'Reported':>10}{'Authored':>10}{'.py':>7}{'tests':>7}"
           f"{'Generated':>11}{'Malformed':>11}{'Inflation':>11}")
    print(hdr)
    print("-" * len(hdr))
    for r in results:
        print(f"{r['generation']:<8}{r['reported_avg']:>10.1f}{r['authored_avg']:>10.1f}"
              f"{r['authored_py_avg']:>7.1f}{r['authored_tests_avg']:>7.1f}"
              f"{r['generated_avg']:>11.1f}{r['malformed_avg']:>11.1f}"
              f"{r['inflation_pct']:>10.1f}%")

    print(f"\nPeak single-firm counts (reported -> authored):")
    for r in results:
        print(f"  {r['generation']:<8}{r['reported_max']:>4} -> {r['authored_max']:<4}")


if __name__ == "__main__":
    main()
