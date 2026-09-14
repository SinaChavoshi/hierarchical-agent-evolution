# Execution-Grounded Re-Verification of Generations 5-10

**Status:** Complete. 60/60 archived firms re-scored.
**Date:** 2026-09-14
**Raw data:** [`execution_grounded_fitness.json`](execution_grounded_fitness.json) | [`execution_grounded_backfill.log`](execution_grounded_backfill.log)
**Reproduce:** `PYTHONPATH=. python3 scripts/backfill_execution_fitness.py --timeout 25`
(must run inside the project container image; the harness needs real `pip`, `pytest`, and `opentelemetry`)

---

## 1. Why this exists

Every fitness number published for Generations 5 through 10 was produced by
`src/sandbox_verifier.py`, which described itself as a "4-Gate Deterministic
Sandbox Verifier." It was not deterministic and it did not execute anything.
All four gates were heuristics:

| Legacy gate | What it actually did |
| :--- | :--- |
| **Build** | Checked whether a filename matching `pyproject.toml\|setup.py\|requirements.txt` appeared in the bundle. Contents were never parsed. |
| **Smoke** | Checked whether any filename ended in `.py`. |
| **Tests** | Ran pytest **if pytest happened to be installed**. When it was not — which was the common case — it fell back to a string match: `any("def test_" in c or "assert " in c for c in files)`. This reports `Tests: PASS` for a file that does not parse. |
| **Telemetry** | Substring search for `opentelemetry` **across the entire bundle including the CEO's prose deliverable**. A firm that merely *wrote the word* passed. |

These heuristics were replaced by [`src/execution_harness.py`](../src/execution_harness.py),
which runs the code. This document reports what happened when all 60 archived
firms were re-scored against it.

### Harness design rules

1. **A gate that cannot be evaluated returns `SKIPPED`, never `PASSED`.** The
   legacy verifier's failure mode was to guess in the candidate's favour.
2. **An uninstalled third-party dependency is an environment limitation, not a
   defect.** `import torch` failing where torch is absent yields `SKIPPED` with
   no penalty. A `SyntaxError` in agent-authored code yields `FAILED`.
3. Every `GateResult` records the `method` used, so a skip can never be
   silently reread as a pass.

A fifth gate, **syntax** (`ast.parse` over every authored `.py` file), is new.
Nothing in the legacy verifier ever checked that generated Python parses.

---

## 2. Headline result: legacy gates vs. executed gates

n = 10 firms per generation, 60 total. Read each cell as `legacy → executed`.

| Gen | Syntax (new) | Build | Smoke | Tests | Telemetry |
| :---: | :---: | :---: | :---: | :---: | :---: |
| **Gen 5** | 7/10 | 6 → 7 | 6 → 0 *(6 skip)* | 0 → 0 | 10 → **5** |
| **Gen 6** | 7/10 | 4 → 5 | 4 → 1 *(7 skip)* | 0 → 0 | 10 → **1** |
| **Gen 7** | 7/10 | 3 → 5 | 3 → 2 *(2 skip)* | 0 → 0 | 10 → **3** |
| **Gen 8** | **3/10** | 3 → 4 | 3 → 2 *(2 skip)* | 2 → 2 | 7 → **3** |
| **Gen 9** | 9/10 | 3 → 7 | 3 → 7 *(2 skip)* | 0 → 0 | 9 → **1** |
| **Gen 10** | 8/10 | **7 → 3** | 7 → 5 *(2 skip)* | 2 → **1** | 9 → **2** |
| **All 60** | **41/60** | 26 → 31 | 26 → 17 | 4 → **3** | **55 → 15** |

Three things to take from this table.

**Telemetry collapses from 55/60 to 15/60.** This is the single largest
correction in the project's history. The legacy gate searched the whole bundle
— including the CEO agent's strategy memo — for the string `opentelemetry`.
The executed gate requires an actual `import` node in a code file *and* a
tracer or span call site. 40 firms were passing this gate on prose.

**19 of 60 firms shipped Python that does not parse.** This was structurally
invisible before: no legacy gate parsed anything. Gen 8 is the worst cohort at
3/10, and — see §4 — the failures cluster on champions.

**Build genuinely improved, 26 → 31.** Not every correction is downward. The
legacy gate only looked for a manifest *filename*; the executed gate runs
`pip install -e . --no-deps`. Several firms that wrote no `pyproject.toml` but
did write a valid `setup.py`, or whose manifest the filename regex missed, pass
real installation. Gen 9 nearly doubles, 3 → 7.

> [!WARNING]
> **Do not compare `harness_net` to `legacy_net` directly.** Because a
> `SKIPPED` gate carries no penalty, a firm with many skips can score *higher*
> under the harness than under the legacy verifier despite executing less code.
> The net columns are recorded in the raw JSON for completeness but they are not
> an apples-to-apples series. **Compare gate pass rates.**

---

## 3. What the corrected data says about the evolutionary run

Define **Execution Score** = (gates genuinely passed) / (gates evaluable,
i.e. excluding skips), per firm, averaged over the cohort.

| Gen | Execution Score | Mean gates passed (of 5) | Firms ≥3 gates | Firms with **zero** gates | Legacy net avg |
| :---: | :---: | :---: | :---: | :---: | :---: |
| Gen 5 | 46.5% | 1.90 | 5 | 3 | 81.34 |
| Gen 6 | 34.0% | 1.40 | 1 | 2 | 72.25 |
| Gen 7 | 36.5% | 1.70 | 2 | 2 | 70.36 |
| Gen 8 | 29.0% | 1.40 | 1 | 1 | 68.98 |
| Gen 9 | **49.5%** | **2.40** | **6** | 1 | 73.24 |
| Gen 10 | 40.0% | 1.90 | 4 | 1 | 84.52 |

### 3.1 Six generations of selection produced no measurable gain in executability

- `corr(generation, execution_score) = +0.045`
- Linear trend: **+0.19 percentage points per generation** — indistinguishable
  from flat against a cohort-to-cohort spread of 20 points.

Over the same span the published legacy net climbed at +0.50 points/generation
and the champion headline went 91.93 → 91.26 with a peak of 91.26 in Gen 10.
**The reported fitness improved. The software did not.**

### 3.2 The fitness function was only weakly coupled to whether the code worked

Across all 60 firms:

| Correlation | Value |
| :--- | :---: |
| `corr(legacy_net, gates_passed)` | **+0.286** |
| `corr(legacy_net, execution_score)` | +0.299 |
| `corr(gross_judge_score, gates_passed)` | +0.201 |
| `corr(gross_judge_score, execution_score)` | +0.178 |

The selection signal explains roughly **8% of the variance** in whether a firm's
code executes. The LLM judge — which supplies 100% of the gross score and has no
execution dimension in its rubric (`src/evaluator.py:89-99`) — explains **4%**.

This is the quantitative form of the user-facing question *"is the quality of
agent output actually improving?"* The answer for Generations 5–10 is: **the
ranking we bred on was close to orthogonal to working software.**

---

## 4. Specific published claims, corrected

### 4.1 RETRACTED — Gen 10's "first zero-penalty four-gate clearance"

`gen_10_consensus_1` (legacy net 89.23) was published as the first firm in
benchmark history to clear all four gates with zero penalty. **It does not
survive execution.**

| Gate | Legacy | Executed | Evidence |
| :--- | :---: | :---: | :--- |
| Syntax | *(n/a)* | **PASS** | All files parse. |
| Build | PASS | **PASS** | Editable install succeeded. |
| Smoke | PASS | **PASS** | 4/4 modules imported. |
| Tests | PASS | **FAIL** | `ERROR tests/test_agent_failure_introspection.py \| ERROR tests/test_economic_attack.py \| Interrupted: 2 errors during collection` |
| Telemetry | PASS | **FAIL** | *No opentelemetry import in any authored Python file.* |

The tests gate passed on the string-match fallback. The telemetry gate passed on
prose. The milestone is withdrawn. Its genuine achievement — **3/5 gates
including a clean 4/4 smoke import, the only perfect smoke result in Gen 10** —
stands and is worth keeping.

### 4.2 CONFIRMED — Gen 8's first live `Tests: PASS`

This one holds. Under real pytest in the container:

- `gen_8_consensus_2` — `All tests passed. x [100%] | 1 xfailed in 0.06s`
- `gen_8_mutant_2` — `All tests passed. . [100%] | 1 passed in 0.01s`

Both genuinely invoke pytest and both come back green. Gen 8 is the first
generation with a real passing test suite, exactly as published.

Two caveats worth stating rather than burying. `gen_8_consensus_2`'s suite
consists of a single `xfail` — pytest is satisfied, but zero assertions
actually ran. And both firms **fail the new syntax gate**:
`gen_8_mutant_2`'s `run_test_cycle.py:1: unexpected indent`,
`gen_8_consensus_2`'s `llm_client.py:97: unterminated string literal`. They ship
working tests alongside unparseable source.

### 4.3 NEW — Gen 9's champion passes zero of five gates

`gen_9_consensus_1` scored **87.68** net, the second-highest in project history,
and was bred forward as the Gen 9 champion. Executed:

```
syntax    FAIL  agent_org/core.py:1: unexpected indent
build     FAIL
smoke     FAIL  agent_org.core: IndentationError (core.py, line 1)
tests     FAIL  ERROR tests/test_core.py | Interrupted: 1 error during collection
telemetry FAIL  No opentelemetry import in any authored Python file.
```

Its core module is an indented fragment — a method body emitted without its
enclosing class. Nothing in the legacy pipeline could see this, because nothing
parsed the file. It won on judge prose and a filename-matching build gate.

It is not alone. Ten firms pass zero gates, and the list is not sorted by
fitness:

| Firm | Gen | Legacy net |
| :--- | :---: | :---: |
| `gen_9_consensus_1` | 9 | **87.68** ← Gen 9 champion |
| `gen_5_consensus_2` | 5 | **85.13** |
| `gen_5_mutant_2` | 5 | 77.87 |
| `gen_10_elite_2` | 10 | 76.84 |
| `gen_5_elite_1` | 5 | 69.36 |
| `gen_7_elite_1` | 7 | 68.08 |
| `gen_6_pareto_bonus_2` | 6 | 64.27 |
| `gen_6_elite_2` | 6 | 62.96 |
| `gen_8_mutant_1` | 8 | 60.90 |
| `gen_7_elite_2` | 7 | 55.78 |

### 4.4 The honest leaderboard

Ranked by gates genuinely passed rather than by judge score:

| Rank | Firm | Gen | Gates passed | Which | Legacy net |
| :---: | :--- | :---: | :---: | :--- | :---: |
| 1 | `gen_9_pareto_bonus_2` | 9 | **4/5** | syntax, build, smoke, telemetry | 75.17 |
| 2 | `gen_9_mutant_1` | 9 | 3/5 | syntax, build, smoke | 81.00 |
| 3 | `gen_9_consensus_2` | 9 | 3/5 | syntax, build, smoke | 75.54 |
| 4 | `gen_9_elite_2` | 9 | 3/5 | syntax, build, smoke | 75.50 |
| 5 | `gen_7_pareto_bonus_1` | 7 | 3/5 | syntax, build, telemetry | 75.14 |
| 6 | `gen_9_elite_1` | 9 | 3/5 | syntax, build, smoke | 69.39 |
| 7 | `gen_8_consensus_2` | 8 | 3/5 | build, tests, telemetry | 71.79 |
| 8 | `gen_9_pareto_bonus_1` | 9 | 3/5 | syntax, build, smoke | 63.76 |

**No firm in 60 has ever passed all five gates.** The best result in project
history is 4/5, from a Gen 9 firm that ranked *sixth* in its own cohort by
legacy fitness and was never selected as a parent.

Note also what §3 obscures: Gen 9 has both the strongest executed cohort
(6 firms at ≥3 gates, an Execution Score of 49.5%) and a champion at 0/5. The
morphogenesis generation genuinely produced the best software in the project.
The fitness function then picked the one firm in the cohort that didn't work.

---

## 5. Consequences for the pipeline

1. **`src/sandbox_verifier.py` is rewritten** to delegate to the harness.
   Landed in commit `0393889`. No heuristic gates remain.
2. **The judge rubric still has no execution dimension.** Until it does, the
   penalty term (62% of score variance in Gen 10) is the only part of fitness
   coupled to reality, and it is coupled at r = +0.29. This is the next fix.
3. **`src/evaluator.py:77-87` silently substitutes `70/70/70/65/70`** when an
   evaluation fails to parse, admitting failed evaluations into the breeding
   pool undetected. This must be removed before Generation 11 is bred.
4. **Champion selection should report the executed gate profile** alongside the
   net score. Gen 9 would not have bred `gen_9_consensus_1` forward had the
   0/5 profile been visible.
5. **Generation 11 must be bred under the harness**, not the legacy verifier,
   or this correction will simply need to be repeated.

---

## 6. Re-scoring the archive under the rebuilt fitness function

Sections 2–4 report what the *gates* say. This section asks the sharper
question: **what would the leaderboard have looked like if the score had ever
been coupled to whether the code ran?**

`src/evaluator.py` has been rebuilt (see §5). The rubric is now:

| Dimension | Old weight | New weight | Source |
| :--- | :---: | :---: | :--- |
| `strategic_depth` | 25% | 20% | LLM judge |
| `technical_feasibility` | 25% | 20% | LLM judge |
| `cross_functional_coherence` | 20% | **10%** | LLM judge |
| `risk_mitigation` | 15% | 10% | LLM judge |
| `actionability_and_synthesis` | 15% | **10%** | LLM judge |
| **`execution_integrity`** | — | **30%** | **Measured by the harness** |

Coherence and actionability are demoted because they are saturated: both sat at
a mean of 99.0 with σ 1.67 across Generation 10 while carrying 35% of the weight
between them. `execution_integrity` is computed deterministically from gate
results — tests 30, syntax 20, smoke 20, build 15, telemetry 15, renormalised
over evaluable gates — and the judge is explicitly told it cannot see or predict
it.

Applying this to all 60 archived firms, using each scorecard's own per-dimension
judge scores and the executed gate results from §2 — no re-runs, no new
inference:

Reproduce with `PYTHONPATH=. python3 scripts/rescore_under_new_rubric.py`.
Output: [`rubric_rescore.json`](rubric_rescore.json).

### 6.1 Five of six champions change

| Gen | Champion we bred | Its rank under the rebuilt rubric | Champion we *should* have bred | Δ |
| :---: | :--- | :---: | :--- | :---: |
| Gen 5 | `gen_5_mutant_3` (91.93) | **#1** of 10 ✅ | `gen_5_mutant_3` (87.15) | — |
| Gen 6 | `gen_6_elite_1` (91.87) | #3 | `gen_6_consensus_3` (80.22) | −13.27 |
| Gen 7 | `gen_7_mutant_1` (82.70) | #5 | `gen_7_pareto_bonus_1` (85.05) | −10.40 |
| Gen 8 | `gen_8_pareto_bonus_2` (79.89) | #9 | `gen_8_consensus_2` (76.30) | −15.27 |
| Gen 9 | `gen_9_consensus_1` (87.68) | **#10 of 10** | `gen_9_pareto_bonus_2` (86.00) | **−22.38** |
| Gen 10 | `gen_10_mutant_3` (91.26) | #2 | `gen_10_elite_1` (89.10) | −4.91 |

This is the most consequential finding in the correction. The champions were not
merely mis-ranked — **in Generation 9 the firm we selected as the sole parent of
the next generation was the worst firm in its cohort**, at `execution_integrity`
0.0. Generation 8's champion ranked ninth of ten. Every genome bred from
Generation 6 onward descends from a lineage chosen this way.

Only Generation 5's champion survives selection under the honest function, and
Generation 10's is close, at #2.

### 6.2 The two rubrics agree only moderately

`corr(legacy_net, rubric_score)` over 60 firms = **+0.650**. Substantial overlap
— prose quality and code quality are not independent — but 58% of the ranking
variance is unexplained, which is where the five champion changes come from.

Largest movers:

| Firm | Gen | Legacy | Rebuilt | `execution_integrity` | Δ |
| :--- | :---: | :---: | :---: | :---: | :---: |
| `gen_9_consensus_1` | 9 | 87.68 | 65.30 | **0.0** | **−22.38** |
| `gen_5_consensus_2` | 5 | 85.13 | 63.50 | **0.0** | −21.63 |
| `gen_8_pareto_bonus_2` | 8 | 79.89 | 64.62 | 18.75 | −15.27 |
| `gen_8_mutant_1` | 8 | 60.90 | 47.40 | 0.0 | −13.50 |
| `gen_6_elite_1` | 6 | 91.87 | 78.60 | 35.0 | −13.27 |
| `gen_9_elite_1` | 9 | 69.39 | **82.90** | 55.0 | **+13.51** |
| `gen_9_pareto_bonus_2` | 9 | 75.17 | **86.00** | **70.0** | +10.83 |
| `gen_8_consensus_1` | 8 | 58.38 | 69.30 | 40.0 | +10.92 |
| `gen_7_pareto_bonus_2` | 7 | 48.69 | 59.12 | 43.75 | +10.43 |
| `gen_5_mutant_1` | 5 | 75.87 | 86.25 | 62.5 | +10.38 |

The pattern is clean: firms that wrote working code and described it plainly
were undervalued; firms that wrote a compelling memo over dead code were
overvalued by up to 22 points.

### 6.3 The trend is still flat

| Gen | Legacy mean | Rebuilt mean |
| :---: | :---: | :---: |
| Gen 5 | 81.34 | 77.04 |
| Gen 6 | 72.25 | 70.07 |
| Gen 7 | 70.36 | 71.69 |
| Gen 8 | 68.98 | 67.05 |
| Gen 9 | 73.24 | 75.92 |
| Gen 10 | 84.52 | 77.30 |

Trend: **+0.41 points/generation** under the rebuilt rubric versus +0.50 under
the legacy one. Correcting the fitness function does not retroactively produce
improvement that was not there. It makes the absence of improvement legible, and
it explains the mechanism: for five of six generations we bred from the wrong
parent.

> [!NOTE]
> The rebuilt scores are **not** a new official leaderboard for Generations
> 5–10. They are a counterfactual computed from archived judge scores under new
> weights. The judge's prompt also changed — it is now told to use the full
> 0–100 range and not to credit claimed testing — so live Generation 11 scores
> will differ from this counterfactual as well as from the legacy series.
