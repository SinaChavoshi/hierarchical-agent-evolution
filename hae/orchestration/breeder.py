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
"""

import copy
import glob
import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from hae.evaluation.judge import (
    JUDGED_DIMENSIONS,
    composite_score,
    execution_integrity,
    resolve_execution_score,
)
from hae.genome.morphogenesis import MorphogenesisEngine, StructuralCrossoverEngine
from hae.genome.schema import CompanyGenome, GenomeValidationError
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

        ranked.append(RankedFirm(
            company_id=company_id,
            genome=genome,
            rubric_score=composite_score(judged, exec_score),
            execution_integrity=exec_score or 0.0,
            gates_passed=gates_passed,
            legacy_net=float(card.get("fitness_score")
                             if card.get("fitness_score") is not None
                             else card.get("overall_score") or 0.0),
            evaluation_failed=bool(card.get("evaluation_failed", False)),
            judged=judged,
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


class Breeder:
    """Produces one generation's population from a spec."""

    def __init__(self, spec: GenerationSpec, repo_root: str = "."):
        self.spec = spec
        self.repo_root = repo_root
        self.morphogenesis = MorphogenesisEngine()
        self.crossover = StructuralCrossoverEngine()

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
            population.append(self._with_mandate(
                parent.genome, f"gen_{gen}_elite_{i + 1}",
                f"Elite clone of {parent.company_id} "
                f"(rubric {parent.rubric_score:.2f}, "
                f"exec {parent.execution_integrity:.1f})"))

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
            population.append(self._with_mandate(
                child, child.company_id,
                f"Crossover of {a.company_id} and {b.company_id}"))

        # Pareto extremes: the per-dimension champions, which the aggregate
        # ranking hides. The best executor is often not the best overall.
        for i, dimension in enumerate(self._pareto_dimensions()[: self.spec.pareto]):
            champion = max(parents, key=lambda r: r.judged.get(
                dimension, r.execution_integrity if dimension == "execution" else 0.0))
            population.append(self._with_mandate(
                champion.genome, f"gen_{gen}_pareto_{i + 1}",
                f"Pareto extreme on {dimension} (from {champion.company_id})"))

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
            population.append(self._with_mandate(
                child, child.company_id,
                f"Morphogenesis from {parent.company_id}"))

        self._assert_distinct(population)
        return population

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
