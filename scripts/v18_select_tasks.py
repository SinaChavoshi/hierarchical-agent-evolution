#!/usr/bin/env python3
"""V18 Benchmark Construction: Build a fresh, offline-screened N=80 SWE-bench Verified evaluation set.

Hard invariants:
  1. Every task ever touched in V14, V15, V16, or V17 (`v15_splits.json` train/val/test,
     `v17_splits.json` train/val/test, and `verified_slice_30.json` = 81 tasks total) is
     permanently retired into `dev_retired` and FORBIDDEN from `test` (`eval_80`).
  2. Every task in `test` (`eval_80`) has standard pytest node IDs (`path/to/test_*.py::test_name`)
     for both `FAIL_TO_PASS` and `PASS_TO_PASS` so `scripts/grade_in_testbed.py` grades accurately
     without false negatives from non-pytest runners (`django/runtests.py`, `sympy/bin/test`).
  3. Every task in `test` (`eval_80`) is screened against external live-network dependencies
     (e.g., `httpbin.org`) in `test_patch`.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import random
from typing import Any, Dict, List, Set, Tuple

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
V15_SPLITS = os.path.join(REPO_ROOT, "data", "swebench", "v15_splits.json")
V17_SPLITS = os.path.join(REPO_ROOT, "data", "swebench", "v17_splits.json")
SLICE_30 = os.path.join(REPO_ROOT, "data", "swebench", "verified_slice_30.json")
VERIFIED_JSONL = os.path.join(REPO_ROOT, "data", "swebench", "verified_test.jsonl")
V18_SPLITS = os.path.join(REPO_ROOT, "data", "swebench", "v18_splits.json")


def _load_json(path: str) -> Any:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def retired_dev_ids() -> Set[str]:
    """Return the complete set of 81 tasks ever used across V14, V15, V16, and V17."""
    v15 = _load_json(V15_SPLITS)
    v17 = _load_json(V17_SPLITS)
    s30 = _load_json(SLICE_30)
    s30_ids = set(s30["instance_ids"] if isinstance(s30, dict) else s30)
    return (
        set(v15.get("train", []))
        | set(v15.get("val", []))
        | set(v15.get("test", []))
        | set(v17.get("train", []))
        | set(v17.get("val", []))
        | set(v17.get("test", []))
        | s30_ids
    )


def _parse_list_field(raw: Any) -> List[str]:
    if isinstance(raw, list):
        return [str(x) for x in raw]
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(x) for x in parsed]
        except Exception:
            return [raw] if raw else []
    return []


def select_fresh_verified_tasks(n_target: int = 80, seed: int = 18) -> Tuple[List[str], List[str], Dict[str, int]]:
    """Select `n_target` stratified fresh pytest-compatible SWE-bench Verified tasks."""
    forbid = retired_dev_ids()
    by_repo: Dict[str, List[Dict[str, Any]]] = collections.defaultdict(list)

    with open(VERIFIED_JSONL, encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            iid = str(row["instance_id"])
            if iid in forbid:
                continue
            tp = str(row.get("test_patch") or "").lower()
            if "httpbin" in tp:
                continue
            f2p = _parse_list_field(row.get("FAIL_TO_PASS"))
            p2p = _parse_list_field(row.get("PASS_TO_PASS"))
            if not (1 <= len(f2p) <= 8) or len(p2p) > 250:
                continue
            if not all("::" in t and t.split("::", 1)[0].endswith(".py") for t in f2p):
                continue
            if not all("::" in t and t.split("::", 1)[0].endswith(".py") for t in p2p):
                continue
            by_repo[str(row["repo"])].append(row)

    rng = random.Random(seed)
    for repo, rows in by_repo.items():
        # Prioritize "15 min - 1 hour" and "1-4 hours" (multi-locus / architectural tasks), then "<15 min fix".
        def _tier(r: Dict[str, Any]) -> int:
            d = str(r.get("difficulty") or "")
            if d == "15 min - 1 hour":
                return 0
            if d == "1-4 hours":
                return 1
            if d == "<15 min fix":
                return 2
            return 3

        rows.sort(key=lambda r: (_tier(r), r["instance_id"]))
        t0 = [r for r in rows if _tier(r) == 0]
        t1 = [r for r in rows if _tier(r) == 1]
        t2 = [r for r in rows if _tier(r) >= 2]
        rng.shuffle(t0)
        rng.shuffle(t1)
        rng.shuffle(t2)
        by_repo[repo] = t0 + t1 + t2

    picked: List[str] = []
    repo_counts: Dict[str, int] = collections.Counter()
    repos = sorted(by_repo.keys())
    idx = 0
    while len(picked) < n_target:
        progressed = False
        for repo in repos:
            if idx < len(by_repo[repo]) and len(picked) < n_target:
                picked.append(by_repo[repo][idx]["instance_id"])
                repo_counts[repo] += 1
                progressed = True
        if not progressed:
            break
        idx += 1

    picked_set = set(picked)
    reserve = sorted(
        r["instance_id"]
        for rows in by_repo.values()
        for r in rows
        if r["instance_id"] not in picked_set
    )
    return sorted(picked), reserve, dict(sorted(repo_counts.items()))


def build_v18_splits(n_eval: int = 80, seed: int = 18, out_path: str = V18_SPLITS) -> Dict[str, Any]:
    v17 = _load_json(V17_SPLITS)
    forbid = retired_dev_ids()
    eval_80, reserve, repo_counts = select_fresh_verified_tasks(n_target=n_eval, seed=seed)
    assert len(eval_80) == n_eval, f"Expected {n_eval} tasks, got {len(eval_80)}"
    assert not (set(eval_80) & forbid), "LEAKAGE ERROR: eval_80 overlaps retired V14-V17 tasks!"
    assert not (set(reserve) & forbid), "LEAKAGE ERROR: reserve overlaps retired V14-V17 tasks!"
    assert not (set(eval_80) & set(reserve)), "Overlap between eval_80 and reserve!"

    # Fast 24-task stratified subset of eval_80 (3 per repo where available) for stage-1 / fast checks
    by_prefix: Dict[str, List[str]] = collections.defaultdict(list)
    for iid in eval_80:
        by_prefix[iid.split("__", 1)[0]].append(iid)
    fast_24: List[str] = []
    r_idx = 0
    while len(fast_24) < min(24, len(eval_80)):
        progressed = False
        for pref in sorted(by_prefix.keys()):
            if r_idx < len(by_prefix[pref]) and len(fast_24) < 24:
                fast_24.append(by_prefix[pref][r_idx])
                progressed = True
        if not progressed:
            break
        r_idx += 1

    splits = {
        "version": "v18",
        "seed": seed,
        "notes": (
            "V18 fresh evaluation benchmark: all 81 previously touched tasks from V14-V17 are permanently "
            "retired to `dev_retired` (`train` + `val`). `test` (`eval_80`) contains 80 strictly disjoint, "
            "offline-screened, pytest-compatible SWE-bench Verified tasks across 8 repositories."
        ),
        "datasets": {
            "train": "data/swebench/swebench_dev.jsonl",
            "train_verified": "data/swebench/verified_test.jsonl",
            "val": "data/swebench/verified_test.jsonl",
            "test": "data/swebench/verified_test.jsonl",
        },
        "stage1": list(v17["stage1"]),
        "train": list(v17["train"]),
        "val": list(v17["test"]),  # The retired 28-task V14-V17 test set is now strictly validation/dev!
        "dev_retired": sorted(forbid),
        "test": eval_80,
        "eval_80": eval_80,
        "eval_24_stratified": sorted(fast_24),
        "reserve": reserve,
        "eval_80_repo_counts": repo_counts,
    }
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(splits, fh, indent=2)
    return splits


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--n-eval", type=int, default=80)
    ap.add_argument("--seed", type=int, default=18)
    ap.add_argument("--out", default=V18_SPLITS)
    args = ap.parse_args()
    s = build_v18_splits(n_eval=args.n_eval, seed=args.seed, out_path=args.out)
    print(
        f"Wrote {args.out}: test/eval_80={len(s['test'])}, eval_24_stratified={len(s['eval_24_stratified'])}, "
        f"dev_retired={len(s['dev_retired'])}, reserve={len(s['reserve'])}\n"
        f"Repo distribution: {s['eval_80_repo_counts']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
