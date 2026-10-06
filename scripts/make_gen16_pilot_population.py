"""Build the Generation 16 V6 pilot population.

The pilot is deliberately *not* a bred generation. It re-runs a subset of the
Gen 14 genomes that plateaued in Gen 15 (same task, same model, same budgets)
with exactly one change: ``epistemic_policy.enabled = true``. That gives a clean
within-genome comparison of the V5 linear repair loop (Gen 15 trajectories)
against the V6 evidence-gated tree-search on the identical starting point.

Usage:
    python3 scripts/make_gen16_pilot_population.py \
        --source results/hae_gen15_fullstack_v5/generation_14_population.json \
        --out configs/generation_16_pilot_population.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hae.genome.schema import CompanyGenome  # noqa: E402

# Gen 15 (V5) plateau firms chosen for diversity of plateau depth and size.
# Gen 15 held-out trajectory (out of 50) is recorded next to each id so the
# pilot summary can be compared without re-opening the Gen 15 archive.
PILOT_FIRMS = {
    "gen_14_elite_1": {"gen15_trajectory": [49, 49, 49, 49, 49], "agents": 60},
    "gen_14_crossover_2": {"gen15_trajectory": [47, 49, 49, 49, 49], "agents": 32},
    "gen_14_mutant_1": {"gen15_trajectory": [48, 48, 48, 48, 48], "agents": 90},
    "gen_14_elite_2": {"gen15_trajectory": [46, 46, 46, 46, 46], "agents": 90},
}


def build(source: str, out: str) -> dict:
    with open(source) as f:
        raw = json.load(f)
    pop = raw["population"] if isinstance(raw, dict) else raw
    by_id = {g["company_id"]: g for g in pop}
    missing = [cid for cid in PILOT_FIRMS if cid not in by_id]
    if missing:
        raise SystemExit(f"source population lacks pilot firms: {missing}")

    pilot = []
    for cid, meta in PILOT_FIRMS.items():
        genome = json.loads(json.dumps(by_id[cid]))  # deep copy, keep verbatim
        genome["epistemic_policy"] = {"enabled": True}  # all other gene fields default
        history = list(genome.get("mutation_history") or [])
        history.append(
            "Gen 16 V6 pilot: identical Gen 14 genome re-run with "
            "epistemic_policy.enabled=true (evidence-gated tree-search repair)")
        genome["mutation_history"] = history
        # Round-trip through the schema so a malformed gene fails here, not in a pod.
        parsed = CompanyGenome.from_dict(genome)
        assert parsed.company_id == cid
        assert parsed.epistemic_policy is not None and parsed.epistemic_policy.enabled, cid
        assert parsed.total_agent_count == meta["agents"], (
            cid, parsed.total_agent_count, meta["agents"])
        pilot.append(genome)

    envelope = {
        "generation": 16,
        "name": "V6 epistemic-search pilot (Gen 14 genomes, V5 plateau subset)",
        "source_population": source,
        "pilot_design": {
            "comparison": "within-genome V5 (Gen 15) vs V6 (Gen 16 pilot)",
            "changed_fields": ["epistemic_policy.enabled"],
            "gen15_reference": {cid: m["gen15_trajectory"] for cid, m in PILOT_FIRMS.items()},
        },
        "population": pilot,
    }
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w") as f:
        json.dump(envelope, f, indent=2)
    return envelope


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="results/hae_gen15_fullstack_v5/generation_14_population.json")
    ap.add_argument("--out", default="configs/generation_16_pilot_population.json")
    args = ap.parse_args()
    env = build(args.source, args.out)
    for i, g in enumerate(env["population"]):
        parsed = CompanyGenome.from_dict(g)
        pol = parsed.epistemic_policy
        print(f"[{i}] {g['company_id']}: agents={parsed.total_agent_count} "
              f"epistemic={pol.enabled} budget={pol.search_budget_moves} k={pol.branching_k} "
              f"c_puct={pol.c_puct} quota={pol.low_prior_quota}")
    print(f"wrote {args.out} ({len(env['population'])} firms)")


if __name__ == "__main__":
    main()
