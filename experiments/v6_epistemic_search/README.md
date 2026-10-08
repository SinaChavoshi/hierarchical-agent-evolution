# Phase 6 (V6): Epistemic Tree-Search Organizations

Status: design finalized October 5, 2026. Stage 1-2 implemented and tested October 6, 2026 (`hae/epistemic/` engine, `epistemic_policy` gene, runner/worker/breeder integration, audit-based fitness; 443 tests, no GPU). Gen 16 pilot (October 6, 2026) and cohort runs 1-3 (October 6-7, 2026) completed; results under `results/hae_gen16_v6_*` ([pilot](../../results/hae_gen16_v6_pilot/README.md), [cohort](../../results/hae_gen16_v6_cohort/README.md), [cohort run 2](../../results/hae_gen16_v6_cohort_r2/README.md)). V7 heads exist in shadow mode (section 5).

V6 turns a repair iteration into a tree search over an explicit ledger of beliefs. The LLM only proposes questions, hypotheses, sandbox probes and patches; a deterministic, non-LLM gatekeeper decides from probe evidence what the firm may believe.

## 1. Why V6 exists: the Generation 15 plateau

Generation 15 was the best run to date (10/10 firms at 46-50 of the 50 held-out tests, two at 50/50; [`generation_15_summary.json`](../../results/hae_gen15_fullstack_v5/generation_15_summary.json)). Its repair trajectories also suggest that the repair loop of V5 (the previous phase) is a greedy hill-climber driven by LLM intuition alone, with a ceiling:

| Symptom | Evidence | Diagnosis |
|---|---|---|
| Flat-line repair trajectories | `gen_14_elite_1` [49, 49, 49, 49, 49], `gen_14_crossover_1` [49, 49, 49, 49, 49], `gen_14_mutant_1` [48, 48, 48, 48, 48], `gen_14_elite_2` [46, 46, 46, 46, 46]: no progress in 16 combined repair iterations. After iteration 2, 8/8 non-converged firms were stuck (only `gen_14_mutant_2` gained 2 tests, at iteration 4). | At `temperature=0.1` the Lead Engineer re-emits the same highest-probability patch every iteration; with no record of what failed and why, the loop cannot pivot. |
| Same wrong mechanism in independent firms | `test_pytest_style_functions_are_collected` failed in 7/8 non-converged snapshots, `test_unittest_style_classes_are_collected` in 6/8. | The LLM's prior favours one plausible but wrong implementation of stdlib test collection; sampling only high-prior moves never reaches the low-prior alternative that passes. |
| Repair needs the oracle's traceback | Every +36 / +41 jump (`gen_14_crossover_3`, `gen_14_pareto_2`, `gen_14_mutant_2`) came after the external benchmark returned exact `AssertionError` diffs. | Firms never ask their own questions or run falsification experiments; without the oracle nothing reduces uncertainty. |
| Half of fitness graded post-hoc prose | `judge.py` scored `ExecutiveDeliverablePacket` narratives written after the code existed. | The score rewards a convincing story, not an auditable reasoning trail. |

The design was triggered by Thore Graepel's essay ["Don't be fooled - LLMs don't reason"](https://www.technologyreview.com/2026/10/02/1145639/dont-be-fooled-llms-dont-reason/) (MIT Technology Review, October 2, 2026; Graepel was on the AlphaGo core team and is now Chair of Machine Learning at UCL): an LLM, chain-of-thought included, is System 1; System 2 must be explicit machinery outside the weights, with an independent component that scores each move by the uncertainty it resolves and updates beliefs only on evidence.

The earlier plan for "100+ internal iterations" was Monte Carlo tree search (MCTS) over code snapshots scored by unit tests, with the CEO LLM assigning priors and an LLM or heuristic critic as the value. V6 differs on four points: the state is a ledger (certified facts, hypotheses with calibrated confidence, ruled-out beliefs with their killing evidence, open questions), so failed patches are no longer lost with everything but `best_workspace_files`; a move is an operator on the ledger (ask, hypothesise with low-prior alternatives, experiment, revise, synthesise from settled knowledge), not a file rewrite; LLMs propose and only the gatekeeper certifies or falsifies, where in V5 any agent could assert "fixed" in prose; and priors order exploration but never prune, since AlphaGo's policy network gave Move 37 a 1-in-10,000 prior and the search found it by evaluating consequences. Fitness audits the ledger instead of grading prose.

## 2. Architecture

```mermaid
flowchart TD
    subgraph S2["System 2 — Explicit Epistemic Search Engine (deterministic Python, hae/epistemic/)"]
        Ledger["EpistemicState E_t (content-addressed ledger)\n• settled_knowledge: CertifiedFact[]\n• hypotheses: Hypothesis[] (status + calibrated confidence)\n• ruled_out: FalsifiedBelief[] (+ killing evidence)\n• open_questions: Question[] (+ uncertainty)"]
        Tree["Epistemic PUCT Tree Search\nnodes = E_t, edges = epistemic moves\n50–100+ moves per company"]
        Gate["Independent Evidence Gatekeeper\n• runs sandbox probes / module checks\n• enforces evidence-backed belief revision\n• computes information gain ΔU"]
        Value["Epistemic Value Function V(E_t)\n= α · (uncertainty resolved by evidence)\n+ (1-α) · learned value head V̂θ(φ(E_t))"]
    end
    subgraph S1["System 1 — LLM Intuition Generators (Qwen3.8-180B, xgrammar-typed)"]
        Q["Question / Decomposer Pod\nfinds what is ambiguous or untested in E_t"]
        H["Hypothesis Pod\nk mutually-exclusive mechanisms,\nincluding low-prior alternatives"]
        X["Experiment Designer Pod\n≤30-line sandbox probe that\nfalsifies H_i vs H_j"]
        B["Synthesis Engineer\ncompiles settled knowledge\ninto the target module"]
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

PUCT is the AlphaGo selection rule (exploration bonus scaled by a prior). `xgrammar` is the grammar-constrained decoder that forces every LLM packet to match a JSON schema. The oracle is the external benchmark's 50 held-out tests, which the firm never sees.

### Worked walkthrough

Numbers from question `q2` of `gen_14_crossover_2` in the Gen 16 pilot; U is the question's uncertainty (starts at 1.0), V the ledger value (starts at 0.05).

1. Priors (LLM). `ProposeHypothesis` ([`moves.py`](../../hae/epistemic/moves.py)) yields two competing explanations, each with a sandbox reproduction (`probe_lines`) and a falsifiable prediction: H1, the parent manager attribute is a raw dict instead of an object, prior P1 = 0.75; H2, the object is valid but `recombine()` omits passing it to the constructor, P2 = 0.10. A prior only orders which probe runs first and never counts as evidence; `low_prior_quota = 0.34` reserves 34% of expansions for low-prior siblings such as H2.
2. Move 1, H1 falsified, ΔU = +0.44. [`EvidenceGatekeeper.apply()`](../../hae/epistemic/gatekeeper.py) runs H1's probe in an isolated stage; the prediction fails (`FALSIFIED`). H1's posterior goes to 0 and H1 enters `ruled_out` (the tabu list). Elimination credit (`ELIMINATION_CREDIT = 0.5`): mass = 0.75 + 0.10 = 0.85, dead = 0.75, dead/mass = 0.882, U = 1.0 x (1 - 0.5 x 0.882) = 0.559, about 0.56; the cap of 0.5 exists because ruling out every suspect still does not say what the bug is. ΔU = 1.0 - 0.56 = 0.44; V rises from 0.05 to 0.31.
3. Move 2, H2 supported, ΔU = +0.425. H2's probe matches its prediction (`SUPPORTED`). With `SUPPORT_STRENGTH = 0.85` the posterior is 1 - (1 - 0.10)(1 - 0.85) = 0.865 and U = max(0.05, 1 - 0.865) = 0.135; ΔU = 0.56 - 0.135 = 0.425, V goes from 0.31 to 0.68.
4. Move 3, synthesis and certification. Only now may an engineer edit `morphogenesis.py`. Once `verify_module()` (gatekeeper) compiles it and the local checks pass (`Q_RESOLVED`), U = 0.02 (V = 0.92); when the oracle returns 50/50 in the next iteration, `q2` becomes `CERTIFIED` (U = 0.00, V = 1.00).
5. Calibration. [`calibration_report()`](../../hae/epistemic/value.py) scores every tested prior P_i against the verdict O_i in {0, 1} with the Brier error (P_i - O_i)^2; P1 = 0.75 on a falsified hypothesis costs 0.5625. [`epistemic_integrity_score()`](../../hae/evaluation/judge.py) rewards lineages whose priors beat a coin flip (Brier below 0.25).

Ranges: P_i 0.05-0.95 (LLM); ΔU 0.0-1.0 (gatekeeper; 0 for refused or no-op moves); V(E_t) 0.0-1.0, alpha x resolved fraction + (1 - alpha) x learned head (`EpistemicValueFunction` in `value.py`); calibration score 0.0-100.

### Stage 1: the engine (`hae/epistemic/`, CPU-only)

| Module | Responsibility | Invariants |
|---|---|---|
| [`ledger.py`](../../hae/epistemic/ledger.py) | `EpistemicState`, `Question`, `Hypothesis`, `CertifiedFact`, `FalsifiedBelief`, `Evidence`: immutable, content-addressed (`state_hash`), JSON-serialisable. | Confidence fields are read-only to LLM proposals; only gatekeeper transitions change status. Every certified fact and falsified belief carries the hash of its evidence. |
| `moves.py` | Typed operators `AskQuestion`, `ProposeHypothesis`, `ProposeExperiment`, `ProposeCodeDiff`, `Synthesize`, each an `xgrammar`-enforceable JSON schema. | A move proposes, never asserts truth. A mechanism matching a `ruled_out` entry is rejected as `TABU`. |
| `gatekeeper.py` | Runs probes in a disposable stage (reference `hae/` tree with the graded modules removed, the firm's workspace overlaid, `PYTHONPATH` pinned, `unshare -rn` network isolation, stdlib-only interpreter); applies the `SUPPORTED` / `FALSIFIED` / `CERTIFIED` rules; computes ΔU; reconciles the ledger with the oracle between iterations. | No LLM call. A belief update needs an `Evidence` record (`exit_code`, `stdout`/`stderr`, prediction match). Vacuous predictions cannot support; timeouts are `INCONCLUSIVE`, never evidence. Probes that touch `/app`, `sys.path`, `held_out`, the network or `inspect.getsource` are rejected before they run. |
| `value.py` | Stage A: feature-engineered value (uncertainty resolved, ruled-out density, stagnation depth, module isolation). Stage B: a learned head, pure-Python logistic regression serialised as JSON, trained on harvested trees. | Calibration is Brier-scored against terminal outcomes; miscalibration is a fitness penalty. |
| [`mcts.py`](../../hae/epistemic/mcts.py) | PUCT over ledger states; question frontier and triage (finding 6); forced low-prior exploration (`low_prior_quota`); dead-end back-propagation; tabu memory. Stop reasons: `budget_moves`, `stagnation`, `all_resolved`, `exhausted`, `deferred_to_oracle`, `token_budget`. | Priors are an exploration tie-breaker, never a pruning filter. Synthesis is offered only for a `SUPPORTED` hypothesis, after `min_hypotheses_before_synthesis` siblings were tested. |
| [`audit.py`](../../hae/epistemic/audit.py) | `build_epistemic_audit(state, search_stats, budget_moves, token_usage)`: evidence-backed fraction, status inconsistencies, Brier calibration of priors against verdicts, certified ratio, tabu discipline, resolved fraction, `ledger_hash`. | Pure function of the ledger; fitness reads it, prose never enters it. |
| [`genes.py`](../../hae/epistemic/genes.py) | `mutate_epistemic_policy` (bounded jitter, rare role re-binding), `crossover_epistemic_policy` (per-field uniform or numeric midpoint). | Operators never flip `enabled`; that is set per generation (`GenerationSpec.epistemic_policy`). Breeding is seeded per slot, so a spec always breeds the same child. |

### Stage 2: integration into the runtime

1. Gene. `CompanyGenome.epistemic_policy` ([`schema.py`](../../hae/genome/schema.py), `EpistemicPolicyGene`) is heritable, mutable and crossover-able: `enabled` (default `False`), `search_budget_moves` [4, 400] (default 40), `branching_k` [2, 6], `c_puct` [0.1, 5.0], `low_prior_quota` [0.0, 0.8] (default 0.34), `experiment_timeout_s`, `max_probe_lines`, `value_alpha`, `min_hypotheses_before_synthesis`, `max_stagnant_moves`, `max_hypothesis_rounds`, `frontier_size` [1, 8] (default 3), `role_bindings` (`question -> qa`, `hypothesis -> engineering`, `experiment -> verification`, `synthesis -> engineering`, matched against department id, name or mandate so pods invented by morphogenesis still route). Genomes from Gen 15 and earlier deserialise with the default gene and behave as before.
2. Runner. `HierarchicalCompanyRunner.run_epistemic_search(objective, failures, iteration)` ([`company.py`](../../hae/runtime/company.py)) runs one repair iteration as a search: the oracle's failure lines seed or update the ledger (`reconcile_epistemic_state`), the bound departments propose `HYPOTHESIS_SET` packets (an `xgrammar` schema like every V5 packet), the gatekeeper runs each probe, and a patch is synthesised through the existing tool loop only for a hypothesis that survived. It returns the `run()` record shape plus `epistemic_ledger` and `epistemic_search`; `run()` is untouched.
3. Worker ([`worker.py`](../../hae/orchestration/worker.py)). Iteration 1 is always `run()`; iterations 2 to N use `run_epistemic_search()` when the gene is enabled. Afterwards the ledger is reconciled with the oracle's final verdict (vanished failures become `CERTIFIED` by oracle evidence; still-failing "resolved" questions are reopened and their patches `FALSIFIED`), the audit is handed to the evaluator, and the trail is written to `<company_id>_epistemic_tree.json` next to the scorecard.
4. Breeder ([`breeder.py`](../../hae/orchestration/breeder.py)). Crossovers blend the parents' policies, mutants perturb them, elite and pareto clones copy them. `GenerationSpec.epistemic_policy` applies cohort-level overrides (`{"enabled": true, ...}`) after inheritance and refuses unknown or out-of-range fields. `rank_scorecards` ranks V6 scorecards by the audit-weighted composite the worker used; V5 scorecards rank as before.
5. Fitness ([`judge.py`](../../hae/evaluation/judge.py)). With a ledger, `composite_score` uses `EPISTEMIC_WEIGHTS`: **60% held-out execution, 25% ledger integrity, 15% search efficiency**. Integrity = 30% evidence backing and status consistency, 25% Brier calibration, 30% oracle-certified ratio, 15% proposal discipline (share of proposals that were new, testable work: a tabu re-proposal or refused probe costs a full point, a duplicate or a probe repaired in place half a point, over `proposed_total`, floored at zero; audits without these counters score as before). Efficiency = 60% uncertainty resolved, 40% resolved per unit of move budget. The prose rubric is still reported but has zero weight; without a ledger the V5 composite is used unchanged.

## 3. Stage 3: Generation 16

### Pilot (October 6, 2026)

Four Gen 14 genomes that had plateaued under V5 in Gen 15 were re-run with one field changed, `epistemic_policy.enabled = true`, on the same task, model, verifier and objective ([`configs/generation_16_pilot_population.json`](../../configs/generation_16_pilot_population.json)). A throw-away GKE (Google Kubernetes Engine) cluster with one spot `g4-standard-96` node served one vLLM inference replica ([`k8s/gen16-pilot-serving.yaml`](../../k8s/gen16-pilot-serving.yaml); Jobs [`k8s/hae-gen16-v6-pilot{,-r2,-r3}-job.yaml`](../../k8s/)); results were harvested from pod logs; the three passes took about 1.7 spot GPU-hours and the cluster was deleted when the last firm finished. Report: [`results/hae_gen16_v6_pilot/`](../../results/hae_gen16_v6_pilot/README.md).

| Firm | Gen 15 (V5) held-out trajectory | V6 pilot, working code (pass 2) |
|---|---|---|
| `gen_14_crossover_2` | [47, 49, 49, 49, 49] | **[48, 48, 50], converged at iteration 3** on a patch from a 0.10-prior hypothesis the gatekeeper supported after the 0.75-prior one was falsified; both questions oracle-certified. |
| `gen_14_mutant_1` | [48, 48, 48, 48, 48] | [49, 49, 49, 49, 49]: 18 hypotheses, 18 falsified (two after a module-check-passing synthesis, by the oracle's final verdict). |
| `gen_14_elite_1` | [49, 49, 49, 49, 49] | [48, 48, 48, 48, 48]: one supported hypothesis never synthesised; the search starved on refused probes (finding 3). |
| `gen_14_elite_2` | [46, 46, 46, 46, 46] | [50] at iteration 1 (pass 1), the unchanged V5 first pass; the search never ran. |

The pilot does not show a statistically meaningful lift (n = 1 per firm per pass; see caveat 8 on first-pass variance). Proposer priors were poorly calibrated on these bugs (Brier 0.12-0.42; `low_prior_wins = 4` for the converging firm), which is the empirical case for a learned value head (V7).

### Findings and fixes

The pilot took three passes because the first two surfaced defects that only a live grammar-constrained decoder and proposer could expose; each was fixed in-tree with tests before the next pass. Findings 7 and 8 came from the cohort runs.

| # | Finding | Fix |
|---|---|---|
| 1 | `xgrammar` could not emit a multi-line `probe_code` string: every hypothesis packet looped until `max_tokens`, and pass 1 tested zero hypotheses. | Probe as a `probe_lines` array, truncated packets salvaged (`a1bb3b8`; caveat 4). |
| 2 | `max_hypothesis_rounds` was a lifetime cap per question, so iterations 3-5 ran 0 moves. | Rounds are per search run; the tabu list is the cross-iteration memory (`e14fd03`). |
| 3 | A probe the gatekeeper refused left its hypothesis `UNVERIFIED`; PUCT re-selected it 17 times. | `UNTESTABLE` status plus in-place probe repair on re-proposal (`998538d`; caveat 5). |
| 4 | Tabu was lexical: 7 paraphrased re-proposals of falsified mechanisms scored Jaccard 0.46-0.71, under the 0.8 threshold. | `same_mechanism` with thresholds measured on the pilot ledgers (`c869dcc`; caveat 6). |
| 5 | 7 of 9 synthesis moves in `crossover_2` re-emitted the module byte for byte, silently. | Logged with failure counts as `module unchanged`; anchored `REPAIR_PLAN` applied by the runner (A2, in the cohort image); after run 2 the synthesis ladder A4 (below), live in run 3. |
| 6 | A 7/50 first pass seeded 18 questions and the search went breadth-first (18 of 40 moves before any experiment); in pass 2 a supported but unsynthesised `q1` (u = 0.105) starved behind a fresh `q2` (u = 1.0). | Question frontier and triage (A1, below): on the 18-question shape, 4 moves to a verified patch instead of 18 proposal rounds (`tests/test_epistemic_frontier.py`). |
| 7 | Cohort, `elite_1__s3`: a graded suite that could not import (`[execution_harness] suite failed to import: ModuleNotFoundError`) did not match the failure-key regex, so the question was filed under module `""` and every synthesis fell back to the first required module. The search diagnosed that `harness.py` was missing (`SUPPORTED`) but could only repair an existing file; a 7/50 first pass sat at 7 for three iterations. 14 probes in the same run were `UNTESTABLE` for using `__file__` or `importlib.util.find_spec`. | `<tag>:suite_import` failure key targeting the tag's module; a synthesis whose target is absent authors it (`mode = author`, stat `syntheses_authored`); the proposer prompt lists the refused constructs (`ee80df0`). Not in the run-1 cohort image. |
| 8 | Cohort run 2, `mutant_3__s1` [23 x5], `crossover_1__s1` [26 x5], `elite_2__s3` [22, 23, 23, 23, 23]: the oracle's failure changed shape mid-run and the questions did not (below). | Oracle reconciliation refreshes a changed question, the benchmark reports traceback locations, `verify_module` gained a static check (below). Not exercised live in runs 1-2; shipped for run 3. |

Frontier and triage (A1): at most `frontier_size` (a gene, default 3) open questions at once, one per cluster, a cluster being `module` plus `failure_signature` (exception class and message with numbers, paths and literals masked). The next move goes to the question with a synthesis ready, then one with experiments pending, then the most uncertain. Once a question resolves, its cluster siblings are deferred to the oracle (`stop = deferred_to_oracle`, `questions_deferred` in the audit).

Finding 8 in detail: in all three runs the 20 failing `execution_harness` tests were one `harness.py` bug, an `except ... as e` followed by a use of `e` after the handler (`UnboundLocalError`). A synthesis that `py_compile`d and imported, so `verify_module` passed it, moved the symptom to a `NameError` (`mnf_err`, `e`, `mod`) without fixing it. In every later iteration the question text still quoted the first error (20/20, 15/20 and 21/21 questions), the proposer reasoned about a symptom that no longer existed, and the tabu list kept blocking mechanisms falsified against it: 37 / 45 / 29 hypotheses falsified, 4 / 3 / 12 refused, no synthesis survived the oracle. The fix:

- `reconcile_with_oracle` refreshes a question whose `failure_signature` changed: new text from the current line, `failure_epoch += 1`, old line archived in `failure_history`, uncertainty back up to 0.75 x initial, `EXHAUSTED`/`RESOLVED` back to `OPEN`. Falsified beliefs carry the epoch they were tested in and only current-epoch ones are tabu (earlier ones collapse into one `RULED OUT (history)` line). A literal-only change (`0 != 4` to `1 != 4`, a fresh temp path) rewords the text without a new epoch.
- The benchmark reports where each failure's traceback ended inside the firm's code (`failure_locations`, e.g. `hae/evaluation/harness.py:212 in _run_pytest`), shown to the proposer as `Oracle traceback ends at:`.
- `verify_module` gained a `static_check` step ([`static_check.py`](../../hae/epistemic/static_check.py), `symtable` plus `ast`): an undefined global in a function body or an `except ... as e` leak fails the synthesis before the oracle sees it. On the three firms' `harness.py` it flags `mnf_err`/`imp_err`, the `e` leak and the `e` `NameError` respectively, and nothing on the repository's own modules.

### Cohort design

[`configs/generations/gen16_cohort.json`](../../configs/generations/gen16_cohort.json) -> [`scripts/make_gen16_cohort_population.py`](../../scripts/make_gen16_cohort_population.py) -> [`configs/generation_16_population.json`](../../configs/generation_16_population.json); invariants in [`tests/test_gen16_cohort.py`](../../tests/test_gen16_cohort.py). The cohort is not a bred generation, since breeding would confound topology with the gene: it re-runs the Gen 14 genomes that plateaued under V5 in Gen 15 with the same task file, verifier, budgets, `max_iterations = 5` and model, and exactly three fields changed: `epistemic_policy.enabled = true`, `search_budget_moves = 60`, `frontier_size = 3`.

| Arm | Lineages | Seeds | Firm-runs | Measures |
|---|---|---|---|---|
| Non-converged (Benchmark A) | `crossover_2`, `elite_1`, `crossover_1`, `mutant_2`, `mutant_1`, `crossover_3`, `pareto_2`, `elite_2` (Gen 15 held-out 46-49/50) | 3 | 24 | Whether evidence-gated repair moves a plateaued lineage, and how noisy that is across seeds. |
| Converged controls | `mutant_3` ([48, 50]), `pareto_1` ([50]) | 1 | 2 | Whether enabling the gene costs a lineage that already converged. |

Replicas are named `<lineage>__s<k>`, ordered seed-major (Job indices 0-9 are one draw of every lineage), and byte-identical to the source genome except `company_id`, `epistemic_policy` and one appended `mutation_history` line (tested). Image `v6-gen16-cohort` is `main @ b80f543`: the three pilot fixes plus the frontier (finding 6) and synthesis as an anchored change (finding 5). Benchmark B (oracle-free debugging) is deferred to a later cohort.

Infrastructure ([`cluster-setup.sh`](../../k8s/cluster-setup.sh), [`gen16-cohort-serving.yaml`](../../k8s/gen16-cohort-serving.yaml), [`hae-gen16-v6-cohort-job.yaml`](../../k8s/hae-gen16-v6-cohort-job.yaml), [`teardown.sh`](../../k8s/teardown.sh) in `k8s/`): a throw-away GKE cluster; 4 vLLM replicas (Qwen3.8-Flash-Next NVFP4, tensor-parallel 2) on spot `g4-standard-96` nodes behind the prefix-affinity gateway; the 26 firm pods on an on-demand CPU pool, because two spot nodes were preempted within fifteen minutes of launch and `us-central1-b` then reported `ZONE_RESOURCE_POOL_EXHAUSTED` for replacements (a replica loss costs a firm an LLM retry, not the run). Results are harvested from pod logs (`scripts/harvest_gen16_pilot.py --job-label app=hae-gen16-v6-cohort`) and summarised per lineage by [`scripts/summarize_gen16_cohort.py`](../../scripts/summarize_gen16_cohort.py).

Cost model from the pilot (pass 2, one replica shared by three firms): 94 k / 250 k / 458 k tokens, $0.03 / $0.07 / $0.07 and 9.5 / 37 / 48 min per firm-run, against $0.03-0.21 per firm under V5 in Gen 15; for 26 firm-runs, 2.4-12 M tokens and $0.8-1.8 of model time at the vLLM rate card, dominated by the GPU (every wall-clock hour is 4 spot `g4-standard-96` node-hours). Actual tokens, wall-clock, node-hours and preemptions are in the cohort report.

Per lineage the cohort reports mean / min / max of the final held-out count over seeds against the Gen 15 trajectory, how many seeds converged and when, and each seed's first-pass draw (the unchanged V5 pass, the main source of variance); nets are reported but not compared across V5 and V6 (caveat 8). Per run it records frontier admissions and deferrals, syntheses by plan versus rewrite, no-op retries and recoveries, tabu and duplicate rejections, refused and repaired probes, forced low-prior picks and wins, and Brier calibration: the telemetry that decides whether findings 5 and 6 are closed.

### Cohort runs 1 and 2 (October 6, 2026)

Run 1 (image `b80f543`, 26 firm-runs; [report](../../results/hae_gen16_v6_cohort/README.md)). Compare by first-pass class, because iteration 1 is the unchanged V5 pass and its draw dominates every trajectory (one lineage drew 50, 47, 50; another 48, 7, 7). 3 of 26 runs converged at iteration 1. **The 16 plateau starts (44-49) ended at a mean of 48.6, two reaching 50 at iteration 5** (`crossover_3__s2` [47, 47, 49, 49, 50], `elite_2__s3` [48, 48, 48, 48, 50]); the other 14 stayed flat on the same three `execution_harness` held-out tests that capped Gen 15. The 7 catastrophic starts (35 or fewer) recovered in none, for the reason in finding 7: the search diagnosed the missing `harness.py` with evidence in 2-3 moves and then rewrote `artifacts.py`, because the question carried no module. Pooled mechanics: 874 moves, 529 experiments (107 supported / 227 falsified / 195 refused), 122 syntheses (95 verified), 115 forced low-prior picks (19 supported). Calibration over 334 tested hypotheses: hit rate 7%, flat across every prior bin, Brier 0.196 against 0.067 for the base rate, so the proposer's stated confidence carries no information. 11.5 M tokens, $1.82 of model time; GPU node-hours in the report.

Run 2 (image `6665432` = run 1 plus A3, the proposal-discipline term of the integrity score, plus the finding 7 fix; same 26 slots, launched 17:55 UTC; [report](../../results/hae_gen16_v6_cohort_r2/README.md)) is the controlled follow-up. Partial results as written up: the two run-1-shaped 7/50 starts now author the missing module and recover to 48 in one iteration, and `mutant_2__s1` and `mutant_1__s3` go 7 to 50 and 8 to 50 in one iteration; caveats in the report.

### A4: the synthesis ladder (between run 2 and run 3)

Run 2's 151 synthesis moves split into 44 written by the anchored `REPAIR_PLAN` (`mode = plan`), 5 by authoring a missing module (`author`) and 102 that fell through to the whole-module rewrite (about 10k tokens at 20-40 tok/s on one stream). Written moves cost 5.3 min mean (10.8 h total); the 28 moves that wrote nothing cost 17.0 min mean, 43.6 max (8.0 h), and 27 had the same shape: `plan not applicable` (15 `plan is a no-op`, 12 `anchor not found`), then a whole-module rewrite that re-emitted identical content, then a second rewrite that did the same (the retry recovered 5 of 33 no-ops in run 2, 8 of 35 in run 1).

A4 makes the whole-module rewrite the last resort:

- The plan's anchor also accepts a unique fuzzy window (`SequenceMatcher` ratio >= 0.92, unique by 0.05) and names the closest window when it misses.
- A grammar-constrained `FUNCTION_REWRITE` rung (`function` an enum of the module's definitions, `new_source` its complete replacement, about 4k tokens) is spliced in by the runner (`apply_function_rewrite`: must parse, same name, not identical, result must parse; `mode = function`, stat `syntheses_by_function`).
- The whole-module rewrite runs at most once and is skipped when the plan and the function rewrite were both no-ops (`synthesis_rewrites_skipped`; two cheap no-ops predicted the expensive one in every observed case, so a skipped rewrite costs 0 min instead of 14-26).
- A synthesis whose module check fails is reverted to the pre-synthesis module (`syntheses_reverted`; move note `path [mode]: ...; reverted to pre-synthesis module`), and every move reports `elapsed_s` (`synthesis_seconds` in the search stats).

Code and tests: `hae/runtime/company.py::_synthesize_patch_adapter`, `tests/test_synthesis_ladder.py`, `tests/test_epistemic_synthesis.py`. A4 was unit-tested but had not met the live decoder before run 3. Run 3 (October 7, 2026, image `bdfa962`; report in [`results/hae_gen16_v6_cohort_r3/`](../../results/hae_gen16_v6_cohort_r3/README.md)) answered the two questions it was built to answer: 29 of 147 syntheses were written by `FUNCTION_REWRITE` and the whole-module rewrite fell from 102 of 151 syntheses (run 2) to 36, with total synthesis time down from 18.8 h to 12.0 h; the double-no-op skip never fired. 19 syntheses were reverted by the module check, 14 of 26 runs converged (6 at iteration 1), 5 of 11 plateau starts and 3 of 9 catastrophic starts reached 50/50, and the pooled hit rate of tested hypotheses rose to 23% from 7% and 11% in runs 1 and 2. The same report scores the V7 shadow heads on data they did not train on (section 5).

## 4. Enabling V6 and reading the results

For a whole generation, add the gene to the generation spec (the cohort's is `configs/generations/gen16_cohort.json`; the example below is for a `configs/generations/gen16.json`). Pinned fields stay fixed; unlisted fields keep evolving.

```json
{
  "generation": 16, "name": "V6 epistemic search", "parent_scorecards": "results/hae_gen15_fullstack_v5",
  "task_file": "configs/tasks/full_stack_hae.json",
  "epistemic_policy": {"enabled": true, "search_budget_moves": 40, "low_prior_quota": 0.34}
}
```

For a single firm, set `"epistemic_policy": {"enabled": true}` on its entry in a population JSON. Nothing else changes: same task file, verifier and `max_iterations`.

The worker emits a `[V6 EPISTEMIC]` banner, one line per move (`[epistemic] <firm> q1/h2 prior=0.30 -> SUPPORTED (dU=0.440) ...`), a final `[EPISTEMIC AUDIT] questions=... certified=... evidence_backed=... brier=... resolved=...` line, `epistemic_integrity` / `epistemic_efficiency` / `epistemic_audit` on the scorecard, and `<firm>_epistemic_tree.json` (ledger, every search trajectory, audit, and the policy that produced it).

### Things to keep in mind when reading Gen 16

Items 1-3 are by design, 4-9 were learnt in the pilot, 10 in the cohort.

1. One known oracle leak predates V6 and is left in place: `AgentWorkspace.write_file`'s Monotonic Verification Guard runs the external benchmark on the four graded module paths to refuse score-lowering overwrites. It is a V4 safeguard applied identically to V5 and V6 firms, so a guard refusal is not epistemic progress; the gatekeeper never calls the benchmark, and probes cannot import `held_out`.
2. Budgets are soft (`86c964c`, September 24): `Budget.can_spend` is unconditionally `True`, so the `token_budget` stop never fires in production. The search is bounded by `search_budget_moves`, `max_stagnant_moves` and `all_resolved` / `exhausted`; cost pressure reaches the gene through Net Fitness (`cost_penalty` / `efficiency_bonus`), as for topology genes.
3. Iteration 1 is identical in both modes. A firm that converges on its first pass never opens a ledger and is scored by the V5 composite, so V6 can only be credited for repair behaviour, which is where Gen 15 plateaued.
4. Probes travel as `probe_lines` arrays of at most 60 lines (finding 1); a packet cut off mid-array is salvaged item by item, and legacy `probe_code` strings are still parsed. In an old tree, `hypotheses_tested = 0` with `stop = exhausted` is the pass-1 signature of this defect, not an unsolvable question.
5. `UNTESTABLE` is not a verdict. A hypothesis whose probe was refused (invalid Python, forbidden import, too long) is parked: posterior untouched, nothing in `ruled_out`, mechanism not tabu, excluded from `hypotheses_tested` and from the synthesis gate. The ledger summary tells the proposer which probe was refused and why; a re-proposal of the same mechanism with a different probe repairs it in place (`probe_repairs` in the audit), and `probes_refused` counts refusals from the evidence log.
6. Tabu means the same mechanism, not the same words. [`same_mechanism`](../../hae/epistemic/ledger.py) merges two hypotheses if their full signatures have Jaccard >= 0.8, or the shorter signature (at least 6 tokens) is at least 75% contained in the longer, or the claims alone have Jaccard >= 0.8; thresholds from the pilot ledgers (seven paraphrased duplicates at claim-Jaccard 1.00 and containment 0.83-0.88; every distinct pair at most 0.48 and 0.67). A `tabu_rejections` count is a detected re-proposal and says nothing about mechanisms the rule cannot see.
7. A `module unchanged` synthesis is a System 1 failure, not a gatekeeper refusal: asked for a complete module rewrite, the synthesiser sometimes re-emits the current file byte for byte. The move is recorded (`synthesis wrote nothing` / `module unchanged`), counts against `MAX_SYNTHESIS_FAILURES = 2` for that hypothesis and resolves nothing; nine syntheses with two verified (`crossover_2`) is what this looks like in an audit.
8. Nets are not comparable across V5 and V6, and n = 1 trajectories are noisy. The ledger composite re-weights fitness (60/25/15), so compare held-out trajectories, not nets; and because iteration 1 is the unchanged V5 pass, a single trajectory is dominated by that draw (`elite_1`: 47, 48 and 7/50 across three pilot passes). Quote means only over several seeds per genome.
9. `deferred_to_oracle` is a deliberate early stop. Once one of the at most `frontier_size` questions resolves, its cluster siblings (same module, same `failure_signature`) go to the oracle: if the patch was the common cause the next iteration certifies them all for free, otherwise they return as open questions at the cost of one iteration. The cluster rule is conservative (different exception classes or message shapes never merge), so under-merging is the expected error; few moves, one `synthesized ... matched`, `questions_deferred = 17` and `stop = deferred_to_oracle` is the intended shape for a many-failure first pass, while `questions_deferred = 0` with many `frontier_admissions` and `syntheses_verified = 0` means each representative was exhausted and its siblings admitted in turn (`frontier_size` is a gene; 1 is strict depth-first).
10. The search is a repair loop, and a missing graded module is a special case: every move assumes the failing module exists (proposals cite its source, probes import it, syntheses rewrite it). Finding 7 describes how a suite that could not import was filed under module `""` until `ee80df0`, after which a synthesis whose target is absent authors it (`syntheses_authored` in the audit). The run-1 image (`v6-gen16-cohort`, `b80f543`) predates that fix, so in those trees a 7/50-8/50 first pass that never moves, with every synthesis targeting `artifacts.py`, is this defect and not evidence that the search cannot repair catastrophic passes (`elite_1__s2` recovered 7 to 48 in the same cohort when the proposer happened to pick the right file).

### What does not change

- The V5 typed `xgrammar` JSON schemas remain the transport for every System 1 proposal; the epistemic moves are new, stricter schemas.
- The unified `llm-d` session (the shared inference gateway with cross-company prefix caching), per-module decomposition and zero-copy content-addressed workspaces are what make 50-100+ moves per company affordable: sibling expansions share about 85% of their prefix.
- Evolution remains the outer loop: `epistemic_policy` genes are selected on audited fitness exactly as topology genes were in V1-V5.

## 5. Phase 7 (V7): parametric RL on certified trajectories

GRPO / DPO distillation (RL fine-tuning on preference or reward signals), formerly planned as V6, is deferred one phase, in the order Graepel describes: enforce the rules first, then let the model improve its reasoning policy from certified past experience. Training on V5 trajectories would distil post-hoc narratives; training on V6 ledgers distils evidence-backed moves (which hypotheses were worth proposing, which experiments were decisive). The RL phase therefore consumes V6 search trees, not V5 transcripts.

The first learned heads exist as a measurement rather than a lift. [`scripts/train_value_heads.py`](../../scripts/train_value_heads.py) fits two pure-Python logistic regressions on the 48 cohort trees (26 firms, runs 1 and 2) and evaluates them out of fold (OOF) with 5 folds grouped by firm (AUC below is the area under the ROC curve); report in [`results/v7_value_heads/`](../../results/v7_value_heads/README.md), heads in [`hae/epistemic/heads/`](../../hae/epistemic/heads/).

- State-value head (`v7_value_head.json`: the 11 `MoveRecord.features`, label = the move's question ended `CERTIFIED`; 1,781 rows, base rate 0.28): **OOF Brier 0.186 / log-loss 0.552 / AUC 0.678** (per-fold 0.62-0.75) against 0.202 / 0.594 for the base rate and 0.221 / 0.646 / AUC 0.44 for the V6 `heuristic_value`; roughly calibrated (predicted 0.15 -> observed 0.12, 0.46 -> 0.52).
- Hypothesis-prior head (`v7_prior_head.json`: 32 hypothesis-level features reconstructed from the ledger at selection time, label = the hypothesis ended `SUPPORTED` or `CERTIFIED`; 875 experiment rows, 83 positives): does not beat the base rate out of fold, Brier 0.0863 against 0.0862, AUC 0.449 with folds from 0.35 to 0.69. On this data neither the proposer's stated prior (AUC 0.44) nor anything cheap computed from the probe, the claim or the ledger state predicts which hypothesis survives.

Both heads ship in shadow mode: `epistemic_policy` may name them (`value_head_path`, `prior_head_path`), but with the defaults `prior_head_weight = 0` and `value_head_live = false` the loop only records what they would have said (`MoveRecord.extra`: `head_p`, `head_rank`, `stated_rank`, `head_v`; `x_*` columns in the telemetry CSV; `prior_head_loaded` / `prior_head_steered` in the search stats) and no decision changes. Today V(s) drives no decision anyway: selection reads only the PUCT prior and stopping reads `delta_u`. The caveat behind every number in this document applies here too: n = 48 trees from one task family, the failure-class and module one-hots are Gen 16 specific, and `l2` was picked on the reported folds. The heads earn a weight only when a cohort run with `prior_head_weight > 0` beats the stated prior on fresh firms.

Run 3 is the first data the heads did not train on. Scored on their shadow predictions ([`results/hae_gen16_v6_cohort_r3/`](../../results/hae_gen16_v6_cohort_r3/README.md), section 3): the prior head reaches AUC 0.59 against 0.49 for the stated prior (369 experiment rows, base rate 0.24), the value head AUC 0.81 against 0.32 for the V6 heuristic (805 moves, base rate 0.59). Both rank better than what they replace and both are miscalibrated there, because run 3's base rates are two to three times those of runs 1-2; neither beats the always-base-rate Brier. That is the case for retraining on all three runs before any cohort turns `prior_head_weight` up, not for turning it up now.
