"""Two-Tier `L0-Macro` Compositional Milestone DAG & Persistent Git Commit Ratchet.

Scales HAE's V13 Context-Bounded Hierarchical AB-MCTS (`L0-Micro -> L1 -> L2`)
from single-horizon bug fixes (10-120 LoC) to multi-stage, multi-hour/multi-day
system builds (5,000-100,000+ LoC across 10-50+ modules) without ever
overloading Layer `L1-A` (Reconnaissance) or Layer `L1-B` (Popperian Falsifier):

  1. **Compositional AND/OR Hierarchy**:
     - `L0-Macro` (`L0MacroPlanner` + `MilestoneDAG`) decomposes a complex
       system build into an acyclic topological AND-DAG of bounded milestones
       ($M_1 \\prec M_2 \\prec \\dots \\prec M_K$, each scoped to $< 500$ LoC).
     - `L0-Micro` (`HierarchicalAgenticMCTSEngine` / `HierarchicalMCTSSweEngine`)
       solves each single milestone $M_k$ using OR-tree AB-MCTS (`GEN` vs `CONT`)
       against $M_k$'s verification contract and acceptance command.
  2. **Persistent Git Commit Ratchet (`GitCommitRatchet`)**:
     - When $M_k$ is certified, `advance_ratchet()` commits $M_k$'s created and
       modified files to Git `HEAD` (`hae(ratchet): certify milestone <id> - <title>`),
       tags `hae-milestone-<id>`, archives $M_k$'s falsification script to
       `.hae/ratchet_suites/falsify_<id>.py`, and persists `.hae/ratchet_state.json`.
     - Because $M_k$ is now `HEAD`, `git diff HEAD` during $M_{k+1}$ contains
       **only $M_{k+1}$'s bounded delta ($< 500$ lines)** while $M_1 \\dots M_k$'s
       files remain intact on disk. `L1-A` and `L1-B` never see a 10,000-line diff!
     - If $M_{k+1}$ fails or crashes, `rollback_to_head()` executes
       `git reset --hard HEAD && git clean -fd -e .hae`, restoring the exact
       certified state of $M_k$ without losing prior subsystems.
  3. **Cumulative Regression Ratchet**:
     - Before certifying $M_k$, `run_cumulative_regression_gate()` runs the
       acceptance commands and archived falsification suites of **all previously
       certified milestones $M_1 \\dots M_{k-1}$ plus $M_k$**, guaranteeing
       monotonic system progress with zero regressions across milestones.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import inspect
import json
import os
import re
import time
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Union

from hae.epistemic.gatekeeper import EvidenceGatekeeper
from hae.epistemic.ledger import EpistemicState
from hae.hierarchical.verifier_plugin import (
    ShellCommandVerifierPlugin,
    VerificationGateResult,
    WorkspaceVerifierPlugin,
    compute_gate_score,
)
from hae.swebench.executor import CommandExecutor, SCRATCH_DIR
from hae.swebench.hierarchical_mcts import HierarchicalAgenticMCTSEngine
from hae.swebench.task import SweTask

# Alias used across V14 architecture docs and tests
HierarchicalMCTSSweEngine = HierarchicalAgenticMCTSEngine

VALID_MILESTONE_STATUSES = ("pending", "in_progress", "certified", "failed")


@dataclass
class MilestoneSpec:
    """Declarative contract for one bounded milestone $M_k$ in the `L0-Macro` DAG."""

    milestone_id: str
    title: str
    objective: str
    depends_on: List[str] = field(default_factory=list)
    target_files: List[str] = field(default_factory=list)
    acceptance_cmd: str = ""
    max_diff_lines: int = 500
    budget_moves: int = 36
    status: str = "pending"  # "pending" | "in_progress" | "certified" | "failed"
    certified_commit_sha: Optional[str] = None
    falsification_suite_path: Optional[str] = None
    mcts_summary: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.milestone_id = str(self.milestone_id).strip()
        if not self.milestone_id:
            raise ValueError("MilestoneSpec.milestone_id must be a non-empty string.")
        self.title = str(self.title or self.milestone_id).strip()
        self.objective = str(self.objective or self.title).strip()
        self.depends_on = [str(d).strip() for d in (self.depends_on or []) if str(d).strip()]
        self.target_files = [str(f).strip() for f in (self.target_files or []) if str(f).strip()]
        self.acceptance_cmd = str(self.acceptance_cmd or "").strip()
        self.max_diff_lines = max(1, int(self.max_diff_lines or 500))
        self.budget_moves = max(1, int(self.budget_moves or 36))
        if self.status not in VALID_MILESTONE_STATUSES:
            raise ValueError(
                f"Invalid milestone status {self.status!r}; must be one of {VALID_MILESTONE_STATUSES}."
            )
        if self.mcts_summary is None:
            self.mcts_summary = {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "milestone_id": self.milestone_id,
            "title": self.title,
            "objective": self.objective,
            "depends_on": list(self.depends_on),
            "target_files": list(self.target_files),
            "acceptance_cmd": self.acceptance_cmd,
            "max_diff_lines": self.max_diff_lines,
            "budget_moves": self.budget_moves,
            "status": self.status,
            "certified_commit_sha": self.certified_commit_sha,
            "falsification_suite_path": self.falsification_suite_path,
            "mcts_summary": dict(self.mcts_summary or {}),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> MilestoneSpec:
        return cls(
            milestone_id=str(data.get("milestone_id") or data.get("id") or ""),
            title=str(data.get("title") or data.get("name") or ""),
            objective=str(data.get("objective") or data.get("description") or ""),
            depends_on=list(data.get("depends_on") or []),
            target_files=list(data.get("target_files") or []),
            acceptance_cmd=str(data.get("acceptance_cmd") or data.get("test_cmd") or ""),
            max_diff_lines=int(data.get("max_diff_lines") or 500),
            budget_moves=int(data.get("budget_moves") or 36),
            status=str(data.get("status") or "pending"),
            certified_commit_sha=data.get("certified_commit_sha"),
            falsification_suite_path=data.get("falsification_suite_path"),
            mcts_summary=dict(data.get("mcts_summary") or {}),
        )


class MilestoneDAG:
    """Topological AND-DAG of `MilestoneSpec` nodes managed by Layer `L0-Macro`."""

    def __init__(
        self,
        milestones: Optional[Union[Sequence[MilestoneSpec], Mapping[str, MilestoneSpec]]] = None,
        system_objective: str = "",
    ) -> None:
        self.system_objective = str(system_objective or "").strip()
        self.milestones: Dict[str, MilestoneSpec] = {}
        if milestones is not None:
            if isinstance(milestones, Mapping):
                for k, v in milestones.items():
                    spec = v if isinstance(v, MilestoneSpec) else MilestoneSpec.from_dict(v)
                    self.milestones[str(k)] = spec
            else:
                for item in milestones:
                    spec = item if isinstance(item, MilestoneSpec) else MilestoneSpec.from_dict(item)
                    if spec.milestone_id in self.milestones:
                        raise ValueError(f"Duplicate milestone_id {spec.milestone_id!r} in MilestoneDAG.")
                    self.milestones[spec.milestone_id] = spec

    def add_milestone(self, spec: MilestoneSpec) -> None:
        if spec.milestone_id in self.milestones:
            raise ValueError(f"Duplicate milestone_id {spec.milestone_id!r} in MilestoneDAG.")
        self.milestones[spec.milestone_id] = spec

    def validate_dag(self) -> List[str]:
        """Validate that all dependencies exist and the graph is a strict DAG (acyclic).

        Returns the topological ordering of `milestone_id`s (preserving insertion order
        among simultaneously ready milestones). Raises `ValueError` on missing dependencies
        or cycles.
        """
        if not self.milestones:
            return []

        in_degree: Dict[str, int] = {mid: 0 for mid in self.milestones}
        dependents: Dict[str, List[str]] = {mid: [] for mid in self.milestones}

        for mid, spec in self.milestones.items():
            seen_deps = set()
            for dep in spec.depends_on:
                if dep == mid:
                    raise ValueError(f"Milestone {mid!r} cannot depend on itself (self-cycle).")
                if dep not in self.milestones:
                    raise ValueError(
                        f"Milestone {mid!r} depends on unknown milestone {dep!r}."
                    )
                if dep not in seen_deps:
                    seen_deps.add(dep)
                    in_degree[mid] += 1
                    dependents[dep].append(mid)

        ready: List[str] = [mid for mid in self.milestones if in_degree[mid] == 0]
        topo_order: List[str] = []

        while ready:
            curr = ready.pop(0)
            topo_order.append(curr)
            for child in dependents[curr]:
                in_degree[child] -= 1
                if in_degree[child] == 0:
                    ready.append(child)

        if len(topo_order) != len(self.milestones):
            cyclic = [mid for mid in self.milestones if mid not in topo_order]
            raise ValueError(
                f"Cycle detected in MilestoneDAG involving milestones: {cyclic}"
            )

        return topo_order

    def topological_order(self) -> List[str]:
        """Return the validated topological order of milestone IDs."""
        return self.validate_dag()

    def next_ready_milestone(self) -> Optional[MilestoneSpec]:
        """Return the first `pending` milestone in topological order whose dependencies are all `certified`."""
        order = self.validate_dag()
        for mid in order:
            spec = self.milestones[mid]
            if spec.status != "pending":
                continue
            if all(self.milestones[dep].status == "certified" for dep in spec.depends_on):
                return spec
        return None

    def certified_milestones(self) -> List[MilestoneSpec]:
        """Return all `certified` milestones in topological order."""
        order = self.validate_dag()
        return [self.milestones[mid] for mid in order if self.milestones[mid].status == "certified"]

    def is_complete(self) -> bool:
        """True iff the DAG is non-empty and every milestone has status `'certified'`."""
        return bool(self.milestones) and all(
            m.status == "certified" for m in self.milestones.values()
        )

    def to_dict(self) -> Dict[str, Any]:
        order = self.validate_dag() if self.milestones else []
        ordered_list = [self.milestones[mid].to_dict() for mid in order]
        return {
            "system_objective": self.system_objective,
            "topological_order": order,
            "milestones": ordered_list,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> MilestoneDAG:
        raw_milestones = data.get("milestones") or []
        specs: List[MilestoneSpec] = []
        if isinstance(raw_milestones, Mapping):
            for mid, mdata in raw_milestones.items():
                if isinstance(mdata, MilestoneSpec):
                    specs.append(mdata)
                else:
                    d = dict(mdata)
                    d.setdefault("milestone_id", mid)
                    specs.append(MilestoneSpec.from_dict(d))
        else:
            for item in raw_milestones:
                specs.append(item if isinstance(item, MilestoneSpec) else MilestoneSpec.from_dict(item))
        dag = cls(
            milestones=specs,
            system_objective=str(
                data.get("system_objective") or data.get("objective") or data.get("system_name") or ""
            ),
        )
        dag.validate_dag()
        return dag


def _slugify_id(text: str, index: int) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")
    slug = "_".join(slug.split("_")[:3]) or f"stage_{index}"
    return f"m{index}_{slug}"


def parse_macro_plan_response(
    raw_text: str,
    objective: str,
    default_acceptance_cmd: str = "pytest -q",
) -> MilestoneDAG:
    """Parse an `L0-Macro` LLM response into a validated `MilestoneDAG`, with a deterministic fallback."""
    if raw_text and raw_text.strip():
        json_match = re.search(r"\{.*\}", raw_text, flags=re.DOTALL)
        if json_match:
            try:
                payload = json.loads(json_match.group(0))
                if isinstance(payload, dict) and payload.get("milestones"):
                    dag = MilestoneDAG.from_dict(payload)
                    if not dag.system_objective:
                        dag.system_objective = objective
                    for m in dag.milestones.values():
                        if not m.acceptance_cmd:
                            m.acceptance_cmd = default_acceptance_cmd
                    dag.validate_dag()
                    return dag
            except Exception:
                pass

    # Resilient deterministic fallback: split explicit numbered/bulleted stages if present,
    # or synthesize a clean 2-stage Core -> Integration milestone DAG.
    stage_lines = [
        line.strip()
        for line in (objective or "").splitlines()
        if re.match(r"^\s*(?:milestone\s*\d+|\d+[\.\)]|[-*])\s+\S+", line, flags=re.IGNORECASE)
    ]
    specs: List[MilestoneSpec] = []
    if len(stage_lines) >= 2:
        prev_id: Optional[str] = None
        for idx, line in enumerate(stage_lines[:8], 1):
            clean = re.sub(r"^\s*(?:milestone\s*\d+\s*[:\-]?|\d+[\.\)]|[-*])\s*", "", line, flags=re.IGNORECASE).strip()
            title = clean.split(":", 1)[0].strip()[:80] or f"Milestone {idx}"
            mid = _slugify_id(title, idx)
            specs.append(
                MilestoneSpec(
                    milestone_id=mid,
                    title=title,
                    objective=clean,
                    depends_on=[prev_id] if prev_id else [],
                    target_files=[],
                    acceptance_cmd=default_acceptance_cmd,
                    max_diff_lines=500,
                    budget_moves=36,
                )
            )
            prev_id = mid
    else:
        specs = [
            MilestoneSpec(
                milestone_id="m1_core_foundation",
                title="Core Data Structures & Foundational Module",
                objective=f"Implement foundational data structures and core logic for: {objective}",
                depends_on=[],
                target_files=[],
                acceptance_cmd=default_acceptance_cmd,
                max_diff_lines=500,
                budget_moves=36,
            ),
            MilestoneSpec(
                milestone_id="m2_integration_and_api",
                title="Subsystem Integration, Public API & End-to-End Verification",
                objective=f"Complete end-to-end integration and edge-case handling for: {objective}",
                depends_on=["m1_core_foundation"],
                target_files=[],
                acceptance_cmd=default_acceptance_cmd,
                max_diff_lines=500,
                budget_moves=36,
            ),
        ]

    dag = MilestoneDAG(milestones=specs, system_objective=objective)
    dag.validate_dag()
    return dag


class L0MacroPlanner:
    """Layer `L0-Macro` Compositional Planner.

    Decomposes a high-level system objective into a topological AND-DAG of
    bounded ($< 500$ LoC) milestones (`MilestoneDAG`), or loads a declarative
    milestone specification from disk.
    """

    def __init__(
        self,
        llm_callable: Optional[Callable[[List[Dict[str, str]]], str]] = None,
        default_acceptance_cmd: str = "pytest -q",
        max_diff_lines: int = 500,
        logger: Callable[[str], None] = print,
    ) -> None:
        self.llm_callable = llm_callable
        self.default_acceptance_cmd = default_acceptance_cmd
        self.max_diff_lines = max_diff_lines
        self.logger = logger

    @staticmethod
    def load_from_file(path: str) -> MilestoneDAG:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return MilestoneDAG.from_dict(data)

    load = load_from_file

    def build_macro_prompt(self, objective: str, repo_files: Sequence[str] = ()) -> List[Dict[str, str]]:
        files_preview = "\n".join(f"- {p}" for p in list(repo_files)[:40]) or "(greenfield / empty repository)"
        sys_prompt = (
            "You are the Layer `L0-Macro` Principal System Architect.\n"
            "Decompose the user's system objective into an acyclic topological DAG of bounded, "
            "self-contained milestones (each `< 500` lines of diff) so that each milestone can be "
            "implemented, falsified, and permanently ratcheted as a Git commit before the next milestone begins.\n"
            "Emit a single JSON object matching:\n"
            "{\n"
            '  "system_objective": "...",\n'
            '  "milestones": [\n'
            "    {\n"
            '      "milestone_id": "m1_lexer",\n'
            '      "title": "...",\n'
            '      "objective": "...",\n'
            '      "depends_on": [],\n'
            '      "target_files": ["pkg/lexer.py", "tests/test_lexer.py"],\n'
            '      "acceptance_cmd": "pytest -q tests/test_lexer.py",\n'
            '      "max_diff_lines": 500,\n'
            '      "budget_moves": 36\n'
            "    }\n"
            "  ]\n"
            "}"
        )
        user_prompt = (
            f"### System Objective\n{objective}\n\n"
            f"### Existing Workspace Files\n{files_preview}\n\n"
            "Emit the JSON `MilestoneDAG` specification now."
        )
        return [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_prompt},
        ]

    def plan(
        self,
        objective: str = "",
        executor: Optional[CommandExecutor] = None,
        spec_path: Optional[str] = None,
        default_acceptance_cmd: Optional[str] = None,
    ) -> MilestoneDAG:
        if spec_path:
            self.logger(f"[l0-macro] Loading declarative MilestoneDAG from {spec_path}")
            return self.load_from_file(spec_path)

        acc_cmd = default_acceptance_cmd or self.default_acceptance_cmd
        repo_files: List[str] = []
        if executor is not None:
            res = executor.git(["ls-files"])
            if res.ok:
                repo_files = [l.strip() for l in res.stdout.splitlines() if l.strip()]

        raw_resp = ""
        if self.llm_callable is not None:
            try:
                messages = self.build_macro_prompt(objective, repo_files)
                raw_resp = self.llm_callable(messages) or ""
            except Exception as exc:
                self.logger(f"[l0-macro] LLM planner call failed ({exc}); using deterministic fallback.")

        dag = parse_macro_plan_response(raw_resp, objective=objective, default_acceptance_cmd=acc_cmd)
        return dag


class GitCommitRatchet:
    """Manages persistent Git commit ratcheting, falsification suite archiving,
    cumulative regression gating, rollback on failure, and `.hae/ratchet_state.json`.
    """

    STATE_FILE_REL = f"{SCRATCH_DIR}/ratchet_state.json"
    RATCHET_SUITES_DIR_REL = f"{SCRATCH_DIR}/ratchet_suites"

    def __init__(
        self,
        executor: CommandExecutor,
        logger: Callable[[str], None] = print,
    ) -> None:
        self.executor = executor
        self.logger = logger

    def ensure_dirs(self) -> None:
        self.executor.run(
            [
                "mkdir",
                "-p",
                f"{self.executor.root}/{SCRATCH_DIR}",
                f"{self.executor.root}/{self.RATCHET_SUITES_DIR_REL}",
            ],
            cwd=self.executor.root,
            timeout_s=15,
        )

    def state_exists(self) -> bool:
        return self.executor.exists(self.STATE_FILE_REL)

    def current_head_sha(self) -> str:
        res = self.executor.git(["rev-parse", "HEAD"])
        return res.stdout.strip() if res.ok else ""

    def current_delta_diff(self) -> str:
        """Return `git diff HEAD` (including newly created untracked files outside `.hae/`).

        Because prior certified milestones $M_1 \\dots M_{k-1}$ are already committed
        to `HEAD`, this diff contains ONLY the active milestone $M_k$'s bounded delta.
        """
        return self.executor.diff()

    def current_delta_files(self) -> List[str]:
        return self.executor.changed_files()

    def archive_falsification_suite(
        self,
        milestone: MilestoneSpec,
        source_rel: Optional[str] = None,
    ) -> Optional[str]:
        """Archive $M_k$'s adversarial falsification script to `.hae/ratchet_suites/falsify_<milestone_id>.py`."""
        self.ensure_dirs()
        target_rel = f"{self.RATCHET_SUITES_DIR_REL}/falsify_{milestone.milestone_id}.py"
        candidates = [
            c
            for c in (
                source_rel,
                f"{SCRATCH_DIR}/falsification_suite.py",
                f"{SCRATCH_DIR}/audit_edge_cases.py",
                f"{SCRATCH_DIR}/reproduce_issue.py",
            )
            if c
        ]
        for cand in candidates:
            if self.executor.exists(cand):
                content = self.executor.read_file(cand)
                if content.strip():
                    self.executor.write_file(target_rel, content)
                    milestone.falsification_suite_path = target_rel
                    return target_rel

        if self.executor.exists(target_rel):
            milestone.falsification_suite_path = target_rel
            return target_rel
        return milestone.falsification_suite_path

    def run_cumulative_regression_gate(
        self,
        dag: MilestoneDAG,
        active_milestone: MilestoneSpec,
        verifier_plugin: Optional[WorkspaceVerifierPlugin] = None,
    ) -> VerificationGateResult:
        """Run acceptance commands and falsification suites of ALL previously certified
        milestones $M_1 \\dots M_{k-1}$ PLUS the active milestone $M_k$.
        """
        order = dag.validate_dag()
        milestones_to_verify: List[MilestoneSpec] = []
        for mid in order:
            m = dag.milestones[mid]
            if m.status == "certified" and mid != active_milestone.milestone_id:
                milestones_to_verify.append(m)
        milestones_to_verify.append(active_milestone)

        compile_ok = True
        repro_ok = True
        tests_ok = True
        falsifier_ok = True
        new_failures = 0
        notes: List[str] = []

        # 1. Check Python syntax of any modified/created .py files in the active delta
        delta_files = self.current_delta_files()
        py_delta = [
            f for f in delta_files
            if f.endswith(".py") and not f.startswith(SCRATCH_DIR + "/") and self.executor.exists(f)
        ]
        if py_delta:
            res_c = self.executor.run(
                [self.executor.python, "-m", "py_compile", *py_delta],
                cwd=self.executor.root,
                env={"PYTHONPATH": self.executor.root},
                timeout_s=30,
            )
            if not res_c.ok:
                compile_ok = False
                repro_ok = False
                tests_ok = False
                new_failures += 1
                notes.append(f"py_compile FAILED on {py_delta}: {(res_c.stderr or res_c.stdout).strip()[:400]}")

        # 2. Check diff line cap (`max_diff_lines`)
        delta_diff = self.current_delta_diff()
        added_lines = sum(
            1
            for line in delta_diff.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )
        if active_milestone.max_diff_lines > 0 and added_lines > active_milestone.max_diff_lines:
            tests_ok = False
            new_failures += 1
            notes.append(
                f"Milestone `{active_milestone.milestone_id}` exceeded max_diff_lines "
                f"({added_lines} added lines > {active_milestone.max_diff_lines})"
            )

        # 3. Cumulative Regression Loop across M_1 .. M_{k-1} + M_k
        if compile_ok:
            for m in milestones_to_verify:
                is_active = m.milestone_id == active_milestone.milestone_id
                if m.acceptance_cmd:
                    shell_vp = ShellCommandVerifierPlugin(
                        test_cmd=m.acceptance_cmd,
                        diff_bloat_threshold=active_milestone.max_diff_lines,
                    )
                    res_m = shell_vp.verify(self.executor, changed_files=delta_files)
                    if not res_m.tests_ok:
                        tests_ok = False
                        if is_active:
                            repro_ok = False
                        new_failures += max(1, res_m.new_failures)
                        notes.append(
                            f"[{m.milestone_id}] acceptance `{m.acceptance_cmd}` FAILED: {res_m.summary}"
                        )
                    else:
                        notes.append(f"[{m.milestone_id}] acceptance PASSED")

                f_path = m.falsification_suite_path
                if f_path and self.executor.exists(f_path):
                    res_f = self.executor.run(
                        [self.executor.python, f_path],
                        cwd=self.executor.root,
                        env={"PYTHONPATH": self.executor.root},
                        timeout_s=60,
                    )
                    if not res_f.ok:
                        falsifier_ok = False
                        new_failures += 1
                        out_f = ((res_f.stdout or "") + "\n" + (res_f.stderr or "")).strip()
                        notes.append(
                            f"[{m.milestone_id}] falsification suite `{f_path}` FAILED (rc={res_f.rc}): {out_f[:400]}"
                        )
                    else:
                        notes.append(f"[{m.milestone_id}] falsification suite `{f_path}` PASSED")

        # 4. Optional additional workspace verifier plugin
        if compile_ok and verifier_plugin is not None:
            vp_res = verifier_plugin.verify(self.executor, changed_files=delta_files)
            compile_ok = compile_ok and vp_res.compile_ok
            repro_ok = repro_ok and vp_res.repro_ok
            tests_ok = tests_ok and vp_res.tests_ok
            falsifier_ok = falsifier_ok and vp_res.falsifier_ok
            new_failures += vp_res.new_failures
            notes.append(f"plugin({verifier_plugin.describe()}): {vp_res.summary}")

        score = compute_gate_score(
            compile_ok=compile_ok,
            repro_ok=repro_ok,
            tests_ok=tests_ok,
            falsifier_ok=falsifier_ok,
            new_failures=new_failures,
            diff_lines=added_lines,
            diff_bloat_threshold=active_milestone.max_diff_lines,
        )
        return VerificationGateResult(
            compile_ok=compile_ok,
            repro_ok=repro_ok,
            tests_ok=tests_ok,
            falsifier_ok=falsifier_ok,
            new_failures=new_failures,
            summary="; ".join(notes) if notes else "Cumulative regression gate PASSED",
            score=score,
        )

    def _clean_transient_scratch(self) -> None:
        """Remove per-episode scratch files while preserving `.hae/ratchet_state.json` and `.hae/ratchet_suites/`."""
        for rel in (
            f"{SCRATCH_DIR}/tmp",
            f"{SCRATCH_DIR}/reproduce_issue.py",
            f"{SCRATCH_DIR}/falsification_suite.py",
            f"{SCRATCH_DIR}/audit_edge_cases.py",
        ):
            if self.executor.exists(rel):
                self.executor.run(["rm", "-rf", rel], cwd=self.executor.root, timeout_s=15)

    def advance_ratchet(
        self,
        milestone: MilestoneSpec,
        dag: Optional[MilestoneDAG] = None,
    ) -> str:
        """Stage all source/test changes, create a permanent Git commit and lightweight tag,
        record `certified_commit_sha`, and persist `.hae/ratchet_state.json`.
        """
        self.ensure_dirs()
        # Ensure `.hae/tmp` is removed before staging
        if self.executor.exists(f"{SCRATCH_DIR}/tmp"):
            self.executor.run(["rm", "-rf", f"{SCRATCH_DIR}/tmp"], cwd=self.executor.root, timeout_s=15)

        # Stage all created/modified source & test files while keeping `.hae` untracked
        self.executor.git(["add", "-A"])
        self.executor.git(["reset", "-q", "HEAD", "--", SCRATCH_DIR])

        commit_msg = f"hae(ratchet): certify milestone {milestone.milestone_id} - {milestone.title}"
        res_commit = self.executor.git(
            [
                "-c",
                "user.name=HAE Ratchet",
                "-c",
                "user.email=hae@local",
                "commit",
                "--allow-empty",
                "-m",
                commit_msg,
            ]
        )
        if not res_commit.ok:
            raise RuntimeError(
                f"Git commit ratchet failed for milestone {milestone.milestone_id!r}: {res_commit.stderr}"
            )

        tag_name = f"hae-milestone-{milestone.milestone_id}"
        self.executor.git(["tag", "-f", tag_name])

        sha = self.current_head_sha()
        milestone.status = "certified"
        milestone.certified_commit_sha = sha

        self._clean_transient_scratch()
        if dag is not None:
            self.save_state(dag)

        self.logger(
            f"[l0-macro-ratchet] Certified milestone `{milestone.milestone_id}` -> commit {sha[:12]} (tag `{tag_name}`)"
        )
        return sha

    def rollback_to_head(self) -> str:
        """Roll back uncommitted working-tree changes to the last certified milestone's `HEAD` commit."""
        self.executor.git(["reset", "--hard", "HEAD"])
        self.executor.git(["clean", "-fd", "-e", SCRATCH_DIR])
        self._clean_transient_scratch()
        sha = self.current_head_sha()
        self.logger(
            f"[l0-macro-ratchet] Rolled back uncommitted working tree to last certified HEAD ({sha[:12]})"
        )
        return sha

    run_cumulative_regression_guard = run_cumulative_regression_gate
    rollback_to_last_commit = rollback_to_head

    def save_state(
        self,
        dag: MilestoneDAG,
        extra: Optional[Mapping[str, Any]] = None,
    ) -> str:
        """Persist `MilestoneDAG` and ratchet metadata to `<workspace>/.hae/ratchet_state.json`."""
        self.ensure_dirs()
        payload: Dict[str, Any] = {
            "version": 1,
            "updated_at": time.time(),
            "head_commit_sha": self.current_head_sha(),
            "dag": dag.to_dict(),
        }
        if extra:
            payload.update(dict(extra))
        self.executor.write_file(self.STATE_FILE_REL, json.dumps(payload, indent=2))
        return self.STATE_FILE_REL

    def load_state(self) -> Optional[MilestoneDAG]:
        """Load persisted `MilestoneDAG` from `<workspace>/.hae/ratchet_state.json` if present."""
        if not self.state_exists():
            return None
        raw = self.executor.read_file(self.STATE_FILE_REL)
        data = json.loads(raw)
        dag_data = data.get("dag") if isinstance(data, dict) and "dag" in data else data
        return MilestoneDAG.from_dict(dag_data)


class _DefaultMicroRunnerAdapter:
    """Minimal runner adapter when running `HierarchicalAgenticMCTSEngine` under a milestone."""

    def __init__(self, executor: CommandExecutor, milestone: MilestoneSpec, model_name: str = "") -> None:
        self.executor = executor
        self.milestone = milestone
        self.model_name = model_name or os.environ.get("HAE_WORKER_MODEL", "nvidia/Qwen3.8-Flash-Next-NVFP4")
        self.calls = 0
        self.max_calls = max(40, milestone.budget_moves * 2)

    def _may_call(self, reason: str = "") -> bool:
        self.calls += 1
        return self.calls <= self.max_calls

    def _account_tokens(self, **kwargs: Any) -> None:
        return None

    def objective_text(self) -> str:
        targets = ", ".join(f"`{t}`" for t in self.milestone.target_files) or "(create/modify as needed)"
        acc = f"\nAcceptance Command: `{self.milestone.acceptance_cmd}`" if self.milestone.acceptance_cmd else ""
        return (
            f"MILESTONE `{self.milestone.milestone_id}`: {self.milestone.title}\n\n"
            f"{self.milestone.objective}\n\n"
            f"Target Files: {targets}{acc}\n"
            f"Maximum Diff Lines for this Milestone: {self.milestone.max_diff_lines}"
        )

    def repo_map(self) -> List[str]:
        res = self.executor.git(["ls-files"])
        files = [l.strip() for l in res.stdout.splitlines() if l.strip()] if res.ok else []
        for tf in self.milestone.target_files:
            if tf not in files:
                files.insert(0, tf)
        return files[:60]


class MacroMilestoneRatchetEngine:
    """Orchestrates the Two-Tier `L0-Macro` Milestone DAG + `L0-Micro` V13 AB-MCTS with Git Commit Ratcheting."""

    def __init__(
        self,
        executor: CommandExecutor,
        dag: Optional[MilestoneDAG] = None,
        objective: str = "",
        planner: Optional[L0MacroPlanner] = None,
        milestone_runner: Optional[Callable[..., Any]] = None,
        firm_runner: Optional[Any] = None,
        verifier_plugin: Optional[WorkspaceVerifierPlugin] = None,
        stop_on_failure: bool = True,
        logger: Callable[[str], None] = print,
    ) -> None:
        self.executor = executor
        self.dag = dag
        self.objective = objective
        self.planner = planner or L0MacroPlanner(logger=logger)
        self.milestone_runner = milestone_runner
        self.firm_runner = firm_runner
        self.verifier_plugin = verifier_plugin
        self.stop_on_failure = stop_on_failure
        self.logger = logger
        self.ratchet = GitCommitRatchet(executor=executor, logger=logger)

    def _invoke_milestone_runner(self, milestone: MilestoneSpec) -> Dict[str, Any]:
        """Execute `L0-Micro` for `milestone` using either the injected `milestone_runner` or V13 AB-MCTS."""
        if self.milestone_runner is not None:
            sig = inspect.signature(self.milestone_runner)
            params = list(sig.parameters.values())
            has_varargs = any(p.kind == inspect.Parameter.VAR_POSITIONAL for p in params)
            if has_varargs or len(params) >= 3:
                out = self.milestone_runner(milestone, self.executor, self.ratchet)
            elif len(params) == 2:
                out = self.milestone_runner(milestone, self.executor)
            else:
                out = self.milestone_runner(milestone)

            if isinstance(out, dict):
                return out
            if isinstance(out, bool):
                return {"resolved_by_self_oracle": out, "ok": out}
            return {"resolved_by_self_oracle": True}

        # Default L0-Micro runner: Generalized V13 `HierarchicalAgenticMCTSEngine`
        runner = self.firm_runner or _DefaultMicroRunnerAdapter(self.executor, milestone)
        head_sha = self.ratchet.current_head_sha() or "HEAD"
        swe_task = SweTask(
            instance_id=f"ratchet__{milestone.milestone_id}",
            repo=os.path.basename(self.executor.root.rstrip("/")) or "workspace",
            base_commit=head_sha,
            version="1.0",
            problem_statement=(
                f"Milestone {milestone.milestone_id} ({milestone.title}):\n"
                f"{milestone.objective}\n"
                + (f"\nAcceptance command: `{milestone.acceptance_cmd}`" if milestone.acceptance_cmd else "")
            ),
        )
        state = EpistemicState(company_id=f"ratchet_{milestone.milestone_id}")
        primary_mod = milestone.target_files[0] if milestone.target_files else "workspace"
        state.add_question(
            f"How to implement and verify milestone {milestone.milestone_id}: {milestone.title}?",
            module=primary_mod,
            uncertainty=0.9,
        )
        gk = EvidenceGatekeeper(
            workspace=self.executor.root,
            python_executable=self.executor.python,
            executor=self.executor,
            logger=self.logger,
        )

        init_sig = inspect.signature(HierarchicalAgenticMCTSEngine.__init__)
        extra_kwargs: Dict[str, Any] = {}
        if "allow_new_files" in init_sig.parameters:
            extra_kwargs["allow_new_files"] = True
        if "diff_bloat_threshold" in init_sig.parameters:
            extra_kwargs["diff_bloat_threshold"] = milestone.max_diff_lines
        if "custom_test_cmd" in init_sig.parameters and milestone.acceptance_cmd:
            extra_kwargs["custom_test_cmd"] = milestone.acceptance_cmd
        if "base_ref" in init_sig.parameters:
            extra_kwargs["base_ref"] = "HEAD"

        micro_engine = HierarchicalAgenticMCTSEngine(
            runner=runner,
            executor=self.executor,
            task=swe_task,
            state=state,
            gatekeeper=gk,
            logger=self.logger,
            **extra_kwargs,
        )
        return micro_engine.run(
            candidate_modules=list(milestone.target_files),
            budget_moves=milestone.budget_moves,
        )

    def _restore_from_saved_state(self, saved_dag: MilestoneDAG) -> None:
        """Merge persisted `.hae/ratchet_state.json` milestone statuses into `self.dag`."""
        if self.dag is None:
            self.dag = saved_dag
        else:
            for mid, saved_m in saved_dag.milestones.items():
                if mid in self.dag.milestones:
                    target = self.dag.milestones[mid]
                    target.status = saved_m.status
                    target.certified_commit_sha = saved_m.certified_commit_sha
                    target.falsification_suite_path = saved_m.falsification_suite_path
                    target.mcts_summary = dict(saved_m.mcts_summary or {})
                else:
                    self.dag.milestones[mid] = saved_m

        # Any milestone that was interrupted ("in_progress") or failed on a prior crashed run
        # is reset to "pending" and the working tree is rolled back to the last certified HEAD.
        for m in self.dag.milestones.values():
            if m.status != "certified":
                m.status = "pending"
        self.ratchet.rollback_to_head()

    def run(self, resume: bool = False) -> Dict[str, Any]:
        """Execute all milestones in topological order with persistent Git commit ratcheting."""
        self.ratchet.ensure_dirs()

        if resume and self.ratchet.state_exists():
            saved_dag = self.ratchet.load_state()
            if saved_dag is not None:
                self._restore_from_saved_state(saved_dag)
                self.logger(
                    f"[l0-macro-ratchet] Resumed from `{self.ratchet.STATE_FILE_REL}` "
                    f"(certified={[m.milestone_id for m in self.dag.certified_milestones()]})"
                )

        if self.dag is None:
            self.dag = self.planner.plan(objective=self.objective, executor=self.executor)

        topo_order = self.dag.validate_dag()
        self.ratchet.save_state(self.dag)

        history: List[Dict[str, Any]] = []

        while True:
            milestone = self.dag.next_ready_milestone()
            if milestone is None:
                break

            base_head = self.ratchet.current_head_sha()
            milestone.status = "in_progress"
            self.ratchet.save_state(self.dag)
            self.logger(
                f"[l0-macro-ratchet] Starting milestone `{milestone.milestone_id}` ({milestone.title!r}) "
                f"from HEAD={base_head[:12]}"
            )

            t0 = time.time()
            runner_error: Optional[str] = None
            micro_summary: Dict[str, Any] = {}
            try:
                micro_summary = self._invoke_milestone_runner(milestone)
            except Exception as exc:
                runner_error = f"{type(exc).__name__}: {exc}"
                micro_summary = {"error": runner_error, "ok": False}

            delta_diff = self.ratchet.current_delta_diff()
            delta_files = self.ratchet.current_delta_files()
            delta_lines = len(delta_diff.splitlines())

            # Archive adversarial falsification script if created during this milestone
            archived_suite = self.ratchet.archive_falsification_suite(milestone)

            if runner_error is not None or micro_summary.get("ok") is False:
                gate_res = VerificationGateResult(
                    compile_ok=False,
                    repro_ok=False,
                    tests_ok=False,
                    falsifier_ok=False,
                    new_failures=1,
                    summary=runner_error or "Milestone runner reported explicit failure.",
                    score=0.0,
                )
            else:
                gate_res = self.ratchet.run_cumulative_regression_gate(
                    dag=self.dag,
                    active_milestone=milestone,
                    verifier_plugin=self.verifier_plugin,
                )

            elapsed_s = round(time.time() - t0, 3)
            milestone.mcts_summary = {
                **micro_summary,
                "base_head_sha": base_head,
                "delta_files": delta_files,
                "delta_diff_lines": delta_lines,
                "archived_falsification_suite": archived_suite,
                "cumulative_gate": gate_res.to_dict(),
                "elapsed_s": elapsed_s,
            }

            if gate_res.ok:
                commit_sha = self.ratchet.advance_ratchet(milestone, dag=self.dag)
                history.append(
                    {
                        "milestone_id": milestone.milestone_id,
                        "status": "certified",
                        "base_head_sha": base_head,
                        "certified_commit_sha": commit_sha,
                        "delta_files": delta_files,
                        "delta_diff_lines": delta_lines,
                        "gate": gate_res.to_dict(),
                        "elapsed_s": elapsed_s,
                    }
                )
            else:
                rolled_back_sha = self.ratchet.rollback_to_head()
                milestone.status = "failed"
                milestone.certified_commit_sha = None
                self.ratchet.save_state(self.dag)
                history.append(
                    {
                        "milestone_id": milestone.milestone_id,
                        "status": "failed",
                        "base_head_sha": base_head,
                        "rolled_back_to_sha": rolled_back_sha,
                        "delta_files": delta_files,
                        "delta_diff_lines": delta_lines,
                        "gate": gate_res.to_dict(),
                        "elapsed_s": elapsed_s,
                    }
                )
                if self.stop_on_failure:
                    break

        certified_ids = [
            mid for mid in topo_order if self.dag.milestones[mid].status == "certified"
        ]
        failed_ids = [
            mid for mid in topo_order if self.dag.milestones[mid].status == "failed"
        ]
        pending_ids = [
            mid for mid in topo_order if self.dag.milestones[mid].status == "pending"
        ]

        return {
            "completed": self.dag.is_complete(),
            "topological_order": topo_order,
            "certified_milestones": certified_ids,
            "failed_milestones": failed_ids,
            "pending_milestones": pending_ids,
            "head_commit_sha": self.ratchet.current_head_sha(),
            "state_file": self.ratchet.STATE_FILE_REL,
            "dag": self.dag.to_dict(),
            "history": history,
        }
