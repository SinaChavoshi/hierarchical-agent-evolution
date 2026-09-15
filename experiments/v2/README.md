# V2 Experiment Set

**Status: not started.** Awaiting expert review.

V2 begins after the platform rebuild described in the root
[`README.md`](../../README.md) is reviewed and accepted. No V2 generation has
been run, and no results are published here.

## Entry criteria

Refactor (complete):

- [x] `src/` refactored into the `hae/` package; all backwards-compatibility
      shims removed
- [x] Self-hosting benchmark implemented and validated against a known-good
      and a known-bad submission
- [x] Unified generation runner replaces the one-off breeding scripts
- [x] Single templated Kubernetes Job replaces the per-generation manifests
- [x] Reachability guard test passing — no unreachable capability modules

Platform work added after the V1 retrospective (complete):

- [x] **Task abstraction** — objective, verifier, budget and capabilities are
      declared together in `configs/tasks/*.json` instead of being hardcoded in
      four unrelated places
- [x] **Verifier is an interface** — `execution-gates`, `benchmark`, `none`,
      composite. Adding a family no longer means editing the runner
- [x] **Budgets enforced** — calls refused at the ceiling, with a reserve so an
      overrun truncates the deliverable rather than deleting it
- [x] **Artifact carryover available** — opt-in, with `inherited_files` and
      `authored_files` recorded separately
- [x] **Unattended campaign loop** with stopping criteria and a completeness
      gate
- [x] **Preflight with IAM repair** — run before every generation, because
      Latchkey reaps the binding on its own schedule
- [x] **Agent commands run with no network** — each `execute_bash` gets its own
      empty network namespace. Env-var scrubbing alone did *not* hold: measured
      in-cluster, a firm's `curl` to `169.254.169.254` returned a live Workload
      Identity token. Tournament pods set `HAE_REQUIRE_NETWORK_ISOLATION=1` and
      fail closed

Remaining:

- [ ] **Expert code review complete** ← the only open item

## What V2 changes about how results are read

| V1 | V2 |
|---|---|
| Objective hardcoded in the launcher | Declared in a task file with its verifier |
| Five Python gates, always | Whichever verifier the task declares |
| Judge-only scores published as measurements | `evaluable=False` when there is no ground truth, said loudly |
| `budget_usd` adjusted the score afterwards | Ceiling enforced; refusals recorded |
| Every generation restarts from empty | Carryover available, opt-in, provenance tracked |
| Human drives five steps per generation | `--mode campaign` runs to a stopping criterion |
| A partly-failed generation gets harvested anyway | Completeness gate refuses a biased survivor set |

> [!CAUTION]
> **The completeness gate exists because of Generation 11.** Three of ten firms
> crashed at genome load, and they were every structurally novel topology in the
> population — both mutants and the pareto bonus. The survivors were elites and
> crossovers. Harvesting them would have reported the conservative half of the
> population as the whole generation and bred from a gene pool with its
> exploration silently removed.
>
> See [`../v1/exp-013-parallel-gen11/`](../v1/exp-013-parallel-gen11/).

## Planned first generations

| Spec | Task | Question it answers |
|---|---|---|
| [`gen1.json`](../../configs/generations/gen1.json) | [`self-hosting-artifacts`](../../configs/tasks/self-hosting-artifacts.json) | Baseline under a real held-out suite, single-shot, comparable to the V1 record |
| _(planned)_ `gen2.json` | [`self-hosting-artifacts-carryover`](../../configs/tasks/self-hosting-artifacts-carryover.json) | Does iterating on the deliverable beat restarting it? The A/B the V1 design could not ask |

## Ledger

Populated by the campaign controller (`--ledger experiments/v2/ledger.json`).

| Experiment | Generation | Task | Best | Mean | Spend | Status |
|---|---|---|---|---|---|---|
| _(none yet)_ | | | | | | |
