#!/usr/bin/env python3
"""V17 training-set expansion: select & optionally screen SWE-bench Verified tasks disjoint from test.

Hard invariant:
  - `test` (28 tasks) and `val` (10 tasks) are identical to `data/swebench/v15_splits.json`.
  - No task in `v15_splits.json["test"]` or `data/swebench/verified_slice_30.json` may EVER appear in
    `train` or `stage1`.

Subcommands:
  build   Deterministically select Stratified Verified tasks (disjoint from test) and write
          `data/swebench/v17_splits.json` (21 dev train + N verified train = ~40 train tasks).
  update  Given one or more evaluation directories on candidate tasks, pick the most discriminative
          tasks (those with partial progress, false-pass verdicts, or disagreement across arms) for
          `train` and `stage1` and update `data/swebench/v17_splits.json`.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import random
from typing import Any, Dict, List, Set

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
V15_SPLITS = os.path.join(REPO_ROOT, "data", "swebench", "v15_splits.json")
SLICE_30 = os.path.join(REPO_ROOT, "data", "swebench", "verified_slice_30.json")
VERIFIED_JSONL = os.path.join(REPO_ROOT, "data", "swebench", "verified_test.jsonl")
V17_SPLITS = os.path.join(REPO_ROOT, "data", "swebench", "v17_splits.json")


def forbidden_test_ids() -> Set[str]:
    v15 = json.load(open(V15_SPLITS, encoding="utf-8"))
    s30 = json.load(open(SLICE_30, encoding="utf-8"))
    s30_ids = set(s30["instance_ids"] if isinstance(s30, dict) else s30)
    return set(v15["test"]) | set(v15["val"]) | s30_ids


def select_verified_candidates(n_target: int = 20, seed: int = 17) -> List[str]:
    """Stratified selection of SWE-bench Verified tasks strictly disjoint from test/val."""
    forbid = forbidden_test_ids()
    v15 = json.load(open(V15_SPLITS, encoding="utf-8"))
    forbid |= set(v15["train"])

    by_repo: Dict[str, List[Dict[str, Any]]] = collections.defaultdict(list)
    with open(VERIFIED_JSONL, encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            iid = row["instance_id"]
            if iid in forbid:
                continue
            f2p = row.get("FAIL_TO_PASS") or []
            if isinstance(f2p, str):
                try:
                    f2p = json.loads(f2p)
                except Exception:
                    f2p = [f2p]
            if not (1 <= len(f2p) <= 6):
                continue
            by_repo[row["repo"]].append(row)

    rng = random.Random(seed)
    for repo, rows in by_repo.items():
        # Prefer "15 min - 1 hour" tasks, then "<15 min fix", then others; deterministic tie-break.
        def _rank(r: Dict[str, Any]):
            diff = str(r.get("difficulty") or "")
            pri = 0 if diff == "15 min - 1 hour" else (1 if diff == "<15 min fix" else 2)
            return (pri, r["instance_id"])

        rows.sort(key=_rank)
        # Shuffle within the top difficulty tier using fixed seed for diversity across modules.
        tier0 = [r for r in rows if r.get("difficulty") == "15 min - 1 hour"]
        rest = [r for r in rows if r.get("difficulty") != "15 min - 1 hour"]
        rng.shuffle(tier0)
        by_repo[repo] = tier0 + rest

    picked: List[str] = []
    repos = sorted(by_repo.keys())
    idx = 0
    while len(picked) < n_target:
        progressed = False
        for repo in repos:
            if idx < len(by_repo[repo]) and len(picked) < n_target:
                picked.append(by_repo[repo][idx]["instance_id"])
                progressed = True
        if not progressed:
            break
        idx += 1
    return sorted(picked)


def build_splits(n_verified_train: int = 20, seed: int = 17, out_path: str = V17_SPLITS) -> Dict[str, Any]:
    v15 = json.load(open(V15_SPLITS, encoding="utf-8"))
    forbid = forbidden_test_ids()
    extra_train = select_verified_candidates(n_target=n_verified_train, seed=seed)
    assert not (set(extra_train) & forbid), "Leakage: candidate overlaps forbidden test/val set!"

    train = list(v15["train"]) + extra_train
    # Stage-1: 5 dev stage-1 tasks + 5 stratified verified train tasks = 10 fast discriminative tasks
    stage1_dev = list(v15["stage1"])[:5]
    stage1_ver = extra_train[:: max(1, len(extra_train) // 5)][:5]
    stage1 = stage1_dev + stage1_ver

    splits = {
        "version": "v17",
        "seed": seed,
        "notes": (
            "V17 splits: 21 swebench_dev train tasks + 20 stratified SWE-bench Verified train tasks "
            "(strictly disjoint from the 28 test tasks and verified_slice_30.json). "
            "val (10) and test (28) are identical to v15_splits.json."
        ),
        "datasets": {
            "train": "data/swebench/swebench_dev.jsonl",
            "val": "data/swebench/swebench_dev.jsonl",
            "test": "data/swebench/verified_test.jsonl",
        },
        "stage1": stage1,
        "train": train,
        "train_dev": list(v15["train"]),
        "train_verified": extra_train,
        "val": list(v15["val"]),
        "test": list(v15["test"]),
    }
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(splits, fh, indent=2)
    return splits


def update_from_screening(screen_dirs: List[str], n_stage1: int = 10, out_path: str = V17_SPLITS) -> Dict[str, Any]:
    """Rank training tasks by discriminativeness across screening runs and refresh `stage1`."""
    splits = json.load(open(out_path, encoding="utf-8"))
    forbid = forbidden_test_ids()
    train = list(splits["train"])
    assert not (set(train) & forbid), "Leakage in train split!"

    by_task: Dict[str, List[Dict[str, Any]]] = collections.defaultdict(list)
    for d in screen_dirs:
        st_path = os.path.join(d, "train_state.json")
        if not os.path.exists(st_path):
            continue
        for gid, gd in json.load(open(st_path, encoding="utf-8")).items():
            for tid, r in (gd.get("results") or {}).items():
                if tid in train:
                    by_task[tid].append(r)

    def _disc_score(tid: str) -> tuple:
        rs = by_task.get(tid) or []
        if not rs:
            return (0, 0, 0, tid)
        resolves = [bool(r.get("resolved")) for r in rs]
        # Highest priority: tasks where arms disagree (some resolve, some fail)
        disagree = int(any(resolves) and not all(resolves))
        # Second priority: tasks with red-team false positives (rt_fp > 0) or partial f2p progress
        fp = sum(int(r.get("rt_fp", 0)) for r in rs)
        partial = int(any(0.0 < float(r.get("f2p_rate", 0.0)) < 1.0 for r in rs))
        non_trivial_solve = int(any(resolves))
        return (disagree, fp > 0, partial, non_trivial_solve, tid)

    ranked = sorted(train, key=_disc_score, reverse=True)
    splits["stage1"] = sorted(ranked[:n_stage1])
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(splits, fh, indent=2)
    return splits


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_build = sub.add_parser("build")
    p_build.add_argument("--n-verified", type=int, default=20)
    p_build.add_argument("--seed", type=int, default=17)
    p_build.add_argument("--out", default=V17_SPLITS)

    p_upd = sub.add_parser("update")
    p_upd.add_argument("--dirs", nargs="+", required=True)
    p_upd.add_argument("--n-stage1", type=int, default=10)
    p_upd.add_argument("--out", default=V17_SPLITS)

    args = ap.parse_args()
    if args.cmd == "build":
        s = build_splits(args.n_verified, args.seed, args.out)
        print(f"Wrote {args.out}: train={len(s['train'])} (dev={len(s['train_dev'])}, "
              f"verified={len(s['train_verified'])}), stage1={len(s['stage1'])}, "
              f"val={len(s['val'])}, test={len(s['test'])}")
        return 0
    s = update_from_screening(args.dirs, args.n_stage1, args.out)
    print(f"Updated stage1 ({len(s['stage1'])} tasks): {s['stage1']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
