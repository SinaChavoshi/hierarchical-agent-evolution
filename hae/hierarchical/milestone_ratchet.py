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
    _parse_passed_count,
    compute_gate_score,
    ensure_workspace_pytest_shim,
)
from hae.swebench.executor import CommandExecutor, ExecResult, SCRATCH_DIR
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


def _extract_json_dict(raw_text: str) -> Optional[Dict[str, Any]]:
    """Extract a JSON dictionary containing `'milestones'` from an LLM response,
    tolerating `<think>...</think>` blocks and markdown code fences.
    """
    if not raw_text or not raw_text.strip():
        return None

    # Strip closed <think>...</think> blocks
    cleaned = re.sub(r"<think>.*?</think>", "", raw_text, flags=re.DOTALL | re.IGNORECASE).strip()
    # If an unclosed <think> tag precedes a ```json fence, strip the <think> prefix
    if "<think>" in cleaned.lower() and "```" in cleaned:
        fence_idx = cleaned.find("```")
        think_idx = cleaned.lower().find("<think>")
        if 0 <= think_idx < fence_idx:
            cleaned = cleaned[fence_idx:]

    # 1. Try fenced ```json ... ``` blocks (from last to first)
    fenced_blocks = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, flags=re.DOTALL | re.IGNORECASE)
    for block in reversed(fenced_blocks):
        try:
            obj = json.loads(block)
            if isinstance(obj, dict) and obj.get("milestones"):
                return obj
        except Exception:
            pass

    # 2. Scan balanced `{ ... }` objects using json.JSONDecoder().raw_decode
    decoder = json.JSONDecoder()
    for text_cand in (cleaned, raw_text):
        pos = 0
        while True:
            brace_idx = text_cand.find("{", pos)
            if brace_idx < 0:
                break
            try:
                obj, _ = decoder.raw_decode(text_cand[brace_idx:])
                if isinstance(obj, dict) and obj.get("milestones"):
                    return obj
            except Exception:
                pass
            pos = brace_idx + 1

    return None


def parse_macro_plan_response(
    raw_text: str,
    objective: str,
    default_acceptance_cmd: str = "pytest -q",
    max_diff_lines: Optional[int] = None,
    budget_moves: Optional[int] = None,
) -> MilestoneDAG:
    """Parse an `L0-Macro` LLM response into a validated `MilestoneDAG`, with a deterministic fallback."""
    payload = _extract_json_dict(raw_text)
    if payload is not None:
        try:
            dag = MilestoneDAG.from_dict(payload)
            if not dag.system_objective:
                dag.system_objective = objective
            for m in dag.milestones.values():
                test_targets = [
                    tf for tf in m.target_files
                    if tf.endswith(".py") and (tf.startswith("tests/") or os.path.basename(tf).startswith("test_"))
                ]
                if not m.acceptance_cmd or m.acceptance_cmd.strip() == "pytest -q":
                    if test_targets:
                        m.acceptance_cmd = f"pytest -q {' '.join(test_targets)}"
                    elif not m.acceptance_cmd:
                        m.acceptance_cmd = default_acceptance_cmd
                if max_diff_lines is not None and max_diff_lines > 0:
                    m.max_diff_lines = max_diff_lines
                if budget_moves is not None and budget_moves > 0:
                    m.budget_moves = budget_moves
            dag.validate_dag()
            return dag
        except Exception:
            pass

    eff_max_diff = int(max_diff_lines or 500)
    eff_budget = int(budget_moves or 36)

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
                    max_diff_lines=eff_max_diff,
                    budget_moves=eff_budget,
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
                max_diff_lines=eff_max_diff,
                budget_moves=eff_budget,
            ),
            MilestoneSpec(
                milestone_id="m2_integration_and_api",
                title="Subsystem Integration, Public API & End-to-End Verification",
                objective=f"Complete end-to-end integration and edge-case handling for: {objective}",
                depends_on=["m1_core_foundation"],
                target_files=[],
                acceptance_cmd=default_acceptance_cmd,
                max_diff_lines=eff_max_diff,
                budget_moves=eff_budget,
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
        budget_moves: int = 36,
        model_name: Optional[str] = None,
        use_live_llm: Optional[bool] = None,
        logger: Callable[[str], None] = print,
    ) -> None:
        self.llm_callable = llm_callable
        self.default_acceptance_cmd = default_acceptance_cmd
        self.max_diff_lines = max_diff_lines
        self.budget_moves = budget_moves
        self.model_name = model_name
        self.use_live_llm = use_live_llm
        self.logger = logger
        self.last_raw_response: str = ""
        self.last_token_usage: Dict[str, int] = {
            "llm_calls": 0,
            "prompt_tokens": 0,
            "output_tokens": 0,
            "thought_tokens": 0,
            "total_tokens": 0,
        }

    def _record_usage(self, usage: Mapping[str, Any]) -> None:
        self.last_token_usage["llm_calls"] += 1
        p_tok = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        o_tok = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
        th_tok = int(usage.get("thought_tokens") or usage.get("reasoning_tokens") or 0)
        tot_tok = int(usage.get("total_tokens") or (p_tok + o_tok) or 0)
        self.last_token_usage["prompt_tokens"] += p_tok
        self.last_token_usage["output_tokens"] += o_tok
        self.last_token_usage["thought_tokens"] += th_tok
        self.last_token_usage["total_tokens"] += tot_tok

    def _default_llm_call(self, messages: List[Dict[str, str]]) -> str:
        from hae.infra.llm import call_llm

        model = (
            self.model_name
            or os.environ.get("HAE_WORKER_MODEL")
            or os.environ.get("VLLM_MODEL")
            or "reflection/Beam-1.0-501B-A23B-NVFP4"
        )
        sys_text = messages[0].get("content", "") if len(messages) > 1 else ""
        user_text = messages[-1].get("content", "") if messages else ""
        usage: Dict[str, Any] = {}
        resp = call_llm(
            prompt=user_text,
            system_instruction=sys_text,
            model_name=model,
            model_tier="executive",
            temperature=0.1,
            provider="vllm" if os.environ.get("LLM_PROVIDER") == "vllm" or os.environ.get("VLLM_BASE_URL") else None,
            usage_sink=usage,
            max_tokens=6400,
            messages=messages,
            enable_thinking=True,
            extra_headers={"X-No-Cache": "1"},
        )
        self._record_usage(usage)
        if not (resp or "").strip() and usage.get("last_reasoning"):
            usage2: Dict[str, Any] = {}
            resp = call_llm(
                prompt=user_text,
                system_instruction=sys_text,
                model_name=model,
                model_tier="executive",
                temperature=0.1,
                provider="vllm" if os.environ.get("LLM_PROVIDER") == "vllm" or os.environ.get("VLLM_BASE_URL") else None,
                usage_sink=usage2,
                max_tokens=4096,
                messages=messages,
                enable_thinking=False,
                extra_headers={"X-No-Cache": "1"},
            )
            self._record_usage(usage2)
        return resp or ""

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
            "Decompose the user's system objective into an acyclic topological DAG of 4 to 5 bounded, "
            f"self-contained milestones (each `< {self.max_diff_lines}` lines of diff, with each individual source file `< 380` lines) "
            "so that each milestone can be implemented, falsified, and permanently ratcheted as a Git commit before the next milestone begins.\n\n"
            "CRITICAL DECOMPOSITION RULES:\n"
            "1. **Self-Contained, Contract-Complete Milestone Objectives**:\n"
            "   Each milestone is executed by an isolated `L0-Micro` solver that sees ONLY that milestone's `objective` "
            "   and the already-committed code in Git `HEAD`. Therefore, each milestone's `objective` MUST preserve "
            "   EVERY exact module path, class name, method/function signature, parameter default, return structure, "
            "   exception class name, and edge-case rule from the System Objective assigned to that milestone.\n"
            "2. **Dedicated Per-Milestone Unit Test File**:\n"
            "   Every milestone `<milestone_id>` MUST include a dedicated unit test file `tests/test_<milestone_id>.py` "
            "   in `target_files`, and its `acceptance_cmd` MUST be `\"pytest -q tests/test_<milestone_id>.py\"`.\n"
            "3. **Clean Package Exports (`__init__.py`) & Modular Files**:\n"
            "   Include `<pkg>/__init__.py` in `m1`'s `target_files` and update `<pkg>/__init__.py` in the final "
            "   milestone so all public symbols required by the System Objective are exported at the top-level package. "
            "   Keep AST/schema dataclasses in their own module (e.g. `ast_nodes.py`) separate from recursive-descent parser logic (`parser.py`).\n"
            "4. **Strict Topological Ordering**:\n"
            "   Order milestones from foundational tokenizer/AST (`m1_...`) -> parser (`m2_...`) -> storage/catalog/expressions (`m3_...`) "
            "   -> query execution/joins/aggregates (`m4_...`) -> transactions/public facade (`m5_...`), "
            "   with each milestone listing its upstream prerequisite milestone IDs in `depends_on`.\n\n"
            "Emit ONLY a single valid JSON object matching this exact schema:\n"
            "{\n"
            '  "system_objective": "...",\n'
            '  "milestones": [\n'
            "    {\n"
            '      "milestone_id": "m1_lexer_and_ast",\n'
            '      "title": "...",\n'
            '      "objective": "Detailed, contract-complete specification of classes, signatures, edge cases, and unit tests...",\n'
            '      "depends_on": [],\n'
            '      "target_files": ["pkg/__init__.py", "pkg/ast_nodes.py", "pkg/lexer.py", "tests/test_m1_lexer_and_ast.py"],\n'
            '      "acceptance_cmd": "pytest -q tests/test_m1_lexer_and_ast.py",\n'
            f'      "max_diff_lines": {self.max_diff_lines},\n'
            f'      "budget_moves": {self.budget_moves}\n'
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
        should_call_live = (
            self.use_live_llm is True
            or (
                self.use_live_llm is None
                and bool(os.environ.get("VLLM_BASE_URL") or os.environ.get("HAE_MACRO_LIVE_LLM"))
            )
        )
        if self.llm_callable is not None:
            try:
                messages = self.build_macro_prompt(objective, repo_files)
                raw_resp = self.llm_callable(messages) or ""
                self.last_raw_response = raw_resp
            except Exception as exc:
                self.logger(f"[l0-macro] LLM planner call failed ({exc}); using deterministic fallback.")
        elif should_call_live and (objective or "").strip():
            try:
                self.logger("[l0-macro] Invoking live LLM to decompose system objective into MilestoneDAG...")
                messages = self.build_macro_prompt(objective, repo_files)
                raw_resp = self._default_llm_call(messages) or ""
                self.last_raw_response = raw_resp
            except Exception as exc:
                self.logger(f"[l0-macro] Live LLM planner call failed ({exc}); using deterministic fallback.")

        dag = parse_macro_plan_response(
            raw_resp,
            objective=objective,
            default_acceptance_cmd=acc_cmd,
            max_diff_lines=self.max_diff_lines,
            budget_moves=self.budget_moves,
        )
        self.logger(
            f"[l0-macro] Planned {len(dag.milestones)} milestones in topological order: {dag.validate_dag()}"
        )
        return dag

    def plan_recovery_milestone(
        self,
        dag: MilestoneDAG,
        external_gate: VerificationGateResult,
        attempt: int = 1,
        executor: Optional[CommandExecutor] = None,
    ) -> MilestoneSpec:
        """Synthesize a bounded remediation milestone on top of `HEAD_K` when an external
        held-out test suite uncovers edge-case failures after the initial planned DAG completes.
        """
        topo = dag.validate_dag()
        certified = [mid for mid in topo if dag.milestones[mid].status == "certified"]
        rec_idx = len(topo) + 1
        rec_id = f"m{rec_idx}_external_recovery_{attempt}"
        rec_test_file = f"tests/test_{rec_id}.py"

        tracked_src: List[str] = []
        if executor is not None:
            res = executor.git(["ls-files"])
            if res.ok:
                tracked_src = [
                    l.strip()
                    for l in res.stdout.splitlines()
                    if l.strip().endswith(".py")
                    and not l.strip().startswith("tests/")
                    and not l.strip().startswith(SCRATCH_DIR + "/")
                ]
        target_files = list(dict.fromkeys([*tracked_src[:6], rec_test_file]))
        failure_diag = (external_gate.summary or "").strip()[:4000]

        rec_objective = (
            f"External held-out acceptance verification uncovered edge-case failure(s) after milestone `{certified[-1] if certified else 'HEAD'}`:\n\n"
            f"```text\n{failure_diag}\n```\n\n"
            f"Requirements for `{rec_id}`:\n"
            f"1. Diagnose and fix the root cause in the existing package modules ({', '.join(tracked_src) or 'workspace'}) "
            "without breaking any previously certified milestone behavior.\n"
            f"2. Write at least 4 real `def test_*` regression tests in `{rec_test_file}` importing the package and reproducing/verifying every failure reported above.\n"
            f"3. Ensure `pytest -q {rec_test_file}` and all prior `tests/test_*.py` pass with 0 failures."
        )
        return MilestoneSpec(
            milestone_id=rec_id,
            title=f"External Acceptance Recovery & Edge-Case Remediation (#{attempt})",
            objective=rec_objective,
            depends_on=[certified[-1]] if certified else [],
            target_files=target_files,
            acceptance_cmd=f"pytest -q {rec_test_file}",
            max_diff_lines=self.max_diff_lines,
            budget_moves=self.budget_moves,
        )


_TRANSIENT_SCRATCH_BASENAMES = (
    "reproduce_issue.py",
    "falsification_suite.py",
    "audit_edge_cases.py",
)


class _RatchetWorkspaceExecutorProxy(CommandExecutor):
    """Executor proxy for `L0-Micro` milestones running in local workspaces (`executor.root != '/testbed'`).

    - Transparently maps `/testbed` references in commands and file paths to `executor.root`
      so specialists (`L1-A`, `L2-A`, `L2-B`, `L1-B`) whose prompts mention `/testbed/.hae/...`
      operate cleanly inside the local ratchet workspace.
    - Prepends `<workspace>/.hae/pybin` (when provisioned by `ensure_workspace_pytest_shim`)
      to `PATH` and `PYTHONPATH` so `pytest`, `python3 -m pytest`, and `import pytest`
      succeed inside the workspace without polluting global host site-packages.
    - Relocates any top-level `reproduce_issue.py`, `falsification_suite.py`, or
      `audit_edge_cases.py` created in the workspace root into `.hae/` so `git diff HEAD`
      only contains real milestone source and test files.
    """

    def __init__(self, inner: CommandExecutor) -> None:
        self._inner = inner
        self.root = inner.root
        self._pybin = ensure_workspace_pytest_shim(inner)
        self.python = f"{self._pybin}/python3" if self._pybin else inner.python

    def _map_path(self, rel_or_abs: str) -> str:
        p = str(rel_or_abs or "")
        if p == "/testbed" or p == "/testbed/":
            return self.root
        if p.startswith("/testbed/"):
            p = p[len("/testbed/") :]
        stripped = p.lstrip("./")
        if stripped in _TRANSIENT_SCRATCH_BASENAMES:
            return f"{SCRATCH_DIR}/{stripped}"
        return p

    def _map_cmd_text(self, text: str) -> str:
        s = str(text or "")
        if "/testbed" in s and self.root != "/testbed":
            s = s.replace("/testbed/", f"{self.root}/")
            s = re.sub(r"/testbed(?=$|[\s\"':;,\)\]])", self.root, s)
        return s

    def _relocate_stray_scratch_files(self) -> None:
        for bname in _TRANSIENT_SCRATCH_BASENAMES:
            if self._inner.exists(bname):
                try:
                    content = self._inner.read_file(bname)
                    if content.strip():
                        self._inner.write_file(f"{SCRATCH_DIR}/{bname}", content)
                    self._inner.run(["rm", "-f", bname], cwd=self.root, timeout_s=10)
                except Exception:
                    pass

    def abspath(self, path: Optional[str]) -> str:
        if not path:
            return self.root
        return self._inner.abspath(self._map_path(path))

    def run(
        self,
        argv: Union[str, Sequence[str]],
        cwd: Optional[str] = None,
        timeout_s: float = 60.0,
        env: Optional[Mapping[str, str]] = None,
        stdin: str = "",
    ) -> ExecResult:
        raw_cmd_str = argv if isinstance(argv, str) else " ".join(str(c) for c in argv)
        parent_dir = os.path.dirname(self.root.rstrip("/"))
        repo_grandparent = os.path.dirname(os.path.dirname(parent_dir))
        if (
            "heldout_external_tests" in raw_cmd_str
            or re.search(r"\bfind\s+/\s", raw_cmd_str)
            or (parent_dir and parent_dir in raw_cmd_str and self.root not in raw_cmd_str)
            or (repo_grandparent and len(repo_grandparent) > 10 and repo_grandparent in raw_cmd_str and self.root not in raw_cmd_str)
        ):
            return ExecResult(
                rc=1,
                stdout="",
                stderr=f"SandboxError: Accessing paths outside workspace `{self.root}` is prohibited.",
                elapsed_s=0.0,
            )
        eff_cwd = self._map_cmd_text(cwd) if cwd else self.root
        if isinstance(argv, str):
            eff_cmd: Sequence[str] = ["bash", "-c", self._map_cmd_text(argv)]
        else:
            eff_cmd = [self._map_cmd_text(str(c)) for c in argv]
            if len(eff_cmd) >= 2 and eff_cmd[0] == "bash" and eff_cmd[1] == "-lc":
                eff_cmd = ["bash", "-c", *eff_cmd[2:]]

        eff_env = dict(env or {})
        if self._pybin:
            cur_path = eff_env.get("PATH") or os.environ.get("PATH", "")
            if self._pybin not in cur_path.split(os.pathsep):
                eff_env["PATH"] = f"{self._pybin}{os.pathsep}{cur_path}" if cur_path else self._pybin
            cur_pypath = eff_env.get("PYTHONPATH") or self.root
            pypath_parts = [p for p in cur_pypath.split(os.pathsep) if p]
            if self.root not in pypath_parts:
                pypath_parts.insert(0, self.root)
            if self._pybin not in pypath_parts:
                pypath_parts.append(self._pybin)
            eff_env["PYTHONPATH"] = os.pathsep.join(pypath_parts)
        else:
            eff_env.setdefault("PYTHONPATH", self.root)

        res = self._inner.run(eff_cmd, cwd=eff_cwd, timeout_s=timeout_s, env=eff_env, stdin=stdin)
        self._relocate_stray_scratch_files()
        return res

    def read_file(self, path: str) -> str:
        return self._inner.read_file(self._map_path(path))

    def write_file(self, path: str, content: str) -> None:
        self._inner.write_file(self._map_path(path), content)

    def exists(self, path: str) -> bool:
        return self._inner.exists(self._map_path(path))

    def list_dir(self, path: str = ".") -> List[str]:
        return self._inner.list_dir(self._map_path(path))

    def git(self, args: Sequence[str], timeout_s: float = 120.0) -> ExecResult:
        return self._inner.git(args, timeout_s=timeout_s)

    def untracked_files(self, exclude: Iterable[str] = ()) -> List[str]:
        self._relocate_stray_scratch_files()
        return self._inner.untracked_files(exclude=exclude)

    def diff(self, exclude: Iterable[str] = ()) -> str:
        self._relocate_stray_scratch_files()
        return self._inner.diff(exclude=exclude)

    def changed_files(self, exclude: Iterable[str] = ()) -> List[str]:
        self._relocate_stray_scratch_files()
        return self._inner.changed_files(exclude=exclude)


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
        ensure_workspace_pytest_shim(self.executor)

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

    def total_repo_loc(self) -> int:
        """Return total lines of code across all tracked + newly created files outside `.hae/`."""
        res = self.executor.git(["ls-files"])
        tracked = [l.strip() for l in res.stdout.splitlines() if l.strip()] if res.ok else []
        files = set(tracked) | set(self.current_delta_files())
        total = 0
        for rel in sorted(files):
            if rel.startswith(SCRATCH_DIR + "/") or not self.executor.exists(rel):
                continue
            try:
                content = self.executor.read_file(rel)
                total += len(content.splitlines())
            except Exception:
                pass
        return total

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
                "falsification_suite.py",
                "audit_edge_cases.py",
                "reproduce_issue.py",
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

    def _run_falsification_file(self, f_path: str) -> ExecResult:
        """Execute an archived falsification script, running `pytest`-style `def test_*` functions if needed."""
        pybin_dir = ensure_workspace_pytest_shim(self.executor)
        env: Dict[str, str] = {"PYTHONPATH": self.executor.root}
        if pybin_dir:
            env["PYTHONPATH"] = f"{self.executor.root}{os.pathsep}{pybin_dir}"
            cur_path = os.environ.get("PATH", "")
            env["PATH"] = f"{pybin_dir}{os.pathsep}{cur_path}" if cur_path else pybin_dir

        res_f = self.executor.run(
            [self.executor.python, f_path],
            cwd=self.executor.root,
            env=env,
            timeout_s=60,
        )
        if not res_f.ok:
            return res_f

        # If the script defines `def test_*` functions without a `__main__` entrypoint,
        # also execute it via pytest so those test functions are genuinely exercised.
        try:
            src = self.executor.read_file(f_path)
        except Exception:
            src = ""
        if re.search(r"^\s*def\s+test_", src, flags=re.MULTILINE) and "__main__" not in src:
            res_pt = self.executor.run(
                [self.executor.python, "-m", "pytest", "-q", f_path],
                cwd=self.executor.root,
                env=env,
                timeout_s=60,
            )
            if not res_pt.ok:
                return res_pt
        return res_f

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
        milestone_passed_tests: Dict[str, int] = {}
        cumulative_passed_tests = 0

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
                    m_passed = int(getattr(res_m, "passed_tests", 0) or 0)
                    milestone_passed_tests[m.milestone_id] = m_passed
                    cumulative_passed_tests += m_passed
                    if not res_m.tests_ok:
                        tests_ok = False
                        if is_active:
                            repro_ok = False
                        new_failures += max(1, res_m.new_failures)
                        notes.append(
                            f"[{m.milestone_id}] acceptance `{m.acceptance_cmd}` FAILED: {res_m.summary}"
                        )
                    else:
                        notes.append(
                            f"[{m.milestone_id}] acceptance PASSED ({m_passed} tests)"
                            if m_passed > 0
                            else f"[{m.milestone_id}] acceptance PASSED"
                        )

                f_path = m.falsification_suite_path
                if f_path and self.executor.exists(f_path):
                    res_f = self._run_falsification_file(f_path)
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
            passed_tests=cumulative_passed_tests,
            details={
                "verified_milestones": [m.milestone_id for m in milestones_to_verify],
                "milestone_passed_tests": milestone_passed_tests,
                "cumulative_passed_tests": cumulative_passed_tests,
                "delta_added_lines": added_lines,
                "delta_diff_lines": len(delta_diff.splitlines()),
            },
        )

    def _clean_transient_scratch(self) -> None:
        """Remove per-episode scratch files while preserving `.hae/ratchet_state.json`, `.hae/ratchet_suites/`, and `.hae/pybin/`."""
        for rel in (
            f"{SCRATCH_DIR}/tmp",
            f"{SCRATCH_DIR}/reproduce_issue.py",
            f"{SCRATCH_DIR}/falsification_suite.py",
            f"{SCRATCH_DIR}/audit_edge_cases.py",
            "reproduce_issue.py",
            "falsification_suite.py",
            "audit_edge_cases.py",
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
        self._clean_transient_scratch()

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
        self.max_calls = max(120, milestone.budget_moves * 6)
        self.prompt_tokens = 0
        self.output_tokens = 0
        self.thought_tokens = 0
        self.total_tokens = 0
        self.llm_calls = 0

    def _may_call(self, reason: str = "") -> bool:
        self.calls += 1
        return self.calls <= self.max_calls

    def _account_tokens(self, usage: Optional[Mapping[str, Any]] = None, **kwargs: Any) -> None:
        self.llm_calls += 1
        u = dict(usage or kwargs or {})
        p_tok = int(u.get("prompt_tokens") or u.get("input_tokens") or 0)
        o_tok = int(u.get("output_tokens") or u.get("completion_tokens") or 0)
        th_tok = int(u.get("thought_tokens") or u.get("reasoning_tokens") or 0)
        tot_tok = int(u.get("total_tokens") or (p_tok + o_tok) or 0)
        self.prompt_tokens += p_tok
        self.output_tokens += o_tok
        self.thought_tokens += th_tok
        self.total_tokens += tot_tok

    def token_summary(self) -> Dict[str, int]:
        return {
            "llm_calls": self.llm_calls,
            "prompt_tokens": self.prompt_tokens,
            "output_tokens": self.output_tokens,
            "thought_tokens": self.thought_tokens,
            "total_tokens": self.total_tokens,
        }

    def _upstream_snapshot_text(self) -> str:
        """Render a compact snapshot of upstream certified source files from `HEAD`."""
        res = self.executor.git(["ls-files"])
        if not res.ok:
            return ""
        tracked = [l.strip() for l in res.stdout.splitlines() if l.strip()]
        src_files = [
            f for f in tracked
            if f.endswith(".py")
            and not f.startswith("tests/")
            and not f.startswith("test_")
            and not f.startswith(SCRATCH_DIR + "/")
            and self.executor.exists(f)
        ]
        if not src_files:
            return ""
        blocks: List[str] = []
        budget_chars = 16000
        used_chars = 0
        for rel in src_files:
            try:
                content = "\n".join(self.executor.read_file(rel).splitlines()[:280])
            except Exception:
                continue
            snippet = f"#### `{rel}` (certified in Git HEAD)\n```python\n{content}\n```"
            if used_chars + len(snippet) > budget_chars and blocks:
                break
            blocks.append(snippet)
            used_chars += len(snippet)
        if not blocks:
            return ""
        return "\n\n### Upstream Certified Modules in Git `HEAD` (Do NOT break existing APIs)\n" + "\n\n".join(blocks)

    def objective_text(self) -> str:
        targets = ", ".join(f"`{t}`" for t in self.milestone.target_files) or "(create/modify as needed)"
        acc = f"\nAcceptance Command: `{self.milestone.acceptance_cmd}`" if self.milestone.acceptance_cmd else ""
        upstream = self._upstream_snapshot_text()
        return (
            f"MILESTONE `{self.milestone.milestone_id}`: {self.milestone.title}\n\n"
            f"{self.milestone.objective}\n\n"
            f"Target Files: {targets}{acc}\n"
            f"Maximum Diff Lines for this Milestone: {self.milestone.max_diff_lines}\n"
            f"Workspace Root: `{self.executor.root}`\n\n"
            "### Greenfield & Milestone Implementation Rules\n"
            "1. To **create a brand-new file** that does not exist yet (e.g. a new module or test file in "
            f"{targets}), use `bash` with `mkdir -p <dir> && cat << 'EOF' > <rel_path> ... EOF` "
            "(because `str_replace` requires matching existing file text).\n"
            "2. Put any temporary reproduction or falsification scripts inside `.hae/` "
            "(`.hae/reproduce_issue.py`, `.hae/falsification_suite.py`), NEVER in the repository root.\n"
            f"3. Run `{self.milestone.acceptance_cmd or 'pytest -q'}` via `bash` to verify all tests pass "
            "with exit code 0 before calling `finish`."
            f"{upstream}"
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
        external_verifier_plugin: Optional[WorkspaceVerifierPlugin] = None,
        max_recovery_milestones: int = 2,
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
        self.external_verifier_plugin = external_verifier_plugin
        self.max_recovery_milestones = max(0, int(max_recovery_milestones))
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
        proxy_executor = (
            self.executor
            if isinstance(self.executor, _RatchetWorkspaceExecutorProxy)
            else _RatchetWorkspaceExecutorProxy(self.executor)
        )
        default_adapter = _DefaultMicroRunnerAdapter(proxy_executor, milestone)
        runner = self.firm_runner or default_adapter
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
            workspace=proxy_executor.root,
            python_executable=proxy_executor.python,
            executor=proxy_executor,
            logger=self.logger,
        )

        init_sig = inspect.signature(HierarchicalAgenticMCTSEngine.__init__)
        extra_kwargs: Dict[str, Any] = {}
        if "allow_new_files" in init_sig.parameters:
            extra_kwargs["allow_new_files"] = True
        if "diff_bloat_threshold" in init_sig.parameters:
            extra_kwargs["diff_bloat_threshold"] = milestone.max_diff_lines
        if "custom_test_cmd" in init_sig.parameters and milestone.acceptance_cmd:
            prior_cmds: List[str] = []
            if self.dag is not None:
                for mid in self.dag.topological_order():
                    if mid == milestone.milestone_id:
                        continue
                    pm = self.dag.milestones.get(mid)
                    if (
                        pm is not None
                        and pm.status == "certified"
                        and pm.acceptance_cmd
                        and pm.acceptance_cmd != milestone.acceptance_cmd
                        and pm.acceptance_cmd not in prior_cmds
                    ):
                        prior_cmds.append(pm.acceptance_cmd)
                    if (
                        pm is not None
                        and pm.status == "certified"
                        and pm.falsification_suite_path
                        and self.executor.exists(pm.falsification_suite_path)
                    ):
                        f_cmd = f"python3 {pm.falsification_suite_path}"
                        if f_cmd not in prior_cmds:
                            prior_cmds.append(f_cmd)
            if "_external_recovery_" in milestone.milestone_id and self.external_verifier_plugin is not None:
                ext_cmd = getattr(self.external_verifier_plugin, "test_cmd", "")
                if ext_cmd and ext_cmd not in prior_cmds:
                    prior_cmds.append(ext_cmd)
            extra_kwargs["custom_test_cmd"] = " && ".join([milestone.acceptance_cmd, *prior_cmds])
        if "base_ref" in init_sig.parameters:
            extra_kwargs["base_ref"] = "HEAD"

        micro_engine = HierarchicalAgenticMCTSEngine(
            runner=runner,
            executor=proxy_executor,
            task=swe_task,
            state=state,
            gatekeeper=gk,
            logger=self.logger,
            **extra_kwargs,
        )
        out = micro_engine.run(
            candidate_modules=list(milestone.target_files),
            budget_moves=milestone.budget_moves,
        )
        if isinstance(out, dict) and hasattr(runner, "token_summary"):
            out["token_usage"] = runner.token_summary()
        return out

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

    def _execute_pending_milestones(self, history: List[Dict[str, Any]]) -> None:
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
            delta_added_lines = sum(
                1
                for line in delta_diff.splitlines()
                if line.startswith("+") and not line.startswith("+++")
            )

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
            total_loc = self.ratchet.total_repo_loc()
            milestone.mcts_summary = {
                **micro_summary,
                "base_head_sha": base_head,
                "delta_files": delta_files,
                "delta_diff_lines": delta_lines,
                "delta_added_lines": delta_added_lines,
                "total_repo_loc": total_loc,
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
                        "git_tag": f"hae-milestone-{milestone.milestone_id}",
                        "delta_files": delta_files,
                        "delta_diff_lines": delta_lines,
                        "delta_added_lines": delta_added_lines,
                        "total_repo_loc": total_loc,
                        "gate": gate_res.to_dict(),
                        "token_usage": micro_summary.get("token_usage", {}),
                        "moves_used": micro_summary.get("moves_used", 0),
                        "elapsed_s": elapsed_s,
                    }
                )
            else:
                if not self.stop_on_failure and self.external_verifier_plugin is not None:
                    try:
                        pre_rb_ext = self.external_verifier_plugin.verify(self.executor, changed_files=[])
                        self._last_failed_external_verification = pre_rb_ext.to_dict()
                    except Exception:
                        pass
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
                        "delta_added_lines": delta_added_lines,
                        "total_repo_loc": total_loc,
                        "gate": gate_res.to_dict(),
                        "token_usage": micro_summary.get("token_usage", {}),
                        "moves_used": micro_summary.get("moves_used", 0),
                        "elapsed_s": elapsed_s,
                    }
                )
                if self.stop_on_failure:
                    break

    def run(self, resume: bool = False) -> Dict[str, Any]:
        """Execute all milestones in topological order with persistent Git commit ratcheting."""
        self.ratchet.ensure_dirs()
        self._last_failed_external_verification = None

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

        self.dag.validate_dag()
        self.ratchet.save_state(self.dag)

        history: List[Dict[str, Any]] = []
        self._execute_pending_milestones(history)

        initial_external_verification: Optional[Dict[str, Any]] = getattr(
            self, "_last_failed_external_verification", None
        )
        final_external_verification: Optional[Dict[str, Any]] = initial_external_verification
        external_verified: Optional[bool] = (
            bool(initial_external_verification.get("ok"))
            if isinstance(initial_external_verification, dict)
            else None
        )
        recovery_milestones: List[str] = []

        if self.dag.is_complete() and self.external_verifier_plugin is not None:
            self.logger(
                f"[l0-macro-ratchet] Evaluating certified repository against held-out external suite "
                f"({self.external_verifier_plugin.describe()})..."
            )
            ext_res = self.external_verifier_plugin.verify(self.executor, changed_files=[])
            initial_external_verification = ext_res.to_dict()
            final_external_verification = ext_res.to_dict()
            external_verified = bool(ext_res.ok)

            existing_recoveries = [
                mid for mid in self.dag.validate_dag() if "_external_recovery_" in mid
            ]
            recovery_milestones = list(existing_recoveries)
            attempt = len(existing_recoveries)
            while not ext_res.ok and attempt < self.max_recovery_milestones:
                attempt += 1
                rec_spec = self.planner.plan_recovery_milestone(
                    dag=self.dag,
                    external_gate=ext_res,
                    attempt=attempt,
                    executor=self.executor,
                )
                self.logger(
                    f"[l0-macro-ratchet] External verification failed ({ext_res.summary[:240]}); "
                    f"scheduling ratcheted recovery milestone `{rec_spec.milestone_id}`"
                )
                self.dag.add_milestone(rec_spec)
                recovery_milestones.append(rec_spec.milestone_id)
                self.ratchet.save_state(self.dag)
                self._execute_pending_milestones(history)
                if rec_spec.status != "certified":
                    break
                ext_res = self.external_verifier_plugin.verify(self.executor, changed_files=[])
                final_external_verification = ext_res.to_dict()
                external_verified = bool(ext_res.ok)

        topo_order = self.dag.validate_dag()
        certified_ids = [
            mid for mid in topo_order if self.dag.milestones[mid].status == "certified"
        ]
        failed_ids = [
            mid for mid in topo_order if self.dag.milestones[mid].status == "failed"
        ]
        pending_ids = [
            mid for mid in topo_order if self.dag.milestones[mid].status == "pending"
        ]

        out: Dict[str, Any] = {
            "completed": self.dag.is_complete() and (external_verified is not False),
            "topological_order": topo_order,
            "certified_milestones": certified_ids,
            "failed_milestones": failed_ids,
            "pending_milestones": pending_ids,
            "head_commit_sha": self.ratchet.current_head_sha(),
            "state_file": self.ratchet.STATE_FILE_REL,
            "planner_token_usage": dict(getattr(self.planner, "last_token_usage", {}) or {}),
            "dag": self.dag.to_dict(),
            "history": history,
        }
        if self.external_verifier_plugin is not None:
            out["external_verification"] = {
                "external_verified": bool(external_verified),
                "initial": initial_external_verification,
                "final": final_external_verification,
                "recovery_milestones": recovery_milestones,
            }
        return out
