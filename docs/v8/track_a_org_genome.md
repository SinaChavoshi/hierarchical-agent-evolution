# V8 Track A: role library + CEO policy genes, turn-0 organisation, cross-generation promotion/pruning

**Status:** implemented and unit-tested on branch `v8a-org-genome`; nothing has run on the cluster yet.
Roadmap sections 1.1 (genome), 1.2 (turn-0 selection), 1.4 (distillation).

## What was built

* **Genes** (`hae/genome/schema.py`): `RoleAllele`, `CEOPolicyGene`, `CEO_POLICY_BOUNDS`, `MAX_ROLE_LIBRARY = 24`;
  `CompanyGenome.role_library` / `.ceo_policy` / `.org_enabled`. `to_dict()` omits both genes while they carry
  no information, so every pre-V8 file is unchanged (all 26 genomes in `configs/generation_16_r3_population.json`
  round-trip byte-identically — tested). `hae/epistemic/org.py` re-exports the types unchanged; they were moved
  there rather than imported because `tests/test_architecture.py` fails on any import cycle.
* **Seeds** (`hae/genome/role_seeds.py`): `seed_role_library("legacy" | "swebench")` — 8 / 10 hand-written roles,
  ids `r_<slug>_<flavour>` — and `role_library_from_departments(genome)` (one role per technical department).
* **Runtime** (`hae/runtime/company.py`): `_bind_dynamic_organization`, `_dynamic_org_bindings`, wired into
  `run_epistemic_search`.
* **Operators** (`hae/genome/mutator.py`) and **breeder** (`hae/orchestration/breeder.py`): `distill_role_statistics`,
  `distill_tag_pool`, `evolve_role_library` (update → prune → promote → cap), `crossover_*`, `mutate_*`,
  `Breeder._evolve_organisation`, `GenerationSpec.ceo_policy` / `.role_library_seed`.
* **Smoke cohort**: `scripts/make_v8_population.py` → `configs/generation_v8_smoke_population.json` — 4 run-3
  lineages, CEO gene on (`headcount_lambda` 0.05/0.15/0.30/0.50), legacy seeds, run-3 `epistemic_policy` untouched.

## Genome shape

```json
"role_library": [{"role_id": "r_traceback_localiser_legacy", "name": "Traceback Localiser", "goal": "...",
                  "backstory": "...", "domain_tags": ["traceback", "nameerror", "harness"],
                  "kind": "probe | synthesis | both", "model_tier": "worker", "temperature": 0.3,
                  "tools_enabled": false, "cost_per_move": 1.0, "uses": 0, "mean_delta_u": 0.0,
                  "support_rate": 0.0, "tasks_resolved": 0, "created_generation": 0,
                  "origin": "seed:legacy | department:<dept_id> | recruited:<source>"}],
"ceo_policy": {"enabled": true, "min_initial_roles": 2, "max_initial_roles": 4, "max_active_roles": 6,
               "headcount_lambda": 0.15, "exploration_c": 0.5, "optimistic_prior": 0.3, "stall_delta_u": 0.05,
               "stall_moves": 4, "recruit_w_stall": 0.5, "recruit_w_unmatched": 0.8, "recruit_w_headcount": 0.4,
               "recruit_bias": -2.0, "recruit_cooldown_moves": 3, "recruit_mode": "library | synthesize | both",
               "temperature": 0.0}
```

## Runtime (each epistemic iteration, only when `ceo_policy.enabled`)

`TaskFeatures.from_failures` → `search_initial_organization` (argmax of Σ Q(r|task) − λ_eff·|O| over 2..4-role
subsets with ≥1 probe- and ≥1 synthesis-capable role; rng seeded from `company_id:iteration`) → `hypothesis` /
`experiment` bound to the team's best probe role, `synthesis` to its best synthesis role, `question` unchanged.
A genome with no library is staffed from the `legacy` seeds. The loop receives `org_state=` and
`recruit_specialist=self._recruit_specialist_adapter(...)` (Track B). The run output gains `org`, `org_audit`,
`org_history` (one `{iteration, org, audit}` per iteration); roles recruited in iteration *k* are candidates at
iteration *k+1*. **Disabled:** loop call, bindings and output are byte-identical to V6 (tested).

## Generation boundary — rules and thresholds (`hae/genome/mutator.py`)

Statistics come from **every breedable firm**, not only survivors. Every child: update → prune → promote → cap;
crossovers also take the library union and a uniform CEO-gene crossover; mutants also get CEO-gene jitter and a
one-tag text mutation. All rng seeds derive from the spec (`gen{N}-{kind}-{slot}-org`).

* **Update:** `mean_delta_u`, `support_rate` are running means weighted by `uses`; `tasks_resolved` accumulates.
* **Promote** a role *recruited* mid-run (recruit log / `recruited_at_move >= 0` / origin `recruited:*`) that earned
  `supported >= 1` **or** cumulative ΔU `>= 0.20` **or** a resolved task; `created_generation` = generation bred.
* **Prune** a role with `>= 6` uses this generation at mean ΔU `<= 0.0` and no resolved task — never below
  `max_initial_roles` roles, never the last probe/synthesis-capable role, never one with `tasks_resolved > 0`.
* **Cap** at 24 by (tasks_resolved, quality, id), keeping probe + synthesis capability.
* **Crossover:** union by `role_id`, the allele with more `uses` wins (tie: rng); `enabled` inherited from parent a.
* **Mutation:** 1–3 numeric CEO genes jittered inside `CEO_POLICY_BOUNDS` (`enabled` never flips); one role gets a
  tag from the generation's unmatched-module / task-tag pool (the library's own tags when the pool is empty).
* `GenerationSpec.ceo_policy` overrides apply after inheritance; a child switched on with no library is seeded from
  `role_library_seed` (default `legacy`) and says so in `mutation_history`. Children with no library and a disabled
  gene are untouched, so V5/V6 lineages breed exactly as before (tested).

## Caveats

* Credit is **end-of-move** credit (the role holding the move when the gatekeeper ruled), not causal credit.
* Seeds are hand-written from the Gen-16 failure vocabulary; no role carries evidence until the smoke cohort runs.
* Turn-0 selection enumerates subsets (bounded by `max_initial_roles <= 8`, library `<= 24`): fine here, not beyond.
* Nothing has been validated live; `configs/generation_v8_smoke_population.json` is the first step.
