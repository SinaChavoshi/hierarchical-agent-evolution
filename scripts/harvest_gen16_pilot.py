"""Monitor and harvest the Generation 16 V6 pilot from the GKE Job.

Polls the indexed Job pods, mirrors their logs locally on every pass, and once
a pod is Running/Succeeded copies `/data/outputs/generation_16/` (result JSON,
epistemic tree sidecar, briefs) out of it. Pods' GCS sync returned HTTP 403 in
Gen 15, so this is the primary harvest path, not a fallback.

Usage:
    python3 scripts/harvest_gen16_pilot.py --watch        # loop until all done
    python3 scripts/harvest_gen16_pilot.py                # single pass
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time

JOB_LABEL = "app=hae-gen16-v6-pilot"
OUT_ROOT = "results/hae_gen16_v6_pilot"
POP_FILE = "configs/generation_16_pilot_population.json"
REMOTE_OUT = "/data/outputs/generation_16"

ITER_RE = re.compile(r"\[Iteration (\d+)/(\d+)\] \[self-hosting-benchmark\] (?:(\d+)/50 held-out tests|submission mutates sys\.path)")
FIT_RE = re.compile(r"FINAL NET FITNESS:\s*([\d.]+)/100\s*\(Gross:\s*([\d.]+)\)\s*\|\s*Iterations:\s*(\d+)/(\d+)")


def sh(cmd, timeout=60):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def pods():
    r = sh(["kubectl", "get", "pods", "-l", JOB_LABEL, "-o", "json"])
    if r.returncode != 0:
        return []
    return json.loads(r.stdout).get("items", [])


def trajectory(log_text: str):
    traj = []
    for m in ITER_RE.finditer(log_text):
        traj.append(int(m.group(3)) if m.group(3) else 0)
    return traj


def extract_embedded(text: str, dst_root: str) -> bool:
    """Decode the `OUTPUT_TGZ_B64` block the pod prints after the worker exits."""
    m = re.search(r"=== OUTPUT_TGZ_B64 BEGIN ===\n(.*?)\n=== OUTPUT_TGZ_B64 END ===", text, re.S)
    if not m:
        return False
    import base64, io, tarfile
    blob = base64.b64decode(m.group(1).strip())
    os.makedirs(dst_root, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tf:
        tf.extractall(dst_root)
    return True


def one_pass(idx_to_cid, copied: set) -> dict:
    log_root = os.path.join(OUT_ROOT, "pod_logs")
    os.makedirs(log_root, exist_ok=True)
    status = {}
    for item in pods():
        pname = item["metadata"]["name"]
        fidx = item["metadata"].get("annotations", {}).get("batch.kubernetes.io/job-completion-index", "?")
        cid = idx_to_cid.get(fidx, f"firm_{fidx}")
        phase = item.get("status", {}).get("phase", "Unknown")
        entry = {"pod": pname, "phase": phase, "trajectory": [], "final": None,
                 "epistemic_lines": 0, "worker_exit": None, "harvested": pname in copied, "last": ""}
        if phase in ("Running", "Succeeded", "Failed"):
            r = sh(["kubectl", "logs", pname], timeout=60)
            text = r.stdout if r.returncode == 0 else ""
            if text:
                with open(os.path.join(log_root, f"pod_{fidx}_{pname}.log"), "w") as f:
                    f.write(text)
                entry["trajectory"] = trajectory(text)
                m = FIT_RE.search(text)
                if m:
                    entry["final"] = {"net": float(m.group(1)), "gross": float(m.group(2)),
                                      "iterations": f"{m.group(3)}/{m.group(4)}"}
                entry["epistemic_lines"] = sum(1 for ln in text.splitlines() if "[epistemic]" in ln or "EPISTEMIC" in ln)
                mx = re.search(r"^WORKER_EXIT=(\d+)", text, re.M)
                entry["worker_exit"] = int(mx.group(1)) if mx else None
                tail = [ln for ln in text.splitlines() if ln.strip() and len(ln) < 400]
                entry["last"] = tail[-1][:160] if tail else ""
                if entry["worker_exit"] is not None and pname not in copied:
                    dst = os.path.join(OUT_ROOT, "outputs", cid)
                    ok = extract_embedded(text, dst)
                    if not ok and phase == "Running":
                        c = sh(["kubectl", "cp", f"{pname}:{REMOTE_OUT}", os.path.join(dst, "generation_16")], timeout=180)
                        ok = c.returncode == 0
                    if ok:
                        copied.add(pname)
                        entry["harvested"] = True
        status[cid] = entry
    return status


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--interval", type=int, default=120)
    ap.add_argument("--max-passes", type=int, default=120)
    args = ap.parse_args()

    pop = json.load(open(POP_FILE))["population"]
    idx_to_cid = {str(i): g["company_id"] for i, g in enumerate(pop)}
    copied: set = set()
    for step in range(args.max_passes if args.watch else 1):
        status = one_pass(idx_to_cid, copied)
        stamp = time.strftime("%H:%M:%S")
        print(f"--- {stamp} pass {step} ---")
        for cid, e in status.items():
            print(f"  {cid:22s} {e['phase']:10s} traj={e['trajectory']} final={e['final']} "
                  f"epi_lines={e['epistemic_lines']} | {e['last']}")
        with open(os.path.join(OUT_ROOT, "pilot_status.json"), "w") as f:
            json.dump(status, f, indent=2)
        done = [e for e in status.values() if e["worker_exit"] is not None and (e["harvested"] or e["phase"] == "Failed")]
        if status and len(done) >= len(pop):
            print("ALL_DONE")
            break
        if not args.watch:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
