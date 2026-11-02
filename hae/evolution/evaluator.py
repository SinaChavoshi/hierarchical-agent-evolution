"""Evaluate `HarnessGenome`s on SWE-bench tasks in GKE testbed pods.

Each (genome, task) job:
  1. runs the agent from the genome's materialized worktree (`harness_root`) with the genome's knobs,
  2. grades the prediction with this repository's `scripts/grade_in_testbed.py` (never the candidate's),
  3. (V17, training split only when `label_verdicts=True`) labels every red-team checkpoint verdict
     against the hidden tests while the pod is still up,
  4. caches `prediction.json` / `record.json` / `grade.json` / `verdicts_grade.json` so a restarted run resumes.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from hae.evolution.genome import HarnessGenome, TaskResult
from hae.evolution.pod_pool import PodPool, interleave_jobs
from hae.evolution.workspace import WorktreeManager

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GRADE_SCRIPT = os.path.join(REPO_ROOT, "scripts", "grade_in_testbed.py")


def _load_batch_module():
    path = os.path.join(REPO_ROOT, "scripts", "swebench_kubectl_batch.py")
    spec = importlib.util.spec_from_file_location("_hae_swebench_kubectl_batch", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


class GenomeEvaluator:
    def __init__(self, *, out_root: str, seed_genome_path: str, dataset_by_task: Dict[str, str],
                 worktrees: WorktreeManager, vllm_url: str, vllm_model: str, context: str,
                 workers: int = 4, budget_moves: int = 30, max_iterations: int = 2,
                 instance_timeout_s: int = 2400, pod_tag: str = "v15",
                 logger: Callable[[str], None] = print, prewarm_pods: int = 4,
                 label_verdicts: bool = False) -> None:
        self.batch = _load_batch_module()
        self.out_root = os.path.abspath(out_root)
        self.evals_root = os.path.join(self.out_root, "evals")
        self.genome_cfg_root = os.path.join(self.out_root, "genome_configs")
        os.makedirs(self.evals_root, exist_ok=True)
        os.makedirs(self.genome_cfg_root, exist_ok=True)
        with open(seed_genome_path, "r", encoding="utf-8") as fh:
            self.seed_cfg = json.load(fh)
        self.dataset_by_task = {k: os.path.abspath(v) for k, v in dataset_by_task.items()}
        self.worktrees = worktrees
        self.vllm_url = vllm_url
        self.vllm_model = vllm_model
        self.context = context
        self.workers = max(1, int(workers))
        self.budget_moves = budget_moves
        self.max_iterations = max_iterations
        self.instance_timeout_s = instance_timeout_s
        self.pod_tag = re.sub(r"[^a-z0-9-]", "", pod_tag.lower())[:10] or "v15"
        self.logger = logger
        self.label_verdicts = bool(label_verdicts)
        self._lock = threading.Lock()
        # V16: infra failures are never scored; a job still failing after this many retries keeps an
        # infra status (not `ok`) so reports can exclude it and it can be re-run.
        self.infra_retries = int(os.environ.get("HAE_INFRA_RETRIES", "5"))
        self.pool = PodPool(create=self._ensure_pod, delete=self._delete_pod,
                            name=lambda iid: self._pod_name("pool", iid), lookahead=prewarm_pods)

    # ------------------------------------------------------------------ #
    def genome_config_path(self, genome: HarnessGenome) -> str:
        cfg = copy.deepcopy(self.seed_cfg)
        cfg["company_id"] = f"v15_{genome.genome_id}"
        pol = cfg.setdefault("epistemic_policy", {})
        pol["v15_knobs"] = dict(genome.knobs)  # unknown keys land in `EpistemicPolicyGene.extra`
        # V16: the genome's organization. org_mode off leaves the harness exactly V15.
        pol["v16_org"] = genome.org_mode
        if genome.has_redteam:
            pol["v17_redteam"] = dict(genome.redteam)
        if genome.roles:
            cfg["role_library"] = [dict(r) for r in genome.roles]
        if genome.ceo_policy:
            cfg["ceo_policy"] = dict(genome.ceo_policy)
        if genome.org_mode != "off" and isinstance(cfg.get("ceo_policy"), dict):
            cfg["ceo_policy"]["enabled"] = True
        path = os.path.join(self.genome_cfg_root, f"{genome.genome_id}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=1, sort_keys=True)
        return path

    def _pod_name(self, genome_id: str, instance_id: str) -> str:
        # Unique per attempt: a retried job must not race with its previous pod's deletion.
        h = hashlib.sha1(f"{genome_id}:{instance_id}:{time.time_ns()}".encode()).hexdigest()[:6]
        slug = re.sub(r"[^a-z0-9-]+", "-", instance_id.lower().replace("__", "-")).strip("-")
        return f"{self.pod_tag}-{h}-{slug}"[:63].rstrip("-")

    def _delete_pod(self, pod: str) -> None:
        """Delete with retries: a delete lost to an API-server timeout leaks a 2-CPU pod."""
        for attempt in range(6):
            try:
                res = self.batch._kubectl(self.context, ["delete", "pod", pod, "--ignore-not-found=true",
                                                         "--wait=false"], timeout_s=60)
                if res.returncode == 0:
                    return
            except Exception:
                pass
            time.sleep(5 * (attempt + 1))
        self.logger(f"[v15-eval] could not delete pod {pod}")

    def _ensure_pod(self, pod: str, instance_id: str) -> Optional[str]:
        b = self.batch
        spec = json.dumps(b.pod_manifest(pod, instance_id, b.instance_image(instance_id)))
        ap = b._kubectl(self.context, ["apply", "-f", "-"], input_text=spec, timeout_s=90)
        if ap.returncode != 0:
            return f"kubectl apply failed: {ap.stdout.strip()[-300:]}"
        wait = b._kubectl(self.context, ["wait", "--for=condition=Ready", f"pod/{pod}", "--timeout=1200s"], timeout_s=1240)
        if wait.returncode != 0:
            return f"pod not Ready: {wait.stdout.strip()[-300:]}"
        return None

    def _label_verdicts_in_pod(self, pod: str, instance_id: str, d: str) -> None:
        """V17 (training split only): grade each candidate patch judged by the red team while `pod` is up."""
        vg_path = os.path.join(d, "verdicts_grade.json")
        if os.path.exists(vg_path):
            return
        try:
            rec = json.load(open(os.path.join(d, "record.json"), encoding="utf-8"))
            grade = json.load(open(os.path.join(d, "grade.json"), encoding="utf-8"))
            pred = json.load(open(os.path.join(d, "prediction.json"), encoding="utf-8"))
        except Exception:
            return
        swe = rec.get("swebench") or {}
        org = swe.get("org") or {}
        verdicts = list(org.get("verdicts") or [])
        patches = dict(org.get("verdict_patches") or {})
        final_patch = (pred.get("model_patch") or "").strip()
        final_sha = hashlib.sha1(final_patch.encode("utf-8", errors="replace")).hexdigest()[:12] if final_patch else ""
        final_resolved = bool(grade.get("resolved"))

        # Fallback when `verdicts` was not populated (e.g. A17 with org_mode=off and no v17_redteam):
        # derive the single final falsifier verdict from mcts_tree if any node ran the falsifier.
        if not verdicts:
            mcts_tree = ((rec.get("epistemic_search") or {}).get("stats") or {}).get("mcts_tree") or {}
            for nid, nd in mcts_tree.items():
                if nid != "root" and nd.get("falsifier_ok") is not None and (nd.get("repro_ok") and nd.get("repo_tests_ok")):
                    verdicts.append({
                        "episode": 1,
                        "node": nid,
                        "diff_sha": final_sha,
                        "panel": [{"role_id": "generalist_falsifier", "passed": bool(nd.get("falsifier_ok")), "critique": ""}],
                        "falsifier_ok": bool(nd.get("falsifier_ok")),
                    })

        resolved_by_sha: Dict[str, bool] = {}
        if final_sha:
            resolved_by_sha[final_sha] = final_resolved
        extra_graded = 0
        for vd in verdicts:
            sha = str(vd.get("diff_sha") or "")
            if not sha or sha in resolved_by_sha:
                continue
            patch_str = (patches.get(sha) or "").strip()
            if not patch_str:
                resolved_by_sha[sha] = False
                continue
            if patch_str == final_patch:
                resolved_by_sha[sha] = final_resolved
                continue
            if extra_graded >= 2 or not pod:
                resolved_by_sha[sha] = final_resolved
                continue
            extra_graded += 1
            v_pred = os.path.join(d, f"verdict_{sha}.pred.json")
            v_grade = os.path.join(d, f"verdict_{sha}.grade.json")
            try:
                if not os.path.exists(v_grade):
                    with open(v_pred, "w", encoding="utf-8") as fh:
                        json.dump({"instance_id": instance_id, "model_name_or_path": "v17-verdict",
                                   "model_patch": patch_str + ("\n" if not patch_str.endswith("\n") else "")}, fh)
                    cmd = [
                        sys.executable, GRADE_SCRIPT,
                        "--instance-id", instance_id,
                        "--dataset", self.dataset_by_task[instance_id],
                        "--executor", "kubectl", "--pod", pod, "--container", "testbed",
                        "--prediction", v_pred,
                        "--out", v_grade,
                    ]
                    subprocess.run(cmd, cwd=REPO_ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   timeout=600, text=True, errors="replace")
                vg_obj = json.load(open(v_grade, encoding="utf-8")) if os.path.exists(v_grade) else {}
                resolved_by_sha[sha] = bool(vg_obj.get("resolved"))
            except Exception:
                resolved_by_sha[sha] = False

        tp = fp = tn = fn = 0
        labelled: List[Dict[str, Any]] = []
        for vd in verdicts:
            sha = str(vd.get("diff_sha") or "")
            gt = bool(resolved_by_sha.get(sha, final_resolved))
            passed = bool(vd.get("falsifier_ok"))
            if passed and gt:
                tp += 1
            elif passed and not gt:
                fp += 1
            elif not passed and not gt:
                tn += 1
            else:
                fn += 1
            labelled.append({**vd, "ground_truth_resolved": gt})

        with open(vg_path, "w", encoding="utf-8") as fh:
            json.dump({
                "instance_id": instance_id,
                "rt_tp": tp, "rt_fp": fp, "rt_tn": tn, "rt_fn": fn,
                "labelled_verdicts": labelled,
            }, fh, indent=1, sort_keys=True)

    def _result_from_dir(self, genome_id: str, instance_id: str, status: str = "ok") -> TaskResult:
        d = os.path.join(self.evals_root, genome_id, instance_id)
        rec, grade, vg = {}, {}, {}
        try:
            rec = json.load(open(os.path.join(d, "record.json"), encoding="utf-8"))
        except Exception:
            pass
        try:
            grade = json.load(open(os.path.join(d, "grade.json"), encoding="utf-8"))
        except Exception:
            pass
        try:
            vg = json.load(open(os.path.join(d, "verdicts_grade.json"), encoding="utf-8"))
        except Exception:
            pass
        swe = rec.get("swebench") or {}
        tok = rec.get("token_usage")
        tok = tok.get("total_tokens") if isinstance(tok, dict) else tok
        return TaskResult(
            instance_id=instance_id,
            resolved=bool(grade.get("resolved")),
            f2p_passed=int(grade.get("f2p_passed") or 0),
            f2p_total=int(grade.get("f2p_total") or 0),
            tokens=int(tok or 0),
            elapsed_s=float(rec.get("elapsed_seconds") or 0.0),
            moves_used=int(swe.get("moves_used") or 0),
            stop=str(swe.get("stop") or ""),
            status=status if grade else (status if status != "ok" else "no_grade"),
            rt_tp=int(vg.get("rt_tp") or 0),
            rt_fp=int(vg.get("rt_fp") or 0),
            rt_tn=int(vg.get("rt_tn") or 0),
            rt_fn=int(vg.get("rt_fn") or 0),
        )

    def org_records(self, genome_id: str, task_ids: Optional[Sequence[str]] = None) -> List[Dict[str, Any]]:
        """`record.json["swebench"]["org"]` blocks (plus graded outcome and V17 labelled verdicts) of a genome's runs."""
        out: List[Dict[str, Any]] = []
        root = os.path.join(self.evals_root, genome_id)
        if not os.path.isdir(root):
            return out
        for t in sorted(task_ids if task_ids is not None else os.listdir(root)):
            d = os.path.join(root, t)
            try:
                rec = json.load(open(os.path.join(d, "record.json"), encoding="utf-8"))
                grade = json.load(open(os.path.join(d, "grade.json"), encoding="utf-8"))
            except Exception:
                continue
            vg = {}
            vg_path = os.path.join(d, "verdicts_grade.json")
            if os.path.exists(vg_path):
                try:
                    vg = json.load(open(vg_path, encoding="utf-8"))
                except Exception:
                    vg = {}
            org = (rec.get("swebench") or {}).get("org")
            if org or vg:
                out.append({
                    "task": t,
                    "resolved": bool(grade.get("resolved")),
                    "org": org or {},
                    "verdicts_grade": vg,
                })
        return out

    def log_tail(self, genome_id: str, instance_id: str, n_chars: int = 6000) -> str:
        p = os.path.join(self.evals_root, genome_id, f"{instance_id}.stdout.log")
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as fh:
                return fh.read()[-n_chars:]
        except OSError:
            return ""

    def _run_job(self, genome: HarnessGenome, instance_id: str, harness_root: str, genome_cfg: str) -> TaskResult:
        """Run one job, retrying when it failed for infrastructure reasons."""
        result = self._run_job_once(genome, instance_id, harness_root, genome_cfg)
        for attempt in range(self.infra_retries):
            if not self._is_infra_failure(genome.genome_id, instance_id, result):
                break
            self.logger(f"[v15-eval] {genome.genome_id} {instance_id}: infrastructure failure "
                        f"({result.status}); discarding and retrying ({attempt + 1}/{self.infra_retries})")
            d = os.path.join(self.evals_root, genome.genome_id, instance_id)
            import shutil
            shutil.rmtree(d, ignore_errors=True)
            time.sleep(30 * (attempt + 1))
            result = self._run_job_once(genome, instance_id, harness_root, genome_cfg)
        return result

    _INFRA_MARKERS = ("Unable to connect to the server", "TLS handshake timeout", "i/o timeout",
                      "connection refused", "error dialing backend", "the server is currently unable",
                      "dial tcp", "connection timed out")

    _STARVED_MARKERS = ("[container] timed out after 120s", "command terminated with exit code 137")

    def _sandbox_starved(self, genome_id: str, instance_id: str) -> bool:
        p = os.path.join(self.evals_root, genome_id, f"{instance_id}.stdout.log")
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as fh:
                head = fh.read(6000)
        except OSError:
            return False
        return "env_import=FAILED" in head and any(m in head for m in self._STARVED_MARKERS)

    def _is_infra_failure(self, genome_id: str, instance_id: str, result: TaskResult) -> bool:
        d = os.path.join(self.evals_root, genome_id, instance_id)
        if self._sandbox_starved(genome_id, instance_id):
            return True
        grade_path = os.path.join(d, "grade.json")
        if os.path.exists(grade_path) and result.status == "timeout":
            return False
        if os.path.exists(grade_path) and result.status == "ok":
            try:
                grade = json.load(open(grade_path, encoding="utf-8"))
            except Exception:
                return True
            return not grade.get("f2p_total") and not grade.get("note") and int(grade.get("patch_chars") or 0) > 0
        if result.status == "failed":
            return True
        if result.status in ("pod_error",) or result.status.startswith("error"):
            return True
        tail = self.log_tail(genome_id, instance_id, n_chars=4000)
        return any(m in tail for m in self._INFRA_MARKERS)

    def _run_job_once(self, genome: HarnessGenome, instance_id: str, harness_root: str, genome_cfg: str) -> TaskResult:
        d = os.path.join(self.evals_root, genome.genome_id, instance_id)
        if os.path.exists(os.path.join(d, "grade.json")):
            if self.label_verdicts and not os.path.exists(os.path.join(d, "verdicts_grade.json")):
                self._label_verdicts_in_pod("", instance_id, d)
            return self._result_from_dir(genome.genome_id, instance_id)
        pod, err = self.pool.acquire(instance_id)
        if err:
            self.pool.release(pod)
            self.logger(f"[v15-eval] {genome.genome_id} {instance_id}: {err}")
            return TaskResult(instance_id=instance_id, status="pod_error")
        status = "ok"
        try:
            res = self.batch.run_policy_on_instance(
                instance_id, pod, self.dataset_by_task[instance_id], genome.genome_id, genome_cfg,
                f"hae-v15/{self.vllm_model.replace('/', '-')}", self.evals_root, self.context,
                budget_moves=self.budget_moves, max_iterations=self.max_iterations,
                instance_timeout_s=self.instance_timeout_s, vllm_url=self.vllm_url,
                vllm_model=self.vllm_model, include_hints=False, harness_root=harness_root,
            )
            status = str(res.get("status") or "ok")
            if status == "timeout":
                os.makedirs(d, exist_ok=True)
                with open(os.path.join(d, "grade.json"), "w", encoding="utf-8") as fh:
                    json.dump({"instance_id": instance_id, "resolved": False, "note": "agent timeout"}, fh)
            elif self.label_verdicts and os.path.exists(os.path.join(d, "grade.json")):
                self._label_verdicts_in_pod(pod, instance_id, d)
        except Exception as exc:
            status = f"error: {type(exc).__name__}: {exc}"[:200]
        finally:
            self.pool.release(pod)
        return self._result_from_dir(genome.genome_id, instance_id, status=status)

    # ------------------------------------------------------------------ #
    def evaluate(self, genomes: Sequence[HarnessGenome], task_ids: Sequence[str]) -> None:
        """Evaluate every genome on every task it has not been evaluated on yet (in place)."""
        prepared: Dict[str, Tuple[HarnessGenome, str, str, List[str]]] = {}
        for g in genomes:
            todo = [t for t in task_ids if t not in g.results]
            if not todo:
                continue
            path, err = self.worktrees.materialize(g)
            for attempt in range(3):
                if not err or "patch does not apply" in err:
                    break
                self.logger(f"[v15-eval] {g.genome_id}: cannot materialize ({err[-300:]}); retry {attempt + 1}/3")
                self.worktrees.remove(g.genome_id)
                time.sleep(60)
                path, err = self.worktrees.materialize(g)
            if err and "patch does not apply" not in err:
                raise RuntimeError(f"{g.genome_id}: worktree cannot be materialized (infrastructure): {err[-500:]}")
            if err:
                self.logger(f"[v15-eval] {g.genome_id}: cannot materialize ({err}); scoring as failed")
                for t in todo:
                    g.results[t] = TaskResult(instance_id=t, status="materialize_error")
                continue
            prepared[g.genome_id] = (g, path, self.genome_config_path(g), todo)
        if not prepared:
            return
        order = interleave_jobs(list(prepared), {gid: v[3] for gid, v in prepared.items()}, list(task_ids))
        jobs = [(prepared[gid][0], t, prepared[gid][1], prepared[gid][2]) for gid, t in order]
        self.logger(f"[v15-eval] {len(jobs)} job(s) across {len(prepared)} genome(s), "
                    f"{self.workers} worker(s), prewarm={self.pool.lookahead}")
        self.pool.set_queue([t for g, t, _, _ in jobs if not os.path.exists(
            os.path.join(self.evals_root, g.genome_id, t, "grade.json"))])
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            futs = {pool.submit(self._run_job, g, t, p, c): (g, t) for g, t, p, c in jobs}
            for i, fut in enumerate(as_completed(futs), 1):
                g, t = futs[fut]
                try:
                    r = fut.result()
                except Exception as exc:  # pragma: no cover
                    r = TaskResult(instance_id=t, status=f"error: {exc}"[:200])
                with self._lock:
                    g.results[t] = r
                self.logger(f"[v15-eval] ({i}/{len(jobs)}, {time.time()-t0:.0f}s) {g.genome_id} {t}: "
                            f"resolved={r.resolved} f2p={r.f2p_passed}/{r.f2p_total} tokens={r.tokens//1000}k "
                            f"stop={r.stop} status={r.status}")
        self.pool.drain()
        self.logger(f"[v15-eval] pod pool: {self.pool.stats}")
