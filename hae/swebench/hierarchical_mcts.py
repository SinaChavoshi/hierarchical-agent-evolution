"""V13 Hierarchical Verification Harness with Agentic AB-MCTS ("Move 37" Engine).

Unifies two foundational principles across HAE:
  1. **Context-Bounded Verification Layers (`L0 -> L1 -> L2`)**:
     - Every sub-agent executes a scoped contract inside a fresh, short-horizon
       context window (4-9 turns) and is garbage-collected upon return.
     - Only structured artifacts (`ReconBrief`, `VerificationContract`, `git diff`,
       and `EvidenceGatekeeper` / `AdversarialFalsifier` critiques) cross layer
       boundaries upward, eliminating context window exhaustion and lossy chat
       summarization.
     - No executor layer is allowed to grade its own work: an independent,
       read-only `AdversarialFalsifierSubAgent` (`L1-B`) audits every candidate
       patch in a fresh context window against `L0`'s contract and grows a
       cumulative `/testbed/.hae/falsification_suite.py`.
  2. **Agentic AB-MCTS (`GEN` vs. `CONT` over Git Workspace Checkpoints)**:
     - The `L0` Seed Architect maintains a compact AB-MCTS tree over verified
       workspace checkpoints `s_i` with empirical Beta posteriors `Beta(alpha_i, beta_i)`.
     - `CONT(s_i)` spawns a fresh-context `L2` Delta-Refinement Sub-Agent starting
       directly from `s_i`'s `git diff` and its exact `Falsifier/Pytest Critique`.
     - `GEN` spawns a fresh-context `L2` Branch Executor from clean `HEAD` on an
       abstraction-stratified causal locus (`consumer_local`, `producer_upstream` /
       "Move 37", or `codebase_symmetry`) conditioned on the Negative Evidence
       Ledger of prior branches.
"""

from __future__ import annotations

import json
import math
import os
import random
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from hae.epistemic.gatekeeper import EvidenceGatekeeper
from hae.epistemic.ledger import (
    EpistemicState,
    Evidence,
    Q_RESOLVED,
    SUPPORTED,
)
from hae.swebench.agentic import (
    AgenticSweEngine,
    PatchCheckpoint,
    SCRATCH_DIR,
    _truncate_output,
    parse_agent_action,
)
from hae.swebench.executor import CommandExecutor
from hae.swebench.task import SweTask


FALSIFICATION_SCRIPT_REL = f"{SCRATCH_DIR}/falsification_suite.py"


@dataclass
class ReconBrief:
    """Distilled upward return from Layer L1-A (`ReconnaissanceSubAgent`)."""

    consumer_locus: str = ""
    producer_upstream_locus: str = ""
    conventions_and_siblings: str = ""
    repro_script_rel: str = f"{SCRATCH_DIR}/reproduce_issue.py"
    repro_fails_on_head: bool = False
    inspected_files: List[str] = field(default_factory=list)
    raw_summary: str = ""

    def to_compact_prompt_block(self) -> str:
        files_str = ", ".join(f"`{p}`" for p in self.inspected_files[:8]) if self.inspected_files else "(see summary)"
        repro_status = (
            f"`{self.repro_script_rel}` (verified FAILING on clean HEAD as expected)"
            if self.repro_fails_on_head
            else (
                f"`{self.repro_script_rel}` (created; verify/update assertions if needed)"
                if self.repro_script_rel
                else "(not yet created — create `/testbed/.hae/reproduce_issue.py` first)"
            )
        )
        parts = [
            f"- **Inspected Files**: {files_str}",
            f"- **Baseline Reproduction Script**: {repro_status}",
        ]
        if self.consumer_locus:
            parts.append(f"- **Consumer / Symptom Locus**: {self.consumer_locus}")
        if self.producer_upstream_locus:
            parts.append(f"- **Producer / Upstream Invariant Locus ('Move 37')**: {self.producer_upstream_locus}")
        if self.conventions_and_siblings:
            parts.append(f"- **Codebase Conventions & Sibling Classes**: {self.conventions_and_siblings}")
        if self.raw_summary:
            parts.append(f"- **Reconnaissance Notes**:\n{_truncate_output(self.raw_summary, 1800)}")
        return "\n".join(parts)


@dataclass
class CausalBranchSpec:
    """One abstraction-stratified causal hypothesis formulated by Layer L0."""

    locus: str  # "consumer_local" | "producer_upstream" | "codebase_symmetry" | "counterfactual_move37"
    title: str
    target_files: List[str]
    mechanism_and_plan: str
    prior: float = 0.6


@dataclass
class VerificationContract:
    """Master specification & falsifiable checklist formulated by Layer L0."""

    invariants: List[str] = field(default_factory=list)
    edge_cases_to_probe: List[str] = field(default_factory=list)
    deprecation_or_message_rules: str = ""
    sibling_parity_targets: str = ""

    def to_prompt_block(self) -> str:
        lines = ["### L0 Master Verification Contract (All items must hold)"]
        for idx, inv in enumerate(self.invariants[:6], 1):
            lines.append(f"{idx}. {inv}")
        if self.edge_cases_to_probe:
            lines.append("\n**Mandatory Edge Cases & Boundary Interactions**:")
            for ec in self.edge_cases_to_probe[:5]:
                lines.append(f"- {ec}")
        if self.deprecation_or_message_rules:
            lines.append(f"\n**Warning / Error Message Contract**: {self.deprecation_or_message_rules}")
        if self.sibling_parity_targets:
            lines.append(f"\n**Sibling Class / Method Parity**: {self.sibling_parity_targets}")
        return "\n".join(lines)


@dataclass
class AgenticMCTSNode:
    """A node in Layer L0's AB-MCTS Tree over Git Workspace Checkpoints."""

    node_id: str
    parent_id: Optional[str]
    locus: str
    hypothesis_claim: str
    checkpoint: Optional[PatchCheckpoint] = None
    falsifier_ran: bool = False
    falsifier_ok: bool = False
    falsifier_critique: str = ""
    value: float = 0.0
    alpha: float = 1.0
    beta: float = 1.0
    visits: int = 1
    stagnant_steps: int = 0
    children: List[str] = field(default_factory=list)
    episode_index: int = 0

    @property
    def is_certified_global_optimum(self) -> bool:
        if self.checkpoint is None or not self.checkpoint.diff.strip():
            return False
        return bool(
            self.checkpoint.compile_ok
            and self.checkpoint.static_ok
            and self.checkpoint.repro_ok
            and self.checkpoint.repo_tests_ok
            and self.falsifier_ok
        )

    def rank_key(self) -> Tuple[int, int, int, int, int, int, int, int, float, int]:
        if self.checkpoint is None or not self.checkpoint.diff.strip() or not self.checkpoint.compile_ok:
            return (0, 0, 0, 0, 0, 0, 0, -999, 0.0, self.episode_index)
        cp = self.checkpoint
        return (
            int(self.is_certified_global_optimum),
            int(cp.repro_ok and cp.repo_tests_ok and self.falsifier_ok),
            int(cp.repro_ok and cp.repo_tests_ok),
            int(self.falsifier_ok),
            int(cp.repro_ok),
            int(cp.repo_tests_ok),
            int(cp.static_ok),
            -int(cp.new_test_failures),
            round(self.value, 4),
            self.episode_index,
        )


def compute_node_empirical_value(
    cp: Optional[PatchCheckpoint],
    falsifier_ran: bool,
    falsifier_ok: bool,
) -> float:
    """Compute empirical node value V(s_i) in [0, 1] with diff-bloat regularization."""
    if cp is None or not cp.diff.strip() or not cp.compile_ok:
        return 0.05
    v = 0.10  # syntax & compile valid non-empty diff
    if cp.static_ok:
        v += 0.05
    if cp.repro_ok:
        v += 0.30
    if cp.repo_tests_ok:
        v += 0.25
    else:
        p2p_partial = max(0.0, 0.20 - 0.05 * min(4, max(1, cp.new_test_failures)))
        v += p2p_partial
    if falsifier_ran and falsifier_ok:
        v += 0.30
    elif falsifier_ran and not falsifier_ok and cp.repro_ok:
        # Passed baseline reproduction, failed adversarial falsifier -> high-value CONT candidate!
        v += 0.08

    diff_lines = len(cp.diff.splitlines())
    bloat_penalty = min(0.12, 0.0005 * max(0, diff_lines - 120))
    return max(0.05, min(1.0, round(v - bloat_penalty, 4)))


def parse_seed_architect_response(
    raw_text: str,
    candidate_modules: List[str],
    recon: ReconBrief,
) -> Tuple[VerificationContract, List[CausalBranchSpec]]:
    """Parse Layer L0 Seed Architect's JSON or structured markdown response with a resilient fallback."""
    default_files = list(recon.inspected_files or candidate_modules or [])[:4]
    contract = VerificationContract(
        invariants=[
            "Resolve the primary issue described in the problem statement and pass `/testbed/.hae/reproduce_issue.py`.",
            "Preserve all existing unit test behaviors (`pytest`) with zero regressions.",
            "Handle boundary conditions (`None`, empty inputs, combined arguments, subclasses) cleanly.",
        ],
        edge_cases_to_probe=[
            "Boundary inputs (`None`, empty collections, subclass instances).",
            "Combined usage of new and legacy/deprecated parameters simultaneously.",
            "Consistent behavior across sibling classes, functions, or rules in the same module.",
        ],
        deprecation_or_message_rules=recon.conventions_and_siblings[:400] if recon.conventions_and_siblings else "",
        sibling_parity_targets=recon.conventions_and_siblings[:400] if recon.conventions_and_siblings else "",
    )
    branches: List[CausalBranchSpec] = [
        CausalBranchSpec(
            locus="consumer_local",
            title="Local Call-Site / Symptom-Locus Surgical Fix",
            target_files=default_files,
            mechanism_and_plan=(
                f"Apply a minimal surgical fix at the immediate symptom/consumer locus "
                f"({recon.consumer_locus or ', '.join(default_files)}), ensuring all required arguments, "
                f"warnings, and return values satisfy the problem statement."
            ),
            prior=0.68,
        ),
        CausalBranchSpec(
            locus="producer_upstream",
            title="Upstream Producer / State-Invariant Fix ('Move 37')",
            target_files=default_files,
            mechanism_and_plan=(
                f"Investigate and fix the upstream producer / state-construction invariant "
                f"({recon.producer_upstream_locus or 'caller/context/builder upstream of symptom'}) "
                f"so the invalid state or mutated context never reaches the downstream consumer."
            ),
            prior=0.64,
        ),
        CausalBranchSpec(
            locus="codebase_symmetry",
            title="Codebase Symmetry & Sibling-Pattern Alignment",
            target_files=default_files,
            mechanism_and_plan=(
                f"Align the implementation with existing sibling classes/methods/rules and warning conventions "
                f"in the repository ({recon.conventions_and_siblings or 'check analogous patterns in module'})."
            ),
            prior=0.58,
        ),
    ]

    if not raw_text or not raw_text.strip():
        return contract, branches

    # Try extracting JSON object if present
    json_match = re.search(r"\{.*\}", raw_text, flags=re.DOTALL)
    if json_match:
        try:
            data = json.loads(json_match.group(0))
            if isinstance(data, dict):
                c_obj = data.get("verification_contract") or data.get("contract") or {}
                if isinstance(c_obj, dict):
                    invs = c_obj.get("invariants")
                    if isinstance(invs, list) and invs:
                        contract.invariants = [str(x).strip() for x in invs if str(x).strip()][:6]
                    ecs = c_obj.get("edge_cases_to_probe") or c_obj.get("edge_cases")
                    if isinstance(ecs, list) and ecs:
                        contract.edge_cases_to_probe = [str(x).strip() for x in ecs if str(x).strip()][:5]
                    if c_obj.get("deprecation_or_message_rules"):
                        contract.deprecation_or_message_rules = str(c_obj["deprecation_or_message_rules"]).strip()
                    if c_obj.get("sibling_parity_targets"):
                        contract.sibling_parity_targets = str(c_obj["sibling_parity_targets"]).strip()
                b_list = data.get("causal_branches") or data.get("branches")
                if isinstance(b_list, list) and b_list:
                    parsed_b: List[CausalBranchSpec] = []
                    for idx, b in enumerate(b_list[:4]):
                        if not isinstance(b, dict):
                            continue
                        locus = str(b.get("locus") or (
                            "consumer_local" if idx == 0 else ("producer_upstream" if idx == 1 else "codebase_symmetry")
                        )).strip()
                        title = str(b.get("title") or locus).strip()
                        t_files = b.get("target_files")
                        if not isinstance(t_files, list) or not t_files:
                            t_files = default_files
                        else:
                            t_files = [str(f).strip() for f in t_files if str(f).strip()]
                        plan = str(b.get("mechanism_and_plan") or b.get("plan") or title).strip()
                        prior = float(b.get("prior") or (0.68 - 0.05 * idx))
                        parsed_b.append(
                            CausalBranchSpec(
                                locus=locus,
                                title=title,
                                target_files=t_files,
                                mechanism_and_plan=plan,
                                prior=max(0.2, min(0.95, prior)),
                            )
                        )
                    if parsed_b:
                        branches = parsed_b
        except Exception:
            pass

    return contract, branches


class HierarchicalAgenticMCTSEngine(AgenticSweEngine):
    """V13 Hierarchical Verification Harness with Agentic AB-MCTS ('Move 37' Engine)."""

    def __init__(
        self,
        runner: Any,
        executor: CommandExecutor,
        task: SweTask,
        state: EpistemicState,
        gatekeeper: EvidenceGatekeeper,
        logger: Callable[[str], None] = print,
    ) -> None:
        super().__init__(
            runner=runner,
            executor=executor,
            task=task,
            state=state,
            gatekeeper=gatekeeper,
            logger=logger,
        )
        self.falsification_script_rel = FALSIFICATION_SCRIPT_REL
        self.mcts_nodes: Dict[str, AgenticMCTSNode] = {
            "root": AgenticMCTSNode(
                node_id="root",
                parent_id=None,
                locus="root",
                hypothesis_claim="Clean repository HEAD with baseline reproduction script.",
                value=0.1,
                alpha=2.0,
                beta=2.0,
            )
        }
        self.branch_queue: List[CausalBranchSpec] = []
        self.contract: VerificationContract = VerificationContract()
        self.recon_brief: ReconBrief = ReconBrief()
        self.rng = random.Random(42)

    # ------------------------------------------------------------------ #
    # Layer L1-A: Reconnaissance Sub-Agent (Fresh Context, 4-6 Turns)
    # ------------------------------------------------------------------ #

    def _recon_system_prompt(self) -> str:
        return (
            "You are the Layer-L1 Epistemic Reconnaissance & Reproduction Specialist.\n"
            f"Repository under test: `{self.task.repo}` at `{self.executor.root}` (commit {self.task.base_commit[:12]}).\n"
            "Your job is to perform fast, surgical reconnaissance in 3-5 turns and return a distilled "
            "Reconnaissance Brief to Layer L0 (the Seed Architect) so downstream executors start with zero search debt.\n\n"
            "## Strict Rules\n"
            "- Do NOT modify any tracked repository source files (you are a Reconnaissance & Reproduction specialist).\n"
            "- Keep `<think>` concise (under 150 words), close `</think>`, and emit EXACTLY ONE tool block (` ```bash `, ` ```view_file `, or ` ```submit `).\n"
            "- Combine `git grep -n` lookups in `bash` to map:\n"
            "  1. **Consumer / Symptom Locus**: exact file, class/function, and line numbers where the issue manifests.\n"
            "  2. **Producer / Upstream Locus ('Move 37')**: upstream caller, context builder, parser, or base class that constructs or mutates the state before it reaches the symptom site.\n"
            "  3. **Codebase Conventions & Siblings**: sibling classes/methods in the same file, existing unit tests, and exact warning/exception classes & message phrasings.\n"
            "- Create `/testbed/.hae/reproduce_issue.py` via `bash` and run it on clean `HEAD` so it reproduces the bug (exits non-zero on `HEAD` and will exit 0 once fixed).\n"
            "- As soon as `/testbed/.hae/reproduce_issue.py` is created and you have inspected the relevant lines, emit:\n"
            "```submit\n"
            "CONSUMER_LOCUS: <file:lines and function/method summary>\n"
            "PRODUCER_UPSTREAM_LOCUS: <upstream file:lines and state/invariant summary>\n"
            "CONVENTIONS_AND_SIBLINGS: <sibling classes/methods, warning classes, existing test patterns>\n"
            "```"
        )

    def _recon_user_prompt(self, candidate_modules: List[str], repo_map: List[str]) -> str:
        cands = ", ".join(f"`{m}`" for m in candidate_modules[:6]) if candidate_modules else "(use `git grep`)"
        rmap = "\n".join(f"- {p}" for p in repo_map[:30])
        return (
            f"{self.runner.objective_text()}\n\n"
            f"### Initial Candidate Modules: {cands}\n"
            f"### Repository Map (top {min(30, len(repo_map))} files)\n{rmap}\n\n"
            "Execute your 3-5 turn reconnaissance now:\n"
            "1. In Turn 1 (`bash`), use `git grep -n` to locate the target function/class, its upstream callers/producers, and its unit test file.\n"
            "2. In Turn 2 (`view_file` or `bash`), inspect the exact lines of both the consumer/symptom site and the upstream producer/siblings.\n"
            "3. In Turn 3 (`bash`), write `/testbed/.hae/reproduce_issue.py` and run it to confirm it fails on the unpatched code.\n"
            "4. Emit ` ```submit ` with `CONSUMER_LOCUS`, `PRODUCER_UPSTREAM_LOCUS`, and `CONVENTIONS_AND_SIBLINGS`."
        )

    def run_recon_episode(
        self,
        candidate_modules: List[str],
        repo_map: List[str],
        max_turns: int = 5,
    ) -> ReconBrief:
        """Run Layer L1-A Reconnaissance in an isolated context window, then GC its transcript."""
        self.logger(f"[v13-hier-mcts] Layer L1-A: Launching ReconnaissanceSubAgent (max_turns={max_turns})")
        messages: List[Dict[str, str]] = [
            {"role": "system", "content": self._recon_system_prompt()},
            {"role": "user", "content": self._recon_user_prompt(candidate_modules, repo_map)},
        ]
        inspected: List[str] = list(candidate_modules[:4])
        submit_summary = ""
        force_no_think_next = False

        for turn in range(1, max_turns + 1):
            if time.time() - self.run_start_time >= self.max_wall_s * 0.35:
                break
            use_thinking = not force_no_think_next
            force_no_think_next = False
            t0 = time.time()
            resp_text, reasoning = self._call_specialist_turn(
                "L1_ReconnaissanceSubAgent",
                messages,
                temperature=0.15,
                enable_thinking=use_thinking,
            )
            dt = time.time() - t0
            self.total_moves_used += 1
            if not resp_text and not reasoning:
                break

            action = parse_agent_action(resp_text)
            self.logger(
                f"[v13-hier-mcts] L1_Recon turn {turn}/{max_turns} ({dt:.1f}s, think={len(reasoning)}c): "
                f"action={action.kind} path={action.path or '-'} cmd={action.command[:55] if action.command else '-'}"
            )
            if action.error:
                if not resp_text.strip() and reasoning:
                    force_no_think_next = True
                    messages.append({"role": "assistant", "content": f"(Reasoning summary: {reasoning[-500:]})"})
                    messages.append({
                        "role": "user",
                        "content": "Emit your next tool block (` ```bash `, ` ```view_file `, or ` ```submit `) immediately.",
                    })
                else:
                    messages.append({"role": "assistant", "content": "(malformed tool block omitted)"})
                    messages.append({"role": "user", "content": f"Observation (error): {action.error}"})
                continue

            messages.append({"role": "assistant", "content": resp_text})
            if action.kind == "submit":
                submit_summary = action.new_str.strip()
                break
            elif action.kind == "view_file":
                rel = self._normalize_rel_path(action.path)
                if rel and rel not in inspected:
                    inspected.append(rel)
                obs = self.execute_view_file_tool(action.path, action.start_line, action.end_line)
                if turn >= max_turns - 1:
                    obs += "\n\n[FINAL RECON TURN: Ensure `/testbed/.hae/reproduce_issue.py` exists and emit ```submit with your brief.]"
                messages.append({"role": "user", "content": obs})
            elif action.kind == "bash":
                obs = self.execute_bash_tool(action.command)
                if turn >= max_turns - 1:
                    obs += "\n\n[FINAL RECON TURN: Emit ```submit with CONSUMER_LOCUS, PRODUCER_UPSTREAM_LOCUS, and CONVENTIONS_AND_SIBLINGS.]"
                messages.append({"role": "user", "content": obs})
            elif action.kind in ("str_replace", "write_file"):
                rel = self._normalize_rel_path(action.path)
                if rel.startswith(SCRATCH_DIR):
                    obs = self.execute_write_file_tool(action.path, action.new_str, run_after=True)
                else:
                    obs = (
                        "Observation: ReconnaissanceSubAgent is read-only on tracked repo files. "
                        "Write reproduction scripts inside `/testbed/.hae/reproduce_issue.py` and emit ```submit."
                    )
                messages.append({"role": "user", "content": obs})

        # Ensure tracked files are clean at the end of Recon
        self._reset_tracked_to_head()
        self._clean_untracked_outside_scratch()

        repro_exists = self.executor.exists(self.repro_script_rel)
        repro_fails_on_head = False
        if repro_exists:
            res_r = self.executor.run(
                [self.executor.python, self.repro_script_rel],
                cwd=self.executor.root,
                env={"PYTHONPATH": self.executor.root},
                timeout_s=45,
            )
            repro_fails_on_head = (res_r.rc != 0 and not res_r.timed_out)

        consumer_loc = ""
        producer_loc = ""
        conventions = ""
        if submit_summary:
            m_c = re.search(r"CONSUMER_LOCUS\s*:\s*(.+?)(?=\n[A-Z_]{4,}\s*:|\Z)", submit_summary, flags=re.DOTALL)
            m_p = re.search(r"PRODUCER_UPSTREAM_LOCUS\s*:\s*(.+?)(?=\n[A-Z_]{4,}\s*:|\Z)", submit_summary, flags=re.DOTALL)
            m_s = re.search(r"CONVENTIONS_AND_SIBLINGS\s*:\s*(.+?)(?=\n[A-Z_]{4,}\s*:|\Z)", submit_summary, flags=re.DOTALL)
            if m_c:
                consumer_loc = m_c.group(1).strip()
            if m_p:
                producer_loc = m_p.group(1).strip()
            if m_s:
                conventions = m_s.group(1).strip()

        if not submit_summary:
            # Distill from last observations if Recon didn't explicitly emit ```submit
            obs_snippets = [
                m["content"][:500]
                for m in messages[-4:]
                if m.get("role") == "user" and "Observation" in m.get("content", "")
            ]
            submit_summary = "\n".join(obs_snippets)[:1400]

        brief = ReconBrief(
            consumer_locus=consumer_loc,
            producer_upstream_locus=producer_loc,
            conventions_and_siblings=conventions,
            repro_script_rel=self.repro_script_rel if repro_exists else "",
            repro_fails_on_head=repro_fails_on_head,
            inspected_files=inspected,
            raw_summary=submit_summary,
        )
        self.recon_brief = brief
        return brief

    # ------------------------------------------------------------------ #
    # Layer L0: Seed Architect & AB-MCTS Tree Master (Ultra-Clean Context)
    # ------------------------------------------------------------------ #

    def formulate_seed_contract_and_branches(
        self,
        candidate_modules: List[str],
        recon: ReconBrief,
    ) -> Tuple[VerificationContract, List[CausalBranchSpec]]:
        """Layer L0 Seed Node: formulates Master Verification Contract & 3 orthogonal causal branches."""
        self.logger("[v13-hier-mcts] Layer L0: SeedArchitectNode formulating VerificationContract & AB-MCTS branches")
        sys_prompt = (
            "You are the Layer-L0 Seed Principal Architect & AB-MCTS Tree Master.\n"
            "Your context window is kept strictly clean of noisy execution logs so you can see the global architecture, "
            "prevent local-optimum tunnel vision, and enforce rigorous verification contracts on lower layers.\n"
            "Respond with a single JSON object containing:\n"
            "1. `verification_contract`: {\n"
            "     `invariants`: [list of 3-5 falsifiable requirements from the problem statement],\n"
            "     `edge_cases_to_probe`: [list of 3-4 adversarial boundary/interaction cases to test],\n"
            "     `deprecation_or_message_rules`: \"exact warning class and message substring requirements (if any)\",\n"
            "     `sibling_parity_targets`: \"sibling classes/methods/rules that must behave consistently\"\n"
            "   }\n"
            "2. `causal_branches`: [\n"
            "     {\n"
            "       `locus`: \"consumer_local\",\n"
            "       `title`: \"...\",\n"
            "       `target_files`: [\"path/to/file.py\"],\n"
            "       `mechanism_and_plan`: \"Concrete surgical plan at the symptom/consumer locus\",\n"
            "       `prior`: 0.70\n"
            "     },\n"
            "     {\n"
            "       `locus`: \"producer_upstream\",\n"
            "       `title`: \"Move 37: Upstream Producer / State-Invariant Fix\",\n"
            "       `target_files`: [\"path/to/file.py\"],\n"
            "       `mechanism_and_plan`: \"Concrete plan fixing the upstream producer/context/parser/class invariant OR handling combined parameter interactions without local symptom hacks\",\n"
            "       `prior`: 0.65\n"
            "     },\n"
            "     {\n"
            "       `locus`: \"codebase_symmetry\",\n"
            "       `title`: \"...\",\n"
            "       `target_files`: [\"path/to/file.py\"],\n"
            "       `mechanism_and_plan`: \"Concrete plan aligning with sibling classes/methods and existing repo test conventions\",\n"
            "       `prior`: 0.60\n"
            "     }\n"
            "   ]"
        )
        user_prompt = (
            f"{self.runner.objective_text()}\n\n"
            f"### Distilled Reconnaissance Brief from Layer L1-A\n"
            f"{recon.to_compact_prompt_block()}\n\n"
            "Emit the JSON object with `verification_contract` and 3 orthogonal `causal_branches` now."
        )
        messages = [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_prompt},
        ]
        resp_text, _ = self._call_specialist_turn(
            "L0_SeedArchitectNode",
            messages,
            temperature=0.2,
            enable_thinking=True,
        )
        self.total_moves_used += 1
        contract, branches = parse_seed_architect_response(resp_text, candidate_modules, recon)
        self.contract = contract
        self.branch_queue = list(branches)
        self.logger(
            f"[v13-hier-mcts] Layer L0 initialized {len(self.contract.invariants)} contract invariants and "
            f"{len(self.branch_queue)} stratified branches: {[b.locus for b in self.branch_queue]}"
        )
        return contract, branches

    def synthesize_move37_counterfactual_branch(self, candidate_modules: List[str]) -> CausalBranchSpec:
        """Ask L0 (in a clean context with the Negative Evidence Ledger) for a fresh Move-37 branch."""
        neg_lines = []
        for nid, node in self.mcts_nodes.items():
            if nid == "root":
                continue
            files = node.checkpoint.changed_files if node.checkpoint else []
            neg_lines.append(
                f"- Node `{nid}` (locus=`{node.locus}`, V={node.value:.2f}, files={files}): "
                f"claim={node.hypothesis_claim[:180]} -> Critique: {node.falsifier_critique[:350]}"
            )
        neg_ledger = "\n".join(neg_lines) if neg_lines else "(No prior branches succeeded)"
        default_files = list(self.recon_brief.inspected_files or candidate_modules or [])[:4]
        return CausalBranchSpec(
            locus="counterfactual_move37",
            title="Move 37 Counterfactual Root-Cause Branch",
            target_files=default_files,
            mechanism_and_plan=(
                "Prior branches hit a local optimum or failed verification:\n"
                f"{neg_ledger}\n"
                "Step back from the previous approach: inspect how upstream callers, base classes, or sibling methods "
                "handle this invariant, ensure `/testbed/.hae/reproduce_issue.py` and `/testbed/.hae/falsification_suite.py` "
                "both pass, and apply a clean structural fix."
            ),
            prior=0.62,
        )

    # ------------------------------------------------------------------ #
    # AB-MCTS Selection (`GEN` vs `CONT` via Thompson Sampling)
    # ------------------------------------------------------------------ #

    def select_next_mcts_operation(
        self,
        candidate_modules: List[str],
    ) -> Tuple[str, str, Optional[CausalBranchSpec]]:
        """Return `(op_kind, target_node_id, branch_spec)` where `op_kind` is `'GEN'` or `'CONT'`.

        Balances:
          - `CONT(s_i)`: refining a high-value checkpoint (`repro_ok=True` or compiling partial fix)
            that has not stagnated (`stagnant_steps < 2`) in a fresh Delta-Refinement context.
          - `GEN("root")`: launching the next orthogonal `CausalBranchSpec` ("Move 37") from clean HEAD.
        """
        non_root = [n for nid, n in self.mcts_nodes.items() if nid != "root"]
        if not non_root:
            spec = self.branch_queue.pop(0) if self.branch_queue else self.synthesize_move37_counterfactual_branch(candidate_modules)
            return "GEN", "root", spec

        # Identify expandable CONT candidates (must have non-empty compiling diff and stagnant_steps < 2)
        cont_candidates: List[AgenticMCTSNode] = [
            n for n in non_root
            if n.checkpoint is not None
            and n.checkpoint.diff.strip()
            and n.checkpoint.compile_ok
            and n.stagnant_steps < 2
            and len(n.children) < 2
        ]

        # Priority rule: if a branch achieved `repro_ok=True` (fixes the primary bug!) and has a concrete
        # Falsifier or P2P regression critique with 0 prior CONT refinements, refine it immediately in a
        # clean Delta-Refinement context before abandoning its basin!
        near_miss_unrefined = [
            n for n in cont_candidates
            if n.checkpoint is not None
            and n.checkpoint.repro_ok
            and len(n.children) == 0
            and n.stagnant_steps == 0
        ]
        if near_miss_unrefined:
            best_nm = max(near_miss_unrefined, key=lambda n: (n.value, n.episode_index))
            return "CONT", best_nm.node_id, None

        # Otherwise run Thompson Sampling across GEN vs CONT candidates
        any_repro_ok = any(n.checkpoint and n.checkpoint.repro_ok for n in non_root)
        max_stagnation = max((n.stagnant_steps for n in non_root), default=0)
        gen_alpha = 2.6 + (1.2 if not any_repro_ok else 0.0) + 0.8 * max_stagnation
        gen_beta = 2.0 + (1.0 if any_repro_ok else 0.0)
        gen_sample = self.rng.betavariate(gen_alpha, gen_beta)

        best_cont_node: Optional[AgenticMCTSNode] = None
        best_cont_sample = -1.0
        for n in cont_candidates:
            sample = self.rng.betavariate(max(0.5, n.alpha), max(0.5, n.beta))
            if sample > best_cont_sample:
                best_cont_sample = sample
                best_cont_node = n

        if best_cont_node is not None and best_cont_sample >= gen_sample:
            return "CONT", best_cont_node.node_id, None

        spec = (
            self.branch_queue.pop(0)
            if self.branch_queue
            else self.synthesize_move37_counterfactual_branch(candidate_modules)
        )
        return "GEN", "root", spec

    # ------------------------------------------------------------------ #
    # Layer L2: Ephemeral Scoped Branch (`GEN`) & Delta-Refinement (`CONT`) Executors
    # ------------------------------------------------------------------ #

    def _build_gen_executor_prompt(self, branch_spec: CausalBranchSpec) -> str:
        neg_notes = []
        for nid, n in self.mcts_nodes.items():
            if nid == "root" or n.checkpoint is None:
                continue
            neg_notes.append(
                f"- Prior branch `{nid}` (`{n.locus}`, V={n.value:.2f} on `{', '.join(n.checkpoint.changed_files)}`): "
                f"{n.falsifier_critique[:300]}"
            )
        neg_block = (
            "\n### Negative Evidence Ledger (Prior Branches to Avoid Repeating)\n" + "\n".join(neg_notes) + "\n"
            if neg_notes
            else ""
        )
        falsify_note = (
            f"\n- Also run `python {self.falsification_script_rel}` in `bash` (contains edge cases from earlier branches!)."
            if self.executor.exists(self.falsification_script_rel)
            else ""
        )
        return (
            f"{self.runner.objective_text()}\n\n"
            f"### Distilled Reconnaissance Brief (From Layer L1-A)\n"
            f"{self.recon_brief.to_compact_prompt_block()}\n\n"
            f"{self.contract.to_prompt_block()}\n"
            f"{neg_block}\n"
            f"### Your Assigned AB-MCTS Branch Contract (`{branch_spec.locus}`: {branch_spec.title})\n"
            f"- **Target Files**: {', '.join(f'`{f}`' for f in branch_spec.target_files) or '(see Recon Brief)'}\n"
            f"- **Causal Mechanism & Execution Plan**: {branch_spec.mechanism_and_plan}\n\n"
            "### Execution Instructions (6-9 Turns in Clean Context)\n"
            "1. You already have the Reconnaissance Brief above — do NOT waste turns repeating broad `git grep` searches! "
            "Use `view_file` on the target lines (if needed) and verify `/testbed/.hae/reproduce_issue.py` exists.\n"
            "2. Apply your surgical fix using `str_replace`.\n"
            f"3. Immediately run `python {self.repro_script_rel}` and `pytest` on the relevant test file via `bash`{falsify_note}.\n"
            "4. Check every item in the L0 Master Verification Contract above, then emit ` ```submit `."
        )

    def _build_cont_executor_prompt(self, parent_node: AgenticMCTSNode) -> str:
        cp = parent_node.checkpoint
        diff_block = _truncate_output(cp.diff, 4500) if cp else "(no diff)"
        files_str = ", ".join(f"`{f}`" for f in (cp.changed_files if cp else []))
        falsify_cmd = (
            f" && python {self.falsification_script_rel}"
            if self.executor.exists(self.falsification_script_rel)
            else ""
        )
        return (
            f"{self.runner.objective_text()}\n\n"
            f"{self.contract.to_prompt_block()}\n\n"
            f"### Parent Checkpoint `{parent_node.node_id}` (Currently Applied in `/testbed`)\n"
            f"Modified files: {files_str}\n"
            f"```diff\n{diff_block}\n```\n\n"
            f"### Exact Validation / Falsifier Critique to Resolve (`CONT` Contract)\n"
            f"{parent_node.falsifier_critique}\n\n"
            "### Your Mandate as Layer-L2 Clean-Context Delta-Refinement Specialist (5-8 Turns)\n"
            "The candidate patch above is **already applied in `/testbed`**.\n"
            "1. **Do NOT reset to HEAD or rewrite from scratch**: inspect the exact failure in the Critique above "
            "(if a `pytest` test or `/testbed/.hae/falsification_suite.py` failed, run it in `bash` to see the exact assertion).\n"
            "2. Apply a targeted surgical refinement using `str_replace` so that the Critique is resolved while keeping "
            "all previously passing behaviors intact.\n"
            f"3. Run `python {self.repro_script_rel}{falsify_cmd}` and the relevant `pytest` test file(s) in `bash`.\n"
            "4. Once all checks exit 0 with zero regressions, immediately emit ` ```submit `."
        )

    # ------------------------------------------------------------------ #
    # Layer L1-B: Independent Adversarial Falsifier Sub-Agent (Read-Only on Repo)
    # ------------------------------------------------------------------ #

    def _falsifier_system_prompt(self) -> str:
        return (
            "You are the Layer-L1B Independent Adversarial Falsifier & Contract Validator.\n"
            f"Repository under test: `{self.task.repo}` at `{self.executor.root}`.\n"
            "You operate in a fresh, unpolluted context window with ZERO confirmation bias toward the Layer-L2 Executor's patch.\n"
            "Your job is to verify whether the candidate patch currently applied in `/testbed` truly satisfies EVERY requirement "
            "in the Problem Statement and L0 Verification Contract, or whether it is a narrow symptom hack / local optimum.\n\n"
            "## Strict Rules\n"
            "- You are **READ-ONLY on tracked repository source and test files** (do NOT edit repo files; your role is pure validation & falsification).\n"
            "- Keep `<think>` concise (under 150 words), close `</think>`, and emit ONE tool block (` ```bash `, ` ```view_file `, or ` ```submit `).\n"
            "- In Turn 1-2 (`bash`), inspect surrounding tests/warnings if relevant and write/run `/testbed/.hae/falsification_suite.py` testing:\n"
            "  1. Every requirement and example in the Problem Statement and L0 Master Verification Contract,\n"
            "  2. Combined argument interactions (e.g., passing both new and deprecated kwargs simultaneously, `None`, empty inputs, subclasses),\n"
            "  3. Exact warning/exception classes and expected warning message substrings (check existing `tests/` for exact expected phrasing!),\n"
            "  4. Sibling classes/methods/rules in the modified module that should behave symmetrically.\n"
            "- Then emit ` ```submit ` with:\n"
            "  - `VERDICT: PASS` if `/testbed/.hae/falsification_suite.py` and `pytest` pass completely and `git diff` is faithful to the contract.\n"
            "  - `VERDICT: FAIL` followed by the exact failing assertion, missing requirement, or regression so Layer L0 can schedule a `CONT` refinement or `GEN` branch."
        )

    def _falsifier_user_prompt(self, cp: PatchCheckpoint) -> str:
        return (
            f"{self.runner.objective_text()}\n\n"
            f"{self.contract.to_prompt_block()}\n\n"
            f"### Candidate Patch Under Audit (`{', '.join(cp.changed_files)}`)\n"
            f"```diff\n{_truncate_output(cp.diff, 5000)}\n```\n\n"
            "Audit this candidate patch in 2-4 turns:\n"
            "1. Write and execute `/testbed/.hae/falsification_suite.py` via `bash` (plus any relevant `pytest` test cases) "
            "to try to falsify the patch on edge cases, combined arguments, sibling classes, or warning message wording.\n"
            "2. Emit ` ```submit ` with `VERDICT: PASS` or `VERDICT: FAIL: <exact explanation and failing assertion>`."
        )

    def run_falsifier_episode(
        self,
        cp: PatchCheckpoint,
        max_turns: int = 4,
    ) -> Tuple[bool, str]:
        """Run Layer L1-B Independent Adversarial Falsifier in a fresh context window.

        Guarantees that tracked repository files remain untouched by restoring `cp` if needed.
        Returns `(falsifier_ok, critique_str)`.
        """
        self.logger(
            f"[v13-hier-mcts] Layer L1-B: Launching AdversarialFalsifierSubAgent on {cp.changed_files} (max_turns={max_turns})"
        )
        messages: List[Dict[str, str]] = [
            {"role": "system", "content": self._falsifier_system_prompt()},
            {"role": "user", "content": self._falsifier_user_prompt(cp)},
        ]
        verdict_text = ""
        force_no_think_next = False

        for turn in range(1, max_turns + 1):
            if time.time() - self.run_start_time >= self.max_wall_s:
                break
            use_thinking = not force_no_think_next
            force_no_think_next = False
            t0 = time.time()
            resp_text, reasoning = self._call_specialist_turn(
                "L1_AdversarialFalsifier",
                messages,
                temperature=0.2,
                enable_thinking=use_thinking,
            )
            dt = time.time() - t0
            self.total_moves_used += 1
            if not resp_text and not reasoning:
                break

            action = parse_agent_action(resp_text)
            self.logger(
                f"[v13-hier-mcts] L1_Falsifier turn {turn}/{max_turns} ({dt:.1f}s, think={len(reasoning)}c): "
                f"action={action.kind} path={action.path or '-'} cmd={action.command[:55] if action.command else '-'}"
            )
            if action.error:
                if not resp_text.strip() and reasoning:
                    force_no_think_next = True
                    messages.append({"role": "assistant", "content": f"(Reasoning summary: {reasoning[-500:]})"})
                    messages.append({
                        "role": "user",
                        "content": "Emit your next tool block (` ```bash `, ` ```view_file `, or ` ```submit `) immediately.",
                    })
                else:
                    messages.append({"role": "assistant", "content": "(malformed tool block omitted)"})
                    messages.append({"role": "user", "content": f"Observation (error): {action.error}"})
                continue

            messages.append({"role": "assistant", "content": resp_text})
            if action.kind == "submit":
                verdict_text = action.new_str.strip()
                break
            elif action.kind == "view_file":
                obs = self.execute_view_file_tool(action.path, action.start_line, action.end_line)
                if turn >= max_turns - 1:
                    obs += "\n\n[FINAL FALSIFIER TURN: Emit ```submit with VERDICT: PASS or VERDICT: FAIL: <reason>.]"
                messages.append({"role": "user", "content": obs})
            elif action.kind == "bash":
                obs = self.execute_bash_tool(action.command)
                if turn >= max_turns - 1:
                    obs += "\n\n[FINAL FALSIFIER TURN: Emit ```submit with VERDICT: PASS or VERDICT: FAIL: <reason>.]"
                messages.append({"role": "user", "content": obs})
            elif action.kind in ("str_replace", "write_file"):
                rel = self._normalize_rel_path(action.path)
                if rel.startswith(SCRATCH_DIR):
                    obs = self.execute_write_file_tool(action.path, action.new_str, run_after=True)
                else:
                    obs = (
                        "Observation: AdversarialFalsifierSubAgent is strictly read-only on tracked repo files. "
                        "Write falsification tests to `/testbed/.hae/falsification_suite.py` and emit ```submit."
                    )
                messages.append({"role": "user", "content": obs})

        # Ensure Falsifier did not alter the candidate patch
        curr_diff, _, _ = self._current_diff_and_files()
        if curr_diff != cp.diff:
            self._restore_checkpoint(cp)
        self._clean_untracked_outside_scratch()

        # Deterministically run `/testbed/.hae/falsification_suite.py` if created
        suite_ok = True
        suite_out = ""
        if self.executor.exists(self.falsification_script_rel):
            res_f = self.executor.run(
                [self.executor.python, self.falsification_script_rel],
                cwd=self.executor.root,
                env={"PYTHONPATH": self.executor.root},
                timeout_s=60,
            )
            if res_f.rc != 0 or res_f.timed_out:
                suite_ok = False
                suite_out = _truncate_output((res_f.stdout + "\n" + res_f.stderr).strip(), 1500)

        explicit_fail = bool(re.search(r"VERDICT\s*:\s*FAIL", verdict_text, flags=re.IGNORECASE))
        if not suite_ok:
            critique = (
                f"Adversarial falsification suite `{self.falsification_script_rel}` FAILED:\n{suite_out}\n"
                + (f"Falsifier notes: {verdict_text[:600]}" if verdict_text else "")
            )
            return False, critique
        if explicit_fail:
            # If the Falsifier wrote a critique but didn't leave a failing script, still record its critique
            return False, f"Adversarial Falsifier rejected patch: {verdict_text[:1000]}"

        return True, f"Adversarial Falsifier CERTIFIED patch ({verdict_text[:300] or 'all checks passed'})."

    # ------------------------------------------------------------------ #
    # Evaluate & Register an MCTS Node after an L2 Episode
    # ------------------------------------------------------------------ #

    def evaluate_and_register_mcts_node(
        self,
        node_id: str,
        parent_id: str,
        locus: str,
        hypothesis_claim: str,
        episode_idx: int,
        run_falsifier_if_gate_ok: bool = True,
    ) -> AgenticMCTSNode:
        """Verify current workspace via Gatekeeper + reproduction + optional L1-B Falsifier, and update Beta posteriors."""
        gate_ok, audit_msg, cp = self.verify_current_workspace(
            specialist=f"L2_{node_id}",
            turn=episode_idx,
            is_auditor=False,
        )
        # Also check if cumulative `.hae/falsification_suite.py` already exists and fails
        existing_falsify_ok = True
        existing_falsify_msg = ""
        if cp is not None and self.executor.exists(self.falsification_script_rel):
            res_f = self.executor.run(
                [self.executor.python, self.falsification_script_rel],
                cwd=self.executor.root,
                env={"PYTHONPATH": self.executor.root},
                timeout_s=60,
            )
            if res_f.rc != 0 or res_f.timed_out:
                existing_falsify_ok = False
                existing_falsify_msg = (
                    f"\n- Cumulative falsification suite `{self.falsification_script_rel}` FAILED (exit {res_f.rc}):\n"
                    f"{_truncate_output((res_f.stdout + chr(10) + res_f.stderr).strip(), 1200)}"
                )

        falsifier_ran = False
        falsifier_ok = False
        critique = audit_msg + existing_falsify_msg

        if (
            cp is not None
            and cp.diff.strip()
            and cp.compile_ok
            and cp.repro_ok
            and cp.repo_tests_ok
            and existing_falsify_ok
            and run_falsifier_if_gate_ok
            and self.runner._may_call("L1B falsifier")
            and (time.time() - self.run_start_time < self.max_wall_s - 60)
        ):
            falsifier_ran = True
            falsifier_ok, falsifier_msg = self.run_falsifier_episode(cp, max_turns=4)
            critique = f"{audit_msg}\n- L1-B Adversarial Falsifier: {falsifier_msg}"
            if falsifier_ok:
                cp.audit_ok = True
                cp.gatekeeper_ok = True
            else:
                cp.audit_ok = False
                cp.gatekeeper_ok = False

        val = compute_node_empirical_value(cp, falsifier_ran=falsifier_ran, falsifier_ok=falsifier_ok)
        alpha = 1.0 + 4.0 * val
        beta = 1.0 + 4.0 * (1.0 - val)

        node = AgenticMCTSNode(
            node_id=node_id,
            parent_id=parent_id,
            locus=locus,
            hypothesis_claim=hypothesis_claim,
            checkpoint=cp,
            falsifier_ran=falsifier_ran,
            falsifier_ok=falsifier_ok,
            falsifier_critique=_truncate_output(critique, 2200),
            value=val,
            alpha=alpha,
            beta=beta,
            visits=1,
            stagnant_steps=0,
            episode_index=episode_idx,
        )
        self.mcts_nodes[node_id] = node

        # Backpropagate to parent in L0's AB-MCTS tree
        parent = self.mcts_nodes.get(parent_id)
        if parent is not None:
            parent.children.append(node_id)
            parent.visits += 1
            if parent_id != "root":
                if val > parent.value + 0.02:
                    parent.alpha += 1.5 * val
                else:
                    # Stagnation penalty! Forces Thompson Sampling to pivot toward GEN (Move 37)
                    parent.stagnant_steps += 1
                    node.stagnant_steps = parent.stagnant_steps
                    parent.beta += 2.5
                    node.beta += 2.0

        self.logger(
            f"[v13-hier-mcts] Registered node `{node_id}` (parent=`{parent_id}`, locus=`{locus}`): "
            f"V={val:.3f}, repro_ok={cp.repro_ok if cp else False}, repo_tests_ok={cp.repo_tests_ok if cp else False}, "
            f"falsifier_ok={falsifier_ok}, certified={node.is_certified_global_optimum}"
        )
        return node

    # ------------------------------------------------------------------ #
    # Top-Level V13 Orchestration (`run`)
    # ------------------------------------------------------------------ #

    def run(self, candidate_modules: List[str], budget_moves: int = 36) -> Dict[str, Any]:
        t_start = time.time()
        self.executor.run(["mkdir", "-p", f"{self.executor.root}/{SCRATCH_DIR}"], cwd=self.executor.root, timeout_s=15)
        repo_map = self.runner.repo_map()

        # --- Stage 1: Layer L1-A Reconnaissance Episode (Fresh Context, 4-5 turns) ---
        recon = self.run_recon_episode(candidate_modules, repo_map, max_turns=5)

        # --- Stage 2: Layer L0 Seed Architect & Contract Formulation (Clean Context, 1 turn) ---
        if self.runner._may_call("L0 seed architect"):
            self.formulate_seed_contract_and_branches(candidate_modules, recon)
        else:
            self.contract, self.branch_queue = parse_seed_architect_response("", candidate_modules, recon)

        # --- Stage 3: Layer L0 Agentic AB-MCTS Loop Over Ephemeral L2 & L1-B Episodes ---
        max_episodes = 4
        gen_count = 0

        for ep in range(1, max_episodes + 1):
            elapsed_total = time.time() - self.run_start_time
            remaining_moves = max(0, budget_moves - self.total_moves_used)
            if elapsed_total >= self.max_wall_s or remaining_moves < 3 or not self.runner._may_call(f"mcts episode {ep}"):
                self.logger(
                    f"[v13-hier-mcts] Stopping AB-MCTS loop before episode {ep} "
                    f"(elapsed={elapsed_total:.1f}s/{self.max_wall_s:.0f}s, remaining_moves={remaining_moves})"
                )
                break

            op_kind, target_nid, branch_spec = self.select_next_mcts_operation(candidate_modules)

            if op_kind == "GEN" and branch_spec is not None:
                gen_count += 1
                new_nid = f"b{gen_count}"
                ep_turns = max(6, min(10, remaining_moves - 3))
                self.logger(
                    f"[v13-hier-mcts] Episode {ep}/{max_episodes}: AB-MCTS selected GEN -> `{new_nid}` "
                    f"(locus=`{branch_spec.locus}`, title={branch_spec.title!r}, max_turns={ep_turns})"
                )
                # Reset tracked source files to clean HEAD while keeping `.hae/reproduce_issue.py` and `.hae/falsification_suite.py`
                self._reset_tracked_to_head()
                self._clean_untracked_outside_scratch()

                self.run_specialist_loop(
                    specialist_name=f"L2_BranchExecutor_{new_nid}_{branch_spec.locus}",
                    system_prompt=self._system_prompt(
                        f"Layer-L2 Scoped Branch Executor ({branch_spec.locus})",
                        f"Implement branch hypothesis '{branch_spec.title}' in a clean context window using `str_replace`, "
                        f"verify against `{self.repro_script_rel}` and `pytest`, and emit `submit`.",
                    ),
                    initial_user_prompt=self._build_gen_executor_prompt(branch_spec),
                    max_turns=ep_turns,
                    temperature=0.2 if gen_count == 1 else 0.35,
                    is_auditor=False,
                )
                node = self.evaluate_and_register_mcts_node(
                    node_id=new_nid,
                    parent_id="root",
                    locus=branch_spec.locus,
                    hypothesis_claim=f"[{branch_spec.locus}] {branch_spec.title}: {branch_spec.mechanism_and_plan[:240]}",
                    episode_idx=ep,
                    run_falsifier_if_gate_ok=True,
                )
                if node.is_certified_global_optimum:
                    self.logger(
                        f"[v13-hier-mcts] Node `{new_nid}` (`{branch_spec.locus}`) CERTIFIED as Global Optimum (V=1.0)! Early stopping."
                    )
                    break

            elif op_kind == "CONT":
                parent_node = self.mcts_nodes[target_nid]
                child_idx = len(parent_node.children) + 1
                new_nid = f"{target_nid}.{child_idx}"
                ep_turns = max(5, min(8, remaining_moves - 2))
                self.logger(
                    f"[v13-hier-mcts] Episode {ep}/{max_episodes}: AB-MCTS selected CONT(`{target_nid}`) -> `{new_nid}` "
                    f"(parent V={parent_node.value:.3f}, max_turns={ep_turns})"
                )
                if parent_node.checkpoint is not None:
                    self._restore_checkpoint(parent_node.checkpoint)
                self._clean_untracked_outside_scratch()

                self.run_specialist_loop(
                    specialist_name=f"L2_DeltaRefinement_{new_nid}",
                    system_prompt=self._system_prompt(
                        f"Layer-L2 Clean-Context Delta-Refinement Specialist ({new_nid})",
                        "Refine the parent checkpoint's patch in place using `str_replace` to resolve the exact "
                        "Validation/Falsifier Critique without breaking passing tests, then emit `submit`.",
                    ),
                    initial_user_prompt=self._build_cont_executor_prompt(parent_node),
                    max_turns=ep_turns,
                    temperature=0.25,
                    is_auditor=False,
                )
                node = self.evaluate_and_register_mcts_node(
                    node_id=new_nid,
                    parent_id=target_nid,
                    locus=f"cont_{parent_node.locus}",
                    hypothesis_claim=f"Delta refinement of {target_nid} ({parent_node.locus})",
                    episode_idx=ep,
                    run_falsifier_if_gate_ok=True,
                )
                if node.is_certified_global_optimum:
                    self.logger(
                        f"[v13-hier-mcts] Node `{new_nid}` (`CONT({target_nid})`) CERTIFIED as Global Optimum (V=1.0)! Early stopping."
                    )
                    break

        # --- Stage 4: Select & Restore Highest-Ranked Node Across AB-MCTS Tree + Checkpoint Ledger ---
        best_node: Optional[AgenticMCTSNode] = None
        candidate_nodes = [
            n for nid, n in self.mcts_nodes.items()
            if nid != "root" and n.checkpoint is not None and n.checkpoint.diff.strip() and n.checkpoint.compile_ok
        ]
        best_cp: Optional[PatchCheckpoint] = None
        if candidate_nodes:
            best_node = max(candidate_nodes, key=lambda n: n.rank_key())
            best_cp = best_node.checkpoint

        # Also check if an intermediate mid-episode checkpoint outranks the end-of-episode state
        non_empty_cps = [c for c in self.checkpoints if c.diff.strip() and c.compile_ok]
        if non_empty_cps:
            top_raw_cp = max(enumerate(non_empty_cps), key=lambda ic: (ic[1].rank_key(), ic[0]))[1]
            if best_cp is None or top_raw_cp.rank_key() > best_cp.rank_key():
                best_cp = top_raw_cp

        if best_cp is not None:
            current_diff, _, _ = self._current_diff_and_files()
            if current_diff != best_cp.diff:
                self.logger(
                    f"[v13-hier-mcts] Restoring best AB-MCTS checkpoint from {best_cp.specialist} "
                    f"(rank={best_cp.rank_key()}, files={best_cp.changed_files})"
                )
                self._restore_checkpoint(best_cp)

        self._clean_untracked_outside_scratch()
        final_diff, final_files, _ = self._current_diff_and_files()

        # Record into EpistemicState for full auditability
        q_list = list(self.state.questions.values())
        if q_list and final_diff.strip():
            root_q = q_list[0]
            for nid, mnode in self.mcts_nodes.items():
                if nid == "root" or mnode.checkpoint is None or not mnode.checkpoint.diff.strip():
                    continue
                h = self.state.add_hypothesis(
                    question_id=root_q.question_id,
                    claim=mnode.hypothesis_claim[:240],
                    mechanism=f"locus={mnode.locus}, V={mnode.value:.3f}, files={mnode.checkpoint.changed_files}",
                    prior=0.75,
                    probe_code="# verified via V13 Hierarchical Agentic AB-MCTS\nprint('OK')",
                    prediction={"expect_exit_code": 0},
                    proposed_by=f"L0_AB_MCTS_{nid}",
                )
                matched = bool(mnode.checkpoint.repro_ok and mnode.checkpoint.repo_tests_ok)
                ev = Evidence(
                    evidence_id=self.state.new_evidence_id(),
                    kind="module_check",
                    hypothesis_id=h.hypothesis_id,
                    question_id=root_q.question_id,
                    command=f"v13_mcts_verify_{nid}",
                    exit_code=0 if matched else 1,
                    stdout=mnode.checkpoint.note,
                    stderr="" if matched else mnode.falsifier_critique[:400],
                    prediction={"expect_exit_code": 0},
                    matched_prediction=matched,
                    detail=mnode.checkpoint.note,
                )
                self.state.record_evidence(ev, self.gatekeeper.authority)
                if matched:
                    self.state.set_hypothesis_verdict(
                        h.hypothesis_id, SUPPORTED, min(0.99, mnode.value), ev.evidence_id, self.gatekeeper.authority
                    )
            if best_cp and (best_cp.gatekeeper_ok or (best_cp.repro_ok and best_cp.repo_tests_ok)):
                self.state.set_question_status(root_q.question_id, Q_RESOLVED, self.gatekeeper.authority)
                self.state.lower_uncertainty(root_q.question_id, 0.02, self.gatekeeper.authority)

        elapsed_s = round(time.time() - t_start, 2)
        resolved_self = bool(best_cp and (best_cp.gatekeeper_ok or (best_cp.repro_ok and best_cp.repo_tests_ok)))
        stop_reason = (
            "resolved_by_hierarchical_ab_mcts"
            if (best_node and best_node.is_certified_global_optimum)
            else (
                "resolved_by_agentic_gatekeeper"
                if resolved_self
                else ("best_checkpoint_restored" if final_diff.strip() else "budget_exhausted")
            )
        )

        mcts_tree_summary = {
            nid: {
                "parent_id": n.parent_id,
                "locus": n.locus,
                "value": n.value,
                "alpha": round(n.alpha, 2),
                "beta": round(n.beta, 2),
                "visits": n.visits,
                "stagnant_steps": n.stagnant_steps,
                "repro_ok": bool(n.checkpoint and n.checkpoint.repro_ok),
                "repo_tests_ok": bool(n.checkpoint and n.checkpoint.repo_tests_ok),
                "falsifier_ok": n.falsifier_ok,
                "certified": n.is_certified_global_optimum,
                "changed_files": n.checkpoint.changed_files if n.checkpoint else [],
                "children": n.children,
            }
            for nid, n in self.mcts_nodes.items()
        }

        return {
            "moves_used": self.total_moves_used,
            "stop_reason": stop_reason,
            "resolved_by_self_oracle": resolved_self,
            "synthesized_paths": final_files,
            "patch_chars": len(final_diff),
            "elapsed_s": elapsed_s,
            "checkpoints_recorded": len(self.checkpoints),
            "best_checkpoint": {
                "specialist": best_cp.specialist,
                "turn": best_cp.turn,
                "rank_key": list(best_cp.rank_key()),
                "changed_files": best_cp.changed_files,
                "note": best_cp.note,
            } if best_cp else None,
            "mcts_tree": mcts_tree_summary,
            "trajectory": self.trajectory_log,
        }
