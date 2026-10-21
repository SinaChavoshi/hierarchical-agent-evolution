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
whether the prompts contained repository- or instance-specific guidance. See
[Caveats](#caveats).

### SWE-bench, 6-task multi-repo slice

| Policy | Model | Hints | Resolved | Moves | Tokens | Time |
| :--- | :--- | :---: | :---: | ---: | ---: | ---: |
| **V14 zero-hint** | **Beam-501B** | **none** | **6 / 6** | **23.3** | **148k** | **279 s** |
| V13 | Beam-501B | specific | 6 / 6 | 22.3 | 150k | 306 s |
| V13 | Qwen3.8-180B | specific | 6 / 6 | 33.8 | 334k | 1,011 s |
| V12 ReAct | Beam-501B | light | 2 / 6 | 27.3 | 162k | 331 s |
| V12 ReAct | Qwen3.8-180B | light | 2 / 6 | 30.0 | 273k | 1,177 s |

<sub>Moves, tokens and time are per-instance means. Beam-501B is
Beam-1.0-501B-A23B-NVFP4 with DFlash2 speculative decoding. Qwen3.8-180B is
Qwen3.8-Flash-Next-NVFP4. V13 and V14 both run the `v13_hier_mcts` policy;
V12 runs `v12_agentic`.</sub>

The zero-hint run passed 57/57 `FAIL_TO_PASS` and 214/214 `PASS_TO_PASS` tests.
With the same 501B model, the single-context ReAct agent (V12) solves 2/6 and
the hierarchical harness solves 6/6. The harness, not the model, is what moves
resolution. The model swap mainly buys speed: about 3.6× less wall-clock time
and less than half the tokens.

Report: [`results/swebench/v14_zero_hint_beam_eval_2026-10-21/`](results/swebench/v14_zero_hint_beam_eval_2026-10-21/README.md) ·
model comparison: [`experiments/v13/beam_vs_qwen38_evaluation_report.md`](experiments/v13/beam_vs_qwen38_evaluation_report.md)

### Milestone ratchet, 4-milestone build

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

- **The V12 and V13 SWE-bench numbers were produced with instance-specific
  prompt hints.** The V13 prompts grew from 3 repo-specific strings (the 15/30
  and 22/30 runs) to 25 in the 30/30 run (`e20a400`). Several of those 25
  describe the gold fix for specific failing instances, such as the file list
  and changes for `pydicom-1255` and the `parse_noqa` logic for `sqlfluff-891`.
  V14 removed all of them: `grep -ciE "sqlfluff|pydicom|pyvista|marshmallow|astroid" hae/swebench/*.py`
  returns 0. **Treat the hinted results as development runs, not benchmark
  scores.**
- **The 6-task slice is not held out.** It comes from the 30-task dev cohort
  the harness was developed against. Some of V14's generic AST checks (for
  example, range validation across sibling property setters) were added while
  debugging these instances.
- **SWE-bench `hints_text` (issue comments) was given to the agent**
  (`hints_included: true`). Standard leaderboard runs exclude it.
- **Each result is a single run** with no repeated seeds. A one-task difference
  on n = 6 is about 17 percentage points.
- **In the ratchet run, the milestones were written by hand** (in
  `scripts/run_macro_ratchet_beam_eval.py`), not produced by `L0MacroPlanner`.
  The agent also wrote each milestone's acceptance tests. Builds far beyond
  ~1k LoC are a design goal that hasn't been measured yet.

The [Roadmap](#roadmap) lists the measurements that would turn these into
comparable results.

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
| **Hierarchical verification** | V13–V14 | 6-task slice, MiniLang | Hierarchy beats ReAct on the same model (6 / 6 vs 2 / 6) |

Since V8, each SWE-bench version has been a single hand-designed genome
(`configs/genomes/`). Population-level evolution, breeding and selection have
not been applied to the V13/V14 harness yet.

---

## Roadmap

The aim is to turn V14 from an internally validated harness into a result
others can compare against. The steps are in priority order.

| # | Step | Question it answers | Done when |
| :-: | :--- | :--- | :--- |
| 1 | **Standard benchmark** | Is V14 competitive? | Results reported on SWE-bench Pro (public) |
| 2 | **Ablations** | Which components earn their cost? | Per-component Δ resolve and Δ cost |
| 3 | **Real long-horizon build** | Does the ratchet scale? | ≥ 10k LoC passing an external test suite |
| 4 | **Evolve the harness** | Does evolution beat hand design? | An evolved genome wins on held-out tasks |

**1. Run a standard benchmark.**
- Freeze V14 and run it on Beam-501B on SWE-bench Pro (public), and
  optionally Verified.
- Exclude `hints_text` and use at least 3 seeds.
- Run mini-SWE-agent or OpenHands on Beam with the same token budget as the
  baseline.
- Success means V14 clearly beats that baseline. On Verified, that means
  clearly above Beam's reported ~82% with a standard scaffold. This step alone
  shows whether V14 is competitive.

**2. Run ablations.** Remove one component at a time and record the change in
resolve rate and in cost (tokens and time):
- the L1-B falsifier
- L1-A recon
- `CONT` refinement, leaving only `GEN`
- the AST static checks

**3. Test the ratchet on a real long-horizon build.**
- Let `L0MacroPlanner` produce the milestones, not a script.
- Choose a target with an external test suite the agent never writes, such as
  a SQLite subset or an interpreter checked against a reference
  implementation.
- Scale to 10k+ LoC and record recovery when a decomposition turns out to be
  wrong.

**4. Bring evolution back.**
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

> As of `dc4b0b6`: 906 tests, 20 failures, 3 errors, 11 skipped. The failures
> are mostly in the SWE-bench runner, gatekeeper and executor tests.

**Serve a model on GKE.** See [`k8s/README.md`](k8s/README.md) for cluster
setup.

```bash
kubectl apply -f k8s/beam-501b-nvfp4-serving.yaml
kubectl get pods -l app=beam-501b-nvfp4 -w            # ~4–5 min to load
kubectl port-forward svc/vllm-qwen38-gateway 8000:8000
```

**Run SWE-bench.** Use `--instances` to pick specific tasks:

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
