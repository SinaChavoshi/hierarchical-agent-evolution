"""Build the V8 smoke population: four run-3 lineages with the CEO policy gene on.

The smoke cohort exists to exercise the V8 organisation path end to end on the
cluster before any breeding depends on it: four distinct Gen 14 lineages (the
``__s1`` replica of each, as run in ``configs/generation_16_r3_population.json``)
are copied with

* ``ceo_policy.enabled = true`` and a per-firm ``headcount_lambda`` /
  ``exploration_c`` so the four firms size their teams differently;
* ``role_library`` = the hand-written ``legacy`` seeds
  (``hae.genome.role_seeds.seed_role_library``), 8 roles, 0 uses;
* the run-3 ``epistemic_policy`` unchanged (enabled, 60 moves, frontier 3,
  V7 heads in shadow mode), so the only new variable is the organisation.

Everything else -- CEO, departments, budgets, role bindings, code overlays --
is copied unchanged; ``tests/test_make_v8_population.py`` checks that at the
dict level. ``mutation_history`` gains one line saying what changed.

Usage:
    python3 scripts/make_v8_population.py \
        [--source configs/generation_16_r3_population.json] \
        [--out configs/generation_v8_smoke_population.json] [--dry-run]
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from typing import Any, Dict, List, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hae.genome.role_seeds import seed_role_library  # noqa: E402
from hae.genome.schema import CompanyGenome  # noqa: E402

DEFAULT_SOURCE = "configs/generation_16_r3_population.json"
DEFAULT_OUT = "configs/generation_v8_smoke_population.json"
GENERATION = 17
SEED_FLAVOUR = "legacy"

# (source company id, headcount_lambda, exploration_c). Lambda spans "cheap
# heads" to "expensive heads"; exploration_c spans "exploit the library's
# statistics" to "try the untried". With a 0-use seed library the statistics
# are all the optimistic prior, so at turn 0 lambda is what moves team size.
SMOKE_SLOTS: List[Tuple[str, float, float]] = [
    ("gen_14_crossover_2__s1", 0.05, 0.25),
    ("gen_14_elite_1__s1", 0.15, 0.50),
    ("gen_14_crossover_1__s1", 0.30, 0.75),
    ("gen_14_mutant_2__s1", 0.50, 1.00),
]


def ceo_policy_for(headcount_lambda: float, exploration_c: float) -> Dict[str, Any]:
    return {"enabled": True, "headcount_lambda": headcount_lambda,
            "exploration_c": exploration_c, "temperature": 0.0}


def build(source: Dict[str, Any]) -> Dict[str, Any]:
    by_id = {f["company_id"]: f for f in source.get("population", [])}
    missing = [cid for cid, _, _ in SMOKE_SLOTS if cid not in by_id]
    if missing:
        raise ValueError(f"source population lacks {missing}")
    library = [r.to_dict() for r in seed_role_library(SEED_FLAVOUR)]
    firms: List[Dict[str, Any]] = []
    for cid, lam, c in SMOKE_SLOTS:
        firm = copy.deepcopy(by_id[cid])
        lineage = cid.split("__")[0]
        firm["company_id"] = f"v8_smoke_{lineage}"
        firm["generation"] = GENERATION
        firm["parent_ids"] = [cid]
        firm["ceo_policy"] = ceo_policy_for(lam, c)
        firm["role_library"] = copy.deepcopy(library)
        firm["mutation_history"] = list(firm.get("mutation_history") or []) + [
            f"V8 smoke cohort: run-3 genome `{cid}` with ceo_policy "
            f"{json.dumps(firm['ceo_policy'], sort_keys=True)} and the {SEED_FLAVOUR!r} "
            f"role library ({len(library)} roles); epistemic_policy unchanged from run 3"]
        # Validate: the genome loads, the genes land where the runner reads
        # them, and the dict round-trips (so the file is what the runner sees).
        genome = CompanyGenome.from_dict(firm)
        if not genome.org_enabled:
            raise ValueError(f"{firm['company_id']}: ceo_policy did not enable")
        if genome.ceo_policy.headcount_lambda != lam or genome.ceo_policy.exploration_c != c:
            raise ValueError(f"{firm['company_id']}: ceo_policy fields did not load")
        if [r.role_id for r in genome.role_library] != [r["role_id"] for r in library]:
            raise ValueError(f"{firm['company_id']}: role library did not load")
        if not genome.epistemic_policy.enabled:
            raise ValueError(f"{firm['company_id']}: epistemic_policy must stay enabled")
        firm = genome.to_dict()
        firms.append(firm)
    return {
        "generation": GENERATION,
        "name": "V8 smoke cohort: 4 run-3 lineages with the CEO policy gene on (legacy seeds)",
        "source_population": DEFAULT_SOURCE,
        "cohort_design": {
            "purpose": "exercise turn-0 organisation selection, role routing and the recruit move "
                       "end to end before any breeding depends on them; not a comparison cohort",
            "changed_fields": ["ceo_policy", "role_library", "company_id", "generation", "parent_ids",
                               "mutation_history"],
            "role_library_seed": SEED_FLAVOUR,
            "slots": [{"company_id": f"v8_smoke_{cid.split('__')[0]}", "source": cid,
                       "headcount_lambda": lam, "exploration_c": c}
                      for cid, lam, c in SMOKE_SLOTS],
        },
        "population": firms,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--source", default=DEFAULT_SOURCE)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    with open(args.source, "r", encoding="utf-8") as fh:
        source = json.load(fh)
    out = build(source)
    for firm in out["population"]:
        print(f"{firm['company_id']}: ceo_policy={json.dumps(firm['ceo_policy'], sort_keys=True)} "
              f"roles={len(firm['role_library'])}")
    if args.dry_run:
        return 0
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1)
        fh.write("\n")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
