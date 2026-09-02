"""Evolutionary Mutation & Crossover Engine ('Mutate Everything')."""

import copy
import json
import random
import re
from typing import List, Tuple
from .schema import CompanyGenome, DepartmentGenome, AgentGenome, EvaluationResult
from .llm_factory import call_vertex_gemini_rest

MUTATOR_SYSTEM_PROMPT = """You are an Evolutionary Organizational Architect and Meta-Prompt Engineer.
Your task is to analyze the performance, score, and identified bottlenecks of a virtual agent organization,
and MUTATE EVERYTHING about its genome to produce a superior next-generation company.

You can and should mutate:
1. Agent Persona Prompts & Backstories: Inject sharper mental models, specialized technical expertise, and domain biases.
2. Department Topology & Roles: Add new critical specialized roles, remove redundant ones, or adjust headcount.
3. Coordination & Delegation Protocols: Alter how managers synthesize and how the CEO resolves trade-offs.
4. Hyperparameters: Adjust sampling temperatures for each role (e.g. lower for auditing/finance, higher for ideation).

Return ONLY valid JSON representing the mutated CompanyGenome matching the schema:
{
  "ceo": {
    "role": "...",
    "goal": "...",
    "backstory": "...",
    "temperature": <float>,
    "model_tier": "executive",
    "system_instructions": "..."
  },
  "executive_deliberation_rules": "...",
  "departments": [
    {
      "dept_id": "...",
      "name": "...",
      "mandate": "...",
      "delegation_rules": "...",
      "manager": { "role": "...", "goal": "...", "backstory": "...", "temperature": <float>, "model_tier": "executive" },
      "agents": [
        { "role": "...", "goal": "...", "backstory": "...", "temperature": <float>, "model_tier": "worker" }
      ]
    }
  ],
  "mutation_summary": "<One sentence explaining the strategic mutation applied>"
}
"""

class OrganizationalMutator:
    """Applies genetic operators (mutation, crossover, topology adaptation) across generations."""

    def mutate(
        self,
        parent: CompanyGenome,
        eval_result: EvaluationResult,
        new_company_id: str,
        target_generation: int
    ) -> CompanyGenome:
        """Mutates an entire company genome conditioned on evaluation feedback."""
        prompt = f"""PERFORM ORGANIZATIONAL MUTATION:

PARENT COMPANY ID: {parent.company_id}
CURRENT OVERALL FITNESS SCORE: {eval_result.fitness.overall_score}/100
IDENTIFIED BOTTLENECKS:
{json.dumps(eval_result.fitness.identified_bottlenecks, indent=2)}

QUALITATIVE JUDGE FEEDBACK:
{eval_result.fitness.qualitative_feedback}

CURRENT PARENT GENOME STRUCTURE:
{parent.model_dump_json(indent=2)}

Design a mutated, upgraded organization that systematically overcomes these bottlenecks. Mutate backstories, roles, and rules. Return only the JSON."""

        raw_response = call_vertex_gemini_rest(
            prompt=prompt,
            model_name="gemini-2.5-pro",
            temperature=0.7,
            system_instruction=MUTATOR_SYSTEM_PROMPT
        )

        try:
            cleaned = raw_response.strip()
            if "```json" in cleaned:
                cleaned = re.search(r'```json\s*(.*?)\s*```', cleaned, re.DOTALL).group(1)
            elif "```" in cleaned:
                cleaned = re.search(r'```\s*(.*?)\s*```', cleaned, re.DOTALL).group(1)
            mutated_data = json.loads(cleaned)

            # Reconstruct DepartmentGenome list
            departments = []
            for d in mutated_data.get("departments", []):
                manager = AgentGenome(**d["manager"])
                agents = [AgentGenome(**a) for a in d.get("agents", [])]
                departments.append(DepartmentGenome(
                    dept_id=d["dept_id"],
                    name=d["name"],
                    mandate=d["mandate"],
                    manager=manager,
                    agents=agents,
                    delegation_rules=d.get("delegation_rules", "Sequential review")
                ))

            ceo = AgentGenome(**mutated_data["ceo"])
            mutation_note = mutated_data.get("mutation_summary", "Holistic prompt and topology mutation")

            history = list(parent.mutation_history)
            history.append(f"Gen {target_generation} from {parent.company_id}: {mutation_note}")

            return CompanyGenome(
                company_id=new_company_id,
                generation=target_generation,
                parent_ids=[parent.company_id],
                mutation_history=history,
                ceo=ceo,
                departments=departments,
                executive_deliberation_rules=mutated_data.get(
                    "executive_deliberation_rules",
                    parent.executive_deliberation_rules
                )
            )
        except Exception as e:
            # Safe programmatic fallback mutation if LLM JSON format had flaws
            return self._fallback_programmatic_mutation(parent, new_company_id, target_generation, str(e))

    def _fallback_programmatic_mutation(
        self,
        parent: CompanyGenome,
        new_company_id: str,
        target_generation: int,
        err_msg: str
    ) -> CompanyGenome:
        """Applies stochastic local mutations if LLM restructuring encountered parsing issues."""
        child = copy.deepcopy(parent)
        child.company_id = new_company_id
        child.generation = target_generation
        child.parent_ids = [parent.company_id]
        
        # Jitter temperatures
        child.ceo.temperature = max(0.2, min(1.2, child.ceo.temperature + random.uniform(-0.15, 0.15)))
        for dept in child.departments:
            dept.manager.temperature = max(0.2, min(1.2, dept.manager.temperature + random.uniform(-0.1, 0.1)))
            for agent in dept.agents:
                agent.temperature = max(0.2, min(1.2, agent.temperature + random.uniform(-0.15, 0.15)))

        child.mutation_history.append(f"Gen {target_generation}: Stochastic temperature & prompt jitter (fallback: {err_msg[:40]})")
        return child

    def crossover(
        self,
        parent_a: CompanyGenome,
        parent_b: CompanyGenome,
        new_company_id: str,
        target_generation: int
    ) -> CompanyGenome:
        """Recombines the highest performing departments from two parent firms."""
        child_departments = []
        dept_ids_a = {d.dept_id: d for d in parent_a.departments}
        dept_ids_b = {d.dept_id: d for d in parent_b.departments}
        all_ids = list(set(list(dept_ids_a.keys()) + list(dept_ids_b.keys())))

        for dept_id in all_ids:
            if dept_id in dept_ids_a and dept_id in dept_ids_b:
                # Randomly pick from Parent A or Parent B
                chosen = copy.deepcopy(random.choice([dept_ids_a[dept_id], dept_ids_b[dept_id]]))
            elif dept_id in dept_ids_a:
                chosen = copy.deepcopy(dept_ids_a[dept_id])
            else:
                chosen = copy.deepcopy(dept_ids_b[dept_id])
            child_departments.append(chosen)

        # CEO inherited from either Parent A or Parent B with crossover tweaks
        chosen_ceo = copy.deepcopy(random.choice([parent_a.ceo, parent_b.ceo]))

        return CompanyGenome(
            company_id=new_company_id,
            generation=target_generation,
            parent_ids=[parent_a.company_id, parent_b.company_id],
            mutation_history=[f"Gen {target_generation}: Sexual crossover of {parent_a.company_id} and {parent_b.company_id}"],
            ceo=chosen_ceo,
            departments=child_departments,
            executive_deliberation_rules=parent_a.executive_deliberation_rules
        )
