# V14 Retrospective: What We Learned and What the Evidence Does Not Show

*Written Oct 27, 2026, when V14 was closed and V15 (harness evolution) opened.*

V14 produced a strong **hand-designed** hierarchical harness. This note records
what the V14 experiments support, where the claims are weaker than the
headlines, and why V15 returns to the project's original thesis: *evolving*
agent organizations under execution-grounded fitness.

---

## 1. What the evidence supports

| Finding | Evidence |
| :--- | :--- |
| **The hierarchy is never worse than flat ReAct on the same model (unseen tasks).** | `0` of `28` unseen Verified tasks where `v12_agentic` resolved and V14 did not; `21/28` vs. `19/28`. *Weakened: see the second caution in §2 (test-task hints).* |
| **Bounded milestones beat one long attempt on a greenfield build.** | Same inner engine on `MiniSQL`: one `+1,651`-line diff scored `8/15` on the held-out `sqlite3` oracle; the milestone ratchet (max `+618` lines per milestone) reached `15/15` (at about `5×` the tokens). |
| **External oracles catch what self-written tests miss.** | The ratchet's internal suites were green, yet the held-out oracle scored `9/15` after the planned DAG. Recovery milestones then fixed those failures. |
| **Prompt-level hints can hide in "generic" code.** | See the caution in §2: a repo-name grep is not a sufficient leakage audit. |

## 2. Where the claims are weaker than the headlines

> [!CAUTION]
> **The 6-task dev slice results are contaminated by gold-fix injectors.**
> An audit during the V15 kickoff found four "Static AST" detectors in
> [`hae/swebench/hierarchical_mcts.py`](../../hae/swebench/hierarchical_mcts.py)
> (`unreferenced_init_attrs`, `deprecated_replacements`,
> `runtime_error_translations`, `range_setter_targets`). They are triggered
> by generic-looking text patterns, but the instructions they inject spell
> out the gold fix for **4 of the 6 dev-slice tasks**:
>
> | Detector | Injected instruction | Dev task it solves |
> | :--- | :--- | :--- |
> | `unreferenced_init_attrs` | pass `self.<attr>` as 6th arg to `DataElement.from_json(...)` in `get_sequence_item`; replace `-> NoReturn` with `-> None` | `pydicom-1255` |
> | `deprecated_replacements` | `DeprecationWarning` in `<old>.__init__` under `sys.version_info >= (3, 8)`; replace decorators with `@cached_property` | `astroid-1417` |
> | `runtime_error_translations` | replace `raise RuntimeError` with `raise <X>ParseError(f"Could not parse: {current_str}", segment=unmatched_segments[0])` in `check_still_complete` | `sqlfluff-1517` |
> | `range_setter_targets` | define `_check_range(value, rng, parm_name)` with the exact error string; call it in 8 named setters (`opacity` … `anisotropy`); don't touch `themes.py` | `pyvista-4226` |
>
> They sit inside `L1-A` recon and the `L1-B` static checks, which is why the
> V14 "zero repo names" grep did not catch them, and why removing recon
> (`v14_no_recon`) cut the dev slice from `6/6` to `2/6`. **Consequences:**
> the dev-slice `6/6`, the 5-way ablation staircase and the `6 : 0` dev-slice
> wins over V12 cannot be used as evidence. No trace of these injections was
> found in the 28 unseen Verified runs, so the unseen comparison
> (`21/28` vs. `19/28`) looked like the only clean SWE-bench result from V14.
> V15 removes all four detectors before evolution starts.

> [!CAUTION]
> **The unseen Verified comparison is also contaminated.** A second audit
> (token overlap between the harness prompts and the gold/hidden-test patches)
> found prompt "examples" that describe specific *test* tasks, added while the
> unseen evaluation was in progress:
>
> | Commit | Prompt text added | Unseen task it targets |
> | :--- | :--- | :--- |
> | `95db203` (during the n=12 run) | "a short CLI option behaving like its long option including `--help` output" | `pylint-dev__pylint-6386` (one of V14's two unseen wins) |
> | `95db203` | "an inferred/explicit scale behaving like a categorical scale", "an operation preserving state" | `mwaskom__seaborn-3069`, `matplotlib__matplotlib-26208` |
> | `03417d7` (during the n=28 run) | the hidden-test inputs `(foo{1,3})`, `foo, bar{1,3}`, `(foo{1,}, foo{1,3}})` and comma-splitting rules for regex lists | `pylint-dev__pylint-8898` (V14 first **failed**, then passed on a rerun after this text was added) |
> | `03417d7` | centered-window offsets `(window - 1) // 2` with odd/even window tests | `pydata__xarray-6744` |
> | `03417d7` | `GenericAliasMixin` vs `NewTypeMixin` vs `TypeVarMixin`, `get_doc()` returning `[[]]` | `sphinx-doc__sphinx-9229` |
>
> The shared L2 prompt in `agentic.py` (used by V12 *and* V14) also carried
> dev-task examples (`cachedproperty -> cached_property`,
> `{**(metadata or {}), **additional_metadata}`, `List` vs `Tuple`/`Dict`).
> **Consequence:** none of V14's SWE-bench numbers are clean. All of these
> texts were removed from the V15 seed, and V15 re-runs the cleaned seed on
> the 28 test tasks as its baseline arm. The audit script logic now lives in
> the leakage gate ([`hae/evolution/leakage.py`](../../hae/evolution/leakage.py)).

1. **Unseen-only significance.** The `p = 0.0078` sign test pools `34` tasks
   (`8 : 0` discordant). `6` of the `8` wins come from the contaminated dev
   slice. On the `28` unseen Verified tasks alone the discordant split is
   `2 : 0` (`p = 0.5`), and resolve rates are `21/28` vs. `19/28`. The
   defensible claim is "+7 pp on unseen tasks, never worse", not a
   significant gain.
2. **Cost.** On the `28` unseen tasks V14 averaged `232k` tokens and `513 s`
   per task vs. `146k` and `341 s` for flat ReAct (`1.6×` tokens, `1.5×` wall
   time). The latency *advantage* in the ablation holds on the dev slice only.
3. **Compute-unmatched baselines.** In the `MiniSQL` comparison the ratchet used
   `1.80M` tokens and `170` moves; the single-milestone baseline used `0.37M`
   and `23`. Flat ReAct's `0/15` traces to one brittle failure (a specialist
   overwrote `storage.py`).
4. **Single seed everywhere.** No variance estimates.
5. **Approximate grading.** [`scripts/grade_in_testbed.py`](../../scripts/grade_in_testbed.py)
   runs plain pytest over the touched files and describes itself as
   indicative. The official `swebench eval` harness has not yet been run on
   the `preds.jsonl` files.
6. **An unbacked result was removed.** Earlier docs cited a "5-milestone ratchet,
   `14/15` zero-shot, `1,976` LoC" run (and a blended "6/6, 672 lines, 1,069 s"
   row). Its artifacts (`results/ratchet/live_beam_minisql_eval_2026-10-21/`)
   were never committed, so it is no longer reported.

## 3. The bigger lesson: evolution was missing

V1–V7 evolved populations of firms, but the tasks saturated (`30/30` firms at
`7/7`), so selection never had to discriminate. From V8 to V14 every version
was **one hand-written genome**, and every improvement came from a human (or
an assistant) editing harness code. The project's central claim, that
*evolution* finds better agent organizations than hand design, was never
tested against hand design or random search.

Symptoms worth noting:
- Most of what V14 does (turn budgets per layer, branch loci, falsifier depth,
  CONT trigger, diff caps) is hard-coded rather than expressed as genes.
- The V13/V14 seed genome accumulated Era-1 prompt residue (a paragraph
  repeated three times and a "Level 3 RSI" instruction about rewriting
  `hae/genome/morphogenesis.py`). This is what genomes look like when
  mutation runs without selection pressure.

## 4. How V15 responds

V15 ([design](../v15/README.md)) makes the harness itself the unit of
evolution:

- **Knobs as genes.** The hard-coded V14 parameters become a typed gene space.
- **Harness code as genes from the start.** Each genome carries a patch against
  the harness source. A meta-agent (HAE's own engine) proposes code mutations
  from failure diagnostics, and the patches are gated by the unit-test suite
  before any evaluation, in the same spirit as the Darwin Gödel Machine but
  with ratchet-style safety gates.
- **Rigorous protocol.** Separate train, validation and test splits (the test
  split is never seen during evolution), plus a random-search control at equal
  compute and the hand-designed V14 as baseline.
