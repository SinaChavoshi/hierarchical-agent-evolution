"""Harvest a finished Gen 16 Job from Cloud Logging after its pods are gone.

`scripts/harvest_gen16_pilot.py` reads pod logs with `kubectl logs`, which
stops working once the Job's `ttlSecondsAfterFinished` deletes the pods. GKE
ships every container stdout line to Cloud Logging (30-day retention by
default), and each pod prints its `/data/outputs/generation_16` directory as a
base64 tarball between `=== OUTPUT_TGZ_B64 BEGIN/END ===` markers, so the full
run can be rebuilt from there.

For every pod of the Job this script pulls the entries in ascending order,
re-joins the text payloads into `<out-root>/pod_logs/pod_<idx>_<pod>.log`,
decodes the embedded tarball into `<out-root>/outputs/<company_id>/` and
writes `<out-root>/harvest_status.json` in the same shape as the live
harvester, so `scripts/summarize_gen16_cohort.py` runs unchanged.

Usage:
    python3 scripts/harvest_from_cloud_logging.py --job-name hae-gen16-v6-cohort-r3 \
        --population-file configs/generation_16_r3_population.json \
        --out-root results/hae_gen16_v6_cohort_r3 [--project gemle-gke-dev] [--freshness 3d]

Requires `gcloud` authenticated against the project that owns the cluster.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harvest_gen16_pilot import FIT_RE, extract_embedded, trajectory  # noqa: E402

POD_INDEX_RE = re.compile(r"-(\d+)-[a-z0-9]{5}$")


def gcloud_read(filter_expr: str, project: str, freshness: str, limit: int) -> list:
    cmd = ["gcloud", "logging", "read", filter_expr, "--order=asc", f"--freshness={freshness}",
           f"--limit={limit}", "--format=json"]
    if project:
        cmd.append(f"--project={project}")
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if r.returncode != 0:
        raise RuntimeError(f"gcloud logging read failed: {r.stderr.strip()[:500]}")
    return json.loads(r.stdout or "[]")


def list_pods(job_name: str, project: str, freshness: str) -> list:
    """Pod names of the Job, found through the worker banner every pod prints."""
    entries = gcloud_read(
        f'resource.type="k8s_container" AND resource.labels.pod_name:"{job_name}-" '
        f'AND textPayload:"WORKER_EXIT="', project, freshness, limit=5000)
    names = sorted({e["resource"]["labels"]["pod_name"] for e in entries})
    return [n for n in names if POD_INDEX_RE.search(n)]


def harvest_pod(pod: str, cid: str, out_root: str, project: str, freshness: str, limit: int) -> dict:
    idx = POD_INDEX_RE.search(pod).group(1)
    entries = gcloud_read(f'resource.type="k8s_container" AND resource.labels.pod_name="{pod}"',
                          project, freshness, limit)
    text = "\n".join(e.get("textPayload", "") for e in entries)
    log_root = os.path.join(out_root, "pod_logs")
    os.makedirs(log_root, exist_ok=True)
    with open(os.path.join(log_root, f"pod_{idx}_{pod}.log"), "w") as f:
        f.write(text)
    entry = {"pod": pod, "phase": "Succeeded", "trajectory": trajectory(text), "final": None,
             "epistemic_lines": sum(1 for ln in text.splitlines() if "[epistemic]" in ln or "EPISTEMIC" in ln),
             "worker_exit": None, "harvested": False, "last": "", "log_entries": len(entries)}
    m = FIT_RE.search(text)
    if m:
        entry["final"] = {"net": float(m.group(1)), "gross": float(m.group(2)),
                          "iterations": f"{m.group(3)}/{m.group(4)}"}
    mx = re.search(r"^WORKER_EXIT=(\d+)", text, re.M)
    entry["worker_exit"] = int(mx.group(1)) if mx else None
    if entry["worker_exit"] not in (None, 0):
        entry["phase"] = "Failed"
    tail = [ln for ln in text.splitlines() if ln.strip() and len(ln) < 400]
    entry["last"] = tail[-1][:160] if tail else ""
    try:
        entry["harvested"] = extract_embedded(text, os.path.join(out_root, "outputs", cid))
    except Exception as exc:  # a truncated tarball is a harvest failure, not a crash
        entry["harvest_error"] = f"{type(exc).__name__}: {exc}"
    return entry


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--job-name", required=True, help="Job name, e.g. hae-gen16-v6-cohort-r3")
    ap.add_argument("--population-file", required=True, help="population the Job indexed into")
    ap.add_argument("--out-root", required=True, help="local results directory")
    ap.add_argument("--project", default=None)
    ap.add_argument("--freshness", default="3d", help="how far back to search (gcloud --freshness)")
    ap.add_argument("--limit", type=int, default=400000, help="max log entries per pod")
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()

    pop = json.load(open(args.population_file))["population"]
    idx_to_cid = {str(i): g["company_id"] for i, g in enumerate(pop)}
    pods = list_pods(args.job_name, args.project, args.freshness)
    print(f"{len(pods)} pods of {args.job_name} found in Cloud Logging (population: {len(pop)})")
    missing = sorted(set(idx_to_cid) - {POD_INDEX_RE.search(p).group(1) for p in pods}, key=int)
    if missing:
        print(f"  no log found for firm indices: {missing}")

    def work(pod: str) -> tuple:
        idx = POD_INDEX_RE.search(pod).group(1)
        cid = idx_to_cid.get(idx, f"firm_{idx}")
        try:
            return cid, harvest_pod(pod, cid, args.out_root, args.project, args.freshness, args.limit)
        except Exception as exc:
            return cid, {"pod": pod, "phase": "Unknown", "error": f"{type(exc).__name__}: {exc}"}

    status = {}
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for cid, entry in ex.map(work, pods):
            status[cid] = entry
            print(f"  {cid:24s} entries={entry.get('log_entries', '?'):>6} traj={entry.get('trajectory')} "
                  f"final={entry.get('final')} harvested={entry.get('harvested')} "
                  f"{entry.get('error') or entry.get('harvest_error') or ''}")
    os.makedirs(args.out_root, exist_ok=True)
    with open(os.path.join(args.out_root, "harvest_status.json"), "w") as f:
        json.dump(status, f, indent=2)
    harvested = sum(1 for e in status.values() if e.get("harvested"))
    print(f"harvested {harvested}/{len(pods)} output tarballs into {args.out_root}/outputs/")


if __name__ == "__main__":
    main()
