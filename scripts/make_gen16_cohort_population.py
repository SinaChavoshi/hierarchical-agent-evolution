"""Build the Generation 16 V6 cohort population (Benchmark A).

The cohort is *not* a bred generation. It re-runs the Gen 14 genomes that ran
in Gen 15 under ``V5`` -- same task file, model, budgets and ``max_iterations``
-- with the ``epistemic_policy`` gene switched on, several independent
replicas ("seeds") per non-converged lineage, and the two Gen 15 convergers as
controls. Breeding would confound topology changes with the gene; replicas
answer the question the pilot could not (``n = 1`` per firm): does the
evidence-gated search move a plateaued lineage, and how noisy is that?

Everything about the cohort lives in the spec (default
``configs/generations/gen16_cohort.json``); this script only applies it.

Usage:
    python3 scripts/make_gen16_cohort_population.py \
        [--spec configs/generations/gen16_cohort.json] \
        [--out configs/generation_16_population.json] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hae.genome.schema import CompanyGenome, EpistemicPolicyGene, GenomeValidationError  # noqa: E402

DEFAULT_SPEC = "configs/generations/gen16_cohort.json"
DEFAULT_OUT = "configs/generation_16_population.json"

SPEC_KEYS = {"generation", "name", "design", "source_population", "source_summary", "task_file",
             "objective", "selection", "epistemic_policy", "notes"}
SELECTION_KEYS = {"non_converged", "non_converged_seeds", "converged_controls",
                  "converged_control_seeds", "order"}
# Fields a replica may differ in from its source genome. Anything else that
# differs is a bug in this script, and the test for it is byte-level.
REPLICA_FIELDS = {"company_id", "epistemic_policy", "mutation_history"}


class CohortError(SystemExit):
    """A spec that cannot be applied exactly as written."""


def load_spec(path: str) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        spec = json.load(fh)
    unknown = set(spec) - SPEC_KEYS
    missing = {"generation", "source_population", "source_summary", "selection", "epistemic_policy"} - set(spec)
    if unknown or missing:
        raise CohortError(f"{path}: unknown keys {sorted(unknown)}, missing keys {sorted(missing)}")
    sel_unknown = set(spec["selection"]) - SELECTION_KEYS
    if sel_unknown:
        raise CohortError(f"{path}: unknown selection keys {sorted(sel_unknown)}")
    return spec


def validated_policy(genome: Dict[str, Any], overrides: Dict[str, Any]) -> Dict[str, Any]:
    """The breeder's rule: unknown or out-of-range override fields refuse, not warn."""
    unknown = set(overrides) - set(EpistemicPolicyGene().to_dict())
    if unknown:
        raise CohortError(f"epistemic_policy override names unknown fields {sorted(unknown)}")
    current = dict(genome.get("epistemic_policy") or {})
    current.update(overrides)
    try:
        return EpistemicPolicyGene.from_dict(current).to_dict()
    except (GenomeValidationError, ValueError, TypeError) as exc:
        raise CohortError(f"epistemic_policy override {overrides} is invalid: {exc}") from exc


def select_lineages(summary: Dict[str, Any], selection: Dict[str, Any]
                    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    board = sorted(summary["leaderboard"], key=lambda f: (-float(f["net_fitness"]), f["company_id"]))
    stuck = [f for f in board if not f.get("converged_100pct")]
    converged = [f for f in board if f.get("converged_100pct")]
    want_stuck = int(selection["non_converged"])
    want_ctrl = int(selection["converged_controls"])
    if len(stuck) < want_stuck or len(converged) < want_ctrl:
        raise CohortError(f"summary has {len(stuck)} non-converged and {len(converged)} converged firms; "
                          f"spec wants {want_stuck} + {want_ctrl}")
    return stuck[:want_stuck], converged[:want_ctrl]


def build(spec: Dict[str, Any], repo_root: str = ".") -> Dict[str, Any]:
    with open(os.path.join(repo_root, spec["source_population"]), encoding="utf-8") as fh:
        raw = json.load(fh)
    source_pop = raw["population"] if isinstance(raw, dict) else raw
    by_id = {g["company_id"]: g for g in source_pop}
    with open(os.path.join(repo_root, spec["source_summary"]), encoding="utf-8") as fh:
        summary = json.load(fh)
    selection = spec["selection"]
    overrides = dict(spec["epistemic_policy"])
    stuck, controls = select_lineages(summary, selection)
    roster = ([(f, "non_converged", int(selection["non_converged_seeds"])) for f in stuck]
              + [(f, "control", int(selection["converged_control_seeds"])) for f in controls])
    missing = [f["company_id"] for f, _, _ in roster if f["company_id"] not in by_id]
    if missing:
        raise CohortError(f"source population lacks selected lineages: {missing}")

    lineages: Dict[str, Dict[str, Any]] = {}
    population: List[Dict[str, Any]] = []
    max_seeds = max(n for _, _, n in roster)
    # Seed-major order: the first wave of a partially parallel Job covers
    # every lineage once, so an aborted cohort still yields one draw each.
    for seed in range(1, max_seeds + 1):
        for firm, role, n_seeds in roster:
            if seed > n_seeds:
                continue
            cid = firm["company_id"]
            traj = [t["passed"] for t in firm["iteration_trajectory"]]
            lineages.setdefault(cid, {"role": role, "seeds": n_seeds, "gen15_trajectory": traj,
                                      "gen15_net_fitness": firm["net_fitness"],
                                      "replicas": []})
            genome = json.loads(json.dumps(by_id[cid]))  # deep copy; everything else verbatim
            replica_id = f"{cid}__s{seed}"
            genome["company_id"] = replica_id
            genome["epistemic_policy"] = validated_policy(genome, overrides)
            history = list(genome.get("mutation_history") or [])
            history.append(
                f"Gen {spec['generation']} V6 cohort, seed {seed}/{n_seeds} ({role}): Gen 14 genome `{cid}` "
                f"(Gen 15 held-out {traj}) re-run with epistemic_policy {json.dumps(overrides, sort_keys=True)}")
            genome["mutation_history"] = history
            parsed = CompanyGenome.from_dict(genome)  # a malformed gene fails here, not in a pod
            assert parsed.company_id == replica_id
            assert parsed.epistemic_policy is not None and parsed.epistemic_policy.enabled, replica_id
            lineages[cid]["replicas"].append(replica_id)
            population.append(genome)

    return {
        "generation": int(spec["generation"]),
        "name": spec.get("name", ""),
        "spec": spec,
        "source_population": spec["source_population"],
        "source_summary": spec["source_summary"],
        "cohort_design": {
            "comparison": "within-genome V5 (Gen 15 trajectories) vs V6 (this cohort), replicated",
            "changed_fields": [f"epistemic_policy.{k}" for k in sorted(overrides)],
            "lineages": lineages,
            "firm_runs": len(population),
        },
        "population": population,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--spec", default=DEFAULT_SPEC)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--dry-run", action="store_true", help="print the roster, write nothing")
    args = ap.parse_args()
    envelope = build(load_spec(args.spec))
    design = envelope["cohort_design"]
    print(f"{envelope['name']}\nchanged fields: {design['changed_fields']}")
    for cid, info in design["lineages"].items():
        print(f"  {cid:<22} {info['role']:<13} seeds={info['seeds']} gen15={info['gen15_trajectory']} "
              f"net={info['gen15_net_fitness']}")
    for i, g in enumerate(envelope["population"]):
        pol = CompanyGenome.from_dict(g).epistemic_policy
        print(f"[{i:>2}] {g['company_id']:<28} budget={pol.search_budget_moves} frontier={pol.frontier_size} "
              f"k={pol.branching_k} quota={pol.low_prior_quota}")
    if args.dry_run:
        print(f"dry run: {design['firm_runs']} firm-runs, nothing written")
        return
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(envelope, fh, indent=2)
        fh.write("\n")
    print(f"wrote {args.out} ({design['firm_runs']} firm-runs)")


if __name__ == "__main__":
    main()
