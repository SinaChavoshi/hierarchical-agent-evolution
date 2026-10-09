#!/usr/bin/env python3
"""Screen SWE-bench dev-slice instances on GKE (`kubectl`) and run paired V8 (`puct`) vs V9 (`ab_mcts`) evaluations.

For each selected instance from `data/swebench/dev_slice_50.json`:
  1. Provision a `sleep infinity` testbed Pod on `firm-pool` (or reuse an existing one) and wait for Ready.
  2. Run the two-control validity screen (`scripts/grade_in_testbed.py --gold` must resolve; `--none` must not).
  3. On valid instances (unless `--screen-only`), run each requested policy genome after resetting `/testbed`,
     then grade its `prediction.json` with `scripts/grade_in_testbed.py`.
  4. Delete the temporary Pod (unless `--keep-pods`) so node disk and CPU stay within capacity across the 50-task slice.

Resumable: completed `screen.json`, `prediction.json`, and `grade.json` files are reused on restart.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from hae.swebench.dataset import DATA_DIR, DEV_SLICE_FILE, instance_image, read_dev_slice  # noqa: E402
from hae.swebench.export import merge_predictions  # noqa: E402

DEFAULT_DATASET = os.path.join(DATA_DIR, "swebench_dev.jsonl")
DEFAULT_SLICE = os.path.join(DATA_DIR, DEV_SLICE_FILE)
DEFAULT_CONTEXT = "gke_gemle-gke-dev_us-central1-b_chavoshi-v6-cohort"
GRADE_SCRIPT = os.path.join(REPO_ROOT, "scripts", "grade_in_testbed.py")

DEFAULT_POLICIES: List[Tuple[str, str, str]] = [
    ("v8_puct", "configs/genomes/v8_smoke_swebench_firm_0.json", "hae-v8-puct/nvidia-Qwen3.8-Flash-Next-NVFP4"),
    ("v9_ab_mcts", "configs/genomes/v9_ab_mcts_swebench_firm_0.json", "hae-v9-ab/nvidia-Qwen3.8-Flash-Next-NVFP4"),
]


def pod_name_for(instance_id: str) -> str:
    """Deterministic DNS-1123 pod name (`<= 63` chars) matching existing conventions where applicable."""
    short_map = {
        "marshmallow-code__marshmallow-1810": "swebench-testbed-marshmallow-1810",
        "pvlib__pvlib-python-1395": "swebench-testbed-pvlib-1395",
        "pydicom__pydicom-938": "swebench-testbed-pydicom-938",
    }
    if instance_id in short_map:
        return short_map[instance_id]
    slug = re.sub(r"[^a-z0-9-]+", "-", instance_id.lower().replace("__", "-")).strip("-")
    name = f"swebench-tb-{slug}"
    return name[:63].rstrip("-")


def pod_manifest(pod_name: str, instance_id: str, image: str, nodepool: str = "firm-pool") -> Dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": pod_name,
            "namespace": "default",
            "labels": {
                "app": "swebench-testbed",
                "instance": re.sub(r"[^A-Za-z0-9_.-]+", "_", instance_id)[:63].strip("_.-"),
            },
        },
        "spec": {
            "nodeSelector": {"cloud.google.com/gke-nodepool": nodepool},
            "restartPolicy": "Never",
            "containers": [
                {
                    "name": "testbed",
                    "image": image,
                    "imagePullPolicy": "IfNotPresent",
                    "command": ["sleep", "infinity"],
                    "resources": {
                        "requests": {"cpu": "1", "memory": "3Gi"},
                        "limits": {"cpu": "4", "memory": "8Gi"},
                    },
                }
            ],
        },
    }


def _kubectl(context: str, argv: Sequence[str], input_text: Optional[str] = None,
             timeout_s: int = 120) -> subprocess.CompletedProcess:
    cmd = ["kubectl"] + (["--context", context] if context else []) + list(argv)
    return subprocess.run(cmd, input=input_text, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          timeout=timeout_s, text=True, errors="replace")


def ensure_pod(instance_id: str, context: str, nodepool: str = "firm-pool",
               ready_timeout_s: int = 420) -> Tuple[str, bool, str]:
    """Ensure testbed pod is Running/Ready. Returns `(pod_name, created_by_us, error)`."""
    pod = pod_name_for(instance_id)
    chk = _kubectl(context, ["get", "pod", pod, "-o", "jsonpath={.status.phase}"], timeout_s=30)
    created = False
    if chk.returncode != 0 or chk.stdout.strip() not in ("Running", "Pending"):
        if chk.returncode == 0:
            _kubectl(context, ["delete", "pod", pod, "--ignore-not-found=true", "--wait=true"], timeout_s=90)
        spec = json.dumps(pod_manifest(pod, instance_id, instance_image(instance_id), nodepool=nodepool))
        ap = _kubectl(context, ["apply", "-f", "-"], input_text=spec, timeout_s=60)
        if ap.returncode != 0:
            return pod, False, f"kubectl apply failed: {ap.stdout.strip()[-300:]}"
        created = True
    wait = _kubectl(context, ["wait", "--for=condition=Ready", f"pod/{pod}", f"--timeout={ready_timeout_s}s"],
                    timeout_s=ready_timeout_s + 30)
    if wait.returncode != 0:
        return pod, created, f"pod not Ready within {ready_timeout_s}s: {wait.stdout.strip()[-300:]}"
    return pod, created, ""


def delete_pod(pod: str, context: str) -> None:
    _kubectl(context, ["delete", "pod", pod, "--ignore-not-found=true", "--wait=false"], timeout_s=60)


def reset_testbed(pod: str, context: str) -> None:
    _kubectl(context, ["exec", pod, "-c", "testbed", "--", "sh", "-c",
                       "cd /testbed && git checkout -q -- . && git clean -fdq && rm -rf /testbed/.hae"],
             timeout_s=60)


def screen_instance(instance_id: str, pod: str, dataset: str, screen_dir: str,
                    timeout_s: int = 600) -> Dict[str, Any]:
    """Run `--gold` and `--none` controls in `pod` (or reuse `screen.json`)."""
    os.makedirs(screen_dir, exist_ok=True)
    screen_path = os.path.join(screen_dir, "screen.json")
    if os.path.exists(screen_path):
        try:
            with open(screen_path, "r", encoding="utf-8") as fh:
                cached = json.load(fh)
            if "valid" in cached:
                return cached
        except Exception:
            pass

    gold_path = os.path.join(screen_dir, "grade_gold.json")
    none_path = os.path.join(screen_dir, "grade_none.json")
    for flag, out_path in (("--gold", gold_path), ("--none", none_path)):
        if not os.path.exists(out_path):
            cmd = [
                sys.executable, GRADE_SCRIPT,
                "--instance-id", instance_id,
                "--dataset", dataset,
                "--executor", "kubectl", "--pod", pod, "--container", "testbed",
                flag, "--out", out_path, "--timeout", str(timeout_s),
            ]
            subprocess.run(cmd, cwd=REPO_ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           timeout=timeout_s + 120, text=True, errors="replace")

    gold = json.load(open(gold_path, "r", encoding="utf-8")) if os.path.exists(gold_path) else {}
    none = json.load(open(none_path, "r", encoding="utf-8")) if os.path.exists(none_path) else {}
    gold_ok = bool(gold.get("resolved"))
    none_ok = bool(none.get("resolved"))
    valid = gold_ok and not none_ok
    if not gold_ok:
        reason = f"gold_failed (f2p={gold.get('f2p_passed')}/{gold.get('f2p_total')}, p2p={gold.get('PASS_TO_PASS_summary')})"
    elif none_ok:
        reason = "none_already_passes_at_base_commit"
    else:
        reason = "ok"
    result = {
        "instance_id": instance_id,
        "valid": valid,
        "reason": reason,
        "gold_resolved": gold_ok,
        "none_resolved": none_ok,
        "f2p_total": gold.get("f2p_total"),
        "p2p_summary_gold": gold.get("PASS_TO_PASS_summary"),
        "gold_elapsed_s": gold.get("elapsed_seconds"),
        "none_elapsed_s": none.get("elapsed_seconds"),
    }
    with open(screen_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=1, sort_keys=True)
        fh.write("\n")
    return result


def run_policy_on_instance(instance_id: str, pod: str, dataset: str, policy_tag: str,
                           genome_path: str, model_name: str, out_root: str,
                           context: str, budget_moves: int = 30, max_iterations: int = 2,
                           instance_timeout_s: int = 2400, vllm_url: str = "http://127.0.0.1:8000/v1",
                           vllm_model: str = "nvidia/Qwen3.8-Flash-Next-NVFP4") -> Dict[str, Any]:
    out_dir = os.path.join(out_root, policy_tag, instance_id)
    os.makedirs(out_dir, exist_ok=True)
    pred_path = os.path.join(out_dir, "prediction.json")
    rec_path = os.path.join(out_dir, "record.json")
    grade_path = os.path.join(out_dir, "grade.json")
    stdout_log = os.path.join(out_root, policy_tag, f"{instance_id}.stdout.log")

    if not (os.path.exists(pred_path) and os.path.exists(rec_path)):
        reset_testbed(pod, context)
        env = dict(os.environ)
        env.update({
            "LLM_PROVIDER": "vllm",
            "V5_TYPESAFE_PROTOCOL": "1",
            "VLLM_BASE_URL": vllm_url,
            "VLLM_MODEL": vllm_model,
            "VLLM_MAX_TOKENS": env.get("VLLM_MAX_TOKENS", "8192"),
            "HAE_REQUIRE_NETWORK_ISOLATION": "0",
            "PYTHONPATH": REPO_ROOT,
        })
        cmd = [
            sys.executable, "-m", "hae.cli", "--mode", "swebench",
            "--instance-id", instance_id,
            "--dataset", dataset,
            "--executor", "kubectl", "--pod", pod, "--container", "testbed",
            "--genome", genome_path,
            "--out-dir", out_dir,
            "--budget-moves", str(budget_moves),
            "--max-iterations", str(max_iterations),
            "--model-name", model_name,
        ]
        started = time.time()
        try:
            proc = subprocess.run(cmd, cwd=REPO_ROOT, env=env, stdin=subprocess.DEVNULL,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  timeout=instance_timeout_s, text=True, errors="replace")
            with open(stdout_log, "w", encoding="utf-8") as fh:
                fh.write(proc.stdout)
        except subprocess.TimeoutExpired as exc:
            out = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            with open(stdout_log, "w", encoding="utf-8") as fh:
                fh.write(out + f"\n[TIMEOUT after {instance_timeout_s}s]\n")
            return {"instance_id": instance_id, "policy": policy_tag, "status": "timeout",
                    "elapsed_s": round(time.time() - started, 1)}

    if os.path.exists(pred_path) and not os.path.exists(grade_path):
        cmd_grade = [
            sys.executable, GRADE_SCRIPT,
            "--instance-id", instance_id,
            "--dataset", dataset,
            "--executor", "kubectl", "--pod", pod, "--container", "testbed",
            "--prediction", pred_path,
            "--out", grade_path,
        ]
        subprocess.run(cmd_grade, cwd=REPO_ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       timeout=900, text=True, errors="replace")

    rec = json.load(open(rec_path, "r", encoding="utf-8")) if os.path.exists(rec_path) else {}
    grade = json.load(open(grade_path, "r", encoding="utf-8")) if os.path.exists(grade_path) else {}
    swe = rec.get("swebench") or {}
    return {
        "instance_id": instance_id,
        "policy": policy_tag,
        "status": "ok" if os.path.exists(pred_path) else "failed",
        "stop": swe.get("stop"),
        "moves_used": swe.get("moves_used"),
        "self_oracle": swe.get("resolved_by_self_oracle"),
        "graded_resolved": grade.get("resolved"),
        "f2p_passed": grade.get("f2p_passed"),
        "f2p_total": grade.get("f2p_total"),
        "p2p_summary": grade.get("PASS_TO_PASS_summary"),
        "patch_chars": swe.get("patch_chars"),
        "changed_files": swe.get("changed_files"),
        "tokens": (rec.get("token_usage") or {}).get("total_tokens"),
        "elapsed_s": rec.get("elapsed_seconds"),
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out-root", required=True, help="Root output directory (e.g. results/swebench/dev_eval_2026-10-09)")
    ap.add_argument("--dataset", default=DEFAULT_DATASET)
    ap.add_argument("--slice", default=DEFAULT_SLICE)
    ap.add_argument("--instances", nargs="*", default=None, help="Explicit instance IDs (overrides --slice)")
    ap.add_argument("--offset", type=int, default=0, help="Start offset in the slice list")
    ap.add_argument("--n", type=int, default=None, help="Max instances from the slice to process")
    ap.add_argument("--valid-target", type=int, default=None,
                    help="Stop after finding and evaluating this many valid instances (sequential mode)")
    ap.add_argument("--workers", type=int, default=1, help="Concurrent instance workers (each uses its own Pod)")
    ap.add_argument("--budget-moves", type=int, default=30)
    ap.add_argument("--max-iterations", type=int, default=2)
    ap.add_argument("--screen-only", action="store_true", help="Only run the --gold / --none validity screen")
    ap.add_argument("--policies", nargs="*", choices=["v8_puct", "v9_ab_mcts"], default=["v8_puct", "v9_ab_mcts"])
    ap.add_argument("--keep-pods", action="store_true", help="Do not delete temporary testbed pods after finishing an instance")
    ap.add_argument("--context", default=DEFAULT_CONTEXT)
    ap.add_argument("--nodepool", default="firm-pool")
    args = ap.parse_args(list(argv) if argv is not None else None)

    if args.instances:
        ids = list(dict.fromkeys(args.instances))
    else:
        slice_info = read_dev_slice(args.slice)
        ids = [str(i) for i in slice_info.get("instance_ids", [])]
        if args.offset:
            ids = ids[args.offset:]
        if args.n is not None:
            ids = ids[:args.n]

    os.makedirs(args.out_root, exist_ok=True)
    policies = [p for p in DEFAULT_POLICIES if p[0] in set(args.policies)]
    screen_rows: List[Dict[str, Any]] = []
    run_rows: List[Dict[str, Any]] = []
    lock = threading.Lock()
    valid_count = [0]

    def process_one(iid: str) -> None:
        with lock:
            if args.valid_target is not None and valid_count[0] >= args.valid_target:
                return
        pod, created, err = ensure_pod(iid, args.context, nodepool=args.nodepool)
        if err:
            row = {"instance_id": iid, "valid": False, "reason": f"pod_error: {err}"}
            with lock:
                screen_rows.append(row)
                print(json.dumps({"event": "screen", **row}), flush=True)
            if created and not args.keep_pods:
                delete_pod(pod, args.context)
            return
        try:
            scr = screen_instance(iid, pod, args.dataset, os.path.join(args.out_root, "screen", iid))
            with lock:
                screen_rows.append(scr)
                print(json.dumps({"event": "screen", **scr}), flush=True)
                if scr.get("valid"):
                    if args.valid_target is not None and valid_count[0] >= args.valid_target:
                        return
                    valid_count[0] += 1
            if scr.get("valid") and not args.screen_only:
                for tag, genome_path, model_name in policies:
                    res = run_policy_on_instance(
                        iid, pod, args.dataset, tag, genome_path, model_name,
                        args.out_root, args.context,
                        budget_moves=args.budget_moves, max_iterations=args.max_iterations,
                    )
                    with lock:
                        run_rows.append(res)
                        print(json.dumps({"event": "run", **res}), flush=True)
        finally:
            if created and not args.keep_pods:
                delete_pod(pod, args.context)

    if args.workers <= 1 or args.valid_target is not None:
        for iid in ids:
            if args.valid_target is not None and valid_count[0] >= args.valid_target:
                break
            process_one(iid)
    else:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            list(pool.map(process_one, ids))

    for tag, _, _ in policies:
        tag_dir = os.path.join(args.out_root, tag)
        if os.path.isdir(tag_dir):
            preds = [os.path.join(tag_dir, i, "prediction.json") for i in ids
                     if os.path.exists(os.path.join(tag_dir, i, "prediction.json"))]
            if preds:
                merge_predictions(preds, os.path.join(tag_dir, "preds.jsonl"))

    summary = {
        "out_root": args.out_root,
        "screened": len(screen_rows),
        "valid": sum(1 for r in screen_rows if r.get("valid")),
        "invalid": [r for r in screen_rows if not r.get("valid")],
        "screen_rows": screen_rows,
        "run_rows": run_rows,
    }
    with open(os.path.join(args.out_root, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=1, sort_keys=True)
        fh.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
