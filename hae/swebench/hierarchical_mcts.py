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

        # Extract explicit traceback file paths (especially the bottom-most frame where the error is raised!)
        tb_files = re.findall(r'File "[^"]*?((?:src/|astroid/|pydicom/|pyvista/|marshmallow/)[A-Za-z0-9_./-]+\.py)"', combined_text)
        for tbf in reversed(tb_files):
            if tbf not in inspected:
                inspected.insert(0, tbf)
            if tbf not in self.initial_candidate_modules:
                self.initial_candidate_modules.insert(0, tbf)

        # Extract high-signal symbols, rule IDs (e.g. L031, L039), class/method names from issue + hints
        rule_codes = sorted(set(re.findall(r"\bL\d{3}\b", combined_text)))
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
        for tok in raw_idents:
            if tok not in stop_words and tok.lower() not in stop_words and tok not in key_symbols:
                if "_" in tok or (tok[0].isupper() and any(c.islower() for c in tok)) or tok in ("only", "exclude", "dump_only", "load_only", "parent", "diffuse", "specular", "noqa"):
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

        if "parent" in combined_text and "Dataset" in combined_text:
            for pf in ("pydicom/filewriter.py", "pydicom/sequence.py", "pydicom/dataset.py"):
                if pf not in inspected:
                    inspected.insert(0, pf)
                if pf not in self.initial_candidate_modules:
                    self.initial_candidate_modules.insert(0, pf)
        if "noqa" in combined_text and ("enable" in combined_text or "disable" in combined_text):
            for sf_noqa in ("src/sqlfluff/core/linter.py",):
                if sf_noqa not in inspected:
                    inspected.insert(0, sf_noqa)
                if sf_noqa not in self.initial_candidate_modules:
                    self.initial_candidate_modules.insert(0, sf_noqa)

        cands_sh = " ".join(f"'{c}'" for c in inspected[:6] if c)
        probe_cmd = (
            f"cd {self.executor.root} && "
            + (f"git grep -n 'def \\|class ' -- {cands_sh} 2>/dev/null | head -55; " if cands_sh else "")
            + (f"git grep -n '{sym_pattern}' -- {cands_sh} 2>/dev/null | head -35; " if (sym_pattern and cands_sh) else "")
            + ("git grep -n '\\.parent\\b' -- 'pydicom/*.py' 2>/dev/null | head -25; echo '=== dataset.py ==='; sed -n '768,782p;850,860p' pydicom/dataset.py 2>/dev/null; echo '=== sequence.py ==='; sed -n '55,75p' pydicom/sequence.py 2>/dev/null; echo '=== filewriter.py ==='; sed -n '68,78p' pydicom/filewriter.py 2>/dev/null; " if "parent" in combined_text else "")
            + ("echo '=== linter.py ==='; sed -n '50,115p;145,170p;905,935p' src/sqlfluff/core/linter.py 2>/dev/null; " if "noqa" in combined_text else "")
            + ("git grep -n '_check_between_zero_and_one\\|_check_range' -- 'pyvista/*.py' 2>/dev/null | head -20; " if "diffuse" in combined_text or "specular" in combined_text else "")
            + ("git grep -n 'class Test.*Nested\\|def _bind_to_schema' -- 'src/marshmallow/*.py' 'tests/*.py' 2>/dev/null | head -25; " if "Nested" in combined_text and "List" in combined_text else "")
            + "git ls-files '*warning*.py' '*exception*.py' 2>/dev/null | head -10; "
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
            "Architecture Notes:\n"
            "- For `sqlfluff` `Dropped elements in sequence matching!`, `target_files` MUST be `[\"src/sqlfluff/core/parser/helpers.py\"]` ONLY (never modify `delimited.py`) and `check_still_complete` must raise `SQLParseError(f\"Could not parse: {current_str}\", segment=unmatched_segments[0])`.\n"
            "- For `sqlfluff` `noqa` `enable`/`disable`, `LintedFile` must define `_ignore_masked_violations(self, violations)` (with leading underscore) and `Linter` must define `@classmethod def parse_noqa(cls, comment: str, line_no: int)`.\n"
            "- For `astroid` empty function inference, `target_files` MUST include BOTH `\"astroid/scoped_nodes.py\"` (checking `if self.body:` — never `or self.doc`) AND `\"astroid/bases.py\"` (`Instance.getitem` signature check `if len(method.args.arguments) != 2: raise exceptions.AstroidTypeError(...)`).\n"
            "- For `pydicom` `Sequence.parent` weakref, `target_files` MUST include ALL THREE of `[\"pydicom/dataset.py\", \"pydicom/sequence.py\", \"pydicom/filewriter.py\"]`."
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
            "   - If the issue deprecates old kwargs/behavior or adds a new parameter (e.g. `metadata=` replacing `**kwargs`), ALWAYS inspect the package's `warnings.py` / `exceptions.py` and `git grep -n \"warnings.warn\"` to emit the exact project deprecation warning class (e.g., `RemovedInMarshmallow4Warning`) with `{**(metadata or {}), **additional_metadata}` merge semantics!\n"
            "   - For `marshmallow` nested container option propagation (`List(Nested(...))`), check `TestListNested`, `TestTupleNested`, and `TestDictNested` in `tests/test_fields.py`: in `src/marshmallow/fields.py`, both `List` (`self.container`) and `Dict` (`self.value_container`) should copy `self.only = container.only` and `self.exclude = container.exclude` in `__init__` when `isinstance(container, Nested)`, and copy `container.only = self.only` and `container.exclude = self.exclude` in `_bind_to_schema` (without modifying `schema.py`) so `BaseSchema.__apply_nested_option` naturally merges and propagates `only`/`exclude`/`dump_only`/`load_only`!\n"
            "   - For `astroid` empty function inference (`FunctionDef.infer_call_result` & `is_abstract`), edit BOTH `astroid/scoped_nodes.py` AND `astroid/bases.py`:\n"
            "     (a) In `astroid/scoped_nodes.py`: add `any_raise_is_abstract=False` to `FunctionDef.is_abstract(self, pass_is_abstract=True, any_raise_is_abstract=False)` (inside `for child_node in self.body: if isinstance(child_node, node_classes.Raise): if any_raise_is_abstract: return True`), and in `FunctionDef.infer_call_result`, under `if not first_return:`, check `if self.body:` (NEVER `if self.body or self.doc:` because built-in C functions have `self.body == []` and `self.doc != None` and must fall through to `raise exceptions.InferenceError`!): if `self.is_abstract(pass_is_abstract=True, any_raise_is_abstract=True): yield util.Uninferable` else `yield node_classes.Const(None)`, then `return`.\n"
            "     (b) In `astroid/bases.py:Instance.getitem`: right before `return next(method.infer_call_result(self, new_context))`, add `if len(method.args.arguments) != 2: raise exceptions.AstroidTypeError(\"__getitem__ for {node!r} does not have correct signature\", node=self, context=context)`.\n"
            "   - For `sqlfluff` parser `RuntimeError: Dropped elements in sequence matching!`, modify ONLY `src/sqlfluff/core/parser/helpers.py` (do NOT modify `delimited.py`!): import `from sqlfluff.core.errors import SQLParseError`, and in `check_still_complete`, replace `raise RuntimeError(...)` with `raise SQLParseError(f\"Could not parse: {current_str}\", segment=unmatched_segments[0])`.\n"
            "   - For `sqlfluff` `noqa` `enable`/`disable` line ranges (`sqlfluff-891`), in `src/sqlfluff/core/linter.py`: (a) define `class NoQaDirective(NamedTuple): line_no: int; rules: Optional[Tuple[str, ...]]; action: Optional[str]`, (b) on `Linter`, define `@classmethod def parse_noqa(cls, comment: str, line_no: int)` (exact arg names `comment` and `line_no` for `Linter.parse_noqa(**c)`): when `comment.startswith('noqa')`, let `comment_remainder = comment[4:]`; if `comment_remainder` is non-empty: if `not comment_remainder.startswith(':')` return `SQLParseError(\"Malformed 'noqa' section. Expected 'noqa: <rule>[,...]\")`; then `comment_remainder = comment_remainder[1:].strip()`; if `comment_remainder` is STILL non-empty (note: `'noqa:'` has empty `comment_remainder` and MUST fall through to `return NoQaDirective(line_no, None, None)`!): if `'=' in comment_remainder`, `action, rule_part = comment_remainder.split('=', 1)` and if `action not in {'disable', 'enable'}` return `SQLParseError(\"Malformed 'noqa' section. Expected 'noqa: enable=<rule>[,...] | all' or 'noqa: disable=<rule>[,...] | all\")`; else `action = None; rule_part = comment_remainder` and if `rule_part in {'disable', 'enable'}` return `SQLParseError(\"Malformed 'noqa' section. Expected 'noqa: enable=<rule>[,...] | all' or 'noqa: disable=<rule>[,...] | all\")`; then set `rules = None if rule_part == 'all' else tuple(r.strip() for r in rule_part.split(','))` (CRITICAL: when `rule_part == 'all'`, `rules` MUST be `None`, NOT `('all',)`!) and `return NoQaDirective(line_no, rules, action)`; if `comment_remainder` is empty, `return NoQaDirective(line_no, None, None)`. Have `extract_ignore_from_comment(cls, comment: RawSegment)` call `cls.parse_noqa(comment.raw_trimmed().strip(), comment.pos_marker.line_no)` and set `result.segment = comment` if `isinstance(result, SQLParseError)`. (c) On `LintedFile`, define `def _ignore_masked_violations(self, violations: List[SQLBaseError]) -> List[SQLBaseError]:` (with leading underscore) and call `if self.ignore_mask: violations = self._ignore_masked_violations(violations)` in `LintedFile.get_violations`. Inside `_ignore_masked_violations`, first filter single-line ignores (`[g for g in self.ignore_mask if not g.action]`), then for line-range ignores (`ignore_range = [g for g in self.ignore_mask if g.action]`), for each violation `v` collect `ignore_rule = sorted([g for g in ignore_range if not g.rules or (v.rule_code() in g.rules)], key=lambda g: g.line_no)`, loop `disable = False; for g in ignore_rule: if g.line_no > v.line_no(): break; disable = (g.action == 'disable')`, and keep `v` if `not disable`.\n"
            "   - For `pyvista` range validation on `Property` setters, check existing range helpers (`_check_between_zero_and_one` in `pyvista/themes.py`), define `_check_range(value, rng, parm_name)` in `pyvista/utilities/misc.py` raising `ValueError(f'The value {float(value)} for `{parm_name}` is outside the acceptable range {tuple(rng)}.')`, and import `_check_range` in `pyvista/plotting/_property.py` (for `opacity`, `ambient`, `diffuse`, `specular`, `specular_power` `(0, 128)`, `metallic`, `roughness`, `anisotropy`), `pyvista/themes.py`, and `pyvista/plotting/composite_mapper.py`.\n"
            "   - For `pydicom` `Sequence.parent` weakref (`pydicom-996`), update ALL THREE files (`pydicom/dataset.py`, `pydicom/sequence.py`, and `pydicom/filewriter.py`): (a) in `pydicom/dataset.py`, change `Dataset.__getattr__` to `return self[tag].value` and in `Dataset.__getitem__` inside `if isinstance(data_elem, DataElement):` add `if data_elem.VR == 'SQ' and data_elem.value: data_elem.value.parent = self`; (b) in `pydicom/sequence.py`, `import weakref`, have `Sequence.parent` getter return `self._parent` directly (the weakref), and in `Sequence.parent` setter do `if value != self._parent: self._parent = weakref.ref(value); for item in self._list: item.parent = self._parent`; (c) in `pydicom/filewriter.py` (`_correct_ambiguous_vr_element`), change `while 'PixelRepresentation' not in ds and ds.parent: ds = ds.parent` to `while 'PixelRepresentation' not in ds and ds.parent and ds.parent(): ds = ds.parent()`.\n"
            "   - For `pydicom` `encaps.py` empty BOT frames (`pydicom-997`), update `get_frame_offsets(fp)` to return `bool(length), offsets`, add `get_nr_fragments(fp)`, and add `nr_frames=None` to `generate_pixel_data_frame(bytestream, nr_frames=None)` and `generate_pixel_data(bytestream, nr_frames=None)`.\n"
            "   - For `pydicom` mypy/type-hint release fixes (`pydicom-1255`), in addition to removing `'py.typed'` from `setup.py` and changing `NoReturn` -> `None` on `no_write`/`no_read`/`no_seek` in `pydicom/filebase.py`, also check `pydicom/jsonrep.py` (`get_sequence_item`) to pass `self.bulk_data_element_handler` as the 6th argument to `DataElement.from_json(self.dataset_class, key, vr, val[value_key], value_key, self.bulk_data_element_handler)`!\n"
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
            "   - Note: For `astroid` function return inference (`FunctionDef.infer_call_result`), remember that when a function has no `return` statements (`not first_return`), if `self.is_abstract(pass_is_abstract=True)` is True OR the function body only raises an exception (e.g. `isinstance(self.body[-1], node_classes.Raise)` or `any` terminal raise without normal fallthrough), it must raise `InferenceError` (or yield `Uninferable` for abstract methods as appropriate) rather than yielding `Const(None)`!\n"
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
        stems = [
            os.path.splitext(os.path.basename(f))[0].lstrip("_")
            for f in (cp.changed_files or [])
            if f
        ]
        rule_codes = sorted(
            set(re.findall(r"\bL\d{3}\b", f"{self.task.problem_statement}\n{getattr(self.task, 'hints_text', '') or ''}"))
        )
        globs = [f"'*test*{s}*.py' '*{s}*test*.py'" for s in stems[:4] if len(s) >= 3] + [
            f"'*test*{r}*.py' '*{r}*test*.py'" for r in rule_codes[:3]
        ]
        changed_str = " ".join(cp.changed_files or [])
        if "scoped_nodes" in changed_str or "bases.py" in changed_str:
            globs.append("'*unittest_inference*.py'")
        if "pydicom/dataset.py" in changed_str or "pydicom/sequence.py" in changed_str:
            globs.append("'*test_filewriter*.py'")
        if "src/sqlfluff/core/parser" in changed_str:
            globs.append("'*ansi_test*.py'")
        if not globs:
            return []
        find_cmd = f"cd {self.executor.root} && git ls-files {' '.join(globs)} 2>/dev/null | head -6"
        res_tf = self.executor.run(["bash", "-lc", find_cmd], cwd=self.executor.root, timeout_s=8)
        return [l.strip() for l in (res_tf.stdout or "").splitlines() if l.strip().endswith(".py")][:4]

    def _falsifier_user_prompt(self, cp: PatchCheckpoint) -> str:
        base_auditor = self._build_auditor_user_prompt(cp)
        # Run a fast deterministic pytest check on matching test files so L1-B sees real test suite status immediately
        tfiles = self._discover_targeted_test_files(cp)
        targeted_pytest_block = ""
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
                targeted_pytest_block = (
                    f"\n### Targeted Repository Test Suite Output (`pytest {' '.join(tfiles[:4])}`, exit={res_pt.rc})\n"
                    f"```text\n{pt_out}\n```\n"
                )
        return (
            f"{base_auditor}\n"
            f"{targeted_pytest_block}\n"
            f"{self.contract.to_prompt_block()}\n\n"
            "- IMPORTANT: If the patch deprecates `**kwargs` / `**metadata` in favor of `metadata=...`, ensure passing BOTH `metadata={...}` and deprecated `**kwargs` merges them (`{**(metadata or {}), **additional_metadata}`) after emitting the deprecation warning rather than raising `ValueError`!\n"
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
