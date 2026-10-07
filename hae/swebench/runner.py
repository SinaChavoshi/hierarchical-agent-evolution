"""The SWE-bench runner: an epistemic firm pointed at a task repository through an executor.

What changes relative to `HierarchicalCompanyRunner.run_epistemic_search`
--------------------------------------------------------------------------
* **Workspace.** `ExecutorWorkspace` replaces `AgentWorkspace`: every read
  and write the firm makes goes through the `CommandExecutor` to the task
  repository (local directory, Docker container or k8s pod). The Monotonic
  Verification Guard of `AgentWorkspace.write_file` is BYPASSED here -- it
  compares oracle scores of this repository's own benchmark modules, which do
  not exist for a task repository -- so a write is accepted whenever the
  executor can perform it. Edits are in place; the gatekeeper's revert path
  (`previous_source`) is the undo.
* **Seeding.** `seed_from_problem_statement` writes the root reproduction
  question and up to three path sub-questions. `reconcile_with_oracle` is
  NEVER called: there is no held-out oracle on this path.
* **Gatekeeper.** Built with the executor (`executor=`, `repo_root`,
  `python_executable`, optional `test_command`); probes and module checks
  run in the container.
* **Self-oracle.** After every iteration that wrote a patch,
  `reconcile_with_reproduction` re-runs the root question's supported
  reproduction probes and marks the root RESOLVED when the bug no longer
  reproduces (definition in the gatekeeper). It is weaker than the hidden
  FAIL_TO_PASS / PASS_TO_PASS tests: a probe can only disprove the behaviour
  it encoded, it cannot see regressions elsewhere, and a sloppy probe can be
  "fixed" by an unrelated change. The hidden tests are never read.
* **Output.** The run record is `_finalize_run_output(...)` plus the ledger,
  the per-iteration searches, a `swebench` block and the exported
  `prediction` (`hae.swebench.export`).

The System 1 adapters (`_propose_hypotheses_adapter`,
`_synthesize_patch_adapter`) and `_execute_agent*` are inherited unchanged;
`_get_focused_module_context` is overridden so prompts see the task header,
the problem statement, a capped repository map, the current source of the
target module (windowed when large) and the probe rules.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from hae.epistemic.gatekeeper import EvidenceGatekeeper
from hae.epistemic.ledger import Q_CERTIFIED, Q_RESOLVED, EpistemicState, Question
from hae.epistemic.mcts import STOP_RESOLVED, EpistemicSearchLoop
from hae.epistemic.moves import (
    MOVE_ASK_QUESTION, MOVE_PROPOSE_HYPOTHESIS, MOVE_RUN_EXPERIMENT, MOVE_SYNTHESIZE, extract_json_object,
)
from hae.epistemic.value import EpistemicValueFunction, load_policy_heads
from hae.genome.schema import CompanyGenome, EpistemicPolicyGene
from hae.runtime.company import HierarchicalCompanyRunner
from hae.swebench.dataset import DATA_DIR, SweTask, find_task
from hae.swebench.executor import (
    SCRATCH_DIR, CommandExecutor, DockerExecutor, KubectlExecutor, LocalExecutor,
)
from hae.swebench.export import DEFAULT_MODEL_NAME, export_prediction, write_prediction
from hae.swebench.task import (
    REPO_MAP_CAP, build_repo_map, focus_lines_for, module_view, resolve_paths, root_question,
    seed_from_problem_statement, task_features,
)
from hae.task.budget import Budget

# Probes in a real repository import real packages; the policy's default 20 s
# (sized for this repository's stdlib-only modules) is too short for e.g. astropy.
PROBE_TIMEOUT_MIN_S = 120
# `AgentWorkspace.read_file` caps at 200 kB and the synthesis ladder treats a
# capped read as "module absent" (author mode, whole-file write). A task
# module that large must not be overwritten by a rewrite, so writes to it are
# refused in this mode.
MAX_EDITABLE_MODULE_CHARS = 200000
MAX_BUNDLE_FILES = 40
STATEMENT_PROMPT_CHARS = 12000
DEFAULT_MAX_ITERATIONS = 3

LOCATE_SCHEMA: Dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "LocatePacket",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "packet": {"type": "string", "enum": ["LOCATE"]},
                "path": {"type": "string", "maxLength": 200},
                "rationale": {"type": "string", "maxLength": 300},
            },
            "required": ["packet", "path", "rationale"],
            "additionalProperties": False,
        },
    },
}

PROBE_RULES = (
    "PROBE RULES FOR THIS TASK (SWE-bench mode):\n"
    "- A probe is a standalone Python script. It runs from the REPOSITORY ROOT with the task environment's "
    "own interpreter, so `import <package>` resolves to the checked-out (edited) source. Import the "
    "repository's package exactly as a user would; do not import `hae`.\n"
    "- The prediction describes what the CURRENT, BUGGY code does. Write the reproduction probe so that it "
    "exits non-zero or prints a `BUG:` marker WHILE THE BUG IS PRESENT and exits 0 quietly once it is fixed: "
    "the Evidence Gatekeeper re-runs the supported reproduction probes after every patch and declares the "
    "bug gone only when the prediction stops holding AND the probe exits 0.\n"
    "- Forbidden (the probe is refused, no verdict, and it costs the firm): network access, absolute paths, "
    "`../`, `__file__`, `sys.path` edits, `PYTHONPATH`, `site-packages`, running the repository's test-suite "
    "or `pytest` from the probe, and any mention of the repository root path.\n"
    "- Patches are edits to files that already exist in the repository. The firm's answer is the git diff "
    "of the repository; do not create helper scripts or notes as files."
)


# --------------------------------------------------------------------------- #
# Workspace over an executor
# --------------------------------------------------------------------------- #

class ExecutorWorkspace:
    """`AgentWorkspace`'s surface, backed by a `CommandExecutor` on the task repository.

    NO Monotonic Verification Guard: `AgentWorkspace.write_file` refuses a
    `.py` write that would score lower on this repository's held-out
    benchmark than the current file; that guard has no meaning for a task
    repository and is bypassed here (module docstring). What remains: path
    hygiene (no `..`, no absolute paths, no writes outside the root), a
    refusal to overwrite modules too large for the in-place synthesis
    ladder, and an audit list of every write.
    """

    def __init__(self, executor: CommandExecutor, repo_map: Callable[[], List[str]],
                 company_id: str = "swebench") -> None:
        self.executor = executor
        self.company_id = company_id
        self._repo_map = repo_map
        self.workspace_dir = executor.root
        self.writes: List[Dict[str, Any]] = []
        self.refused: List[Dict[str, Any]] = []

    @property
    def path(self) -> str:
        return self.workspace_dir

    @staticmethod
    def _clean(relative_path: str) -> str:
        rel = str(relative_path or "").replace("\\", "/").strip()
        while rel.startswith("./"):
            rel = rel[2:]
        if not rel or rel.startswith("/") or ".." in rel.split("/"):
            raise ValueError(f"path outside the repository: {relative_path!r}")
        return rel

    def write_file(self, relative_path: str, content: str) -> Dict[str, Any]:
        try:
            rel = self._clean(relative_path)
        except ValueError as exc:
            self.refused.append({"path": relative_path, "reason": str(exc)})
            return {"status": "error", "path": relative_path, "error": str(exc)}
        content = content if isinstance(content, str) else str(content)
        if rel.endswith(".py"):
            try:
                existing = self.executor.read_file(rel) if self.executor.exists(rel) else ""
            except Exception:
                existing = ""
            if len(existing) >= MAX_EDITABLE_MODULE_CHARS:
                reason = (f"{rel} is {len(existing)} chars; modules of {MAX_EDITABLE_MODULE_CHARS}+ chars cannot be "
                          "rewritten in place in SWE-bench mode")
                self.refused.append({"path": rel, "reason": reason})
                return {"status": "error", "path": rel, "error": reason}
        try:
            self.executor.write_file(rel, content)
        except Exception as exc:
            self.refused.append({"path": rel, "reason": f"{type(exc).__name__}: {exc}"})
            return {"status": "error", "path": rel, "error": f"{type(exc).__name__}: {exc}"}
        nbytes = len(content.encode("utf-8", errors="replace"))
        self.writes.append({"path": rel, "bytes": nbytes, "at": time.time()})
        return {"status": "ok", "path": rel, "bytes_written": nbytes}

    def read_file(self, relative_path: str, max_bytes: int = MAX_EDITABLE_MODULE_CHARS) -> Dict[str, Any]:
        try:
            rel = self._clean(relative_path)
            if not self.executor.exists(rel):
                return {"status": "error", "path": rel, "error": "not found", "content": ""}
            content = self.executor.read_file(rel)
        except Exception as exc:
            return {"status": "error", "path": relative_path, "error": f"{type(exc).__name__}: {exc}", "content": ""}
        truncated = len(content) > max_bytes
        return {"status": "ok", "path": rel, "content": content[:max_bytes], "truncated": truncated}

    def list_files(self, subpath: str = "") -> List[Dict[str, Any]]:
        prefix = subpath.strip("/") + "/" if subpath and subpath.strip("/") else ""
        return [{"path": p, "size": 0} for p in self._repo_map() if not prefix or p.startswith(prefix)]

    def get_file_tree(self) -> str:
        files = self._repo_map()
        if not files:
            return "(repository map unavailable)"
        head = f"(repository root: {self.workspace_dir}; {len(files)} most relevant tracked .py files)"
        return head + "\n" + "\n".join(f"- {p}" for p in files)

    def export_bundle(self) -> Dict[str, str]:
        """Only files that differ from HEAD (plus untracked, non-scratch files): the firm's authored set."""
        out: Dict[str, str] = {}
        try:
            changed = self.executor.changed_files()
        except Exception:
            changed = []
        for rel in changed[:MAX_BUNDLE_FILES]:
            if rel.startswith(SCRATCH_DIR + "/"):
                continue
            try:
                out[rel] = self.executor.read_file(rel)
            except Exception:
                continue
        return out

    def _sandbox_env(self) -> Dict[str, str]:
        return {}

    def execute_bash(self, command: str, timeout: int = 60) -> Dict[str, Any]:
        res = self.executor.run(["sh", "-c", command], cwd=self.executor.root, timeout_s=timeout)
        return {"status": "timeout" if res.timed_out else ("ok" if res.rc == 0 else "error"),
                "exit_code": res.rc, "stdout": res.stdout, "stderr": res.stderr}

    def cleanup(self) -> None:  # the container's life is the batch script's business
        return None


class _NoVerificationLoop:
    """The `verify` tool has no held-out harness to run in SWE-bench mode."""

    def __init__(self) -> None:
        self.used = 0
        self.budget = 0
        self.attempts: List[Any] = []

    @property
    def remaining(self) -> int:
        return 0

    def verify(self, turn: int = 0, agent_role: str = "") -> str:
        self.used += 1
        return ("Observation (verify): not available in SWE-bench mode -- there is no held-out harness here. "
                "Write the fix; the Evidence Gatekeeper checks the module and re-runs the reproduction probe.")

    def summary(self) -> Dict[str, Any]:
        return {"mode": "swebench", "verify_calls": self.used, "attempts": 0, "budget": 0}


# --------------------------------------------------------------------------- #
# The runner
# --------------------------------------------------------------------------- #

class SweBenchCompanyRunner(HierarchicalCompanyRunner):
    """`HierarchicalCompanyRunner` whose repository is a SWE-bench instance behind an executor."""

    def __init__(self, genome: CompanyGenome, task: SweTask, executor: CommandExecutor,
                 budget: Optional[Budget] = None, include_hints: bool = False,
                 test_command: Optional[Any] = None, model_name: str = DEFAULT_MODEL_NAME,
                 repo_map_cap: int = REPO_MAP_CAP) -> None:
        super().__init__(genome, budget=budget)
        # The base class made a scratch AgentWorkspace under /tmp; this run never uses it.
        try:
            self.workspace.cleanup()
        except Exception:
            pass
        self.task = task
        self.executor = executor
        self.include_hints = include_hints
        self.test_command = test_command
        self.model_name = model_name
        self.repo_map_cap = repo_map_cap
        self._py_files: Optional[List[str]] = None
        self._repo_map_cache: Optional[List[str]] = None
        self.seed_info: Dict[str, Any] = {}
        self.reproduction_log: List[Dict[str, Any]] = []
        self.iteration_log: List[Dict[str, Any]] = []
        self.locate_log: List[Dict[str, Any]] = []
        self.workspace = ExecutorWorkspace(executor, self.repo_map, company_id=genome.company_id)
        self.verification_loop = _NoVerificationLoop()
        # No graded modules: the synthesiser only targets a question's own module.
        self._required_modules = []
        self._pending_modules = []

    # -- repository views ---------------------------------------------- #

    def py_files(self) -> List[str]:
        if self._py_files is None:
            res = self.executor.run(["git", "ls-files"], cwd=self.executor.root, timeout_s=120)
            files = [l.strip() for l in res.stdout.splitlines() if l.strip().endswith(".py")] if res.rc == 0 else []
            self._py_files = sorted(set(files))
        return self._py_files

    def repo_map(self) -> List[str]:
        if self._repo_map_cache is None:
            paths = list(self.seed_info.get("paths") or [])
            self._repo_map_cache = build_repo_map(self.py_files(), self.task, paths, cap=self.repo_map_cap)
        return self._repo_map_cache

    def objective_text(self) -> str:
        t = self.task
        hints = f"\n\nISSUE DISCUSSION (hints_text, enabled explicitly):\n{t.hints_text[:4000]}" if t.hints_text else ""
        return (f"SWE-bench instance {t.instance_id}: repository {t.repo} at commit {t.base_commit[:12]}"
                f" (version {t.version or '?'}).\n\nPROBLEM STATEMENT (GitHub issue):\n"
                f"{t.problem_statement[:STATEMENT_PROMPT_CHARS]}{hints}")

    def _task_header(self) -> str:
        t = self.task
        return (f"TASK: fix the behaviour reported in the issue below by editing the repository `{t.repo}` "
                f"(checked out at `{self.executor.root}`, commit {t.base_commit[:12]}) in place. The answer is the "
                f"resulting `git diff`. Interpreter: `{self.executor.python}`.")

    def _module_source(self, rel: str) -> str:
        try:
            if rel and self.executor.exists(rel):
                return self.executor.read_file(rel)
        except Exception:
            pass
        return ""

    def _get_focused_module_context(self, target_mod: str, objective: str) -> str:
        """Prompt context for both adapters: task header, statement, repo map, target source, probe rules."""
        parts = [self._task_header(), objective]
        files = self.repo_map()
        parts.append("REPOSITORY MAP (tracked .py files most related to the issue; paths are relative to the root):\n"
                     + ("\n".join(f"- {p}" for p in files) if files else "(unavailable)"))
        target = (target_mod or "").lstrip("./")
        if target:
            source = self._module_source(target)
            if source:
                view = module_view(source, focus_lines_for(source, self.task.problem_statement, target))
                parts.append(f"CURRENT SOURCE OF `{target}` (edit THIS file in place; it is the module under "
                             f"investigation):\n```python\n{view}\n```")
            else:
                parts.append(f"`{target}` does not exist in the repository; choose an existing module from the map.")
        parts.append(PROBE_RULES)
        return "\n\n".join(parts)

    def _epistemic_module_context(self, module: str, objective: str, cap: int = 24000) -> str:
        ctx = self._get_focused_module_context(module, objective)
        if len(ctx) > cap:
            ctx = ctx[:cap] + "\n# [CONTEXT TRUNCATED FOR THE PROPOSER; THE SYNTHESISER SEES THE FULL MODULE]"
        return ctx

    # -- gatekeeper ----------------------------------------------------- #

    def _make_gatekeeper(self, policy: Optional[EpistemicPolicyGene] = None) -> EvidenceGatekeeper:
        policy = policy or self.epistemic_policy
        return EvidenceGatekeeper(
            self.workspace,
            timeout_s=max(int(policy.experiment_timeout_s), PROBE_TIMEOUT_MIN_S),
            max_probe_lines=policy.max_probe_lines,
            graded_modules=[], module_for_tag={},
            executor=self.executor, repo_root=self.executor.root, python_executable=self.executor.python,
            test_command=self.test_command, isolate=False, stage_reference=False,
            logger=lambda msg: print(msg, flush=True),
        )

    # -- locating the module when the issue names none ------------------ #

    def locate_module(self, question: Question, agent: Any, objective: str) -> str:
        """One LOCATE call: which existing module should the root question investigate?

        Used only when the statement named no resolvable path. The answer is
        validated against the repository's tracked files (suffix match) and
        written to `question.module` -- metadata, not a belief; the ledger's
        authority rules cover status and uncertainty only.
        """
        prompt = (
            "EPISTEMIC MOVE: LOCATE -- name the ONE existing module most likely to contain the code whose "
            "behaviour the issue reports. Reply with a LOCATE packet: `path` (a repository-relative path from "
            "the REPOSITORY MAP, or another tracked .py file) and `rationale` (one sentence)."
        )
        raw = ""
        try:
            raw = self._execute_agent(agent, prompt, context=self._epistemic_module_context("", objective),
                                      response_format=LOCATE_SCHEMA, max_tokens=400)
        except Exception as exc:
            self.locate_log.append({"error": f"{type(exc).__name__}: {exc}"})
        data = extract_json_object(raw or "") or {}
        cand = str(data.get("path", "") or "").strip()
        resolved = resolve_paths([cand], self.py_files()) if cand else []
        chosen = resolved[0] if resolved else ""
        self.locate_log.append({"candidate": cand, "resolved": chosen, "rationale": str(data.get("rationale", ""))[:300]})
        if chosen:
            question.module = chosen
            if chosen not in self.seed_info.get("paths", []):
                self.seed_info.setdefault("paths", []).insert(0, chosen)
                self._repo_map_cache = None
        print(f"[swebench] {self.genome.company_id}: LOCATE -> {chosen or '(none)'} (candidate {cand!r})", flush=True)
        return chosen

    # -- the loop -------------------------------------------------------- #

    def _diff_hash(self) -> str:
        try:
            return hashlib.sha256(self.executor.diff().encode("utf-8", errors="replace")).hexdigest()
        except Exception:
            return ""

    def run_swebench(self, budget_moves: Optional[int] = None,
                     max_iterations: int = DEFAULT_MAX_ITERATIONS) -> Dict[str, Any]:
        """Seed -> search -> (patch?) -> self-oracle recheck, until resolved or the budget is spent.

        `budget_moves` is the TOTAL across iterations (default: the policy's
        `search_budget_moves`). An iteration is one `EpistemicSearchLoop.run`
        over the remaining moves; after an iteration that wrote a patch the
        root question's reproduction probes are re-run. Stops when the
        self-oracle says the bug is gone, when the budget is exhausted, when
        an iteration used no moves, or after `max_iterations`.
        """
        start_time = time.time()
        policy = self.epistemic_policy
        if not getattr(self.genome.ceo, "model_tier", ""):
            self.genome.ceo.model_tier = "executive"
        if self.epistemic_state is None:
            self.epistemic_state = EpistemicState(self.genome.company_id)
        state = self.epistemic_state
        gatekeeper = self._make_gatekeeper(policy)
        agents = self._bind_epistemic_agents(policy)
        objective = self.objective_text()

        seed = seed_from_problem_statement(state, self.task, gatekeeper.authority, known_files=self.py_files())
        self.seed_info = seed
        self._repo_map_cache = None
        root = state.questions[seed["root_question_id"]]
        if not root.module:
            self.locate_module(root, agents["hypothesis"], objective)
        self._required_modules = [root.module] if root.module else []
        features = task_features(self.task, seed.get("paths", []))
        print(f"[swebench] {self.genome.company_id}: {self.task.instance_id} seeded root={root.question_id} "
              f"module={root.module or '(none)'} subs={seed['sub_question_ids']} "
              f"paths={seed['paths'][:3]} complexity={features.complexity:.2f}", flush=True)

        total_budget = int(budget_moves if budget_moves is not None else policy.search_budget_moves)
        remaining = total_budget
        heads = load_policy_heads(policy.extra, logger=lambda m: print(f"[epistemic] {self.genome.company_id} {m}", flush=True))
        last_hash = self._diff_hash()
        last_search: Dict[str, Any] = {}
        last_recheck: Optional[Dict[str, Any]] = None
        stop = ""
        for iteration in range(1, max(1, int(max_iterations)) + 1):
            if remaining <= 0:
                stop = "budget"
                break
            if not self._may_call("swebench iteration"):
                stop = "tokens"
                break
            resolved_before = {qid for qid, q in state.questions.items() if q.status == Q_RESOLVED}
            seed_material = f"{self.genome.company_id}:{self.task.instance_id}:{iteration}".encode("utf-8")
            loop = EpistemicSearchLoop(
                state, gatekeeper,
                EpistemicValueFunction(policy.value_alpha, head=heads.value_head if heads.value_head_live else None),
                policy,
                propose_hypotheses=self._propose_hypotheses_adapter(agents["hypothesis"], objective, policy),
                synthesize_patch=self._synthesize_patch_adapter(agents["synthesis"], objective),
                revert_patch=lambda path, src: self.workspace.write_file(path, src),
                may_continue=lambda: self._may_call("epistemic search move"),
                agent_roles={MOVE_PROPOSE_HYPOTHESIS: agents["hypothesis"].role,
                             MOVE_RUN_EXPERIMENT: "EvidenceGatekeeper",
                             MOVE_SYNTHESIZE: agents["synthesis"].role,
                             MOVE_ASK_QUESTION: agents["question"].role},
                rng_seed=int(hashlib.sha256(seed_material).hexdigest()[:8], 16),
                logger=lambda msg: print(
                    f"[epistemic] {self.genome.company_id} "
                    f"{msg[len('[epistemic] '):] if msg.startswith('[epistemic] ') else msg}", flush=True),
                prior_head=heads.prior_head, prior_head_weight=heads.prior_head_weight, value_head=heads.value_head,
            )
            result = loop.run(remaining)
            remaining -= int(result.moves_used)
            search = result.to_dict()
            search["iteration"] = iteration
            self.epistemic_searches.append(search)
            self._merge_search_stats(result.stats)
            last_search = search
            resolved_now = [qid for qid, q in state.questions.items()
                            if q.status == Q_RESOLVED and qid not in resolved_before]
            new_hash = self._diff_hash()
            patched = bool(result.synthesized_paths) or (new_hash != last_hash)
            last_hash = new_hash
            recheck: Optional[Dict[str, Any]] = None
            if patched:
                recheck = gatekeeper.reconcile_with_reproduction(state, root.question_id, iteration=iteration,
                                                                  resolved_this_iteration=resolved_now)
                self.reproduction_log.append(recheck)
                last_recheck = recheck
            entry = {"iteration": iteration, "moves_used": result.moves_used, "stop_reason": result.stop_reason,
                     "synthesized_paths": list(result.synthesized_paths), "patched": patched,
                     "resolved_this_iteration": resolved_now,
                     "recheck": None if recheck is None else {k: v for k, v in recheck.items() if k != "reruns"}}
            self.iteration_log.append(entry)
            print(f"[swebench] {self.genome.company_id} iteration {iteration}: {result.moves_used} moves, "
                  f"stop={result.stop_reason}, patched={patched}, "
                  f"self-oracle={recheck['verdict'] if recheck else 'not run'}, remaining={remaining}", flush=True)
            if recheck is not None and recheck["verdict"] == "resolved":
                stop = "resolved"
                break
            if result.moves_used == 0:
                stop = "no_progress"
                break
            if result.stop_reason == STOP_RESOLVED and (recheck is None or recheck["verdict"] == "inconclusive"):
                stop = "all_questions_resolved_unconfirmed"
                break
        else:
            stop = stop or "max_iterations"
        if not stop:
            stop = "budget" if remaining <= 0 else "max_iterations"

        prediction = export_prediction(self.executor, self.task, self.model_name)
        resolved = bool(last_recheck and last_recheck["verdict"] == "resolved"
                        and root.status in (Q_RESOLVED, Q_CERTIFIED))
        swe_block = {
            "instance_id": self.task.instance_id, "repo": self.task.repo, "base_commit": self.task.base_commit,
            "executor": self.executor.describe(), "root_question_id": root.question_id,
            "root_module": root.module, "root_status": root.status, "resolved_by_self_oracle": resolved,
            "stop": stop, "iterations": self.iteration_log, "budget_moves": total_budget,
            "moves_used": total_budget - remaining, "seed": seed, "locate": self.locate_log,
            "task_features": features.to_dict(), "reproduction": self.reproduction_log,
            "patch_chars": len(prediction["model_patch"]), "changed_files": self.executor.changed_files(),
            "workspace_writes": len(self.workspace.writes), "workspace_refusals": self.workspace.refused,
            "hints_included": bool(self.task.hints_text),
        }
        deliverable = self._epistemic_deliverable(state, last_search, len(self.iteration_log))
        deliverable += (f"\n\n### SWE-bench {self.task.instance_id}\n```json\n"
                        f"{json.dumps({k: v for k, v in swe_block.items() if k not in ('reproduction', 'seed')}, indent=1)}\n```")
        briefs = {"swebench": json.dumps({"instance_id": self.task.instance_id, "resolved_by_self_oracle": resolved,
                                          "stop": stop, "moves_used": total_budget - remaining})}
        deliverable, bundle = self._append_workspace_files(deliverable)
        return self._finalize_run_output(
            start_time, deliverable, briefs, bundle,
            extra={"epistemic_ledger": state.to_dict(), "epistemic_search": last_search,
                   "epistemic_searches": list(self.epistemic_searches), "epistemic_iteration": len(self.iteration_log),
                   "swebench": swe_block, "prediction": prediction})


# --------------------------------------------------------------------------- #
# Script entry points (used by scripts/run_swebench_instance.py and the CLI)
# --------------------------------------------------------------------------- #

def make_executor(kind: str, container: str = "", pod: str = "", namespace: str = "default",
                  root: str = "", python: str = "") -> CommandExecutor:
    kind = (kind or "local").lower()
    if kind == "local":
        if not root:
            raise ValueError("--root is required for the local executor")
        return LocalExecutor(root, python=python or None)
    if kind == "docker":
        if not container:
            raise ValueError("--container is required for the docker executor")
        kw: Dict[str, Any] = {}
        if root:
            kw["root"] = root
        if python:
            kw["python"] = python
        return DockerExecutor(container, **kw)
    if kind == "kubectl":
        if not pod:
            raise ValueError("--pod is required for the kubectl executor")
        kw = {"namespace": namespace}
        if container:
            kw["container"] = container
        if root:
            kw["root"] = root
        if python:
            kw["python"] = python
        return KubectlExecutor(pod, **kw)
    raise ValueError(f"unknown executor kind {kind!r}")


def load_genome(path: str) -> CompanyGenome:
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if isinstance(data, dict) and "genome" in data and isinstance(data["genome"], dict):
        data = data["genome"]
    return CompanyGenome.from_dict(data)


class _Tee:
    def __init__(self, *streams: Any) -> None:
        self.streams = streams

    def write(self, s: str) -> int:
        for st in self.streams:
            st.write(s)
        return len(s)

    def flush(self) -> None:
        for st in self.streams:
            st.flush()


def run_instance(instance_id: str, dataset_path: str, executor: CommandExecutor, genome: CompanyGenome,
                 out_dir: str, budget_moves: Optional[int] = None, max_iterations: int = DEFAULT_MAX_ITERATIONS,
                 include_hints: bool = False, test_command: Optional[str] = None,
                 model_name: str = DEFAULT_MODEL_NAME, budget_usd: Optional[float] = None) -> Dict[str, Any]:
    """Runs one instance end to end and writes prediction.json, record.json and firm.log into `out_dir`."""
    task = find_task(dataset_path, instance_id, include_hints=include_hints)
    os.makedirs(out_dir, exist_ok=True)
    budget = Budget(limit_usd=float(budget_usd)) if budget_usd else None
    log_path = os.path.join(out_dir, "firm.log")
    real_stdout = sys.stdout
    started = time.time()
    with open(log_path, "a", encoding="utf-8") as log_fh:
        sys.stdout = _Tee(real_stdout, log_fh)
        try:
            runner = SweBenchCompanyRunner(genome, task, executor, budget=budget, include_hints=include_hints,
                                           test_command=test_command, model_name=model_name)
            output = runner.run_swebench(budget_moves=budget_moves, max_iterations=max_iterations)
        finally:
            sys.stdout = real_stdout
    prediction = output["prediction"]
    write_prediction(os.path.join(out_dir, "prediction.json"), prediction)
    audit = runner.epistemic_audit(token_usage=int(output.get("token_usage", 0) or 0))
    record = {
        "instance_id": task.instance_id, "task": task.to_dict(), "genome_id": genome.company_id,
        "generation": genome.generation, "executor": executor.describe(),
        "elapsed_seconds": round(time.time() - started, 2),
        "token_usage": output.get("token_usage"), "token_accounting": output.get("token_accounting"),
        "opex": output.get("opex"), "budget": output.get("budget"), "budget_exhausted": output.get("budget_exhausted"),
        "swebench": output.get("swebench"), "epistemic_ledger": output.get("epistemic_ledger"),
        "epistemic_searches": output.get("epistemic_searches"), "epistemic_audit": audit,
        "verification_loop": output.get("verification_loop"), "authored_files": output.get("authored_files"),
        "prediction_chars": len(prediction.get("model_patch", "")),
    }
    org_state = getattr(runner, "org_state", None)
    if org_state is not None and hasattr(org_state, "to_dict"):
        try:
            record["org"] = org_state.to_dict()
        except Exception:
            pass
    with open(os.path.join(out_dir, "record.json"), "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=1, sort_keys=True, default=str)
        fh.write("\n")
    return record


def default_dataset_path(name: str = "swebench_dev") -> str:
    return os.path.join(DATA_DIR, f"{name}.jsonl")


def main(argv: Optional[Sequence[str]] = None) -> int:
    """`python -m hae.cli --mode swebench ...` / `scripts/run_swebench_instance.py` share this parser."""
    import argparse
    ap = argparse.ArgumentParser(prog="hae swebench", description="Run one SWE-bench instance with an HAE firm.")
    ap.add_argument("--instance-id", required=True)
    ap.add_argument("--dataset", default=default_dataset_path(), help="jsonl split file (data/swebench/*.jsonl)")
    ap.add_argument("--executor", choices=["local", "docker", "kubectl"], default="docker")
    ap.add_argument("--container", default="", help="docker container name, or k8s container name with --executor kubectl")
    ap.add_argument("--pod", default="")
    ap.add_argument("--namespace", default="default")
    ap.add_argument("--root", default="", help="repository root (default /testbed; required for local)")
    ap.add_argument("--python", default="", help="interpreter inside the executor (default: the testbed env)")
    ap.add_argument("--genome", required=True, help="CompanyGenome JSON")
    ap.add_argument("--out-dir", required=True, help="e.g. results/swebench/<run>/<instance_id>/")
    ap.add_argument("--budget-moves", type=int, default=None)
    ap.add_argument("--max-iterations", type=int, default=DEFAULT_MAX_ITERATIONS)
    ap.add_argument("--budget-usd", type=float, default=None)
    ap.add_argument("--include-hints", action="store_true")
    ap.add_argument("--test-command", default=None, help="shell command replacing the proximity test heuristic")
    ap.add_argument("--model-name", default=DEFAULT_MODEL_NAME)
    args = ap.parse_args(list(argv) if argv is not None else None)
    executor = make_executor(args.executor, container=args.container, pod=args.pod, namespace=args.namespace,
                             root=args.root, python=args.python)
    genome = load_genome(args.genome)
    record = run_instance(args.instance_id, args.dataset, executor, genome, args.out_dir,
                          budget_moves=args.budget_moves, max_iterations=args.max_iterations,
                          include_hints=args.include_hints, test_command=args.test_command,
                          model_name=args.model_name, budget_usd=args.budget_usd)
    swe = record.get("swebench") or {}
    print(json.dumps({"instance_id": record["instance_id"], "resolved_by_self_oracle": swe.get("resolved_by_self_oracle"),
                      "stop": swe.get("stop"), "moves_used": swe.get("moves_used"),
                      "prediction_chars": record.get("prediction_chars"), "out_dir": args.out_dir}))
    return 0
