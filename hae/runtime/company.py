"""Hierarchical Company Runner with Active Tool Sandboxing, Asset Marketplace & OpEx Economics."""

import os
import re
import json
import time
import concurrent.futures
from typing import Dict, Tuple, List, Any, Optional
from hae.genome.schema import CompanyGenome, DepartmentGenome, AgentGenome, OpExBreakdown
from hae.infra.llm import call_llm
from hae.runtime.workspace import AgentWorkspace
from hae.evaluation.artifacts import filter_bundle
from hae.evaluation.verification_loop import VERIFY_TOOL_GUIDE, VerificationLoop
from hae.task.budget import Budget

# List token pricing per 1k tokens
COST_TABLE = {
    # Google Gemini
    "gemini-2.5-flash": {"input_per_1k": 0.000075, "output_per_1k": 0.00030},
    "gemini-2.5-pro": {"input_per_1k": 0.00125, "output_per_1k": 0.00500},
    # OpenAI
    "gpt-4o-mini": {"input_per_1k": 0.00015, "output_per_1k": 0.00060},
    "gpt-4o": {"input_per_1k": 0.00250, "output_per_1k": 0.01000},
    # Anthropic
    "claude-3-5-haiku-20241022": {"input_per_1k": 0.00100, "output_per_1k": 0.00500},
    "claude-3-5-sonnet-20241022": {"input_per_1k": 0.00300, "output_per_1k": 0.01500},
    # Local open-source (Ollama / vLLM)
    "ollama": {"input_per_1k": 0.0, "output_per_1k": 0.0},
    "vllm": {"input_per_1k": 0.0, "output_per_1k": 0.0},
}

# Capability keywords that mark a department as one which must be granted real
# filesystem/shell tools. Derived from the department's identity rather than a
# hardcoded id list so that pods invented by MorphogenesisEngine (which cannot be
# enumerated ahead of time) are still able to author artifacts instead of prose.
TECHNICAL_DEPT_KEYWORDS = (
    "engineering", "systems_eng", "qa", "redteam", "red_team", "red team",
    "verification", "formal", "test", "security", "infra", "platform",
    "sre", "devops", "implementation", "acceleration", "compiler",
)

def is_technical_department(dept: DepartmentGenome) -> bool:
    """Returns True if a department should be granted active workspace tools.

    Membership is inferred from the department's id, name, and mandate so that
    dynamically synthesized topologies are classified correctly.
    """
    dept_text = f"{dept.dept_id} {dept.name} {dept.mandate}".lower()
    if any(kw in dept_text for kw in TECHNICAL_DEPT_KEYWORDS):
        return True
    # A department is also technical if its genome explicitly enables tools.
    return any(bool(agent.tools_enabled) for agent in dept.agents)

def parse_tool_action(text: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Parses ReAct tool actions from agent response."""
    action_match = re.search(r"Action:\s*(write_file|read_file|execute_bash|list_files|verify|finish)", text, re.IGNORECASE)
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

    elif action == "verify":
        return action, {}

    elif action == "finish":
        return action, {"text": text}

    return None

class HierarchicalCompanyRunner:
    """Executes one virtual firm: CEO, department pods, workspace, and OpEx."""

    def __init__(self, genome: CompanyGenome,
                 budget: Optional[Budget] = None,
                 seed_files: Optional[Dict[str, str]] = None):
        """
        `budget`, when given, is enforced: calls are refused once the ceiling
        is reached. V1's `genome.budget_usd` was only ever used to compute a
        score penalty after the fact, which is a scoring opinion rather than a
        spend limit.

        `seed_files` pre-populates the workspace, so a firm can continue from
        a previous generation's artifact instead of starting from an empty
        directory. Off unless a Task asks for it: it changes what a fitness
        trajectory means, and that has to be a deliberate per-experiment
        choice rather than something that quietly starts happening.
        """
        self.genome = genome
        self.budget = budget
        self.flash_input_tokens = 0
        self.flash_output_tokens = 0
        self.pro_input_tokens = 0
        self.pro_output_tokens = 0
        # Provenance of the token counts above. Vertex returns usageMetadata on
        # every response; when it is present those numbers are used verbatim.
        # The len(text)/4 estimate is only a fallback, and it is a bad one: it
        # cannot see reasoning tokens at all, so on a thinking model it
        # understates real usage and inflates the efficiency bonus.
        self.measured_calls = 0
        self.estimated_calls = 0
        self.thought_tokens = 0

        # Active execution workspace. Everything a firm is scored on must be
        # written here; prose in the deliverable does not count.
        self.workspace = AgentWorkspace(company_id=self.genome.company_id)

        # Inherited artifacts, recorded so the scorecard can distinguish "this
        # firm wrote 12 files" from "this firm was handed 11 and wrote 1".
        self.seeded_files: Dict[str, str] = dict(seed_files or {})
        for path, content in self.seeded_files.items():
            self.workspace.write_file(path, content)

        # Agents can query the same harness that scores them. One budget for
        # the whole firm, not per pod, so departments have to coordinate
        # rather than each burning attempts independently.
        self.verification_loop = VerificationLoop(self.workspace)

    def _account_tokens(self, is_pro: bool, prompt_text: str, system_text: str,
                        response_text: str, usage: Dict[str, Any],
                        label: str = "", reserved: bool = False) -> None:
        """Adds one call's token usage to the running totals, and bills it.

        Prefers the provider's `usageMetadata`. Falls back to `len(text)/4`
        only when the provider reported nothing, and records which happened so
        the scorecard can say whether its cost figure is measured or guessed.

        Billing happens here because this is the only place that knows what a
        call actually cost. The ceiling itself is enforced before the call, in
        `_may_call`; by the time we get here the money is already spent, so the
        budget can overshoot by at most one call. That residual is bounded and
        reported rather than hidden.
        """
        if usage.get("measured"):
            in_tokens = int(usage.get("prompt_tokens", 0))
            out_tokens = int(usage.get("output_tokens", 0))
            thoughts = int(usage.get("thought_tokens", 0))
            # Reasoning tokens bill at the output rate.
            out_tokens += thoughts
            self.thought_tokens += thoughts
            self.measured_calls += 1
        else:
            in_tokens = int((len(prompt_text) + len(system_text)) / 4.0)
            out_tokens = int(len(response_text) / 4.0)
            self.estimated_calls += 1

        if is_pro:
            self.pro_input_tokens += in_tokens
            self.pro_output_tokens += out_tokens
        else:
            self.flash_input_tokens += in_tokens
            self.flash_output_tokens += out_tokens

        if self.budget is not None:
            model = "gemini-2.5-pro" if is_pro else "gemini-2.5-flash"
            rates = COST_TABLE[model]
            cost = ((in_tokens / 1000.0) * rates["input_per_1k"]
                    + (out_tokens / 1000.0) * rates["output_per_1k"])
            self.budget.charge(cost, label=label or model, reserved=reserved)

    def _may_call(self, label: str, reserved: bool = False) -> bool:
        """Whether this firm may make another billed call.

        Returns False rather than raising. A firm that hits its ceiling should
        return a degraded deliverable built from the work it already did, not
        lose the run -- the department pods run concurrently, and an exception
        escaping one of them would discard the others' completed work too.
        """
        if self.budget is None:
            return True
        if self.budget.can_spend(reserved=reserved):
            return True
        self.budget.refuse(label)
        return False

    @staticmethod
    def _budget_notice(what: str) -> str:
        return (f"[BUDGET EXHAUSTED] {what} was not performed: the firm reached "
                f"its spend ceiling. This is a truncated result, not a finding.")

    def _execute_agent(self, agent: AgentGenome, prompt: str, context: str = "",
                       reserved: bool = False) -> str:
        """Invokes a single agent without tools (fast prompt pass)."""
        if not self._may_call(agent.role, reserved=reserved):
            return self._budget_notice(f"{agent.role}'s contribution")
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
        
        usage: Dict[str, Any] = {}
        resp = call_llm(
            prompt=full_prompt,
            model_name=model_name,
            temperature=agent.temperature,
            system_instruction=system_prompt,
            usage_sink=usage
        )
        self._account_tokens(is_pro, full_prompt, system_prompt, resp, usage,
                             label=agent.role, reserved=reserved)

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
            + VERIFY_TOOL_GUIDE +
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
            # Checked per turn: a tool loop is exactly the shape that runs away.
            if not self._may_call(f"{agent.role} turn {turn + 1}"):
                final_summary = (final_summary
                                 or self._budget_notice(f"{agent.role}'s remaining turns"))
                break
            usage: Dict[str, Any] = {}
            step_resp = call_llm(
                prompt=conversation_history,
                model_name=model_name,
                temperature=agent.temperature,
                system_instruction=system_prompt,
                usage_sink=usage
            )
            self._account_tokens(is_pro, conversation_history, system_prompt,
                                 step_resp, usage, label=agent.role)

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
            elif action == "verify":
                observation = self.verification_loop.verify(
                    turn=turn, agent_role=agent.role)

            conversation_history += f"\nAssistant Response:\n{step_resp}\n\n{observation}\n"
            final_summary = step_resp

        return final_summary

    def _run_department_pod(self, dept: DepartmentGenome, ceo_directive: str, objective: str = "") -> Tuple[str, str]:
        """Runs a department's operational agents and manager synthesis."""
        is_technical = is_technical_department(dept)
        any_explicit_tools = any(
            bool(a.tools_enabled)
            for d in self.genome.departments
            for a in d.agents
        )
        pod_context = ""
        if is_technical:
            pod_context += f"Current Workspace Tree:\n{self.workspace.get_file_tree()}\n"
            if objective:
                pod_context += f"\nFull Technical Specification & Verifier Feedback:\n{objective}\n"

        operational_findings = []
        for agent in dept.agents:
            agent_prompt = (
                f"The Executive Suite has issued the following directive for {dept.name}:\n"
                f"{ceo_directive}\n\n"
                f"As the {agent.role}, execute your domain analysis, generate concrete implementations, "
                f"and surface critical considerations for your Department Manager."
            )
            
            # Use active tools for agents with tools_enabled (or technical dept if no genome-level explicit tool assignment)
            has_tools = bool(agent.tools_enabled) if any_explicit_tools else is_technical
            if has_tools:
                findings = self._execute_agent_with_tools(agent, agent_prompt, context=pod_context, max_turns=4)
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
        inherited_section = ""
        if self.seeded_files:
            inherited_section = (
                f"\nINHERITED WORK PRODUCT ({len(self.seeded_files)} files):\n"
                f"{self.workspace.get_file_tree()}\n"
                "This is the previous generation's deliverable. Your firm's job "
                "is to IMPROVE it, not to restart from scratch. Read what is "
                "there before writing. Rewriting working code from zero is a "
                "regression, not a contribution.\n")

        ceo_init_prompt = (
            f"As CEO, review this strategic business challenge:\n\n{objective}\n\n"
            f"{inherited_section}"
            f"Target Token Operating Budget: ${self.genome.budget_usd:.2f} USD.\n"
            f"Break this mission into targeted directives for your {len(self.genome.departments)} departments:\n"
            + "\n".join([f"- {d.name} ({d.dept_id}): {d.mandate}" for d in self.genome.departments]) +
            "\n\nIssue clear, actionable, and ambitious instructions for each Department Manager."
        )
        ceo_directives = self._execute_agent(self.genome.ceo, ceo_init_prompt)

        # Step 2: Parallel Departmental Pod Execution
        departmental_briefs: Dict[str, str] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(self.genome.departments))) as executor:
            future_to_dept = {
                executor.submit(self._run_department_pod, dept, ceo_directives, objective): dept
                for dept in self.genome.departments
            }
            for future in concurrent.futures.as_completed(future_to_dept):
                dept_id, brief = future.result()
                departmental_briefs[dept_id] = brief

        # Step 2.5: Closed-Loop Sandbox Test Verification & Automated Code Self-Repair
        repair_brief = ""
        test_files = [f for f in self.workspace.list_files() if "test" in f.get("path", "").lower() and f.get("path", "").endswith(".py")]
        if test_files:
            test_run = self.workspace.execute_bash("python3 -m pytest tests/ -q", timeout=20)
            if test_run.get("exit_code") != 0 and "No module named pytest" in test_run.get("stderr", ""):
                test_run = self.workspace.execute_bash("python3 -m unittest discover -s tests/ -p 'test_*.py'", timeout=20)

            if test_run.get("exit_code") != 0:
                # Tests failed! Trigger Closed-Loop Self-Repair with technical specialist
                repair_agent = None
                for dept in self.genome.departments:
                    if dept.dept_id in ["dept_systems_eng", "dept_qa_redteam", "dept_formal_verification"]:
                        for a in dept.agents:
                            role_l = a.role.lower()
                            if "engineer" in role_l or "devops" in role_l or "specialist" in role_l or "qa" in role_l or "verification" in role_l:
                                repair_agent = a
                                break
                    if repair_agent:
                        break
                if not repair_agent:
                    repair_agent = self.genome.departments[0].agents[0] if self.genome.departments[0].agents else self.genome.departments[0].manager

                max_repair_passes = 2
                current_res = test_run
                for r_pass in range(max_repair_passes):
                    repair_prompt = (
                        f"CRITICAL TEST BREAKAGE: Sandbox test execution failed with exit code {current_res.get('exit_code')}!\n\n"
                        f"FAILURE STDOUT:\n{current_res.get('stdout', '')[:1500]}\n\n"
                        f"FAILURE STDERR:\n{current_res.get('stderr', '')[:1500]}\n\n"
                        f"Current Workspace Tree:\n{self.workspace.get_file_tree()}\n\n"
                        f"MANDATE: Analyze the failure traceback. Identify the broken mock, syntax error, missing import, or assertion mismatch. "
                        f"Use 'read_file' to inspect the code, 'write_file' to patch the implementation or test fixtures, "
                        f"and 'execute_bash' ('python3 -m pytest tests/ -q' or unittest) to verify the fix. Once tests pass, use Action: finish."
                    )
                    repair_summary = self._execute_agent_with_tools(repair_agent, repair_prompt, max_turns=3)
                    
                    # Re-test in sandbox
                    current_res = self.workspace.execute_bash("python3 -m pytest tests/ -q", timeout=20)
                    if current_res.get("exit_code") != 0 and "No module named pytest" in current_res.get("stderr", ""):
                        current_res = self.workspace.execute_bash("python3 -m unittest discover -s tests/ -p 'test_*.py'", timeout=20)

                    if current_res.get("exit_code") == 0:
                        repair_brief = f"[Self-Repair PASS]: All sandbox tests verified after repair turn {r_pass + 1}."
                        break
                else:
                    repair_brief = f"[Self-Repair ATTEMPTED]: Completed repair passes. Exit code: {current_res.get('exit_code')}."
            else:
                repair_brief = "[Self-Repair STATUS]: All tests passed on initial execution."

        # Step 3: Executive Council Reconciliation & Master Strategic Synthesis
        all_briefs_text = "\n\n".join([
            f"==================== DEPARTMENT BRIEF: {dept_id.upper()} ====================\n{brief}"
            for dept_id, brief in departmental_briefs.items()
        ])

        workspace_summary = f"Active Workspace File Tree:\n{self.workspace.get_file_tree()}\n"
        if repair_brief:
            workspace_summary += f"\nSandbox Test Status & Self-Repair Diagnostic:\n{repair_brief}\n"
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
        # `reserved=True`: this is the call the reserve exists for. Without it
        # a firm can spend everything on departmental work and then have no
        # budget left to write up what it found, scoring zero for work it did.
        final_deliverable = self._execute_agent(
            self.genome.ceo, ceo_final_prompt, reserved=True)

        # Append physical workspace files if deliverable did not include them.
        # NOTE: the filtered bundle is what gets returned as `workspace_files`
        # further down, so the reported artifact count and the verifier's view
        # of the workspace agree with what is shown to the judge.
        workspace_bundle = filter_bundle(self.workspace.export_bundle())
        for path, content in workspace_bundle.items():
            if len(content) > 50000:
                content = content[:50000] + "\n# [TRUNCATED DUE TO SIZE]"
            if f"### File: {path}" not in final_deliverable and f"### File: `{path}`" not in final_deliverable:
                final_deliverable += f"\n\n### File: {path}\n```python\n{content}\n```"

        elapsed = round(time.time() - start_time, 2)
        
        # Calculate OpEx
        flash_cost = (self.flash_input_tokens / 1000.0) * COST_TABLE["gemini-2.5-flash"]["input_per_1k"] + \
                     (self.flash_output_tokens / 1000.0) * COST_TABLE["gemini-2.5-flash"]["output_per_1k"]
        pro_cost = (self.pro_input_tokens / 1000.0) * COST_TABLE["gemini-2.5-pro"]["input_per_1k"] + \
                   (self.pro_output_tokens / 1000.0) * COST_TABLE["gemini-2.5-pro"]["output_per_1k"]
        
        total_cost = round(flash_cost + pro_cost, 4)
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

        import math
        budget = max(0.05, float(self.genome.budget_usd or 0.50))
        cost_penalty = 0.0
        efficiency_bonus = 0.0
        if total_cost > budget:
            # Smooth, unbounded logarithmic token-efficiency penalty:
            # Never truncates execution, never plateaus, and monotonically rewards lower token usage.
            excess_ratio = (total_cost - budget) / budget
            cost_penalty = round(4.0 * math.log1p(excess_ratio), 2)
        else:
            efficiency_bonus = round(min(5.0, ((budget - total_cost) / budget) * 5.0), 2)

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
            flash_count=flash_count,
            # The efficiency bonus is worth up to +3 net points, so an
            # understated cost is an unearned score. Record whether every
            # call reported real usage so a reader can tell.
            fully_measured=(self.estimated_calls == 0 and self.measured_calls > 0)
        )

        return {
            "final_deliverable": final_deliverable,
            "departmental_briefs": departmental_briefs,
            "elapsed_seconds": elapsed,
            "token_usage": total_tokens,
            "verification_loop": self.verification_loop.summary(),
            "token_accounting": {
                "measured_calls": self.measured_calls,
                "estimated_calls": self.estimated_calls,
                "thought_tokens": self.thought_tokens,
                # True only if every call reported usageMetadata. A partially
                # measured run still has an approximate cost figure.
                "fully_measured": self.estimated_calls == 0 and self.measured_calls > 0,
            },
            "workspace_files": workspace_bundle,
            "workspace_tree": self.workspace.get_file_tree(),
            "workspace_path": str(self.workspace.workspace_dir),
            # Carryover provenance. Without this a firm that inherited eleven
            # files and wrote one reports the same "12 files" as a firm that
            # wrote twelve, and the artifact-count trend becomes meaningless
            # the moment carryover is switched on.
            "inherited_files": sorted(self.seeded_files),
            "authored_files": sorted(
                p for p in workspace_bundle
                if p not in self.seeded_files
                or workspace_bundle[p] != self.seeded_files.get(p)),
            "budget": self.budget.to_dict() if self.budget else None,
            "budget_exhausted": bool(self.budget and self.budget.overrun),
            "opex": opex.to_dict()
        }