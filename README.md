# Hierarchical Agent Evolution (HAE)

HAE is a research codebase for building multi-agent LLM systems that repair and
build real software, and for scoring them only on what executes. It started as
an experiment in *evolving* agent organisations ("firms") described by a
heritable `CompanyGenome`. Over 14 versions it became a hierarchical
verification harness that runs agents inside sandboxed containers and accepts a
patch only after deterministic tests and an adversarial reviewer agree.

All inference runs on self-hosted open-weight MoE models on GKE (vLLM behind an
`llm-d` gateway). There are no hosted-API calls in the evaluation path.

**Current version: V14.** It has two tiers:

- **Inner engine (`L0-Micro`)**: context-bounded tree search over git
  checkpoints, used for single bug fixes (`hae/swebench/hierarchical_mcts.py`).
- **Outer ratchet (`L0-Macro`)**: splits a larger build into milestones and
  commits each verified milestone to git, so every step works on a small diff
  (`hae/hierarchical/`).

---

## Current results

All SWE-bench numbers are graded by the official `swebench.harness` containers
(`FAIL_TO_PASS` and `PASS_TO_PASS` tests). The *Hints* column records
whether the prompts contained repository- or instance-specific guidance or
`hints_text` (`--include-hints`). See [Caveats](#caveats).

![V14 Zero-Hint Benchmark & 5-Way Component Ablation Study](experiments/v14/assets/v14_ablation_and_verified_benchmark.svg)

### 1. 5-way component ablation on the 6-task multi-repo slice (`--include-hints` OFF)

Removing one layer at a time on `Beam-1.0-501B-A23B-NVFP4` (`+ DFlash2`) with
both prompt hints and SWE-bench `hints_text` disabled (`30` live pod runs):

| Policy / Ablation | Architecture enabled | Hints | Resolved | F2P tests | Moves | Tokens | Time |
| :--- | :--- | :---: | :---: | :---: | ---: | ---: | ---: |
| **V14 full (`v13_hier_mcts`)** | **Recon + L0 (`GEN`+`CONT`) + L2 + Falsifier** | **none** | **6 / 6 (100%)** | **57 / 57** | **23.3** | **148k** | **279 s** |
| `v14_no_falsifier` | Ablate `L1-B` adversarial falsifier | none | 4 / 6 (66.7%) | 55 / 57 | 22.0 | 157k | 364 s |
| `v14_no_cont` | Ablate `CONT` delta-refinement (`GEN`-only) | none | 4 / 6 (66.7%) | 55 / 57 | 26.0 | 158k | 368 s |
| `v14_no_recon` | Ablate `L1-A` reconnaissance | none | 2 / 6 (33.3%) | 29 / 57 | 31.7 | 170k | 446 s |
| `v12_agentic` (ReAct) | Single-layer ReAct baseline (no hierarchy) | none | 0 / 6 (0.0%) | 14 / 57 | 25.5 | 134k | 311 s |
| *Prior V13 (`Beam-501B`)* | *Full hierarchy (with `hints_text` & repo hints)* | *specific* | *6 / 6 (100%)* | *57 / 57* | *22.3* | *150k* | *306 s* |
| *Prior V13 (`Qwen3.8-180B`)* | *Full hierarchy (with `hints_text` & repo hints)* | *specific* | *6 / 6 (100%)* | *57 / 57* | *33.8* | *334k* | *1,011 s* |
| *Prior V12 ReAct (`Beam-501B`)* | *Single-layer ReAct (with `hints_text`)* | *light* | *2 / 6 (33.3%)* | *29 / 57* | *27.3* | *162k* | *331 s* |
| *Prior V12 ReAct (`Qwen3.8-180B`)* | *Single-layer ReAct (with `hints_text`)* | *light* | *2 / 6 (33.3%)* | *29 / 57* | *30.0* | *273k* | *1,177 s* |

<sub>Moves, tokens and time are per-instance means. All 4 hierarchical arms had
`214 / 214` `PASS_TO_PASS` (`0` regressions); `v12_agentic` had `213 / 214`.</sub>

Every architectural layer earns its cost and reduces wall-clock latency:
- Removing **L1-B Falsifier** or **CONT refinement** drops resolution by
  `-33.3 pp` (`6/6 → 4/6`, failing `marshmallow-1359` and `pydicom-1255`).
- Removing **L1-A Recon** drops resolution by `-66.7 pp` (`6/6 → 2/6`) and
  increases mean wall-clock time by `+60.0%` (`279s → 446s`) as executors burn
  turns grepping inside `/testbed`.
- Without `hints_text`, the single-layer ReAct baseline (`v12_agentic`) drops to
  `0 / 6` (`14 / 57` F2P), while **V14** solves **`6 / 6` (`57 / 57` F2P)** in
  `279s` (`3.6×` faster than `Qwen3.8-180B`).

Report: [`experiments/v14/v14_verified_and_ablation_report.md`](experiments/v14/v14_verified_and_ablation_report.md) ·
ablation data: [`results/swebench/v14_component_ablation_2026-10-21/`](results/swebench/v14_component_ablation_2026-10-21/README.md) ·
model comparison: [`experiments/v13/beam_vs_qwen38_evaluation_report.md`](experiments/v13/beam_vs_qwen38_evaluation_report.md)

### 2. Unseen SWE-bench Verified & combined zero-hint evaluation (`--include-hints` OFF)

Frozen V14 and V12 ReAct were evaluated head-to-head on 12 container-verified
instances from `princeton-nlp/SWE-bench_Verified` across 8 repositories
(`astropy`, `matplotlib`, `seaborn`, `flask`, `requests`, `xarray`, `pylint`,
`sphinx`), disjoint from the 50-task dev pool and with `--include-hints` OFF
(`24` live pod runs):

| Policy | Dev slice (`n = 6`) | Unseen Verified (`n = 12`) | **Combined (`n = 18`)** | Total F2P | Total P2P |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **V14 (`v13_hier_mcts`)** | **6 / 6 (100%)** | **9 / 12 (75.0%)** | **15 / 18 (83.3%)** | **74 / 79 (93.7%)** | **3,747 / 3,747 (100%)** |
| V12 ReAct (`v12_agentic`) | 0 / 6 (0.0%) | 7 / 12 (58.3%) | 7 / 18 (38.9%) | 28 / 79 (35.4%) | 3,746 / 3,747 (99.97%) |
| **Delta (`V14 − V12`)** | **+100.0 pp** | **+16.7 pp** | **+44.4 pp (2.14×)** | **+46 F2P (+58.2 pp)** | **0 regressions** |

On the unseen `Verified` slice, V14 resolves `9 / 12` (`75.0%`, `17 / 22` F2P,
`3,533 / 3,533` P2P, `412.6s` mean latency) vs. `7 / 12` (`58.3%`, `14 / 22`
F2P) for V12 ReAct, winning head-to-head on `pydata__xarray-2905` (via `L1-B`
adversarial falsification and `CONT` delta-refinement) and
`pylint-dev__pylint-6386` (via `L1-A` IDF-weighted symbol discovery of
`pylint/config/utils.py`), with zero instance-level regressions (`V14 >= V12`
on `18 / 18` tasks).

Report: [`results/swebench/verified_unseen_eval_2026-10-21/`](results/swebench/verified_unseen_eval_2026-10-21/README.md)

### 3. Milestone ratchet, 4-milestone build

A small language toolchain (`MiniLang`) was built as four dependent milestones:
lexer → parser → tree-walking evaluator → bytecode compiler and VM.

| Milestone | Moves | Time | Diff size | Repo size | Cumulative tests |
| :--- | ---: | ---: | ---: | ---: | ---: |
| `m1_lexer` | 11 | 56 s | 274 lines | 263 LoC | 10 |
| `m2_parser` | 10 | 57 s | 328 lines | 564 LoC | 14 |
| `m3_evaluator` | 10 | 51 s | 257 lines | 798 LoC | 22 |
| `m4_bytecode_vm` | 18 | 79 s | 364 lines | 1,139 LoC | 25 |
| **Total** | **49** | **246 s** | max 364 | 1,139 LoC | 25 / 25 |

The run shows the mechanism working end to end. The total repository keeps
growing, but each milestone's diff stays under the 500-line cap that the
verifier has to read. Every certified milestone is a tagged commit, and earlier
milestones' tests re-run as a regression guard.

Report: [`results/ratchet/beam_macro_ratchet_eval_2026-10-21/`](results/ratchet/beam_macro_ratchet_eval_2026-10-21/README.md)

### Caveats

- **The earlier V12 and V13 SWE-bench dev numbers were produced with
  instance-specific prompt hints.** The V13 prompts grew from 3 repo-specific
  strings (the 15/30 and 22/30 runs) to 25 in the 30/30 run (`e20a400`). Several
  of those 25 describe the gold fix for specific failing instances, such as the
  file list and changes for `pydicom-1255` and the `parse_noqa` logic for
  `sqlfluff-891`. V14 removed all of them:
  `grep -ciE "sqlfluff|pydicom|pyvista|marshmallow|astroid" hae/swebench/*.py`
  returns 0. **Treat the earlier hinted V12/V13 runs as development runs, not
  benchmark scores.**
- **The 6-task ablation slice comes from the dev pool; the 12-task Verified
  slice is unseen.** Some of V14's generic AST checks (such as range validation
  across sibling property setters) were developed while debugging the 6-task dev
  slice. The 12-task `SWE-bench Verified` evaluation was run afterwards on
  unseen instances with `--include-hints` OFF (`hints_included: false`).
- **Sample size and seeds.** The zero-hint evaluations cover 18 tasks (`6` dev +
  `12` unseen `Verified`, `54` pod runs total) at a single seed per arm, rather
  than the full 500-task `SWE-bench Verified` or `SWE-bench Pro` public split.
- **In the ratchet run, the milestones were written by hand** (in
  `scripts/run_macro_ratchet_beam_eval.py`), not produced by `L0MacroPlanner`.
  The agent also wrote each milestone's acceptance tests. Builds far beyond
  ~1k LoC are a design goal that hasn't been measured yet.

The [Roadmap](#roadmap) lists the next steps to scale these results.

---

## How it works

![V14 two-tier architecture](experiments/v14/assets/v14_macro_ratchet_architecture.svg)

### Inner engine: context-bounded hierarchical search (`L0-Micro`)

Each layer runs in its own short-lived context and passes upward only
verifiable artifacts: a brief, a diff or a critique. Raw transcripts are
discarded at every boundary, so no context accumulates across the search.

| Layer | Role | Turns | Returns |
| :--- | :--- | :---: | :--- |
| **L1-A Recon** | Locate the fault; write a failing repro script | 0–6 | Brief (< 800 tokens) |
| **L0 Controller** | Write the contract; pick `GEN` or `CONT` | — | Next move |
| **L2 Executors** | Edit code in `/testbed` with ReAct tools | 6–18 | Candidate `git diff` |
| **L1-B Verifier** | Run tests, then adversarial review | 4–6 | Score V and critique |

- **L0 Controller.** It seeds three causal branches: local consumer, upstream
  producer and codebase symmetry. It chooses each next move with Thompson
  sampling over Beta posteriors and keeps only a small ledger (under 4k tokens).
- **L2 Executors.** `GEN` starts a fresh branch from clean `HEAD`. `CONT`
  refines a near-miss checkpoint, with its diff and the failing test
  pre-loaded.
- **L1-B Verifier.** Stage A is deterministic: compile, repro script and
  target tests. Stage B is a read-only falsifier agent that audits the diff
  against the contract and adds edge-case tests to a shared suite.

A patch is accepted only when both stages pass (V = 1.0). A failure or
critique updates the controller's posteriors. Near-misses get refined, and
stalled branches lose probability mass to the other branches.

Spec: [`experiments/v13/v13_hierarchical_agentic_ab_mcts_architecture.md`](experiments/v13/v13_hierarchical_agentic_ab_mcts_architecture.md)

### Outer ratchet: milestones as git commits (`L0-Macro`)

A larger build is expressed as a DAG of milestones. Each milestone has target
files, an `acceptance_cmd` and a diff budget (default 500 lines).

1. The inner engine solves milestone *k* starting from `HEAD_{k-1}`.
2. The milestone must pass its own acceptance command, every earlier
   milestone's acceptance command, and every archived falsification suite.
3. `GitCommitRatchet` commits the result, tags it `hae-milestone-<id>`, and
   saves `.hae/ratchet_state.json`, so a crashed run can `--resume`.

Because finished work is committed, `git diff HEAD` only ever contains the
current milestone. Recon and the falsifier therefore see a bounded diff
however large the repository gets. Verification is pluggable through
`WorkspaceVerifierPlugin`, which ships with pytest and shell-command verifiers
(for `cargo test`, `go test`, `npm test` and similar).

Spec: [`experiments/v14/v14_generalized_macro_ratchet_architecture.md`](experiments/v14/v14_generalized_macro_ratchet_architecture.md)

### Serving

| | Beam-501B (primary) | Qwen3.8-180B |
| :--- | :--- | :--- |
| Layout | 3 replicas × TP=4 (Ray) | 6 replicas × TP=2 + EPLB |
| Decoding | DFlash2 speculative (8 tokens) | standard |
| Manifest | [beam-501b-nvfp4-serving.yaml](k8s/beam-501b-nvfp4-serving.yaml) | [qwen38-…-eplb-serving.yaml](k8s/qwen38-flash-next-180b-eplb-serving.yaml) |

Both run on 6× `g4-standard-96` nodes (12× RTX PRO 6000 Blackwell, 96 GB
each).

Both run behind the `llm-d` gateway (prefix-affinity routing, request
coalescing). Setup guide: [`k8s/README.md`](k8s/README.md)

---

## Project history

HAE went through three eras. The full phase-by-phase table is in
[`experiments/README.md`](experiments/README.md).

| Era | Versions | Benchmark | Key finding |
| :--- | :---: | :--- | :--- |
| **Execution-grounded evolution** | V1–V7 | Internal 7- and 50-test tasks | LLM judges are gameable; held-out tests work |
| **SWE-bench, single-layer agents** | V8–V12 | 30-task dev cohort | ReAct tool loops: 4 / 30 → 14 / 30 |
| **Hierarchical verification** | V13–V14 | 6-task ablation, 12-task Verified, MiniLang | Hierarchy more than doubles zero-hint resolution vs ReAct (15 / 18 vs 7 / 18) |

Since V8, each SWE-bench version has been a single hand-designed genome
(`configs/genomes/`). Population-level evolution, breeding and selection have
not been applied to the V13/V14 harness yet.

---

## Roadmap

The aim is to turn V14 from a slice-validated harness into a full-scale benchmark
and evolutionary result. The steps are in priority order.

| # | Step | Status | Next milestone |
| :-: | :--- | :--- | :--- |
| 1 | **Component ablations** | **Done (`n = 6`, 30 runs)** | Extend ablation to unseen `Verified` tasks |
| 2 | **Unseen benchmark (`--include-hints` OFF)** | **Pilot done (`n = 12` Verified, `9/12` vs `7/12`)** | Scale to full `SWE-bench Verified` / `SWE-bench Pro` public split (3 seeds) |
| 3 | **Real long-horizon build** | **Mechanism proven (`1,139` LoC MiniLang)** | ≥ 10k LoC planner-driven build passing an external test suite |
| 4 | **Evolve the harness** | Planned | An evolved V14 genome beats hand-designed V14 on held-out tasks |

**1. Scale the unseen benchmark evaluation.**
- Expand the zero-hint (`--include-hints` OFF) evaluation from the 12-task
  `SWE-bench Verified` pilot (`9/12 = 75.0%` vs `7/12 = 58.3%`) to a larger
  stratified sample of `SWE-bench Verified` and `SWE-bench Pro` (public), with 3
  seeds and matched token budgets against OpenHands / mini-SWE-agent on Beam.
- Address the remaining failure modes surfaced on unseen `Verified`: (a) guard
  interaction between newly added axis/limit blocks and adjacent override blocks
  (`seaborn-3069`, `1/2` F2P passed), (b) disambiguating conflicting issue-text
  conjectures vs. existing test conventions when `hints_text` is disabled
  (`seaborn-3187`, where both gold files were found), and (c) tracing indirect
  state mutations across shared-axis callbacks (`matplotlib-26208`).

**2. Test the ratchet on a real long-horizon build.**
- Let `L0MacroPlanner` produce the milestones automatically rather than from a
  script.
- Choose a target with an external test suite the agent never writes, such as
  a SQLite subset or an interpreter checked against a reference
  implementation.
- Scale to 10k+ LoC and record recovery when a decomposition turns out to be
  wrong.

**3. Bring evolution back.**
- Treat V14's knobs as genes: turn budgets, branch types, falsifier policy,
  contract templates and diff thresholds.
- Evolve a population on a training split of tasks and select on a validation
  split.
- Report the result against hand-designed V14 on held-out tasks. An evolved
  harness that beats a hand-built one for open models is the result this
  project is uniquely placed to show.

---

## Repository layout

```text
hae/
  hierarchical/   L0-Macro milestone ratchet (milestone_ratchet.py) and verifier plugins (verifier_plugin.py)
  swebench/       L0-Micro hierarchical AB-MCTS (hierarchical_mcts.py), V12 ReAct engine (agentic.py), executor, runner
  epistemic/      V6–V11 epistemic ledger, gatekeeper, PUCT and packet-level AB-MCTS, V7 value heads
  genome/         CompanyGenome schema, mutation, crossover, role seeds
  runtime/        firm runner, workspaces, code overlays
  evaluation/     benchmarks, execution harness, fitness
  orchestration/  tournament engine, GKE worker, breeder
  infra/          LLM providers (vLLM, Vertex), config, preflight, telemetry
  task/           task specs, budgets, verifiers
  cli.py          entry point
configs/          per-generation populations, SWE-bench genomes (genomes/), task definitions
k8s/              GKE cluster setup and serving / job manifests
scripts/          SWE-bench batch evaluator, ratchet eval, harvesters, summaries
experiments/      per-version design notes and evaluation reports
results/          harvested runs: patches, grade.json, records, summaries
data/swebench/    50-instance dev pool manifest (disjoint from SWE-bench Verified)
tests/            stdlib unittest suite, no network access needed
```

---

## Running

**Tests.** These need Python 3.10+ and only the standard library:

```bash
PYTHONPATH=. python3 -m unittest discover -s tests -q
```

> 908 tests, 0 failures, 10 skipped.

**Serve a model on GKE.** See [`k8s/README.md`](k8s/README.md) for cluster
setup.

```bash
kubectl apply -f k8s/beam-501b-nvfp4-serving.yaml
kubectl get pods -l app=beam-501b-nvfp4 -w            # ~4–5 min to load
kubectl port-forward svc/vllm-qwen38-gateway 8000:8000
```

**Run SWE-bench (zero-hint default).** Use `--instances` to pick specific tasks:

```bash
PYTHONPATH=. python3 -u scripts/swebench_kubectl_batch.py \
  --out-root results/swebench/my_run \
  --policies v13_hier_mcts \
  --vllm-url http://127.0.0.1:8000/v1 \
  --vllm-model reflection/Beam-1.0-501B-A23B-NVFP4 \
  --workers 3 \
  --instances marshmallow-code__marshmallow-1702
```

**Run a milestone build:**

```bash
PYTHONPATH=. python3 -m hae.cli --mode ratchet \
  --workspace-dir /path/to/repo \
  --objective "Build a tokenizer, recursive-descent parser and evaluator with unit tests." \
  --acceptance-cmd "pytest -q" \
  --resume
```

The MiniLang run above came from `scripts/run_macro_ratchet_beam_eval.py`,
which defines its milestones explicitly.

---

## License

Apache-2.0. See [`LICENSE`](LICENSE).
