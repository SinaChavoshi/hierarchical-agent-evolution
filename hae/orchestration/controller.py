"""Runs generations back to back without a human in the loop.

V1 ran every generation by hand: breed, render a manifest, `kubectl apply`,
watch, harvest, edit a README, repeat. Roughly five steps, thirteen times. It
worked because someone was always watching, which is also why it cannot be what
a platform does when a user submits a goal and leaves.

Automating it is mostly plumbing over scripts that already exist. The part that
is not plumbing is knowing **when to stop**, and the Generation 11 post-mortem
is the reason that gets real attention here.

The Generation 11 failure, and the gate it produced
---------------------------------------------------
Gen 11 launched ten firms. Three crashed at genome load -- and those three were
`gen_11_pareto_bonus_1`, `gen_11_mutant_1` and `gen_11_mutant_2`: every
structurally novel topology in the population. The seven survivors were elites
and crossovers, i.e. the firms that had changed least.

An automated controller would have harvested those seven, computed a mean,
written it to a ledger as "Generation 11", and bred generation 12 from a gene
pool that had silently had all its exploration removed. Repeated over a few
generations that is not a bug that shows up as an error; it is a slow collapse
of diversity reported as a rising fitness curve.

So `CompletenessGate` refuses a generation when the survivors are not a fair
sample of what was launched -- specifically when any breeding operator class is
wiped out, even if the overall completion rate looks acceptable. A controller
that runs unattended has to be more suspicious than an operator who can see the
pod list.
"""

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from hae.orchestration.breeder import GenerationSpec, breed_generation
from hae.task import Task

#: Operator classes a bred population is expected to contain. Derived from the
#: `company_id` prefix the breeder assigns.
OPERATOR_CLASSES = ("elite", "crossover", "consensus", "pareto", "mutant")


class ControllerError(RuntimeError):
    """Raised when the loop cannot safely continue."""


# ---------------------------------------------------------------------- #
# Stopping criteria
# ---------------------------------------------------------------------- #

@dataclass
class StopDecision:
    """Why the loop did or did not continue."""

    stop: bool
    reason: str = ""

    def __bool__(self) -> bool:
        return self.stop


@dataclass
class StoppingCriteria:
    """When to stop running generations.

    All limits are ceilings, not targets. A loop with no ceiling and an
    open-ended objective is an unbounded bill, which is the whole reason the
    budget work landed before this did.
    """

    max_generations: int = 10
    max_total_usd: Optional[float] = None
    max_wall_seconds: Optional[float] = None

    # Stop when the best score has not improved by `plateau_delta` for this
    # many consecutive generations. Optimising past a plateau spends real money
    # to move noise around.
    plateau_generations: Optional[int] = 3
    plateau_delta: float = 0.5

    def evaluate(self, history: List["GenerationOutcome"],
                 elapsed_s: float, spent_usd: float) -> StopDecision:
        if len(history) >= self.max_generations:
            return StopDecision(True, f"reached max_generations={self.max_generations}")
        if self.max_total_usd is not None and spent_usd >= self.max_total_usd:
            return StopDecision(
                True, f"spent ${spent_usd:.2f} of ${self.max_total_usd:.2f} ceiling")
        if self.max_wall_seconds is not None and elapsed_s >= self.max_wall_seconds:
            return StopDecision(
                True, f"elapsed {elapsed_s:.0f}s of {self.max_wall_seconds:.0f}s ceiling")

        if self.plateau_generations and len(history) > self.plateau_generations:
            window = history[-(self.plateau_generations + 1):]
            best = [g.best_score for g in window if g.best_score is not None]
            if len(best) == len(window):
                improvement = max(best) - best[0]
                if improvement < self.plateau_delta:
                    return StopDecision(
                        True,
                        f"no improvement > {self.plateau_delta} over "
                        f"{self.plateau_generations} generations "
                        f"(best moved {improvement:+.2f})")
        return StopDecision(False)


# ---------------------------------------------------------------------- #
# Completeness
# ---------------------------------------------------------------------- #

@dataclass
class CompletenessReport:
    launched: int
    completed: int
    missing: List[str] = field(default_factory=list)
    missing_classes: List[str] = field(default_factory=list)
    ok: bool = True
    reason: str = ""

    @property
    def completion_rate(self) -> float:
        return round(self.completed / self.launched, 3) if self.launched else 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "launched": self.launched,
            "completed": self.completed,
            "completion_rate": self.completion_rate,
            "missing": self.missing,
            "missing_classes": self.missing_classes,
            "ok": self.ok,
            "reason": self.reason,
        }


def operator_class(company_id: str) -> Optional[str]:
    """The breeding operator that produced a firm, from its id.

    Ids look like `gen_11_pareto_bonus_1` or `gen_9_mutant_2`.
    """
    for cls in OPERATOR_CLASSES:
        if re.search(rf"_{cls}(_|$)", company_id):
            return cls
    return None


@dataclass
class CompletenessGate:
    """Decides whether a generation's results may be used for breeding.

    `min_completion_rate` alone is not sufficient, which Generation 11
    demonstrated: 7/10 is a 70% completion rate that looks survivable and was
    in fact the loss of every exploratory firm. `require_all_classes` is the
    check that mattered.
    """

    min_completion_rate: float = 0.8
    require_all_classes: bool = True

    def check(self, launched_ids: List[str],
              completed_ids: List[str]) -> CompletenessReport:
        completed = set(completed_ids)
        missing = sorted(set(launched_ids) - completed)

        launched_classes = {operator_class(c) for c in launched_ids} - {None}
        completed_classes = {operator_class(c) for c in completed_ids} - {None}
        missing_classes = sorted(launched_classes - completed_classes)

        report = CompletenessReport(
            launched=len(launched_ids),
            completed=len(completed),
            missing=missing,
            missing_classes=missing_classes,
        )

        if report.completion_rate < self.min_completion_rate:
            report.ok = False
            report.reason = (
                f"only {report.completed}/{report.launched} firms completed "
                f"({report.completion_rate:.0%} < {self.min_completion_rate:.0%}). "
                f"Missing: {', '.join(missing) or 'unknown'}.")
            return report

        if self.require_all_classes and missing_classes:
            report.ok = False
            report.reason = (
                f"breeding operator class(es) {missing_classes} were wiped out: "
                f"every firm produced by {'them' if len(missing_classes) > 1 else 'it'} "
                f"failed. The survivors are a biased sample -- scoring them "
                f"would report the conservative half of the population as the "
                f"whole generation, which is exactly how Generation 11 would "
                f"have silently destroyed its own diversity. Missing firms: "
                f"{', '.join(missing)}.")
            return report

        return report


# ---------------------------------------------------------------------- #
# The loop
# ---------------------------------------------------------------------- #

@dataclass
class GenerationOutcome:
    """What one generation produced."""

    generation: int
    population_file: str
    completeness: CompletenessReport
    scorecards: List[Dict[str, Any]] = field(default_factory=list)
    best_score: Optional[float] = None
    best_company_id: str = ""
    mean_score: Optional[float] = None
    spent_usd: float = 0.0
    elapsed_s: float = 0.0
    aborted: bool = False
    abort_reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "generation": self.generation,
            "population_file": self.population_file,
            "completeness": self.completeness.to_dict(),
            "best_score": self.best_score,
            "best_company_id": self.best_company_id,
            "mean_score": self.mean_score,
            "spent_usd": round(self.spent_usd, 4),
            "elapsed_s": round(self.elapsed_s, 1),
            "aborted": self.aborted,
            "abort_reason": self.abort_reason,
            "firms": len(self.scorecards),
        }


class GenerationController:
    """breed -> launch -> wait -> harvest -> gate -> repeat.

    The launch and harvest steps are injected rather than hardcoded so the loop
    is testable without a cluster, and so a local runtime can be substituted
    for Kubernetes later without touching the control logic.
    """

    def __init__(
        self,
        task: Task,
        spec_paths: List[str],
        launch: Callable[[int, str, Task], Any],
        harvest: Callable[[int], List[Dict[str, Any]]],
        stopping: Optional[StoppingCriteria] = None,
        gate: Optional[CompletenessGate] = None,
        preflight: Optional[Callable[[], bool]] = None,
        ledger_path: Optional[str] = None,
        repo_root: str = ".",
    ):
        if not spec_paths:
            raise ControllerError("controller needs at least one generation spec")
        self.task = task
        self.spec_paths = spec_paths
        self.launch = launch
        self.harvest = harvest
        self.stopping = stopping or StoppingCriteria()
        self.gate = gate or CompletenessGate()
        self.preflight = preflight
        self.ledger_path = ledger_path
        self.repo_root = repo_root
        self.history: List[GenerationOutcome] = []

    # ------------------------------------------------------------------ #

    def run(self) -> List[GenerationOutcome]:
        started = time.time()
        spent = 0.0

        for spec_path in self.spec_paths:
            decision = self.stopping.evaluate(
                self.history, time.time() - started, spent)
            if decision:
                print(f"[controller] stopping: {decision.reason}")
                break

            # Preflight before *every* generation, not once at the start.
            # Latchkey reaps IAM bindings on its own schedule, so a check that
            # passed an hour ago carries no information about now.
            if self.preflight and not self.preflight():
                raise ControllerError(
                    "preflight failed; refusing to launch. Run "
                    "`python -m hae.cli --mode preflight --repair`.")

            outcome = self._run_one(spec_path)
            self.history.append(outcome)
            spent += outcome.spent_usd
            self._append_ledger(outcome)

            if outcome.aborted:
                print(f"[controller] generation {outcome.generation} aborted: "
                      f"{outcome.abort_reason}")
                print("[controller] not breeding from a biased population. "
                      "Fix the cause and resume.")
                break

        return self.history

    def _run_one(self, spec_path: str) -> GenerationOutcome:
        t0 = time.time()
        spec = GenerationSpec.load(spec_path)
        generation = spec.generation

        print(f"\n[controller] === generation {generation} ===")
        population_file, genomes = breed_generation(spec_path, repo_root=self.repo_root)
        launched_ids = [g.company_id for g in genomes]
        print(f"[controller] bred {len(launched_ids)} firms -> {population_file}")

        self.launch(generation, population_file, self.task)
        scorecards = self.harvest(generation)
        completed_ids = [c.get("company_id", "") for c in scorecards]

        completeness = self.gate.check(launched_ids, completed_ids)
        outcome = GenerationOutcome(
            generation=generation,
            population_file=population_file,
            completeness=completeness,
            scorecards=scorecards,
            elapsed_s=time.time() - t0,
            spent_usd=sum(
                (c.get("opex") or {}).get("estimated_cost_usd", 0.0)
                for c in scorecards),
        )

        if not completeness.ok:
            outcome.aborted = True
            outcome.abort_reason = completeness.reason
            return outcome

        scores = [c.get("fitness_score") for c in scorecards
                  if isinstance(c.get("fitness_score"), (int, float))
                  and not c.get("evaluation_failed")]
        if scores:
            outcome.best_score = round(max(scores), 2)
            outcome.mean_score = round(sum(scores) / len(scores), 2)
            best = max(
                (c for c in scorecards
                 if c.get("fitness_score") == max(scores)),
                key=lambda c: c.get("fitness_score", 0), default={})
            outcome.best_company_id = best.get("company_id", "")
            print(f"[controller] best {outcome.best_score} "
                  f"({outcome.best_company_id}), mean {outcome.mean_score}, "
                  f"${outcome.spent_usd:.2f}")
        return outcome

    def _append_ledger(self, outcome: GenerationOutcome) -> None:
        if not self.ledger_path:
            return
        ledger: List[Dict[str, Any]] = []
        if os.path.exists(self.ledger_path):
            with open(self.ledger_path, "r", encoding="utf-8") as fh:
                ledger = json.load(fh)
        ledger.append(outcome.to_dict())
        os.makedirs(os.path.dirname(self.ledger_path) or ".", exist_ok=True)
        with open(self.ledger_path, "w", encoding="utf-8") as fh:
            json.dump(ledger, fh, indent=2)
