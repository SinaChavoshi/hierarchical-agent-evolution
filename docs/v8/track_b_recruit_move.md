# V8 Track B: `MOVE_RECRUIT_SPECIALIST`, per-role routing and evidence credit

Status: implemented and unit-tested (`tests/test_recruit_move.py`); **not yet
exercised on a live run**. Every number below describes mechanism, not
observed behaviour. Wiring the `OrgState` into `run_epistemic_search` belongs
to Track A; until then no firm takes these code paths.

## What the loop does with an organisation

`EpistemicSearchLoop(..., org_state=OrgState, recruit_specialist=callback)`
adds three things to the V6 loop (`hae/epistemic/mcts.py`, module docstring
"The organisation as search state"):

* **Routing.** Before a PROPOSE move `org_state.pick_role("probe")` names the
  specialist (UCB over the active roles able to probe: mean credited dU +
  exploration bonus); the adapter is called with `role=`. SYNTHESIZE does
  the same with `pick_role("synthesis")`. The hypothesis carries the proposer's
  `role_id` and `proposed_by = role.name`; the move record's `agent_role` is
  the role's name and `role_id` its id. When no active role can take the move
  (`pick_role` is None) the adapter is called exactly as in V6, without the
  keyword, and the record shows the static binding.
* **Credit.** After RUN_EXPERIMENT the hypothesis' *proposer* is credited
  (`OrgState.credit`) with the move's dU and the verdict; the experiment record
  carries the proposer's `role_id` while `agent_role` stays the gatekeeper's
  label. After SYNTHESIZE the synthesising role is credited with the dU and
  `wrote = module check passed` -- a write that broke the module (reverted or
  not) is a stall for the role, not a patch; an adapter error or an unwritten
  synthesis is credited as dU 0, not written. PROPOSE earns nothing:
  proposals are free until tested. This is **end-of-move credit, not causal
  credit** (org.py "Honesty notes"): a role proposing after another role
  narrowed the question inherits the easier position.
* **The recruit move.** `_maybe_recruit` runs once per step, before the usual
  move. It takes the move when *all* of: a callback exists; the previous move
  was not itself a recruit; `OrgState.can_recruit(move_index)` (cooldown and
  headcount); pressure -- `stall_counter >= policy.stall_moves` or some
  surfaced module no active role's tags cover; and `rng.random() <
  org_state.recruit_prior()`. The prior is the *probability of taking the move
  on such a step* (the CEO policy gene tunes eagerness), not a PUCT score.
  The callback `(org, state, question, move_index) -> (RoleAllele | None,
  note)` decides *whom*: a role is appended with `OrgState.recruit` (optimistic
  routing prior, stall counter reset, covered modules pruned); `None` -- or a
  role whose id is already active -- is a *declined* recruit. Both are
  recorded as a `recruit_specialist` move with dU 0, count against the move
  budget, and start the cooldown (`last_recruit_move`), so a CEO that keeps
  declining is not asked every step.

`move_index` everywhere is the ledger's 1-based cumulative index
(`len(state.move_log) + 1`, what `MoveRecord.move_index` records), so
`recruited_at_move`, `recruit_log[*].move_index` and the move rows line up.

Modules are observed on frontier admission (`question_modules`: the question's
module, the traceback location's path part, every `*.py` path in the oracle
line or question text) through `OrgState.observe_modules`.

## The CEO adapter (`HierarchicalCompanyRunner._recruit_specialist_adapter`)

`org.policy.recruit_mode` picks the source. `library`: the first candidate in
`OrgState.library_candidates` order whose tags overlap the gaps (task tokens +
path parts of the uncovered modules) -- no LLM call; returned as a copy with
`extra["source"] = "library"`. `synthesize`: the CEO (executive tier) answers a
grammar-constrained `RECRUIT_SPECIALIST` packet (`V8_RECRUIT_SCHEMA`: name,
goal, backstory, 3-8 domain tags, kind, rationale; `parse_recruit_packet` is
tolerant, only `name` is required) which becomes a worker-tier `RoleAllele`
with origin `recruited:<company>:g<gen>:m<move>` and `extra["source"] =
"synthesized"`. `both`: library first, CEO when nothing overlaps. A duplicate
name, an unparseable reply, a refused budget (`_may_call`) or any exception is
a declined recruit with a note; the move can never stop the search.

The System 1 adapters accept `role=`: the routed specialist executes the call
(`RoleAllele.to_agent_genome()` puts its persona in the system prompt; the
PROPOSE prompt also names it). Without `role` the prompts are byte-identical
to V6.

## Guarantees for V5/V6 genomes

With `org_state=None` the None branch runs the pre-V8 statements verbatim: no
rng draw, no `role=` keyword, the same `proposed_by`, `agent_role` and stats
key set. `tests/test_recruit_move.py::NoneBranchIdentityTests` compares the
full move logs of the same seeded search with and without the V8 keywords.
The only schema change visible to old code paths is the new `role_id` field
(`""`) on `Hypothesis` and `MoveRecord`; `from_dict` tolerates its absence.

## Telemetry

* `SearchResult.to_dict()["org"]` (= `OrgState.to_dict()`) only when an
  organisation ran; `stats` gain `recruits`, `recruits_from_library`,
  `recruits_synthesized`, `recruit_declined`, `moves_by_role`,
  `delta_u_by_role`, `roles_routed` (True once at least one PROPOSE/SYNTHESIZE
  was actually routed) only then.
* `scripts/extract_value_telemetry.py`: a trailing `role_id` column and a
  `moves_by_role` summary, present only when some move carries a non-empty
  role id; `recruit_specialist` moves are ordinary rows (`delta_u` 0).
* `scripts/summarize_gen16_cohort.py`: pools the recruit counters, adds a
  per-run `org` entry and a "V8 organisation" table only when a tree's
  searches carry `org`. Re-running both scripts on
  `results/hae_gen16_v6_cohort_r2` reproduces the committed CSV/JSON byte for
  byte (checked); `summary_tables.md` there already predates commit 434ecd9's
  mechanics columns -- pre-existing drift, unrelated to this track.

## Caveats

* Recruit moves have dU 0, so they count toward `max_stagnant_moves` like a
  proposal does; a declined recruit under a stalled team brings the stagnation
  stop closer.
* With `recruit_cooldown_moves = 0` the loop still never takes two recruit
  moves in a row, but a CEO that keeps declining can be asked every other move
  while the pressure lasts; that is the policy gene's cost to bear.
* Routing to the recruit "on the very next move" holds when the incumbents'
  mean credited dU is below the CEO's optimistic prior (a genuine stall:
  refused probes, falsifications that freed little mass). A productive
  incumbent keeps the move; the test fixture uses refused probes for that
  reason.
* Experiments on hypotheses without a `role_id` (proposed under static
  bindings, or by an earlier iteration without an organisation) credit nobody
  and do not advance the stall counter.
