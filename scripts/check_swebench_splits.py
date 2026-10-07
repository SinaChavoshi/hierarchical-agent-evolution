#!/usr/bin/env python3
"""Assert the evolution pool and the held-out benchmark share no instance.

Reads `data/swebench/dev_slice_50.json` (the committed slice), the fetched dev
split files when present (`swebench_dev.jsonl`, `lite_dev.jsonl`) and the
held-out `verified_test.jsonl`, and exits non-zero if any id of the pool
appears in Verified. Verified is drawn from SWE-bench's *test* split, so the
expected overlap is zero -- this script exists so that the claim is checked
rather than remembered.

    python3 scripts/check_swebench_splits.py            # data/swebench/
    python3 scripts/check_swebench_splits.py --data-dir /path/to/files

Output is one line per file with its id count, then the overlap counts.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Dict, List, Optional, Set

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hae.swebench.dataset import DATA_DIR, DEV_SLICE_FILE, instance_ids, read_dev_slice  # noqa: E402

POOL_FILES = ("swebench_dev.jsonl", "lite_dev.jsonl")
HELD_OUT_FILE = "verified_test.jsonl"


def collect_ids(data_dir: str) -> Dict[str, Optional[Set[str]]]:
    """Id sets keyed by file name; `None` marks a file that is not present."""
    out: Dict[str, Optional[Set[str]]] = {}
    slice_path = os.path.join(data_dir, DEV_SLICE_FILE)
    if os.path.exists(slice_path):
        out[DEV_SLICE_FILE] = {str(i) for i in read_dev_slice(slice_path).get("instance_ids", [])}
    else:
        out[DEV_SLICE_FILE] = None
    for name in POOL_FILES + (HELD_OUT_FILE,):
        path = os.path.join(data_dir, name)
        out[name] = instance_ids(path) if os.path.exists(path) else None
    return out


def check_splits(ids: Dict[str, Optional[Set[str]]]) -> Dict[str, object]:
    """Pure check over the id sets. Returns counts, overlaps and an `ok` flag.

    `ok` is False when the held-out file is missing (nothing to check against),
    when the slice file is missing, or when any pool id is in the held-out set.
    The Lite dev split is also checked to be a subset of the full dev split
    when both are present (it should be; a violation means the files are
    from different dataset revisions).
    """
    held = ids.get(HELD_OUT_FILE)
    report: Dict[str, object] = {
        "counts": {name: (len(s) if s is not None else None) for name, s in ids.items()},
        "overlaps": {},
        "lite_subset_of_dev": None,
        "ok": True,
        "problems": [],
    }
    problems: List[str] = report["problems"]  # type: ignore[assignment]
    if held is None:
        problems.append(f"{HELD_OUT_FILE} missing: run scripts/fetch_swebench.py first")
    if ids.get(DEV_SLICE_FILE) is None:
        problems.append(f"{DEV_SLICE_FILE} missing")
    for name in (DEV_SLICE_FILE,) + POOL_FILES:
        pool = ids.get(name)
        if pool is None or held is None:
            continue
        overlap = sorted(pool & held)
        report["overlaps"][name] = len(overlap)  # type: ignore[index]
        if overlap:
            problems.append(f"{name} shares {len(overlap)} id(s) with {HELD_OUT_FILE}: "
                            + ", ".join(overlap[:5]) + (" ..." if len(overlap) > 5 else ""))
    dev, lite = ids.get("swebench_dev.jsonl"), ids.get("lite_dev.jsonl")
    if dev is not None and lite is not None:
        report["lite_subset_of_dev"] = lite <= dev
        if not lite <= dev:
            problems.append("lite_dev.jsonl is not a subset of swebench_dev.jsonl")
    slice_ids, dev_ids = ids.get(DEV_SLICE_FILE), ids.get("swebench_dev.jsonl")
    if slice_ids is not None and dev_ids is not None and not slice_ids <= dev_ids:
        problems.append(f"{DEV_SLICE_FILE} contains ids not in swebench_dev.jsonl")
    report["ok"] = not problems
    return report


def render(report: Dict[str, object]) -> str:
    lines = []
    for name, count in report["counts"].items():  # type: ignore[union-attr]
        lines.append(f"{name:24s} {'(missing)' if count is None else str(count) + ' ids'}")
    for name, n in report["overlaps"].items():  # type: ignore[union-attr]
        lines.append(f"overlap {name} ∩ {HELD_OUT_FILE}: {n}")
    if report["lite_subset_of_dev"] is not None:
        lines.append(f"lite_dev ⊆ swebench_dev: {report['lite_subset_of_dev']}")
    for p in report["problems"]:  # type: ignore[union-attr]
        lines.append(f"PROBLEM: {p}")
    lines.append("OK: evolution pool and Verified are disjoint" if report["ok"] else "FAILED")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data-dir", default=DATA_DIR)
    args = ap.parse_args(argv)
    report = check_splits(collect_ids(args.data_dir))
    print(render(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
