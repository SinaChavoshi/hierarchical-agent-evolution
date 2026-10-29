"""Evaluate `HarnessGenome`s on SWE-bench tasks in GKE testbed pods.

Each (genome, task) job:
  1. runs the agent from the genome's materialized worktree (`harness_root`) with the genome's knobs,
  2. grades the prediction with this repository's `scripts/grade_in_testbed.py` (never the candidate's),
  3. caches `prediction.json` / `record.json` / `grade.json` so a restarted run resumes.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from hae.evolution.genome import HarnessGenome, TaskResult
from hae.evolution.pod_pool import PodPool, interleave_jobs
from hae.evolution.workspace import WorktreeManager

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


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
                 logger: Callable[[str], None] = print, prewarm_pods: int = 4) -> None:
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
        self._lock = threading.Lock()
        self.pool = PodPool(create=self._ensure_pod, delete=lambda pod: self.batch.delete_pod(pod, self.context),
                            name=lambda iid: self._pod_name("pool", iid), lookahead=prewarm_pods)

    # ------------------------------------------------------------------ #
    def genome_config_path(self, genome: HarnessGenome) -> str:
        cfg = copy.deepcopy(self.seed_cfg)
        cfg["company_id"] = f"v15_{genome.genome_id}"
        pol = cfg.setdefault("epistemic_policy", {})
        pol["v15_knobs"] = dict(genome.knobs)  # unknown keys land in `EpistemicPolicyGene.extra`
        # V16: the genome's organization. org_mode off leaves the harness exactly V15.
        pol["v16_org"] = genome.org_mode
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

    def _result_from_dir(self, genome_id: str, instance_id: str, status: str = "ok") -> TaskResult:
        d = os.path.join(self.evals_root, genome_id, instance_id)
        rec, grade = {}, {}
        try:
            rec = json.load(open(os.path.join(d, "record.json"), encoding="utf-8"))
        except Exception:
            pass
        try:
            grade = json.load(open(os.path.join(d, "grade.json"), encoding="utf-8"))
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
        )

    def org_records(self, genome_id: str, task_ids: Optional[Sequence[str]] = None) -> List[Dict[str, Any]]:
        """`record.json["swebench"]["org"]` blocks (plus the graded outcome) of a genome's runs."""
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
            org = (rec.get("swebench") or {}).get("org")
            if org:
                out.append({"task": t, "resolved": bool(grade.get("resolved")), "org": org})
        return out

    def log_tail(self, genome_id: str, instance_id: str, n_chars: int = 6000) -> str:
        p = os.path.join(self.evals_root, genome_id, f"{instance_id}.stdout.log")
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as fh:
                return fh.read()[-n_chars:]
        except OSError:
            return ""

    def _run_job(self, genome: HarnessGenome, instance_id: str, harness_root: str, genome_cfg: str) -> TaskResult:
        """Run one job, retrying (up to 2 more times) when it failed for infrastructure reasons.

        A failure caused by the Kubernetes API or a pod (not by the agent) must not count
        against the genome, so such runs are discarded and re-run.
        """
        result = self._run_job_once(genome, instance_id, harness_root, genome_cfg)
        for attempt in range(2):
            if not self._is_infra_failure(genome.genome_id, instance_id, result):
                break
            self.logger(f"[v15-eval] {genome.genome_id} {instance_id}: infrastructure failure "
                        f"({result.status}); discarding and retrying ({attempt + 1}/2)")
            d = os.path.join(self.evals_root, genome.genome_id, instance_id)
            import shutil
            shutil.rmtree(d, ignore_errors=True)
            time.sleep(30)
            result = self._run_job_once(genome, instance_id, harness_root, genome_cfg)
        return result

    _INFRA_MARKERS = ("Unable to connect to the server", "TLS handshake timeout", "i/o timeout",
                      "connection refused", "error dialing backend", "the server is currently unable",
                      "dial tcp", "connection timed out")

    # Signs that the sandbox pod was starved (CPU/memory overcommit on the node) before the agent
    # even started: the package-import probe timed out or was OOM-killed.
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
            # A grade with no FAIL_TO_PASS results means grading itself failed (e.g. kubectl exec
            # timeout during the hidden-test run), not that the agent failed.
            try:
                grade = json.load(open(grade_path, encoding="utf-8"))
            except Exception:
                return True
            # An empty model patch is a genuine agent failure (graded without running tests).
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
            return self._result_from_dir(genome.genome_id, instance_id)
        pod, err = self.pool.acquire(instance_id)
        if err:
            self.pool.release(pod)
            self.logger(f"[v15-eval] {genome.genome_id} {instance_id}: {err}")
            return TaskResult(instance_id=instance_id, status=f"pod_error")
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
                # Record the timeout as a graded failure so the job is not retried forever.
                os.makedirs(d, exist_ok=True)
                with open(os.path.join(d, "grade.json"), "w", encoding="utf-8") as fh:
                    json.dump({"instance_id": instance_id, "resolved": False, "note": "agent timeout"}, fh)
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
            if err:
                self.logger(f"[v15-eval] {g.genome_id}: cannot materialize ({err}); scoring as failed")
                for t in todo:
                    g.results[t] = TaskResult(instance_id=t, status="materialize_error")
                continue
            prepared[g.genome_id] = (g, path, self.genome_config_path(g), todo)
        if not prepared:
            return
        # Interleave so every genome runs under the same cluster load over the course of the run.
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
        self.logger(f"[v15-eval] pod pool: {self.pool.stats}")
