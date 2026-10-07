#!/usr/bin/env python3
"""Fetch the SWE-bench splits used by v8 and build the committed dev slice.

Writes (stdlib only, HuggingFace datasets-server `/rows`, 100 rows per page):

    data/swebench/swebench_dev.jsonl    princeton-nlp/SWE-bench       dev   (225)
    data/swebench/lite_dev.jsonl        princeton-nlp/SWE-bench_Lite  dev   (23)
    data/swebench/verified_test.jsonl   princeton-nlp/SWE-bench_Verified test (500)
    data/swebench/dev_slice_50.json     deterministic repo-balanced slice of dev ∪ lite-dev

Only `dev_slice_50.json` and `README.md` are committed; the jsonl files stay
local (`.gitignore`) -- the Verified file is the held-out benchmark and must
never land in the repository.

`--check-images` asks Docker Hub whether `swebench/sweb.eval.x86_64.<id>` is
published for every dev instance and restricts the slice to published images
when at least `--n` of them are; the per-id answer is stored in the slice file
so a batch run knows which instances need `swebench images build` first.

Usage:
    python3 scripts/fetch_swebench.py [--data-dir data/swebench] [--n 50] [--seed 0]
                                      [--check-images] [--skip-verified]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Iterable, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hae.swebench import dataset as ds  # noqa: E402

DOCKER_HUB_TAGS = "https://hub.docker.com/v2/repositories/{repo}/tags?page_size=1"


def _log(msg: str) -> None:
    sys.stderr.write(msg + "\n")
    sys.stderr.flush()


def image_published(instance_id: str, timeout: float = 30.0, retries: int = 3) -> Optional[bool]:
    """True/False when Docker Hub answers 200/404 for the instance image, None when unknown."""
    repo = ds.instance_image(instance_id).split(":")[0]
    url = DOCKER_HUB_TAGS.format(repo=repo)
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "hae-swebench-fetch/0.1"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status == 200
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return False
            if exc.code == 429:
                time.sleep(5.0 * (attempt + 1))
                continue
            time.sleep(1.0 * (attempt + 1))
        except (urllib.error.URLError, TimeoutError):
            time.sleep(1.0 * (attempt + 1))
    return None


def check_images(instance_ids: Iterable[str], workers: int = 6) -> Dict[str, Optional[bool]]:
    ids = sorted(set(instance_ids))
    out: Dict[str, Optional[bool]] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for iid, ok in zip(ids, pool.map(image_published, ids)):
            out[iid] = ok
    return out


def fetch_all(data_dir: str, skip_verified: bool = False) -> Dict[str, Dict[str, int]]:
    summary: Dict[str, Dict[str, int]] = {}
    for short, (dataset_name, split) in ds.SPLITS.items():
        if skip_verified and short == "verified_test":
            continue
        path = os.path.join(data_dir, f"{short}.jsonl")
        result = ds.fetch_split(dataset_name, split, logger=_log)
        n = ds.write_jsonl(path, result["rows"])
        summary[short] = {"rows_written": n, "num_rows_total": int(result["num_rows_total"]),
                          "truncated_rows": int(result["truncated_rows"])}
        _log(f"[fetch] wrote {path}: {n} rows (server total {result['num_rows_total']}, "
             f"truncated cells in {result['truncated_rows']} rows)")
        if n != int(result["num_rows_total"]):
            raise SystemExit(f"{short}: wrote {n} rows but the server reports {result['num_rows_total']}")
        if result["truncated_rows"]:
            raise SystemExit(f"{short}: {result['truncated_rows']} rows had truncated cells; "
                             "re-run with a smaller page or fetch via the parquet files")
    return summary


def build_slice(data_dir: str, n: int, seed: int, do_check_images: bool) -> Dict[str, object]:
    dev = ds.read_jsonl(os.path.join(data_dir, "swebench_dev.jsonl"))
    lite = ds.read_jsonl(os.path.join(data_dir, "lite_dev.jsonl"))
    pool = dev + lite
    eligible = None
    published: Dict[str, Optional[bool]] = {}
    if do_check_images:
        published = check_images([r["instance_id"] for r in pool])
        yes = sorted(i for i, ok in published.items() if ok)
        _log(f"[images] {len(yes)} of {len(published)} dev images are published on Docker Hub "
             f"({sum(1 for ok in published.values() if ok is None)} unknown)")
        if len(yes) >= n:
            eligible = yes
        else:
            _log(f"[images] fewer than {n} published images; slicing over the whole pool instead")
    info = ds.select_dev_slice(pool, n=n, seed=seed, eligible=eligible)
    info["source_splits"] = {short: list(ds.SPLITS[short]) for short in ("swebench_dev", "lite_dev")}
    info["pool"] = {"swebench_dev": len(dev), "lite_dev": len(lite),
                    "lite_dev_subset_of_dev": {r["instance_id"] for r in lite} <= {r["instance_id"] for r in dev}}
    info["restricted_to_published_images"] = eligible is not None
    if published:
        info["image_published"] = {i: published.get(i) for i in info["instance_ids"]}
        info["image_published_counts"] = {
            "true": sum(1 for ok in published.values() if ok is True),
            "false": sum(1 for ok in published.values() if ok is False),
            "unknown": sum(1 for ok in published.values() if ok is None),
            "checked": len(published),
        }
    info["image_name_rule"] = "swebench/sweb.eval.x86_64.<instance_id with __ -> _1776_>:latest (lower-cased)"
    return info


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", default=ds.DATA_DIR)
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--check-images", action="store_true", help="query Docker Hub for every dev image")
    ap.add_argument("--skip-fetch", action="store_true", help="reuse existing jsonl files")
    ap.add_argument("--skip-verified", action="store_true")
    args = ap.parse_args(argv)

    os.makedirs(args.data_dir, exist_ok=True)
    summary: Dict[str, object] = {}
    if not args.skip_fetch:
        summary["fetch"] = fetch_all(args.data_dir, skip_verified=args.skip_verified)
    info = build_slice(args.data_dir, args.n, args.seed, args.check_images)
    slice_path = os.path.join(args.data_dir, ds.DEV_SLICE_FILE)
    ds.write_dev_slice(slice_path, info)
    summary["slice"] = {"path": slice_path, "n": info["n"], "repo_counts": info["repo_counts"],
                        "restricted_to_published_images": info["restricted_to_published_images"],
                        "image_published_counts": info.get("image_published_counts")}
    print(json.dumps(summary, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
