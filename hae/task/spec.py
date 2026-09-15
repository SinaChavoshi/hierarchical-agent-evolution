"""What a firm is asked to do, and on what terms.

V1 scattered these four concerns across four unrelated mechanisms:

  * the objective was a `--objective` string with a hyperscale-AI-strategy
    default baked into the CLI;
  * how it was checked was hardcoded into `ExecutionHarness`;
  * what it could spend was a genome field consulted only to compute a
    post-hoc score penalty;
  * what tools it could use was `bool(agent.tools_enabled) or is_technical`,
    half a genome field and half a hardcoded department-name heuristic.

None of them could be stated in one place, which meant a "task" was not a thing
the system had a representation of. That is fine while we write every objective
ourselves. It is the blocker for a platform where someone else supplies the
goal, because there is nowhere to put their answer to any of these questions.

`Task` is that representation. It changes no behaviour on its own -- the default
task reproduces exactly what V1 ran -- and it is the seam that later capability
levels plug into.
"""

import json
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, Optional

from hae.task.budget import Budget
from hae.task.verifier import (
    BenchmarkVerifier,
    ExecutionGateVerifier,
    NullVerifier,
    Verifier,
)


class TaskError(ValueError):
    """Raised when a task declaration is incoherent."""


# The action space a firm may use. Today only these exist; the point of naming
# them is that adding one is a declaration rather than an edit to the runner.
#
# `tools_enabled` was a List[str] that no genome ever populated, every consumer
# read as a boolean, and morphogenesis wrote a raw `True` into -- which is what
# killed three of ten firms in Generation 11. Capabilities replace it with
# something that says what it means.
CAPABILITY_WRITE = "workspace.write"     # author files
CAPABILITY_READ = "workspace.read"       # inspect files
CAPABILITY_SHELL = "workspace.shell"     # execute_bash
CAPABILITY_VERIFY = "workspace.verify"   # query the harness mid-run

ALL_CAPABILITIES: FrozenSet[str] = frozenset({
    CAPABILITY_WRITE, CAPABILITY_READ, CAPABILITY_SHELL, CAPABILITY_VERIFY,
})

#: What a technical department got implicitly in V1.
DEFAULT_CAPABILITIES: FrozenSet[str] = ALL_CAPABILITIES


@dataclass
class Task:
    """A goal, a way to check it, a spend limit, and an action space."""

    task_id: str
    objective: str
    verifier: Verifier = field(default_factory=NullVerifier)
    budget: Optional[Budget] = None
    capabilities: FrozenSet[str] = DEFAULT_CAPABILITIES

    # Inner-loop depth. 1 reproduces V1: a firm gets one attempt and its
    # workspace is discarded. Greater than 1 means the firm's own output is fed
    # back to it, which is the difference between evolving an organisation and
    # improving a deliverable.
    max_iterations: int = 1

    # Whether generation N+1 starts from generation N's best workspace rather
    # than an empty one. Off by default because it changes what a fitness
    # trajectory means, and that must be a deliberate choice per experiment.
    carry_artifacts: bool = False

    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.task_id.strip():
            raise TaskError("Task.task_id must be non-empty")
        if not self.objective.strip():
            raise TaskError(f"{self.task_id}: objective must be non-empty")
        if self.max_iterations < 1:
            raise TaskError(
                f"{self.task_id}: max_iterations must be >= 1, "
                f"got {self.max_iterations}")
        unknown = set(self.capabilities) - ALL_CAPABILITIES
        if unknown:
            raise TaskError(
                f"{self.task_id}: unknown capabilities {sorted(unknown)}. "
                f"Known: {sorted(ALL_CAPABILITIES)}")
        self.capabilities = frozenset(self.capabilities)
        if self.carry_artifacts and self.max_iterations < 1:
            raise TaskError(f"{self.task_id}: carry_artifacts needs iterations")

    # ------------------------------------------------------------------ #

    @property
    def is_verified(self) -> bool:
        """Whether this task has ground truth at all.

        Callers that record a fitness number should consult this and say so
        when it is False, rather than publishing a judge-only score that looks
        like a measurement.
        """
        return not isinstance(self.verifier, NullVerifier)

    def allows(self, capability: str) -> bool:
        return capability in self.capabilities

    def describe(self) -> str:
        return (f"task={self.task_id} verifier={self.verifier.describe()} "
                f"budget={'$%.2f' % self.budget.limit_usd if self.budget else 'none'} "
                f"iterations={self.max_iterations} "
                f"carry_artifacts={self.carry_artifacts}")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "objective": self.objective,
            "verifier": self.verifier.describe(),
            "is_verified": self.is_verified,
            "budget": self.budget.to_dict() if self.budget else None,
            "capabilities": sorted(self.capabilities),
            "max_iterations": self.max_iterations,
            "carry_artifacts": self.carry_artifacts,
            "metadata": self.metadata,
        }

    # ------------------------------------------------------------------ #
    # Declaration
    # ------------------------------------------------------------------ #

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Task":
        """Builds a Task from a config fragment.

        Unknown keys are rejected rather than ignored, for the same reason
        `GenerationSpec` rejects them: a silently dropped setting is a run that
        did not do what its config says it did.
        """
        known = {
            "task_id", "objective", "verifier", "benchmark_task", "budget_usd",
            "budget", "max_calls", "reserve_fraction", "capabilities",
            "max_iterations", "carry_artifacts", "metadata",
        }
        unknown = set(data) - known
        if unknown:
            raise TaskError(
                f"task declares unknown keys {sorted(unknown)}. Known: "
                f"{sorted(known)}")

        verifier = _build_verifier(data)

        budget: Optional[Budget] = None
        if "budget" in data and isinstance(data["budget"], dict):
            budget = Budget.from_dict(data["budget"])
        elif data.get("budget_usd") is not None:
            budget = Budget(
                limit_usd=float(data["budget_usd"]),
                max_calls=data.get("max_calls"),
                **({"reserve_fraction": float(data["reserve_fraction"])}
                   if "reserve_fraction" in data else {}))

        caps = data.get("capabilities")
        capabilities = (frozenset(caps) if caps is not None
                        else DEFAULT_CAPABILITIES)

        return cls(
            task_id=data["task_id"],
            objective=data["objective"],
            verifier=verifier,
            budget=budget,
            capabilities=capabilities,
            max_iterations=int(data.get("max_iterations", 1)),
            carry_artifacts=bool(data.get("carry_artifacts", False)),
            metadata=data.get("metadata", {}),
        )

    @classmethod
    def load(cls, path: str) -> "Task":
        with open(path, "r", encoding="utf-8") as fh:
            return cls.from_dict(json.load(fh))


def _build_verifier(data: Dict[str, Any]) -> Verifier:
    """Resolves the `verifier` declaration into an instance."""
    name = data.get("verifier")
    benchmark_task = data.get("benchmark_task")

    if name and benchmark_task and name != "benchmark":
        raise TaskError(
            f"task {data.get('task_id')!r} sets verifier={name!r} and "
            f"benchmark_task={benchmark_task!r}; these conflict.")

    if benchmark_task or name == "benchmark":
        if not benchmark_task:
            raise TaskError("verifier='benchmark' requires `benchmark_task`")
        return BenchmarkVerifier(benchmark_task)

    if name in (None, "execution-gates"):
        # The V1 default, made explicit.
        return ExecutionGateVerifier()
    if name in ("none", "null"):
        return NullVerifier()

    raise TaskError(
        f"unknown verifier {name!r}. Known: execution-gates, benchmark, none.")


#: The V1 objective, preserved so the default task reproduces what we ran.
LEGACY_OBJECTIVE = (
    "Formulate an unassailable 5-year commercial and technical strategy for an "
    "enterprise aiming to establish a next-generation hyperscale AI compute "
    "cloud (100k+ custom accelerators). Address physical power delivery and "
    "cooling limits, high-bandwidth interconnect fabric, enterprise developer "
    "APIs, capital expenditure financing, unit economics, and competitive "
    "counter-moves by incumbent cloud hyperscalers."
)


def legacy_task(objective: Optional[str] = None,
                budget_usd: Optional[float] = None) -> Task:
    """Exactly what V1 ran, expressed as a Task.

    Used by the CLI and the worker so their default path is a Task like any
    other, rather than a second code path that happens to bypass all of this.
    """
    return Task(
        task_id="legacy-prose-objective",
        objective=objective or LEGACY_OBJECTIVE,
        verifier=ExecutionGateVerifier(),
        budget=Budget(limit_usd=budget_usd) if budget_usd else None,
        capabilities=DEFAULT_CAPABILITIES,
        max_iterations=1,
        carry_artifacts=False,
    )
