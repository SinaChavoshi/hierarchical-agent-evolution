# Generation 16 — `V6` Epistemic-Search Pilot (October 6, 2026)

> **TL;DR.** Four Gen-14 plateau genomes were re-run with exactly one change — `epistemic_policy.enabled = true` — against the Gen 15 (`V5`) baseline on the same task, model and verifier. The pilot needed three passes because the first two surfaced engine defects that no CPU test could have shown (grammar-constrained decoding, per-question round caps, refused probes); each was fixed in-tree and the fix re-run. On the final working code (pass 2), **`gen_14_crossover_2` broke its Gen 15 plateau — `[47, 49, 49, 49, 49]` under `V5` became `[48, 48, 50]` under `V6`**, converging at iteration 3 on a patch synthesised from a **0.10-prior hypothesis** that the Evidence Gatekeeper had SUPPORTED. The other two firms stayed on their plateau (`[48×5]`, `[49×5]`) but produced fully evidence-backed ledgers — including 18 falsified hypotheses for one firm, two of which had passed module checks and were then **falsified by the oracle's final verdict**, exactly the accounting `V5` could not do. Five engine findings (four fixed in commits listed below, one documented as a Stage 3 design item) came out of ~1.7 GPU-hours. The GPU cluster was deleted afterwards and verified gone.

---

## 1. Design

| | |
|---|---|
| **Question** | Does the `V6` epistemic search change the repair trajectory of genomes that plateaued under `V5` — *within genome*, nothing else varied? |
| **Firms** | `gen_14_elite_1`, `gen_14_crossover_2`, `gen_14_mutant_1`, `gen_14_elite_2` — [`configs/generation_16_pilot_population.json`](../../configs/generation_16_pilot_population.json), built by [`scripts/make_gen16_pilot_population.py`](../../scripts/make_gen16_pilot_population.py) from the Gen-14 genomes that Gen 15 ran. The only field changed is `epistemic_policy.enabled`; the gene's other fields are the defaults (`search_budget_moves 40`, `branching_k 3`, `c_puct 1.4`, `low_prior_quota 0.34`, `max_hypothesis_rounds 2`, `max_stagnant_moves 8`). |
| **Held fixed** | Task [`configs/tasks/self-hosting-fullstack-iter5.json`](../../configs/tasks/self-hosting-fullstack-iter5.json) (50 held-out tests, `max_iterations 5`), model `nvidia/Qwen3.8-Flash-Next-NVFP4` on vLLM behind the Gen 15 `llm-d` gateway, same objective string, same verifier. Gen 15 reference trajectories are embedded in the population file (`pilot_design.gen15_reference`). |
| **Infra** | GKE cluster `chavoshi-v6-pilot` (`us-central1-b`): 1 × `e2-standard-8` for the gateway + 1 × **spot `g4-standard-96`** (2 × RTX PRO 6000) for one vLLM replica — [`k8s/gen16-pilot-serving.yaml`](../../k8s/gen16-pilot-serving.yaml). Spot G4 capacity was unavailable in `us-east5-a/b/c` and `us-east4-b/c`; `us-central1-b` had it. |
| **Harvest** | Pod → GCS still returns 403 (as in Gen 15), so each pod prints its `generation_16/` outputs as a base64 tarball at the end of its log and [`scripts/harvest_gen16_pilot.py`](../../scripts/harvest_gen16_pilot.py) extracts it. [`scripts/summarize_gen16_pilot.py`](../../scripts/summarize_gen16_pilot.py) builds [`generation_16_pilot_summary.json`](generation_16_pilot_summary.json) and the per-firm `traces/*.txt` (pod logs themselves are git-ignored). |

### Passes

| pass | image (commit) | firms | why |
|---|---|---|---|
| 1 | `v6-gen16-pilot` (`23077c9`) | all four | first live run — exposed findings #1 and #2; search never engaged (`hypotheses_tested = 0`) |
| 2 | `v6-gen16-pilot-r2b` (`e14fd03`) | `elite_1`, `crossover_2`, `mutant_1` (`elite_2` omitted: it hit 50/50 at iteration 1 in pass 1, so its search never runs) | **the result pass** — proposer and round caps fixed; exposed #3, #4, #5 |
| 3 | `v6-gen16-pilot-r3` (`998538d`) | `elite_1` only | bounded check of the #3 fix on the firm that exposed it — **aborted** after iteration 2: iteration 1 (identical in `V5`/`V6`) drew a 7/50 start that seeded 18 questions (see §4, "iteration-1 variance" and finding #6). No GPU time was spent beyond `crossover_2`'s pass-2 finish. |

---

## 2. Results

Held-out score per iteration (`passed/50`). Gen 15 = `V5`, same genome, same task.

| firm | Gen 15 (`V5`) | pass 1 | **pass 2 (`V6`, working code)** | pass 3 |
|---|---|---|---|---|
| `gen_14_elite_1` | `[49, 49, 49, 49, 49]` | `[47, 47, 47, 47, 47]` | `[48, 48, 48, 48, 48]` | `[7, 7]` (aborted) |
| `gen_14_crossover_2` | `[47, 49, 49, 49, 49]` | `[8, 8]` (terminated, proposer broken) | **`[48, 48, 50]` — converged at iteration 3** | — |
| `gen_14_mutant_1` | `[48, 48, 48, 48, 48]` | `[48, 48, 48, 48, 48]` | `[49, 49, 49, 49, 49]` | — |
| `gen_14_elite_2` | `[46, 46, 46, 46, 46]` | `[50]` (iteration 1, search never ran) | — | — |

Ledger audit for pass 2 (every number recomputed from the ledger, not from the loop's counters — [`hae/epistemic/audit.py`](../../hae/epistemic/audit.py)):

| firm | questions (certified) | hypotheses tested (S / F) | syntheses (verified) | forced low-prior picks | low-prior wins | Brier | moves | `ΔU` resolved | net fitness | cost | wall-clock |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `elite_1` | 2 (0) | 1 (1 / 0) | 0 | 9 | 0 | 0.123 | 35 / 160 | 0.45 | 86.72 | \$0.034 | 9.5 min |
| `crossover_2` | 2 (**2**) | 10 (3 / 5, + 2 certified) | 9 (2) | 2 | **4** | 0.418 | 23 / 160 | **1.00** | **97.77** | \$0.067 | 48 min |
| `mutant_1` | 1 (0) | 18 (0 / **18**) | 2 (2, both later falsified by the oracle) | 4 | 0 | 0.137 | 27 / 160 | 0.50 | 87.14 | \$0.070 | 37 min |

> [!IMPORTANT]
> **Net fitness is not comparable across `V5` and `V6`.** A ledger switches the composite to `60 % execution · 25 % ledger integrity · 15 % search efficiency` (Gen 15 nets for these genomes were 96.65 / 96.65 / 94.23 / 90.06 under the prose composite). Compare the **held-out trajectories**, which are the same oracle in both generations.

### What the ledgers say

**`gen_14_crossover_2` — plateau broken** ([tree](r2/outputs/gen_14_crossover_2/generation_16/gen_14_crossover_2_epistemic_tree.json), [trace](r2/traces/gen_14_crossover_2_epistemic_trace.txt)). Two failing tests in `hae/genome/morphogenesis.py` (`GenomeValidationError: DepartmentGenome 'dept_systems_eng' has no manager`). Iteration 2: the 0.75-prior explanation for `q2` (`recombine` fails to *copy* the manager) was **FALSIFIED** by its own probe; the 0.15-prior one (the manager is passed as a raw dict / wrong type) was **SUPPORTED** (`ΔU = 0.32`); four synthesis attempts (two on each of the two SUPPORTED hypotheses) re-emitted the module unchanged (finding #5) and the search stopped on stagnation. Iteration 3: a fresh round proposed `h10` (prior **0.10**: *"`recombine` identifies the manager but never passes it to the `DepartmentGenome` constructor"*), the gatekeeper SUPPORTED it, the synthesis changed the module, the module check passed — and the held-out oracle went to **50/50**. Both questions were then **CERTIFIED by oracle evidence** at the final reconciliation. Brier is poor (0.418) precisely because the firm's intuition was inverted on this bug: high priors died, low priors won (`low_prior_wins = 4`). This is the mechanism the design is built around — *the prior steers, the evidence decides*.

**`gen_14_mutant_1` — honest failure** ([tree](r2/outputs/gen_14_mutant_1/generation_16/gen_14_mutant_1_epistemic_tree.json), [trace](r2/traces/gen_14_mutant_1_epistemic_trace.txt)). One failing test (`AssertionError: 6 != 4`, `authored_files` count in the harness). 18 hypotheses, **18 falsified**: a forced low-prior pick (`h6`, 0.20) and `h13` (0.35) were SUPPORTED by their probes, synthesised, passed `py_compile`/import — and the held-out test still failed, so the final oracle reconciliation reopened the question and **FALSIFIED both patched hypotheses**. The firm ends with 18 evidence-backed ruled-out beliefs and zero claims it cannot back (`evidence_backed_fraction 1.0`, `status_inconsistencies 0`). Also the firm that exposed finding #4: seven of its 18 hypotheses were paraphrases of mechanisms it had already ruled out.

**`gen_14_elite_1` — starved by a syntax error** ([tree](r2/outputs/gen_14_elite_1/generation_16/gen_14_elite_1_epistemic_tree.json), [trace](r2/traces/gen_14_elite_1_epistemic_trace.txt)). Two failing harness tests. `q1`'s 0.65-prior hypothesis was SUPPORTED on move 3 (`ΔU = 0.895`) and never synthesised: all three probes for `q2` nested single quotes inside a TOML string, the gatekeeper refused them (`probe is not valid Python`), the hypotheses stayed `UNVERIFIED` with growing visit counts, and PUCT re-selected `h4` **17 times** — iterations 3–5 each burnt their 8 stagnation moves on the same error while `q2` (uncertainty 1.0) out-ranked `q1` (0.105) in question selection. Finding #3.

**`gen_14_elite_2` / iteration-1 variance.** Iteration 1 is the unchanged `V5` full-company pass in both modes. `elite_2` hit 50/50 on its first pass (Gen 15: 46/50, five times), `crossover_2` drew 8/50 in pass 1 and 48/50 in pass 2, `elite_1` drew 47, 48 and **7**/50 across the three passes. With `n = 1` per firm per pass, the first-pass draw dominates the trajectory; the pilot's within-genome comparison is therefore qualitative. A Stage 3 cohort needs several seeds per genome before any mean is quoted.

---

## 3. Findings and fixes

| # | finding (evidence) | fix | commit |
|---|---|---|---|
| 1 | **Grammar-constrained decoding cannot emit a multi-line `probe_code` string.** Under `xgrammar`, every `HYPOTHESIS_SET` reply degenerated into a repetition loop (`import inspect, sys, …, _base._base…`) until `finish_reason = length` at 1 600 tokens; claims were hard-cut at `maxLength 200`. Pass 1: `hypotheses_tested = 0` for every firm, iterations 3–5 ran 0 moves ([pass-1 traces](traces/)). | Probe transported as a `probe_lines` **array** (≤ 60 lines); truncated packets salvaged item-by-item; `max_tokens` 3 000; parser accepts legacy `probe_code`. Validated live: same question → `finish_reason = stop`, 3 hypotheses, 13–16-line probes. | `a1bb3b8` |
| 2 | **`max_hypothesis_rounds` was a lifetime cap per question**, so questions that spent both rounds on broken replies in iteration 2 were dead for iterations 3–5 (`0 moves, stop=exhausted`). | Rounds reset per search run; the cross-iteration memory is the tabu list, not the counter. | `e14fd03` |
| 3 | **Refused probes stayed `UNVERIFIED` and fed PUCT forever** (`h4` visits = 17; 32 of `elite_1`'s 35 moves were refused probes). | New `UNTESTABLE` status: a refused probe parks the hypothesis (no belief change, nothing ruled out, mechanism stays proposable); the ledger summary shows the proposer `PROBE REFUSED (…) — re-propose with a corrected probe_lines array`; a re-proposal of the same mechanism **repairs the probe in place** (same id and prior, so calibration scores the original belief). Synthesis gate and audit count only real verdicts. 9 tests. | `998538d` |
| 4 | **Tabu memory was lexical enough to paraphrase around.** `mutant_1` re-proposed 7 already-falsified mechanisms with the same claim and a reworded mechanism paragraph: full-signature Jaccard 0.46–0.71 (< 0.8 threshold); ~10 of 18 experiments re-falsified dead mechanisms. Measured on the pilot ledgers: duplicate pairs have claim-Jaccard 1.00 and overlap coefficient 0.83–0.88; every distinct pair on the same question ≤ 0.48 / ≤ 0.67. | `same_mechanism()` = full Jaccard ≥ 0.8 **or** containment ≥ 0.75 (shorter signature ≥ 6 tokens) **or** claim Jaccard ≥ 0.8; used by tabu, in-round duplicate and repair matching. 9 tests pin the pilot's own texts on both sides of the rule. | `c869dcc` |
| 5 | **Silent / no-op syntheses.** `crossover_2` spent 7 of its 9 synthesis moves (each a full-module LLM rewrite) re-emitting the module byte-for-byte; the move log said `synthesis wrote nothing` next to a `CODE_ARTIFACT_WRITTEN … SUCCESS` packet, and nothing reached the pod log. | Synthesis failures are logged as `[epistemic]` lines with the failure count; the adapter now reports `module unchanged: synthesiser re-emitted identical content`. **Open for Stage 3:** ask the synthesiser for a targeted edit / diff rather than a complete rewrite, and tell it when its previous attempt was a no-op. | `c869dcc`, this commit |
| 6 | **No question triage for catastrophic starts.** Pass 3's 7/50 iteration 1 seeded 18 questions; selection (uncertainty, then fewest hypotheses) proposed breadth-first — 18 of the 40-move budget would go to proposals before a single experiment. The same rule made `crossover_2` leave a SUPPORTED-but-unsynthesised `q1` (u = 0.105) for a fresh `q2` (u = 1.0). | Question **frontier + triage** in `mcts.py`: at most `frontier_size` (new gene, default 3, bounds [1, 8]) open questions at a time, one per cluster (`module` + `failure_signature` — exception class and message with numbers/paths/literals masked); the next move goes to a question with a synthesis ready, then one with experiments pending, then the most uncertain. Once a question resolves, its cluster siblings are deferred to the oracle (`stop = deferred_to_oracle`, `questions_deferred` in the audit) and certified next iteration if the patch was the common cause. On the 18-question shape: 4 moves to a verified patch, 17 deferred, instead of 18 proposal rounds. 15 tests; the behavioural ones fail under the old rule. | `a3a0224` |

Two things the pilot confirmed rather than found: the **forced low-prior quota works mechanically** (15 forced picks across the three firms, 1 forced SUPPORTED — `mutant_1`'s `h6`, later falsified by the oracle), and **oracle reconciliation closes the loop honestly** (vanished failures → `CERTIFIED` with oracle evidence; a patched hypothesis whose test still fails → `FALSIFIED`).

---

## 4. Cost and teardown

- **LLM** (metered by the firms' own opex on the local vLLM): pass 1 ≈ \$0.12, pass 2 ≈ \$0.17 (≈ 1.2 M tokens); pass 3 was aborted before its scorecard, so it is unmetered (≈ 17 min of one firm, a few cents). ≈ **\$0.30** nominal in total.
- **GPU:** one spot `g4-standard-96` from `01:38` to `03:17 UTC` (≈ 1.65 h; model download + vLLM ready took 11 min of that) plus one `e2-standard-8` for the gateway over the same window. Spot pricing was not metered from inside this run; at the list spot rate this is single-digit dollars.
- **Teardown:** Jobs `hae-gen16-v6-pilot-r2` / `-r3` deleted, serving stack deleted, then the whole cluster `chavoshi-v6-pilot` deleted (`gcloud container clusters delete … --quiet`). Verified afterwards: no `chavoshi-v6-pilot*` or `g4-*` instances in project `gemle-gke-dev` (`gcloud compute instances list` — output in the final report). The pre-existing CPU cluster `chavoshi-evolution-east4` was not touched.

---

## 5. What this pilot does and does not show

- **Shows:** the `V6` engine runs end-to-end on the production stack; the gatekeeper's verdicts, oracle certification/falsification and the audit are all consistent (`status_inconsistencies = 0`, `evidence_backed_fraction = 1.0` for every firm); one plateaued genome converged under `V6` via a low-prior hypothesis; proposer priors on these bugs are badly calibrated (Brier 0.12–0.42), which is the empirical case for a learned value head (`V7`).
- **Does not show:** a statistically meaningful lift. `n = 1` per firm per pass, iteration-1 variance is large, and the search budget (`40` moves, `k = 3`) was never tuned. Nothing here should be quoted as "V6 beats V5 by X".
- **Stage 3 go / no-go:** go — finding #6 is addressed (`a3a0224`); run with ≥ 3 seeds per genome, land the targeted-edit synthesis (finding #5) first, and keep `elite_2`-style first-pass convergers in the cohort (they are the control for the `V5` composite path).

## 6. Files

```
results/hae_gen16_v6_pilot/
├── README.md                             # this report
├── generation_16_pilot_summary.json      # all passes, per firm: trajectory, scores, audit, hypothesis outcomes
├── pilot_status.json                     # pass-1 watcher snapshot
├── outputs/<firm>/generation_16/         # pass-1 epistemic trees (elite_1, mutant_1); pass-1 scorecards are
│                                         #   git-ignored (outputs/) — their numbers are in the summary JSON
├── traces/<firm>_epistemic_trace.txt     # pass-1 evidence (committable extract of the pod logs)
├── r2/outputs/<firm>/generation_16/      # pass-2 scorecards + epistemic trees (all three firms, force-added)
├── r2/traces/, r2/pilot_status.json
└── r3/traces/gen_14_elite_1_epistemic_trace.txt   # pass-3 (aborted) trace
```
