"""Schema definitions for hierarchical virtual organizations, evolutionary lineages, and economic OpEx."""

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

class CorporateAsset(BaseModel):
    """Reusable corporate asset (Code module, Agent Skill, Trait Pack) for IP marketplace."""
    asset_id: str = ""
    asset_type: str = "code_module"  # "code_module", "agent_skill", "trait_pack"
    name: str = ""
    description: str = ""
    content: str = ""
    author_company_id: str = ""
    licensing_fee_usd: float = 0.0

class OpExBreakdown(BaseModel):
    """Financial balance sheet detailing token expenditure, model tiers, and unit economics."""
    flash_input_tokens: int = 0
    flash_output_tokens: int = 0
    pro_input_tokens: int = 0
    pro_output_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0
    budget_usd: float = 0.50
    cost_penalty: float = 0.0
    efficiency_bonus: float = 0.0
    headcount: int = 31
    pro_count: int = 1
    flash_count: int = 30

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        for k, v in kwargs.items():
            setattr(self, k, v)

class AgentGenome(BaseModel):
    """Genome representing an individual agent within a department or executive suite."""
    role: str = ""
    goal: str = ""
    backstory: str = ""
    backstory_traits: List[str] = None
    temperature: float = 0.7
    model_tier: str = "worker"  # "worker" (Flash) vs "executive" (Pro)
    tools_enabled: List[str] = None
    system_instructions: Optional[str] = None

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if not hasattr(self, "backstory_traits") or self.backstory_traits is None:
            self.backstory_traits = kwargs.get("backstory_traits", [])
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
    """Genome representing the entire virtual enterprise (CEO + Departments + Budget)."""
    company_id: str = ""
    generation: int = 0
    parent_ids: List[str] = None
    mutation_history: List[str] = None
    ceo: AgentGenome = None
    departments: List[DepartmentGenome] = None
    executive_deliberation_rules: str = "Dialectic review: challenge assumptions, stress-test trade-offs"
    budget_usd: float = 0.50
    assets: List[CorporateAsset] = None

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
        if not hasattr(self, "budget_usd") or self.budget_usd is None:
            self.budget_usd = kwargs.get("budget_usd", 0.50)
        if not hasattr(self, "assets") or self.assets is None:
            self.assets = kwargs.get("assets", [])

    @property
    def total_agent_count(self) -> int:
        dept_agents = sum(d.total_agents for d in (self.departments or []))
        return 1 + dept_agents

class FitnessScore(BaseModel):
    """Multi-dimensional evaluation scorecard produced by LLM-as-a-Judge and OpEx engine."""
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
    """Complete evaluation record for a company's performance, sandbox gates, and financials."""
    company_id: str = ""
    generation: int = 0
    objective: str = ""
    final_deliverable: str = ""
    departmental_briefs: Dict[str, str] = None
    fitness: FitnessScore = None
    opex: Optional[OpExBreakdown] = None
    timestamp: str = ""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        fit = kwargs.get("fitness")
        if isinstance(fit, dict):
            self.fitness = FitnessScore(**fit)
        elif fit is not None:
            self.fitness = fit
        op = kwargs.get("opex")
        if isinstance(op, dict):
            self.opex = OpExBreakdown(**op)
        elif op is not None:
            self.opex = op
        if not hasattr(self, "departmental_briefs") or self.departmental_briefs is None:
            self.departmental_briefs = kwargs.get("departmental_briefs", {})

class EvaluationMetricSpec(BaseModel):
    """Specification of an endogenous success metric or OKR defined autonomously by an enterprise."""
    metric_id: str = ""
    name: str = ""
    metric_type: str = "deterministic"  # "deterministic", "financial", "rubric", "compliance"
    target_value: str = ""
    evaluation_code_or_prompt: str = ""
    weight: float = 0.20
    description: str = ""
