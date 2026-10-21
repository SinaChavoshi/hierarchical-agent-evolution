# Live `L0-Macro` Milestone DAG & Git Commit Ratchet Evaluation (`Beam-1.0-501B-A23B-NVFP4`)

- **Date**: `2026-10-21`
- **Live Endpoint**: `http://127.0.0.1:8000/v1`
- **Model**: `reflection/Beam-1.0-501B-A23B-NVFP4` (`+ DFlash2` speculative decoding on GKE)
- **Macro Orchestrator**: `MacroMilestoneRatchetEngine` + `GitCommitRatchet` + `PytestVerifierPlugin`
- **Micro Solver (`L0-Micro -> L1 -> L2`)**: `HierarchicalAgenticMCTSEngine` (`allow_new_files=True`)
- **All 4 Milestones Certified**: `True` (`4/4`)
- **Total Wall-Clock Time**: `245.58s`
- **Total LLM Calls / Tokens**: `49` calls / `381,652` tokens (`prompt=365,138`, `output=16,514`)
- **Final Ratcheted `HEAD` SHA**: `c7e1d45161d6f899460205a0ea6b2b71001ea570`

## Per-Milestone Ratchet & Telemetry Summary

| Milestone | Status | Moves | LLM Calls | Total Tokens | Wall Time (s) | Active Delta (`+LoC` vs `HEAD_{k-1}`) | Total Repo LoC | Cumulative Regression Gate (`M_1..M_k`) | Certified Commit SHA | Git Tag |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| `m1_lexer` | **CERTIFIED** | 11 | 11 | 64,552 | 56.23s | `+256` lines (`< 500`) | `263` lines | `m1_lexer` (**10 passed**) | `43eb7bda4374` | `hae-milestone-m1_lexer` |
| `m2_parser` | **CERTIFIED** | 10 | 10 | 67,755 | 57.21s | `+302` lines (`< 500`) | `564` lines | `m1_lexer+m2_parser` (**14 passed**) | `701ded5bea21` | `hae-milestone-m2_parser` |
| `m3_evaluator` | **CERTIFIED** | 10 | 10 | 75,457 | 51.06s | `+235` lines (`< 500`) | `798` lines | `m1_lexer+m2_parser+m3_evaluator` (**22 passed**) | `da346ab6e349` | `hae-milestone-m3_evaluator` |
| `m4_bytecode_vm` | **CERTIFIED** | 18 | 18 | 173,888 | 79.06s | `+342` lines (`< 500`) | `1139` lines | `m1_lexer+m2_parser+m3_evaluator+m4_bytecode_vm` (**25 passed**) | `c7e1d45161d6` | `hae-milestone-m4_bytecode_vm` |

## Verification of `L0-Macro` Ratchet Guarantees

1. **Bounded Active Context & Delta (`< 500` LoC per milestone)**:
   - Because `GitCommitRatchet.advance_ratchet()` commits each certified milestone $M_k$ to `HEAD`, `git diff HEAD` during $M_{k+1}$ contains **only $M_{k+1}$'s active delta** while total repository size grows monotonically across `m1_lexer -> m2_parser -> m3_evaluator -> m4_bytecode_vm`.
   - `m1_lexer`: active delta `+256` lines (total diff `274` lines, cap `500`), cumulative repository size `263` LoC.
   - `m2_parser`: active delta `+302` lines (total diff `328` lines, cap `500`), cumulative repository size `564` LoC.
   - `m3_evaluator`: active delta `+235` lines (total diff `257` lines, cap `500`), cumulative repository size `798` LoC.
   - `m4_bytecode_vm`: active delta `+342` lines (total diff `364` lines, cap `500`), cumulative repository size `1139` LoC.
2. **Monotonic Cumulative Regression Ratchet (`M_1 .. M_k`)**:
   - At `m1_lexer` certification: verified `['m1_lexer']` -> **10 cumulative tests passed** (`m1_lexer`=10); archived falsification suite: `.hae/ratchet_suites/falsify_m1_lexer.py`.
   - At `m2_parser` certification: verified `['m1_lexer', 'm2_parser']` -> **14 cumulative tests passed** (`m1_lexer`=10, `m2_parser`=4); archived falsification suite: `.hae/ratchet_suites/falsify_m2_parser.py`.
   - At `m3_evaluator` certification: verified `['m1_lexer', 'm2_parser', 'm3_evaluator']` -> **22 cumulative tests passed** (`m1_lexer`=10, `m2_parser`=4, `m3_evaluator`=8); archived falsification suite: `.hae/ratchet_suites/falsify_m3_evaluator.py`.
   - At `m4_bytecode_vm` certification: verified `['m1_lexer', 'm2_parser', 'm3_evaluator', 'm4_bytecode_vm']` -> **25 cumulative tests passed** (`m1_lexer`=10, `m2_parser`=4, `m3_evaluator`=8, `m4_bytecode_vm`=3); archived falsification suite: `None`.
3. **Persistent Git Commits, Lightweight Tags & `.hae/ratchet_state.json`**:
   - `21b08e15f7eb`: `chore: initial repository scaffold`
   - `43eb7bda4374`: `hae(ratchet): certify milestone m1_lexer - MiniLang Tokenizer & Lexical Analyzer`
   - `701ded5bea21`: `hae(ratchet): certify milestone m2_parser - MiniLang AST Nodes & Recursive-Descent Parser`
   - `da346ab6e349`: `hae(ratchet): certify milestone m3_evaluator - MiniLang Lexical Environment, Closures & Tree-Walking Evaluator`
   - `c7e1d45161d6`: `hae(ratchet): certify milestone m4_bytecode_vm - MiniLang Constant-Folding Bytecode Compiler & Stack Virtual Machine`

## Artifacts in This Directory

- `summary.json`: Full machine-readable telemetry (per-milestone wall time, moves, tokens, active delta lines, certified commit SHAs, git tags, cumulative regression test counts).
- `ratchet_state.json` & `.hae/ratchet_state.json`: Persisted `GitCommitRatchet` state snapshot after certifying all 4 milestones.
- `.hae/ratchet_suites/`: Archived Popperian falsification suites (`falsify_m1_lexer.py` .. `falsify_m4_bytecode_vm.py`).
- `minilang_repo/`: The live Git repository containing the certified 4-milestone `MiniLang` system build and git tags.
