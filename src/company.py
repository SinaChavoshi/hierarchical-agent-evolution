"""Hierarchical Virtual Organization Runner (CEO -> Department Managers -> Operational Pods)."""

import time
import concurrent.futures
from typing import Dict, Tuple, List, Any
from .schema import CompanyGenome, DepartmentGenome, AgentGenome
from .llm_factory import call_vertex_gemini_rest

class HierarchicalCompanyRunner:
    """Executes a 30-50 agent virtual enterprise using federated hierarchical coordination."""

    def __init__(self, genome: CompanyGenome):
        self.genome = genome

    def _execute_agent(self, agent: AgentGenome, prompt: str, context: str = "") -> str:
        """Invokes a single agent with their persona backstory, operational traits, and system instructions."""
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
        
        model_name = "gemini-2.5-pro" if agent.model_tier == "executive" else "gemini-2.5-flash"
        return call_vertex_gemini_rest(
            prompt=full_prompt,
            model_name=model_name,
            temperature=agent.temperature,
            system_instruction=system_prompt
        )

    def _run_department_pod(self, dept: DepartmentGenome, ceo_directive: str) -> Tuple[str, str]:
        """Runs a department's operational agents and manager synthesis."""
        dept_logs = []
        
        # 1. Operational agents conduct parallel or sequential domain work
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

        # 2. Department Manager synthesizes team findings into a cohesive Departmental Brief
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

        # Step 1: CEO Directive Generation
        ceo_init_prompt = (
            f"As CEO, review this strategic business challenge:\n\n{objective}\n\n"
            f"Break this mission into targeted directives for your 5 departments:\n"
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
            f"You are the CEO presiding over the Executive Council.\n"
            f"Original Strategic Objective:\n{objective}\n\n"
            f"Executive Deliberation Rules:\n{self.genome.executive_deliberation_rules}\n\n"
            f"Departmental Submissions:\n{all_briefs_text}\n\n"
            f"Synthesize these submissions into a comprehensive Master Strategic Deliverable. "
            f"Reconcile trade-offs between departments, challenge weak assumptions, and deliver "
            f"an exhaustive, actionable, and world-class strategic execution plan.\n\n"
            f"CRITICAL DELIVERABLE INVARIANT: If technical specifications, Python code, configuration manifests "
            f"(e.g. pyproject.toml), or test suites were produced by your engineering teams, you MUST include the complete, unabridged "
            f"source code files in your final deliverable formatted strictly as '### File: <relative_path>' followed by fenced code blocks. "
            f"Do not truncate or summarize code into bullet points, as automated sandbox verification requires valid executable files."
        )
        final_deliverable = self._execute_agent(self.genome.ceo, ceo_final_prompt)

        elapsed = time.time() - start_time
        total_tokens_approx = int((len(ceo_directives) + len(all_briefs_text) + len(final_deliverable)) / 4.0)

        return {
            "company_id": self.genome.company_id,
            "generation": self.genome.generation,
            "objective": objective,
            "ceo_directives": ceo_directives,
            "departmental_briefs": departmental_briefs,
            "final_deliverable": final_deliverable,
            "elapsed_seconds": round(elapsed, 2),
            "estimated_tokens": total_tokens_approx,
            "total_agents": self.genome.total_agent_count
        }
