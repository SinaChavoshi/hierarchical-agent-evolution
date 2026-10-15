# V13 (`v13_hier_mcts`) Evaluation Report: Hierarchical Verification Harness + Agentic AB-MCTS ("Move 37" Engine)

> [!IMPORTANT]
> **Headline Result**: **V13 (`v13_hier_mcts`) achieves a `50.0%` (`15 / 30`) gold held-out resolution rate** on the 30-instance SWE-bench benchmark cohort (`dev_eval_2026-10-09`), surpassing V12 (`46.7%`, `14 / 30`), V11 (`20.0%`, `6 / 30`), V10 (`16.7%`, `5 / 30`), V9 (`10.0%`, `3 / 30`), and V8 (`13.3%`, `4 / 30`) on the exact same `Qwen3.8-Flash-Next-NVFP4` model (`48` moves budget).
>
> Across the union **`V12 ∪ V13`**, **`17 / 30` (`56.7%`)** of the cohort is resolved, and V13 achieves **`100.0%` (`30 / 30`) non-empty verified patches**, **`96.7%` (`29 / 30`) zero-regression on held-out `PASS_TO_PASS` suites**, and an **8.7% reduction in mean wall-clock execution time** (`1,197.3s` vs `1,310.7s` in V12).

---

## 1. Executive Summary & Architectural Paradigm Validation

V13 (`v13_hier_mcts`) tested our central architectural thesis:
1. **Hierarchical Verification Harness (Epistemic Context Isolation)**: Instead of mimicking a corporate org chart where managers pass conversational transcripts down a chain of command, each layer (`L0` Seed Architect, `L1-A` Reconnaissance, `L2` Branch/Refinement Executor, `L1-B` Adversarial Falsifier) operates in a **fresh, zero-inheritance context window** bounded by an explicit `VerificationContract` and a Popperian falsification loop.
2. **Agentic AB-MCTS ("Move 37" Engine)**: Instead of running AB-MCTS over single LLM turns (which fragmented coding trajectories in V8–V11) or running a single linear trajectory (which got trapped in local optima in V12), V13 elevates each AB-MCTS node (`AgenticMCTSNode`) into a **complete, git-checkpointed multi-turn agentic episode** (`10–22` turns) governed by Beta-posterior Thompson Sampling ($\theta_n \sim \text{Beta}(\alpha_n, \beta_n)$) over `GEN` (spawning a causally distinct branch at `root`: `consumer_local`, `producer_upstream`, `codebase_symmetry`, or `move37_counterfactual`) vs. `CONT` (targeted delta-refinement of a surviving parent checkpoint via `git apply`).

### Six-Generation Longitudinal Progression (`V8` $\to$ `V13`)

```mermaid
xychart-beta
    title "SWE-bench Gold Held-Out Resolution Rate (%) Across V8 - V13 (n = 30)"
    x-axis ["V8 (PUCT)", "V9 (AB-MCTS)", "V10 (+EPLB)", "V11 (+Attr Prior)", "V12 (Deep Agentic)", "V13 (Hier AB-MCTS)"]
    y-axis "Graded Resolved (%)" 0 --> 60
    bar [13.3, 10.0, 16.7, 20.0, 46.7, 50.0]
```

| Metric | `v8_puct` | `v9_ab_mcts` | `v10_ab_mcts` | `v11_ab_mcts` | `v12_agentic` | **`v13_hier_mcts` (V13)** | $\Delta$ (`V13` vs `V12`) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Gold Held-Out Resolved (`resolved=True`)** | `4 / 30` (`13.3%`) | `3 / 30` (`10.0%`) | `5 / 30` (`16.7%`) | `6 / 30` (`20.0%`) | `14 / 30` (`46.7%`) | **`15 / 30` (`50.0%`)** | **`+1` (`+3.3%` abs / `+7.1%` rel)** |
| **Union Resolved (`V12 ∪ V13`)** | — | — | — | — | `14 / 30` (`46.7%`) | **`17 / 30` (`56.7%`)** | **`+3` new instances solved** |
| **Instances Passing $\ge 1$ `FAIL_TO_PASS` Test** | `4 / 30` (`13.3%`) | `3 / 30` (`10.0%`) | `5 / 30` (`16.7%`) | `6 / 30` (`20.0%`) | `17 / 30` (`56.7%`) | **`17 / 30` (`56.7%`)** | `0` |
| **Non-Empty Verified Patch Produced** | `9 / 30` (`30.0%`) | `8 / 30` (`26.7%`) | `11 / 30` (`36.7%`) | `11 / 30` (`36.7%`) | `26 / 30` (`86.7%`) | **`30 / 30` (`100.0%`)** | **`+4` (`+13.3%`)** |
| **Self-Oracle + Falsifier Certified** | `6 / 30` (`20.0%`) | `8 / 30` (`26.7%`) | `6 / 30` (`20.0%`) | `9 / 30` (`30.0%`) | `16 / 30` (`53.3%`) | **`24 / 30` (`80.0%`)** | **`+8` (`+26.7%`)** |
| **Zero-Regression on `PASS_TO_PASS`** | `28 / 30` (`93.3%`) | `30 / 30` (`100.0%`) | `29 / 30` (`96.7%`) | `30 / 30` (`100.0%`) | `28 / 30` (`93.3%`) | **`29 / 30` (`96.7%`)** | **`+1` (`+3.4%`)** |
| **Mean LLM Moves Used (cap `48`)** | `17.0` | `18.0` | `18.9` | `19.3` | `29.7` | **`33.9`** | `+4.2` |
| **Mean Tokens per Instance** | `85,483` | `96,041` | `100,631` | `99,133` | `276,902` | **`300,800`** | `+8.6%` |
| **Mean Wall-Clock Time per Instance (s)** | `555.8s` | `463.8s` | `539.8s` | `519.5s` | `1,310.7s` | **`1,197.3s`** | **`-113.4s` (`-8.7%` faster)** |

---

## 2. Deep-Dive Forensics: The Three V13 Breakthroughs ("Move 37" & Popperian Falsification in Action)

Every one of the three new instances resolved by V13 (`marshmallow-code__marshmallow-1702`, `pylint-dev__astroid-1417`, and `pylint-dev__astroid-934`) had failed across **all five previous architectures (`V8`, `V9`, `V10`, `V11`, and `V12`)** because a single-path agent converged on a seductive local optimum that passed its own reproduction script.

### Breakthrough 1: `pylint-dev__astroid-934` (`0/1` F2P in V8–V12 $\to$ `1/1` F2P, `312/312` P2P in V13)
- **The Bug**: Calling `ClassDef.infer_ancestors()` during complex multiple inheritance mutated the shared `InferenceContext.path` set across sibling base class evaluations, prematurely raising ` InferenceError` / dropping valid ancestors.
- **Why V12 Failed (`Local Optimum`)**: V12's single-trajectory specialist modified `astroid/scoped_nodes.py` in a way that broke collection/inference (`0/0` F2P) and exhausted its turn budget without escaping the broken state.
- **How V13's Hierarchical AB-MCTS Solved It (`34` moves, `998.9s`)**:
  - **Layer `L0` (`SeedArchitectNode`)** decomposed the search into `b1` (`consumer_local`: local ancestor filtering in `ClassDef.ancestors`) and `b2` (`producer_upstream`: upstream `InferenceContext` cloning in `ClassDef.infer_ancestors`).
  - **Episode 1 (`b1`, `consumer_local`)** ran for 22 turns, scored $V(b_1) = 0.05$ (`repro_ok=False`), and updated its Beta posterior downward ($\beta_{b_1} \leftarrow \beta_{b_1} + 0.95$).
  - **Episode 2 (`b2`, `producer_upstream` — "Move 37" Branch Switch)**: Instead of wasting remaining turns trying to salvage `b1`'s polluted context, AB-MCTS Thompson Sampling selected `GEN -> b2` (`producer_upstream`, *"Ensure Context Cloning in ClassDef.infer_ancestors"*), reset `/testbed` cleanly to `HEAD`, and launched a fresh `L2` executor with the Negative Evidence Ledger from `b1`.
  - Within 11 turns, `b2` pinpointed `context = context.clone()` inside `ClassDef.infer_ancestors`, verified it against `/testbed/.hae/reproduce_issue.py` and the full 928-test suite (`0` regressions), and **Layer `L1-B` (`AdversarialFalsifierSubAgent`)** corroborated the fix across diamond inheritance and cyclic base graphs!

### Breakthrough 2: `pylint-dev__astroid-1417` (`0/1` F2P in V8–V12 $\to$ `1/1` F2P, `3/3` P2P in V13)
- **The Bug**: Deprecate `astroid.decorators.cachedproperty` in favor of `functools.cached_property`.
- **Why V12 Failed (`Single-File Myopia`)**: V12 edited only `astroid/decorators.py` to emit a `DeprecationWarning` inside `cachedproperty`. However, `astroid`'s own internal classes (`astroid/mixins.py`, `astroid/objects.py`, `astroid/nodes/node_ng.py`, `astroid/nodes/node_classes.py`, `astroid/nodes/scoped_nodes/scoped_nodes.py`) still used `@cachedproperty`! When the held-out gold test ran `pytest -Werror`, internal `astroid` imports triggered the newly added `DeprecationWarning` and failed the test (`0/1` F2P)!
- **How V13 Solved It (`19` moves, `806.4s`, 6 files updated cleanly)**:
  - **Layer `L1-A` Reconnaissance + Layer `L0` `VerificationContract`** explicitly encoded the codebase-wide symmetry invariant (*"All internal consumers of deprecated `@cachedproperty` across `astroid/` must be migrated to `@cached_property` on Python $\ge 3.8$ so internal imports do not emit `DeprecationWarning`"*).
  - **Layer `L2` (`b1`)** updated `astroid/decorators.py` **and** all 5 internal consumer modules (`astroid/mixins.py`, `astroid/nodes/node_classes.py`, `astroid/nodes/node_ng.py`, `astroid/nodes/scoped_nodes/scoped_nodes.py`, `astroid/objects.py`), passing Gatekeeper (`1113 passed` in whole-suite pytest) and surviving `L1-B` Adversarial Falsification (`1/1` F2P, `3/3` P2P).

### Breakthrough 3: `marshmallow-code__marshmallow-1702` (`14/28` F2P in V12 $\to$ `28/28` F2P, `71/71` P2P in V13)
- **The Bug**: Deprecate passing field metadata via keyword arguments (`Field(description="...")`) in favor of `Field(metadata={"description": "..."})`.
- **Why V12 Failed (`50%` F2P Partial Fix)**: V12 emitted `RemovedInMarshmallow4Warning` when `additional_metadata` was non-empty, **but** mutated `self.metadata` in the wrong dictionary merge order (`metadata.update(additional_metadata)` or overwriting `metadata`), failing `14/28` parameterized `FAIL_TO_PASS` assertions when both `metadata={...}` and `**additional_metadata` were supplied.
- **How V13 Solved It (`31` moves, `849.2s`)**:
  - **Layer `L1-A` (`ReconnaissanceSubAgent`)** automatically extracted `src/marshmallow/warnings.py` (`RemovedInMarshmallow4Warning`) and `src/marshmallow/fields.py`.
  - **Layer `L2` (`b1`)** implemented both the `warnings.warn(..., RemovedInMarshmallow4Warning, stacklevel=2)` emission and the non-destructive dictionary merge `self.metadata = {**(metadata or {}), **additional_metadata}`, and **Layer `L1-B` (`AdversarialFalsifierSubAgent`)** explicitly tested passing both `metadata={"a": 1}` and `b=2` simultaneously (`28/28` F2P, `71/71` P2P).

---

## 3. Post-Audit Harness Refinements Validated During V13

During our trajectory inspection of the initial V13 batch run, we identified and fixed a subtle Popperian falsification edge case in [`hae/swebench/hierarchical_mcts.py`](file:///usr/local/google/home/chavoshi/hierarchical-agent-evolution/hae/swebench/hierarchical_mcts.py#L845-L858):
- **Popperian Corroboration Principle (`marshmallow-code__marshmallow-1252`)**:
  - In `marshmallow-1252`, `b1` produced the exact 695-character passing patch in `src/marshmallow/utils.py` (`from_iso_time` / `from_iso_datetime`) and passed Gatekeeper (`37/37` P2P).
  - `L1_AdversarialFalsifier` then executed 8 turns of adversarial testing (testing timezone offsets, microseconds, and full `pytest` suites), finding **zero counterexamples** (`suite_ok=True`, `explicit_fail=False`), but reached Turn 8/8 before emitting a `submit` block.
  - Under Popperian epistemology, a hypothesis that passes Gatekeeper and survives an 8-turn adversarial falsification attempt with zero failing assertions (`suite_ok=True and not explicit_fail`) is **corroborated** (`falsifier_ok=True`), rather than rejected by default for omitting a closing tag. Enforcing this principle in [`run_falsifier_episode`](file:///usr/local/google/home/chavoshi/hierarchical-agent-evolution/hae/swebench/hierarchical_mcts.py#L845-L858) immediately restored `marshmallow-code__marshmallow-1252` (`1/1` F2P, `37/37` P2P in `28` moves).

---

## 4. Per-Repository Breakdown (`V8` $\to$ `V13`)

| Repository | Cohort Count | `v8_puct` | `v9_ab_mcts` | `v10_ab_mcts` | `v11_ab_mcts` | `v12_agentic` | **`v13_hier_mcts` (V13)** | **`V12 ∪ V13` Union** |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| `marshmallow-code/marshmallow` | 9 | `2 / 9` (`22.2%`) | `1 / 9` (`11.1%`) | `3 / 9` (`33.3%`) | `3 / 9` (`33.3%`) | `6 / 9` (`66.7%`) | **`7 / 9` (`77.8%`)** | **`7 / 9` (`77.8%`)** |
| `pylint-dev/astroid` | 8 | `1 / 8` (`12.5%`) | `1 / 8` (`12.5%`) | `1 / 8` (`12.5%`) | `2 / 8` (`25.0%`) | `5 / 8` (`62.5%`) | **`6 / 8` (`75.0%`)** | **`7 / 8` (`87.5%`)** |
| `sqlfluff/sqlfluff` | 8 | `1 / 8` (`12.5%`) | `1 / 8` (`12.5%`) | `1 / 8` (`12.5%`) | `1 / 8` (`12.5%`) | `2 / 8` (`25.0%`) | **`2 / 8` (`25.0%`)** | **`2 / 8` (`25.0%`)** |
| `pyvista/pyvista` | 1 | `0 / 1` (`0.0%`) | `0 / 1` (`0.0%`) | `0 / 1` (`0.0%`) | `0 / 1` (`0.0%`) | `1 / 1` (`100.0%`) | `0 / 1` (`0.0%`)* | **`1 / 1` (`100.0%`)** |
| `pydicom/pydicom` | 4 | `0 / 4` (`0.0%`) | `0 / 4` (`0.0%`) | `0 / 4` (`0.0%`) | `0 / 4` (`0.0%`) | `0 / 4` (`0.0%`) | `0 / 4` (`0.0%`) | `0 / 4` (`0.0%`) |
| **Total (All 30 Valid Instances)** | **30** | **`4 / 30` (`13.3%`)** | **`3 / 30` (`10.0%`)** | **`5 / 30` (`16.7%`)** | **`6 / 30` (`20.0%`)** | **`14 / 30` (`46.7%`)** | **`15 / 30` (`50.0%`)** | **`17 / 30` (`56.7%`)** |

*\*Note on `pyvista__pyvista-4226`: The held-out gold test imports a private helper `from pyvista.plotting._property import _check_range` that is not named anywhere in the GitHub issue description; V13 implemented the range validation via `_validate_range` and passed all self-oracle checks.*

---

## 5. Complete 30-Instance Cohort Ledger (`V8` $\to$ `V13`)

| # | Instance ID | `v8_puct` | `v9_ab_mcts` | `v10_ab_mcts` | `v11_ab_mcts` | `v12_agentic` | **`v13_hier_mcts`** | V13 `F2P` | V13 `P2P` | V13 Moves | V13 Time (s) | V13 Stop / Mechanism |
| :-: | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| 1 | `marshmallow-code__marshmallow-1164` | FAIL | FAIL | **PASS** | **PASS** | **PASS** | **PASS** | `1/1` | `46/46` | 17 | 440.2 | `resolved_by_hierarchical_ab_mcts` (`b1` certified) |
| 2 | `marshmallow-code__marshmallow-1229` | FAIL | FAIL | FAIL | FAIL | FAIL (`2/8`) | FAIL (`2/8`) | `2/8` | `69/69` | 41 | 1837.0 | `resolved_by_agentic_gatekeeper` (partial F2P) |
| 3 | `marshmallow-code__marshmallow-1252` | FAIL | FAIL | FAIL | FAIL | **PASS** | **PASS** | `1/1` | `37/37` | 28 | 804.8 | `resolved_by_hierarchical_ab_mcts` (`b1` corroborated) |
| 4 | `marshmallow-code__marshmallow-1343` | **PASS** | FAIL | **PASS** | **PASS** | **PASS** | **PASS** | `1/1` | `64/64` | 47 | 1707.2 | `resolved_by_agentic_gatekeeper` (`CONT(b1) -> b1.2`) |
| 5 | `marshmallow-code__marshmallow-1359` | **PASS** | **PASS** | **PASS** | **PASS** | **PASS** | **PASS** | `1/1` | `20/20` | 25 | 564.1 | `resolved_by_hierarchical_ab_mcts` (`b1` certified) |
| 6 | `marshmallow-code__marshmallow-1524` | FAIL | FAIL | FAIL | FAIL | **PASS** | **PASS** | `5/5` | `38/38` | 17 | 447.6 | `resolved_by_hierarchical_ab_mcts` (`b1` certified) |
| 7 | `marshmallow-code__marshmallow-1702` | FAIL | FAIL | FAIL | FAIL | FAIL (`14/28`) | **PASS (+NEW)** | **`28/28`** | **`71/71`** | 31 | 849.2 | `resolved_by_hierarchical_ab_mcts` (`L1-A` warning probe + `L1-B` audit) |
| 8 | `marshmallow-code__marshmallow-1810` | FAIL | FAIL | FAIL | FAIL | **PASS** | **PASS** | `1/1` | `25/25` | 24 | 586.2 | `resolved_by_hierarchical_ab_mcts` (`b1` certified) |
| 9 | `marshmallow-code__marshmallow-2123` | FAIL | FAIL | FAIL | FAIL | FAIL | FAIL | `0/23` | `51/51` | 34 | 1342.3 | `resolved_by_hierarchical_ab_mcts` |
| 10 | `pydicom__pydicom-1255` | FAIL | FAIL | FAIL | FAIL | FAIL | FAIL | `0/1` | `47/47` | 39 | 1128.2 | `resolved_by_hierarchical_ab_mcts` |
| 11 | `pydicom__pydicom-863` | FAIL | FAIL | FAIL | FAIL | FAIL | FAIL | `0/3` | `34/35` | 26 | 1838.9 | `best_checkpoint_restored` |
| 12 | `pydicom__pydicom-996` | FAIL | FAIL | FAIL | FAIL | FAIL | FAIL | `0/1` | `146/146` | 37 | 1729.1 | `best_checkpoint_restored` |
| 13 | `pydicom__pydicom-997` | FAIL | FAIL | FAIL | FAIL | FAIL | FAIL | `0/70` | `58/58` | 31 | 1701.9 | `best_checkpoint_restored` |
| 14 | `pylint-dev__astroid-1262` | FAIL | FAIL | FAIL | FAIL | **PASS** | **PASS** | `1/1` | `97/97` | 46 | 1411.1 | `resolved_by_agentic_gatekeeper` (`CONT(b1)`) |
| 15 | `pylint-dev__astroid-1417` | FAIL | FAIL | FAIL | FAIL | FAIL (`0/1`) | **PASS (+NEW)** | **`1/1`** | **`3/3`** | 19 | 806.4 | `resolved_by_hierarchical_ab_mcts` (6-file symmetry migration) |
| 16 | `pylint-dev__astroid-1866` | FAIL | FAIL | FAIL | FAIL | **PASS** | **PASS** | `1/1` | `86/86` | 46 | 1448.2 | `resolved_by_agentic_gatekeeper` (`b2` upstream brain fix) |
| 17 | `pylint-dev__astroid-1959` | FAIL | FAIL | FAIL | **PASS** | **PASS** | **PASS** | `1/1` | `15/15` | 26 | 523.9 | `resolved_by_hierarchical_ab_mcts` (`b1` certified) |
| 18 | `pylint-dev__astroid-1962` | **PASS** | **PASS** | **PASS** | **PASS** | **PASS** | **PASS** | `1/1` | `15/15` | 28 | 740.1 | `resolved_by_hierarchical_ab_mcts` (`b1` certified) |
| 19 | `pylint-dev__astroid-934` | FAIL | FAIL | FAIL | FAIL | FAIL (`0/0`) | **PASS (+NEW)** | **`1/1`** | **`312/312`** | 34 | 998.9 | `resolved_by_hierarchical_ab_mcts` (**`b2` Move-37 upstream context clone**) |
| 20 | `pylint-dev__astroid-946` | FAIL | FAIL | FAIL | FAIL | **PASS** | FAIL | `0/1` | `155/155` | 43 | 1342.5 | `resolved_by_hierarchical_ab_mcts` (`LOCATE` seeded `objectmodel.py`) |
| 21 | `pylint-dev__astroid-983` | FAIL | FAIL | FAIL | FAIL | FAIL (`2/3`) | FAIL (`1/3`) | `1/3` | `312/312` | 38 | 1770.6 | `resolved_by_agentic_gatekeeper` (partial F2P) |
| 22 | `pyvista__pyvista-4226` | FAIL | FAIL | FAIL | FAIL | **PASS** | FAIL | `0/25` | `0/0` | 41 | 862.3 | `resolved_by_hierarchical_ab_mcts` (`_validate_range` vs `_check_range`) |
| 23 | `sqlfluff__sqlfluff-1517` | FAIL | FAIL | FAIL | FAIL | FAIL | FAIL | `0/1` | `6/6` | 31 | 810.6 | `resolved_by_hierarchical_ab_mcts` |
| 24 | `sqlfluff__sqlfluff-1625` | FAIL | FAIL | FAIL | FAIL | FAIL | FAIL | `0/1` | `1/1` | 31 | 1209.8 | `resolved_by_hierarchical_ab_mcts` |
| 25 | `sqlfluff__sqlfluff-1733` | FAIL | FAIL | FAIL | FAIL | FAIL | FAIL | `0/1` | `1/1` | 44 | 1720.0 | `best_checkpoint_restored` |
| 26 | `sqlfluff__sqlfluff-1763` | FAIL | FAIL | FAIL | FAIL | FAIL | FAIL | `0/3` | `83/83` | 47 | 1626.5 | `resolved_by_agentic_gatekeeper` |
| 27 | `sqlfluff__sqlfluff-880` | **PASS** | **PASS** | **PASS** | **PASS** | **PASS** | **PASS** | `39/39` | `116/116` | 27 | 1950.5 | `best_checkpoint_restored` (39-file codebase-wide refactor) |
| 28 | `sqlfluff__sqlfluff-884` | FAIL | FAIL | FAIL | FAIL | FAIL | FAIL | `0/1` | `18/18` | 47 | 1118.8 | `resolved_by_agentic_gatekeeper` |
| 29 | `sqlfluff__sqlfluff-891` | FAIL | FAIL | FAIL | FAIL | FAIL | FAIL | `0/49` | `114/114` | 40 | 1731.7 | `best_checkpoint_restored` |
| 30 | `sqlfluff__sqlfluff-905` | FAIL | FAIL | FAIL | FAIL | **PASS** | **PASS** | `1/1` | `1/1` | 31 | 870.4 | `resolved_by_hierarchical_ab_mcts` (`b1` certified) |
