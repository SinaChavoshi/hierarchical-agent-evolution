"""Hierarchical Company Runner with Active Tool Sandboxing, Asset Marketplace & OpEx Economics."""

import os
import re
import json
import hashlib
import time
import threading
import concurrent.futures
from typing import Dict, Tuple, List, Any, Optional
from hae.genome.schema import CompanyGenome, DepartmentGenome, AgentGenome, OpExBreakdown, EpistemicPolicyGene
from hae.infra.llm import call_llm
from hae.runtime.workspace import AgentWorkspace
from hae.evaluation.artifacts import filter_bundle
from hae.evaluation.verification_loop import VERIFY_TOOL_GUIDE, VerificationLoop
from hae.task.budget import Budget
from hae.epistemic.audit import build_epistemic_audit
from hae.epistemic.gatekeeper import EvidenceGatekeeper
from hae.epistemic.ledger import EpistemicState, Hypothesis, Question
from hae.epistemic.mcts import EpistemicSearchLoop
from hae.epistemic.moves import (
    MOVE_ASK_QUESTION, MOVE_PROPOSE_HYPOTHESIS, MOVE_RUN_EXPERIMENT, MOVE_SYNTHESIZE,
    V6_HYPOTHESIS_SCHEMA, V6_REPAIR_PLAN_SCHEMA, HypothesisProposal, apply_repair_plan,
    extract_json_object, parse_hypothesis_packet, parse_repair_plan,
)
from hae.epistemic.value import EpistemicValueFunction

# V5 TypeSafe AI Hardware-Enforced JSON Schemas (vLLM xgrammar Constrained Decoding)
V5_CEO_DIRECTIVE_SCHEMA: Dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "SpecContractPacket",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "packet": {"type": "string", "enum": ["SPEC_CONTRACT"]},
                "target_module": {"type": "string", "maxLength": 45},
                "required_symbols": {
                    "type": "array",
                    "items": {"type": "string", "maxLength": 36},
                    "maxItems": 4,
                },
                "core_invariants": {
                    "type": "array",
                    "items": {"type": "string", "maxLength": 75},
                    "maxItems": 4,
                },
                "dept_assignments": {
                    "type": "array",
                    "items": {"type": "string", "maxLength": 65},
                    "maxItems": 5,
                },
            },
            "required": ["packet", "target_module", "required_symbols", "core_invariants", "dept_assignments"],
            "additionalProperties": False,
        },
    },
}

V5_SPECIALIST_PACKET_SCHEMA: Dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "SpecialistAnalysisPacket",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "packet": {"type": "string", "enum": ["DOMAIN_VECTOR"]},
                "role": {"type": "string", "maxLength": 45},
                "verified_invariants": {
                    "type": "array",
                    "items": {"type": "string", "maxLength": 75},
                    "maxItems": 3,
                },
                "edge_case_guards": {
                    "type": "array",
                    "items": {"type": "string", "maxLength": 75},
                    "maxItems": 2,
                },
                "status": {"type": "string", "enum": ["VERIFIED", "READY"]},
            },
            "required": ["packet", "role", "verified_invariants", "edge_case_guards", "status"],
            "additionalProperties": False,
        },
    },
}

V5_DEPT_SYNTHESIS_SCHEMA: Dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "DeptSynthesisPacket",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "packet": {"type": "string", "enum": ["DEPT_SYNTHESIS"]},
                "dept_id": {"type": "string", "maxLength": 35},
                "key_decisions": {
                    "type": "array",
                    "items": {"type": "string", "maxLength": 80},
                    "maxItems": 3,
                },
                "risk_mitigations": {
                    "type": "array",
                    "items": {"type": "string", "maxLength": 80},
                    "maxItems": 2,
                },
                "artifact_verified": {"type": "boolean"},
            },
            "required": ["packet", "dept_id", "key_decisions", "risk_mitigations", "artifact_verified"],
            "additionalProperties": False,
        },
    },
}

V5_CEO_FINAL_SCHEMA: Dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "ExecutiveDeliverablePacket",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "packet": {"type": "string", "enum": ["EXEC_DELIVERABLE"]},
                "strategic_depth_architecture": {"type": "string", "maxLength": 380},
                "technical_feasibility_proof": {"type": "string", "maxLength": 380},
                "cross_functional_coherence_matrix": {"type": "string", "maxLength": 300},
                "risk_mitigation_and_redteam_guards": {"type": "string", "maxLength": 300},
                "actionability_and_verification_milestones": {"type": "string", "maxLength": 300},
            },
            "required": [
                "packet",
                "strategic_depth_architecture",
                "technical_feasibility_proof",
                "cross_functional_coherence_matrix",
                "risk_mitigation_and_redteam_guards",
                "actionability_and_verification_milestones",
            ],
            "additionalProperties": False,
        },
    },
}

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

_REFERENCE_ACCESS_RE = re.compile(
    r"(?:^|[\s'\"=(:])/(?:app|usr|opt|root|home|proc|tmp/hae_benchmark|tmp/hae_grader)\b"
    r"|(?:^|\s)/(?:\s|$|\*)|\.\./|site-packages|dist-packages|importlib\.util\.find_spec|inspect\.getsource|__file__",
)

def _touches_reference_tree(text: str) -> bool:
    """True if an agent tool argument reaches outside its workspace.

    The worker image ships this repository -- including the reference
    implementation of every module being graded -- under /app. The shell sandbox
    removes network access but not filesystem access, so without this guard an
    agent could `cat /app/hae/evaluation/harness.py` or `inspect.getsource` the
    installed package and submit the answer. Best-effort: every command is also
    logged with an [AUDIT] prefix so a bypass is visible in pod logs.
    """
    return bool(text) and bool(_REFERENCE_ACCESS_RE.search(text))

def parse_tool_action(text: str, default_path: str = "hae/genome/morphogenesis.py") -> Optional[Tuple[str, Dict[str, Any]]]:
    """Parses ReAct tool actions, native Qwen3.8 <function=...> XML tool calls, and fenced code modules."""
    # 1. Native Qwen3.8 XML <function=NAME><parameter=K>V</parameter></function>
    fn_m = re.search(r"<function=([a-zA-Z0-9_]+)>\s*(.*?)\s*</function>", text, re.DOTALL | re.IGNORECASE)
    if fn_m:
        action = fn_m.group(1).lower().strip()
        body = fn_m.group(2)
        params: Dict[str, str] = {}
        for pm in re.finditer(r"<parameter=([a-zA-Z0-9_]+)>\s*(.*?)\s*</parameter>", body, re.DOTALL | re.IGNORECASE):
            params[pm.group(1).lower().strip()] = pm.group(2).strip()
        if action == "write_file":
            path = params.get("path") or params.get("file_path") or params.get("filepath") or default_path
            content = params.get("content") or params.get("code") or ""
            if not content:
                cm = re.search(r"```(?:[a-zA-Z0-9_\-]+)?\s*\n(.*?)\n```(?:\s*$|\s*\n)", text, re.DOTALL)
                if cm:
                    content = cm.group(1)
            return "write_file", {"path": path.strip(" `\""), "content": content}
        elif action == "read_file":
            path = params.get("path") or params.get("file_path") or ""
            return "read_file", {"path": path.strip(" `\"")}
        elif action in ("execute_bash", "bash", "run_command"):
            cmd = params.get("command") or params.get("cmd") or ""
            return "execute_bash", {"command": cmd}
        elif action == "list_files":
            return "list_files", {}
        elif action == "verify":
            return "verify", {}
        elif action == "finish":
            return "finish", {"text": params.get("summary") or text}

    # 2. Fenced Python module defining target classes/functions
    for cb in re.findall(r"```(?:python|py)?\s*\n(.*?)\n```(?:\s*$|\s*\n)", text, re.DOTALL):
        if "class MorphogenesisEngine" in cb and "class StructuralCrossoverEngine" in cb:
            return "write_file", {"path": "hae/genome/morphogenesis.py", "content": cb.strip()}
        if "class ExecutionHarness" in cb and "class VerificationReport" in cb:
            return "write_file", {"path": "hae/evaluation/harness.py", "content": cb.strip()}
        if "class VerificationLoop" in cb and "VERIFY_TOOL_GUIDE" in cb:
            return "write_file", {"path": "hae/evaluation/verification_loop.py", "content": cb.strip()}
        if "def filter_bundle" in cb and "def is_malformed_path" in cb:
            return "write_file", {"path": "hae/evaluation/artifacts.py", "content": cb.strip()}

    # 3. Standard ReAct Action: <name>
    action_match = re.search(r"Action:\s*(write_file|read_file|execute_bash|list_files|verify|finish)", text, re.IGNORECASE)
    if not action_match:
        return None
    action = action_match.group(1).lower()

    if action == "write_file":
        path_match = re.search(r"Path:\s*([^\n\r]+)", text)
        path = path_match.group(1).strip(" `\"") if path_match else default_path
        code_match = re.search(r"```(?:[a-zA-Z0-9_\-]+)?\s*\n(.*?)\n```(?:\s*$|\s*\n)", text, re.DOTALL)
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


MODULE_TASK_MAP: Dict[str, str] = {
    "hae/evaluation/artifacts.py": "artifacts",
    "hae/evaluation/harness.py": "harness",
    "hae/evaluation/verification_loop.py": "verification_loop",
    "hae/genome/morphogenesis.py": "morphogenesis",
}

SUITE_TAG_TO_MODULE: Dict[str, str] = {
    "artifacts": "hae/evaluation/artifacts.py",
    "artifact_hygiene": "hae/evaluation/artifacts.py",
    "execution_harness": "hae/evaluation/harness.py",
    "harness": "hae/evaluation/harness.py",
    "verification_loop": "hae/evaluation/verification_loop.py",
    "morphogenesis": "hae/genome/morphogenesis.py",
}


class HierarchicalCompanyRunner:
    """Executes one virtual firm: CEO, department pods, workspace, and OpEx."""

    def __init__(self, genome: CompanyGenome,
                 budget: Optional[Budget] = None,
                 seed_files: Optional[Dict[str, str]] = None):
        self.genome = genome
        self.budget = budget
        self.flash_input_tokens = 0
        self.flash_output_tokens = 0
        self.pro_input_tokens = 0
        self.pro_output_tokens = 0
        self.measured_calls = 0
        self.estimated_calls = 0
        self.thought_tokens = 0

        self.workspace = AgentWorkspace(company_id=self.genome.company_id)

        self.seeded_files: Dict[str, str] = dict(seed_files or {})
        for path, content in self.seeded_files.items():
            self.workspace.write_file(path, content)

        self.verification_loop = VerificationLoop(self.workspace)
        self._code_build_lock = threading.Lock()
        self._code_written_this_run = False
        self._required_modules: List[str] = ["hae/genome/morphogenesis.py"]
        self._pending_modules: List[str] = []
        self._written_modules_this_run: set = set()
        self._locked_passing_modules: set = set()
        self.xgrammar_calls = 0

        # V6: the firm's epistemic ledger. One per firm, persisted across the
        # worker's repair iterations so settled knowledge and ruled-out
        # mechanisms carry forward. None until the first epistemic iteration.
        self.epistemic_state: Optional[EpistemicState] = None
        self.epistemic_searches: List[Dict[str, Any]] = []
        self.epistemic_search_stats: Dict[str, Any] = {}

    def _has_valid_module_artifact(self, mod_path: str) -> bool:
        """Returns True once `mod_path` has been written with valid Python content."""
        try:
            r = self.workspace.read_file(mod_path)
            if r.get("status") not in ("ok", "success"):
                return False
            content = r.get("content", "")
            return len(content) > 300 and ("def " in content or "class " in content)
        except Exception:
            return False

    def _has_valid_morphogenesis_artifact(self) -> bool:
        """Backward-compatible helper checking whether all required modules for this run are written."""
        return len(self._pending_modules) == 0 and all(
            self._has_valid_module_artifact(m) for m in self._required_modules
        )

    def _claim_next_module(self) -> Optional[str]:
        """Thread-safely claims the next unassigned target module for a Departmental Lead Engineer."""
        with self._code_build_lock:
            while self._pending_modules:
                candidate = self._pending_modules.pop(0)
                if candidate not in self._written_modules_this_run:
                    return candidate
            return None

    def _get_focused_module_context(self, target_mod: str, objective: str) -> str:
        """Builds a focused specification and visible context for `target_mod`."""
        from hae.evaluation.benchmark import TASKS, module_specification, REPO_ROOT
        task_key = MODULE_TASK_MAP.get(target_mod)
        parts = [f"Current Workspace Tree:\n{self.workspace.get_file_tree()}\n"]
        if task_key and task_key in TASKS:
            btask = TASKS[task_key]
            abs_mod = os.path.join(REPO_ROOT, target_mod)
            if os.path.exists(abs_mod):
                spec = module_specification(abs_mod)
                ctx_blocks = []
                graded_targets = set(self._required_modules)
                for vis in btask.visible_context:
                    # Prefer the company's own authored version when present.
                    ws_read = self.workspace.read_file(vis)
                    if ws_read.get("status") in ("ok", "success") and len(ws_read.get("content", "")) > 200:
                        ctx_blocks.append(f"### File: {vis} (authored by your firm)\n```python\n{ws_read['content']}\n```")
                        continue
                    abs_vis = os.path.join(REPO_ROOT, vis)
                    if not os.path.exists(abs_vis):
                        continue
                    if vis in graded_targets:
                        # This dependency is itself being graded in this run. Showing
                        # the repo copy would hand the firm the reference answer, so
                        # expose only its public contract.
                        ctx_blocks.append(
                            f"### Dependency contract: {vis} (being implemented in parallel by another department)\n"
                            f"{module_specification(abs_vis)}")
                    else:
                        body = open(abs_vis, "r", encoding="utf-8").read()
                        ctx_blocks.append(f"### File: {vis}\n```python\n{body}\n```")
                parts.append(
                    f"Implement the module `{target_mod}`.\n\n"
                    f"SPECIFICATION\n{spec}\n\n"
                    f"{btask.summary}\n\n"
                    + ("\n\n".join(ctx_blocks) + "\n\n" if ctx_blocks else "")
                )
        else:
            parts.append(f"Full Technical Specification:\n{objective}\n")

        if "GROUND-TRUTH VERIFIER FEEDBACK" in objective:
            fb_idx = objective.find("======================================================================")
            if fb_idx != -1:
                raw_fb = objective[fb_idx:]
                mod_tags = {
                    tag for tag, mod_path in SUITE_TAG_TO_MODULE.items()
                    if mod_path == target_mod
                }
                filtered_fb_lines = []
                for fb_line in raw_fb.splitlines():
                    stripped = fb_line.strip()
                    if stripped.startswith("- [") and "]" in stripped:
                        line_tag = stripped[3:stripped.find("]")]
                        if mod_tags and line_tag not in mod_tags:
                            continue
                    filtered_fb_lines.append(fb_line)
                parts.append("\n".join(filtered_fb_lines))
            existing = self.workspace.read_file(target_mod)
            if existing.get("status") in ("ok", "success") and existing.get("content"):
                parts.append(
                    f"\nCURRENT WORKSPACE IMPLEMENTATION OF `{target_mod}` TO REPAIR:\n"
                    f"```python\n{existing['content']}\n```\n"
                )
        return "\n".join(parts)

    def _account_tokens(self, is_pro: bool, prompt_text: str, system_text: str,
                        response_text: str, usage: Dict[str, Any],
                        label: str = "", reserved: bool = False) -> None:
        """Adds one call's token usage to the running totals, and bills it."""
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
        """Whether this firm may make another billed call."""
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

    def _execute_agent(
        self,
        agent: AgentGenome,
        prompt: str,
        context: str = "",
        reserved: bool = False,
        response_format: Optional[Dict[str, Any]] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        """Invokes a single agent with V5 TypeSafe AI xgrammar schema enforcement."""
        if not self._may_call(agent.role, reserved=reserved):
            return self._budget_notice(f"{agent.role}'s contribution")
        traits_section = ""
        if hasattr(agent, "backstory_traits") and agent.backstory_traits:
            traits_section = "\nCore Operational Axioms & Behavioral Traits:\n" + "\n".join([f"- {t}" for t in agent.backstory_traits]) + "\n"

        v5_enabled = os.environ.get("V5_TYPESAFE_PROTOCOL", "1") == "1"
        active_schema = response_format
        active_max_tokens = max_tokens
        if v5_enabled:
            if active_schema is None:
                active_schema = V5_SPECIALIST_PACKET_SCHEMA
            if active_max_tokens is None:
                active_max_tokens = 180

        system_prompt = (
            f"You are the {agent.role}.\n"
            f"Your Core Mission: {agent.goal}\n"
            f"Your Professional Background & Perspective:\n{agent.backstory}\n"
            f"{traits_section}"
        )
        if agent.system_instructions:
            system_prompt += f"\nSpecific Behavioral Guardrails: {agent.system_instructions}\n"
        if v5_enabled and active_schema is not None:
            system_prompt += (
                "\n[V5 TYPESAFE AI PROTOCOL]: Emit ONLY a compact, strictly valid JSON state packet "
                "matching the enforced hardware schema. Zero prose, zero headers, zero conversational filler.\n"
            )

        full_prompt = f"{prompt}\n\nContext & Inputs:\n{context}" if context else prompt
        
        is_pro = (agent.model_tier == "executive")
        model_name = "gemini-2.5-pro" if is_pro else "gemini-2.5-flash"
        
        usage: Dict[str, Any] = {}
        if v5_enabled and active_schema is not None:
            self.xgrammar_calls += 1
        resp = call_llm(
            prompt=full_prompt,
            model_name=model_name,
            temperature=agent.temperature,
            system_instruction=system_prompt,
            usage_sink=usage,
            response_format=active_schema,
            max_tokens=active_max_tokens,
        )
        self._account_tokens(is_pro, full_prompt, system_prompt, resp, usage,
                             label=agent.role, reserved=reserved)

        return resp

    def _execute_agent_with_tools(
        self,
        agent: AgentGenome,
        prompt: str,
        context: str = "",
        max_turns: int = 3,
        target_path: str = "hae/genome/morphogenesis.py",
    ) -> str:
        """Invokes a Departmental Lead Implementation Engineer with a sandboxed workspace tool loop."""
        traits_section = ""
        if hasattr(agent, "backstory_traits") and agent.backstory_traits:
            traits_section = "\nCore Operational Axioms & Behavioral Traits:\n" + "\n".join([f"- {t}" for t in agent.backstory_traits]) + "\n"

        tool_guide = (
            "\nACTIVE WORKSPACE SANDBOX:\n"
            "You have direct access to an isolated active workspace environment on disk.\n"
            "Current files in your workspace:\n"
            f"{self.workspace.get_file_tree()}\n\n"
            f"CRITICAL EXECUTION RULE: All allowed import modules and the complete specification for `{target_path}` "
            "are already provided inline below. Do NOT call `execute_bash` or `list_files` first. "
            f"Your VERY FIRST response MUST write the complete Python implementation of `{target_path}` "
            "with ZERO conversational preamble using:\n"
            "Action: write_file\n"
            f"Path: {target_path}\n"
            "```python\n"
            f"<complete implementation of {target_path} satisfying 100% of the Public API Contract>\n"
            "```\n"
            + VERIFY_TOOL_GUIDE +
            "- To complete your assignment:\n"
            "  Action: finish\n"
            "  Summary: <concise status>\n"
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
            if not self._may_call(f"{agent.role} turn {turn + 1}"):
                final_summary = (final_summary
                                 or self._budget_notice(f"{agent.role}'s remaining turns"))
                break
            usage: Dict[str, Any] = {}
            step_resp = call_llm(
                prompt=conversation_history,
                model_name=model_name,
                temperature=0.1,
                system_instruction=system_prompt,
                usage_sink=usage,
                max_tokens=8192,
            )
            self._account_tokens(is_pro, conversation_history, system_prompt,
                                 step_resp, usage, label=agent.role)

            parsed = parse_tool_action(step_resp, default_path=target_path)
            if not parsed or parsed[0] == "finish":
                final_summary = step_resp
                break

            action, args = parsed
            observation = ""
            if action == "write_file":
                out_path = (args.get("path") or target_path).lstrip("./")
                if out_path in getattr(self, "_locked_passing_modules", set()) and target_path not in getattr(self, "_locked_passing_modules", set()):
                    out_path = target_path
                w_res = self.workspace.write_file(out_path, args.get("content", ""))
                observation = f"Observation (write_file): Status={w_res.get('status')}, Bytes={w_res.get('bytes_written', 0)}"
                if w_res.get("status") in ("ok", "success") and args.get("bytes_written", w_res.get("bytes_written", 0)) > 300:
                    with self._code_build_lock:
                        self._code_written_this_run = True
                        self._written_modules_this_run.add(out_path.lstrip("./"))
                    final_summary = f'{{"packet":"CODE_ARTIFACT_WRITTEN","path":"{out_path}","bytes":{w_res.get("bytes_written", 0)},"status":"SUCCESS"}}'
                    break
            elif action == "read_file":
                rpath = args.get("path", "")
                if _touches_reference_tree(rpath):
                    print(f"[AUDIT] {self.genome.company_id} {agent.role}: BLOCKED read_file {rpath!r}", flush=True)
                    observation = "Observation (read_file): Status=denied (outside workspace)"
                else:
                    r_res = self.workspace.read_file(rpath)
                    content = r_res.get("content", "")
                    observation = f"Observation (read_file): Status={r_res.get('status')}\n{content[:2000]}"
            elif action == "execute_bash":
                cmd = args.get("command", "")
                if _touches_reference_tree(cmd):
                    print(f"[AUDIT] {self.genome.company_id} {agent.role}: BLOCKED bash {cmd[:200]!r}", flush=True)
                    observation = "Observation (execute_bash): refused -- commands may only operate inside the workspace."
                else:
                    print(f"[AUDIT] {self.genome.company_id} {agent.role}: bash {cmd[:200]!r}", flush=True)
                    b_res = self.workspace.execute_bash(cmd)
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
        """Runs a department's operational agents and manager synthesis under V5 TypeSafe Protocol.

        In multi-module benchmarks (`full_stack_hae`), each department pod claims a distinct
        target module from `self._pending_modules` and assigns its Lead Implementation Engineer
        to synthesize that module concurrently with other departments.
        """
        is_technical = is_technical_department(dept)
        compact_pod_context = f"Current Workspace Tree:\n{self.workspace.get_file_tree()}\n"

        # Each department claims up to 1 target module initially so modules distribute across parallel departments
        dept_claimed_module = self._claim_next_module()
        dept_built_initial = False

        operational_findings = []
        for agent in dept.agents:
            agent_prompt = (
                f"Executive SpecContractPacket for {dept.name}:\n"
                f"{ceo_directive}\n\n"
                f"Role: {agent.role}. Emit your domain verification/implementation state packet."
            )

            target_to_build: Optional[str] = None
            if dept_claimed_module and not dept_built_initial:
                target_to_build = dept_claimed_module
                dept_built_initial = True
            elif is_technical and len(self._pending_modules) > 0:
                target_to_build = self._claim_next_module()

            if target_to_build:
                mod_context = self._get_focused_module_context(target_to_build, objective)
                builder_prompt = (
                    f"Implement the complete Python module `{target_to_build}` "
                    "satisfying 100% of its Public API Contract and unit tests."
                )
                findings = self._execute_agent_with_tools(
                    agent,
                    builder_prompt,
                    context=mod_context,
                    max_turns=3,
                    target_path=target_to_build,
                )
            else:
                findings = self._execute_agent(
                    agent,
                    agent_prompt,
                    context=compact_pod_context,
                    response_format=V5_SPECIALIST_PACKET_SCHEMA,
                    max_tokens=180,
                )

            operational_findings.append(f"[{agent.role}]: {findings}")

        combined_findings = "\n".join(operational_findings)

        # Department Manager synthesizes findings into a compact DeptSynthesisPacket via vLLM xgrammar
        manager_context = combined_findings
        if is_technical or dept_built_initial:
            manager_context += f"\nVerified Workspace Files Authored:\n{self.workspace.get_file_tree()}"

        manager_prompt = (
            f"Department Manager: {dept.name} ({dept.dept_id}).\n"
            f"Mandate: {dept.mandate}\n"
            f"CEO SpecContractPacket:\n{ceo_directive}\n\n"
            f"Specialist Packets:\n{manager_context}\n\n"
            f"Emit the DeptSynthesisPacket for {dept.dept_id}."
        )
        dept_brief = self._execute_agent(
            dept.manager,
            manager_prompt,
            response_format=V5_DEPT_SYNTHESIS_SCHEMA,
            max_tokens=200,
        )
        return dept.dept_id, dept_brief

    def _init_required_modules_for_objective(self, objective: str) -> None:
        """Determines which target modules must be synthesized or repaired in this iteration."""
        all_four = [
            "hae/evaluation/artifacts.py",
            "hae/evaluation/harness.py",
            "hae/evaluation/verification_loop.py",
            "hae/genome/morphogenesis.py",
        ]
        if all(m in objective for m in all_four) or "TARGET MODULE 1/4" in objective:
            self._required_modules = list(all_four)
        else:
            m = re.search(r"Implement the module `([^`]+)`", objective)
            if m:
                mods = [x.strip() for x in m.group(1).split(",") if x.strip()]
                self._required_modules = mods if mods else ["hae/genome/morphogenesis.py"]
            else:
                self._required_modules = ["hae/genome/morphogenesis.py"]

        self._written_modules_this_run = set()
        self._locked_passing_modules = set()
        if "GROUND-TRUTH VERIFIER FEEDBACK" in objective:
            # On repair iterations, only re-queue modules that failed a check or are missing
            failing_mods: List[str] = []
            for tag, mod_path in SUITE_TAG_TO_MODULE.items():
                if f"[{tag}]" in objective and mod_path in self._required_modules and mod_path not in failing_mods:
                    failing_mods.append(mod_path)
            for mod_path in self._required_modules:
                if not self._has_valid_module_artifact(mod_path) and mod_path not in failing_mods:
                    failing_mods.append(mod_path)
            self._pending_modules = failing_mods if failing_mods else list(self._required_modules)
            self._locked_passing_modules = set(self._required_modules) - set(self._pending_modules)
            self._written_modules_this_run.update(self._locked_passing_modules)
        else:
            self._pending_modules = list(self._required_modules)

    def run(self, objective: str) -> Dict[str, Any]:
        """Executes the complete multi-tier organizational workflow on the business objective."""
        start_time = time.time()

        if not hasattr(self.genome.ceo, "model_tier") or not self.genome.ceo.model_tier:
            self.genome.ceo.model_tier = "executive"

        self._init_required_modules_for_objective(objective)

        # Step 1: CEO Directive Generation (V5 TypeSafe AI SpecContractPacket via vLLM xgrammar)
        inherited_section = ""
        if self.seeded_files:
            inherited_section = (
                f"\nINHERITED WORK PRODUCT ({len(self.seeded_files)} files):\n"
                f"{self.workspace.get_file_tree()}\n"
            )

        ceo_init_prompt = (
            f"As CEO, compile the V5 SpecContractPacket for this technical objective:\n\n{objective[:3500]}\n\n"
            f"{inherited_section}"
            f"Target Modules ({len(self._required_modules)}): {', '.join(self._required_modules)}\n"
            f"Departments ({len(self.genome.departments)}): "
            + ", ".join([f"{d.dept_id} ({d.name})" for d in self.genome.departments])
        )
        ceo_directives = self._execute_agent(
            self.genome.ceo,
            ceo_init_prompt,
            response_format=V5_CEO_DIRECTIVE_SCHEMA,
            max_tokens=220,
        )

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

        # Ensure any remaining unwritten module in self._required_modules is completed
        missing_mods = [m for m in self._required_modules if not self._has_valid_module_artifact(m)]
        if missing_mods and self.genome.departments:
            lead_agent = (
                self.genome.departments[0].agents[0]
                if self.genome.departments[0].agents
                else self.genome.departments[0].manager
            )
            for m_path in missing_mods:
                mod_ctx = self._get_focused_module_context(m_path, objective)
                self._execute_agent_with_tools(
                    lead_agent,
                    f"Implement the complete Python module `{m_path}` satisfying 100% of its Public API Contract and unit tests.",
                    context=mod_ctx,
                    max_turns=3,
                    target_path=m_path,
                )

        # Step 2.5: Closed-Loop Sandbox Test Verification & Automated Code Self-Repair (single-module tasks only)
        repair_brief = ""
        test_files = [f for f in self.workspace.list_files() if "test" in f.get("path", "").lower() and f.get("path", "").endswith(".py")]
        if test_files and len(self._required_modules) == 1:
            test_run = self.workspace.execute_bash("python3 -m pytest tests/ -q", timeout=20)
            if test_run.get("exit_code") != 0 and "No module named pytest" in test_run.get("stderr", ""):
                test_run = self.workspace.execute_bash("python3 -m unittest discover -s tests/ -p 'test_*.py'", timeout=20)

            if test_run.get("exit_code") != 0:
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
                        f"MANDATE: Analyze the failure traceback and patch hae/genome/morphogenesis.py."
                    )
                    repair_summary = self._execute_agent_with_tools(repair_agent, repair_prompt, max_turns=3)
                    
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

        # Step 3: Executive Council Reconciliation & Master Strategic Synthesis (V5 ExecutiveDeliverablePacket)
        all_briefs_text = "\n".join([
            f"[{dept_id}]: {brief}"
            for dept_id, brief in departmental_briefs.items()
        ])

        workspace_summary = f"Active Workspace File Tree:\n{self.workspace.get_file_tree()}\n"
        if repair_brief:
            workspace_summary += f"Sandbox Verification: {repair_brief}\n"
        ceo_final_prompt = (
            f"As CEO, compile the final ExecutiveDeliverablePacket synthesizing all departmental packets:\n\n"
            f"DeptSynthesisPackets:\n{all_briefs_text}\n\n"
            f"{workspace_summary}"
        )
        final_deliverable_json = self._execute_agent(
            self.genome.ceo,
            ceo_final_prompt,
            reserved=True,
            response_format=V5_CEO_FINAL_SCHEMA,
            max_tokens=650,
        )
        final_deliverable = (
            f"### V5 TypeSafe AI Executive Synthesis Packet (xgrammar Hardware-Enforced)\n"
            f"```json\n{final_deliverable_json}\n```\n\n"
            f"### Departmental Synthesis Packets\n```json\n{all_briefs_text}\n```"
        )

        # Append physical workspace files so the judge and verifier inspect the full authored implementation.
        final_deliverable, workspace_bundle = self._append_workspace_files(final_deliverable)
        return self._finalize_run_output(start_time, final_deliverable, departmental_briefs, workspace_bundle)

    # ------------------------------------------------------------------ #
    # Shared run epilogue
    # ------------------------------------------------------------------ #

    def _append_workspace_files(self, final_deliverable: str) -> Tuple[str, Dict[str, str]]:
        """Appends every authored file as a `### File:` block. Returns (deliverable, bundle)."""
        workspace_bundle = filter_bundle(self.workspace.export_bundle())
        for path, content in workspace_bundle.items():
            if len(content) > 50000:
                content = content[:50000] + "\n# [TRUNCATED DUE TO SIZE]"
            if f"### File: {path}" not in final_deliverable and f"### File: `{path}`" not in final_deliverable:
                final_deliverable += f"\n\n### File: {path}\n```python\n{content}\n```"
        return final_deliverable, workspace_bundle

    def _finalize_run_output(self, start_time: float, final_deliverable: str,
                             departmental_briefs: Dict[str, str],
                             workspace_bundle: Dict[str, str],
                             extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """OpEx accounting and the standard run-output record (shared by V5 and V6 paths)."""
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

        output: Dict[str, Any] = {
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
        if extra:
            output.update(extra)
        return output

    # ------------------------------------------------------------------ #
    # V6: Epistemic tree-search (replaces the linear repair pass)
    # ------------------------------------------------------------------ #

    @property
    def epistemic_policy(self) -> EpistemicPolicyGene:
        return self.genome.epistemic_policy or EpistemicPolicyGene()

    def _make_gatekeeper(self, policy: Optional[EpistemicPolicyGene] = None) -> EvidenceGatekeeper:
        policy = policy or self.epistemic_policy
        return EvidenceGatekeeper(
            self.workspace,
            timeout_s=policy.experiment_timeout_s,
            max_probe_lines=policy.max_probe_lines,
            graded_modules=self._required_modules,
            module_for_tag=SUITE_TAG_TO_MODULE,
            logger=lambda msg: print(msg, flush=True),
        )

    def _find_agent_for(self, keyword: str, needs_tools: bool) -> AgentGenome:
        """The agent a move kind is routed to, by department keyword.

        Matches the keyword against each department's id, name and mandate
        (the same inference `is_technical_department` uses, so pods invented
        by morphogenesis route correctly). Falls back to the first technical
        department, then to the first department.
        """
        keyword = (keyword or "").lower().strip()
        ordered: List[DepartmentGenome] = []
        for dept in self.genome.departments:
            dept_text = f"{dept.dept_id} {dept.name} {dept.mandate}".lower()
            if keyword and keyword in dept_text:
                ordered.append(dept)
        ordered += [d for d in self.genome.departments if is_technical_department(d) and d not in ordered]
        ordered += [d for d in self.genome.departments if d not in ordered]
        for dept in ordered:
            agents = list(dept.agents)
            if needs_tools:
                preferred = [a for a in agents if a.tools_enabled or "engineer" in a.role.lower()]
                if preferred:
                    return preferred[0]
            if agents:
                return agents[0]
            if dept.manager is not None:
                return dept.manager
        return self.genome.ceo

    def _bind_epistemic_agents(self, policy: EpistemicPolicyGene) -> Dict[str, AgentGenome]:
        return {
            "question": self._find_agent_for(policy.role_bindings.get("question", "qa"), needs_tools=False),
            "hypothesis": self._find_agent_for(policy.role_bindings.get("hypothesis", "engineering"), needs_tools=False),
            "experiment": self._find_agent_for(policy.role_bindings.get("experiment", "verification"), needs_tools=False),
            "synthesis": self._find_agent_for(policy.role_bindings.get("synthesis", "engineering"), needs_tools=True),
        }

    def _module_fingerprint(self, path: str) -> str:
        """Whitespace-normalised hash of a module, used by the synthesis move to detect no-ops.

        Trailing whitespace per line and trailing blank lines are ignored: the
        tool-loop fence parser drops the newline before the closing fence, so a
        re-emission that differs only there is still a no-op and must be
        re-prompted rather than counted as a synthesis.
        """
        r = self.workspace.read_file(path)
        if r.get("status") not in ("ok", "success"):
            return ""
        lines = [ln.rstrip() for ln in str(r.get("content", "")).splitlines()]
        while lines and not lines[-1]:
            lines.pop()
        return hashlib.sha256("\n".join(lines).encode("utf-8", errors="replace")).hexdigest()

    def _epistemic_module_context(self, module: str, objective: str, cap: int = 24000) -> str:
        if not module:
            return f"Full Technical Specification:\n{objective[:cap]}"
        ctx = self._get_focused_module_context(module, objective)
        if len(ctx) > cap:
            ctx = ctx[:cap] + "\n# [CONTEXT TRUNCATED FOR THE PROPOSER; THE SYNTHESISER SEES THE FULL MODULE]"
        return ctx

    def _propose_hypotheses_adapter(self, agent: AgentGenome, objective: str, policy: EpistemicPolicyGene):
        """System 1 adapter: ask a department for k falsifiable hypotheses as a HYPOTHESIS_SET packet."""

        def propose(question: Question, state: EpistemicState, k: int) -> List[HypothesisProposal]:
            k = max(2, int(k))
            prompt = (
                f"EPISTEMIC MOVE: PROPOSE {k} MUTUALLY-EXCLUSIVE HYPOTHESES for question {question.question_id}.\n"
                f"QUESTION: {question.text}\n"
                f"MODULE UNDER INVESTIGATION: `{question.module or 'see specification'}`\n\n"
                "Rules of the ledger:\n"
                "1. Each hypothesis names ONE concrete mechanism in the CURRENT implementation that would cause this failure.\n"
                "2. Hypotheses must be mutually exclusive, and at least one must be a mechanism you consider UNLIKELY.\n"
                "3. `prior` is your honest probability (0.05-0.95) that this mechanism is the actual cause. It is "
                "recorded and later scored for calibration against the evidence, so do not inflate it.\n"
                f"4. `probe_lines` is a standalone Python script of at most {policy.max_probe_lines} lines, given as a "
                "JSON array with ONE SOURCE LINE PER ELEMENT (no embedded newlines). It imports the module FROM THE "
                "WORKSPACE (e.g. `from hae.evaluation.harness import ExecutionHarness`), exercises exactly the "
                "suspected behaviour, and prints a short marker. The Evidence Gatekeeper REFUSES (UNTESTABLE, no "
                "verdict, and it costs your firm) any probe that uses `__file__`, `importlib.util.find_spec`, "
                "`inspect.getsource`, absolute paths (`/app`, `/usr`, `/tmp/...`), `../`, `site-packages`, "
                "`sys.path` mutation, `PYTHONPATH`, the network, or `held_out`. To test whether a module or "
                "name exists, import it inside `try/except ImportError` and print which branch ran.\n"
                "5. `prediction` states what the probe prints / exits with IF THE HYPOTHESIS IS TRUE of the current "
                "code -- not what a fixed implementation would print. The Evidence Gatekeeper runs the probe and "
                "falsifies any hypothesis whose prediction does not hold.\n"
                "6. Never re-propose a mechanism listed under RULED OUT. It has already been falsified by evidence.\n"
                f"Set `question_id` to \"{question.question_id}\"."
            )
            context = (
                f"{state.summary(question.question_id)}\n\n"
                f"{self._epistemic_module_context(question.module, objective)}"
            )
            # 3000 tokens: three hypotheses with 40-line probes run to ~2000
            # tokens; the Gen 16 pilot's 1600 cut off even well-formed packets.
            raw = self._execute_agent(agent, prompt, context=context,
                                      response_format=V6_HYPOTHESIS_SCHEMA, max_tokens=3000)
            proposals = parse_hypothesis_packet(raw, max_items=k)
            if not proposals:
                data = extract_json_object(raw)
                shape = ("no JSON object" if data is None else
                         f"JSON with {len(data.get('hypotheses') or [])} items but none usable")
                print(f"[epistemic] {self.genome.company_id} {agent.role}: no parseable hypotheses "
                      f"({shape}; {len(raw)} chars; head: {raw[:120]!r}; tail: {raw[-80:]!r})", flush=True)
            return proposals

        return propose

    def _synthesize_patch_adapter(self, agent: AgentGenome, objective: str):
        """System 1 adapter: turn a SUPPORTED hypothesis into an *anchored change* to its module.

        Gen 16 pilot finding #5: asked for a complete rewrite, the synthesiser
        re-emitted the current module byte for byte on 7 of 9 moves. The move
        now runs in up to three steps, each cheaper than the next:

          1. REPAIR_PLAN packet (grammar-constrained): which function, which
             exact lines (`old_lines`, verbatim) become which lines
             (`new_lines`). If the anchor matches exactly once, the runner
             applies the edit itself -- no rewrite, no chance of a silent no-op.
          2. Otherwise the tool-loop rewrite, with the plan as its anchor and
             an explicit instruction that the output must differ.
          3. If the module hash still did not move, one re-prompt inside the
             same move: "your previous attempt changed nothing".

        The returned dict says which step wrote (`mode`) and whether a no-op
        had to be re-prompted (`retried`, `noop_recovered`).
        """

        def synthesize(question: Question, hypothesis: Hypothesis, state: EpistemicState) -> Dict[str, Any]:
            target = (question.module or (self._required_modules[0] if self._required_modules else "")).lstrip("./")
            if not target:
                return {"written": False, "path": "", "summary": "no target module for this question"}
            evidence_lines = []
            for eid in hypothesis.evidence_ids:
                ev = state.evidence_by_id(eid)
                if ev is None:
                    continue
                evidence_lines.append(
                    f"- {ev.command} (exit {ev.exit_code}, prediction {'HELD' if ev.matched_prediction else 'FAILED'}): "
                    f"{ev.detail}\n    stdout: {ev.stdout[:400]!r}\n    stderr: {ev.stderr[-300:]!r}")
            finding = (
                f"The Evidence Gatekeeper SUPPORTED this hypothesis about the current implementation of `{target}`:\n"
                f"  CLAIM: {hypothesis.claim}\n"
                f"  MECHANISM: {hypothesis.mechanism}\n"
                f"  PROBE THAT CONFIRMED IT:\n```python\n{hypothesis.probe_code}\n```\n"
                f"  EVIDENCE:\n" + ("\n".join(evidence_lines) or "  (none recorded)") + "\n\n"
                f"Oracle failure being answered: {question.source_failure or question.text}\n"
            )
            context = (
                f"{state.summary(question.question_id)}\n\n"
                f"{self._get_focused_module_context(target, objective)}"
            )
            current = self.workspace.read_file(target)
            source = current.get("content", "") if current.get("status") in ("ok", "success") else ""
            before = self._module_fingerprint(target)
            notes: List[str] = []

            # Step 1: an anchored plan, applied by the runner.
            plan = None
            if source:
                plan_prompt = (
                    f"EPISTEMIC MOVE: SYNTHESIZE -- plan the smallest repair of `{target}` that fixes this mechanism.\n\n"
                    f"{finding}\n"
                    "Reply with a REPAIR_PLAN packet: `function` (where the change lands), `rationale` (one sentence), "
                    "`old_lines` (the exact lines of the CURRENT module to replace -- copied verbatim, including "
                    "indentation, 1-40 consecutive lines, enough to be unique in the file) and `new_lines` (the lines "
                    "that replace them, same indentation). The runner applies the edit mechanically: if `old_lines` is "
                    "not found verbatim exactly once, the plan is discarded. Do not restate the whole module. "
                    "`new_lines` MUST differ from `old_lines`. Mechanisms under RULED OUT were falsified by evidence; "
                    f"do not address them. Set `hypothesis_id` to \"{hypothesis.hypothesis_id}\"."
                )
                try:
                    raw_plan = self._execute_agent(agent, plan_prompt, context=context,
                                                   response_format=V6_REPAIR_PLAN_SCHEMA, max_tokens=3000)
                except Exception as exc:  # the plan is an optimisation; the rewrite path still exists
                    raw_plan = ""
                    notes.append(f"plan call failed: {type(exc).__name__}")
                plan = parse_repair_plan(raw_plan) if raw_plan else None
                if plan is None:
                    notes.append("no usable plan")
                else:
                    new_source, note = apply_repair_plan(source, plan)
                    if new_source is not None:
                        w_res = self.workspace.write_file(target, new_source)
                        after = self._module_fingerprint(target)
                        if w_res.get("status") in ("ok", "success") and after and after != before:
                            with self._code_build_lock:
                                self._code_written_this_run = True
                                self._written_modules_this_run.add(target)
                            summary = f"anchored edit in `{plan.function or '?'}`: {note}; {plan.rationale}"
                            print(f"[epistemic] {self.genome.company_id} {agent.role}: {summary[:220]}", flush=True)
                            return {"written": True, "path": target, "summary": summary[:400],
                                    "mode": "plan", "retried": False, "noop_recovered": False}
                        notes.append(f"plan write refused: {str(w_res.get('status'))[:60]} "
                                     f"{str(w_res.get('message') or w_res.get('error') or '')[:120]}".strip())
                    else:
                        notes.append(f"plan not applicable: {note}")

            # Step 2: the rewrite, anchored by the plan when there is one -- or,
            # when the module does not exist at all (a first pass that never
            # wrote it; the oracle says `suite failed to import`), its creation.
            # Gen 16 cohort finding #7: the search could diagnose a missing
            # module but every synthesis landed on an existing one.
            missing = not source
            anchor = ""
            if plan is not None:
                anchor = (f"\nYour own repair plan, which could not be applied mechanically "
                          f"({notes[-1] if notes else 'anchor mismatch'}):\n"
                          f"  function: {plan.function}\n  rationale: {plan.rationale}\n"
                          f"  replace:\n```python\n{plan.old_text}\n```\n  with:\n```python\n{plan.new_text}\n```\n"
                          "Apply exactly that change in the full module you emit.\n")
            if missing:
                notes.append("target module absent: authoring it")
                rewrite_prompt = (
                    f"EPISTEMIC MOVE: SYNTHESIZE `{target}`, which does NOT exist in the workspace.\n\n{finding}\n"
                    f"The held-out suite cannot import `{target}` because the file is missing, so there is nothing to "
                    "repair: create it. Emit `Action: write_file` with the COMPLETE module implementing this file's "
                    "section of the specification in the context, consistent with the sibling modules shown there "
                    "(import paths, class and function names, return types). Do not modify any other file."
                )
            else:
                rewrite_prompt = (
                    f"EPISTEMIC MOVE: SYNTHESIZE a repair of `{target}`.\n\n{finding}{anchor}\n"
                    "Emit `Action: write_file` with the COMPLETE module in which ONLY the code that implements this "
                    "mechanism is changed; every currently passing behaviour must be preserved. The current module is "
                    "shown in the context: your output MUST differ from it -- re-emitting it unchanged is a failed move. "
                    "Do not speculate about other causes: mechanisms under RULED OUT were falsified by evidence."
                )
            mode = "author" if missing else "rewrite"
            summary = self._execute_agent_with_tools(agent, rewrite_prompt, context=context, max_turns=3,
                                                     target_path=target)
            after = self._module_fingerprint(target)
            if after and after != before:
                return {"written": True, "path": target, "summary": f"{mode}: {str(summary)[:300]}",
                        "mode": mode, "retried": False, "noop_recovered": False, "notes": notes}

            # Step 3: the no-op retry. Tell the synthesiser what just happened.
            what_happened = (
                f"Your previous attempt did not create `{target}`: no `Action: write_file` for that path reached the "
                "workspace, so the suite still cannot import it."
                if missing else
                "Your previous attempt changed nothing: the module you wrote was byte-for-byte identical to the "
                "current one (same SHA-256), so the oracle failure is still unanswered."
            )
            retry_prompt = (
                f"EPISTEMIC MOVE: SYNTHESIZE {'`' + target + '`' if missing else 'a repair of `' + target + '`'} "
                f"-- SECOND ATTEMPT.\n\n{what_happened}\n\n{finding}{anchor}\n"
                + ("Emit `Action: write_file` with `Path: " + target + "` and the complete module. "
                   if missing else
                   "Name, to yourself, the single function where this mechanism lives, then emit `Action: write_file` "
                   "with the complete module in which that function is actually changed. ")
                + "Output identical to the current workspace will be recorded as a failed synthesis."
            )
            summary2 = self._execute_agent_with_tools(agent, retry_prompt, context=context, max_turns=3,
                                                      target_path=target)
            after2 = self._module_fingerprint(target)
            if after2 and after2 != before:
                return {"written": True, "path": target, "mode": mode, "retried": True, "noop_recovered": True,
                        "summary": f"no-op (re-prompted, then changed): {str(summary2)[:260]}", "notes": notes}
            return {"written": False, "path": target, "mode": "none", "retried": True, "noop_recovered": False,
                    "summary": (f"no-op (re-prompted, gave up): synthesiser "
                                f"{'did not create the file' if missing else 're-emitted identical content'} twice; "
                                f"{'; '.join(notes)[:160]}"), "notes": notes}

        return synthesize

    def reconcile_epistemic_state(self, failures: List[str], iteration: int) -> Dict[str, Any]:
        """Lets the oracle's latest verdict certify / reopen questions. Creates the ledger if needed."""
        if self.epistemic_state is None:
            self.epistemic_state = EpistemicState(self.genome.company_id)
        gatekeeper = self._make_gatekeeper()
        default_module = self._required_modules[0] if len(self._required_modules) == 1 else ""
        rec = gatekeeper.reconcile_with_oracle(self.epistemic_state, failures, iteration=iteration,
                                               default_module=default_module)
        print(f"[epistemic] {self.genome.company_id} iteration {iteration}: oracle reconciliation -> "
              f"certified={len(rec['certified'])} reopened={len(rec['reopened'])} seeded={len(rec['seeded'])} "
              f"open={len(self.epistemic_state.open_questions())} "
              f"uncertainty={self.epistemic_state.total_uncertainty():.2f}", flush=True)
        return rec

    def epistemic_audit(self, token_usage: int = 0) -> Optional[Dict[str, Any]]:
        if self.epistemic_state is None:
            return None
        return build_epistemic_audit(self.epistemic_state, self.epistemic_search_stats,
                                     budget_moves=int(self.epistemic_search_stats.get("budget_moves", 0)),
                                     token_usage=token_usage)

    def _epistemic_deliverable(self, state: EpistemicState, search: Dict[str, Any], iteration: int) -> str:
        counts = state.counts()
        ledger_view = {
            "iteration": iteration,
            "counts": counts,
            "uncertainty": {"initial": state.initial_uncertainty(), "remaining": state.total_uncertainty()},
            "settled_knowledge": [f.statement for f in state.settled_knowledge][-12:],
            "ruled_out": [b.claim for b in state.ruled_out][-12:],
            "open_questions": [
                {"id": q.question_id, "module": q.module, "uncertainty": round(q.uncertainty, 3),
                 "status": q.status, "text": q.text[:200]}
                for q in state.open_questions()][:12],
            "ledger_hash": state.state_hash(),
        }
        return (
            f"### V6 Epistemic Ledger (iteration {iteration}; beliefs written only by the Evidence Gatekeeper)\n"
            f"```json\n{json.dumps(ledger_view, indent=1)}\n```\n\n"
            f"### Epistemic Search ({search.get('moves_used', 0)} moves, stop={search.get('stop_reason', '')})\n"
            f"```json\n{json.dumps(search.get('stats', {}), indent=1)}\n```"
        )

    def _merge_search_stats(self, stats: Dict[str, Any]) -> None:
        merged = self.epistemic_search_stats
        for key, value in stats.items():
            if isinstance(value, bool):
                merged[key] = value
            elif isinstance(value, (int, float)):
                merged[key] = merged.get(key, 0) + value
            else:
                merged[key] = value
        merged["iterations"] = merged.get("iterations", 0) + 1

    def run_epistemic_search(self, objective: str, failures: List[str],
                             iteration: int = 2, max_iterations: int = 1) -> Dict[str, Any]:
        """One repair iteration as an epistemic tree-search instead of a linear rewrite.

        The oracle's failures seed or update the ledger; System 1 agents
        propose hypotheses with probes; the gatekeeper runs them; a patch is
        synthesised only for a hypothesis that survived. Returns the same
        record shape as `run()` plus `epistemic_ledger` and `epistemic_search`.
        """
        start_time = time.time()
        policy = self.epistemic_policy
        if not hasattr(self.genome.ceo, "model_tier") or not self.genome.ceo.model_tier:
            self.genome.ceo.model_tier = "executive"
        self._init_required_modules_for_objective(objective)
        self.reconcile_epistemic_state(list(failures or []), iteration=iteration)
        state = self.epistemic_state
        assert state is not None

        agents = self._bind_epistemic_agents(policy)
        print(f"[epistemic] {self.genome.company_id}: bindings hypothesis={agents['hypothesis'].role!r} "
              f"synthesis={agents['synthesis'].role!r} budget={policy.search_budget_moves} moves "
              f"k={policy.branching_k} c_puct={policy.c_puct} low_prior_quota={policy.low_prior_quota}", flush=True)

        gatekeeper = self._make_gatekeeper(policy)
        seed_material = f"{self.genome.company_id}:{iteration}".encode("utf-8")
        loop = EpistemicSearchLoop(
            state, gatekeeper, EpistemicValueFunction(policy.value_alpha), policy,
            propose_hypotheses=self._propose_hypotheses_adapter(agents["hypothesis"], objective, policy),
            synthesize_patch=self._synthesize_patch_adapter(agents["synthesis"], objective),
            may_continue=lambda: self._may_call("epistemic search move"),
            agent_roles={MOVE_PROPOSE_HYPOTHESIS: agents["hypothesis"].role,
                         MOVE_RUN_EXPERIMENT: "EvidenceGatekeeper",
                         MOVE_SYNTHESIZE: agents["synthesis"].role,
                         MOVE_ASK_QUESTION: agents["question"].role},
            rng_seed=int(hashlib.sha256(seed_material).hexdigest()[:8], 16),
            logger=lambda msg: print(
                f"[epistemic] {self.genome.company_id} "
                f"{msg[len('[epistemic] '):] if msg.startswith('[epistemic] ') else msg}", flush=True),
        )
        result = loop.run(policy.search_budget_moves)
        search = result.to_dict()
        search["iteration"] = iteration
        self.epistemic_searches.append(search)
        self._merge_search_stats(result.stats)
        print(f"[epistemic] {self.genome.company_id} iteration {iteration}: {result.moves_used} moves, "
              f"dU={result.delta_u_total:.3f}, stop={result.stop_reason}, "
              f"synthesized={result.synthesized_paths}, counts={state.counts()}", flush=True)

        final_deliverable = self._epistemic_deliverable(state, search, iteration)
        departmental_briefs = {
            "epistemic_search": json.dumps({"iteration": iteration, "moves_used": result.moves_used,
                                            "delta_u_total": result.delta_u_total,
                                            "stop_reason": result.stop_reason,
                                            "synthesized_paths": result.synthesized_paths}),
        }
        final_deliverable, workspace_bundle = self._append_workspace_files(final_deliverable)
        return self._finalize_run_output(
            start_time, final_deliverable, departmental_briefs, workspace_bundle,
            extra={"epistemic_ledger": state.to_dict(), "epistemic_search": search,
                   "epistemic_iteration": iteration})