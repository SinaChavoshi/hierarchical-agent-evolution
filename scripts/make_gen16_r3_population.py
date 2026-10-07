"""Build the Generation 16 V6 cohort population for run 3 (day-2 tracks).

Run 3 re-runs the same 26 slots as runs 1 and 2 (``configs/generation_16_population.json``)
on an image that carries the day-2 tracks. The only change to the genomes is the
``epistemic_policy.extra`` block that points the search at the first ``V7`` heads in
**shadow mode**: the heads are loaded and their predictions are logged on every
move, but ``prior_head_weight`` is 0.0 and ``value_head_live`` is false, so
hypothesis selection and the recorded value are byte-identical to what run 2 did.
That gives an off-policy evaluation of the heads on fresh firms without
confounding the run-2 -> run-3 comparison with steering.

Everything else -- company ids, lineages, seeds, budgets, role bindings -- is
copied unchanged, and the test for that is byte-level (``tests/test_make_gen16_r3_population.py``).
The head keys are written FLAT inside ``epistemic_policy`` (that is how ``_Model`` serialises ``extra``).

Usage:
    python3 scripts/make_gen16_r3_population.py \
        [--source configs/generation_16_population.json] \
        [--out configs/generation_16_r3_population.json] [--dry-run]
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from typing import Any, Dict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hae.genome.schema import CompanyGenome  # noqa: E402

DEFAULT_SOURCE = "configs/generation_16_population.json"
DEFAULT_OUT = "configs/generation_16_r3_population.json"

# Shadow mode: heads loaded, predictions logged, nothing steered.
SHADOW_EXTRA: Dict[str, Any] = {
    "prior_head_path": "v7_prior_head.json",
    "value_head_path": "v7_value_head.json",
    "prior_head_weight": 0.0,
    "value_head_live": False,
}


def build(source: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(source)
    out["name"] = f"{source.get('name', 'gen16_cohort')}_r3"
    out["cohort_design"] = dict(source.get("cohort_design") or {})
    out["cohort_design"]["run"] = 3
    out["cohort_design"]["heads"] = dict(SHADOW_EXTRA)
    out["cohort_design"]["note"] = (
        "Run 3: same 26 slots as runs 1-2; image carries the day-2 tracks (synthesis "
        "ladder, oracle reconciliation refresh + static gate, V7 heads in shadow mode). "
        "Genomes differ from generation_16_population.json only in epistemic_policy.extra.")
    for firm in out["population"]:
        policy = dict(firm.get("epistemic_policy") or {})
        # `_Model` serialises `extra` FLAT: unknown keys inside `epistemic_policy`
        # are collected into `EpistemicPolicyGene.extra` on load, and `to_dict`
        # writes them back at the same level. A nested `"extra": {...}` would
        # load as `extra = {"extra": {...}}`.
        policy.update(SHADOW_EXTRA)
        firm["epistemic_policy"] = policy
        # Validate: the genome must still load, and the keys must land in `extra`.
        genome = CompanyGenome.from_dict(firm)
        loaded = dict(genome.epistemic_policy.extra)
        missing = {k: v for k, v in SHADOW_EXTRA.items() if loaded.get(k) != v}
        if missing:
            raise ValueError(f"{firm['company_id']}: head keys did not load into extra: {missing}")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--source", default=DEFAULT_SOURCE)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    with open(args.source, "r", encoding="utf-8") as fh:
        source = json.load(fh)
    out = build(source)
    firms = out["population"]
    print(f"{len(firms)} firms; extra={json.dumps(SHADOW_EXTRA, sort_keys=True)}")
    if args.dry_run:
        return 0
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1)
        fh.write("\n")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
