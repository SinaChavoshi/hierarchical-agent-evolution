# Phase 6 (`V6`): Epistemic Tree-Search Organizations — *"The Scientific Method on Steroids"*

> **Status:** Design finalized `October 5, 2026` · **Stage 1 & 2 implemented and tested** (`October 6, 2026`: `hae/epistemic/` engine, `epistemic_policy` gene, runner/worker/breeder integration, audit-based fitness; `443` tests, `$0` GPU) · **Gen 16 pilot run `October 6, 2026`** (4 plateau genomes, 3 passes, ≈ 1.7 spot-GPU-hours, cluster deleted afterwards): one plateaued genome converged to `50/50` under `V6` via a `0.10`-prior hypothesis; five engine defects found live and fixed in-tree — see **§3.3** and [`results/hae_gen16_v6_pilot/README.md`](../../results/hae_gen16_v6_pilot/README.md) · **Stage 3 (full cohort) pending** finding #6 and a multi-seed design
> **Trigger:** Thore Graepel (AlphaGo core team, now Chair of Machine Learning at UCL), *["Don't be fooled—LLMs don't reason"](https://www.technologyreview.com/2026/10/02/1145639/dont-be-fooled-llms-dont-reason/)*, MIT Technology Review, October 2, 2026
> **Empirical Motivation:** Generation 15 plateau analysis ([`results/hae_gen15_fullstack_v5/generation_15_summary.json`](../../results/hae_gen15_fullstack_v5/generation_15_summary.json))

---

## 1. Why `V6` Exists: What Generation 15 Taught Us About the Limits of Linear LLM Self-Repair

Generation 15 was our most successful run to date (`10 / 10` firms at `46–50 / 50`, two firms at `50 / 50`). It also produced the clearest evidence yet that **our repair loop is a 1-D greedy hill-climber driven purely by LLM intuition**, and that this architecture has a hard ceiling:

| Symptom in Gen 15 | Evidence | Diagnosis |
| :--- | :--- | :--- |
| **Flat-line repair trajectories** | `gen_14_elite_1` `[49, 49, 49, 49, 49]`, `gen_14_crossover_1` `[49, 49, 49, 49, 49]`, `gen_14_mutant_1` `[48, 48, 48, 48, 48]`, `gen_14_elite_2` `[46, 46, 46, 46, 46]` — **4 firms made zero progress across 16 combined repair iterations.** After Iteration 2, **`8 / 8` non-converged firms were stuck** (only `gen_14_mutant_2` gained `+2` tests at Iteration 4). | At `temperature=0.1`, the Lead Engineer re-emits the same highest-probability patch every iteration. The loop has **no memory of what it already tried and why it failed** (no *ruled-out* ledger), so it cannot pivot. |
| **The same wrong mechanism across independent firms** | `test_pytest_style_functions_are_collected` failed in **`7 / 8`** non-converged firm snapshots; `test_unittest_style_classes_are_collected` failed in **`6 / 8`**. | This is Graepel's *Move 37 problem* in reverse: the LLM's policy prior strongly favours one plausible-but-wrong implementation of stdlib test collection. A system that only samples **high-prior** moves never explores the low-prior alternative that actually passes. |
| **Repair only works when an oracle hands over the traceback** | Every `+36` / `+41` test jump (`gen_14_crossover_3`, `gen_14_pareto_2`, `gen_14_mutant_2`) happened **after** the external benchmark returned exact `AssertionError` diffs. | Firms never **ask their own questions** or **run their own falsification experiments**. Remove the oracle (as in real engineering) and the loop has no mechanism for reducing uncertainty. |
| **`50%` of fitness graded post-hoc prose** | `judge.py` scores `ExecutiveDeliverablePacket` narratives that are generated **after** the code exists. | Graepel: LLMs *"concoct [chains of thought] after the fact, reaching an answer by one route but reporting another."* We are rewarding a convincing story, not an auditable reasoning trail. |

---

## 2. The Architectural Diff: Our Previous "AlphaGo-Style" Plan vs. What AlphaGo Actually Does

Before reading Graepel's essay, our roadmap for "100+ internal iterations" was **MCTS over code snapshots scored by unit tests, with the CEO LLM assigning policy priors $P(s,a)$ and an LLM/heuristic critic as $V(s)$**. Graepel's account of AlphaGo exposes three fundamental errors in that framing:

```diff
- [PREVIOUS PLAN]  State s_t  = workspace code snapshot + binary test pass/fail vector
+ [GRAEPEL / V6]   State E_t  = explicit, persistent, INSPECTABLE EPISTEMIC LEDGER:
+                               { settled_knowledge (certified facts),
+                                 hypotheses (with calibrated confidence),
+                                 ruled_out (falsified beliefs + the evidence that killed them),
+                                 open_questions (unresolved uncertainty) }

- [PREVIOUS PLAN]  Move a_t   = "LLM rewrites a module file -> run external benchmark"
+ [GRAEPEL / V6]   Move a_t   = EPISTEMIC OPERATOR that changes E_t to reduce uncertainty:
+                               ASK / DECOMPOSE -> HYPOTHESIZE (k competing, incl. low-prior)
+                               -> EXPERIMENT (sandbox probe that can falsify) -> REVISE
+                               -> SYNTHESIZE code strictly from settled knowledge

- [PREVIOUS PLAN]  Value V(s) = held-out test count + LLM/AST heuristic critic
+ [GRAEPEL / V6]   Value V(E) = INDEPENDENT, NON-LLM evaluator of "how much uncertainty this
+                               move actually resolved, backed by evidence" + a learned
+                               value head trained on past epistemic search trees
```

| Dimension | ❌ Previous Plan & Current `V5` Runtime | ✅ Graepel's AlphaGo Architecture (`V6`) | Consequence |
| :--- | :--- | :--- | :--- |
| **System 1 / System 2 boundary** | CEO directives, specialist JSON packets, and chain-of-thought were treated as *deliberation*. | **The LLM is 100% System 1.** Chain-of-thought is *"the same next-token prediction process, iterated for longer."* System 2 must be **explicit, non-LLM machinery outside the weights** (AlphaGo's game tree + search). | Stacking more LLM calls on top of LLM calls does not create reasoning. The *organization* must be the System 2. |
| **Who edits beliefs** | Any agent could assert "fixed" / "verified" in prose. | *"An **independent part of the system** must evaluate each move by how much it actually resolves uncertainty, **updating beliefs only when the change is backed by evidence**."* | LLMs may **propose**; only a deterministic **Evidence Gatekeeper** running sandboxed probes may **certify** or **falsify**. |
| **Policy prior & Move 37** | CEO LLM assigns $P(s,a)$ and prunes low-plausibility branches. | AlphaGo's policy net gave Move 37 a **1-in-10,000** prior. Search found it **because it looked past plausibility** and evaluated consequences. | LLM-plausibility pruning systematically kills every "Move 37". `V6` deliberately expands **mutually exclusive, low-prior hypotheses** and lets cheap experiments — not the LLM — decide. |
| **Memory of dead ends** | Only `best_workspace_files` is kept; failed patches are forgotten. | The game tree retains **every variation considered**, annotated with evaluations. | `ruled_out` is a first-class ledger section; proposals matching a falsified mechanism are rejected before spending a turn. |
| **Auditability** | Fitness partly graded on post-hoc narrative. | *"Conclusions arise from an auditable sequence of evidence, inference, and belief revision rather than from a convincing story told after the fact."* | Replace prose rubric with an **Epistemic Integrity Audit** of the ledger (claims backed by certified experiments, calibration, no re-attempted falsified ideas). |

---

## 3. `V6` Architecture: The Company *Is* the System 2

```mermaid
flowchart TD
    subgraph S2["System 2 — Explicit Epistemic Search Engine (deterministic Python, hae/epistemic/)"]
        Ledger["EpistemicState E_t (content-addressed ledger)\n• settled_knowledge: CertifiedFact[]\n• hypotheses: Hypothesis[] (status + calibrated confidence)\n• ruled_out: FalsifiedBelief[] (+ killing evidence)\n• open_questions: Question[] (+ uncertainty)"]
        Tree["Epistemic PUCT Tree Search\nnodes = E_t, edges = epistemic moves\n50–100+ moves per company"]
        Gate["Independent Evidence Gatekeeper\n• runs sandbox probes / module checks\n• enforces evidence-backed belief revision\n• computes information gain ΔU"]
        Value["Epistemic Value Function V(E_t)\n= α · (uncertainty resolved by evidence)\n+ (1-α) · learned value head V̂θ(φ(E_t))"]
    end
    subgraph S1["System 1 — LLM Intuition Generators (Qwen3.8-180B, xgrammar-typed)"]
        Q["Question / Decomposer Pod\n'what is ambiguous or untested in E_t?'"]
        H["Hypothesis Pod\n'k mutually-exclusive mechanisms,\nincluding low-prior alternatives'"]
        X["Experiment Designer Pod\n'≤30-line sandbox probe that\nfalsifies H_i vs H_j'"]
        B["Synthesis Engineer\n'compile settled knowledge\ninto the target module'"]
    end
    Ledger --> Tree
    Tree -->|select open question| Q
    Tree -->|expand| H
    H --> X
    X -->|probe| Gate
    B -->|candidate module| Gate
    Gate -->|certified evidence only| Ledger
    Value --> Tree
```

### 3.1 Stage 1 — Core Engine (`hae/epistemic/`, CPU-only, `$0` GPU)

| Module | Responsibility | Key Invariants |
| :--- | :--- | :--- |
| [`hae/epistemic/ledger.py`](../../hae/epistemic/ledger.py) | `EpistemicState`, `Question`, `Hypothesis`, `CertifiedFact`, `FalsifiedBelief`, `Evidence`. Immutable, content-addressed (`state_hash`), JSON-serialisable, fully inspectable. | Confidence fields are **read-only to LLM proposals**; only `Gatekeeper` transitions mutate status. Every `CertifiedFact` / `FalsifiedBelief` carries the hash of the evidence that produced it. |
| [`hae/epistemic/moves.py`](../../hae/epistemic/moves.py) | Typed epistemic operators: `AskQuestion`, `ProposeHypothesis`, `ProposeExperiment`, `ProposeCodeDiff`, `Synthesize`. Each is an `xgrammar`-enforceable JSON schema the LLM may emit. | A move **proposes**; it never asserts truth. Proposals whose mechanism signature matches a `ruled_out` entry are rejected with `TABU`. |
| [`hae/epistemic/gatekeeper.py`](../../hae/epistemic/gatekeeper.py) | Independent evidence evaluator: executes probes in a disposable **stage** (reference `hae/` tree with the graded modules removed + the firm's workspace overlaid, `PYTHONPATH` pinned, `unshare -rn` network isolation, stdlib-only interpreter), applies deterministic belief-revision rules (`SUPPORTED` / `FALSIFIED` / `CERTIFIED`), computes information gain $\Delta U$, and reconciles the ledger with the oracle between iterations. | **No LLM call inside the gatekeeper.** Belief updates require an `Evidence` record with `exit_code`, `stdout/stderr`, prediction match. Vacuous predictions cannot support; timeouts are `INCONCLUSIVE`, never evidence. Probes that reach for `/app`, `sys.path`, `held_out`, the network or `inspect.getsource` are rejected before they run. |
| [`hae/epistemic/value.py`](../../hae/epistemic/value.py) | Epistemic value function: Stage-A feature-engineered (uncertainty resolved, ruled-out density, stagnation depth, module isolation); Stage-B learned head $\hat V_\theta$ (pure-Python logistic, JSON-serialisable) trained on harvested search trees. | Calibration is scored (Brier) against terminal outcomes; miscalibration is a fitness penalty, not a free lunch. |
| [`hae/epistemic/mcts.py`](../../hae/epistemic/mcts.py) | PUCT tree search over `EpistemicState` nodes; a **question frontier** (≤ `frontier_size` open questions at once, one per cluster of *module + normalised oracle failure*, next move to the question closest to paying off) with **triage** (siblings of a question resolved in this run are deferred to the oracle); forced exploration of low-prior siblings (`low_prior_quota`); dead-end back-propagation; tabu memory; stop reasons `budget_moves · stagnation · all_resolved · exhausted · deferred_to_oracle · token_budget`. | Policy priors are **only** an exploration tie-breaker — never a pruning filter (Move 37 rule). Synthesis is only offered for a `SUPPORTED` hypothesis, and only after `min_hypotheses_before_synthesis` siblings have been tested. |
| [`hae/epistemic/audit.py`](../../hae/epistemic/audit.py) | `build_epistemic_audit(state, search_stats, budget_moves, token_usage)`: the non-LLM audit of the reasoning trail — evidence-backed fraction, status inconsistencies, Brier calibration of System 1 priors vs. gatekeeper verdicts, certified ratio, tabu discipline, resolved fraction, `ledger_hash`. | Pure function of the ledger. This is what fitness reads; prose never enters it. |
| [`hae/epistemic/genes.py`](../../hae/epistemic/genes.py) | `mutate_epistemic_policy` (bounded jitter + rare role re-binding) and `crossover_epistemic_policy` (per-field uniform / numeric midpoint) for the `epistemic_policy` gene. | Operators never flip `enabled`; that is a generation-level decision (`GenerationSpec.epistemic_policy`). Breeding is seeded per slot, so a spec always breeds the same child. |

### 3.2 Stage 2 — Organizational Integration (`company.py`, `schema.py`, `worker.py`, `breeder.py`, `judge.py`) — *implemented*

1. **`CompanyGenome.epistemic_policy`** ([`schema.py`](../../hae/genome/schema.py), `EpistemicPolicyGene`) — heritable, mutable, crossover-able. Fields and bounds: `enabled` (default `False`), `search_budget_moves` `[4, 400]` (default `40`), `branching_k` `[2, 6]`, `c_puct` `[0.1, 5.0]`, `low_prior_quota` `[0.0, 0.8]` (default `0.34` — the Move-37 quota), `experiment_timeout_s`, `max_probe_lines`, `value_alpha`, `min_hypotheses_before_synthesis`, `max_stagnant_moves`, `max_hypothesis_rounds`, `frontier_size` `[1, 8]` (default `3` — how many open questions the search works on at once; see finding #6), and `role_bindings` (`question → qa`, `hypothesis → engineering`, `experiment → verification`, `synthesis → engineering`; matched against department id/name/mandate so morphogenesis-invented pods still route). Genomes from Gen ≤ 15 deserialise with the default gene and behave exactly as before.
2. **`HierarchicalCompanyRunner.run_epistemic_search(objective, failures, iteration)`** ([`company.py`](../../hae/runtime/company.py)) — one repair iteration as a tree-search: the oracle's failure lines seed or update the ledger (`reconcile_epistemic_state`), the bound departments propose `HYPOTHESIS_SET` packets (an `xgrammar` schema, like every `V5` packet), the gatekeeper runs each probe, and a patch is synthesised through the existing tool loop **only** for a hypothesis that survived. Returns the same record shape as `run()` plus `epistemic_ledger` / `epistemic_search`, so every downstream consumer of a scorecard keeps working. `run()` itself is untouched.
3. **Worker dispatch** ([`worker.py`](../../hae/orchestration/worker.py)) — iteration 1 is always `run()`; iterations `2..N` go to `run_epistemic_search()` when the gene is enabled. After the loop the ledger is reconciled with the oracle's *final* verdict (vanished failures → `CERTIFIED` by oracle evidence; still-failing "resolved" questions → reopened and their patches `FALSIFIED`), the non-LLM audit is computed and handed to the evaluator, and the full trail is written to `<company_id>_epistemic_tree.json` next to the scorecard.
4. **Breeder** ([`breeder.py`](../../hae/orchestration/breeder.py)) — crossovers blend the parents' policies, mutants perturb them, elites/pareto clones carry them verbatim; `GenerationSpec.epistemic_policy` applies cohort-level overrides (`{"enabled": true, ...}`) after inheritance and refuses unknown or out-of-range fields. `rank_scorecards` ranks `V6` scorecards by the same audit-weighted composite the worker scored with; `V5` scorecards rank exactly as before.
5. **Fitness** ([`judge.py`](../../hae/evaluation/judge.py)) — when a ledger exists, `composite_score` becomes `EPISTEMIC_WEIGHTS`: **`60%` held-out execution · `25%` ledger integrity** (`30%` evidence backing & status consistency, `25%` Brier calibration of System 1 priors against gatekeeper verdicts, `30%` oracle-certified ratio, `15%` tabu discipline) **· `15%` search efficiency** (`60%` uncertainty resolved, `40%` resolved per unit of move budget). The prose rubric is still *reported* on the scorecard for continuity but has **zero weight** in a `V6` composite. Without a ledger the `V5` composite is used unchanged.

### 3.3 Stage 3 — Generation 16

#### 3.3.1 The pilot (`October 6, 2026`) — *run; report in [`results/hae_gen16_v6_pilot/`](../../results/hae_gen16_v6_pilot/README.md)*

Before any cohort, four Gen-14 genomes that plateaued under `V5` in Gen 15 were re-run with **one field changed** — `epistemic_policy.enabled = true` — on the same task, model, verifier and objective ([`configs/generation_16_pilot_population.json`](../../configs/generation_16_pilot_population.json)). Infra: a throw-away GKE cluster with one spot `g4-standard-96` serving one vLLM replica ([`k8s/gen16-pilot-serving.yaml`](../../k8s/gen16-pilot-serving.yaml)), Jobs [`k8s/hae-gen16-v6-pilot{,-r2,-r3}-job.yaml`](../../k8s/), results harvested from the pod logs. The cluster was deleted when the last firm finished.

| firm | Gen 15 (`V5`) held-out trajectory | `V6` pilot, working code (pass 2) |
|---|---|---|
| `gen_14_crossover_2` | `[47, 49, 49, 49, 49]` | **`[48, 48, 50]` — converged at iteration 3** on a patch synthesised from a `0.10`-prior hypothesis the gatekeeper SUPPORTED after the `0.75`-prior one was falsified; both questions oracle-`CERTIFIED` |
| `gen_14_mutant_1` | `[48, 48, 48, 48, 48]` | `[49, 49, 49, 49, 49]` — 18 hypotheses, 18 falsified (two of them *after* a module-check-passing synthesis, by the oracle's final verdict) |
| `gen_14_elite_1` | `[49, 49, 49, 49, 49]` | `[48, 48, 48, 48, 48]` — one SUPPORTED hypothesis never synthesised: the search starved on refused probes (finding #3) |
| `gen_14_elite_2` | `[46, 46, 46, 46, 46]` | `[50]` at iteration 1 (pass 1) — the unchanged `V5` first pass; the search never ran |

The pilot took three passes because the first two surfaced defects that only a live grammar-constrained decoder and a live proposer could expose. Each was fixed in-tree with tests before the next pass:

| # | finding | fix |
|---|---|---|
| 1 | `xgrammar` cannot emit a multi-line `probe_code` string — every hypothesis packet degenerated into a repetition loop until `max_tokens`; pass 1 tested **zero** hypotheses | probe as a `probe_lines` array, truncated-packet salvage (`a1bb3b8`) |
| 2 | `max_hypothesis_rounds` was a lifetime cap per question → iterations 3–5 ran `0 moves` | rounds are per search run; the tabu list is the cross-iteration memory (`e14fd03`) |
| 3 | a probe the gatekeeper refused left its hypothesis `UNVERIFIED`; PUCT re-selected it 17 times | `UNTESTABLE` status + in-place probe repair on re-proposal (`998538d`) |
| 4 | tabu was lexical: 7 paraphrased re-proposals of falsified mechanisms slipped under Jaccard `0.8` (scored `0.46–0.71`) | `same_mechanism`: Jaccard **or** containment ≥ `0.75` **or** claim-Jaccard ≥ `0.8`, thresholds measured on the pilot ledgers (`c869dcc`) |
| 5 | 7 of 9 synthesis moves in `crossover_2` re-emitted the module byte-for-byte, silently | logged with failure counts; summary says `module unchanged`; *open:* targeted-edit synthesis |
| 6 | a `7/50` first pass seeded 18 questions and the search proposed breadth-first (18 of 40 moves before any experiment); in pass 2 a supported-but-unsynthesised `q1` (`u = 0.105`) starved behind a fresh `q2` (`u = 1.0`) | **question frontier + triage**: ≤ `frontier_size` (gene, default `3`) open questions at once, one per cluster (`module` + `failure_signature`: exception class and message with numbers/paths/literals masked); the next move goes to the question with a synthesis ready, then one with experiments pending, then the most uncertain; once a question resolves its cluster siblings are **deferred to the oracle** (`stop = deferred_to_oracle`, `questions_deferred` in the audit). On the 18-question shape this is 4 moves to a verified patch instead of 18 proposal rounds (`tests/test_epistemic_frontier.py`) |

What the pilot does **not** show: a statistically meaningful lift. `n = 1` per firm per pass and the unchanged `V5` first pass varies wildly (`elite_1` drew 47, 48 and 7/50 in three passes). Proposer priors were badly calibrated on these bugs (Brier `0.12–0.42`, `low_prior_wins = 4` for the converging firm), which is the empirical case for the `V7` value head.

#### 3.3.2 The cohort — *pending ≥ 3 seeds per genome; finding #6 fixed, finding #5 (targeted-edit synthesis) in progress*

- **Benchmark A (regression):** `full_stack_hae` (`50` held-out tests) — target: break the `46–49/50` plateau on the 8 non-converged lineages **without** increasing full-company passes.
- **Benchmark B (new, oracle-free):** a multi-file debugging task where **no benchmark traceback is handed to the company** — firms must author their own probes to localise the fault. This is the benchmark `V5` cannot attempt at all.
- **Telemetry harvested for the learned value head:** every `(E_t features, move, ΔU, terminal outcome)` tuple across all firms — already recorded per move in `MoveRecord.features` / `value_before` / `value_after` inside each `_epistemic_tree.json`.

### 3.4 How to Enable `V6`, and What to Read in the Results

**Enable for a whole generation** — in `configs/generations/gen16.json` (any field of the gene may be pinned; unlisted fields keep evolving):

```json
{
  "generation": 16, "name": "V6 epistemic search", "parent_scorecards": "results/hae_gen15_fullstack_v5",
  "task_file": "configs/tasks/full_stack_hae.json",
  "epistemic_policy": {"enabled": true, "search_budget_moves": 40, "low_prior_quota": 0.34}
}
```

**Enable for a single firm** — set `"epistemic_policy": {"enabled": true}` on its entry in a population JSON. Nothing else changes: same task file, same verifier, same `max_iterations`.

**What the worker emits** — `[V6 EPISTEMIC]` banner at start; `[epistemic] <firm> q1/h2 prior=0.30 -> SUPPORTED (dU=0.440) ...` per move; `[EPISTEMIC AUDIT] questions=… certified=… evidence_backed=… brier=… resolved=…` at the end; `epistemic_integrity` / `epistemic_efficiency` / `epistemic_audit` on the scorecard; and `<firm>_epistemic_tree.json` (ledger + every search trajectory + audit + the policy that produced it).

**Things to keep in mind when reading Gen 16 (1–3 by design, 4–9 learnt in the pilot):**

1. **One known oracle leak predates `V6` and is left in place:** `AgentWorkspace.write_file`'s *Monotonic Verification Guard* runs the external benchmark on the four graded module paths to refuse score-lowering overwrites. It is a `V4` safeguard, it applies identically to `V5` and `V6` firms, and it is documented here so that nobody mistakes a guard refusal for epistemic progress. The gatekeeper itself never calls the benchmark; probes cannot import `held_out`.
2. **Budgets are soft** (`86c964c`, Sep 24): `Budget.can_spend` is unconditionally `True`, so the search's `token_budget` stop never fires in production. The search is bounded by `search_budget_moves`, `max_stagnant_moves` and `all_resolved` / `exhausted`; cost pressure reaches the gene through Net Fitness (`cost_penalty` / `efficiency_bonus`), exactly as it reaches topology genes.
3. **Iteration 1 is identical in both modes.** A firm that converges on its first pass never opens a ledger and is scored by the `V5` composite — so `V6` can only be *credited* for repair behaviour, which is where Gen 15 plateaued.
4. **Probes travel as `probe_lines` arrays, not strings.** Under `xgrammar` a multi-line string field made the decoder loop until `max_tokens`; the schema in [`moves.py`](../../hae/epistemic/moves.py) therefore takes an array of ≤ 60 lines, and a packet cut off mid-array is salvaged item by item. Legacy `probe_code` strings are still accepted by the parser. Reading an old tree: `hypotheses_tested = 0` with `stop = exhausted` is the pass-1 signature of this defect, not of an unsolvable question.
5. **`UNTESTABLE` is not a verdict.** A hypothesis whose probe the gatekeeper refused (invalid Python, forbidden import, too long) is parked: posterior untouched, nothing in `ruled_out`, mechanism not tabu, excluded from `hypotheses_tested` and from the synthesis gate. The ledger summary tells the proposer which probe was refused and why; a re-proposal of the same mechanism with a different probe repairs it in place (`probe_repairs` in the audit). `probes_refused` counts the gatekeeper's refusals from the evidence log.
6. **Tabu is "same mechanism", not "same words".** [`same_mechanism`](../../hae/epistemic/ledger.py) treats two hypotheses as one if their full signatures have Jaccard ≥ `0.8`, **or** the shorter signature (≥ 6 tokens) is ≥ `75 %` contained in the longer, **or** the claims alone have Jaccard ≥ `0.8`. The thresholds come from the pilot ledgers (seven paraphrased duplicates at claim-Jaccard `1.00` / containment `0.83–0.88`; every distinct pair ≤ `0.48` / ≤ `0.67`). A `tabu_rejections` count is therefore a *detected* re-proposal; it still says nothing about mechanisms the rule cannot see.
7. **A `module unchanged` synthesis is a System 1 failure, not a gatekeeper refusal.** The synthesiser is asked for a complete module rewrite and sometimes re-emits the current file byte for byte; the move is recorded (`synthesis wrote nothing` / `module unchanged`), counts against `MAX_SYNTHESIS_FAILURES = 2` for that hypothesis, and resolves nothing. Nine syntheses with two verified (`crossover_2`) is what that looks like in an audit.
8. **Nets are not comparable across `V5` and `V6`, and `n = 1` trajectories are noisy.** The ledger composite re-weights fitness (`60/25/15`), so compare held-out trajectories, not nets; and because iteration 1 is the unchanged `V5` pass, a single run's trajectory is dominated by that first draw (`elite_1`: 47, 48 and 7/50 across three pilot passes). Quote means only over several seeds per genome.
9. **`deferred_to_oracle` is a deliberate early stop, not a failure.** The search works on at most `frontier_size` questions at a time and, once one is resolved, hands its cluster siblings (same module, same `failure_signature`) to the oracle instead of spending moves on them: if the patch was the common cause, the next iteration certifies them all for free; if not, they come back as open questions with nothing lost but one iteration. The cluster rule is deliberately conservative — different exception classes or message shapes never merge — so under-merging (two true siblings worked separately) is the expected error, not over-merging. Reading a tree: few moves, one `synthesized … matched`, `questions_deferred = 17`, `stop = deferred_to_oracle` is the intended shape for a many-failure first pass; `questions_deferred = 0` with many `frontier_admissions` and `syntheses_verified = 0` means each representative was exhausted and its siblings were admitted in turn — still one cluster at a time, but no patch to defer on. `frontier_size` is a gene, so the breeder can move it; `1` is strict depth-first.

---

## 4. What Does *Not* Change

- `V5` TypeSafe `xgrammar` schemas remain the transport for every System 1 proposal (the epistemic moves are simply new, stricter schemas).
- The unified `llm-d` session, per-module decomposition, and zero-copy content-addressed workspaces are what make `50–100+` moves per company cheap: sibling expansions share `~85%` of their prefix.
- Evolution remains the **outer loop**: `epistemic_policy` genes are selected on audited fitness exactly like topology genes were in `V1–V5`.

## 5. Phase 7 (`V7`): Parametric RL on Certified Trajectories (formerly `V6`)

GRPO / DPO distillation is deferred one phase, for a reason Graepel makes explicit: *"Once these rules are enforced, the model can accumulate certified knowledge and improve its reasoning policy by learning from past reasoning experiences."* Training on `V5` trajectories would distil post-hoc narratives; training on `V6` ledgers distils **evidence-backed moves** (which hypotheses were worth proposing, which experiments were decisive). The RL phase therefore consumes `V6` search trees, not `V5` transcripts.
