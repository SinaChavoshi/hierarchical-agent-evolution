"""V15 mutation operators: knob mutation, crossover, and LLM-proposed code mutations.

Code mutation follows the AlphaEvolve-style SEARCH/REPLACE protocol: the meta-model sees
a failure digest from the parent's evaluations plus the full source of a few harness
"hot spots", and returns exact-match edit blocks. Each proposal must apply cleanly, pass
the leakage gate, compile, and pass the gate unit tests before it becomes a genome.
"""

from __future__ import annotations

import ast
import json
import os
import random
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from hae.evolution.genome import EVOLVABLE_FILES, HarnessGenome
from hae.evolution.leakage import LeakageGate
from hae.evolution.workspace import WorktreeManager, current_patch, run_gate
from hae.swebench.harness_knobs import KNOB_SPACE, crossover_knobs, mutate_knobs

# (file, qualified function name) pairs that code mutations may target. All are <= ~120 lines.
HOT_SPOTS: List[Tuple[str, str]] = [
    ("hae/swebench/hierarchical_mcts.py", "compute_node_empirical_value"),
    ("hae/swebench/hierarchical_mcts.py", "HierarchicalAgenticMCTSEngine.select_next_mcts_operation"),
    ("hae/swebench/hierarchical_mcts.py", "HierarchicalAgenticMCTSEngine._build_gen_executor_prompt"),
    ("hae/swebench/hierarchical_mcts.py", "HierarchicalAgenticMCTSEngine._build_cont_executor_prompt"),
    ("hae/swebench/hierarchical_mcts.py", "HierarchicalAgenticMCTSEngine._recon_system_prompt"),
    ("hae/swebench/hierarchical_mcts.py", "HierarchicalAgenticMCTSEngine._recon_user_prompt"),
    ("hae/swebench/hierarchical_mcts.py", "HierarchicalAgenticMCTSEngine._falsifier_system_prompt"),
    ("hae/swebench/hierarchical_mcts.py", "HierarchicalAgenticMCTSEngine._falsifier_user_prompt"),
    ("hae/swebench/hierarchical_mcts.py", "HierarchicalAgenticMCTSEngine.formulate_seed_contract_and_branches"),
    ("hae/swebench/hierarchical_mcts.py", "HierarchicalAgenticMCTSEngine.synthesize_move37_counterfactual_branch"),
    ("hae/swebench/hierarchical_mcts.py", "HierarchicalAgenticMCTSEngine._discover_targeted_test_files"),
    ("hae/swebench/hierarchical_mcts.py", "HierarchicalAgenticMCTSEngine._scan_sibling_call_sites"),
    ("hae/swebench/hierarchical_mcts.py", "parse_seed_architect_response"),
    ("hae/swebench/agentic.py", "AgenticSweEngine._system_prompt"),
    ("hae/swebench/agentic.py", "AgenticSweEngine._condense_messages"),
    ("hae/swebench/agentic.py", "AgenticSweEngine._build_primary_user_prompt"),
]

ARCHITECTURE_SUMMARY = """\
The harness is a hierarchical software-repair agent that fixes GitHub issues inside a sandboxed repository:
- L1-A Recon (`run_recon_episode`, prompts `_recon_*`): localizes the fault and writes a failing reproduction script.
- L0 Controller (`formulate_seed_contract_and_branches`, `select_next_mcts_operation`, `compute_node_empirical_value`):
  writes a verification contract, seeds causal branches, and picks GEN (fresh branch from HEAD) or CONT (refine a
  near-miss checkpoint) each episode using AB-MCTS / Thompson sampling.
- L2 Executors (`_build_gen_executor_prompt`, `_build_cont_executor_prompt`, `AgenticSweEngine._system_prompt`): ReAct
  agents that edit code with bash / view_file / str_replace tools and `submit`.
- L1-B Verifier (`_falsifier_*`, `run_falsifier_episode`): deterministic checks, then an adversarial reviewer that tries
  to break the patch with edge-case tests.
A patch is accepted when the deterministic checks and the falsifier both pass. Success is measured by hidden tests."""

SYSTEM_PROMPT = """\
You are a research engineer improving an autonomous software-repair agent harness by editing its Python source.
Your edit will be evaluated by running the harness on held-out GitHub issues from many different repositories.

Hard rules (violations are rejected automatically):
1. The change must be DOMAIN-GENERAL. Never mention a specific repository, project, library, file name from a task,
   issue number, test name, or a task-specific fix. Do not hard-code behavior for particular code patterns of one task.
2. Keep every function signature and return type unchanged. Keep the code importable and the existing unit tests passing.
3. Make one focused improvement (typically 5-60 changed lines). Prefer changes to prompts, search/selection policy,
   verification strategy, or context management that plausibly help on MANY issues.
4. Output ONLY edit blocks in exactly this format (one or more):

<<<<<<< SEARCH path/to/file.py
<exact lines copied from the provided source, including indentation>
=======
<replacement lines>
>>>>>>> REPLACE

Then a final line: RATIONALE: <one sentence on why this helps in general>."""

_BLOCK_RE = re.compile(
    r"<<<<<<< SEARCH[ \t]*(?P<path>[^\n]*)\n(?P<search>.*?)\n=======\n(?P<replace>.*?)\n>>>>>>> REPLACE",
    re.DOTALL,
)


def extract_function_source(source: str, qualname: str) -> Optional[str]:
    """Return the exact source text of `qualname` (`func` or `Class.method`) in `source`."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    parts = qualname.split(".")
    body = tree.body
    node = None
    for i, name in enumerate(parts):
        node = next((n for n in body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                     and n.name == name), None)
        if node is None:
            return None
        body = getattr(node, "body", [])
    if node is None or not hasattr(node, "end_lineno"):
        return None
    lines = source.splitlines()
    start = node.lineno - 1
    if getattr(node, "decorator_list", None):
        start = min(d.lineno for d in node.decorator_list) - 1
    return "\n".join(lines[start:node.end_lineno])


def parse_edit_blocks(text: str) -> Tuple[List[Tuple[str, str, str]], str]:
    blocks = [(m.group("path").strip(), m.group("search"), m.group("replace")) for m in _BLOCK_RE.finditer(text or "")]
    m_r = re.search(r"RATIONALE:\s*(.+)", text or "")
    return blocks, (m_r.group(1).strip()[:300] if m_r else "")


def apply_edit_blocks(worktree: str, blocks: Sequence[Tuple[str, str, str]]) -> Optional[str]:
    """Apply SEARCH/REPLACE blocks in place. Returns an error string or None."""
    if not blocks:
        return "no edit blocks found"
    for path, search, replace in blocks:
        if path not in EVOLVABLE_FILES:
            return f"edit targets non-evolvable file {path!r}"
        full = os.path.join(worktree, path)
        with open(full, "r", encoding="utf-8") as fh:
            src = fh.read()
        count = src.count(search) if search.strip() else 0
        if count != 1:
            return f"SEARCH block for {path} matched {count} times (must match exactly once; copy it verbatim)"
        src = src.replace(search, replace, 1)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(src)
    return None


def failure_digest(genome: HarnessGenome, logs: Dict[str, str], max_items: int = 6) -> str:
    """Summarize the parent's evaluation outcomes without naming tasks or leaking hidden tests."""
    rows = []
    failed = [r for r in genome.results.values() if not r.resolved]
    solved = [r for r in genome.results.values() if r.resolved]
    rows.append(f"Parent resolved {len(solved)}/{len(genome.results)} training issues "
                f"(mean {genome.mean_tokens()/1e3:.0f}k tokens, knobs={json.dumps(genome.knobs, sort_keys=True)}).")
    stops: Dict[str, int] = {}
    for r in failed:
        stops[r.stop or r.status or "unknown"] = stops.get(r.stop or r.status or "unknown", 0) + 1
    if stops:
        rows.append("Stop reasons on FAILED issues: " + ", ".join(f"{k}={v}" for k, v in sorted(stops.items())))
    for i, r in enumerate(failed[:max_items], 1):
        tail = logs.get(r.instance_id, "")
        # Keep only harness trace lines (layer/episode/verdict), never repository paths or test names.
        trace = [ln for ln in tail.splitlines() if ln.startswith(("[v13-hier-mcts]", "[v12-agentic]"))]
        trace = [re.sub(r"`[^`]*`|'[^']*'|\S+\.py\S*", "<redacted>", ln)[:220] for ln in trace[-14:]]
        rows.append(f"\nFailed issue #{i}: moves={r.moves_used}, stop={r.stop}, f2p={r.f2p_passed}/{r.f2p_total}, "
                    f"tokens={r.tokens//1000}k\n  " + "\n  ".join(trace))
    return "\n".join(rows)


@dataclass
class CodeMutationResult:
    patch: str
    rationale: str
    targets: List[str]
    attempts: int


class CodeMutator:
    """Proposes, applies and validates LLM code mutations on a parent genome."""

    def __init__(self, worktrees: WorktreeManager, leakage: LeakageGate,
                 llm: Callable[[str, str], str], logger: Callable[[str], None] = print,
                 max_attempts: int = 3) -> None:
        self.worktrees = worktrees
        self.leakage = leakage
        self.llm = llm
        self.logger = logger
        self.max_attempts = max_attempts

    def propose(self, parent: HarnessGenome, child_id: str, digest: str, rng: random.Random) -> Optional[CodeMutationResult]:
        targets = rng.sample(HOT_SPOTS, k=2)
        feedback = ""
        for attempt in range(1, self.max_attempts + 1):
            child = HarnessGenome(genome_id=child_id, knobs=dict(parent.knobs), patch=parent.patch)
            self.worktrees.remove(child_id)
            path, err = self.worktrees.materialize(child)
            if err:
                self.logger(f"[v15-mutate] {child_id}: cannot materialize parent patch: {err}")
                return None
            sources = []
            for rel, qn in targets:
                with open(os.path.join(path, rel), "r", encoding="utf-8") as fh:
                    src = extract_function_source(fh.read(), qn)
                if src:
                    sources.append(f"### {rel} :: {qn}\n```python\n{src}\n```")
            prompt = (
                f"{ARCHITECTURE_SUMMARY}\n\n## Evaluation feedback from the parent harness\n{digest}\n\n"
                f"## Source you may edit (copy SEARCH text exactly from here)\n" + "\n\n".join(sources)
                + (f"\n\n## Your previous attempt was rejected\n{feedback}\nFix the problem and try again." if feedback else "")
            )
            try:
                reply = self.llm(SYSTEM_PROMPT, prompt)
            except Exception as exc:  # pragma: no cover - network failures
                feedback = f"LLM call failed: {exc}"
                continue
            blocks, rationale = parse_edit_blocks(reply)
            err = apply_edit_blocks(path, blocks)
            if err:
                feedback = err
                self.logger(f"[v15-mutate] {child_id} attempt {attempt}: {err}")
                continue
            patch = current_patch(path)
            if not patch.strip() or patch == parent.patch:
                feedback = "the edit produced no change"
                continue
            leak = self.leakage.check(patch)
            if leak:
                feedback = leak + " Remove all task-specific content."
                self.logger(f"[v15-mutate] {child_id} attempt {attempt}: {leak}")
                continue
            gate = run_gate(path)
            if not gate.ok:
                feedback = f"safety gate failed at {gate.stage}:\n{gate.message[-1500:]}"
                self.logger(f"[v15-mutate] {child_id} attempt {attempt}: gate failed ({gate.stage})")
                continue
            return CodeMutationResult(patch=patch, rationale=rationale,
                                      targets=[f"{r}::{q}" for r, q in targets], attempts=attempt)
        self.worktrees.remove(child_id)
        return None


def crossover_patches(worktrees: WorktreeManager, a: HarnessGenome, b: HarnessGenome, child_id: str,
                      leakage: LeakageGate) -> str:
    """Apply A's patch, then try B's on top. Falls back to A's patch if B conflicts or the gate fails."""
    from hae.evolution.workspace import apply_patch

    child = HarnessGenome(genome_id=child_id, patch=a.patch)
    worktrees.remove(child_id)
    path, err = worktrees.materialize(child)
    if err or not b.patch.strip() or b.patch == a.patch:
        worktrees.remove(child_id)
        return a.patch
    ok, _ = apply_patch(path, b.patch)
    merged = current_patch(path) if ok else a.patch
    if ok and (leakage.check(merged) or not run_gate(path).ok):
        merged = a.patch
    worktrees.remove(child_id)
    return merged


__all__ = [
    "HOT_SPOTS", "CodeMutator", "CodeMutationResult", "crossover_patches", "failure_digest",
    "extract_function_source", "parse_edit_blocks", "apply_edit_blocks", "mutate_knobs", "crossover_knobs",
    "KNOB_SPACE",
]
