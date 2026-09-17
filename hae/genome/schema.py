"""Heritable structure of a virtual organisation, and the records it produces.

Three nested genomes -- agent, department, company -- plus the scorecard and
evaluation record that a tournament writes back out.

On validation
-------------
V1 declared `pydantic>=2.0.0` as a hard dependency and then disabled it: every
model set `extra: "allow"` and overrode `__init__` to coerce nested dicts by
hand, so pydantic's validators never ran. Worse, when pydantic was absent the
module silently substituted a shim that performed no validation at all, and
genomes loaded anyway. A dependency that is declared, unused, and silently
optional is the same failure as a capability module that is never imported.

So there is no schema library here. These are stdlib dataclasses with
validation we wrote, that runs everywhere, and that is covered by tests.
Invalid genomes raise `GenomeValidationError` at construction; they do not load
degraded.

Unrecognised keys are preserved in `extra` rather than dropped, so an archived
genome round-trips, but they are never promoted to attributes -- code cannot
come to depend on a field that is not declared here.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field, fields, is_dataclass
from typing import Any, Dict, List, Mapping, Optional

MODEL_TIERS = ("worker", "executive")

# Deliberate bounds. A temperature above 2.0 is rejected by every provider we
# call, and a mutation that produced one used to fail deep inside an HTTP
# retry loop an hour into a tournament instead of at load time.
MIN_TEMPERATURE = 0.0
MAX_TEMPERATURE = 2.0


class GenomeValidationError(ValueError):
    """Raised when a genome or scorecard violates a structural invariant."""


def _to_plain(value: Any) -> Any:
    if isinstance(value, _Model):
        return value.to_dict()
    if isinstance(value, list):
        return [_to_plain(v) for v in value]
    if isinstance(value, dict):
        return {k: _to_plain(v) for k, v in value.items()}
    return value


@dataclass
class _Model:
    """Shared serialisation for every record in this module."""

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for f in fields(self):
            if f.name == "extra":
                continue
            out[f.name] = _to_plain(getattr(self, f.name))
        extra = getattr(self, "extra", None)
        if extra:
            out.update(_to_plain(extra))
        return out

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    def copy(self) -> "_Model":
        """Deep copy. Genomes are mutated during breeding; never share one."""
        return copy.deepcopy(self)

    @classmethod
    def _split(cls, data: Mapping[str, Any]) -> tuple:
        """Partitions input into declared fields and everything else."""
        known = {f.name for f in fields(cls)} - {"extra"}
        declared = {k: v for k, v in data.items() if k in known}
        extra = {k: v for k, v in data.items() if k not in known}
        return declared, extra

    @classmethod
    def from_dict(cls, data: Optional[Mapping[str, Any]]) -> Any:
        if data is None:
            return None
        if isinstance(data, cls):
            return data
        if not isinstance(data, Mapping):
            raise GenomeValidationError(
                f"{cls.__name__}.from_dict expected a mapping, got {type(data).__name__}"
            )
        declared, extra = cls._split(data)
        obj = cls(**declared)
        obj.extra = dict(extra)
        return obj


@dataclass
class AgentGenome(_Model):
    """An individual agent: its role, disposition, and model tier."""

    role: str = ""
    goal: str = ""
    backstory: str = ""
    backstory_traits: List[str] = field(default_factory=list)
    temperature: float = 0.7
    model_tier: str = "worker"
    # Whether this agent may use the workspace tools (write_file, bash, verify).
    #
    # Declared `List[str]` in V1, but no genome ever named a tool and every
    # consumer wrote `bool(agent.tools_enabled)`. Morphogenesis passed a raw
    # `True` into the list field and the unvalidated schema accepted it. It is
    # a boolean; it is now typed as one.
    tools_enabled: bool = False
    system_instructions: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.role).strip():
            raise GenomeValidationError("AgentGenome.role must be non-empty")
        if self.model_tier not in MODEL_TIERS:
            raise GenomeValidationError(
                f"AgentGenome.model_tier must be one of {MODEL_TIERS}, "
                f"got {self.model_tier!r}"
            )
        try:
            self.temperature = float(self.temperature)
        except (TypeError, ValueError):
            raise GenomeValidationError(
                f"AgentGenome.temperature must be numeric, got {self.temperature!r}"
            )
        if not MIN_TEMPERATURE <= self.temperature <= MAX_TEMPERATURE:
            raise GenomeValidationError(
                f"AgentGenome.temperature must be within "
                f"[{MIN_TEMPERATURE}, {MAX_TEMPERATURE}], got {self.temperature}"
            )
        self.backstory_traits = list(self.backstory_traits or [])
        # Archived genomes store this as a list of tool names. Whether that
        # list was empty is exactly the boolean we want.
        self.tools_enabled = bool(self.tools_enabled)

    @property
    def is_executive(self) -> bool:
        return self.model_tier == "executive"


@dataclass
class DepartmentGenome(_Model):
    """An operational pod: one manager plus its team."""

    dept_id: str = ""
    name: str = ""
    mandate: str = ""
    manager: Optional[AgentGenome] = None
    agents: List[AgentGenome] = field(default_factory=list)
    delegation_rules: str = "Sequential review with collaborative cross-questioning"
    extra: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.dept_id).strip():
            raise GenomeValidationError("DepartmentGenome.dept_id must be non-empty")
        self.manager = AgentGenome.from_dict(self.manager) if isinstance(
            self.manager, Mapping) else self.manager
        if self.manager is None:
            raise GenomeValidationError(
                f"DepartmentGenome {self.dept_id!r} has no manager. A pod without "
                "a manager cannot route work and silently produced nothing in V1."
            )
        self.agents = [
            AgentGenome.from_dict(a) if isinstance(a, Mapping) else a
            for a in (self.agents or [])
        ]

    @property
    def total_agents(self) -> int:
        return 1 + len(self.agents)


@dataclass
class CompanyGenome(_Model):
    """A whole firm: a CEO, its departments, and its operating budget."""

    company_id: str = ""
    generation: int = 0
    parent_ids: List[str] = field(default_factory=list)
    mutation_history: List[str] = field(default_factory=list)
    ceo: Optional[AgentGenome] = None
    departments: List[DepartmentGenome] = field(default_factory=list)
    executive_deliberation_rules: str = (
        "Dialectic review: challenge assumptions, stress-test trade-offs")
    budget_usd: float = 0.50
    code_overlays: Dict[str, str] = field(default_factory=dict)
    extra: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.company_id).strip():
            raise GenomeValidationError("CompanyGenome.company_id must be non-empty")
        self.ceo = AgentGenome.from_dict(self.ceo) if isinstance(
            self.ceo, Mapping) else self.ceo
        if self.ceo is None:
            raise GenomeValidationError(
                f"CompanyGenome {self.company_id!r} has no CEO")
        self.departments = [
            DepartmentGenome.from_dict(d) if isinstance(d, Mapping) else d
            for d in (self.departments or [])
        ]
        if not self.departments:
            raise GenomeValidationError(
                f"CompanyGenome {self.company_id!r} has no departments")
        try:
            self.budget_usd = float(self.budget_usd)
        except (TypeError, ValueError):
            raise GenomeValidationError(
                f"CompanyGenome.budget_usd must be numeric, got {self.budget_usd!r}")
        if self.budget_usd <= 0:
            raise GenomeValidationError(
                f"CompanyGenome.budget_usd must be positive, got {self.budget_usd}")
        self.parent_ids = list(self.parent_ids or [])
        self.mutation_history = list(self.mutation_history or [])
        if self.code_overlays is None:
            self.code_overlays = {}
        elif not isinstance(self.code_overlays, Mapping):
            raise GenomeValidationError(
                f"CompanyGenome.code_overlays must be a mapping, got {type(self.code_overlays).__name__}")
        else:
            self.code_overlays = {str(k): str(v) for k, v in self.code_overlays.items()}

    @property
    def total_agent_count(self) -> int:
        return 1 + sum(d.total_agents for d in self.departments)

    @property
    def dept_ids(self) -> List[str]:
        return [d.dept_id for d in self.departments]


@dataclass
class OpExBreakdown(_Model):
    """What a single firm's run cost, and the score adjustment that implies."""

    flash_input_tokens: int = 0
    flash_output_tokens: int = 0
    pro_input_tokens: int = 0
    pro_output_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0
    budget_usd: float = 0.50
    cost_penalty: float = 0.0
    efficiency_bonus: float = 0.0
    headcount: int = 0
    pro_count: int = 0
    flash_count: int = 0
    # True when every accounted call reported real usage from the provider.
    # False means some figure was inferred, and the cost is a lower bound.
    fully_measured: bool = False
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class FitnessScore(_Model):
    """A firm's scorecard: five judged dimensions and one measured one."""

    strategic_depth: float = 0.0
    technical_feasibility: float = 0.0
    cross_functional_coherence: float = 0.0
    risk_mitigation: float = 0.0
    actionability_and_synthesis: float = 0.0
    # Measured from execution harness gates. The judge never sees it.
    execution_integrity: float = 0.0
    # False when no gate could be evaluated, in which case `fitness_score` is
    # prose-only and `execution_integrity` carries no information.
    execution_evaluable: bool = False
    # True when the judge call or its JSON could not be parsed. Such a firm
    # scores 0.0 and must never be bred forward.
    evaluation_failed: bool = False
    fitness_score: float = 0.0
    qualitative_feedback: str = ""
    identified_bottlenecks: List[str] = field(default_factory=list)
    token_usage: int = 0
    elapsed_seconds: float = 0.0
    extra: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.identified_bottlenecks = list(self.identified_bottlenecks or [])
        for name in ("strategic_depth", "technical_feasibility",
                     "cross_functional_coherence", "risk_mitigation",
                     "actionability_and_synthesis", "execution_integrity",
                     "fitness_score"):
            value = float(getattr(self, name))
            if not 0.0 <= value <= 100.0:
                raise GenomeValidationError(
                    f"FitnessScore.{name} must be within [0, 100], got {value}")
            setattr(self, name, value)


@dataclass
class EvaluationResult(_Model):
    """The complete record of one firm's run: what it made and how it scored."""

    company_id: str = ""
    generation: int = 0
    objective: str = ""
    final_deliverable: str = ""
    departmental_briefs: Dict[str, str] = field(default_factory=dict)
    fitness: Optional[FitnessScore] = None
    opex: Optional[OpExBreakdown] = None
    verification: Optional[Dict[str, Any]] = None
    timestamp: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.fitness, Mapping):
            self.fitness = FitnessScore.from_dict(self.fitness)
        if isinstance(self.opex, Mapping):
            self.opex = OpExBreakdown.from_dict(self.opex)
        self.departmental_briefs = dict(self.departmental_briefs or {})
