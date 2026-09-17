"""
Core Genome Schemas for Agentic Organizations.

This module defines the foundational Pydantic models for representing organizational
genomes, including Agents, Departments, and Companies. These schemas are designed
to be immutable by convention for their core attributes, but allow for dynamic
modification through specific mutation and recombination operations.

The `BaseGenome` class provides common functionality, including a robust `copy` method
that ensures deep-copy isolation for complex, nested genome structures.
"""

from __future__ import annotations
import copy
from datetime import datetime
from typing import Dict, List, Optional, Any, Set, TypeVar, Type

from pydantic import BaseModel, Field, PrivateAttr, model_validator, field_validator

# Type variable for BaseGenome and its subclasses
T = TypeVar('T', bound='BaseGenome')

class BaseGenome(BaseModel):
    """
    Base class for all genome components, providing common functionality
    like deep copying and tracking of internal state.
    """
    _created_at: datetime = PrivateAttr(default_factory=datetime.utcnow)
    _last_modified_at: datetime = PrivateAttr(default_factory=datetime.utcnow)

    def __setattr__(self, name: str, value: Any) -> None:
        """Update last_modified_at on attribute changes."""
        if name not in ["_created_at", "_last_modified_at"]:
            self._last_modified_at = datetime.utcnow()
        super().__setattr__(name, value)

    def copy(self: T, *, deep: bool = True, update: Optional[Dict[str, Any]] = None, **kwargs: Any) -> T:
        """
        Creates a deep copy of the Genome object by default.

        This method ensures that all nested mutable objects, including other BaseGenome
        instances and lists/dictionaries containing them, are also deeply copied.
        This is critical for maintaining deep-copy isolation invariants, preventing
        unintended side-effects when modifying a copied genome.

        Args:
            deep: If True (default), performs a deep copy of all nested mutable attributes.
                  If False, performs a shallow copy (only top-level attributes are new instances).
                  Note: For BaseGenome objects, `deep=True` is the recommended and default
                  behavior to ensure proper isolation.
            update: A dictionary of attributes to update in the new copy.
            **kwargs: Additional attributes to update (takes precedence over 'update').

        Returns:
            A new, independent BaseGenome instance.

        Example:
            >>> original_company = CompanyGenome(...)
            >>> new_company = original_company.copy() # This is a deep copy by default
            >>> new_company.departments[0].name = "New Name"
            >>> assert original_company.departments[0].name != "New Name" # Invariant holds
        """
        if not deep:
            # Pydantic's model_copy() performs a shallow copy by default.
            # We explicitly handle mutable collections to ensure a consistent shallow copy.
            new_instance = self.model_copy(update=update, **kwargs)
            for field_name, field_info in self.model_fields.items():
                if field_name not in (update or {}) and field_name not in kwargs:
                    value = getattr(self, field_name)
                    if isinstance(value, list):
                        setattr(new_instance, field_name, list(value))
                    elif isinstance(value, dict):
                        setattr(new_instance, field_name, dict(value))
                    elif isinstance(value, set):
                        setattr(new_instance, field_name, set(value))
            return new_instance

        # Perform a deep copy
        data = self.model_dump()
        
        # Manually deep-copy fields that are BaseGenome instances or lists/dicts of them
        for field_name, field_info in self.model_fields.items():
            if field_name in data: # Ensure field exists in dumped data
                value = getattr(self, field_name)
                if isinstance(value, BaseGenome):
                    data[field_name] = value.copy(deep=True)
                elif isinstance(value, list):
                    # Deep copy list items if they are BaseGenome or other mutable types
                    data[field_name] = [
                        item.copy(deep=True) if isinstance(item, BaseGenome)
                        else copy.deepcopy(item) if isinstance(item, (dict, list, set))
                        else item
                        for item in value
                    ]
                elif isinstance(value, dict):
                    # Deep copy dict values if they are BaseGenome or other mutable types
                    data[field_name] = {
                        k: (v.copy(deep=True) if isinstance(v, BaseGenome)
                            else copy.deepcopy(v) if isinstance(v, (dict, list, set))
                            else v)
                        for k, v in value.items()
                    }
                elif isinstance(value, set):
                    # Deep copy set items if they are BaseGenome or other mutable types
                    data[field_name] = {
                        item.copy(deep=True) if isinstance(item, BaseGenome)
                        else copy.deepcopy(item) if isinstance(item, (dict, list, set))
                        else item
                        for item in value
                    }
                else:
                    # For other types, a deepcopy might still be needed if they are mutable
                    data[field_name] = copy.deepcopy(value)


        if update:
            data.update(update)
        data.update(kwargs) # kwargs take precedence

        # Reconstruct the Pydantic model from the deeply copied data
        # Use model_validate to ensure all validations are run
        return self.__class__.model_validate(data)


class AgentGenome(BaseGenome):
    """
    Defines the genetic blueprint for an individual agent within the organization.
    """
    agent_id: str = Field(..., min_length=1)
    role: str = Field(..., min_length=3)
    goal: str = Field(..., min_length=10)
    backstory: str = Field(..., min_length=20)
    backstory_traits: List[str] = Field(default_factory=list)
    tools_enabled: bool = False
    temperature: float = Field(0.7, ge=0.0, le=1.0) # LLM temperature
    model_tier: str = Field("standard", pattern="^(standard|executive|vision|multimodal)$")

    @field_validator('backstory_traits')
    @classmethod
    def cap_backstory_traits(cls, v: List[str]) -> List[str]:
        return v[:6] # Cap at 6 traits

class DepartmentGenome(BaseGenome):
    """
    Defines the genetic blueprint for a department, including its manager and agents.
    """
    dept_id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=3)
    mandate: str = Field(..., min_length=20)
    manager: Optional[AgentGenome] = None
    agents: List[AgentGenome] = Field(default_factory=list)
    budget_usd: float = Field(0.0, ge=0.0)
    
    @model_validator(mode='after')
    def check_manager_and_agents(self) -> 'DepartmentGenome':
        if self.manager is None and not self.agents:
            raise ValueError("Department must have at least a manager or agents.")
        return self

class CompanyGenome(BaseGenome):
    """
    Defines the genetic blueprint for an entire company, composed of departments.
    """
    company_id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=3)
    mission: str = Field(..., min_length=20)
    ceo: Optional[AgentGenome] = None
    departments: List[DepartmentGenome] = Field(default_factory=list)
    generation: int = Field(0, ge=0)
    parent_ids: List[str] = Field(default_factory=list)
    mutation_history: List[str] = Field(default_factory=list)
    code_overlays: Dict[str, str] = Field(default_factory=dict) # Key: file_path, Value: code_snippet

    @model_validator(mode='after')
    def check_ceo_and_departments(self) -> 'CompanyGenome':
        if self.ceo is None and not self.departments:
            raise ValueError("Company must have at least a CEO or departments.")
        return self

    @field_validator('departments')
    @classmethod
    def ensure_unique_department_ids(cls, v: List[DepartmentGenome]) -> List[DepartmentGenome]:
        dept_ids = [dept.dept_id for dept in v]
        if len(dept_ids) != len(set(dept_ids)):
            raise ValueError("All department IDs within a company must be unique.")
        return v

