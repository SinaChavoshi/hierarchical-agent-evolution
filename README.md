# Hierarchical Agent Evolution (HAE)

**Hierarchical Agent Evolution (HAE)** is a distributed evolutionary framework on Kubernetes that breeds multi-agent software engineering organizations (*companies*). Instead of hand-designing static agent workflows, each organization's hierarchy, role definitions, communication schemas, search policy (`epistemic_policy`), and self-authored source changes (`code_overlays`) are encoded in a heritable `CompanyGenome`.

Populations of companies execute in isolated sandboxes against real software tasks, are scored by deterministic test suites and non-LLM reasoning audits, and reproduce across generations via tournament selection, crossover, and mutation.

---

## Core Architecture

- **Heritable Organization Genome (`hae/genome/`):** Defines department structure, worker/manager roles, token budgets, `V5` typed communication contracts, `V6` search hyper-parameters, and inherited source overlays.
- **Typed Inter-Agent Protocol (`V5` / `xgrammar`):** Replaces open-ended prose memos between agents with FSM-enforced JSON schemas at the vLLM decoder, cutting coordination token overhead by ~92% (`1,420` $\rightarrow$ `115` tokens/call).
- **Shared Multi-Company Serving (`llm-d` Gateway):** Deduplicates identical cross-company coordination queries in `<1 ms` (`singleflight` + response short-circuiting) and routes prompts by prefix hash across shared GPU/DRAM KV caches while keeping code synthesis uncached per company.
- **Evidence-Gated Epistemic Search (`V6`, `hae/epistemic/`):** Treats the LLM strictly as a hypothesis generator (*System 1*) and the company runtime as an explicit search engine (*System 2*). Agents propose falsifiable hypotheses with sandbox test probes; only a deterministic Python **Evidence Gatekeeper** can mark a belief `SUPPORTED`, `FALSIFIED` (tabu), or `CERTIFIED` based on actual probe execution before any code patch is synthesized.

---

## Experiment Progression (`V1` – `V7`)

Full generational logs, caveats, and raw artifacts are indexed in [`experiments/README.md`](experiments/README.md).

| Phase | Generations | Focus | Summary of Findings | Report |
| :--- | :--- | :--- | :--- | :--- |
| **`V1`** | `Gen 0–10` | LLM-as-a-Judge grading | **Goodhart's Law:** Subjective prose grading (`99.5/100`) correlated at `r = +0.045` with actual test pass rates (`77.7%`), breeding bloated hierarchies that wrote polished prose over working code. | [`experiments/v1/`](experiments/v1/README.md) |
| **`V2`** | `Gen 1–6` | Deterministic test grading & self-repair | Replacing prose grading with an 18-test suite (`hae/compiler/optimizer.py`) plus a 3-turn test-and-repair rollback loop brought population pass rates to `100%` (`30/30` companies). | [`experiments/v2/`](experiments/v2/README.md) |
| **`V3`** | `Gen 7–9` | Recursive self-improvement (`code_overlays`) | Companies modified HAE's own breeding/scoring code (`fitness.py`, `morphogenesis.py`). Post-run audit uncovered an `inherited_files` benchmark leak in Gen 8–9 and 56 KB of inter-agent memo bloat. | [`experiments/v3_rsi/`](experiments/v3_rsi/README.md) |
| **`V4`** | `Gen 10–11` | Zero-seed baseline & global `llm-d` gateway | Fixed the workspace leak and added cross-company `llm-d` caching/coalescing across `3x Qwen3.8-180B` replicas, reaching `10/10` (`7/7` tests) in Gen 11. | [`experiments/v4_llmd/`](experiments/v4_llmd/README.md) |
| **`V5`** | `Gen 12–15` | `xgrammar` JSON schemas & 4-module full-stack benchmark | Enforced typed JSON packets (`-91.9%` coordination tokens) and scaled to `full_stack_hae` (`4` core modules, `50` held-out tests, uncached synthesis). Reached `46–50/50` (`96.8%` mean) in Gen 15 (`2/10` at `50/50`), but exposed flat-line plateaus when linear LLM self-repair got stuck on high-prior wrong fixes. *(Gen 12 execution scores retracted due to a gateway cache seed.)* | [`experiments/v5_typesafe_xgrammar/`](experiments/v5_typesafe_xgrammar/README.md) |
| **`V6`** | `Gen 16` *(Active)* | Epistemic PUCT search & non-LLM Evidence Gatekeeper | Separates System 1 proposals from System 2 sandbox verification (`EpistemicState` ledger, `low_prior_quota` for underdog hypotheses, Brier prior calibration, and question frontier triage). Validated in the Gen 16 pilot & multi-seed cohorts ([`pilot`](results/hae_gen16_v6_pilot/README.md), [`cohort`](results/hae_gen16_v6_cohort/README.md)). | [`experiments/v6_epistemic_search/`](experiments/v6_epistemic_search/README.md) |
| **`V7`** | *Planned* | Offline RL (`GRPO`/`DPO`) on certified trajectories | Train role-specific adapters (`Questioner`, `Hypothesizer`, `Experimenter`, `Synthesizer`) and a learned value head on verified `V6` search trees rather than post-hoc prose transcripts. | [`experiments/v6_epistemic_search/#5`](experiments/v6_epistemic_search/README.md) |

---

## Repository Layout

```text
hierarchical-agent-evolution/
├── hae/
│   ├── genome/          # CompanyGenome schema, breeding operators, and morphogenesis
│   ├── runtime/         # HierarchicalCompanyRunner (V5 typed execution & V6 epistemic search)
│   ├── epistemic/       # V6 engine: ledger, moves, sandbox gatekeeper, PUCT search, value & audit
│   ├── evaluation/      # Deterministic verification harness and composite fitness scoring
│   └── orchestration/   # GKE worker dispatch and generation breeder
├── configs/             # Generation specs, population files, and benchmark task definitions
├── experiments/         # Per-phase design notes, post-mortems, and errata (V1 -> V6)
├── results/             # Harvested scorecards, epistemic search trees, and summaries
├── k8s/                 # GKE serving and worker job manifests
└── tests/               # Unit and regression test suite
```

---

## Running Tests

```bash
python3 -m unittest discover -s tests -q
```
