"""Hierarchical Virtual Organization Runner with Dynamic Sizing and OpEx Accounting."""

import time
import concurrent.futures
from typing import Dict, Tuple, List, Any
from .schema import CompanyGenome, DepartmentGenome, AgentGenome, OpExBreakdown
from .llm_factory import call_vertex_gemini_rest

# List token pricing per 1k tokens
COST_TABLE = {
    "gemini-2.5-flash": {"input_per_1k": 0.000075, "output_per_1k": 0.00030},
    "gemini-2.5-pro": {"input_per_1k": 0.00125, "output_per_1k": 0.00500},
}

class HierarchicalCompanyRunner:
    """Executes a virtual enterprise with autonomous sizing and token OpEx accounting."""

    def __init__(self, genome: CompanyGenome):
        self.genome = genome
        self.flash_input_tokens = 0
        self.flash_output_tokens = 0
        self.pro_input_tokens = 0
        self.pro_output_tokens = 0

    def _execute_agent(self, agent: AgentGenome, prompt: str, context: str = "") -> str:
        """Invokes a single agent with persona, operational traits, and token accounting."""
        traits_section = ""
        if hasattr(agent, "backstory_traits") and agent.backstory_traits:
            traits_section = "\nCore Operational Axioms & Behavioral Traits:\n" + "\n".join([f"- {t}" for t in agent.backstory_traits]) + "\n"

        system_prompt = (
            f"You are the {agent.role}.\n"
            f"Your Core Mission: {agent.goal}\n"
            f"Your Professional Background & Perspective:\n{agent.backstory}\n"
            f"{traits_section}"
        )
        if agent.system_instructions:
            system_prompt += f"\nSpecific Behavioral Guardrails: {agent.system_instructions}\n"

        full_prompt = f"{prompt}\n\nContext & Inputs:\n{context}" if context else prompt
        
        # Select model tier
        is_pro = (agent.model_tier == "executive")
        model_name = "gemini-2.5-pro" if is_pro else "gemini-2.5-flash"
        
        # Estimate input tokens (~4 chars per token)
        in_tokens = int((len(full_prompt) + len(system_prompt)) / 4.0)
        if is_pro:
            self.pro_input_tokens += in_tokens
        else:
            self.flash_input_tokens += in_tokens

        resp = call_vertex_gemini_rest(
            prompt=full_prompt,
            model_name=model_name,
            temperature=agent.temperature,
            system_instruction=system_prompt
        )

        out_tokens = int(len(resp) / 4.0)
        if is_pro:
            self.pro_output_tokens += out_tokens
        else:
            self.flash_output_tokens += out_tokens

        return resp

    def _run_department_pod(self, dept: DepartmentGenome, ceo_directive: str) -> Tuple[str, str]:
        """Runs a department's operational agents and manager synthesis."""
        operational_findings = []
        for agent in dept.agents:
            agent_prompt = (
                f"The Executive Suite has issued the following directive for {dept.name}:\n"
                f"{ceo_directive}\n\n"
                f"As the {agent.role}, execute your domain analysis, generate concrete recommendations, "
                f"and surface critical considerations for your Department Manager."
            )
            findings = self._execute_agent(agent, agent_prompt)
            operational_findings.append(f"### Contribution from {agent.role}:\n{findings}")

        combined_findings = "\n\n".join(operational_findings)

        manager_prompt = (
            f"You are the Department Manager for {dept.name}.\n"
            f"Your Team Mandate: {dept.mandate}\n"
            f"CEO Directive Received:\n{ceo_directive}\n\n"
            f"Your team members have produced the following findings:\n"
            f"{combined_findings}\n\n"
            f"Synthesize these findings into an authoritative, rigorous Departmental Brief to be submitted "
            f"to the CEO and Executive Council. Follow delegation rule: '{dept.delegation_rules}'."
        )
        dept_brief = self._execute_agent(dept.manager, manager_prompt)
        return dept.dept_id, dept_brief

    def run(self, objective: str) -> Dict[str, Any]:
        """Executes the complete multi-tier organizational workflow on the business objective."""
        start_time = time.time()

        # Step 0: CEO Sizing & Resource Allocation Review
        # By default, CEO uses executive model, managers and specialists use worker model unless promoted
        if not hasattr(self.genome.ceo, "model_tier") or not self.genome.ceo.model_tier:
            self.genome.ceo.model_tier = "executive"

        # Step 1: CEO Directive Generation
        ceo_init_prompt = (
            f"As CEO, review this strategic business challenge:\n\n{objective}\n\n"
            f"Target Token Operating Budget: ${self.genome.budget_usd:.2f} USD.\n"
            f"Break this mission into targeted directives for your {len(self.genome.departments)} departments:\n"
            + "\n".join([f"- {d.name} ({d.dept_id}): {d.mandate}" for d in self.genome.departments]) +
            "\n\nIssue clear, actionable, and ambitious instructions for each Department Manager."
        )
        ceo_directives = self._execute_agent(self.genome.ceo, ceo_init_prompt)

        # Step 2: Parallel Departmental Pod Execution
        departmental_briefs: Dict[str, str] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(5, len(self.genome.departments))) as executor:
            future_to_dept = {
                executor.submit(self._run_department_pod, dept, ceo_directives): dept
                for dept in self.genome.departments
            }
            for future in concurrent.futures.as_completed(future_to_dept):
                dept_id, brief = future.result()
                departmental_briefs[dept_id] = brief

        # Step 3: Executive Council Reconciliation & Master Strategic Synthesis
        all_briefs_text = "\n\n".join([
            f"==================== DEPARTMENT BRIEF: {dept_id.upper()} ====================\n{brief}"
            for dept_id, brief in departmental_briefs.items()
        ])

        ceo_final_prompt = (
            f"As CEO, synthesize the final unified enterprise deliverable addressing the strategic objective:\n\n"
            f"{objective}\n\n"
            f"Here are the authoritative Departmental Briefs from your 5 Department Managers:\n"
            f"{all_briefs_text}\n\n"
            f"Reconcile trade-offs under executive rule: '{self.genome.executive_deliberation_rules}'.\n"
            f"IMPORTANT: You MUST preserve all concrete implementation artifacts (Python code, pyproject.toml manifests, test suites) "
            f"authored by engineering pods verbatim in fenced code blocks formatted as '### File: <relative_path>'."
        )
        final_deliverable = self._execute_agent(self.genome.ceo, ceo_final_prompt)

        elapsed = round(time.time() - start_time, 2)
        
        # Calculate OpEx
        flash_cost = (self.flash_input_tokens / 1000.0) * COST_TABLE["gemini-2.5-flash"]["input_per_1k"] + \
                     (self.flash_output_tokens / 1000.0) * COST_TABLE["gemini-2.5-flash"]["output_per_1k"]
        pro_cost = (self.pro_input_tokens / 1000.0) * COST_TABLE["gemini-2.5-pro"]["input_per_1k"] + \
                   (self.pro_output_tokens / 1000.0) * COST_TABLE["gemini-2.5-pro"]["output_per_1k"]
        total_cost = round(flash_cost + pro_cost, 4)
        total_tokens = self.flash_input_tokens + self.flash_output_tokens + self.pro_input_tokens + self.pro_output_tokens

        # Count Pro vs Flash agents
        pro_count = 1 if self.genome.ceo.model_tier == "executive" else 0
        flash_count = 1 if self.genome.ceo.model_tier != "executive" else 0
        for d in self.genome.departments:
            if d.manager.model_tier == "executive":
                pro_count += 1
            else:
                flash_count += 1
            for a in d.agents:
                if a.model_tier == "executive":
                    pro_count += 1
                else:
                    flash_count += 1

        # Budget Envelope & Margin
        budget = getattr(self.genome, "budget_usd", 0.50) or 0.50
        cost_penalty = 0.0
        efficiency_bonus = 0.0
        if total_cost > budget:
            # Progressive penalty: docked up to 15 pts if 2.5x over budget
            cost_penalty = round(min(15.0, ((total_cost - budget) / budget) * 10.0), 2)
        else:
            # Efficiency bonus for lean execution under budget
            efficiency_bonus = round(min(3.0, ((budget - total_cost) / budget) * 2.5), 2)

        opex = OpExBreakdown(
            flash_input_tokens=self.flash_input_tokens,
            flash_output_tokens=self.flash_output_tokens,
            pro_input_tokens=self.pro_input_tokens,
            pro_output_tokens=self.pro_output_tokens,
            total_tokens=total_tokens,
            estimated_cost_usd=total_cost,
            budget_usd=budget,
            cost_penalty=cost_penalty,
            efficiency_bonus=efficiency_bonus,
            headcount=self.genome.total_agent_count,
            pro_count=pro_count,
            flash_count=flash_count
        )

        return {
            "final_deliverable": final_deliverable,
            "departmental_briefs": departmental_briefs,
            "elapsed_seconds": elapsed,
            "estimated_tokens": total_tokens,
            "opex": opex.model_dump()
        }
