# exp-013 — Generation 11 (Parallel, us-east4) — **FAILED RUN**

> [!CAUTION]
> **This generation did not complete and produced no valid generation-level score.**
> It is recorded here because the *way* it failed is the most informative result in
> the V1 series. Do not cite a "Generation 11 fitness" figure — none exists.

| Field | Value |
|---|---|
| Status | **Aborted** (job deleted 2026-09-14T23:57Z) |
| Started | 2026-09-14T23:28:50Z |
| Population | 10 firms, parallelism 3, indexed Job |
| Image | `agent-evolution:gen11` (pre-refactor, pydantic schema) |
| Completed | 3 / 10 |
| Permanently dead | 3 / 10 (deterministic loader crash) |
| Killed in flight | 4 / 10 |

---

## 1. What happened

Six pods failed with an identical, deterministic crash before any LLM call:

```
pydantic_core.ValidationError: 2 validation errors for CompanyGenome
departments.5.agents.0.tools_enabled
  Input should be a valid list [type=list_type, input_value=True, input_type=bool]
departments.5.agents.1.tools_enabled
  Input should be a valid list [type=list_type, input_value=True, input_type=bool]
```

Six failures, three firms, two attempts each (`backoffLimit`).

## 2. Root cause

`AgentGenome.tools_enabled` was declared `List[str]`. No genome in any generation
ever named a tool, and every consumer in the codebase only ever read
`bool(agent.tools_enabled)`. The field was, in practice, a boolean wearing a list's
type annotation.

`morphogenesis.py` — the Generation 9 "Autonomous Morphogenesis & Dynamic
Topologies" capability — writes a raw `True` into that field when it synthesises a
new agent. That is the correct *value* for how the field is actually used, and the
wrong *type* for how it was declared.

The bug had been latent since Generation 9. It never fired because morphogenesis was
effectively inert: it was reachable only from one-off breeding scripts, and no
generation before 11 shipped a genome it had touched. **Generation 11 is the first
population containing real morphogenesis output, so it is the first population the
loader rejects.**

## 3. Which firms died

Audited across every archived population in `experiments/v1/configs/`:

| Generation | Firms with `tools_enabled: true` |
|---|---|
| 1–10 | **0** |
| 11 | **3** |

| Index | `company_id` | Bad agents | Lineage operator |
|---|---|---|---|
| 5 | `gen_11_pareto_bonus_1` | 2 | pareto |
| 7 | `gen_11_mutant_1` | 2 | mutant (morphogenesis) |
| 8 | `gen_11_mutant_2` | 2 | mutant (morphogenesis) |

## 4. Why the run was aborted rather than harvested

The three dead firms are **the only three structurally novel topologies in the
population**. Everything that survived is an elite or a crossover — the conservative
half, carrying topologies already measured in Generation 10.

Harvesting the survivors would have produced a "Generation 11 score" computed
exclusively from the firms that changed least, while silently dropping every firm
that explored. That is the same survivorship bias that forced the retraction of the
Generation 9 and Generation 10 headline claims (see
[`execution_grounded_correction.md`](../execution_grounded_correction.md)). Reporting
it would have repeated the exact error this project spent a week correcting.

The run was therefore terminated and recorded as a failure.

## 5. Partial data (3 firms — **not** a generation score)

Retained in [`scorecards/`](scorecards/) for forensic value only. These three are
not a representative sample and must not be averaged into the trajectory.

| Firm | Gross | Net | Syntax | Build | Smoke | Tests | Telemetry | Penalty | Authored |
|---|---|---|---|---|---|---|---|---|---|
| `gen_11_elite_1` | 74.7 | 74.97 | PASS | PASS | PASS | **FAIL** | PASS | 6.25 | 3 (2 .py) |
| `gen_11_consensus_1` | 68.3 | 68.04 | PASS | PASS | FAIL | **FAIL** | PASS | 12.50 | 10 (4 .py) |
| `gen_11_elite_2` | 61.6 | 61.75 | **FAIL** | PASS | FAIL | **FAIL** | PASS | 18.75 | 4 (3 .py) |

Two observations worth carrying into V2:

- **`technical_feasibility` collapsed to 30–35** across all three, against
  `cross_functional_coherence` 98 and `actionability` 97–99. The rebuilt rubric is
  discriminating on the one dimension that tracks whether the thing works, while the
  prose dimensions remain saturated. This is the intended behaviour of the new
  weights, observed live for the first time.
- **No firm passed the `tests` gate.** Three for three. The execution gap documented
  in the V1 correction is not closing on its own.

## 6. Disposition

- Fixed in the V2 refactor: `tools_enabled` is now typed `bool`, with coercion for
  archived list-shaped genomes, and the schema validates on construction instead of
  accepting whatever it is handed.
- The V2 schema is stdlib dataclasses with explicit validation; a type/value mismatch
  of this kind now raises at breed time, in-process, rather than at pod start.
- Generation 11 is **not** re-run under V1. The V1 experiment set closes at
  Generation 10.

## 7. The lesson

A capability that is never exercised is not a capability. Morphogenesis shipped in
Generation 9, was described in the README as a headline feature for two generations,
and was carrying a crash the whole time. The first moment it did real work, it took
down 30% of the population.

The V2 architectural guard (`tests/test_architecture.py`) exists because of this
class of failure: it walks the import graph from the entry points and fails the build
on any module nothing can reach. Run against the V1 tree it flags five orphans,
morphogenesis among them.
