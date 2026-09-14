"""Harvests, ranks and archives any generation's tournament results.

Replaces the one-off `harvest_and_finalize_gen7/8/9/10.py` scripts, which were
near-identical copies differing only in a generation number and a directory
name. Each copy also drifted: the Gen 9 version skipped the survivor export
entirely, and none of them ranked by anything but legacy net fitness.

Ranking here is by `execution_integrity`-weighted rubric score
(`src/evaluator.composite_score`) when gate data is available, because ranking
by legacy net picked the wrong champion in five of six generations -- and in
Generation 9 picked the worst firm in the cohort as the sole parent of the
next. Legacy net is still computed and reported side by side so the two can be
compared.

Usage:
    PYTHONPATH=. python3 scripts/harvest_generation.py --generation 11
    PYTHONPATH=. python3 scripts/harvest_generation.py --generation 11 --no-download
"""

import argparse
import glob
import json
import os
import statistics as st
import subprocess
import sys
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.evaluator import (  # noqa: E402
    JUDGED_DIMENSIONS,
    composite_score,
    execution_integrity,
)

BUCKET = "gemle-gke-dev-agent-evolution"

# Gate results for generations whose scorecards predate the execution harness.
# Produced by scripts/backfill_execution_fitness.py.
BACKFILL_PATH = "experiments/v1/execution_grounded_fitness.json"

# Generation -> experiment directory. Generations before 5 predate the
# execution-grounded pipeline and are archived, not harvested.
EXPERIMENT_DIRS = {
    5: "experiments/v1/exp-007-parallel-gen5",
    6: "experiments/v1/exp-008-parallel-gen6",
    7: "experiments/v1/exp-009-parallel-gen7",
    8: "experiments/v1/exp-010-parallel-gen8",
    9: "experiments/v1/exp-011-parallel-gen9",
    10: "experiments/v1/exp-012-parallel-gen10",
    11: "experiments/v1/exp-013-parallel-gen11",
}

GATES = ("syntax", "build", "smoke", "tests", "telemetry")


def access_token() -> str:
    """ADC token. `gsutil` reports invalid credentials on this host, so the
    raw JSON API is used instead."""
    return subprocess.check_output(
        ["gcloud", "auth", "application-default", "print-access-token"]
    ).decode().strip()


def download_generation(generation: int, dest: str) -> int:
    os.makedirs(dest, exist_ok=True)
    token = access_token()
    prefix = f"parallel_runs/generation_{generation}/"
    req = urllib.request.Request(
        f"https://storage.googleapis.com/storage/v1/b/{BUCKET}/o"
        f"?prefix={urllib.parse.quote(prefix, safe='')}",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req) as resp:
        items = json.loads(resp.read().decode()).get("items", [])

    count = 0
    for item in items:
        name = item["name"]
        fname = os.path.basename(name)
        if not fname.endswith(".json"):
            continue
        url = (f"https://storage.googleapis.com/download/storage/v1/b/{BUCKET}/o/"
               f"{urllib.parse.quote(name, safe='')}?alt=media")
        dl = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(dl) as resp:
            content = resp.read()
        with open(os.path.join(dest, fname), "wb") as fh:
            fh.write(content)
        count += 1
        print(f"  -> {fname} ({len(content):,} bytes)")
    return count


def load_backfill() -> Dict[str, Dict[str, str]]:
    """Gate results for pre-harness generations, keyed by company_id."""
    if not os.path.exists(BACKFILL_PATH):
        return {}
    with open(BACKFILL_PATH) as fh:
        data = json.load(fh)
    return {f["company_id"]: f["harness_gates"]
            for gen in data for f in gen.get("firms", [])}


def gate_status(card: Dict[str, Any],
                backfill: Dict[str, Dict[str, str]]) -> Optional[Dict[str, str]]:
    """Harness gate statuses for a firm, live or backfilled.

    Scorecards written before the harness landed carry only the legacy
    booleans. Those are not gate statuses and are deliberately not coerced
    into looking like them -- the backfill is consulted instead, and if that
    has nothing either the caller must refuse to rank rather than guess.
    """
    status = (card.get("verification") or {}).get("gate_status")
    if status:
        return status
    return backfill.get(card.get("company_id")) or None


def judged_dims(card: Dict[str, Any]) -> Dict[str, float]:
    fitness = (card.get("evaluation") or {}).get("fitness", {}) or {}
    out = {}
    for dim in JUDGED_DIMENSIONS:
        value = fitness.get(dim)
        if value is None and dim == "actionability_and_synthesis":
            value = fitness.get("actionability", card.get("actionability"))
        if value is None:
            value = card.get(dim)
        out[dim] = float(value) if value is not None else 0.0
    return out


def summarise(card: Dict[str, Any],
              backfill: Dict[str, Dict[str, str]]) -> Dict[str, Any]:
    status = gate_status(card, backfill)
    exec_score = execution_integrity(status)
    loop = card.get("run_output", {}).get("verification_loop") or {}
    accounting = card.get("run_output", {}).get("token_accounting") or {}
    return {
        "company_id": card.get("company_id"),
        "legacy_net": round(float(card.get("overall_score") or 0.0), 2),
        "gross_score": round(float(card.get("gross_score") or 0.0), 2),
        "rubric_score": composite_score(judged_dims(card), exec_score),
        "execution_integrity": exec_score,
        "gate_status": status or {},
        "gates_passed": sum(1 for s in (status or {}).values() if s == "passed"),
        "authored_files": (card.get("verification") or {}).get("authored_files", 0),
        "evaluation_failed": bool(
            ((card.get("evaluation") or {}).get("fitness") or {}).get("evaluation_failed")),
        "verify_attempts": loop.get("attempt_count", 0),
        "verify_converged": loop.get("converged"),
        "tokens_measured": accounting.get("fully_measured"),
        "thought_tokens": accounting.get("thought_tokens", 0),
        "cost_usd": round(float((card.get("opex") or {}).get("estimated_cost_usd") or 0.0), 4),
        "headcount": (card.get("opex") or {}).get("headcount", 0),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--generation", type=int, required=True)
    ap.add_argument("--no-download", action="store_true",
                    help="rank scorecards already on disk")
    ap.add_argument("--top", type=int, default=5, help="survivor count to export")
    args = ap.parse_args()

    exp_dir = EXPERIMENT_DIRS.get(args.generation)
    if not exp_dir:
        print(f"No experiment directory registered for generation {args.generation}.")
        return 1
    sc_dir = os.path.join(exp_dir, "scorecards")

    if not args.no_download:
        print(f"Downloading generation {args.generation} scorecards from gs://{BUCKET} ...")
        print(f"  {download_generation(args.generation, sc_dir)} file(s) synced.")

    cards = []
    for path in sorted(glob.glob(os.path.join(sc_dir, "*.json"))):
        with open(path) as fh:
            cards.append(json.load(fh))
    if not cards:
        print(f"No scorecards found under {sc_dir}.")
        return 1

    backfill = load_backfill()
    rows = [summarise(c, backfill) for c in cards]

    # Refuse to rank without execution data. Ranking on prose alone is exactly
    # what put the wrong champion at the top of five of six generations, and
    # composite_score() will happily renormalise the judged weights and return
    # a confident number when handed nothing. A missing gate map is a reason to
    # stop, not a reason to fall back.
    ungated = [r["company_id"] for r in rows if r["execution_integrity"] is None]
    if ungated:
        print(f"\nREFUSING TO RANK: {len(ungated)} of {len(rows)} firm(s) have no "
              f"execution gate data, live or backfilled:")
        for cid in ungated:
            print(f"    {cid}")
        print("\nWithout gates, rubric_score is a prose-only number that looks "
              "authoritative and is not. Run the backfill first:")
        print("    PYTHONPATH=. python3 scripts/backfill_execution_fitness.py "
              f"--gen {args.generation}")
        print("(the harness needs real pip/pytest/opentelemetry, so run it in "
              "the project container, not on a workstation).")
        return 2
    by_rubric = sorted(rows, key=lambda r: r["rubric_score"], reverse=True)
    by_legacy = sorted(rows, key=lambda r: r["legacy_net"], reverse=True)

    print(f"\nGeneration {args.generation}: {len(rows)} firms\n")
    header = (f"{'#':<3}{'firm':<24}{'rubric':<9}{'legacy':<9}{'exec':<7}"
              f"{'gates':<7}{'files':<7}{'vfy':<5}{'cost':<9}{'measured':<9}")
    print(header)
    print("-" * len(header))
    for pos, row in enumerate(by_rubric, 1):
        exec_str = "n/a" if row["execution_integrity"] is None else f"{row['execution_integrity']:.1f}"
        flag = "  [EVAL FAILED]" if row["evaluation_failed"] else ""
        print(f"{pos:<3}{row['company_id']:<24}{row['rubric_score']:<9}"
              f"{row['legacy_net']:<9}{exec_str:<7}{row['gates_passed']}/5    "
              f"{row['authored_files']:<7}{row['verify_attempts']:<5}"
              f"${row['cost_usd']:<8}{str(row['tokens_measured']):<9}{flag}")

    if by_rubric[0]["company_id"] != by_legacy[0]["company_id"]:
        print(f"\n  NOTE: rubric champion ({by_rubric[0]['company_id']}) differs from "
              f"legacy champion ({by_legacy[0]['company_id']}).")

    failed = [r for r in rows if r["evaluation_failed"]]
    if failed:
        print(f"\n  WARNING: {len(failed)} firm(s) have a FAILED evaluation and "
              f"must be excluded from breeding: {[r['company_id'] for r in failed]}")

    exec_scores = [r["execution_integrity"] for r in rows if r["execution_integrity"] is not None]
    if exec_scores:
        print(f"\n  execution_integrity: mean {st.mean(exec_scores):.1f}, "
              f"max {max(exec_scores):.1f}, "
              f"firms at 0.0: {sum(1 for e in exec_scores if e == 0.0)}")
    gate_totals = {g: sum(1 for r in rows if r["gate_status"].get(g) == "passed") for g in GATES}
    print(f"  gates passed across cohort: {gate_totals}")

    # Champion and survivors, ranked by the rubric.
    eligible = [r for r in by_rubric if not r["evaluation_failed"]]
    by_id = {c.get("company_id"): c for c in cards}

    champ = eligible[0]

    # Written under rubric-specific names. `winning_champion_genome.json` and
    # `top_N_survivor_genomes.json` are the historical record of what each
    # tournament actually selected at the time, including where that selection
    # was wrong; overwriting them would erase the very thing the correction
    # documents. The rubric ranking goes alongside, not on top.
    champ_path = os.path.join(exp_dir, "rubric_champion_genome.json")
    survivors_path = os.path.join(exp_dir, f"top_{args.top}_rubric_survivors.json")
    standings_path = os.path.join(exp_dir, "rubric_standings.json")

    champ_card = by_id[champ["company_id"]]
    if "genome" not in champ_card:
        print(f"\n  WARNING: {champ['company_id']} has no genome in its scorecard; "
              f"skipping genome export. Standings are still written.")
    else:
        with open(champ_path, "w") as fh:
            json.dump(champ_card["genome"], fh, indent=2)
        with open(survivors_path, "w") as fh:
            json.dump([by_id[r["company_id"]] for r in eligible[:args.top]],
                      fh, indent=2)
    with open(standings_path, "w") as fh:
        json.dump({"generation": args.generation, "ranked_by": "rubric_score",
                   "legacy_champion": by_legacy[0]["company_id"],
                   "rubric_champion": champ["company_id"],
                   "firms": by_rubric}, fh, indent=2)

    print(f"\nChampion: {champ['company_id']} "
          f"(rubric {champ['rubric_score']}, legacy {champ['legacy_net']}, "
          f"exec {champ['execution_integrity']})")
    print(f"Wrote rubric ranking artifacts to {exp_dir}")
    print("(historical winning_champion_genome.json left untouched)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
