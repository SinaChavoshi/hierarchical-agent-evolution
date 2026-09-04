"""Hierarchical Company Runner with Active Tool Sandboxing, Asset Marketplace & OpEx Economics."""

import os
import re
import json
import time
import concurrent.futures
from typing import Dict, Tuple, List, Any, Optional
from .schema import CompanyGenome, DepartmentGenome, AgentGenome, OpExBreakdown
from .llm_factory import call_vertex_gemini_rest
from .sandbox_env import AgentWorkspace

# List token pricing per 1k tokens
COST_TABLE = {
    "gemini-2.5-flash": {"input_per_1k": 0.000075, "output_per_1k": 0.00030},
    "gemini-2.5-pro": {"input_per_1k": 0.00125, "output_per_1k": 0.00500},
}

def parse_tool_action(text: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Parses ReAct tool actions from agent response."""
    action_match = re.search(r"Action:\s*(write_file|read_file|execute_bash|list_files|finish)", text, re.IGNORECASE)
    if not action_match:
        return None
    action = action_match.group(1).lower()

    if action == "write_file":
        path_match = re.search(r"Path:\s*([^\n\r]+)", text)
        path = path_match.group(1).strip(" `\"") if path_match else ""
        code_match = re.search(r"```(?:[a-zA-Z0-9_\-]+)?\s*\n(.*?)```", text, re.DOTALL)
        if code_match:
            content = code_match.group(1)
        else:
            content_match = re.search(r"Content:\s*\n?(.*)", text, re.DOTALL)
            content = content_match.group(1).strip() if content_match else ""
        return action, {"path": path, "content": content}

    elif action == "read_file":
        path_match = re.search(r"Path:\s*([^\n\r]+)", text)
        path = path_match.group(1).strip(" `\"") if path_match else ""
        return action, {"path": path}

    elif action == "execute_bash":
        cmd_match = re.search(r"Command:\s*([^\n\r]+)", text)
        cmd = cmd_match.group(1).strip(" `\"") if cmd_match else ""
        return action, {"command": cmd}

    elif action == "list_files":
        return action, {}

    elif action == "finish":
        return action, {"text": text}

    return None

class HierarchicalCompanyRunner:
    """Executes a virtual enterprise with active sandboxing, asset marketplace, and token OpEx accounting."""

    def __init__(self, genome: CompanyGenome, assets_registry_path: Optional[str] = None):
        self.genome = genome
        self.flash_input_tokens = 0
        self.flash_output_tokens = 0
        self.pro_input_tokens = 0
        self.pro_output_tokens = 0

        # Initialize active execution workspace
        self.workspace = AgentWorkspace(company_id=self.genome.company_id)

        # Load & mount pre-licensed corporate assets from marketplace
        self.licensed_assets_text = ""
        licensed_ids = getattr(self.genome, "licensed_assets", []) or []
        if licensed_ids:
            reg_path = assets_registry_path or (
                "/configs/generation_5_initial_assets.json"
                if os.path.exists("/configs/generation_5_initial_assets.json")
                else "configs/generation_5_initial_assets.json"
            )
            if os.path.exists(reg_path):
                try:
                    with open(reg_path, "r") as f_assets:
                        all_assets = json.load(f_assets)
                    matched = [a for a in all_assets if a.get("asset_id") in licensed_ids]
                    if matched:
                        # Physically mount assets into the workspace
                        mounted_paths = self.workspace.mount_assets(matched)
                        asset_blocks = []
                        for ma in matched:
                            asset_blocks.append(
                                f"### File: {ma.get('name')}\n"
                                f"# PRE-LICENSED CORPORATE ASSET (ID: {ma.get('asset_id')}, Author: {ma.get('author_company_id')})\n"
                                f"{ma.get('content')}"
                            )
                        self.licensed_assets_text = (
                            f"\n\n==================== PRE-LICENSED CORPORATE ASSETS ({len(mounted_paths)} MOUNTED) ====================\n"
                            + "\n\n".join(asset_blocks)
                        )
                except Exception as e:
                    print(f" [WARNING] Error loading licensed assets: {e}")

    def _execute_agent(self, agent: AgentGenome, prompt: str, context: str = "") -> str:
        """Invokes a single agent without tools (fast prompt pass)."""
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
        
        is_pro = (agent.model_tier == "executive")
        model_name = "gemini-2.5-pro" if is_pro else "gemini-2.5-flash"
        
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

    def _execute_agent_with_tools(self, agent: AgentGenome, prompt: str, context: str = "", max_turns: int = 3) -> str:
        """Invokes a technical specialist with an active sandboxed workspace tool-calling loop."""
        traits_section = ""
        if hasattr(agent, "backstory_traits") and agent.backstory_traits:
            traits_section = "\nCore Operational Axioms & Behavioral Traits:\n" + "\n".join([f"- {t}" for t in agent.backstory_traits]) + "\n"

        tool_guide = (
            "\nACTIVE WORKSPACE SANDBOX:\n"
            "You have direct access to an isolated active workspace environment on disk.\n"
            "Current files in your workspace:\n"
            f"{self.workspace.get_file_tree()}\n\n"
            "You can execute actions using this exact syntax:\n"
            "- To write/update a file:\n"
            "  Action: write_file\n"
            "  Path: <relative/path>\n"
            "  ```<language>\n"
            "  <content>\n"
            "  ```\n"
            "- To inspect a file:\n"
            "  Action: read_file\n"
            "  Path: <relative/path>\n"
            "- To run shell commands (tests, syntax checks):\n"
            "  Action: execute_bash\n"
            "  Command: <shell command, e.g. python3 -m pytest tests/ or python3 -m py_compile src/...>\n"
            "- To list workspace files:\n"
            "  Action: list_files\n"
            "- To complete your assignment:\n"
            "  Action: finish\n"
            "  Summary: <your findings, contribution, and verification status>\n\n"
            "Work iteratively: author code, run tests, fix any errors, and finish once verified.\n"
        )

        system_prompt = (
            f"You are the {agent.role}.\n"
            f"Your Core Mission: {agent.goal}\n"
            f"Your Professional Background & Perspective:\n{agent.backstory}\n"
            f"{traits_section}\n"
            f"{tool_guide}"
        )
        if agent.system_instructions:
            system_prompt += f"\nSpecific Behavioral Guardrails: {agent.system_instructions}\n"

        is_pro = (agent.model_tier == "executive")
        model_name = "gemini-2.5-pro" if is_pro else "gemini-2.5-flash"

        conversation_history = f"Directive:\n{prompt}\n\nContext & Inputs:\n{context}\n"
        final_summary = ""

        for turn in range(max_turns):
            in_tokens = int((len(conversation_history) + len(system_prompt)) / 4.0)
            if is_pro:
                self.pro_input_tokens += in_tokens
            else:
                self.flash_input_tokens += in_tokens

            step_resp = call_vertex_gemini_rest(
                prompt=conversation_history,
                model_name=model_name,
                temperature=agent.temperature,
                system_instruction=system_prompt
            )

            out_tokens = int(len(step_resp) / 4.0)
            if is_pro:
                self.pro_output_tokens += out_tokens
            else:
                self.flash_output_tokens += out_tokens

            parsed = parse_tool_action(step_resp)
            if not parsed or parsed[0] == "finish":
                final_summary = step_resp
                break

            action, args = parsed
            observation = ""
            if action == "write_file":
                w_res = self.workspace.write_file(args.get("path", ""), args.get("content", ""))
                observation = f"Observation (write_file): Status={w_res.get('status')}, Bytes={w_res.get('bytes_written', 0)}"
            elif action == "read_file":
                r_res = self.workspace.read_file(args.get("path", ""))
                content = r_res.get("content", "")
                observation = f"Observation (read_file): Status={r_res.get('status')}\n{content[:2000]}"
            elif action == "execute_bash":
                b_res = self.workspace.execute_bash(args.get("command", ""))
                observation = (
                    f"Observation (execute_bash exit {b_res.get('exit_code')}):\n"
                    f"STDOUT: {b_res.get('stdout', '')[:1500]}\n"
                    f"STDERR: {b_res.get('stderr', '')[:1500]}"
                )
            elif action == "list_files":
                tree = self.workspace.get_file_tree()
                observation = f"Observation (list_files):\n{tree}"

            conversation_history += f"\nAssistant Response:\n{step_resp}\n\n{observation}\n"
            final_summary = step_resp

        return final_summary

    def _run_department_pod(self, dept: DepartmentGenome, ceo_directive: str) -> Tuple[str, str]:
        """Runs a department's operational agents and manager synthesis."""
        is_technical = dept.dept_id in ["dept_systems_eng", "dept_qa_redteam"]
        pod_context = ""
        if self.licensed_assets_text:
            pod_context += self.licensed_assets_text + "\n\n"
        if is_technical:
            pod_context += f"Current Workspace Tree:\n{self.workspace.get_file_tree()}\n"

        operational_findings = []
        for agent in dept.agents:
            agent_prompt = (
                f"The Executive Suite has issued the following directive for {dept.name}:\n"
                f"{ceo_directive}\n\n"
                f"As the {agent.role}, execute your domain analysis, generate concrete implementations, "
                f"and surface critical considerations for your Department Manager."
            )
            
            # Use active tools for technical specialists or agents with tools enabled
            has_tools = bool(agent.tools_enabled) or is_technical
            if has_tools:
                findings = self._execute_agent_with_tools(agent, agent_prompt, context=pod_context, max_turns=3)
            else:
                findings = self._execute_agent(agent, agent_prompt, context=pod_context)

            operational_findings.append(f"### Contribution from {agent.role}:\n{findings}")

        combined_findings = "\n\n".join(operational_findings)

        # Department Manager synthesizes findings
        manager_context = combined_findings
        if is_technical:
            manager_context += f"\n\nVerified Workspace Files Authored:\n{self.workspace.get_file_tree()}\n"

        manager_prompt = (
            f"You are the Department Manager for {dept.name}.\n"
            f"Your Team Mandate: {dept.mandate}\n"
            f"CEO Directive Received:\n{ceo_directive}\n\n"
            f"Your team members have produced the following findings and implementation assets:\n"
            f"{manager_context}\n\n"
            f"Synthesize these findings into an authoritative, rigorous Departmental Brief to be submitted "
            f"to the CEO and Executive Council. Follow delegation rule: '{dept.delegation_rules}'."
        )
        dept_brief = self._execute_agent(dept.manager, manager_prompt)
        return dept.dept_id, dept_brief

    def run(self, objective: str) -> Dict[str, Any]:
        """Executes the complete multi-tier organizational workflow on the business objective."""
        start_time = time.time()

        if not hasattr(self.genome.ceo, "model_tier") or not self.genome.ceo.model_tier:
            self.genome.ceo.model_tier = "executive"

        # Step 1: CEO Directive Generation
        licensed_summary = f" (Licensed {len(self.genome.licensed_assets)} pre-existing assets)" if getattr(self.genome, "licensed_assets", None) else ""
        ceo_init_prompt = (
            f"As CEO, review this strategic business challenge:\n\n{objective}\n\n"
            f"Target Token Operating Budget: ${self.genome.budget_usd:.2f} USD{licensed_summary}.\n"
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

        workspace_summary = f"Active Workspace File Tree:\n{self.workspace.get_file_tree()}\n"
        ceo_final_prompt = (
            f"As CEO, synthesize the final unified enterprise deliverable addressing the strategic objective:\n\n"
            f"{objective}\n\n"
            f"Here are the authoritative Departmental Briefs from your 5 Department Managers:\n"
            f"{all_briefs_text}\n\n"
            f"{workspace_summary}\n"
            f"Reconcile trade-offs under executive rule: '{self.genome.executive_deliberation_rules}'.\n"
            f"CRITICAL REQUIREMENT: Explicitly confirm the physical artifacts in the workspace and format all code "
            f"in standard fenced code blocks tagged with '### File: <path>'."
        )
        final_deliverable = self._execute_agent(self.genome.ceo, ceo_final_prompt)

        # Append physical workspace files if deliverable did not include them
        workspace_bundle = self.workspace.export_bundle()
        for path, content in workspace_bundle.items():
            if f"### File: {path}" not in final_deliverable and f"### File: `{path}`" not in final_deliverable:
                final_deliverable += f"\n\n### File: {path}\n```python\n{content}\n```"

        elapsed = round(time.time() - start_time, 2)
        
        # Calculate OpEx & Marketplace Royalties
        flash_cost = (self.flash_input_tokens / 1000.0) * COST_TABLE["gemini-2.5-flash"]["input_per_1k"] + \
                     (self.flash_output_tokens / 1000.0) * COST_TABLE["gemini-2.5-flash"]["output_per_1k"]
        pro_cost = (self.pro_input_tokens / 1000.0) * COST_TABLE["gemini-2.5-pro"]["input_per_1k"] + \
                   (self.pro_output_tokens / 1000.0) * COST_TABLE["gemini-2.5-pro"]["output_per_1k"]
        
        licensing_cost = round(len(getattr(self.genome, "licensed_assets", []) or []) * 0.015, 4)
        royalty_revenue = round(getattr(self.genome, "royalty_revenue_usd", 0.0) or 0.0, 4)
        total_cost = round(flash_cost + pro_cost + licensing_cost - royalty_revenue, 4)
        total_tokens = self.flash_input_tokens + self.flash_output_tokens + self.pro_input_tokens + self.pro_output_tokens

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

        budget = getattr(self.genome, "budget_usd", 0.50) or 0.50
        cost_penalty = 0.0
        efficiency_bonus = 0.0
        if total_cost > budget:
            cost_penalty = round(min(15.0, ((total_cost - budget) / budget) * 10.0), 2)
        else:
            efficiency_bonus = round(min(3.0, ((budget - total_cost) / budget) * 2.5), 2)

        opex = OpExBreakdown(
            flash_input_tokens=self.flash_input_tokens,
            flash_output_tokens=self.flash_output_tokens,
            pro_input_tokens=self.pro_input_tokens,
            pro_output_tokens=self.pro_output_tokens,
            total_tokens=total_tokens,
            estimated_cost_usd=total_cost,
            licensing_cost_usd=licensing_cost,
            royalty_revenue_usd=royalty_revenue,
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
            "workspace_files": workspace_bundle,
            "workspace_tree": self.workspace.get_file_tree(),
            "workspace": self.workspace,
            "opex": opex.model_dump()
        }