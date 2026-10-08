# V9 — Adaptive Branching MCTS in the epistemic loop

Status: implemented and unit-tested on `main` (2026-10-08); no live
comparison against V8 yet. Paper: Inoue, Misaki, Imajuku, Kuroki, Nakamura,
Akiba, "Wider or Deeper? Scaling LLM Inference-Time Compute with Adaptive
Branching Tree Search", [arXiv:2503.04412](https://arxiv.org/abs/2503.04412).
Specification: `docs/v8_role_evolution_and_swebench_roadmap.md`, section
"Agent Implementation Specification for V9". Code: `hae/epistemic/ab_mcts.py`
(the controller), `hae/epistemic/mcts.py` (`_ab_step`, the gate),
`hae/genome/schema.py` (the gene fields), `hae/runtime/company.py` (the k=1
proposer prompt). Tests: `tests/test_ab_mcts.py` (29 tests).

## 1. What changes and what does not

The epistemic loop (`EpistemicSearchLoop.run`) still picks a frontier question
and spends one move on it per step. What V9 changes is how the move is chosen
when the gene says so:

| | V8 (`search_algorithm = "puct"`, default) | V9 (`"ab_mcts"`) |
|---|---|---|
| Next move at a question | Waterfall in `_choose_action`: synthesize if a hypothesis is SUPPORTED and `min_hypotheses_before_synthesis` tested, else experiment (PUCT over untested, Move-37 quota), else propose | Thompson sampling over the arm set below |
| Hypotheses per PROPOSE | `branching_k` (2-6) blind, mutually exclusive | 1, conditioned on every earlier probe's output on the question |
| How many proposal rounds | `max_hypothesis_rounds` per search | No cap; a question's GEN arms are exhausted after 2 consecutive draws that added nothing |
| Recruit | `_maybe_recruit` before the waterfall: stall or uncovered module, then a coin flip with `recruit_prior()` | A CEO arm in the same draw as everything else |
| Stop criteria | budget, token budget, stagnation, resolved, exhausted, deferred | unchanged |

Under the default the loop is the V8 loop. `tests/golden/puct_v8_trajectories.json`
holds three seeded searches (no organisation; organisation with a
stall-triggered recruit; two proposal rounds) recorded from the code before
the controller existed, and `PuctIdentityTests` compares every move, every
stats key and every hypothesis against it. The controller object is not even
constructed under `puct`, and no stats key or `move.extra` field is added.

## 2. The arms

At a question `q` the controller builds `A(q)` in a fixed order (ties in the
draw fall to the earlier arm):

| Arm | Present when | Prior mean `p` | Evidence `y` |
|---|---|---|---|
| `("experiment", h)` | `h` is UNVERIFIED | `h.prior`, the proposer's stated probability | reward of every pull on `h` |
| `("synthesize", h)` | `h` SUPPORTED, not patched, `< MAX_SYNTHESIS_FAILURES` failures | `h.posterior`, re-read every step | reward of every synthesis attempt on `h` |
| `("gen_hypothesis", r)` | one per active role that can probe; one unnamed arm when there is no organisation or no role can probe | `r.support_rate` when `r.uses > 0`, else the CEO gene's `optimistic_prior` (0.5 for the unnamed arm) | reward of every tested hypothesis `r` proposed on `q`; 0 for a draw that added nothing |
| `("gen_specialist", "ceo")` | recruit callback present, `OrgState.can_recruit`, previous move not a recruit | `OrgState.recruit_prior()` (the sigmoid over stall, uncovered modules, headcount), re-read every step | 0 per declined recruit; reward of every tested hypothesis proposed by a role hired during this search |

Every arm is `Beta(1 + tau*p + wins, 1 + tau*(1 - p) + losses)` with
`tau = EpistemicPolicyGene.ab_prior_strength` (default 4.0, bounds 0.5-32,
evolvable). Selection draws `theta ~ Beta(alpha, beta)` for each arm with the
loop's seeded `random.Random` (`betavariate`) and takes the largest.

Rewards are in [0, 1]:

```
tested hypothesis   y = clamp(dU + 0.5 * [verdict is SUPPORTED], 0, 1)
synthesis           y = clamp(dU + 0.5 * [module check passed],   0, 1)
```

`dU` is the move's `delta_u` from the ledger, the uncertainty the move
actually resolved. An early falsification carries dU and is rewarded; a late
one that resolves nothing scores 0, and that is what makes a role's GEN arm
sink until the CEO arm or another role's wins. A GEN draw itself is free
(proposals earn nothing until tested) unless it added nothing, which scores 0
and counts towards exhaustion.

This is AB-MCTS-A ("node aggregation") from the paper: the GEN node has its own
posterior aggregated from the rewards of the children it produced. There are
two GEN nodes per question instead of one -- an epistemic GEN per role and an
organisational GEN for the CEO -- and the CEO's children are the hypotheses of
the roles it hired.

## 3. How a step runs (`EpistemicSearchLoop._ab_step`)

1. `idx = len(move_log) + 1`; `arms = controller.candidate_arms(q, state, org, recruit_eligible)`.
2. If the set is empty and a recruit is possible except for the cooldown, the
   set is rebuilt with the CEO arm (section 5).
3. `chosen, draws = controller.select(arms)`.
4. The chosen arm runs the loop's existing move: `_do_experiment(q, h, False)`,
   `_do_synthesize(q, h)`, `_do_propose(q, role=<the arm's role>, k=1)` or
   `_do_recruit(q, ...)`. The ledger writes, role credit (`OrgState.credit`),
   `moves_by_role` / `delta_u_by_role` and the move record are exactly what
   the PUCT path writes for the same move.
5. The move's `delta_u` becomes the reward (`observe_experiment`,
   `observe_synthesis`, `observe_generation`, `observe_recruit`).
6. `move.extra` gains `ab_theta` (the winning draw), `ab_mean` (the arm's
   posterior mean after the update), `ab_arms` (how many arms competed) and
   `ab_y` (the reward); the controller appends a trace entry.

At the end of `run()` the result's `stats` carries
`search_algorithm: "ab_mcts"` and `ab_mcts: {algorithm, prior_strength,
counts, gen_empty, arms, trace}` -- per question, every arm's prior, alpha,
beta, mean, pulls, observations and reward history, plus the first 400 steps'
draws. Nothing else in the stats changes, so the V6/V8 summarisers keep
working on V9 trees.

## 4. The k=1 proposer

`HierarchicalCompanyRunner._propose_hypotheses_adapter` used to force
`k = max(2, k)`. With exactly `k == 1` it now asks for "1 NEW FALSIFIABLE
HYPOTHESIS" whose mechanism differs from every listed and RULED OUT one, and
adds a `PROBE OUTCOMES ON THIS QUESTION` block to the context: for each probe
that ran on the question, the hypothesis id and verdict, the claim, what the
probe predicted and what it actually printed (exit code and the last 160
characters of stdout), or the gatekeeper's refusal. One exception to "NEW" is
spelled out: a hypothesis whose probe was refused may be restated with the
same claim and mechanism and a corrected probe, which the ledger records as a
repair of the original (the V8 k=3 prompt reaches the same repair path
without being told; the first live V9 run showed that the stricter k=1 rule
had closed it). The `k >= 2` prompt and context are unchanged byte for byte
(`branching_k` is bounded to [2, 6], so the PUCT path never reaches the single
mode). The schema (`V6_HYPOTHESIS_SCHEMA`) and `max_tokens` are the same;
`parse_hypothesis_packet` keeps one item.

## 5. Decisions the specification left open

These are choices, listed so they can be objected to:

- **Reward clamp.** `dU` can exceed 0.5 on a first falsification (0.95 for a
  SUPPORTED probe that resolves a whole question), so the reward is clamped to
  [0, 1] rather than only at the top as the spec's `min(1.0, ...)` reads.
- **CEO arm prior.** The spec says "boosted by uncovered module count". The
  arm's prior is the live `OrgState.recruit_prior()`, which already is the CEO
  gene's sigmoid over stall, uncovered modules and headcount. The six recruit
  knobs therefore stay evolvable and keep their meaning, but there is no hard
  stall/unmatched trigger and no coin flip: pressure moves the prior, the draw
  decides.
- **Cooldown as last resort.** The arm honours `recruit_cooldown_moves`,
  `max_active_roles` and "never two recruits in a row". One exception: when a
  question has no arm left at all (nothing to deepen, GEN arms exhausted) and
  only the cooldown stands in the way, the CEO arm is offered anyway. Without
  this the search ended "exhausted" with budget and a hire still available
  (seen in the decline scenario under seeded sampling). The no-two-in-a-row
  rule still terminates a CEO that keeps declining.
- **GEN exhaustion replaces the round cap.** After `GEN_EMPTY_LIMIT = 2`
  consecutive GEN draws on a question that added or repaired nothing (all
  proposals tabu or duplicate, or a proposer error), the question has no GEN
  arms until a recruit resets it. Budget, token and stagnation stops are
  unchanged; `max_stagnant_moves` is the knob that bounds a long run of
  falsifications that resolve nothing.
- **Per-question GEN arms.** A role's GEN posterior is kept per question, not
  per search: a specialist who is wrong about one question is not penalised on
  another. Its prior (support_rate) carries the cross-run record.
- **Synthesiser routing unchanged.** The synthesize arm names the hypothesis;
  which role writes the patch is still `OrgState.pick_role("synthesis")`, as
  in V8. The experiment arm needs no role (the gatekeeper runs it; credit goes
  to the proposer as before).
- **Unused PUCT knobs.** `branching_k`, `max_hypothesis_rounds`,
  `min_hypotheses_before_synthesis`, `low_prior_quota` and `c_puct` are not
  read under `ab_mcts`. They stay on the gene (the operators still mutate
  them) so a genome can be switched between algorithms without a schema
  change; `ab_prior_strength` is likewise carried, inert, under `puct`.
- **Serialisation.** `to_dict()` omits `search_algorithm` under `puct` and
  `ab_prior_strength` when it is at its default under `puct`, so the archived
  Gen 16 and V8 population files round-trip unchanged
  (`tests/test_v8_genome.py`). `search_algorithm` is a cohort-level control
  like `enabled`: mutation never flips it, crossover inherits parent a's.

## 6. What the tests pin down

`tests/test_ab_mcts.py`, all with the real gatekeeper (probes in a
subprocess), ledger and loop, and a fake System 1. The behavioural tests swap
the controller's rng for one whose Beta "draw" is the posterior mean, so the
move sequence follows from the rewards alone; a few also run the real seeded
sampler and assert loosely.

1. **Identity.** Three seeded PUCT searches equal the golden fixture; a PUCT
   loop has no controller, no `ab_*` fields and no V9 stats keys.
2. **Width 1.** One hypothesis with prior 0.9: `propose(k=1)`, `run_experiment`,
   `synthesize`, `all_resolved` in 3 moves, with `min_hypotheses_before_synthesis = 3`
   ignored. Under the seeded sampler, at most 5 moves over 6 seeds. A refused
   probe restated with a corrected probe is a repair, not a duplicate: 5 moves,
   one hypothesis id, `probe_repairs = 1`.
3. **Widening.** Seven falsified mechanisms then the right one: 8 hypotheses,
   17 moves, `all_resolved`; the GEN arm's mean falls monotonically from 0.5
   to below 0.3 while each new hypothesis is tested as soon as it exists. The
   same proposals under PUCT (`max_hypothesis_rounds = 2`, `branching_k = 3`)
   stop `exhausted` at 6 hypotheses without ever asking for the right one. A
   proposer that returns nothing exhausts the question in 2 moves regardless of
   `max_hypothesis_rounds`.
4. **Specialist decay.** An analyst (support_rate 0.6) whose two probes are
   refused: GEN(analyst) falls 0.567 -> 0.486 -> 0.425, the CEO arm at
   sigmoid(-0.4) has mean 0.433 and wins at move 5; the hire's GEN arm proposes
   next, the hypothesis is SUPPORTED and the patch verified (8 moves). The
   hire's reward is also credited to the CEO arm. Variants: no callback means no
   CEO arm; a declined recruit lowers the CEO arm and the cooldown holds; the
   cooldown is waived when the recruit is the only move left.
5. **Gene.** Validation, serialisation rules, operators.
6. **Proposer.** The k=1 prompt and its probe block; the k=2 prompt unchanged.

## 7. Running it

`configs/genomes/v9_ab_mcts_swebench_firm_0.json` is the V8 smoke genome with
`epistemic_policy.search_algorithm = "ab_mcts"` and `ab_prior_strength = 4.0`;
every other gene is identical, so a run against the same instance is a
like-for-like comparison of the step policy:

```bash
python3 -m hae.cli --mode swebench --instance-id <id> \
    --dataset data/swebench/swebench_dev.jsonl \
    --executor kubectl --pod <pod> --container testbed \
    --genome configs/genomes/v9_ab_mcts_swebench_firm_0.json \
    --out-dir results/swebench/<run>/<id> --budget-moves 30 --max-iterations 2
```

The tree's `stats.ab_mcts.trace` shows, per step, every arm's draw and the one
taken; `stats.ab_mcts.arms` shows where each posterior ended.

## 8. First live run

`results/swebench/v9_ab_mcts_2026-10-08/README.md` reports the first live run
(`marshmallow-code__marshmallow-1810`, self-hosted Qwen3.8, 2026-10-08) next
to a `puct` rerun of the same genome on the same code. In short: AB-MCTS
resolved the instance in 24 moves (10 min, 146k tokens) and stopped on
`all_resolved`; the width-1 path, the widening on the hard question and the
decay-driven recruit all happened as the tests describe. The `puct` rerun
stopped `exhausted` at move 19 and its patch was broken by a later synthesis
that the module gate should have failed. The same write-up records the two
shared defects found (a pytest start-up failure passed the gate as
inconclusive; cosmetic function rewrites pass the gate) and the V9-specific
one (refused probes could not be repaired under the k=1 prompt); the first
and third are fixed. One instance says nothing about rates; the V8-vs-V9
comparison on the 50-task dev slice is the next measurement.
