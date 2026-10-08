#!/usr/bin/env python3
"""GRADING ONLY: approximate the SWE-bench harness inside a live testbed container.

    PYTHONPATH=. python3 scripts/grade_in_testbed.py \
        --instance-id marshmallow-code__marshmallow-1810 --dataset data/swebench/swebench_dev.jsonl \
        --executor kubectl --pod swebench-testbed-marshmallow-1810 --container testbed \
        --prediction results/swebench/<run>/marshmallow-code__marshmallow-1810/prediction.json \
        [--out results/swebench/<run>/marshmallow-code__marshmallow-1810/grade.json] [--gold]

Steps, mirroring `swebench.harness.run_evaluation` without Docker-on-host:
  1. reset the checkout to HEAD (`git checkout -- . && git clean -fdq`, keeping nothing of the firm's run),
  2. `git apply` the model patch (`--gold` applies the dataset's gold patch instead, as a control),
  3. `git apply` the hidden `test_patch`,
  4. run pytest on FAIL_TO_PASS then PASS_TO_PASS by node id with `-rA`, parse the per-test lines,
  5. resolved := every FAIL_TO_PASS test passed and every PASS_TO_PASS test passed,
  6. reset the checkout again.

Differences from the official harness: no per-repo install/pre-test commands, pytest is invoked
directly by node id (the harness runs the touched test files and parses their log with a
repo-specific parser), and nothing is rebuilt. Treat the verdict as indicative; the official
number comes from `swebench eval` on `preds.jsonl`. This script imports `load_grading_info`,
which the firm's runtime never does; keep it out of `hae/`.
"""

import argparse
import json
import os
import re
import sys
import time
from typing import Dict, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hae.swebench.dataset import load_grading_info  # noqa: E402  grading-side loader
from hae.swebench.runner import make_executor  # noqa: E402

_RESULT_RE = re.compile(r"^(PASSED|FAILED|ERROR|XFAIL|XPASS|SKIPPED)\s+(\S+)", re.M)


def _sh(ex, script: str, timeout_s: float = 600.0):
    return ex.run(["bash", "-c", script], cwd=ex.root, timeout_s=timeout_s)


def _apply(ex, name: str, patch: str) -> Tuple[bool, str]:
    if not patch.strip():
        return False, f"{name}: empty patch"
    rel = f".hae/{name}.diff"
    ex.write_file(rel, patch if patch.endswith("\n") else patch + "\n")
    res = _sh(ex, f"git apply --whitespace=nowarn {rel} || git apply --3way --whitespace=nowarn {rel}")
    return res.rc == 0, (res.stdout + res.stderr).strip()[-800:]


def _run_tests(ex, test_ids: List[str], timeout_s: float) -> Tuple[Dict[str, str], str]:
    if not test_ids:
        return {}, ""
    argv = [ex.python, "-m", "pytest", "-rA", "-p", "no:cacheprovider", "--no-header", "-q", *test_ids]
    res = ex.run(argv, cwd=ex.root, timeout_s=timeout_s)
    out = res.stdout + "\n" + res.stderr
    status: Dict[str, str] = {}
    for m in _RESULT_RE.finditer(out):
        status[m.group(2)] = m.group(1)
    # pytest may report a parametrised id or a differently-rooted path; fall back to a loose match.
    for tid in test_ids:
        if tid in status:
            continue
        tail = tid.split("::", 1)[-1]
        for k, v in status.items():
            if k.endswith(tid) or (tail and k.endswith(tail)):
                status[tid] = v
                break
        status.setdefault(tid, "MISSING" if res.rc != 0 or not out.strip() else "MISSING")
    return status, out[-4000:]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--instance-id", required=True)
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--executor", choices=["docker", "kubectl", "local"], default="kubectl")
    ap.add_argument("--pod", default="")
    ap.add_argument("--container", default="")
    ap.add_argument("--namespace", default="default")
    ap.add_argument("--root", default="")
    ap.add_argument("--python", default="")
    ap.add_argument("--prediction", help="prediction.json written by the runner")
    ap.add_argument("--gold", action="store_true", help="grade the dataset's gold patch instead (control)")
    ap.add_argument("--none", action="store_true", help="apply no model patch (control: FAIL_TO_PASS must fail)")
    ap.add_argument("--out", default="")
    ap.add_argument("--timeout", type=float, default=900.0)
    args = ap.parse_args(argv)

    ex = make_executor(args.executor, container=args.container, pod=args.pod, namespace=args.namespace,
                       root=args.root, python=args.python)
    grading = load_grading_info(args.dataset, args.instance_id)
    if args.none:
        model_patch, source = "", "none"
    elif args.gold:
        model_patch, source = grading.patch, "gold"
    else:
        with open(args.prediction, "r", encoding="utf-8") as fh:
            model_patch = json.load(fh).get("model_patch", "")
        source = args.prediction
    started = time.time()
    report = {"instance_id": args.instance_id, "patch_source": source, "executor": ex.describe(),
              "patch_chars": len(model_patch), "steps": {}}
    try:
        reset = _sh(ex, "git checkout -q -- . && git clean -fdq && git status --short | wc -l")
        report["steps"]["reset"] = {"rc": reset.rc, "dirty_after": reset.stdout.strip()}
        if args.none:
            ok, note = True, "no model patch (control)"
        else:
            ok, note = _apply(ex, "model", model_patch)
        report["steps"]["apply_model_patch"] = {"ok": ok, "note": note}
        if ok:
            ok_t, note_t = _apply(ex, "test", grading.test_patch)
            report["steps"]["apply_test_patch"] = {"ok": ok_t, "note": note_t}
            if ok_t:
                f2p, f2p_log = _run_tests(ex, grading.FAIL_TO_PASS, args.timeout)
                p2p, p2p_log = _run_tests(ex, grading.PASS_TO_PASS, args.timeout)
                report["FAIL_TO_PASS"] = f2p
                report["PASS_TO_PASS_summary"] = {k: sum(1 for v in p2p.values() if v == k)
                                                  for k in ("PASSED", "FAILED", "ERROR", "SKIPPED", "MISSING")}
                report["PASS_TO_PASS_failures"] = sorted(k for k, v in p2p.items() if v not in ("PASSED", "SKIPPED", "XFAIL"))
                report["f2p_log_tail"] = f2p_log[-1500:]
                f2p_ok = bool(f2p) and all(v == "PASSED" for v in f2p.values())
                p2p_ok = all(v in ("PASSED", "SKIPPED", "XFAIL") for v in p2p.values())
                report["resolved"] = f2p_ok and p2p_ok
                report["f2p_passed"] = sum(1 for v in f2p.values() if v == "PASSED")
                report["f2p_total"] = len(f2p)
    finally:
        _sh(ex, "git checkout -q -- . && git clean -fdq")
        report["elapsed_seconds"] = round(time.time() - started, 1)
    if args.out:
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=1, sort_keys=True)
            fh.write("\n")
    print(json.dumps({k: report.get(k) for k in ("instance_id", "patch_source", "resolved", "f2p_passed",
                                                  "f2p_total", "PASS_TO_PASS_summary", "steps")}, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
