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
  `Breeder._evolve_organisation`, `GenerationSpec.ceo_policy` / `.role_library_seed`; later `distill_role_evidence`,
  `select_roles_for_revision`, `mutate_role_text` + `hae/genome/role_mutation.py` and
  `GenerationSpec.role_text_mutation` (section below).
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

## Between-generation role text mutation (`hae/genome/role_mutation.py`, roadmap 1.4 item 3)

The one-tag mutation above never rewrote a role's `goal`/`backstory`; between generations a persona could only be
born mid-run (`MOVE_RECRUIT_SPECIALIST`, `recruit_mode=synthesize`). `mutate_role_text` closes that gap behind a
spec field, **`GenerationSpec.role_text_mutation`** = `tags` (default: exactly the one-tag mutation, byte-identical
children for every existing spec) | `llm` | `off`.

* **Selection** (`select_roles_for_revision`): a role is a candidate when, after this generation's statistics are
  folded in, `uses >= 3` and (`mean_delta_u <= 0.05` or `support_rate <= 0.10`); ordered by (mean dU, support
  rate, −uses, id), one role per mutant child. Never a role with `tasks_resolved > 0` (lifetime, or this
  generation per `RoleStatsSummary`). With no candidate the operator still fires on a uniformly chosen role with
  probability 0.25, so a healthy library keeps exploring persona space.
* **Evidence in the prompt** (`Breeder.role_evidence` = `distill_role_evidence(records)`, next to `role_stats`): the
  role as it is and its numbers; the claims of its FALSIFIED hypotheses (≤ 8, 200 chars each), the reasons its probes
  were refused (≤ 5), up to 3 SUPPORTED claims (so the rewrite keeps what worked), the questions it was routed to
  (≤ 5), the modules it met and the modules no active role covered, and how many of its syntheses were reverted. Read
  from `run_output.epistemic_ledger` / `epistemic_searches` / `org_history`; a V6 record has no `role_id` anywhere
  and yields nothing (tested on a real Gen-16 tree). The instruction: rewrite goal/backstory so the specialist would
  NOT have proposed the falsified mechanisms and WOULD cover the unmatched modules; keep the name unless the expertise
  changes. Reply grammar: `ROLE_REVISION_SCHEMA` (name ≤ 60, goal ≤ 400, backstory ≤ 600, 3–8 tags, kind enum,
  rationale ≤ 300); parser tolerant of fences/prose/string tags, an unknown kind keeps the parent's, no goal → no
  revision.
* **The revision is a new allele that replaces its parent in the child's library:** `role_id = <parent>__g<gen>`
  (plus a 6-char hash of the new goal on collision), `origin = mutated:<parent>:g<gen>`, `created_generation = gen`,
  `uses`/`mean_delta_u`/`support_rate`/`tasks_resolved` reset to 0, `extra` = parent's + `parent_role_id`,
  `revision_rationale`, `revision_source: "llm"`. The child's `mutation_history` gets one line per revision:
  `Generation N: role <id> revised by LLM -> <new_id>: goal '<old 60>' -> '<new 60>' (<rationale 80>)`.
* **Fallback:** an LLM error, an unparseable reply or a reply that leaves goal and backstory unchanged never raises;
  the role is kept, the child carries the tag mutation only, and the role-library history line says
  `role <id> kept, LLM revision declined (<reason>)`.
* **Record, not seed:** the reply is not reproducible (temperature 0.7; `call_llm` has no seed parameter, one is
  passed only to a callable that declares it), so for `llm` mode the population JSON is the record of what was bred.
  The breed CLI takes no override: the mode is declared in the generation spec like every other cohort decision.
* **Caveat:** unit-tested with a fake LLM only (`tests/test_role_mutation.py`); no revision has been produced by a live
  model, and whether rewritten personas earn more dU than their parents is an open empirical question.

## Caveats

* Credit is **end-of-move** credit (the role holding the move when the gatekeeper ruled), not causal credit.
* Seeds are hand-written from the Gen-16 failure vocabulary; no role carries evidence until the smoke cohort runs.
* Turn-0 selection enumerates subsets (bounded by `max_initial_roles <= 8`, library `<= 24`): fine here, not beyond.
* Nothing has been validated live; `configs/generation_v8_smoke_population.json` is the first step.
* A revised allele replaces its parent only in the child that bred it; a crossover with a lineage that still carries
  the parent re-unites both alleles in one library (capped at 24 like everything else).
