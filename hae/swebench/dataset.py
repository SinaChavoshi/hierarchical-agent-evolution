"""SWE-bench records: fetching, the firm-facing task, grading information, dev-slice selection.

Two objects, deliberately separate
----------------------------------
`SweTask` is what a firm is allowed to see: the instance id, the repository,
the commit to work from, the version, the problem statement, its date and the
environment-setup commit. `hints_text` (the issue's discussion thread) is kept
*empty* unless the loader is told otherwise, because the thread often
paraphrases the fix.

`GradingInfo` is everything that would let a firm cheat -- the gold `patch`,
the `test_patch`, `FAIL_TO_PASS` and `PASS_TO_PASS` -- and it is loaded only by
`load_grading_info`, which nothing on the firm's code path calls (asserted by
`tests/test_swebench_dataset.py`, which scans the runtime modules for those
names). The gatekeeper's test selection in executor mode is a proximity
heuristic on file names for the same reason; see
`EvidenceGatekeeper._proximity_tests`.

Fetching
--------
Rows come from the HuggingFace datasets-server REST API, paginated 100 at a
time, with no third-party dependency (`datasets`/`pyarrow` are not in the
image). The API truncates oversized cells and marks them in `truncated_cells`;
the fetch records that count and the caller decides whether to accept it.

Splits
------
`princeton-nlp/SWE-bench` dev (225) and `princeton-nlp/SWE-bench_Lite` dev (23)
are the evolution pool; `princeton-nlp/SWE-bench_Verified` test (500) is the
held-out benchmark. Verified is drawn from the SWE-bench *test* split, so the
dev slice is disjoint from it by construction -- `scripts/check_swebench_splits.py`
checks anyway and prints the numbers.
"""

from __future__ import annotations

import json
import os
import random
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, fields
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set

HF_ROWS_URL = "https://datasets-server.huggingface.co/rows"
HF_PAGE = 100

# short name -> (dataset, split). Files land in data/swebench/<short>.jsonl.
SPLITS: Dict[str, tuple] = {
    "swebench_dev": ("princeton-nlp/SWE-bench", "dev"),
    "lite_dev": ("princeton-nlp/SWE-bench_Lite", "dev"),
    "verified_test": ("princeton-nlp/SWE-bench_Verified", "test"),
}
DATA_DIR = os.path.join("data", "swebench")
DEV_SLICE_FILE = "dev_slice_50.json"

TASK_FIELDS = ("instance_id", "repo", "base_commit", "version", "problem_statement",
               "created_at", "environment_setup_commit")
GRADING_FIELDS = ("patch", "test_patch", "FAIL_TO_PASS", "PASS_TO_PASS")

# Docker Hub naming used by the upstream harness (swebench/image_builder/image_spec.py,
# `ImageSpec.name`): f"{namespace}/sweb.eval.{arch}.{instance_id}:{tag}" with "__"
# replaced by "_1776_" ("docker hub doesn't allow dunders in image names") and
# lower-cased; amd64 images keep the historical "x86_64" label.
IMAGE_NAMESPACE = "swebench"
IMAGE_ARCH = "x86_64"
IMAGE_TAG = "latest"


def instance_image(instance_id: str, namespace: str = IMAGE_NAMESPACE,
                   tag: str = IMAGE_TAG, arch: str = IMAGE_ARCH) -> str:
    """The prebuilt instance image name for `instance_id` (see IMAGE_NAMESPACE)."""
    key = f"sweb.eval.{arch}.{instance_id}:{tag}"
    if namespace:
        key = f"{namespace}/{key}".replace("__", "_1776_")
    return key.lower()


# --------------------------------------------------------------------------- #
# Records
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class SweTask:
    """What the firm sees. No gold patch, no test patch, no test names."""

    instance_id: str
    repo: str
    base_commit: str
    version: str
    problem_statement: str
    created_at: str = ""
    environment_setup_commit: str = ""
    # Empty unless `from_record(..., include_hints=True)`; see module docstring.
    hints_text: str = ""

    @classmethod
    def from_record(cls, record: Mapping[str, Any], include_hints: bool = False) -> "SweTask":
        missing = [k for k in ("instance_id", "repo", "base_commit", "problem_statement") if not record.get(k)]
        if missing:
            raise ValueError(f"SWE-bench record lacks {missing}")
        return cls(
            instance_id=str(record["instance_id"]),
            repo=str(record["repo"]),
            base_commit=str(record["base_commit"]),
            version=str(record.get("version", "") or ""),
            problem_statement=str(record["problem_statement"]),
            created_at=str(record.get("created_at", "") or ""),
            environment_setup_commit=str(record.get("environment_setup_commit", "") or ""),
            hints_text=str(record.get("hints_text", "") or "") if include_hints else "",
        )

    @property
    def title(self) -> str:
        for line in self.problem_statement.splitlines():
            if line.strip():
                return line.strip()[:200]
        return self.instance_id

    @property
    def repo_name(self) -> str:
        return self.repo.split("/")[-1]

    @property
    def image(self) -> str:
        return instance_image(self.instance_id)

    def to_dict(self) -> Dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}


@dataclass(frozen=True)
class GradingInfo:
    """Grading-only fields. Loaded by `load_grading_info` and by nothing the firm runs."""

    instance_id: str
    patch: str
    test_patch: str
    FAIL_TO_PASS: List[str] = field(default_factory=list)
    PASS_TO_PASS: List[str] = field(default_factory=list)

    @staticmethod
    def _names(raw: Any) -> List[str]:
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except ValueError:
                return [raw] if raw.strip() else []
        return [str(x) for x in (raw or [])]

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "GradingInfo":
        return cls(instance_id=str(record.get("instance_id", "")),
                   patch=str(record.get("patch", "") or ""),
                   test_patch=str(record.get("test_patch", "") or ""),
                   FAIL_TO_PASS=cls._names(record.get("FAIL_TO_PASS")),
                   PASS_TO_PASS=cls._names(record.get("PASS_TO_PASS")))


# --------------------------------------------------------------------------- #
# Fetching (HuggingFace datasets-server, stdlib only)
# --------------------------------------------------------------------------- #

def _http_json(url: str, timeout: float = 120.0, retries: int = 4) -> Dict[str, Any]:
    last: Optional[Exception] = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "hae-swebench-fetch/0.1"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError) as exc:
            last = exc
            import time
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"HF datasets-server request failed after {retries} attempts: {url}: {last}")


def fetch_split(dataset: str, split: str, config: str = "default", page: int = HF_PAGE,
                fetch=_http_json, logger=None) -> Dict[str, Any]:
    """All rows of one split via the `/rows` endpoint. Returns {rows, num_rows_total, truncated_rows}."""
    rows: List[Dict[str, Any]] = []
    truncated = 0
    offset = 0
    total: Optional[int] = None
    while True:
        qs = urllib.parse.urlencode({"dataset": dataset, "config": config, "split": split,
                                     "offset": offset, "length": page})
        data = fetch(f"{HF_ROWS_URL}?{qs}")
        batch = data.get("rows") or []
        if total is None:
            total = int(data.get("num_rows_total") or 0)
        for item in batch:
            if item.get("truncated_cells"):
                truncated += 1
            rows.append(dict(item.get("row") or {}))
        if logger:
            logger(f"[fetch] {dataset}:{split} {len(rows)}/{total}")
        offset += len(batch)
        if not batch or (total is not None and offset >= total):
            break
    return {"rows": rows, "num_rows_total": total or len(rows), "truncated_rows": truncated}


# --------------------------------------------------------------------------- #
# Files
# --------------------------------------------------------------------------- #

def write_jsonl(path: str, records: Iterable[Mapping[str, Any]]) -> int:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    n = 0
    with open(path, "w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(dict(rec), ensure_ascii=False, sort_keys=True) + "\n")
            n += 1
    return n


def read_jsonl(path: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def instance_ids(path: str) -> Set[str]:
    return {str(r.get("instance_id", "")) for r in read_jsonl(path) if r.get("instance_id")}


def load_tasks(path: str, include_hints: bool = False) -> List[SweTask]:
    """Every record of a split file as a `SweTask` (gold columns dropped on the way in)."""
    return [SweTask.from_record(r, include_hints=include_hints) for r in read_jsonl(path)]


def find_task(path: str, instance_id: str, include_hints: bool = False) -> SweTask:
    for rec in read_jsonl(path):
        if str(rec.get("instance_id")) == instance_id:
            return SweTask.from_record(rec, include_hints=include_hints)
    raise KeyError(f"{instance_id} is not in {path}")


def load_grading_info(path: str, instance_id: str) -> GradingInfo:
    """GRADING ONLY. Reads the gold patch and the hidden test names for one instance.

    Not imported by `hae.swebench.runner`, `hae.swebench.task`,
    `hae.swebench.export` or the gatekeeper; a firm never sees these.
    """
    for rec in read_jsonl(path):
        if str(rec.get("instance_id")) == instance_id:
            return GradingInfo.from_record(rec)
    raise KeyError(f"{instance_id} is not in {path}")


# --------------------------------------------------------------------------- #
# Dev slice
# --------------------------------------------------------------------------- #

def select_dev_slice(records: Sequence[Mapping[str, Any]], n: int = 50, seed: int = 0,
                     eligible: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    """Deterministic, repo-balanced round-robin pick of `n` instance ids.

    Records are de-duplicated by instance id, grouped by `repo`, each group
    shuffled with `random.Random(seed)` (groups visited in sorted repo order,
    so the shuffle is reproducible), then taken one per repo per round until
    `n` are chosen or the pool is empty. `eligible`, when given, restricts the
    pool first (e.g. to instances whose prebuilt image exists on Docker Hub).
    Returns ids, per-repo counts and the parameters, ready to be written as JSON.
    """
    pool: Dict[str, Mapping[str, Any]] = {}
    for rec in records:
        iid = str(rec.get("instance_id", ""))
        if iid and iid not in pool:
            pool[iid] = rec
    allowed: Optional[Set[str]] = set(eligible) if eligible is not None else None
    by_repo: Dict[str, List[str]] = {}
    for iid in sorted(pool):
        if allowed is not None and iid not in allowed:
            continue
        by_repo.setdefault(str(pool[iid].get("repo", "")), []).append(iid)
    rng = random.Random(seed)
    for repo in sorted(by_repo):
        rng.shuffle(by_repo[repo])
    picked: List[str] = []
    order = sorted(by_repo)
    while len(picked) < n and any(by_repo[r] for r in order):
        for repo in order:
            if len(picked) >= n:
                break
            if by_repo[repo]:
                picked.append(by_repo[repo].pop(0))
    counts: Dict[str, int] = {}
    for iid in picked:
        counts[str(pool[iid].get("repo", ""))] = counts.get(str(pool[iid].get("repo", "")), 0) + 1
    return {"n": len(picked), "requested": int(n), "seed": int(seed),
            "pool_size": sum(1 for iid in pool if allowed is None or iid in allowed),
            "instance_ids": picked, "repo_counts": dict(sorted(counts.items()))}


def write_dev_slice(path: str, slice_info: Mapping[str, Any]) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(dict(slice_info), fh, indent=1, sort_keys=True)
        fh.write("\n")


def read_dev_slice(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)
