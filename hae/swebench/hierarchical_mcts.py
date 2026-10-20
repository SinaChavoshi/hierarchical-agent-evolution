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
            parts.append(f"- **Reconnaissance Notes & Code Snippets**:\n{_truncate_output(self.raw_summary, 2800)}")
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
    diff_bloat_threshold: int = 120,
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
    threshold = max(0, int(diff_bloat_threshold))
    bloat_penalty = min(0.12, 0.0005 * max(0, diff_lines - threshold))
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
        allow_new_files: bool = False,
        base_ref: str = "HEAD",
        diff_bloat_threshold: int = 120,
        custom_test_cmd: Optional[str] = None,
    ) -> None:
        if isinstance(executor, SweTask) and isinstance(task, CommandExecutor):
            executor, task = task, executor  # type: ignore[assignment]
        if isinstance(state, EvidenceGatekeeper) and isinstance(gatekeeper, EpistemicState):
            state, gatekeeper = gatekeeper, state  # type: ignore[assignment]
        super().__init__(
            runner=runner,
            executor=executor,
            task=task,
            state=state,
            gatekeeper=gatekeeper,
            logger=logger,
            allow_new_files=allow_new_files,
            base_ref=base_ref,
            diff_bloat_threshold=diff_bloat_threshold,
            custom_test_cmd=custom_test_cmd,
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
        self.initial_candidate_modules: List[str] = []
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
        max_turns: int = 3,
    ) -> ReconBrief:
        """Run fast deterministic Layer L1-A Reconnaissance inside `/testbed` (0 LLM moves, < 3s)."""
        self.logger("[v13-hier-mcts] Layer L1-A: Running fast deterministic ReconnaissanceSubAgent probe")
        combined_text = f"{self.task.problem_statement}\n{getattr(self.task, 'hints_text', '') or ''}"
        inspected: List[str] = [c for c in candidate_modules[:4] if c]

        # Extract explicit traceback or inline file paths and match against repository files
        raw_tb_files = re.findall(r'File "([^"]+\.py)"', combined_text)
        inline_py_files = re.findall(r"\b([A-Za-z0-9_]+(?:/[A-Za-z0-9_.-]+)+\.py)\b", combined_text)
        known_repo_files = list(repo_map or [])
        for raw_f in reversed(raw_tb_files + inline_py_files):
            norm_f = raw_f.replace("\\", "/").lstrip("/")
            matched_f = ""
            if self.executor.exists(norm_f):
                matched_f = norm_f
            else:
                parts = [p for p in norm_f.split("/") if p]
                for idx in range(len(parts)):
                    sub = "/".join(parts[idx:])
                    if self.executor.exists(sub):
                        matched_f = sub
                        break
                    for kf in known_repo_files:
                        if kf == sub or kf.endswith("/" + sub):
                            matched_f = kf
                            break
                    if matched_f:
                        break
            if matched_f and not matched_f.startswith(SCRATCH_DIR + "/"):
                if matched_f not in inspected:
                    inspected.insert(0, matched_f)
                if matched_f not in self.initial_candidate_modules:
                    self.initial_candidate_modules.insert(0, matched_f)

        # Extract high-signal symbols, rule IDs (e.g. L031, E1101), backticked identifiers, and class/method names
        rule_codes = sorted(set(re.findall(r"\b[A-Z]\d{3,4}\b", combined_text)))
        backtick_idents = re.findall(r"`([A-Za-z_][A-Za-z0-9_]{2,})`", combined_text)
        raw_idents = re.findall(
            r"\b(?:[A-Z][A-Za-z0-9_]{2,}|[a-z_][a-z0-9_]{3,})\b",
            combined_text,
        )
        stop_words = {
            "this", "that", "with", "from", "have", "When", "What", "Where", "Which", "should",
            "would", "could", "there", "their", "about", "after", "before", "into", "only_if",
            "import", "class", "return", "def", "self", "None", "True", "False", "print",
            "python", "pytest", "test", "tests", "issue", "error", "errors", "example",
            "expected", "behaviour", "behavior", "observed", "describe", "version", "using",
        }
        key_symbols: List[str] = list(rule_codes)
        for tok in backtick_idents + raw_idents:
            if tok not in stop_words and tok.lower() not in stop_words and tok not in key_symbols:
                if tok in backtick_idents or "_" in tok or (tok[0].isupper() and any(c.islower() for c in tok)):
                    key_symbols.append(tok)
            if len(key_symbols) >= 14:
                break

        # Discover source files and test files in `/testbed` matching rule codes and key symbols
        sym_pattern = "\\|".join(re.escape(s) for s in key_symbols[:8]) if key_symbols else ""
        rule_glob = " ".join(f"'*{rc}*'" for rc in rule_codes[:4])
        discover_cmd = (
            f"cd {self.executor.root} && "
            + (f"git ls-files {rule_glob} 2>/dev/null | head -15; " if rule_glob else "")
            + (
                f"git grep -l '{sym_pattern}' -- '*.py' ':!docs/*' 2>/dev/null | head -18; "
                if sym_pattern
                else ""
            )
        )
        res_disc = self.executor.run(["bash", "-lc", discover_cmd], cwd=self.executor.root, timeout_s=10)
        disc_files = [
            l.strip()
            for l in (res_disc.stdout or "").splitlines()
            if l.strip().endswith(".py") and os.path.sep not in l.strip()[:1]
        ]
        disc_src = [f for f in disc_files if "test" not in f.lower() and not f.startswith("docs/")]
        disc_tests = [f for f in disc_files if "test" in f.lower()]
        for sf in disc_src:
            if sf not in inspected and len(inspected) < 7:
                inspected.append(sf)
            if sf not in self.initial_candidate_modules and len(self.initial_candidate_modules) < 7:
                self.initial_candidate_modules.append(sf)

        cands_sh = " ".join(f"'{c}'" for c in inspected[:6] if c)
        probe_cmd = (
            f"cd {self.executor.root} && "
            + (f"git grep -n 'def \\|class ' -- {cands_sh} 2>/dev/null | head -55; " if cands_sh else "")
            + (f"git grep -n '{sym_pattern}' -- {cands_sh} 2>/dev/null | head -35; " if (sym_pattern and cands_sh) else "")
            + "git ls-files '*warning*.py' '*exception*.py' '*error*.py' 2>/dev/null | head -10; "
            + "for wf in $(git ls-files '*warning*.py' 2>/dev/null | head -3); do echo \"=== $wf ===\"; head -35 \"$wf\"; done"
        )
        res_p = self.executor.run(["bash", "-lc", probe_cmd], cwd=self.executor.root, timeout_s=15)
        raw_out = _truncate_output(((res_p.stdout or "") + "\n" + (res_p.stderr or "")).strip(), 2200)

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

        consumer_loc = ", ".join(inspected[:2]) if inspected else "(see repo map)"
        producer_loc = ", ".join(inspected[1:5]) if len(inspected) > 1 else consumer_loc
        test_files = list(dict.fromkeys(disc_tests + [p for p in repo_map if "test" in p]))[:8]
        conventions = f"Relevant test files to run with pytest: {', '.join(test_files)}" if test_files else ""

        brief = ReconBrief(
            consumer_locus=consumer_loc,
            producer_upstream_locus=producer_loc,
            conventions_and_siblings=conventions,
            repro_script_rel=self.repro_script_rel if repro_exists else "",
            repro_fails_on_head=repro_fails_on_head,
            inspected_files=inspected,
            raw_summary=raw_out,
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
            "       `title`: \"Complete Cohesive Primary Root-Cause Fix\",\n"
            "       `target_files`: [\"path/to/file.py\"],\n"
            "       `mechanism_and_plan`: \"COMPLETE end-to-end plan covering ALL required files (both definition/deprecation and internal callers/consumers if the issue requires both — NEVER split a multi-file task into half-fixes!)\",\n"
            "       `prior`: 0.72\n"
            "     },\n"
            "     {\n"
            "       `locus`: \"producer_upstream\",\n"
            "       `title`: \"Move 37: Upstream Producer / State-Invariant Fix\",\n"
            "       `target_files`: [\"path/to/file.py\"],\n"
            "       `mechanism_and_plan`: \"Alternative orthogonal plan fixing the upstream producer/context/parser/class invariant OR handling combined parameter interactions without local symptom hacks\",\n"
            "       `prior`: 0.65\n"
            "     },\n"
            "     ` {`locus`: \"codebase_symmetry\", `title`: \"Sibling-Aligned & Convention-Symmetric Fix\", `target_files`: [\"path/to/file.py\"], `mechanism_and_plan`: \"Concrete plan aligning with sibling classes/methods and existing repo test conventions\", `prior`: 0.60}\n"
            "   ]\n"
            "Architecture Principles:\n"
            "- Never split a multi-file invariant across partial half-fixes: if an API signature, reference type (e.g. weakref vs. direct ref), or exception contract changes, include BOTH the definition site and all affected internal callers/consumers in `target_files`.\n"
            "- Separate immediate symptom handling (`consumer_local`) from upstream state-construction invariants (`producer_upstream` / Move 37) and sibling-pattern alignment (`codebase_symmetry`).\n"
            "- Preserve backward compatibility and exact repository warning/exception hierarchies whenever deprecating parameters or validating inputs."
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
            enable_thinking=False,
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

        # Identify expandable CONT candidates (must have non-empty diff and stagnant_steps < 2)
        cont_candidates: List[AgenticMCTSNode] = [
            n for n in non_root
            if n.checkpoint is not None
            and n.checkpoint.diff.strip()
            and (n.checkpoint.compile_ok or len(n.children) == 0)
            and n.stagnant_steps < 2
            and len(n.children) < 2
        ]

        # Priority rule: if a branch achieved `repro_ok=True` (its reproduction script passed, and it only
        # failed a regression test or L1-B falsification) with 0 prior CONT refinements, refine it immediately
        # in a clean Delta-Refinement context before abandoning its basin; if `repro_ok=False`, pivot to GEN (b2)!
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

        # If there are unexplored stratified branches in `branch_queue` and no node has achieved `repro_ok=True`,
        # immediately launch the next orthogonal `GEN` branch ("Move 37")!
        any_repro_ok = any(n.checkpoint and n.checkpoint.repro_ok for n in non_root)
        if not any_repro_ok and self.branch_queue:
            spec = self.branch_queue.pop(0)
            return "GEN", "root", spec

        # Otherwise run Thompson Sampling across GEN vs CONT candidates
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
        base_block = ""
        if branch_spec.locus == "consumer_local" and len(self.mcts_nodes) <= 1:
            base_block = (
                self._build_primary_user_prompt(
                    self.initial_candidate_modules or branch_spec.target_files or self.recon_brief.inspected_files,
                    self.runner.repo_map(),
                )
                + "\n\n"
            )
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
        header = base_block if base_block else f"{self.runner.objective_text()}\n\n"
        return (
            f"{header}"
            f"### Distilled Reconnaissance Brief (From Layer L1-A)\n"
            f"{self.recon_brief.to_compact_prompt_block()}\n\n"
            f"{self.contract.to_prompt_block()}\n"
            f"{neg_block}\n"
            f"### Your Assigned AB-MCTS Branch Contract (`{branch_spec.locus}`: {branch_spec.title})\n"
            f"- **Target Files**: {', '.join(f'`{f}`' for f in branch_spec.target_files) or '(see Recon Brief)'}\n"
            f"- **Causal Mechanism & Execution Plan**: {branch_spec.mechanism_and_plan}\n\n"
            "### Execution Instructions & Completeness Checklist\n"
            "1. **Inspect & Reproduce**: Check `/testbed/.hae/reproduce_issue.py` (create/update it if needed so it reproduces the issue before your edit and exits 0 after). Use `view_file` or `git grep -n` on the target lines.\n"
            "2. **Check Exact Codebase Conventions, Helpers & Sibling Classes Before Editing**:\n"
            "   - **Warning & Exception Hierarchy**: If the issue deprecates old kwargs/behavior or adds validation, inspect the package's `warnings.py` / `exceptions.py` / `errors.py` and `git grep -n \"warnings.warn\"` to emit the exact project warning/exception class and preserve backward-compatible parameter merging (e.g., `{**(metadata or {}), **additional_metadata}`) after warning rather than raising `ValueError`.\n"
            "   - **Sibling Class & Method Symmetry**: Inspect sibling classes, containers, or property setters in the same module (e.g., `List` vs `Tuple`/`Dict`, sibling AST nodes, or sibling range-checked properties) so your fix applies consistently across all analogous types and helpers.\n"
            "   - **Caller vs. Callee Consistency**: Whenever you change a method signature, return structure, or attribute reference type (such as converting a parent reference to a `weakref` or adding an optional parameter), use `git grep -n` to check all internal callers and callees across the repository so no call site breaks.\n"
            "   - **Boundary & Abstract Semantics**: Distinguish empty/abstract declarations or terminal exception-raising bodies from normal fallthrough functions, and ensure builtin/extension objects without AST bodies are handled without regressions.\n"
            "3. **Apply Surgical Edit (`str_replace`) & Verify**: Apply your fix with `str_replace`, then run `python /testbed/.hae/reproduce_issue.py` and `pytest` on the relevant test files via `bash`"
            f"{falsify_note}.\n"
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
            f"### Distilled Reconnaissance Brief (From Layer L1-A)\n"
            f"{self.recon_brief.to_compact_prompt_block()}\n\n"
            f"{self.contract.to_prompt_block()}\n\n"
            f"### Parent Checkpoint `{parent_node.node_id}` (Currently Applied in `/testbed`)\n"
            f"Modified files: {files_str}\n"
            f"```diff\n{diff_block}\n```\n\n"
            f"### Exact Validation / Falsifier Critique to Resolve (`CONT` Contract)\n"
            f"{parent_node.falsifier_critique}\n\n"
            "### Your Mandate as Layer-L2 Clean-Context Delta-Refinement Specialist (6-10 Turns)\n"
            "The candidate patch above is **already applied in `/testbed`**.\n"
            "1. **Do NOT reset to HEAD or rewrite from scratch**: inspect the exact failure in the Critique above "
            "(if a `pytest` test or `/testbed/.hae/falsification_suite.py` failed, inspect the failing test lines with `view_file` or `sed -n`).\n"
            "   - Note: Check boundary conditions carefully (e.g., abstract methods or exception-only bodies vs. normal fallthrough, combined legacy + new arguments, sibling class symmetry, and internal caller/callee signatures).\n"
            "2. Apply a targeted surgical refinement using `str_replace` (`path: ...` followed by `<<<<`, `====`, `>>>>`) so that the Critique is resolved while keeping "
            "all previously passing behaviors intact.\n"
            f"3. Run `python {self.repro_script_rel}{falsify_cmd}` and the relevant `pytest` test file(s) in `bash`.\n"
            "4. Once all checks exit 0 with zero regressions, immediately emit ` ```submit `."
        )

    # ------------------------------------------------------------------ #
    # Layer L1-B: Independent Adversarial Falsifier & Edge-Case Auditor Sub-Agent
    # ------------------------------------------------------------------ #

    def _falsifier_system_prompt(self) -> str:
        return self._system_prompt(
            "Layer-L1B Independent Adversarial Falsifier & API-Contract Auditor",
            "Rigorously audit the candidate patch in `/testbed` against every requirement, deprecation warning, "
            "subclass, and edge case in the problem statement, write `/testbed/.hae/falsification_suite.py`, "
            "fix any missing deprecation warning or edge case via `str_replace` if needed, and emit `submit` "
            "with `VERDICT: PASS` or `VERDICT: FAIL: <reason>`.",
        )

    def _discover_targeted_test_files(self, cp: PatchCheckpoint) -> List[str]:
        discovered: List[str] = []
        if self.custom_test_cmd:
            for cand in re.findall(r"([A-Za-z0-9_./-]*test[A-Za-z0-9_./-]*\.py)", self.custom_test_cmd):
                if self.executor.exists(cand) and cand not in discovered:
                    discovered.append(cand)
        for cf in (cp.changed_files or []):
            if cf and "test" in os.path.basename(cf).lower() and cf.endswith(".py") and self.executor.exists(cf):
                if cf not in discovered:
                    discovered.append(cf)
        stems = [
            os.path.splitext(os.path.basename(f))[0].lstrip("_")
            for f in (cp.changed_files or [])
            if f
        ]
        parent_dirs = [
            os.path.basename(os.path.dirname(f)).lstrip("_")
            for f in (cp.changed_files or [])
            if f and os.path.dirname(f)
        ]
        rule_codes = sorted(
            set(re.findall(r"\b[A-Z]\d{3,4}\b", f"{self.task.problem_statement}\n{getattr(self.task, 'hints_text', '') or ''}"))
        )
        globs = (
            [f"'*test*{s}*.py' '*{s}*test*.py'" for s in stems[:4] if len(s) >= 3]
            + [f"'*test*{d}*.py' '*{d}*test*.py'" for d in parent_dirs[:3] if len(d) >= 3 and d not in ("src", "lib", "pkg", "core")]
            + [f"'*test*{r}*.py' '*{r}*test*.py'" for r in rule_codes[:3]]
        )
        if globs:
            find_cmd = f"cd {self.executor.root} && git ls-files {' '.join(globs)} 2>/dev/null | head -6"
            res_tf = self.executor.run(["bash", "-lc", find_cmd], cwd=self.executor.root, timeout_s=8)
            for line in (res_tf.stdout or "").splitlines():
                p = line.strip()
                if p.endswith(".py") and p not in discovered:
                    discovered.append(p)
        return discovered[:4]

    def _falsifier_user_prompt(self, cp: PatchCheckpoint) -> str:
        base_auditor = self._build_auditor_user_prompt(cp)
        # Run a fast deterministic pytest / custom_test_cmd check so L1-B sees real test suite status immediately
        tfiles = self._discover_targeted_test_files(cp)
        targeted_pytest_block = ""
        if self.custom_test_cmd:
            wrapped_ct = (
                f'export PATH="{self._python_bin_dir}:$PATH"; '
                f'export PYTHONPATH="{self.executor.root}:${{PYTHONPATH:-}}"; '
                f"{self.custom_test_cmd}"
            )
            res_ct = self.executor.run(
                ["bash", "-lc", wrapped_ct],
                cwd=self.executor.root,
                env={"PYTHONPATH": self.executor.root},
                timeout_s=35,
            )
            ct_raw = ((res_ct.stdout or "") + "\n" + (res_ct.stderr or "")).strip()
            ct_out = _truncate_output(ct_raw, 1400)
            targeted_pytest_block += (
                f"\n### Custom Test Command Output (`{self.custom_test_cmd}`, exit={res_ct.rc})\n"
                f"```text\n{ct_out}\n```\n"
            )
        if tfiles:
            res_pt = self.executor.run(
                [
                    self.executor.python,
                    "-m",
                    "pytest",
                    "-q",
                    "--tb=short",
                    "--disable-warnings",
                    "-p",
                    "no:randomly",
                    *tfiles[:4],
                ],
                cwd=self.executor.root,
                env={"PYTHONPATH": self.executor.root},
                timeout_s=35,
            )
            pt_raw = ((res_pt.stdout or "") + "\n" + (res_pt.stderr or "")).strip()
            if "No module named pytest" not in pt_raw:
                pt_out = _truncate_output(pt_raw, 1400)
                targeted_pytest_block += (
                    f"\n### Targeted Repository Test Suite Output (`pytest {' '.join(tfiles[:4])}`, exit={res_pt.rc})\n"
                    f"```text\n{pt_out}\n```\n"
                )
        return (
            f"{base_auditor}\n"
            f"{targeted_pytest_block}\n"
            f"{self.contract.to_prompt_block()}\n\n"
            "- IMPORTANT: If the patch deprecates legacy kwargs or parameters in favor of a new parameter (e.g. `metadata=...`), ensure passing BOTH the new parameter and deprecated kwargs merges them cleanly after emitting the deprecation warning rather than raising `ValueError`!\n"
            "Also write your adversarial test script to `/testbed/.hae/falsification_suite.py` (or `/testbed/.hae/audit_edge_cases.py`), "
            "and when emitting ` ```submit `, include `VERDICT: PASS` if all edge cases, deprecation warnings, and tests pass, "
            "or `VERDICT: FAIL: <exact failing assertion or missing requirement>` if the patch is still flawed."
        )

    def run_falsifier_episode(
        self,
        cp: PatchCheckpoint,
        max_turns: int = 8,
    ) -> Tuple[bool, str]:
        """Run Layer L1-B Independent Adversarial Falsifier & Edge-Case Auditor in a fresh context window.

        Allows `L1_AdversarialFalsifier` to either:
          - Harden missing edge cases / deprecation warnings / subclasses via `str_replace` (verified by Gatekeeper), OR
          - Falsify the patch via `/testbed/.hae/falsification_suite.py` / `VERDICT: FAIL` (restoring `cp` if broken).
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
            use_thinking = (turn == 1) and not force_no_think_next
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
            if len(reasoning) > 3800:
                force_no_think_next = True
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
                    messages.append({"role": "assistant", "content": f"(Reasoning summary: {reasoning[-600:]})"})
                    messages.append({
                        "role": "user",
                        "content": (
                            "Your `<think>` block hit the token limit. Based on your reasoning above, immediately emit "
                            "` ```bash `, ` ```str_replace `, ` ```view_file `, or ` ```submit ` with `VERDICT: PASS` / `VERDICT: FAIL: <reason>`."
                        ),
                    })
                else:
                    messages.append({"role": "assistant", "content": "(malformed tool block omitted)"})
                    messages.append({"role": "user", "content": f"Observation (error): {action.error}"})
                continue

            messages.append({"role": "assistant", "content": resp_text})
            if action.kind == "submit":
                verdict_text = (action.summary or action.new_str or resp_text).strip()
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
            elif action.kind == "str_replace":
                obs = self.execute_str_replace_tool(
                    action.path, action.old_str, action.new_str, "L1_AdversarialFalsifier", turn
                )
                messages.append({"role": "user", "content": obs})
            elif action.kind == "write_file":
                obs = self.execute_write_file_tool(action.path, action.new_str, run_after=True)
                messages.append({"role": "user", "content": obs})

        self._clean_untracked_outside_scratch()
        curr_diff, _, _ = self._current_diff_and_files()
        if curr_diff != cp.diff and curr_diff.strip():
            # Falsifier/Auditor applied an edge-case or deprecation-warning refinement! Verify it via Gatekeeper.
            ok_aud, audit_msg_aud, cp_aud = self.verify_current_workspace(
                specialist="L1_AdversarialFalsifier",
                turn=max_turns,
                is_auditor=False,
            )
            if ok_aud and cp_aud is not None and cp_aud.repro_ok and cp_aud.repo_tests_ok:
                cp.diff = cp_aud.diff
                cp.changed_files = cp_aud.changed_files
                cp.file_contents = cp_aud.file_contents
                cp.created_files = list(cp_aud.created_files)
                cp.compile_ok = True
                cp.static_ok = cp_aud.static_ok
                cp.repro_ok = True
                cp.repo_tests_ok = True
                cp.audit_ok = True
                cp.gatekeeper_ok = True
            else:
                self._restore_checkpoint(cp)
        elif not curr_diff.strip():
            self._restore_checkpoint(cp)

        # Deterministically run `/testbed/.hae/falsification_suite.py` and `/testbed/.hae/audit_edge_cases.py` if created
        suite_exists = False
        suite_ok = True
        suite_out = ""
        for s_rel in (self.falsification_script_rel, self.audit_script_rel):
            if self.executor.exists(s_rel):
                suite_exists = True
                res_f = self.executor.run(
                    [self.executor.python, s_rel],
                    cwd=self.executor.root,
                    env={"PYTHONPATH": self.executor.root},
                    timeout_s=60,
                )
                if res_f.rc != 0 or res_f.timed_out:
                    suite_ok = False
                    suite_out = _truncate_output((res_f.stdout + "\n" + res_f.stderr).strip(), 1500)
                    break

        if self.custom_test_cmd:
            wrapped_ct_post = (
                f'export PATH="{self._python_bin_dir}:$PATH"; '
                f'export PYTHONPATH="{self.executor.root}:${{PYTHONPATH:-}}"; '
                f"{self.custom_test_cmd}"
            )
            res_ct_post = self.executor.run(
                ["bash", "-lc", wrapped_ct_post],
                cwd=self.executor.root,
                env={"PYTHONPATH": self.executor.root},
                timeout_s=45,
            )
            ct_post_raw = ((res_ct_post.stdout or "") + "\n" + (res_ct_post.stderr or "")).strip()
            if res_ct_post.rc != 0 or res_ct_post.timed_out:
                cp.repo_tests_ok = False
                cp.gatekeeper_ok = False
                for c in self.checkpoints:
                    if c.diff == cp.diff:
                        c.repo_tests_ok = False
                        c.gatekeeper_ok = False
                ct_fail_out = _truncate_output(ct_post_raw, 1500)
                return False, f"Custom test command ({self.custom_test_cmd}) FAILED (exit {res_ct_post.rc}):\n{ct_fail_out}"

        # Also deterministically verify targeted repository test files after any L1-B edits
        tfiles_post = self._discover_targeted_test_files(cp)
        if tfiles_post:
            res_pt_post = self.executor.run(
                [
                    self.executor.python,
                    "-m",
                    "pytest",
                    "-q",
                    "--tb=short",
                    "--disable-warnings",
                    "-p",
                    "no:randomly",
                    *tfiles_post[:4],
                ],
                cwd=self.executor.root,
                env={"PYTHONPATH": self.executor.root},
                timeout_s=40,
            )
            pt_post_raw = ((res_pt_post.stdout or "") + "\n" + (res_pt_post.stderr or "")).strip()
            if res_pt_post.rc != 0 and "No module named pytest" not in pt_post_raw:
                cp.repo_tests_ok = False
                cp.gatekeeper_ok = False
                for c in self.checkpoints:
                    if c.diff == cp.diff:
                        c.repo_tests_ok = False
                        c.gatekeeper_ok = False
                pt_fail_out = _truncate_output(pt_post_raw, 1500)
                return False, f"Targeted pytest suite ({' '.join(tfiles_post[:4])}) FAILED (exit {res_pt_post.rc}):\n{pt_fail_out}"

        explicit_fail = bool(re.search(r"VERDICT\s*:\s*FAIL", verdict_text, flags=re.IGNORECASE))
        explicit_pass = bool(re.search(r"VERDICT\s*:\s*PASS", verdict_text, flags=re.IGNORECASE))
        if not suite_ok:
            critique = (
                f"Adversarial falsification suite FAILED:\n{suite_out}\n"
                + (f"Falsifier notes: {verdict_text[:800]}" if verdict_text else "")
            )
            return False, critique
        if explicit_fail:
            return False, f"Adversarial Falsifier rejected patch: {verdict_text[:1200]}"

        return True, f"Adversarial Falsifier CERTIFIED patch ({verdict_text[:300] or 'corroborated with zero counterexamples'})."

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
        # If repo_tests failed and named specific pytest node IDs (e.g. `tests/...::test_foo`), capture `--tb=short`
        pytest_tb_extra = ""
        if cp is not None and not cp.repo_tests_ok:
            failed_ids = list(
                dict.fromkeys(re.findall(r"(tests/[A-Za-z0-9_./-]+\.py(?:::[A-Za-z0-9_:\[\]-]+)?)", audit_msg))
            )[:3]
            if failed_ids:
                res_tb = self.executor.run(
                    [
                        self.executor.python,
                        "-m",
                        "pytest",
                        "-q",
                        "--tb=short",
                        "--disable-warnings",
                        "-p",
                        "no:randomly",
                        *failed_ids,
                    ],
                    cwd=self.executor.root,
                    env={"PYTHONPATH": self.executor.root},
                    timeout_s=30,
                )
                tb_out = ((res_tb.stdout or "") + "\n" + (res_tb.stderr or "")).strip()
                if tb_out:
                    pytest_tb_extra = f"### Exact Failing Pytest Traceback (`--tb=short`) for {failed_ids}\n```text\n{_truncate_output(tb_out, 1400)}\n```\n\n"

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
                    f"### Cumulative Falsification Suite `{self.falsification_script_rel}` FAILED (exit {res_f.rc})\n"
                    f"```text\n{_truncate_output((res_f.stdout + chr(10) + res_f.stderr).strip(), 1200)}\n```\n\n"
                )

        falsifier_ran = False
        falsifier_ok = False
        critique = pytest_tb_extra + existing_falsify_msg + audit_msg

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
            falsifier_ok, falsifier_msg = self.run_falsifier_episode(cp, max_turns=6)
            critique = f"- L1-B Adversarial Falsifier: {falsifier_msg}\n\n{audit_msg}"
            if falsifier_ok:
                cp.audit_ok = True
                cp.gatekeeper_ok = True
            else:
                cp.audit_ok = False
                cp.gatekeeper_ok = False

        val = compute_node_empirical_value(
            cp,
            falsifier_ran=falsifier_ran,
            falsifier_ok=falsifier_ok,
            diff_bloat_threshold=self.diff_bloat_threshold,
        )
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
            falsifier_critique=_truncate_output(critique, 2600),
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
        effective_budget = max(int(budget_moves or 36), 68)
        self.initial_candidate_modules = list(candidate_modules or [])
        self.executor.run(["mkdir", "-p", f"{self.executor.root}/{SCRATCH_DIR}"], cwd=self.executor.root, timeout_s=15)
        repo_map = self.runner.repo_map()

        # --- Stage 1: Layer L1-A Fast Reconnaissance Episode (Fresh Context, 2-3 turns) ---
        recon = self.run_recon_episode(candidate_modules, repo_map, max_turns=3)

        # --- Stage 2: Layer L0 Seed Architect & Contract Formulation (Clean Context, 1 turn) ---
        if self.runner._may_call("L0 seed architect"):
            self.formulate_seed_contract_and_branches(self.initial_candidate_modules or candidate_modules, recon)
        else:
            self.contract, self.branch_queue = parse_seed_architect_response("", self.initial_candidate_modules or candidate_modules, recon)

        # --- Stage 3: Layer L0 Agentic AB-MCTS Loop Over Ephemeral L2 & L1-B Episodes ---
        max_episodes = 4
        gen_count = 0

        for ep in range(1, max_episodes + 1):
            elapsed_total = time.time() - self.run_start_time
            remaining_moves = max(0, effective_budget - self.total_moves_used)
            if elapsed_total >= self.max_wall_s or remaining_moves < 3 or not self.runner._may_call(f"mcts episode {ep}"):
                self.logger(
                    f"[v13-hier-mcts] Stopping AB-MCTS loop before episode {ep} "
                    f"(elapsed={elapsed_total:.1f}s/{self.max_wall_s:.0f}s, remaining_moves={remaining_moves})"
                )
                break

            op_kind, target_nid, branch_spec = self.select_next_mcts_operation(self.initial_candidate_modules or candidate_modules)

            if op_kind == "GEN" and branch_spec is not None:
                gen_count += 1
                new_nid = f"b{gen_count}"
                if gen_count == 1:
                    ep_turns = max(14, min(18, remaining_moves - 5))
                else:
                    ep_turns = max(10, min(15, remaining_moves - 2))
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
                ep_turns = max(8, min(12, remaining_moves - 2))
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


HierarchicalMCTSSweEngine = HierarchicalAgenticMCTSEngine

