#!/usr/bin/env python3
"""Live End-to-End Multi-Stage `L0-Macro` Milestone DAG & Git Commit Ratchet Evaluation.

Executes a 4-Milestone Compositional System Build (`MiniLang` expression & scripting
engine) on the live GKE `reflection/Beam-1.0-501B-A23B-NVFP4` (`+ DFlash2`) cluster:
  - `m1_lexer`: Tokenizer (`Token`, `TokenType`, `tokenize(src)`, `LexError` with line/col)
  - `m2_parser`: Recursive-descent parser (`ast_nodes.py`, `parse(src)`, `ParseError`)
  - `m3_evaluator`: Lexical-scope `Environment`, `evaluate(src, env=None)`, closures, recursion, `EvalError`
  - `m4_bytecode_vm`: Stack-based bytecode compiler (`compile_expr`), constant-folding pass, and `VirtualMachine.run()`

Verifies all `L0-Macro` ratchet guarantees on live infrastructure:
  1. Topological DAG scheduling (`m1_lexer -> m2_parser -> m3_evaluator -> m4_bytecode_vm`)
  2. Bounded active milestone deltas (`git diff HEAD_{k-1} < 500` LoC per milestone even as total repo size grows)
  3. Permanent Git commit ratcheting (`hae(ratchet): certify milestone <id>`) & Git tags (`hae-milestone-<id>`)
  4. Falsification suite archiving (`.hae/ratchet_suites/falsify_<id>.py`)
  5. Monotonic cumulative regression gating (`M_1`, `M_1+M_2`, `M_1..M_3`, `M_1..M_4`)
  6. Persistent `.hae/ratchet_state.json` checkpointing
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from hae.hierarchical import (
    GitCommitRatchet,
    HierarchicalMCTSSweEngine,
    MacroMilestoneRatchetEngine,
    MilestoneDAG,
    MilestoneSpec,
    PytestVerifierPlugin,
)
from hae.infra.llm import call_llm, detect_llm_provider
from hae.swebench.executor import LocalExecutor, SCRATCH_DIR


def verify_vllm_endpoint(vllm_url: str, vllm_model: str) -> Dict[str, Any]:
    """Verify live vLLM endpoint connectivity and model availability via /models."""
    models_url = f"{vllm_url.rstrip('/')}/models"
    req = urllib.request.Request(models_url, method="GET")
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    model_ids = [m.get("id", "") for m in data.get("data", [])]
    return {
        "endpoint": vllm_url,
        "requested_model": vllm_model,
        "available_models": model_ids,
        "detected_provider": detect_llm_provider(),
        "hierarchical_micro_engine": HierarchicalMCTSSweEngine.__name__,
    }


def init_minilang_git_workspace(workspace_dir: Path, clean: bool = True) -> LocalExecutor:
    """Initialize a clean Git repository for the 4-Milestone MiniLang build."""
    if clean and workspace_dir.exists():
        shutil.rmtree(workspace_dir)
    workspace_dir.mkdir(parents=True, exist_ok=True)

    executor = LocalExecutor(root=str(workspace_dir), python=sys.executable)
    if not (workspace_dir / ".git").exists():
        executor.git(["init"])
        executor.git(["config", "user.name", "HAE Ratchet Eval"])
        executor.git(["config", "user.email", "hae-eval@local"])
        executor.write_file(
            ".gitignore",
            ".hae/\n__pycache__/\n*.pyc\n.pytest_cache/\n",
        )
        executor.write_file(
            "README.md",
            "# MiniLang Expression & Scripting Engine\n\n"
            "Compositional 4-milestone build managed by `MacroMilestoneRatchetEngine`.\n",
        )
        executor.git(["add", ".gitignore", "README.md"])
        executor.git(["commit", "-m", "chore: initial repository scaffold"])
    return executor


def build_minilang_milestone_dag(
    budget_moves: int = 18,
    max_diff_lines: int = 500,
) -> MilestoneDAG:
    """Construct the 4-Milestone Compositional System Build DAG (`MiniLang`)."""
    m1 = MilestoneSpec(
        milestone_id="m1_lexer",
        title="MiniLang Tokenizer & Lexical Analyzer",
        objective=(
            "Create `minilang/__init__.py`, `minilang/lexer.py`, and `tests/test_m1_lexer.py` "
            "(keep total milestone diff compact, under 350 lines).\n\n"
            "Requirements for `minilang/lexer.py`:\n"
            "1. `TokenType(Enum)` with members: `INT`, `FLOAT`, `STRING`, `IDENT`, "
            "`LET`, `FN`, `IF`, `ELSE`, `RETURN`, "
            "`PLUS`, `MINUS`, `STAR`, `SLASH`, "
            "`EQ` (`==`), `NEQ` (`!=`), `LT` (`<`), `GT` (`>`), `LE` (`<=`), `GE` (`>=`), `ASSIGN` (`=`), "
            "`LPAREN` (`(`), `RPAREN` (`)`), `LBRACE` (`{`), `RBRACE` (`}`), `COMMA` (`,`), `SEMI` (`;`), `EOF`.\n"
            "2. `@dataclass class Token`: fields `type: TokenType`, `value: Any`, `line: int = 1`, `col: int = 1`.\n"
            "3. `class LexError(Exception)`: stores `message: str`, `line: int = 1`, `col: int = 1` and includes "
            "`line` and `col` in its string message.\n"
            "4. `tokenize(src: str) -> List[Token]`:\n"
            "   - Tokenizes integer literals (`TokenType.INT`, `int` value) and float literals (`TokenType.FLOAT`, `float` value).\n"
            "   - Tokenizes double- or single-quoted string literals (`TokenType.STRING`, unescaped `str` value); "
            "raises `LexError` with line/col on unterminated strings.\n"
            "   - Tokenizes identifiers (`TokenType.IDENT`) and keywords (`let`->`LET`, `fn`->`FN`, `if`->`IF`, "
            "`else`->`ELSE`, `return`->`RETURN`).\n"
            "   - Tokenizes arithmetic (`+`, `-`, `*`, `/`), comparison (`==`, `!=`, `<`, `>`, `<=`, `>=`), "
            "assignment (`=`), and punctuation (`(`, `)`, `{`, `}`, `,`, `;`).\n"
            "   - Skips `#` comments until end of line and tracks 1-based `line` and `col` for every token.\n"
            "   - Appends a trailing `Token(TokenType.EOF, None, line, col)` and raises `LexError` on unknown characters.\n"
            "5. Export `Token`, `TokenType`, `LexError`, `tokenize` from `minilang/__init__.py`.\n"
            "6. Write unit tests in `tests/test_m1_lexer.py` covering integers, floats, strings, keywords, "
            "identifiers, operators, `#` comments, line/col positions, and `LexError` cases."
        ),
        depends_on=[],
        target_files=["minilang/__init__.py", "minilang/lexer.py", "tests/test_m1_lexer.py"],
        acceptance_cmd="pytest -q tests/test_m1_lexer.py",
        max_diff_lines=max_diff_lines,
        budget_moves=budget_moves,
    )

    m2 = MilestoneSpec(
        milestone_id="m2_parser",
        title="MiniLang AST Nodes & Recursive-Descent Parser",
        objective=(
            "Create `minilang/ast_nodes.py`, `minilang/parser.py`, and `tests/test_m2_parser.py` "
            "(and optionally export in `minilang/__init__.py`), building on `minilang/lexer.py` from `m1_lexer`. "
            "Keep the milestone delta compact (under 400 lines).\n\n"
            "Requirements for `minilang/ast_nodes.py`:\n"
            "Define clean `@dataclass` AST nodes:\n"
            "- `NumberLiteral(value: Union[int, float])`, `StringLiteral(value: str)` (and alias `Literal = NumberLiteral`)\n"
            "- `Identifier(name: str)`\n"
            "- `UnaryOp(op: str, operand: Any)` (where `op` is `'-'` or `'+'`)\n"
            "- `BinaryOp(op: str, left: Any, right: Any)` (where `op` is `'+'`, `'-'`, `'*'`, `'/'`, `'=='`, `'!='`, `'<'`, `'>'`, `'<='`, `'>='`)\n"
            "- `LetStmt(name: str, expr: Any)`\n"
            "- `ReturnStmt(expr: Any)`\n"
            "- `Block(statements: List[Any])`\n"
            "- `IfExpr(cond: Any, then_branch: Any, else_branch: Optional[Any] = None)`\n"
            "- `FnDef(name: str, params: List[str], body: Any)`\n"
            "- `CallExpr(callee: str, args: List[Any])`\n"
            "- `Program(statements: List[Any])`\n\n"
            "Requirements for `minilang/parser.py`:\n"
            "- `class ParseError(Exception)`\n"
            "- `parse(src: str) -> Program`: recursive-descent parser using `tokenize(src)` from `minilang.lexer`:\n"
            "  - Operator precedence (highest to lowest):\n"
            "    1. Primary: `INT`/`FLOAT` -> `NumberLiteral`, `STRING` -> `StringLiteral`, `IDENT` -> `Identifier` or `CallExpr(name, args)` when followed by `(`, parenthesized `(expr)`, and `if` expressions.\n"
            "    2. Unary: `-x`, `+x` -> `UnaryOp`.\n"
            "    3. Multiplicative: `*`, `/` (left-associative `BinaryOp`).\n"
            "    4. Additive: `+`, `-` (left-associative `BinaryOp`).\n"
            "    5. Comparison: `==`, `!=`, `<`, `>`, `<=`, `>=` (left-associative `BinaryOp`).\n"
            "  - Statements & expressions (optional `;` between statements):\n"
            "    - `let <ident> = <expr>` -> `LetStmt`\n"
            "    - `fn <ident>(<p1>, <p2>, ...) { <stmts> }` (and `let <ident> = fn(<p1>, ...) { <stmts> }`) -> `FnDef`\n"
            "    - `return <expr>` -> `ReturnStmt`\n"
            "    - `if <cond> { <stmts> } else { <stmts> }` (supporting optional parentheses around `<cond>`, optional `else` or `else if`, and either `{ ... }` blocks or single expressions) -> `IfExpr`\n"
            "    - Expression statements.\n"
            "- Write unit tests in `tests/test_m2_parser.py` verifying precedence (`* /` over `+ -` over comparisons), "
            "unary `-`, parentheses, `let`, `if/else`, `fn` & calls, and `ParseError`."
        ),
        depends_on=["m1_lexer"],
        target_files=[
            "minilang/ast_nodes.py",
            "minilang/parser.py",
            "minilang/__init__.py",
            "tests/test_m2_parser.py",
        ],
        acceptance_cmd="pytest -q tests/test_m2_parser.py",
        max_diff_lines=max_diff_lines,
        budget_moves=budget_moves,
    )

    m3 = MilestoneSpec(
        milestone_id="m3_evaluator",
        title="MiniLang Lexical Environment, Closures & Tree-Walking Evaluator",
        objective=(
            "Create `minilang/evaluator.py` and `tests/test_m3_evaluator.py` (and export in `minilang/__init__.py` if helpful), "
            "building on `minilang/ast_nodes.py` and `minilang/parser.py` from `m2_parser`. "
            "Keep the milestone delta compact (under 400 lines).\n\n"
            "Requirements for `minilang/evaluator.py`:\n"
            "1. `class EvalError(Exception)`: raised on division-by-zero (`/` by `0` or `0.0`), undefined variable lookup, "
            "undefined function call, or arity mismatch.\n"
            "2. `class Environment`:\n"
            "   - `__init__(self, parent: Optional[Environment] = None, values: Optional[Dict[str, Any]] = None)`\n"
            "   - `define(self, name: str, value: Any) -> Any` (or `set`): binds `name` in the current scope.\n"
            "   - `get(self, name: str) -> Any`: resolves `name` in current or ancestor `parent` scopes, raising `EvalError` if undefined.\n"
            "3. `evaluate(src_or_node: Union[str, Any], env: Optional[Environment] = None) -> Any`:\n"
            "   - If given a `str`, parses it via `parse(src_or_node)`.\n"
            "   - Evaluates `Program` / `Block` statements sequentially, returning the last expression value or early `return` value.\n"
            "   - Evaluates `NumberLiteral`, `StringLiteral`, `Identifier`, `UnaryOp`, `BinaryOp` "
            "(`+`, `-`, `*`, `/`, `==`, `!=`, `<`, `>`, `<=`, `>=`; raises `EvalError` on division by 0).\n"
            "   - Evaluates `LetStmt`, `IfExpr` (truthy condition executes `then_branch`, falsy executes `else_branch` or returns `None`/`0`), "
            "`FnDef` (captures defining `Environment` as a lexical closure), `CallExpr` (creates child `Environment(parent=closure_env)`, "
            "binds parameters, supports recursion and closures), and `ReturnStmt`.\n"
            "4. Write unit tests in `tests/test_m3_evaluator.py` covering arithmetic, `let` bindings, lexical closures, "
            "recursive functions (e.g. factorial and fibonacci), `if/else`, and `EvalError` on division by zero and undefined variables."
        ),
        depends_on=["m2_parser"],
        target_files=[
            "minilang/evaluator.py",
            "minilang/__init__.py",
            "tests/test_m3_evaluator.py",
        ],
        acceptance_cmd="pytest -q tests/test_m3_evaluator.py",
        max_diff_lines=max_diff_lines,
        budget_moves=budget_moves,
    )

    m4 = MilestoneSpec(
        milestone_id="m4_bytecode_vm",
        title="MiniLang Constant-Folding Bytecode Compiler & Stack Virtual Machine",
        objective=(
            "Create `minilang/bytecode.py` and `tests/test_m4_bytecode.py` (and export in `minilang/__init__.py` if helpful), "
            "building on `m1_lexer`, `m2_parser`, and `m3_evaluator`. Keep the milestone delta compact (under 450 lines).\n\n"
            "Requirements for `minilang/bytecode.py`:\n"
            "1. `OpCode(Enum)` (or string/Enum opcodes) and `@dataclass class Instruction(op: Any, arg: Any = None)`.\n"
            "2. `@dataclass class BytecodeChunk(instructions: List[Instruction], constants: List[Any] = ...)`.\n"
            "3. Constant-folding pass (`fold_constants(node)` called by `compile_expr(src_or_ast, fold: bool = True)`):\n"
            "   - Folds constant `UnaryOp` and `BinaryOp` subtrees at compile time (e.g., `2 + 3 * 4` folds to `NumberLiteral(14)` "
            "and emits a single `LOAD_CONST` instruction instead of multiple arithmetic instructions).\n"
            "   - Do NOT fold division by zero at compile time so `VirtualMachine.run()` raises `EvalError` at runtime matching `evaluate()`.\n"
            "4. `compile_expr(src_or_ast: Union[str, Any], fold: bool = True) -> BytecodeChunk`:\n"
            "   - Compiles `MiniLang` source or AST into stack-based bytecode instructions (`LOAD_CONST`, `LOAD_VAR`, `STORE_VAR`, "
            "`UNARY_NEG`, `BINARY_ADD`, `BINARY_SUB`, `BINARY_MUL`, `BINARY_DIV`, `CMP_EQ`, `CMP_NEQ`, `CMP_LT`, `CMP_GT`, "
            "`CMP_LE`, `CMP_GE`, `JUMP_IF_FALSE`, `JUMP`, `MAKE_FN`, `CALL_FN`, `RETURN`, `HALT`).\n"
            "5. `class VirtualMachine`:\n"
            "   - `run(self, chunk_or_src: Union[BytecodeChunk, str], env: Optional[Environment] = None) -> Any`: executes the compiled "
            "`BytecodeChunk` (compiling first if passed a `str`) on an explicit operand stack + call frames / lexical `Environment`, "
            "raising `EvalError` on division by zero or undefined variables, and matching `evaluate(src)` across arithmetic, variables, "
            "`if/else` conditionals, closures, and recursive functions.\n"
            "6. Write unit tests in `tests/test_m4_bytecode.py` verifying:\n"
            "   - Constant folding reduces instruction count on constant expressions (`compile_expr('2 + 3 * 4', fold=True)` has fewer instructions than `fold=False`),\n"
            "   - `VirtualMachine().run(compile_expr(src)) == evaluate(src)` across arithmetic, variables, `if/else`, closures, and recursive functions,\n"
            "   - `EvalError` is raised on division by zero and undefined variables."
        ),
        depends_on=["m3_evaluator"],
        target_files=[
            "minilang/bytecode.py",
            "minilang/__init__.py",
            "tests/test_m4_bytecode.py",
        ],
        acceptance_cmd="pytest -q tests/test_m4_bytecode.py",
        max_diff_lines=max_diff_lines,
        budget_moves=budget_moves,
    )

    return MilestoneDAG(
        milestones=[m1, m2, m3, m4],
        system_objective=(
            "Build the 4-Milestone MiniLang Expression & Scripting Engine "
            "(Lexer -> Recursive-Descent Parser -> Lexical Closure Evaluator -> Constant-Folding Bytecode VM) "
            "with persistent Git commit ratcheting and cumulative regression verification."
        ),
    )


def collect_git_ratchet_telemetry(executor: LocalExecutor, dag: MilestoneDAG) -> Dict[str, Any]:
    """Inspect the Git commit history, tags, diffs, and file line counts in the workspace."""
    log_res = executor.git(["log", "--reverse", "--pretty=format:%H|%s"])
    commits: List[Dict[str, str]] = []
    if log_res.ok:
        for line in log_res.stdout.splitlines():
            if "|" in line:
                sha, subject = line.split("|", 1)
                commits.append({"sha": sha.strip(), "subject": subject.strip()})

    tag_res = executor.git(["tag", "-l", "hae-milestone-*"])
    tags: Dict[str, str] = {}
    if tag_res.ok:
        for tname in [l.strip() for l in tag_res.stdout.splitlines() if l.strip()]:
            rev = executor.git(["rev-parse", tname])
            if rev.ok:
                tags[tname] = rev.stdout.strip()

    milestone_git_stats: Dict[str, Dict[str, Any]] = {}
    for mid in dag.topological_order():
        m = dag.milestones[mid]
        sha = m.certified_commit_sha
        if not sha:
            continue
        parent_res = executor.git(["rev-parse", f"{sha}^"])
        parent_sha = parent_res.stdout.strip() if parent_res.ok else ""
        diff_res = executor.git(["diff", parent_sha, sha]) if parent_sha else executor.git(["show", sha])
        diff_text = diff_res.stdout if diff_res.ok else ""
        added = sum(1 for l in diff_text.splitlines() if l.startswith("+") and not l.startswith("+++"))
        removed = sum(1 for l in diff_text.splitlines() if l.startswith("-") and not l.startswith("---"))

        # Count total repository LoC at this commit (`git ls-tree -r <sha> --name-only`)
        tree_res = executor.git(["ls-tree", "-r", "--name-only", sha])
        repo_loc_at_commit = 0
        tracked_files_at_commit: List[str] = []
        if tree_res.ok:
            for rel in [l.strip() for l in tree_res.stdout.splitlines() if l.strip()]:
                if rel.startswith(SCRATCH_DIR + "/"):
                    continue
                tracked_files_at_commit.append(rel)
                blob_res = executor.git(["show", f"{sha}:{rel}"])
                if blob_res.ok:
                    repo_loc_at_commit += len(blob_res.stdout.splitlines())

        milestone_git_stats[mid] = {
            "base_commit_sha": parent_sha,
            "certified_commit_sha": sha,
            "git_tag": f"hae-milestone-{mid}",
            "tag_matches_sha": tags.get(f"hae-milestone-{mid}") == sha,
            "delta_added_lines": added,
            "delta_removed_lines": removed,
            "delta_total_diff_lines": len(diff_text.splitlines()),
            "bounded_under_max_diff_lines": added <= m.max_diff_lines,
            "total_repo_loc_at_commit": repo_loc_at_commit,
            "tracked_files_at_commit": tracked_files_at_commit,
        }

    return {
        "commits": commits,
        "tags": tags,
        "milestone_git_stats": milestone_git_stats,
    }


def write_eval_readme(out_dir: Path, summary: Dict[str, Any]) -> None:
    """Render a comprehensive Markdown report in `<out_dir>/README.md`."""
    lines: List[str] = []
    lines.append("# Live `L0-Macro` Milestone DAG & Git Commit Ratchet Evaluation (`Beam-1.0-501B-A23B-NVFP4`)")
    lines.append("")
    lines.append(f"- **Date**: `2026-10-21`")
    lines.append(f"- **Live Endpoint**: `{summary['endpoint_info']['endpoint']}`")
    lines.append(f"- **Model**: `{summary['endpoint_info']['requested_model']}` (`+ DFlash2` speculative decoding on GKE)")
    lines.append(f"- **Macro Orchestrator**: `MacroMilestoneRatchetEngine` + `GitCommitRatchet` + `PytestVerifierPlugin`")
    lines.append(f"- **Micro Solver (`L0-Micro -> L1 -> L2`)**: `{summary['endpoint_info']['hierarchical_micro_engine']}` (`allow_new_files=True`)")
    lines.append(f"- **All 4 Milestones Certified**: `{summary['completed']}` (`{len(summary['certified_milestones'])}/{len(summary['topological_order'])}`)")
    lines.append(f"- **Total Wall-Clock Time**: `{summary['total_wall_clock_s']:.2f}s`")
    lines.append(f"- **Total LLM Calls / Tokens**: `{summary['totals']['llm_calls']}` calls / `{summary['totals']['total_tokens']:,}` tokens (`prompt={summary['totals']['prompt_tokens']:,}`, `output={summary['totals']['output_tokens']:,}`)")
    lines.append(f"- **Final Ratcheted `HEAD` SHA**: `{summary['head_commit_sha']}`")
    lines.append("")
    lines.append("## Per-Milestone Ratchet & Telemetry Summary")
    lines.append("")
    lines.append(
        "| Milestone | Status | Moves | LLM Calls | Total Tokens | Wall Time (s) | Active Delta (`+LoC` vs `HEAD_{k-1}`) | Total Repo LoC | Cumulative Regression Gate (`M_1..M_k`) | Certified Commit SHA | Git Tag |"
    )
    lines.append(
        "| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |"
    )
    for m in summary["milestones"]:
        mid = m["milestone_id"]
        status = m["status"]
        moves = m["moves_used"]
        llm_calls = m["token_usage"].get("llm_calls", 0)
        tot_tok = m["token_usage"].get("total_tokens", 0)
        elapsed = m["elapsed_s"]
        added = m["delta_added_lines"]
        max_dl = m["max_diff_lines"]
        total_loc = m["total_repo_loc"]
        cum_stages = "+".join(m["verified_milestones"])
        cum_tests = m["cumulative_passed_tests"]
        sha_short = (m["certified_commit_sha"] or "")[:12]
        tag = m["git_tag"]
        lines.append(
            f"| `{mid}` | **{status.upper()}** | {moves} | {llm_calls} | {tot_tok:,} | {elapsed:.2f}s | "
            f"`+{added}` lines (`< {max_dl}`) | `{total_loc}` lines | "
            f"`{cum_stages}` (**{cum_tests} passed**) | `{sha_short}` | `{tag}` |"
        )

    lines.append("")
    lines.append("## Verification of `L0-Macro` Ratchet Guarantees")
    lines.append("")
    lines.append("1. **Bounded Active Context & Delta (`< 500` LoC per milestone)**:")
    lines.append(
        "   - Because `GitCommitRatchet.advance_ratchet()` commits each certified milestone $M_k$ to `HEAD`, "
        "`git diff HEAD` during $M_{k+1}$ contains **only $M_{k+1}$'s active delta** while total repository size grows monotonically across `m1_lexer -> m2_parser -> m3_evaluator -> m4_bytecode_vm`."
    )
    for m in summary["milestones"]:
        lines.append(
            f"   - `{m['milestone_id']}`: active delta `+{m['delta_added_lines']}` lines "
            f"(total diff `{m['delta_diff_lines']}` lines, cap `{m['max_diff_lines']}`), "
            f"cumulative repository size `{m['total_repo_loc']}` LoC."
        )
    lines.append("2. **Monotonic Cumulative Regression Ratchet (`M_1 .. M_k`)**:")
    for m in summary["milestones"]:
        per_m = ", ".join(f"`{k}`={v}" for k, v in m["milestone_passed_tests"].items())
        lines.append(
            f"   - At `{m['milestone_id']}` certification: verified `{m['verified_milestones']}` -> "
            f"**{m['cumulative_passed_tests']} cumulative tests passed** ({per_m}); "
            f"archived falsification suite: `{m.get('archived_falsification_suite')}`."
        )
    lines.append("3. **Persistent Git Commits, Lightweight Tags & `.hae/ratchet_state.json`**:")
    for c in summary["git_telemetry"]["commits"]:
        lines.append(f"   - `{c['sha'][:12]}`: `{c['subject']}`")
    lines.append("")
    lines.append("## Artifacts in This Directory")
    lines.append("")
    lines.append("- `summary.json`: Full machine-readable telemetry (per-milestone wall time, moves, tokens, active delta lines, certified commit SHAs, git tags, cumulative regression test counts).")
    lines.append("- `ratchet_state.json` & `.hae/ratchet_state.json`: Persisted `GitCommitRatchet` state snapshot after certifying all 4 milestones.")
    lines.append("- `.hae/ratchet_suites/`: Archived Popperian falsification suites (`falsify_m1_lexer.py` .. `falsify_m4_bytecode_vm.py`).")
    lines.append("- `minilang_repo/`: The live Git repository containing the certified 4-milestone `MiniLang` system build and git tags.")
    lines.append("")

    (out_dir / "README.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run Live 4-Milestone L0-Macro Git Commit Ratchet Evaluation on Beam-1.0-501B-A23B-NVFP4."
    )
    parser.add_argument(
        "--out-dir",
        type=str,
        default="results/ratchet/beam_macro_ratchet_eval_2026-10-21",
        help="Output directory for summary.json, README.md, .hae/ratchet_state.json, and workspace.",
    )
    parser.add_argument(
        "--vllm-url",
        type=str,
        default="http://127.0.0.1:8000/v1",
        help="Live vLLM OpenAI-compatible base URL.",
    )
    parser.add_argument(
        "--vllm-model",
        type=str,
        default="reflection/Beam-1.0-501B-A23B-NVFP4",
        help="Model identifier served by the live vLLM endpoint.",
    )
    parser.add_argument(
        "--budget-moves",
        type=int,
        default=18,
        help="Maximum L0-Micro AB-MCTS moves per milestone.",
    )
    parser.add_argument(
        "--max-diff-lines",
        type=int,
        default=500,
        help="Maximum added diff lines permitted per individual milestone.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from existing workspace .hae/ratchet_state.json if present.",
    )
    args = parser.parse_args()

    # Configure environment so all L0-Micro -> L1 -> L2 specialists use the live vLLM endpoint
    os.environ["LLM_PROVIDER"] = "vllm"
    os.environ["VLLM_BASE_URL"] = args.vllm_url
    os.environ["VLLM_MODEL"] = args.vllm_model
    os.environ["HAE_WORKER_MODEL"] = args.vllm_model
    os.environ["VLLM_API_KEY"] = os.environ.get("VLLM_API_KEY", "EMPTY")
    os.environ["HAE_DISABLE_STAGNATION_ESCALATION"] = "1"
    os.environ["HAE_LLM_MAX_TOKENS"] = os.environ.get("HAE_LLM_MAX_TOKENS", "8192")

    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    workspace_dir = out_dir / "minilang_repo"

    print(f"[beam-macro-eval] Verifying live vLLM endpoint at {args.vllm_url} (model={args.vllm_model})...", flush=True)
    endpoint_info = verify_vllm_endpoint(args.vllm_url, args.vllm_model)
    print(f"[beam-macro-eval] Endpoint verified: {json.dumps(endpoint_info)}", flush=True)

    executor = init_minilang_git_workspace(workspace_dir, clean=not args.resume)
    dag = build_minilang_milestone_dag(
        budget_moves=args.budget_moves,
        max_diff_lines=args.max_diff_lines,
    )
    verifier_plugin = PytestVerifierPlugin(diff_bloat_threshold=args.max_diff_lines)

    engine = MacroMilestoneRatchetEngine(
        executor=executor,
        dag=dag,
        verifier_plugin=verifier_plugin,
        stop_on_failure=True,
        logger=lambda msg: print(msg, flush=True),
    )

    t_start = time.time()
    result = engine.run(resume=args.resume)
    total_wall_s = round(time.time() - t_start, 3)

    git_telemetry = collect_git_ratchet_telemetry(executor, dag)

    # Snapshot `.hae/ratchet_state.json` and `.hae/ratchet_suites/` into `out_dir`
    ws_state_file = workspace_dir / SCRATCH_DIR / "ratchet_state.json"
    out_hae_dir = out_dir / SCRATCH_DIR
    out_hae_dir.mkdir(parents=True, exist_ok=True)
    if ws_state_file.exists():
        shutil.copy2(ws_state_file, out_dir / "ratchet_state.json")
        shutil.copy2(ws_state_file, out_hae_dir / "ratchet_state.json")

    ws_suites_dir = workspace_dir / SCRATCH_DIR / "ratchet_suites"
    if ws_suites_dir.exists():
        out_suites_dir = out_hae_dir / "ratchet_suites"
        if out_suites_dir.exists():
            shutil.rmtree(out_suites_dir)
        shutil.copytree(ws_suites_dir, out_suites_dir)

    milestones_summary: List[Dict[str, Any]] = []
    tot_prompt_tokens = 0
    tot_output_tokens = 0
    tot_thought_tokens = 0
    tot_tokens = 0
    tot_llm_calls = 0
    tot_moves = 0

    for mid in dag.topological_order():
        m = dag.milestones[mid]
        ms = m.mcts_summary or {}
        gate = ms.get("cumulative_gate") or {}
        gate_details = gate.get("details") or {}
        tok = ms.get("token_usage") or {}
        git_stat = git_telemetry["milestone_git_stats"].get(mid, {})

        p_tok = int(tok.get("prompt_tokens", 0))
        o_tok = int(tok.get("output_tokens", 0))
        th_tok = int(tok.get("thought_tokens", 0))
        t_tok = int(tok.get("total_tokens", 0))
        calls = int(tok.get("llm_calls", 0))
        moves = int(ms.get("moves_used", 0))

        tot_prompt_tokens += p_tok
        tot_output_tokens += o_tok
        tot_thought_tokens += th_tok
        tot_tokens += t_tok
        tot_llm_calls += calls
        tot_moves += moves

        milestones_summary.append(
            {
                "milestone_id": mid,
                "title": m.title,
                "depends_on": list(m.depends_on),
                "target_files": list(m.target_files),
                "acceptance_cmd": m.acceptance_cmd,
                "max_diff_lines": m.max_diff_lines,
                "status": m.status,
                "base_head_sha": ms.get("base_head_sha", git_stat.get("base_commit_sha", "")),
                "certified_commit_sha": m.certified_commit_sha,
                "git_tag": f"hae-milestone-{mid}",
                "elapsed_s": ms.get("elapsed_s", 0.0),
                "moves_used": moves,
                "token_usage": {
                    "llm_calls": calls,
                    "prompt_tokens": p_tok,
                    "output_tokens": o_tok,
                    "thought_tokens": th_tok,
                    "total_tokens": t_tok,
                },
                "delta_files": ms.get("delta_files", []),
                "delta_added_lines": git_stat.get("delta_added_lines", ms.get("delta_added_lines", 0)),
                "delta_removed_lines": git_stat.get("delta_removed_lines", 0),
                "delta_diff_lines": git_stat.get("delta_total_diff_lines", ms.get("delta_diff_lines", 0)),
                "total_repo_loc": git_stat.get("total_repo_loc_at_commit", ms.get("total_repo_loc", 0)),
                "archived_falsification_suite": m.falsification_suite_path,
                "verified_milestones": gate_details.get("verified_milestones", []),
                "milestone_passed_tests": gate_details.get("milestone_passed_tests", {}),
                "cumulative_passed_tests": gate_details.get("cumulative_passed_tests", gate.get("passed_tests", 0)),
                "cumulative_gate": gate,
            }
        )

    summary_payload: Dict[str, Any] = {
        "eval_id": "beam_macro_ratchet_eval_2026-10-21",
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "endpoint_info": endpoint_info,
        "completed": result["completed"],
        "topological_order": result["topological_order"],
        "certified_milestones": result["certified_milestones"],
        "failed_milestones": result["failed_milestones"],
        "pending_milestones": result["pending_milestones"],
        "head_commit_sha": result["head_commit_sha"],
        "total_wall_clock_s": total_wall_s,
        "totals": {
            "moves_used": tot_moves,
            "llm_calls": tot_llm_calls,
            "prompt_tokens": tot_prompt_tokens,
            "output_tokens": tot_output_tokens,
            "thought_tokens": tot_thought_tokens,
            "total_tokens": tot_tokens,
        },
        "milestones": milestones_summary,
        "git_telemetry": git_telemetry,
        "ratchet_state_path": str(out_dir / ".hae" / "ratchet_state.json"),
    }

    summary_path = out_dir / "summary.json"
    summary_path.write_text(json.dumps(summary_payload, indent=2), encoding="utf-8")
    write_eval_readme(out_dir, summary_payload)

    print(
        f"[beam-macro-eval] Finished! completed={result['completed']} "
        f"certified={result['certified_milestones']} wall_s={total_wall_s:.2f}s "
        f"summary={summary_path}",
        flush=True,
    )
    return 0 if result["completed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
