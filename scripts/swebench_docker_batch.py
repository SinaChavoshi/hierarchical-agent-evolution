#!/usr/bin/env python3
"""Run a batch of SWE-bench instances, one prebuilt Docker container each.

For every instance id (from `data/swebench/dev_slice_50.json` by default):

    docker pull swebench/sweb.eval.x86_64.<iid with __ -> _1776_>:latest
    docker run -d --name hae_<run>_<iid> <image> sleep infinity
    python3 scripts/run_swebench_instance.py --instance-id <iid> \
        --executor docker --container hae_<run>_<iid> ... --out-dir <out_root>/<iid>/
    docker rm -f hae_<run>_<iid>

then merges every `<out_root>/<iid>/prediction.json` into `<out_root>/preds.jsonl`,
the file `swebench eval` consumes. The batch is resumable: instances whose
directory already holds a `prediction.json` are skipped. Instances whose
image the fetch script found unpublished on Docker Hub are skipped unless
`--include-unpublished` (they need `swebench build`, see upstream README).

    python3 scripts/swebench_docker_batch.py --run-id v8c_smoke --genome <firm.json> --n 5 --workers 2
    python3 scripts/swebench_docker_batch.py --run-id v8c_smoke --genome <firm.json> --dry-run

Requires the Docker CLI on this host; nothing here was executed where Docker
was unavailable (the argv builders are unit-tested instead).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from hae.swebench.dataset import DATA_DIR, DEV_SLICE_FILE, instance_image, read_dev_slice  # noqa: E402
from hae.swebench.export import merge_predictions  # noqa: E402

INSTANCE_SCRIPT = os.path.join(REPO_ROOT, "scripts", "run_swebench_instance.py")
DEFAULT_DATASET = os.path.join(DATA_DIR, "swebench_dev.jsonl")
DEFAULT_SLICE = os.path.join(DATA_DIR, DEV_SLICE_FILE)
DEFAULT_INSTANCE_TIMEOUT_S = 3600
PULL_TIMEOUT_S = 1800
DOCKER_TIMEOUT_S = 120


# --------------------------------------------------------------------------- #
# Pure argv builders (unit-tested; no Docker needed)
# --------------------------------------------------------------------------- #

def image_name(instance_id: str) -> str:
    return instance_image(instance_id)


def container_name(instance_id: str, run_id: str = "") -> str:
    """`hae_<run>_<iid>` restricted to Docker's [a-zA-Z0-9][a-zA-Z0-9_.-] alphabet."""
    raw = f"hae_{run_id}_{instance_id}" if run_id else f"hae_{instance_id}"
    return "".join(ch if (ch.isalnum() or ch in "_.-") else "_" for ch in raw)


def docker_pull_argv(image: str, docker: str = "docker") -> List[str]:
    return [docker, "pull", image]


def docker_run_argv(image: str, name: str, docker: str = "docker") -> List[str]:
    return [docker, "run", "-d", "--name", name, image, "sleep", "infinity"]


def docker_rm_argv(name: str, docker: str = "docker") -> List[str]:
    return [docker, "rm", "-f", name]


def instance_argv(instance_id: str, dataset: str, container: str, genome: str, out_dir: str,
                  extra: Sequence[str] = (), python: str = "") -> List[str]:
    return [python or sys.executable, INSTANCE_SCRIPT,
            "--instance-id", instance_id, "--dataset", dataset,
            "--executor", "docker", "--container", container,
            "--genome", genome, "--out-dir", out_dir] + list(extra)


def select_instances(slice_info: Mapping[str, Any], n: Optional[int] = None,
                     explicit: Sequence[str] = (), include_unpublished: bool = False) -> Dict[str, List[str]]:
    """Ids to run, in slice order, split into `run` and `skipped_unpublished`.

    `explicit` ids bypass the slice entirely (and the publication filter: the
    caller asked for them by name). `image_published` in the slice file is
    `True`/`False`/`None` (unknown); only an explicit `False` skips.
    """
    if explicit:
        return {"run": list(dict.fromkeys(explicit)), "skipped_unpublished": []}
    ids = [str(i) for i in slice_info.get("instance_ids", [])]
    published = slice_info.get("image_published") or {}
    run, skipped = [], []
    for iid in ids:
        if not include_unpublished and published.get(iid) is False:
            skipped.append(iid)
        else:
            run.append(iid)
    if n is not None:
        run = run[: max(0, int(n))]
    return {"run": run, "skipped_unpublished": skipped}


def pending(out_root: str, ids: Iterable[str]) -> List[str]:
    """Ids without a `prediction.json` under `out_root/<iid>/` (resumability)."""
    return [i for i in ids if not os.path.exists(os.path.join(out_root, i, "prediction.json"))]


def merge_preds(out_root: str, ids: Iterable[str], out_path: Optional[str] = None) -> Dict[str, Any]:
    out_path = out_path or os.path.join(out_root, "preds.jsonl")
    paths = [os.path.join(out_root, i, "prediction.json") for i in ids]
    n = merge_predictions(paths, out_path)
    return {"path": out_path, "count": n}


# --------------------------------------------------------------------------- #
# Execution
# --------------------------------------------------------------------------- #

Runner = Callable[[List[str], int, Optional[str]], Dict[str, Any]]


def host_run(argv: List[str], timeout_s: int, log_path: Optional[str] = None) -> Dict[str, Any]:
    """Runs `argv`, appending combined output to `log_path`; never raises on rc/timeouts."""
    started = time.time()
    try:
        proc = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, timeout=timeout_s, text=True,
                              errors="replace")
        rc, out, timed_out = proc.returncode, proc.stdout, False
    except subprocess.TimeoutExpired as exc:
        partial = exc.stdout or ""
        if isinstance(partial, bytes):
            partial = partial.decode("utf-8", "replace")
        rc, out, timed_out = 124, str(partial), True
    except OSError as exc:
        rc, out, timed_out = 127, f"{type(exc).__name__}: {exc}\n", False
    if log_path:
        os.makedirs(os.path.dirname(os.path.abspath(log_path)), exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(f"\n$ {' '.join(argv)}\n{out}\n[rc={rc} timed_out={timed_out} elapsed={time.time() - started:.1f}s]\n")
    return {"rc": rc, "output": out, "timed_out": timed_out, "elapsed_s": round(time.time() - started, 1)}


def run_one(instance_id: str, args: argparse.Namespace, runner: Runner = host_run) -> Dict[str, Any]:
    """pull -> run container -> instance script -> rm. Returns a status row."""
    out_dir = os.path.join(args.out_root, instance_id)
    log_path = os.path.join(out_dir, "batch.log")
    image = image_name(instance_id)
    name = container_name(instance_id, args.run_id)
    row: Dict[str, Any] = {"instance_id": instance_id, "image": image, "container": name, "steps": {}}
    if args.dry_run:
        row["argv"] = [docker_pull_argv(image, args.docker), docker_run_argv(image, name, args.docker),
                       instance_argv(instance_id, args.dataset, name, args.genome, out_dir, args.extra),
                       docker_rm_argv(name, args.docker)]
        row["status"] = "dry-run"
        return row
    os.makedirs(out_dir, exist_ok=True)
    try:
        if args.pull:
            pull = runner(docker_pull_argv(image, args.docker), PULL_TIMEOUT_S, log_path)
            row["steps"]["pull"] = pull["rc"]
            if pull["rc"] != 0:
                row["status"] = "pull_failed"
                return row
        runner(docker_rm_argv(name, args.docker), DOCKER_TIMEOUT_S, log_path)  # stale container from a previous attempt
        started = runner(docker_run_argv(image, name, args.docker), DOCKER_TIMEOUT_S, log_path)
        row["steps"]["run"] = started["rc"]
        if started["rc"] != 0:
            row["status"] = "container_failed"
            return row
        res = runner(instance_argv(instance_id, args.dataset, name, args.genome, out_dir, args.extra),
                     args.instance_timeout_s, log_path)
        row["steps"]["instance"] = res["rc"]
        row["elapsed_s"] = res["elapsed_s"]
        has_pred = os.path.exists(os.path.join(out_dir, "prediction.json"))
        row["status"] = "ok" if res["rc"] == 0 and has_pred else ("timeout" if res["timed_out"] else "instance_failed")
        return row
    finally:
        if not args.dry_run and not args.keep_containers:
            runner(docker_rm_argv(name, args.docker), DOCKER_TIMEOUT_S, log_path)


def run_batch(args: argparse.Namespace, runner: Runner = host_run) -> Dict[str, Any]:
    slice_info = read_dev_slice(args.slice) if (os.path.exists(args.slice) and not args.instances) else {}
    selected = select_instances(slice_info, n=args.n, explicit=args.instances or (),
                                include_unpublished=args.include_unpublished)
    todo = pending(args.out_root, selected["run"]) if not args.dry_run else list(selected["run"])
    done_before = [i for i in selected["run"] if i not in todo]
    summary: Dict[str, Any] = {
        "run_id": args.run_id, "out_root": args.out_root, "selected": len(selected["run"]),
        "skipped_unpublished": selected["skipped_unpublished"], "already_done": done_before,
        "rows": [], "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    lock = threading.Lock()

    def work(iid: str) -> None:
        row = run_one(iid, args, runner)
        with lock:
            summary["rows"].append(row)
            print(json.dumps({k: row.get(k) for k in ("instance_id", "status", "steps", "elapsed_s")}), flush=True)

    if todo:
        with ThreadPoolExecutor(max_workers=max(1, int(args.workers))) as pool:
            list(pool.map(work, todo))
    if not args.dry_run:
        os.makedirs(args.out_root, exist_ok=True)
        summary["merged"] = merge_preds(args.out_root, selected["run"])
        statuses = {}
        for row in summary["rows"]:
            statuses[row["status"]] = statuses.get(row["status"], 0) + 1
        summary["status_counts"] = statuses
        with open(os.path.join(args.out_root, "batch_status.json"), "w", encoding="utf-8") as fh:
            json.dump(summary, fh, indent=1, sort_keys=True)
            fh.write("\n")
    return summary


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run-id", required=True, help="names the result directory and the containers")
    ap.add_argument("--genome", required=True, help="CompanyGenome JSON for the firm")
    ap.add_argument("--dataset", default=DEFAULT_DATASET)
    ap.add_argument("--slice", default=DEFAULT_SLICE, help="dev slice JSON listing instance_ids")
    ap.add_argument("--instances", nargs="*", default=None, help="explicit instance ids (bypasses --slice)")
    ap.add_argument("--n", type=int, default=None, help="run only the first N selected instances")
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--out-root", default=None, help="default results/swebench/<run-id>")
    ap.add_argument("--instance-timeout-s", type=int, default=DEFAULT_INSTANCE_TIMEOUT_S)
    ap.add_argument("--include-unpublished", action="store_true",
                    help="also run instances whose image was not found on Docker Hub (build it first)")
    ap.add_argument("--no-pull", dest="pull", action="store_false", help="assume images are present locally")
    ap.add_argument("--keep-containers", action="store_true")
    ap.add_argument("--docker", default="docker")
    ap.add_argument("--dry-run", action="store_true", help="print the commands and exit")
    ap.add_argument("extra", nargs="*", help="passed through to run_swebench_instance.py after '--'")
    args = ap.parse_args(list(argv) if argv is not None else None)
    args.out_root = args.out_root or os.path.join("results", "swebench", args.run_id)
    return args


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    summary = run_batch(args)
    if args.dry_run:
        for row in summary["rows"]:
            for cmd in row["argv"]:
                print(" ".join(cmd))
        print(f"[dry-run] {len(summary['rows'])} instance(s); skipped unpublished: {len(summary['skipped_unpublished'])}")
        return 0
    merged = summary["merged"]
    print(f"{merged['count']} prediction(s) -> {merged['path']}  (status: {summary['status_counts']}; "
          f"skipped unpublished: {len(summary['skipped_unpublished'])}; already done: {len(summary['already_done'])})")
    print(f"grade with: swebench eval verified -p {merged['path']} --run-id {args.run_id}  "
          f"(dev instances: use the SWE-bench dev dataset instead of 'verified')")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
