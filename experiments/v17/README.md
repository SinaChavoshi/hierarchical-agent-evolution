# V17: Red Team as a Foundation Piece & High-Budget Agentic Harnesses

## 1. Motivation & Hypotheses

In V16, the internal falsifier ("adversarial reviewer") rubber-stamped broken patches on 41–57% of runs where it declared `VERDICT: PASS`, and evolution could not select for stricter verification because:
1. **No direct evolutionary reward for falsifier accuracy**: fitness depended only on final task resolution, not on whether the falsifier caught broken patches (`TN`) or avoided rubber-stamping (`FP`).
2. **Permissive certification contract**: any response that did not literally contain `"VERDICT: FAIL"` was treated as `PASS`.
3. **Shared mutable workspace**: the falsifier had full write access (`str_replace`, `write_file`, `bash`) and could weaken `.hae/reproduce_issue.py` or edit source files to make tests pass.
4. **Tight step budgets**: `budget_moves=30` and `4–6` MCTS episodes (`8–16` turns per specialist) caused early termination before iterative refinement could recover from a Red-Team rejection.

### Pre-Registered Hypotheses
- **H1 (Strict Red-Team Foundation)**: Enforcing read-only repo access during falsification (`no_edit`), immutable reproduction tests (`lock_repro_test`), calibrated skeptical certification (`skeptic_default`), broader regression execution (`evidence_tests`), and continuous refinement (`continuous_build`) (`Arm S`) significantly lowers the false-pass rate (`FP / (FP + TN)`) and improves resolve rate over the permissive baseline (`Arm A17`).
- **H2 (Natural Emergence of Red-Team Strictness)**: Adding a direct Red-Team accuracy objective (`redteam_weight = 0.30` with a $2\times$ penalty on false `PASS` verdicts) and per-verdict role credit (`Arm R`) causes evolutionary selection to naturally discover and retain stricter Red-Team configurations and higher-utility falsifier roles, outperforming both `Arm A17` and matched-budget random search (`Arm Rr`).
- **H3 (High-Budget Scaling)**: Lifting the tool-call budget from `30` to `250` moves (`12` MCTS episodes, `16–40` turns per specialist, `7200s` instance timeout) allows agents to exhaust their options and self-terminate only after passing independent verification.

---

## 2. Red-Team Architecture (`hae/swebench/org_binding.py`)

V17 introduces `REDTEAM_SPACE`, a 6-gene evolvable Red-Team specification enforced by `HarnessOrg`:

| Gene | Values | Mechanism |
|---|---|---|
| `no_edit` | `False, True` | Blocks `str_replace` and `write_file` on repository files during falsifier turns (allowing only `.hae/` diagnostic scripts) and reverts any accidental repo modifications after `bash` commands. |
| `lock_repro_test` | `False, True` |Snapshots `.hae/reproduce_issue.py` once verified failing on `HEAD`, blocks modifications to it, and restores it prior to `verify_current_workspace`. |
| `skeptic_default` | `False, True` | Replaces permissive certification (`not "VERDICT: FAIL"`) with strict proof-of-execution: requires an explicit `VERDICT: PASS`, no `VERDICT: FAIL`, and at least one executed `bash` test or `.hae/falsification_suite.py` in the episode. Injects `CALIBRATED_SKEPTIC_PROMPT` with concrete calibration rules. |
| `evidence_tests` | `False, True` | Runs `pytest` on up to 6 discovered proximity test files (`tfiles[:6]`) during `verify_current_workspace` in addition to the reproduction script. |
| `panel_size` | `1, 2, 3` | Runs $k$ distinct Red-Team roles in sequence; a candidate patch is certified only if all $k$ panelists return `VERDICT: PASS` (`AND`-gate). |
| `continuous_build` | `False, True` | Prioritizes `CONT` (`L2_DeltaRefinement`) on compiling uncertified checkpoints over resetting to `HEAD` (`GEN`). |

---

## 3. Evolutionary Selection for Strict Verification

1. **Ground-Truth Verdict Labeling (`train` split only)**:
   After a training run completes on a testbed pod, `GenomeEvaluator._grade_verdicts` grades up to 6 unique candidate patches (`org.verdict_patches`) against the hidden test suite, recording `tp, fp, tn, fn` in `verdicts_grade.json`.
2. **Red-Team Accuracy Score (`HarnessGenome.red_team_score`)**:
   $$\text{sensitivity} = \frac{\text{TP}}{\text{TP} + \text{FN}}, \quad \text{specificity}_{2\times} = \frac{\text{TN}}{\text{TN} + 2\cdot\text{FP}}, \quad \text{red\_team\_score} = \frac{\text{sensitivity} + \text{specificity}_{2\times}}{2}$$
3. **Training Fitness (`HarnessGenome.fitness`)**:
   $$\text{fitness} = \text{resolve\_rate} + 0.25 \cdot \text{partial\_f2p} + 0.30 \cdot \text{red\_team\_score} - 0.05 \cdot \frac{\text{mean\_tokens}}{100{,}000}$$
4. **Per-Verdict Role Credit (`hae/evolution/org_evolution.py`)**:
   Every verified candidate patch updates the active falsifier role's empirical utility (`RoleAllele.record_episode`): `+1` for `TP`/`TN`, `-2` for `FP` (rubber-stamp), and `-1` for `FN` (over-rejection). The CEO selects falsifier roles via UCB over measured verdict accuracy without keyword-overlap discounting.

---

## 4. Experimental Design & Splits (`data/swebench/v17_splits.json`)

- **Training (`41` tasks)**: 21 `swebench_dev.jsonl` tasks + 20 stratified `verified_test.jsonl` tasks (strictly disjoint from `val`, `test`, and `verified_slice_30.json`).
- **Stage-1 Filter (`10` tasks)**: 5 `swebench_dev` tasks + 5 `verified_test` training tasks.
- **Validation (`10` tasks)**: Identical to `v15_splits.json["val"]`.
- **Held-Out Test (`28` tasks)**: Identical to `v15_splits.json["test"]` (unseen SWE-bench Verified).

### Arms
- **`A17`**: Dynamic org (`Arm C`) + high budgets (`V17_HIGH_BUDGET_KNOBS`, `250` moves, `7200s` timeout) + permissive `V17_DEFAULT_REDTEAM`.
- **`S`**: `A17` + strict Red-Team foundation (`V17_STRICT_REDTEAM`), fixed (no evolution).
- **`R`**: Full evolution (`scripts/v17_experiment.py evolve --mode evolve`) over Red-Team genes, roles, CEO policy, knobs, and code.
- **`Rr`**: Matched-budget random search (`scripts/v17_experiment.py evolve --mode random`).
- **`V12`**: Flat ReAct agent at matched V17 high budget (`250` moves, `7200s` timeout).

---

## 5. Industry Survey on Agent Harness Innovation (2024–2026)

Recent research and engineering literature across academia and industry converges on the principle that the **agent harness**—its environment boundary, tool guardrails, state persistence, and verification architecture—is as decisive as the underlying model weights:

1. **Agent–Computer Interfaces (ACI) & Sandboxed Execution** — *Yang et al., ["SWE-agent: Agent-Computer Interfaces Enable Automated Software Engineering"](https://arxiv.org/abs/2405.15793) (NeurIPS 2024, Princeton NLP)*; *Wang et al., ["OpenHands: An Open Platform for AI Software Developers as Generalist Agents"](https://arxiv.org/abs/2407.16741) (ICLR 2025, All Hands AI)*.
2. **Localization, Reproduction & Regression Guardrails** — *Xia et al., ["Agentless: Demystifying LLM-based Software Engineering Agents"](https://arxiv.org/abs/2407.01489) (UIUC, 2024)*; *Gauthier, ["Aider: Architect/Editor Mode & Automated Lint/Test Loops"](https://aider.chat/2024/09/26/architect.html) (2024–2025)*.
3. **Harness Engineering & Deterministic Environment Constraints** — *OpenAI, ["Harness Engineering: Leveraging Codex in an Agent-First World"](https://openai.com/index/harness-engineering/) (Feb 2026)*; *Böckeler, ["Harness Engineering for Coding Agent Users"](https://martinfowler.com/articles/exploring-gen-ai/harness-engineering.html) (Martin Fowler / Thoughtworks, 2026)*; *LangChain, ["Improving Deep Agents with Harness Engineering"](https://blog.langchain.com/) (2026)*.
4. **Multi-Session Progress Artifacts & Independent Evaluator Separation** — *Anthropic Engineering, ["Effective Harnesses for Long-Running Agents"](https://www.anthropic.com/research/effective-harnesses-for-long-running-agents) (Nov 2025)* and *["Harness Design for Long-Running Application Development"](https://www.anthropic.com/research/harness-design-long-running-apps) (Mar 2026)*.
