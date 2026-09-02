"""Schema definitions for hierarchical virtual organizations and evolutionary lineages."""

from typing import List, Dict, Optional, Any
import copy
import json

try:
    from pydantic import BaseModel, Field
    HAS_PYDANTIC = True
except ImportError:
    HAS_PYDANTIC = False

    class BaseModel:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)
        
        def model_dump(self) -> Dict[str, Any]:
            def _serialize(val):
                if isinstance(val, BaseModel):
                    return val.model_dump()
                elif isinstance(val, list):
                    return [_serialize(x) for x in val]
                elif isinstance(val, dict):
                    return {k: _serialize(v) for k, v in val.items()}
                return val
            return {k: _serialize(v) for k, v in self.__dict__.items()}

        def model_dump_json(self, indent: int = 2) -> str:
            return json.dumps(self.model_dump(), indent=indent)

        def model_copy(self, deep: bool = True):
            return copy.deepcopy(self) if deep else copy.copy(self)

    def Field(default=None, default_factory=None, **kwargs):
        if default_factory is not None:
            return default_factory()
        return default

class AgentGenome(BaseModel):
    """Genome representing an individual agent within a department or executive suite."""
    role: str = ""
    goal: str = ""
    backstory: str = ""
    temperature: float = 0.7
    model_tier: str = "worker"
    tools_enabled: List[str] = None
    system_instructions: Optional[str] = None

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if not hasattr(self, "tools_enabled") or self.tools_enabled is None:
            self.tools_enabled = kwargs.get("tools_enabled", [])
        if not hasattr(self, "temperature") or self.temperature is None:
            self.temperature = kwargs.get("temperature", 0.7)
        if not hasattr(self, "model_tier") or self.model_tier is None:
            self.model_tier = kwargs.get("model_tier", "worker")

class DepartmentGenome(BaseModel):
    """Genome representing an operational department pod (Manager + Team Members)."""
    dept_id: str = ""
    name: str = ""
    mandate: str = ""
    manager: AgentGenome = None
    agents: List[AgentGenome] = None
    delegation_rules: str = "Sequential review with collaborative cross-questioning"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        mgr = kwargs.get("manager")
        if isinstance(mgr, dict):
            self.manager = AgentGenome(**mgr)
        elif mgr is not None:
            self.manager = mgr
            
        raw_agents = kwargs.get("agents", [])
        self.agents = [AgentGenome(**a) if isinstance(a, dict) else a for a in raw_agents]
        if not hasattr(self, "delegation_rules") or self.delegation_rules is None:
            self.delegation_rules = kwargs.get("delegation_rules", "Sequential review")

    @property
    def total_agents(self) -> int:
        return 1 + len(self.agents or [])

class CompanyGenome(BaseModel):
    """Genome representing the entire virtual enterprise (CEO + Departments)."""
    company_id: str = ""
    generation: int = 0
    parent_ids: List[str] = None
    mutation_history: List[str] = None
    ceo: AgentGenome = None
    departments: List[DepartmentGenome] = None
    executive_deliberation_rules: str = "Dialectic review: challenge assumptions, stress-test trade-offs"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        ceo_val = kwargs.get("ceo")
        if isinstance(ceo_val, dict):
            self.ceo = AgentGenome(**ceo_val)
        elif ceo_val is not None:
            self.ceo = ceo_val
            
        raw_depts = kwargs.get("departments", [])
        self.departments = [DepartmentGenome(**d) if isinstance(d, dict) else d for d in raw_depts]
        if not hasattr(self, "parent_ids") or self.parent_ids is None:
            self.parent_ids = kwargs.get("parent_ids", [])
        if not hasattr(self, "mutation_history") or self.mutation_history is None:
            self.mutation_history = kwargs.get("mutation_history", [])

    @property
    def total_agent_count(self) -> int:
        dept_agents = sum(d.total_agents for d in (self.departments or []))
        return 1 + dept_agents

class FitnessScore(BaseModel):
    """Multi-dimensional evaluation scorecard produced by LLM-as-a-Judge."""
    strategic_depth: float = 0.0
    technical_feasibility: float = 0.0
    cross_functional_coherence: float = 0.0
    risk_mitigation: float = 0.0
    actionability_and_synthesis: float = 0.0
    overall_score: float = 0.0
    qualitative_feedback: str = ""
    identified_bottlenecks: List[str] = None
    token_count: int = 0
    elapsed_seconds: float = 0.0

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if not hasattr(self, "identified_bottlenecks") or self.identified_bottlenecks is None:
            self.identified_bottlenecks = kwargs.get("identified_bottlenecks", [])

class EvaluationResult(BaseModel):
    """Complete evaluation record for a company's performance on an objective."""
    company_id: str = ""
    generation: int = 0
    objective: str = ""
    final_deliverable: str = ""
    departmental_briefs: Dict[str, str] = None
    fitness: FitnessScore = None
    timestamp: str = ""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        fit = kwargs.get("fitness")
        if isinstance(fit, dict):
            self.fitness = FitnessScore(**fit)
        elif fit is not None:
            self.fitness = fit
        if not hasattr(self, "departmental_briefs") or self.departmental_briefs is None:
            self.departmental_briefs = kwargs.get("departmental_briefs", {})
