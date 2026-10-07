"""Builds the next generation's population from the last one's results.

Replaces ten near-identical one-off scripts (`breed_gen2_population.py` through
`breed_gen11_population.py`). Each was a copy of its predecessor with the
constants edited, which meant a fix to one -- most importantly, the switch from
ranking by legacy net score to ranking by the rebuilt rubric -- had to be
remembered ten times and was not. What a generation did lived in whichever copy
happened to run, so a run was not reproducible from configuration alone.

A generation is now declared, not scripted: `configs/generations/genNN.json`
says who the parents are, how they are ranked, how many of each offspring kind
to produce, and what the firms are told. This module reads that and produces
`configs/generation_NN_population.json`.

Ranking is by `hae.evaluation.judge.composite_score`, which weights measured
execution at 30%. Under the legacy ranking, five of six V1 champions were the
wrong firm, and Generation 9 bred forward the single worst firm in its cohort.
Where gate data is missing this module refuses to rank rather than emitting a
prose-only ordering that looks authoritative.

V8 adds cross-company role distillation at the generation boundary
(roadmap section 1.4): every breedable firm's `org` trajectory is read into
per-role statistics (`distill_role_statistics`), and each child's
`role_library` is then updated, pruned and extended with promoted recruits
(`hae.genome.mutator.evolve_role_library`). Children of V5/V6 parents carry no
library and a disabled `ceo_policy`, and nothing here touches them.
"""

import copy
import glob
import json
import os
import random
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Set, Tuple

from hae.epistemic.genes import crossover_epistemic_policy, mutate_epistemic_policy
from hae.evaluation.judge import (
    JUDGED_DIMENSIONS,
    composite_score,
    execution_integrity,
    resolve_execution_score,
)
from hae.genome.morphogenesis import MorphogenesisEngine, StructuralCrossoverEngine
from hae.genome.mutator import (
    RoleStatsSummary,
    crossover_ceo_policy,
    crossover_role_library,
    evolve_role_library,
    mutate_ceo_policy,
    mutate_role_library,
)
from hae.genome.role_seeds import SEED_FLAVOURS, seed_role_library
from hae.genome.schema import (
    CEOPolicyGene, CompanyGenome, EpistemicPolicyGene, GenomeValidationError, normalise_tags,
)
from hae.runtime.overlay import get_overlay_class

GENERATION_CONFIG_DIR = "configs/generations"
POPULATION_OUTPUT_DIR = "configs"


class BreedingError(RuntimeError):
    """Raised when a generation cannot be bred honestly."""


@dataclass
class GenerationSpec:
    """Everything that distinguishes one generation from another."""

    generation: int
    name: str
    # Where the parents' scorecards live. Empty for a seeded generation.
    parent_scorecards: str = ""
    # Seed genome, used when there is no parent generation.
    seed_template: str = ""
    survivors: int = 5
    # Offspring composition. Must sum to the intended population size.
    elite: int = 2
    crossover: int = 3
    pareto: int = 2
    mutant: int = 3
    # Injected into every CEO's system_instructions. Cohort context, not a fix:
    # it names what previous generations failed at, never how to fix it.
    mandate: str = ""
    # Exactly one of these three declares what the generation is asked to do.
    # `task_file` is the V2 form: it binds the objective to its verifier, its
    # spend ceiling and its action space, rather than leaving those three to be
    # configured somewhere else and hoped about.
    objective: str = ""
    benchmark_task: str = ""
    task_file: str = ""
    # V6: field overrides stamped onto every child's `epistemic_policy` gene
    # after inheritance, e.g. {"enabled": true}. Switching the epistemic
    # search on is a generation-level decision (it changes what fitness
    # measures), while the numeric fields keep evolving per lineage.
    epistemic_policy: Dict[str, Any] = field(default_factory=dict)
    # V8: the same kind of cohort-level override for the CEO gene, e.g.
    # {"enabled": true}. A child switched on this way that has no library yet
    # is seeded from `role_library_seed` (a `hae.genome.role_seeds` flavour),
    # because an enabled CEO with nothing to staff from would fall back to the
    # legacy seeds silently at run time; the breeder makes that choice visible.
    ceo_policy: Dict[str, Any] = field(default_factory=dict)
    role_library_seed: str = ""

    @property
    def population_size(self) -> int:
        return self.elite + self.crossover + self.pareto + self.mutant

    @classmethod
    def load(cls, path: str) -> "GenerationSpec":
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        known = {f for f in cls.__dataclass_fields__}
        unknown = set(data) - known
        if unknown:
            raise BreedingError(
                f"{path} declares unknown keys {sorted(unknown)}. A silently "
                "ignored setting is a generation that did not do what its "
                "config says it did.")
        spec = cls(**data)
        declared = [k for k in ("objective", "benchmark_task", "task_file")
                    if getattr(spec, k)]
        if not declared:
            raise BreedingError(
                f"{path} declares no work: set one of `task_file` (preferred), "
                f"`benchmark_task`, or `objective`.")
        if len(declared) > 1:
            raise BreedingError(
                f"{path} sets {declared}; pick exactly one. Two sources for "
                f"the objective is two objectives, and only one of them ends "
                f"up in front of the firms.")
        if spec.role_library_seed and spec.role_library_seed not in SEED_FLAVOURS:
            raise BreedingError(
                f"{path} sets role_library_seed={spec.role_library_seed!r}; known flavours: {SEED_FLAVOURS}")
        return spec


@dataclass
class RankedFirm:
    """One parent candidate, with the evidence behind its rank."""

    company_id: str
    genome: CompanyGenome
    rubric_score: float
    execution_integrity: float
    gates_passed: int
    legacy_net: float
    evaluation_failed: bool = False
    judged: Dict[str, float] = field(default_factory=dict)
    # The scorecard itself. V8 distillation reads the firm's `org` trajectory
    # from it; nothing about ranking looks at it.
    record: Dict[str, Any] = field(default_factory=dict)


def _gate_status(card: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """Gate verdicts from a scorecard, or None if it has none.

    Explicitly does not synthesise verdicts from legacy booleans. Those came
    from heuristics that never executed anything, and treating them as
    measurements is how V1 published six generations of progress that was not
    there.
    """
    verification = card.get("verification") or {}
    status = verification.get("gate_status")
    if isinstance(status, dict) and status:
        return {k: str(v).lower() for k, v in status.items()}
    return None


def _load_scorecard_entries(scorecard_dir: str) -> List[Tuple[str, Dict[str, Any]]]:
    """Loads (source_path, scorecard_dict) pairs from a local directory or gs:// URI."""
    if scorecard_dir.startswith("gs://"):
        import re
        import urllib.parse
        import urllib.request
        from hae.infra.llm import get_adc_access_token

        # Check local mirror first if harvested to experiments/v2/generation_<N>_results
        m = re.search(r"generation_(\d+)/?$", scorecard_dir.rstrip("/"))
        if m:
            local_mirror = os.path.join("experiments/v2", f"generation_{m.group(1)}_results")
            local_paths = sorted(glob.glob(os.path.join(local_mirror, "*.json")))
            if local_paths:
                out = []
                for p in local_paths:
                    with open(p, "r", encoding="utf-8") as fh:
                        out.append((p, json.load(fh)))
                return out

        # Otherwise fetch directly from GCS
        without_scheme = scorecard_dir[len("gs://"):]
        bucket, _, prefix = without_scheme.partition("/")
        prefix = prefix.rstrip("/") + "/"
        token = get_adc_access_token()
        if not token:
            raise BreedingError(f"No ADC access token available to read {scorecard_dir}")
        listing = urllib.parse.quote(prefix)
        url = f"https://storage.googleapis.com/storage/v1/b/{bucket}/o?prefix={listing}"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            items = json.load(resp).get("items", [])
        out: List[Tuple[str, Dict[str, Any]]] = []
        for item in sorted(items, key=lambda x: x.get("name", "")):
            name = item.get("name", "")
            if not name.endswith("_result.json"):
                continue
            obj = urllib.parse.quote(name, safe="")
            media = f"https://storage.googleapis.com/storage/v1/b/{bucket}/o/{obj}?alt=media"
            r = urllib.request.Request(media, headers={"Authorization": f"Bearer {token}"})
            with urllib.request.urlopen(r, timeout=120) as resp:
                out.append((f"gs://{bucket}/{name}", json.load(resp)))
        return out

    paths = sorted(glob.glob(os.path.join(scorecard_dir, "*.json")))
    out = []
    for path in paths:
        with open(path, "r", encoding="utf-8") as fh:
            out.append((path, json.load(fh)))
    return out


def rank_scorecards(scorecard_dir: str) -> List[RankedFirm]:
    """Ranks a generation's firms by the rebuilt rubric. Refuses without execution scores."""
    entries = _load_scorecard_entries(scorecard_dir)
    if not entries:
        raise BreedingError(f"No scorecards found in {scorecard_dir}")

    ranked: List[RankedFirm] = []
    ungated: List[str] = []
    for path, card in entries:
        company_id = card.get("company_id") or os.path.basename(path)

        status = _gate_status(card)
        exec_score = resolve_execution_score(card.get("verification"))
        if exec_score is None and card.get("execution_evaluable") is True:
            raw_exec = card.get("execution_integrity")
            if isinstance(raw_exec, (int, float)):
                exec_score = float(raw_exec)

        if exec_score is None:
            ungated.append(company_id)
            continue

        judged = {d: float(card.get(d, 0.0)) for d in JUDGED_DIMENSIONS}
        # Scorecards spell actionability_and_synthesis as `actionability`.
        if not judged["actionability_and_synthesis"]:
            judged["actionability_and_synthesis"] = float(
                card.get("actionability", 0.0))

        genome_data = card.get("genome")
        if not genome_data:
            raise BreedingError(f"{path} has no genome to breed from")
        try:
            genome = CompanyGenome.from_dict(genome_data)
        except GenomeValidationError as exc:
            raise BreedingError(f"{path} holds an invalid genome: {exc}") from exc

        if exec_score is not None and exec_score >= 100.0:
            ws_files = (card.get("run_output") or {}).get("workspace_files") or {}
            for fpath, fcontent in ws_files.items():
                clean_p = fpath.lstrip("./")
                if (clean_p.startswith("hae/") and clean_p.endswith(".py")
                        and "test" not in os.path.basename(clean_p)
                        and isinstance(fcontent, str)):
                    genome.code_overlays[clean_p] = fcontent

        if status:
            gates_passed = sum(1 for v in status.values() if v == "passed")
        else:
            ver = card.get("verification") or {}
            details = ver.get("evidence") or ver.get("details") or {}
            gates_passed = int(details.get("tests_passed", 1 if exec_score >= 100.0 else 0))

        # V6 scorecards carry the non-LLM ledger audit; ranking must use the
        # same composite the worker scored with, or the breeder would select
        # on a different fitness than the one the firms were measured by.
        audit = card.get("epistemic_audit")
        epistemic_audit = audit if isinstance(audit, dict) and audit else None

        ranked.append(RankedFirm(
            company_id=company_id,
            genome=genome,
            rubric_score=composite_score(judged, exec_score, epistemic_audit=epistemic_audit),
            execution_integrity=exec_score or 0.0,
            gates_passed=gates_passed,
            legacy_net=float(card.get("fitness_score")
                             if card.get("fitness_score") is not None
                             else card.get("overall_score") or 0.0),
            evaluation_failed=bool(card.get("evaluation_failed", False)),
            judged=judged,
            record=card,
        ))

    if ungated:
        raise BreedingError(
            f"{len(ungated)} of {len(entries)} firms have no execution gate data: "
            f"{', '.join(sorted(ungated)[:8])}. Ranking them would produce a "
            "prose-only ordering that looks authoritative and is not. Run the "
            "execution backfill first.")

    # A firm whose evaluation failed scored 0.0 for reasons that say nothing
    # about its genome. It must not be bred forward.
    breedable = [r for r in ranked if not r.evaluation_failed]
    if not breedable:
        raise BreedingError("Every firm in this cohort has a failed evaluation.")

    breedable.sort(key=lambda r: r.rubric_score, reverse=True)
    return breedable


# --------------------------------------------------------------------------- #
# V8: cross-company role distillation
# --------------------------------------------------------------------------- #
#
# A result record is the worker's scorecard: `run_output` is the last repair
# iteration's output, which (for a V8 firm) carries `org` (the final
# `OrgState.to_dict()`), `org_audit` (the turn-0 selection audit) and
# `org_history` (one {iteration, org, audit} entry per epistemic iteration,
# accumulated by the runner). Everything below tolerates any of these being
# absent -- V5/V6 records contribute nothing -- and also accepts a bare
# `run_output` dict, so the functions work on in-process results too.

def _run_output(record: Mapping[str, Any]) -> Mapping[str, Any]:
    ro = record.get("run_output")
    return ro if isinstance(ro, Mapping) else record


def _org_dicts(record: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    """Every `OrgState` dict a result record carries, one per epistemic iteration."""
    ro = _run_output(record)
    history = ro.get("org_history")
    out: List[Mapping[str, Any]] = []
    if isinstance(history, list):
        for entry in history:
            if not isinstance(entry, Mapping):
                continue
            org = entry.get("org") if isinstance(entry.get("org"), Mapping) else (
                entry if "stats" in entry else None)
            if org is not None:
                out.append(org)
    if out:
        return out
    org = ro.get("org") if isinstance(ro.get("org"), Mapping) else record.get("org")
    return [org] if isinstance(org, Mapping) else []


def _org_audits(record: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    ro = _run_output(record)
    out: List[Mapping[str, Any]] = []
    history = ro.get("org_history")
    if isinstance(history, list):
        out += [e.get("audit") for e in history if isinstance(e, Mapping) and isinstance(e.get("audit"), Mapping)]
    if not out and isinstance(ro.get("org_audit"), Mapping):
        out.append(ro["org_audit"])
    return out


def _record_resolved(record: Mapping[str, Any]) -> bool:
    """Whether the firm's final oracle verdict was a full pass."""
    score = resolve_execution_score(record.get("verification"))
    if score is None:
        vo = record.get("verifier_outcome")
        score = resolve_execution_score(vo) if isinstance(vo, Mapping) else None
    if score is not None:
        return score >= 100.0
    ro = _run_output(record)
    history = record.get("iterations_history") or ro.get("iterations_history") or []
    return any(bool(h.get("passed")) for h in history if isinstance(h, Mapping))


def _move_role_credits(record: Mapping[str, Any]) -> List[Tuple[str, float]]:
    """(role_id, delta_u) per move, from the search trajectories, for records without an `org` dict."""
    ro = _run_output(record)
    searches = ro.get("epistemic_searches")
    if not isinstance(searches, list):
        searches = [ro["epistemic_search"]] if isinstance(ro.get("epistemic_search"), Mapping) else []
    out: List[Tuple[str, float]] = []
    for s in searches:
        for m in (s.get("trajectory") or []) if isinstance(s, Mapping) else []:
            if not isinstance(m, Mapping):
                continue
            rid = m.get("role_id") or (m.get("extra") or {}).get("role_id") if isinstance(m.get("extra"), Mapping) else m.get("role_id")
            if rid:
                try:
                    out.append((str(rid), float(m.get("delta_u", 0.0) or 0.0)))
                except (TypeError, ValueError):
                    out.append((str(rid), 0.0))
    return out


def distill_role_statistics(results: Iterable[Mapping[str, Any]]) -> Dict[str, RoleStatsSummary]:
    """Per-role evidence summed over a generation's result records.

    Reads each record's `org` dicts (per-role `stats`, the `active_roles`
    alleles and the `recruit_log`). A role counts as recruited in a record when
    it appears in the recruit log, its stats say `recruited_at_move >= 0`, or
    its allele's origin starts with `recruited:`. A role is credited with a
    resolved task when the record passed the oracle and the role wrote a patch
    or earned a SUPPORTED verdict in it. Records without any `org` dict fall
    back to move-level `role_id`s in the search trajectories (visits and dU
    only). All of it is end-of-move credit, not causal credit.
    """
    out: Dict[str, RoleStatsSummary] = {}
    for rec in results:
        if not isinstance(rec, Mapping):
            continue
        orgs = _org_dicts(rec)
        resolved = _record_resolved(rec)
        seen: Set[str] = set()
        recruited: Set[str] = set()
        credited_resolution: Set[str] = set()
        for org in orgs:
            alleles = {str(a.get("role_id")): a for a in (org.get("active_roles") or [])
                       if isinstance(a, Mapping) and a.get("role_id")}
            rec_ids = {str(e.get("role_id")) for e in (org.get("recruit_log") or []) if isinstance(e, Mapping)}
            rec_ids |= {rid for rid, a in alleles.items() if str(a.get("origin") or "").startswith("recruited:")}
            for rid, st in (org.get("stats") or {}).items():
                if not isinstance(st, Mapping):
                    continue
                rid = str(rid)
                s = out.setdefault(rid, RoleStatsSummary(role_id=rid))
                visits = int(st.get("visits", 0) or 0)
                s.uses += visits
                s.cumulative_delta_u += float(st.get("cumulative_delta_u", 0.0) or 0.0)
                s.supported += int(st.get("supported", 0) or 0)
                s.falsified += int(st.get("falsified", 0) or 0)
                s.untestable += int(st.get("untestable", 0) or 0)
                s.syntheses_written += int(st.get("syntheses_written", 0) or 0)
                if int(st.get("recruited_at_move", -1) or -1) >= 0:
                    rec_ids.add(rid)
                seen.add(rid)
                if resolved and (int(st.get("syntheses_written", 0) or 0) > 0 or int(st.get("supported", 0) or 0) > 0):
                    credited_resolution.add(rid)
            for rid, a in alleles.items():
                s = out.setdefault(rid, RoleStatsSummary(role_id=rid))
                s.allele = dict(a)
                s.name = str(a.get("name") or s.name)
                seen.add(rid)
            recruited |= rec_ids & set(out)
        if not orgs:
            for rid, du in _move_role_credits(rec):
                s = out.setdefault(rid, RoleStatsSummary(role_id=rid))
                s.uses += 1
                s.cumulative_delta_u += du
                seen.add(rid)
        for rid in seen:
            out[rid].firms += 1
        for rid in recruited:
            out[rid].recruited += 1
        for rid in credited_resolution:
            out[rid].tasks_resolved += 1
    return out


def distill_tag_pool(results: Iterable[Mapping[str, Any]]) -> List[str]:
    """Tags the generation met that its teams did not cover, for the one-tag role mutation.

    Unmatched modules (split into path stems) come first in importance but the
    pool is returned sorted and de-duplicated so the mutation is reproducible.
    Task tags from the turn-0 audits are included too.
    """
    pool: List[str] = []
    for rec in results:
        if not isinstance(rec, Mapping):
            continue
        for org in _org_dicts(rec):
            for m in org.get("unmatched_modules") or []:
                pool += [p for p in re.split(r"[/.]", str(m)) if p and p != "py"]
        for audit in _org_audits(rec):
            task = audit.get("task")
            if isinstance(task, Mapping):
                pool += [str(t) for t in (task.get("tags") or [])]
    return sorted(set(normalise_tags(pool)))


class Breeder:
    """Produces one generation's population from a spec."""

    def __init__(self, spec: GenerationSpec, repo_root: str = "."):
        self.spec = spec
        self.repo_root = repo_root
        self.morphogenesis = MorphogenesisEngine()
        self.crossover = StructuralCrossoverEngine()
        # V8: filled by `survivors()` from every breedable firm's trajectory,
        # not only the parents'. A role's evidence is cross-company (roadmap
        # section 1.4): a specialist that paid off in a firm that did not make
        # the cut is still a specialist that paid off.
        self.role_stats: Dict[str, RoleStatsSummary] = {}
        self.tag_pool: List[str] = []

    # ------------------------------------------------------------------ #

    def survivors(self) -> List[RankedFirm]:
        if not self.spec.parent_scorecards:
            raise BreedingError(
                f"Generation {self.spec.generation} declares no parent "
                "scorecards. Use `seed_population()` for a seeded generation.")
        target = (self.spec.parent_scorecards
                  if self.spec.parent_scorecards.startswith("gs://")
                  else os.path.join(self.repo_root, self.spec.parent_scorecards))
        ranked = rank_scorecards(target)
        self.role_stats = distill_role_statistics(r.record for r in ranked)
        self.tag_pool = distill_tag_pool(r.record for r in ranked)
        return ranked[: self.spec.survivors]

    def seed_population(self) -> List[CompanyGenome]:
        """The population for a generation with no ancestors.

        Two of the four operators are meaningless here and saying so is the
        whole point of this method. Crossover needs two parents; there is one.
        Pareto extremes need per-dimension scores to be extreme on; nothing has
        been scored yet. Filling those slots anyway would produce clones
        labelled `crossover` and `pareto`, and the CompletenessGate classifies
        firms by that label -- so a wiped-out operator class would be
        undetectable precisely because the labels were fiction.

        So a seeded generation has two honest classes: `elite`, meaning the
        untouched seed, which is the control the next generation is measured
        against; and `mutant`, topology variants that supply the initial
        diversity. The population size the spec declares is preserved.
        """
        if not self.spec.seed_template:
            raise BreedingError(
                f"Generation {self.spec.generation} has neither "
                "`parent_scorecards` nor `seed_template`; there is nothing to "
                "breed from.")

        path = os.path.join(self.repo_root, self.spec.seed_template)
        if not os.path.exists(path):
            raise BreedingError(f"seed_template not found: {path}")
        with open(path, "r", encoding="utf-8") as fh:
            seed = CompanyGenome.from_dict(json.load(fh))

        gen = self.spec.generation
        population: List[CompanyGenome] = []

        for i in range(self.spec.elite):
            population.append(self._with_mandate(
                seed, f"gen_{gen}_elite_{i + 1}",
                f"Unmodified seed from {self.spec.seed_template}"))

        # Everything that is not the control is a topology variant. Counted
        # from the spec's total so the population size stays what was declared.
        variants = (self.spec.crossover + self.spec.pareto + self.spec.mutant)
        for i in range(variants):
            child = self.morphogenesis.morph_genome_topology(
                seed,
                mutation_name=f"Generation {gen} seed diversification {i + 1}",
                target_generation=gen,
                child_id=f"gen_{gen}_mutant_{i + 1}")
            population.append(self._with_mandate(
                child, child.company_id, "Seed topology variant"))

        self._apply_policy_overrides(population)
        self._assert_distinct(population)
        return population

    def _with_mandate(self, genome: CompanyGenome, company_id: str,
                      lineage: str) -> CompanyGenome:
        child = copy.deepcopy(genome)
        child.company_id = company_id
        child.generation = self.spec.generation
        child.parent_ids = [genome.company_id]
        child.mutation_history = list(genome.mutation_history) + [lineage]
        if self.spec.mandate and child.ceo is not None:
            existing = child.ceo.system_instructions or ""
            child.ceo.system_instructions = (
                f"{existing}\n\n{self.spec.mandate}".strip())
        return child

    def breed(self) -> List[CompanyGenome]:
        """The full population for this generation, in a deterministic order."""
        if not self.spec.parent_scorecards:
            return self.seed_population()
        parents = self.survivors()
        gen = self.spec.generation
        population: List[CompanyGenome] = []

        # Elites: the best parents carried forward unchanged except for the
        # mandate. They are the control group -- without them a generation
        # cannot be compared to its predecessor.
        for i in range(self.spec.elite):
            parent = parents[i % len(parents)]
            child = self._with_mandate(
                parent.genome, f"gen_{gen}_elite_{i + 1}",
                f"Elite clone of {parent.company_id} "
                f"(rubric {parent.rubric_score:.2f}, "
                f"exec {parent.execution_integrity:.1f})")
            self._evolve_organisation(child, "elite", i + 1)
            population.append(child)

        # Structural crossovers between the top parents, aligned by the
        # functional role of each department rather than by position.
        for i in range(self.spec.crossover):
            a = parents[i % len(parents)]
            b = parents[(i + 1) % len(parents)]
            crossover_cls = get_overlay_class(
                a.genome.code_overlays or b.genome.code_overlays,
                "hae/genome/morphogenesis.py",
                "StructuralCrossoverEngine",
                StructuralCrossoverEngine,
                firm_id=a.company_id,
            )
            try:
                child = crossover_cls().recombine(
                    a.genome, b.genome,
                    child_id=f"gen_{gen}_crossover_{i + 1}",
                    target_generation=gen,
                    label=f"Structural crossover {a.company_id} x {b.company_id}")
            except Exception:
                child = self.crossover.recombine(
                    a.genome, b.genome,
                    child_id=f"gen_{gen}_crossover_{i + 1}",
                    target_generation=gen,
                    label=f"Structural crossover {a.company_id} x {b.company_id}")
            merged_overlays = dict(b.genome.code_overlays or {})
            merged_overlays.update(a.genome.code_overlays or {})
            child.code_overlays = merged_overlays
            # The search policy recombines like topology does: per-field, with
            # numeric midpoints, under a seed derived from the slot so the
            # same spec always breeds the same child.
            child.epistemic_policy = crossover_epistemic_policy(
                a.genome.epistemic_policy, b.genome.epistemic_policy,
                random.Random(f"gen{gen}-crossover-{i + 1}"))
            # V8: the role library is the union of both parents' (de-duplicated
            # by role id, the better-evidenced allele winning) and the CEO gene
            # recombines uniformly. Only when at least one parent carries the
            # genes: two V6 parents breed a V6 child, byte for byte.
            if (a.genome.role_library or b.genome.role_library
                    or a.genome.org_enabled or b.genome.org_enabled):
                org_rng = random.Random(f"gen{gen}-crossover-{i + 1}-org")
                child.ceo_policy = crossover_ceo_policy(
                    a.genome.ceo_policy, b.genome.ceo_policy, org_rng)
                child.role_library = crossover_role_library(
                    a.genome.role_library, b.genome.role_library, org_rng,
                    policy=child.ceo_policy)
                child.mutation_history.append(
                    f"Generation {gen} role library: union of {a.company_id} "
                    f"({len(a.genome.role_library)} roles) and {b.company_id} "
                    f"({len(b.genome.role_library)} roles) -> {len(child.role_library)} roles; "
                    f"ceo_policy recombined uniformly")
            child = self._with_mandate(
                child, child.company_id,
                f"Crossover of {a.company_id} and {b.company_id}")
            self._evolve_organisation(child, "crossover", i + 1)
            population.append(child)

        # Pareto extremes: the per-dimension champions, which the aggregate
        # ranking hides. The best executor is often not the best overall.
        for i, dimension in enumerate(self._pareto_dimensions()[: self.spec.pareto]):
            champion = max(parents, key=lambda r: r.judged.get(
                dimension, r.execution_integrity if dimension == "execution" else 0.0))
            child = self._with_mandate(
                champion.genome, f"gen_{gen}_pareto_{i + 1}",
                f"Pareto extreme on {dimension} (from {champion.company_id})")
            self._evolve_organisation(child, "pareto", i + 1)
            population.append(child)

        # Directed mutants: topology changes, which is the only operator that
        # can add or remove a department.
        for i in range(self.spec.mutant):
            parent = parents[i % len(parents)]
            morph_cls = get_overlay_class(
                parent.genome.code_overlays,
                "hae/genome/morphogenesis.py",
                "MorphogenesisEngine",
                MorphogenesisEngine,
                firm_id=parent.company_id,
            )
            try:
                child = morph_cls().morph_genome_topology(
                    parent.genome,
                    mutation_name=f"Generation {gen} directed morphogenesis {i + 1}",
                    target_generation=gen,
                    child_id=f"gen_{gen}_mutant_{i + 1}")
            except Exception:
                child = self.morphogenesis.morph_genome_topology(
                    parent.genome,
                    mutation_name=f"Generation {gen} directed morphogenesis {i + 1}",
                    target_generation=gen,
                    child_id=f"gen_{gen}_mutant_{i + 1}")
            child.code_overlays = dict(parent.genome.code_overlays or {})
            # Bounded jitter on the numeric search knobs and, rarely, a role
            # re-binding. `enabled` is never flipped by an operator.
            child.epistemic_policy = mutate_epistemic_policy(
                parent.genome.epistemic_policy,
                random.Random(f"gen{gen}-mutant-{i + 1}"))
            child = self._with_mandate(
                child, child.company_id,
                f"Morphogenesis from {parent.company_id}")
            # V8: mutants are the only children whose CEO gene is jittered and
            # whose library gets the one-tag text mutation.
            self._evolve_organisation(child, "mutant", i + 1, mutate=True)
            population.append(child)

        self._apply_policy_overrides(population)
        self._assert_distinct(population)
        return population

    # ------------------------------------------------------------------ #
    # V8: organisation genes at the generation boundary
    # ------------------------------------------------------------------ #

    def _evolve_organisation(self, child: CompanyGenome, kind: str, slot: int,
                             mutate: bool = False) -> None:
        """Updates `child.role_library` (and, for mutants, `child.ceo_policy`) in place.

        Statistics update -> prune -> promote -> cap, from the generation's
        cross-company `role_stats` (`hae.genome.mutator.evolve_role_library`).
        With `mutate`, the CEO gene is jittered within bounds and one role gets
        a tag from the generation's unmatched-module pool. Both draw from an
        rng seeded by generation, child kind and slot, so the same spec breeds
        the same child. A child with no library and a disabled CEO gene is left
        exactly as it was: V5/V6 lineages breed as they did before V8.
        """
        if not child.role_library and not child.org_enabled:
            return
        gen = self.spec.generation
        rng = random.Random(f"gen{gen}-{kind}-{slot}-org")
        library, notes = evolve_role_library(
            child.role_library, self.role_stats, child.ceo_policy, gen,
            summarise_updates=True)
        if mutate:
            child.ceo_policy, gene_notes = mutate_ceo_policy(child.ceo_policy, rng)
            notes += gene_notes
            library, tag_notes = mutate_role_library(library, rng, self.tag_pool, gen)
            notes += tag_notes
        child.role_library = library
        if notes:
            child.mutation_history.append(
                f"Generation {gen} role library ({len(library)} roles): " + "; ".join(notes))

    def _apply_policy_overrides(self, population: List[CompanyGenome]) -> None:
        """Stamps the spec's `epistemic_policy` and `ceo_policy` overrides onto every child.

        Applied after inheritance so the cohort-level decision (is the search
        on? what budget? is the CEO staffing the loop?) wins, while unlisted
        fields keep whatever the operators bred. Unknown or out-of-range
        fields raise: a generation config that says `enabled: true` and is
        silently ignored is a generation that did not run the experiment it
        claims to have run.
        """
        self._apply_epistemic_overrides(population)
        self._apply_ceo_overrides(population)

    def _apply_epistemic_overrides(self, population: List[CompanyGenome]) -> None:
        overrides = dict(self.spec.epistemic_policy or {})
        if not overrides:
            return
        unknown = set(overrides) - set(EpistemicPolicyGene().to_dict())
        if unknown:
            raise BreedingError(
                f"Generation {self.spec.generation} sets unknown epistemic_policy "
                f"fields {sorted(unknown)}.")
        for genome in population:
            current = genome.epistemic_policy.to_dict() if genome.epistemic_policy else {}
            current.update(overrides)
            try:
                genome.epistemic_policy = EpistemicPolicyGene.from_dict(current)
            except (GenomeValidationError, ValueError, TypeError) as exc:
                raise BreedingError(
                    f"Generation {self.spec.generation} epistemic_policy override "
                    f"{overrides} is invalid for {genome.company_id}: {exc}") from exc
            if overrides.get("enabled"):
                genome.mutation_history.append(
                    f"Generation {self.spec.generation}: V6 epistemic search enabled "
                    f"(budget={genome.epistemic_policy.search_budget_moves} moves)")

    def _apply_ceo_overrides(self, population: List[CompanyGenome]) -> None:
        """V8: the same cohort-level override for the CEO gene.

        A child switched on here that carries no role library is seeded from
        `spec.role_library_seed` (default `legacy`), and says so in its
        history. The runtime would otherwise fall back to the legacy seeds on
        its own, which is the right behaviour for a hand-edited genome and the
        wrong one for a bred population, where the choice should be on record.
        """
        overrides = dict(self.spec.ceo_policy or {})
        if not overrides:
            return
        unknown = set(overrides) - set(CEOPolicyGene().to_dict())
        if unknown:
            raise BreedingError(
                f"Generation {self.spec.generation} sets unknown ceo_policy "
                f"fields {sorted(unknown)}.")
        flavour = self.spec.role_library_seed or "legacy"
        for genome in population:
            current = genome.ceo_policy.to_dict() if genome.ceo_policy else {}
            current.update(overrides)
            try:
                genome.ceo_policy = CEOPolicyGene.from_dict(current)
            except (GenomeValidationError, ValueError, TypeError) as exc:
                raise BreedingError(
                    f"Generation {self.spec.generation} ceo_policy override "
                    f"{overrides} is invalid for {genome.company_id}: {exc}") from exc
            if overrides.get("enabled"):
                note = (f"Generation {self.spec.generation}: V8 CEO policy enabled "
                        f"(initial team {genome.ceo_policy.min_initial_roles}-"
                        f"{genome.ceo_policy.max_initial_roles}, "
                        f"max active {genome.ceo_policy.max_active_roles}, "
                        f"recruit_mode={genome.ceo_policy.recruit_mode})")
                if not genome.role_library:
                    genome.role_library = seed_role_library(flavour)
                    note += f"; role library seeded from {flavour!r} ({len(genome.role_library)} roles)"
                genome.mutation_history.append(note)

    def _pareto_dimensions(self) -> List[str]:
        # Execution first: it is the dimension the programme exists to improve
        # and the one the judge cannot see.
        return ["execution", "technical_feasibility", "strategic_depth",
                "risk_mitigation", "cross_functional_coherence"]

    @staticmethod
    def _assert_distinct(population: List[CompanyGenome]) -> None:
        ids = [g.company_id for g in population]
        duplicates = {i for i in ids if ids.count(i) > 1}
        if duplicates:
            raise BreedingError(
                f"Duplicate company ids in population: {sorted(duplicates)}")

    def write(self, population: List[CompanyGenome]) -> str:
        out_dir = os.path.join(self.repo_root, POPULATION_OUTPUT_DIR)
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(
            out_dir, f"generation_{self.spec.generation}_population.json")
        payload = {
            "generation": self.spec.generation,
            "name": self.spec.name,
            "objective": self.spec.objective,
            "benchmark_task": self.spec.benchmark_task,
            "population": [g.to_dict() for g in population],
        }
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)

        overlays_root = os.path.join(
            self.repo_root, "experiments", "v3_rsi", "overlays", f"generation_{self.spec.generation}"
        )
        for g in population:
            if g.code_overlays:
                for mod_path, mod_code in g.code_overlays.items():
                    full_ov = os.path.join(overlays_root, g.company_id, mod_path.lstrip("./"))
                    os.makedirs(os.path.dirname(full_ov), exist_ok=True)
                    with open(full_ov, "w", encoding="utf-8") as ov_fh:
                        ov_fh.write(mod_code)
        return path


def breed_generation(spec_path: str, repo_root: str = ".") -> Tuple[str, List[CompanyGenome]]:
    """Loads a generation spec, breeds it, and writes the population file."""
    spec = GenerationSpec.load(spec_path)
    existing_path = os.path.join(
        repo_root, POPULATION_OUTPUT_DIR, f"generation_{spec.generation}_population.json")
    if os.environ.get("HAE_REUSE_EXISTING_POPULATION") == "1" and os.path.exists(existing_path):
        with open(existing_path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        population = [CompanyGenome.from_dict(g) for g in payload.get("population", [])]
        if population:
            return existing_path, population
    breeder = Breeder(spec, repo_root=repo_root)
    population = breeder.breed()
    path = breeder.write(population)
    return path, population
