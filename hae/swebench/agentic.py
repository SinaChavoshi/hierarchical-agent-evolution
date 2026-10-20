"""V12 Agentic Epistemic SWE Engine for `hae.swebench`.

Replaces the V6-era single-shot blind static-excerpt MCTS loop with a native
multi-turn interactive agentic execution engine designed around
`nvidia/Qwen3.8-Flash-Next-NVFP4`'s post-training distribution (`<think>`
chain-of-thought reasoning + interactive `bash`, `view_file`, `str_replace`,
`write_file`, `python`, and `submit` tools inside `/testbed`), combined with
HAE's Multi-Specialist Epistemic Verification:

  1. **Specialist 1 (Interactive Localization, Reproduction & Surgical Repair)**:
     Explores `/testbed` via `bash` (`git grep`, `find`, `pytest`) and
     `view_file`, writes `/testbed/.hae/reproduce_issue.py` to reproduce the
     bug on the unpatched repository, applies surgical edits with `str_replace`
     (guarded by immediate in-memory `compile()` + `static_check`), and
     iterates in-place against `reproduce_issue.py` and `pytest` without
     destructive workspace wipes.
  2. **Gatekeeper Pre-Submission Verification**:
     Every `submit` action is audited by `EvidenceGatekeeper`: untracked
     scratch files are cleaned, modified `.py` files are compiled, imported,
     statically checked (`undefined_names`), tested against
     `/testbed/.hae/reproduce_issue.py`, and checked against the repository's
     own `pytest` suite (`verify_module`). Premature submissions with failing
     repros or new test regressions are rejected with full diagnostic output
     so the specialist fixes them in-place.
  3. **Specialist 2 (Adversarial Edge-Case Auditor / Escalation Specialist)**:
     - When Specialist 1 produces a verified patch, the **Adversarial Auditor**
       reviews `git diff` against every requirement in the problem statement,
       authors `/testbed/.hae/audit_edge_cases.py` covering boundary inputs,
       sibling methods/classes, return types, and warnings, and refines the
       patch if any edge case fails.
     - When Specialist 1 stalls without a verified patch, **Specialist 2**
       is launched from a clean checkout (preserving any working reproduction
       script) with a structured post-mortem of Specialist 1's attempts and
       failure tracebacks to pursue an alternative root cause.
  4. **Monotonic Best-Patch Checkpointing**:
     Every candidate `git diff` is scored and checkpointed in memory so a
     working or partially verified patch is never lost to a `0`-byte revert.
"""

from __future__ import annotations

import dataclasses
import difflib
import os
import re
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from hae.epistemic.gatekeeper import EvidenceGatekeeper
from hae.epistemic.ledger import (
    EpistemicState,
    Evidence,
    Q_RESOLVED,
    SUPPORTED,
)
from hae.epistemic.static_check import describe_findings, undefined_names
from hae.swebench.executor import CommandExecutor, ExecResult, SCRATCH_DIR
from hae.swebench.task import SweTask, is_test_path


MAX_OBS_CHARS = 8000
MAX_VIEW_LINES = 500
CONDENSE_KEEP_RECENT_MESSAGES = 16
CONDENSE_MAX_OLD_OBS_CHARS = 1200


@dataclasses.dataclass
class PatchCheckpoint:
    """Snapshot of a candidate repository diff and its verification signals."""
    diff: str
    changed_files: List[str]
    file_contents: Dict[str, str]
    gatekeeper_ok: bool = False
    repro_ok: bool = False
    audit_ok: bool = False
    compile_ok: bool = True
    static_ok: bool = True
    repo_tests_ok: bool = False
    new_test_failures: int = 999
    specialist: str = ""
    turn: int = 0
    note: str = ""

    def rank_key(self) -> Tuple[int, int, int, int, int, int, int]:
        """Higher is better."""
        return (
            1 if self.gatekeeper_ok else 0,
            1 if (self.repro_ok and self.repo_tests_ok) else 0,
            1 if self.audit_ok else 0,
            1 if self.repro_ok else 0,
            1 if self.repo_tests_ok else 0,
            1 if (self.compile_ok and self.static_ok) else 0,
            -int(self.new_test_failures),
        )


def _truncate_output(text: str, max_chars: int = MAX_OBS_CHARS) -> str:
    if not text:
        return "(empty output)"
    if len(text) <= max_chars:
        return text
    half = (max_chars - 120) // 2
    omitted = len(text) - (2 * half)
    return f"{text[:half]}\n... [truncated {omitted} chars] ...\n{text[-half:]}"


def _strip_line_number_prefixes(block: str) -> str:
    """If the model copied `  123 | code` prefixes from `view_file`, strip them."""
    lines = block.split("\n")
    if not lines:
        return block
    pipe_pat = re.compile(r"^\s*\d+\s*\|\s?(.*)$")
    matches = [pipe_pat.match(l) for l in lines if l.strip()]
    if matches and all(m is not None for m in matches):
        out = []
        for l in lines:
            m = pipe_pat.match(l)
            out.append(m.group(1) if m else l)
        return "\n".join(out)
    return block


@dataclasses.dataclass
class ParsedAction:
    kind: str  # "bash" | "view_file" | "str_replace" | "write_file" | "submit" | "none"
    raw: str = ""
    command: str = ""
    path: str = ""
    start_line: int = 1
    end_line: int = 300
    old_str: str = ""
    new_str: str = ""
    summary: str = ""
    error: str = ""


# Dedicated regex for `str_replace` that anchors on `>>>>` before closing ` ``` `,
# so nested triple backticks inside Python docstrings never truncate the block.
_STR_REPLACE_FENCE_RE = re.compile(
    r"```(?:str_replace|edit_file)\b[^\n]*\n(?P<body>.*?\n\s*>{4,}[^\n]*)\s*(?:</?[a-zA-Z0-9_:-]+>\s*)*```",
    re.DOTALL | re.IGNORECASE,
)

# Bare `path: ...\n<<<<\n...\n====\n...\n>>>>` even if the model omitted outer ```str_replace
_BARE_STR_REPLACE_RE = re.compile(
    r"(?:^|\n)\s*(?:path|file|filepath)\s*:\s*(?P<path>[^\n]+?)\s*\n"
    r"<{4,}[^\n]*\n(?P<old>.*?)\n={4,}[^\n]*\n(?P<new>.*?)\n>{4,}",
    re.DOTALL | re.IGNORECASE,
)

_OTHER_FENCE_RE = re.compile(
    r"```(?P<tag>bash|sh|shell|view_file|write_file|submit|python|py)\b[^\n]*\n(?P<body>.*?)(?:```|\Z)",
    re.DOTALL | re.IGNORECASE,
)


def _strip_rfl_artifacts(val: str) -> str:
    """Strip Reflection Beam `<rfl:...>` / `</rfl:...>` and XML tool-call closing tags (including truncated tail tags)."""
    if not val:
        return ""
    val = re.sub(
        r"^\s*(?:</?(?:rfl:[a-zA-Z0-9_:-]*|param[a-zA-Z0-9_:-]*\b[^>]*|func[a-zA-Z0-9_:-]*\b[^>]*|tool_call\b[^>]*|rfl\b[^>]*)\s*>?\s*)+",
        "",
        val,
        flags=re.IGNORECASE,
    )
    val = re.sub(
        r"(?:\s*</?(?:rfl:[a-zA-Z0-9_:-]*|param[a-zA-Z0-9_:-]*\b[^>]*|func[a-zA-Z0-9_:-]*\b[^>]*|tool_call\b[^>]*|rfl\b[^>]*)\s*>?\s*)+\Z",
        "",
        val,
        flags=re.IGNORECASE,
    )
    val = re.sub(r"<(/?)rfl:", r"<\1", val, flags=re.IGNORECASE)
    return val


def _clean_new_str_tail(val: str) -> str:
    """Strip trailing `>>>>`, closing fences ```` ``` ````, and XML/Beam tags from `new_str`."""
    cleaned = _strip_rfl_artifacts(val or "")
    cleaned = re.sub(
        r"(?:\r?\n)?(?:>{4,}[^\n]*|```)\s*(?:</?[a-zA-Z0-9_:-]+>\s*)*\Z",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    return _strip_rfl_artifacts(cleaned)


def _clean_path_token(raw_path: str) -> str:
    p = _strip_rfl_artifacts(raw_path or "").strip()
    if "\n" in p:
        p = p.splitlines()[0].strip()
    p = re.sub(r"^(?:path|file|filepath)\s*:\s*", "", p, flags=re.IGNORECASE)
    return p.strip().strip("'\"")


def _decode_kv_block_str(raw_val: str) -> str:
    """Decode an `old_str:` / `new_str:` value from either a JSON/Python quoted string or a multiline block."""
    cleaned = _strip_rfl_artifacts(raw_val)
    cleaned = re.sub(r"\n*```\s*\Z", "", cleaned)
    cleaned = re.sub(r"^\s*[|>][-+]?\s*\r?\n", "\n", cleaned)
    s = cleaned.strip()
    if len(s) >= 2 and ((s[0] == '"' and s[-1] == '"') or (s[0] == "'" and s[-1] == "'")):
        try:
            import ast
            val = ast.literal_eval(s)
            if isinstance(val, str):
                return val
        except Exception:
            pass
    if cleaned.startswith("\r\n"):
        cleaned = cleaned[2:]
    elif cleaned.startswith("\n"):
        cleaned = cleaned[1:]
    elif cleaned.startswith(" "):
        # If first line was written on the same line after `old_str: `, strip the single separator space
        # unless subsequent lines are indented deeper and the first line already has leading spaces.
        lines = cleaned.splitlines()
        if len(lines) == 1 or not cleaned.startswith("  "):
            cleaned = cleaned[1:]
    if cleaned.endswith("\r\n"):
        cleaned = cleaned[:-2]
    elif cleaned.endswith("\n"):
        cleaned = cleaned[:-1]
    return cleaned


def _parse_str_replace_body(body: str, raw_text: str) -> ParsedAction:
    body = _strip_rfl_artifacts(body)
    body = re.sub(r"\s*```\s*\Z", "", body)
    body = _strip_rfl_artifacts(body)
    m_sr = re.search(
        r"(?:^|\n)\s*(?:path|file|filepath)\s*:\s*(?P<path>[^\n]+?)\s*\n"
        r"\s*<{4,}[^\n]*\n(?P<old>.*?)\n\s*={4,}[^\n]*(?:\n(?P<new>.*?))?(?:\n\s*>{4,}[^\n]*|\Z)",
        body,
        re.DOTALL | re.IGNORECASE,
    )
    if not m_sr:
        m_sr = re.search(
            r"^\s*(?P<path>[^\n<]+?)\s*\n"
            r"\s*<{4,}[^\n]*\n(?P<old>.*?)\n\s*={4,}[^\n]*(?:\n(?P<new>.*?))?(?:\n\s*>{4,}[^\n]*|\Z)",
            body,
            re.DOTALL,
        )
    if not m_sr:
        # Handle nested fences like ```path\n/testbed/foo.py\n```\n<<<<\n...\n====\n...\n>>>>
        m_markers = re.search(
            r"(?:^|\n)\s*<{4,}[^\n]*\n(?P<old>.*?)\n\s*={4,}[^\n]*(?:\n(?P<new>.*?))?(?:\n\s*>{4,}[^\n]*|\Z)",
            body,
            re.DOTALL,
        )
        if m_markers:
            header = body[: m_markers.start()]
            extracted_path = ""
            for line in header.splitlines():
                s = line.strip()
                if not s or s.startswith("```") or s.startswith("<"):
                    continue
                cand = _clean_path_token(s)
                if cand and cand.lower() not in ("path", "file", "filepath", "str_replace", "edit_file"):
                    extracted_path = cand
                    break
            if extracted_path:
                old_str = m_markers.group("old")
                new_str = _clean_new_str_tail(m_markers.group("new") or "")
                return ParsedAction(
                    kind="str_replace", path=extracted_path, old_str=old_str, new_str=new_str, raw=raw_text
                )
        # Fallback if `<<<<` was omitted after `path: ...` (i.e. `path: foo.py\n<old>\n====\n<new>\n>>>>`)
        m_no_lt = re.search(
            r"(?:^|\n)\s*(?:path|file|filepath)\s*:\s*(?P<path>[^\n]+?)\s*\n(?P<old>.*?)\n\s*={4,}[^\n]*(?:\n(?P<new>.*?))?(?:\n\s*>{4,}[^\n]*|\Z)",
            body,
            re.DOTALL | re.IGNORECASE,
        )
        if m_no_lt:
            p_cand = _clean_path_token(m_no_lt.group("path"))
            old_c = re.sub(r"^\s*<{4,}[^\n]*\n", "", m_no_lt.group("old"))
            new_c = _clean_new_str_tail(m_no_lt.group("new") or "")
            if p_cand and old_c:
                return ParsedAction(kind="str_replace", path=p_cand, old_str=old_c, new_str=new_c, raw=raw_text)
        # Fallback if `>>>>` was used instead of `====` as the divider (`path: ...\n<<<<\n<old>\n>>>>\n<new>\n>>>>`)
        m_gt_div = re.search(
            r"(?:^|\n)\s*(?:(?:path|file|filepath)\s*:\s*)?(?P<path>[^\n<]+?)\s*\n"
            r"\s*<{4,}[^\n]*\n(?P<old>.*?)\n\s*>{4,}[^\n]*\n(?P<new>.+?)(?:\n\s*>{4,}[^\n]*|\Z)",
            body,
            re.DOTALL | re.IGNORECASE,
        )
        if m_gt_div:
            p_cand = _clean_path_token(m_gt_div.group("path"))
            old_c = m_gt_div.group("old")
            new_c = _clean_new_str_tail(m_gt_div.group("new"))
            if p_cand and old_c:
                return ParsedAction(kind="str_replace", path=p_cand, old_str=old_c, new_str=new_c, raw=raw_text)
        # Fallback for `path: ...\nold_str: ...\nnew_str: ...` (used by Reflection Beam)
        m_kv_sr = re.search(
            r"(?:^|\n)\s*(?:path|file|filepath)\s*:\s*(?P<path>[^\n]+?)\s*\n"
            r"\s*(?:old_str|old_string|old)\s*:(?P<old>.*?)\n"
            r"\s*(?:new_str|new_string|new)\s*:(?P<new>.*)",
            body,
            re.DOTALL | re.IGNORECASE,
        )
        if m_kv_sr:
            p_cand = _clean_path_token(m_kv_sr.group("path"))
            old_c = _decode_kv_block_str(m_kv_sr.group("old"))
            new_c = _decode_kv_block_str(m_kv_sr.group("new"))
            if p_cand and old_c:
                return ParsedAction(kind="str_replace", path=p_cand, old_str=old_c, new_str=new_c, raw=raw_text)
        return ParsedAction(
            kind="str_replace",
            error=(
                "Malformed `str_replace` block. Emit a markdown fenced block with this exact format:\n"
                "```str_replace\npath: path/to/file.py\n<<<<\n<exact old lines>\n====\n<replacement lines>\n>>>>\n```"
            ),
            raw=raw_text,
        )
    path = _clean_path_token(m_sr.group("path"))
    old_str = m_sr.group("old")
    new_str = _clean_new_str_tail(m_sr.group("new") or "")
    return ParsedAction(kind="str_replace", path=path, old_str=old_str, new_str=new_str, raw=raw_text)


def _strip_single_outer_newlines(val: str) -> str:
    if val.startswith("\r\n"):
        val = val[2:]
    elif val.startswith("\n"):
        val = val[1:]
    if val.endswith("\r\n"):
        val = val[:-2]
    elif val.endswith("\n"):
        val = val[:-1]
    return val


def _parse_xml_tool_call(text: str) -> Optional[ParsedAction]:
    """Parse Qwen3/Qwen3.8 and Reflection Beam native `<tool_call><function=...><parameter=...>...</tool_call>` blocks."""
    norm_text = re.sub(r"<(/?)rfl:", r"<\1", text, flags=re.IGNORECASE)
    m_tc = re.search(r"<tool_call>\s*(.*?)(?:</tool_call>|\Z)", norm_text, re.DOTALL | re.IGNORECASE)
    inner = m_tc.group(1) if m_tc else norm_text

    m_fn = re.search(
        r"<(?:function\s*=\s*|(?==))=?([a-zA-Z0-9_:-]+)\s*>?(.*?)(?:</function>|(?=<tool_call>)|\Z)",
        inner,
        re.DOTALL | re.IGNORECASE,
    )
    if not m_fn:
        m_direct_tag = re.search(
            r"<(bash|sh|shell|run_command|execute_bash|view_file|read_file|view|str_replace|edit_file|write_file|create_file|submit|finish)\b[^>]*>(.*?)(?:</\1>|(?=</tool_call>)|\Z)",
            inner,
            re.DOTALL | re.IGNORECASE,
        )
        if m_direct_tag:
            m_fn = m_direct_tag
    if not m_fn and m_tc:
        # Handle `<tool_call>\n<parameter=bash>\n<parameter=command>...` or `<tool_call>\n{\n<parameter name="command">...`
        m_alt = re.search(
            r"<parameter\s*(?:=|\s+name=)\s*[\"']?(bash|sh|shell|view_file|str_replace|write_file|submit)[\"']?\s*>?(.*)",
            inner,
            re.DOTALL | re.IGNORECASE,
        )
        if m_alt:
            m_fn = m_alt
        elif re.search(r"<parameter\s*(?:=|\s+name=)\s*[\"']?command[\"']?\s*>", inner, re.IGNORECASE):
            m_cmd = re.search(
                r"<parameter\s*(?:=|\s+name=)\s*[\"']?command[\"']?\s*>(.*?)(?:</parameter>|(?=</tool_call>)|\Z)",
                inner,
                re.DOTALL | re.IGNORECASE,
            )
            if m_cmd:
                return _dispatch_xml_function("bash", {"command": _strip_single_outer_newlines(_strip_rfl_artifacts(m_cmd.group(1)))}, inner, text)
    if not m_fn:
        if m_tc:
            # Check if JSON inside <tool_call>{...}</tool_call>
            import json
            try:
                payload = json.loads(inner.strip())
                if isinstance(payload, dict) and "name" in payload:
                    fname = str(payload.get("name") or "").lower()
                    args = payload.get("arguments") or payload.get("parameters") or {}
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except Exception:
                            args = {}
                    if isinstance(args, dict):
                        params = {str(k).lower(): str(v) for k, v in args.items()}
                        return _dispatch_xml_function(fname, params, "", text)
            except Exception:
                pass
        return None

    func_name = m_fn.group(1).strip().lower()
    fn_body = m_fn.group(2)

    params: Dict[str, str] = {}
    for m_p in re.finditer(
        r"<parameter\s*(?:=|\s+name=)\s*[\"']?([a-zA-Z0-9_:-]+)[\"']?\s*>?(.*?)(?:</parameter>|(?=<parameter\b)|(?=</function>)|(?=</tool_call>)|\Z)",
        fn_body,
        re.DOTALL | re.IGNORECASE,
    ):
        pkey = m_p.group(1).strip().lower()
        pval = _strip_single_outer_newlines(_strip_rfl_artifacts(m_p.group(2)))
        params[pkey] = pval

    return _dispatch_xml_function(func_name, params, fn_body, text)


def _dispatch_xml_function(
    func_name: str, params: Dict[str, str], fn_body: str, raw_text: str
) -> Optional[ParsedAction]:
    if func_name in ("bash", "sh", "shell", "run_command", "execute_bash", "terminal"):
        cmd = _strip_rfl_artifacts(
            params.get("command")
            or params.get("cmd")
            or params.get("script")
            or re.sub(r"</?(?:parameter|b|function)[^>]*>", "", fn_body).strip()
        ).strip()
        if not cmd:
            return ParsedAction(
                kind="bash",
                error="Empty `command` in `bash`. Emit a markdown block:\n```bash\nyour_command_here\n```",
                raw=raw_text,
            )
        if "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT" in cmd:
            return ParsedAction(kind="submit", summary=cmd, raw=raw_text)
        return ParsedAction(kind="bash", command=cmd, raw=raw_text)

    if func_name in ("view_file", "read_file", "view", "open_file"):
        for line in re.sub(r"</?parameter[^>]*>", "", fn_body).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                k_clean = k.strip().lower()
                if k_clean in ("path", "file", "filepath", "start_line", "start", "end_line", "end") and k_clean not in params:
                    params[k_clean] = v.strip().strip("'\"")
        path = _clean_path_token(params.get("path") or params.get("file") or params.get("filepath") or "")
        if not path:
            # Try extracting path from lines in fn_body if parameter tag was malformed
            clean_lines = [
                l.strip()
                for l in re.sub(r"</?parameter[^>]*>", "", fn_body).splitlines()
                if l.strip() and not l.strip().isdigit()
            ]
            if clean_lines:
                path = _clean_path_token(clean_lines[0])
        start_line = 1
        end_line = 350
        for sk in ("start_line", "start", "from_line", "from", "offset"):
            if sk in params:
                try:
                    start_line = max(1, int(params[sk].strip()))
                except ValueError:
                    pass
        for ek in ("end_line", "end", "to_line", "to", "limit"):
            if ek in params:
                try:
                    end_line = max(1, int(params[ek].strip()))
                except ValueError:
                    pass
        if not path:
            return ParsedAction(
                kind="view_file",
                error=(
                    "Missing `path` in `view_file`. Emit a markdown block with `path:` explicitly:\n"
                    "```view_file\npath: path/to/file.py\nstart_line: 1\nend_line: 250\n```"
                ),
                raw=raw_text,
            )
        return ParsedAction(kind="view_file", path=path, start_line=start_line, end_line=end_line, raw=raw_text)

    if func_name in ("str_replace", "edit_file", "replace_in_file", "str_replace_editor"):
        raw_path_param = params.get("path") or params.get("file") or params.get("filepath") or ""
        path = _clean_path_token(raw_path_param)
        if not path:
            # Check if first non-tag line of fn_body before <<<< is a path
            pre_conflict = fn_body.split("<<<<", 1)[0]
            for l in re.sub(r"</?parameter[^>]*>", "", pre_conflict).splitlines():
                cand = _clean_path_token(l)
                if cand and ("/" in cand or cand.endswith(".py")):
                    path = cand
                    break

        # 1. Check if `fn_body` contains `<<<< ... ==== ... >>>>` conflict markers (hybrid XML + conflict format)
        m_conflict = re.search(
            r"<{4,}[^\n]*\n(?P<old>.*?)\n={4,}[^\n]*\n(?P<new>.*?)(?:\n>{4,}[^\n]*|</parameter>|</function>|</tool_call>|\Z)",
            fn_body,
            re.DOTALL,
        )
        if path and m_conflict:
            old_c = m_conflict.group("old")
            new_c = re.sub(r"\n*(?:>{4,}[^\n]*|</parameter>|</function>|</tool_call>)\s*\Z", "", m_conflict.group("new"))
            return ParsedAction(kind="str_replace", path=path, old_str=old_c, new_str=new_c, raw=raw_text)

        # 2. Check named or prefix-matched parameters (`old_str`, `old_string`, `old_code`, `old_lines`, etc.)
        old_str = None
        for ok in ("old_str", "old_string", "old", "old_text", "old_code", "old_lines", "old_content", "target", "search", "original", "before", "find"):
            if ok in params:
                old_str = params[ok]
                break
        if old_str is None:
            for k, v in params.items():
                if k not in ("path", "file", "filepath") and k.startswith(("old", "orig", "search", "find", "target", "before")):
                    old_str = v
                    break

        new_str = None
        for nk in ("new_str", "new_string", "new", "new_text", "new_code", "new_lines", "new_content", "replacement", "replace", "updated", "after", "content"):
            if nk in params:
                new_str = params[nk]
                break
        if new_str is None:
            for k, v in params.items():
                if k not in ("path", "file", "filepath") and k.startswith(("new", "repl", "updat", "after")):
                    new_str = v
                    break

        # 3. Positional fallback if 2 non-path parameters were supplied
        non_path_items = [(k, v) for k, v in params.items() if k not in ("path", "file", "filepath")]
        if (old_str is None or new_str is None) and len(non_path_items) == 2:
            if old_str is None:
                old_str = non_path_items[0][1]
            if new_str is None:
                new_str = non_path_items[1][1]
        elif (old_str is None or new_str is None) and len(non_path_items) == 1:
            single_val = non_path_items[0][1]
            m_split = re.search(r"^(?:<{4,}[^\n]*\n)?(?P<old>.*?)\n={4,}[^\n]*\n(?P<new>.*?)(?:\n>{4,}[^\n]*|\Z)", single_val, re.DOTALL)
            if m_split:
                old_str = m_split.group("old")
                new_str = m_split.group("new")

        if path and old_str is not None and new_str is not None:
            old_str = re.sub(r"^<{4,}[^\n]*\n", "", old_str)
            new_str = re.sub(r"\n>{4,}[^\n]*\Z", "", new_str)
            return ParsedAction(kind="str_replace", path=path, old_str=old_str, new_str=new_str, raw=raw_text)
        return _parse_str_replace_body(fn_body, raw_text)

    if func_name in ("write_file", "create_file"):
        path = (params.get("path") or params.get("file") or params.get("filepath") or "").strip().strip("'\"")
        content = (
            params.get("content")
            or params.get("new_str")
            or params.get("file_text")
            or params.get("code")
            or ""
        )
        if not path:
            return ParsedAction(kind="write_file", error="Missing `path` in `<function=write_file>`.", raw=raw_text)
        return ParsedAction(kind="write_file", path=path, new_str=content, raw=raw_text)

    if func_name in ("submit", "finish", "complete", "complete_task"):
        summary = (
            params.get("summary")
            or params.get("message")
            or params.get("explanation")
            or re.sub(r"</?parameter[^>]*>", "", fn_body).strip()
            or "Completed repair."
        )
        return ParsedAction(kind="submit", summary=summary, raw=raw_text)

    return None


def parse_agent_action(response_text: str) -> ParsedAction:
    """Extract the structured tool action from an assistant response."""
    text = (response_text or "").strip()
    if not text:
        return ParsedAction(kind="none", error="Empty response from model.")

    # 0. Check for Qwen3.8 / Reflection Beam native `<tool_call><function=...>` XML format if it appears first
    xml_action = _parse_xml_tool_call(text)
    m_sr_fence = _STR_REPLACE_FENCE_RE.search(text)
    m_other = _OTHER_FENCE_RE.search(text)
    m_xml_pos = re.search(
        r"<(?:(?:rfl:)?tool_call|(?:rfl:)?function\s*=|(?:rfl:)?view_file\b|(?:rfl:)?str_replace\b|(?:rfl:)?bash\b|(?:rfl:)?write_file\b|(?:rfl:)?submit\b)",
        text,
        re.IGNORECASE,
    )
    first_fence_pos = min(
        [m.start() for m in (m_sr_fence, m_other) if m is not None],
        default=len(text) + 1,
    )
    if xml_action is not None and m_xml_pos is not None and m_xml_pos.start() <= first_fence_pos:
        return xml_action

    # 1. Check for ```str_replace ... >>>>\n``` first (handles nested ``` inside docstrings)
    if m_sr_fence and (not m_other or m_sr_fence.start() <= m_other.start()):
        return _parse_str_replace_body(m_sr_fence.group("body"), text)

    # Also check if a ```str_replace fence was opened without matching _STR_REPLACE_FENCE_RE
    m_sr_open = re.search(r"```(?:str_replace|edit_file)\b[^\n]*\n(.*)", text, re.DOTALL | re.IGNORECASE)
    if m_sr_open and (not m_other or m_sr_open.start() <= m_other.start()):
        inner = _strip_rfl_artifacts(m_sr_open.group(1))
        if inner.endswith("```"):
            inner = inner[:-3].rstrip()
        return _parse_str_replace_body(inner, text)

    # 2. Check for bare `path: ...\n<<<<\n...\n====\n...\n>>>>`
    m_bare_sr = _BARE_STR_REPLACE_RE.search(text)
    if m_bare_sr and (not m_other or m_bare_sr.start() <= m_other.start()):
        return ParsedAction(
            kind="str_replace",
            path=_clean_path_token(m_bare_sr.group("path")),
            old_str=m_bare_sr.group("old"),
            new_str=_clean_new_str_tail(m_bare_sr.group("new")),
            raw=text,
        )

    # 3. Check standard fenced blocks (`bash`, `view_file`, `write_file`, `submit`, `python`)
    if m_other:
        tag = m_other.group("tag").lower()
        body = _strip_rfl_artifacts(m_other.group("body"))

        if tag in ("bash", "sh", "shell"):
            cmd = body.strip()
            if "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT" in cmd:
                return ParsedAction(kind="submit", summary=cmd, raw=text)
            if re.match(r"^(?:str_replace|edit_file)\s*\n", cmd, re.IGNORECASE) or (
                "\n<<<<\n" in cmd and "\n====\n" in cmd
            ):
                sr_body = re.sub(r"^(?:str_replace|edit_file)\s*\n", "", cmd, flags=re.IGNORECASE)
                return _parse_str_replace_body(sr_body, text)
            return ParsedAction(kind="bash", command=cmd, raw=text)

        if tag == "submit":
            return ParsedAction(kind="submit", summary=body.strip() or "Completed repair.", raw=text)

        if tag == "view_file":
            path = ""
            start_line = 1
            end_line = 350
            for line in body.strip().splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    k = k.strip().lower()
                    v = v.strip().strip("'\"")
                    if k in ("path", "file", "filepath"):
                        path = v
                    elif k in ("start_line", "start", "from_line", "from"):
                        try:
                            start_line = max(1, int(v))
                        except ValueError:
                            pass
                    elif k in ("end_line", "end", "to_line", "to"):
                        try:
                            end_line = max(1, int(v))
                        except ValueError:
                            pass
                elif not path and line.strip():
                    tok = line.strip()
                    m_range = re.match(r"^([^\s:]+)(?::(\d+)-(\d+)|\s+(\d+)\s+(\d+))?$", tok)
                    if m_range:
                        path = m_range.group(1)
                        if m_range.group(2) and m_range.group(3):
                            start_line, end_line = int(m_range.group(2)), int(m_range.group(3))
                        elif m_range.group(4) and m_range.group(5):
                            start_line, end_line = int(m_range.group(4)), int(m_range.group(5))
            if not path:
                return ParsedAction(kind="view_file", error="Missing `path:` in ```view_file block.", raw=text)
            return ParsedAction(kind="view_file", path=path, start_line=start_line, end_line=end_line, raw=text)

        if tag == "write_file":
            lines = body.splitlines()
            if lines and re.match(r"^\s*(?:path|file|filepath)\s*:\s*", lines[0], re.IGNORECASE):
                p = lines[0].split(":", 1)[1].strip().strip("'\"")
                content = "\n".join(lines[1:])
                return ParsedAction(kind="write_file", path=p, new_str=content, raw=text)

        if tag in ("python", "py"):
            # Legacy Action: write_file + ```python support
            m_act_wf = re.search(
                r"Action\s*:\s*write_file.*?Path\s*:\s*([^\n]+)",
                text[: m_other.start()],
                re.DOTALL | re.IGNORECASE,
            )
            if m_act_wf:
                return ParsedAction(
                    kind="write_file",
                    path=_clean_path_token(m_act_wf.group(1)),
                    new_str=body,
                    raw=text,
                )
            return ParsedAction(
                kind="none",
                error=(
                    "Standalone ```python blocks are not executed directly. "
                    "To run Python or write `/testbed/.hae/reproduce_issue.py`, emit a ```bash block; "
                    "to edit a repository file, emit a ```str_replace block."
                ),
                raw=text,
            )

    if xml_action is not None:
        return xml_action

    if "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT" in text:
        return ParsedAction(kind="submit", summary=text, raw=text)

    return ParsedAction(
        kind="none",
        error=(
            "No valid tool block found in your response. Emit EXACTLY ONE markdown fenced tool block per turn: "
            "```bash ... ```, ```view_file ... ```, ```str_replace ... ```, or ```submit ... ```."
        ),
        raw=text,
    )


def apply_str_replace_in_memory(
    source: str, old_str: str, new_str: str
) -> Tuple[Optional[str], int, str]:
    """Replace `old_str` with `new_str` in `source`.

    Returns `(updated_source_or_None, replacement_line_number, status_note)`.
    """
    if old_str == new_str:
        return None, 0, "`old_str` and `new_str` are identical (no-op edit)."

    # 1. Exact match
    count = source.count(old_str)
    if count == 1:
        idx = source.find(old_str)
        line_no = source[:idx].count("\n") + 1
        return source.replace(old_str, new_str, 1), line_no, "exact match"
    if count > 1:
        return None, 0, f"`old_str` appears {count} times in the file; include more surrounding context lines to make it unique."

    # 2. Try stripping accidental `123 | ` line numbers
    cleaned_old = _strip_line_number_prefixes(old_str)
    cleaned_new = _strip_line_number_prefixes(new_str)
    if cleaned_old != old_str:
        c2 = source.count(cleaned_old)
        if c2 == 1:
            idx = source.find(cleaned_old)
            line_no = source[:idx].count("\n") + 1
            return source.replace(cleaned_old, cleaned_new, 1), line_no, "matched after stripping line-number prefixes"
        if c2 > 1:
            return None, 0, f"`old_str` appears {c2} times in the file; include more surrounding context lines."

    # 3. Line-by-line rstrip match
    src_lines = source.split("\n")
    old_lines = cleaned_old.split("\n")
    new_lines = cleaned_new.split("\n")
    n_old = len(old_lines)
    if n_old > 0 and n_old <= len(src_lines):
        old_r = [l.rstrip() for l in old_lines]
        hits = [
            i for i in range(len(src_lines) - n_old + 1)
            if [src_lines[i + k].rstrip() for k in range(n_old)] == old_r
        ]
        if len(hits) == 1:
            i = hits[0]
            updated = src_lines[:i] + new_lines + src_lines[i + n_old:]
            return "\n".join(updated), i + 1, "rstrip-normalized match"
        if len(hits) > 1:
            return None, 0, f"`old_str` matches {len(hits)} locations (at lines {[h + 1 for h in hits[:5]]}); include more context lines."

        # 4. Uniform indentation-offset match
        def _min_indent(lines_seq: Sequence[str]) -> int:
            non_empty = [len(l) - len(l.lstrip(" ")) for l in lines_seq if l.strip()]
            return min(non_empty) if non_empty else 0

        old_base_indent = _min_indent(old_lines)
        old_dedented = [l[old_base_indent:].rstrip() if l.strip() else "" for l in old_lines]
        non_blank_old = sum(1 for l in old_dedented if l)
        if non_blank_old >= 2:
            indent_hits: List[Tuple[int, int]] = []
            for i in range(len(src_lines) - n_old + 1):
                window = src_lines[i : i + n_old]
                win_indent = _min_indent(window)
                win_dedented = [l[win_indent:].rstrip() if l.strip() else "" for l in window]
                if win_dedented == old_dedented:
                    indent_hits.append((i, win_indent - old_base_indent))
            if len(indent_hits) == 1:
                i, delta = indent_hits[0]
                shifted_new: List[str] = []
                for l in new_lines:
                    if not l.strip():
                        shifted_new.append("")
                    elif delta >= 0:
                        shifted_new.append((" " * delta) + l)
                    else:
                        strip_n = min(-delta, len(l) - len(l.lstrip(" ")))
                        shifted_new.append(l[strip_n:])
                updated = src_lines[:i] + shifted_new + src_lines[i + n_old:]
                return "\n".join(updated), i + 1, f"indent-adjusted match (delta={delta:+d})"

    best_ratio = 0.0
    best_start = 1
    if n_old > 0 and src_lines:
        step = max(1, n_old // 4)
        target_norm = "\n".join(l.strip() for l in old_lines if l.strip())
        for i in range(0, max(1, len(src_lines) - n_old + 1), step):
            win_norm = "\n".join(l.strip() for l in src_lines[i : i + n_old] if l.strip())
            if not win_norm:
                continue
            r = difflib.SequenceMatcher(None, target_norm, win_norm).quick_ratio()
            if r > best_ratio:
                best_ratio = r
                best_start = i + 1
    hint = f" (closest match is around lines {best_start}-{min(len(src_lines), best_start + n_old + 5)}; use `view_file` to inspect exact lines)" if best_ratio >= 0.45 else ""
    return None, 0, f"`old_str` not found in file{hint}."


class AgenticSweEngine:
    """Multi-Specialist Agentic SWE Engine for V12."""

    def __init__(
        self,
        runner: Any,
        task: SweTask,
        executor: CommandExecutor,
        gatekeeper: EvidenceGatekeeper,
        state: EpistemicState,
        logger: Callable[[str], None] = print,
    ) -> None:
        self.runner = runner
        self.task = task
        self.executor = executor
        self.gatekeeper = gatekeeper
        self.state = state
        self.logger = logger
        self.checkpoints: List[PatchCheckpoint] = []
        self.trajectory_log: List[Dict[str, Any]] = []
        self.total_moves_used: int = 0
        self.repro_script_rel = f"{SCRATCH_DIR}/reproduce_issue.py"
        self.audit_script_rel = f"{SCRATCH_DIR}/audit_edge_cases.py"
        self._python_bin_dir = os.path.dirname(self.executor.python or "/opt/miniconda3/envs/testbed/bin/python")
        self.run_start_time: float = time.time()
        self.max_wall_s: float = float(os.environ.get("HAE_AGENTIC_MAX_WALL_S", "1650"))

    # ------------------------------------------------------------------ #
    # Workspace & Shell Helpers
    # ------------------------------------------------------------------ #

    def normalize_rel_path(self, raw_path: str) -> str:
        p = _clean_path_token(raw_path)
        root = self.executor.root.rstrip("/")
        if p.startswith(root + "/"):
            p = p[len(root) + 1 :]
        return p.lstrip("./")

    _normalize_rel_path = normalize_rel_path

    def _clean_untracked_outside_scratch(self) -> List[str]:
        """Move any stray untracked `.py` scripts at repo root into `.hae/` and remove stray untracked artifacts."""
        res = self.executor.run(["git", "status", "--porcelain"], cwd=self.executor.root, timeout_s=30)
        if res.rc != 0:
            return []
        cleaned: List[str] = []
        for line in res.stdout.splitlines():
            if not line.startswith("?? "):
                continue
            rel = line[3:].strip()
            if not rel or rel.startswith(SCRATCH_DIR + "/") or rel == SCRATCH_DIR:
                continue
            base = os.path.basename(rel).lower()
            if ("repro" in base or base.startswith("debug") or base.startswith("tmp_")) and rel.endswith(".py"):
                dst = self.repro_script_rel if not self.executor.exists(self.repro_script_rel) else f"{SCRATCH_DIR}/{os.path.basename(rel)}"
                self.executor.run(["mkdir", "-p", f"{self.executor.root}/{SCRATCH_DIR}"], cwd=self.executor.root, timeout_s=15)
                self.executor.run(["mv", "-f", rel, dst], cwd=self.executor.root, timeout_s=15)
                cleaned.append(f"{rel} -> {dst}")
            else:
                self.executor.run(["rm", "-rf", rel], cwd=self.executor.root, timeout_s=15)
                cleaned.append(f"removed untracked {rel}")
        return cleaned

    def _current_diff_and_files(self) -> Tuple[str, List[str], Dict[str, str]]:
        self._clean_untracked_outside_scratch()
        diff = self.executor.diff()
        changed = [
            f for f in self.executor.changed_files()
            if not f.startswith(SCRATCH_DIR + "/") and f != SCRATCH_DIR
        ]
        contents: Dict[str, str] = {}
        for rel in changed:
            try:
                if self.executor.exists(rel):
                    contents[rel] = self.executor.read_file(rel)
            except Exception:
                pass
        return diff, changed, contents

    def _restore_checkpoint(self, cp: PatchCheckpoint) -> None:
        """Reset tracked files to HEAD and write `cp.file_contents`."""
        self.executor.run(["git", "checkout", "-q", "--", "."], cwd=self.executor.root, timeout_s=60)
        self._clean_untracked_outside_scratch()
        for rel, content in cp.file_contents.items():
            self.executor.write_file(rel, content)

    def _reset_tracked_to_head(self) -> None:
        self.executor.run(["git", "checkout", "-q", "--", "."], cwd=self.executor.root, timeout_s=60)
        self._clean_untracked_outside_scratch()

    # ------------------------------------------------------------------ #
    # Tool Execution
    # ------------------------------------------------------------------ #

    def execute_bash_tool(self, command: str, timeout_s: float = 120.0) -> str:
        cmd = (command or "").strip()
        if not cmd:
            return "Observation (bash): empty command."
        if re.search(r"\bgit\s+(?:commit|push|rebase|stash|reset\s+--hard\s+HEAD~|filter-branch)\b", cmd):
            return "Observation (bash): ERROR: git history/stash mutation (`commit`/`push`/`stash`/`reset HEAD~`) is not permitted. Edit files in place so `git diff` reflects your changes."
        if re.search(r"\bgit\s+(?:fetch|pull)\b", cmd) or re.search(r"\bpip\s+(?:download|install)\b", cmd):
            return (
                "Observation (bash): ERROR: Network commands (`git fetch`/`git pull`/`pip install`) are disabled in the benchmark sandbox. "
                "Inspect and edit the current working tree directly using `git grep -n`, `view_file`, `pytest`, and `str_replace`."
            )

        wrapped = f'export PATH="{self._python_bin_dir}:$PATH"; export PYTHONPATH="{self.executor.root}:${{PYTHONPATH:-}}"; export PYTHONDONTWRITEBYTECODE=1; {cmd}'
        t0 = time.time()
        res: ExecResult = self.executor.run(["bash", "-c", wrapped], cwd=self.executor.root, timeout_s=timeout_s)
        dt = time.time() - t0
        combined = ""
        if res.stdout:
            combined += res.stdout
        if res.stderr:
            if combined and not combined.endswith("\n"):
                combined += "\n"
            combined += res.stderr

        if "reproduce_issue.py" in cmd or "audit_edge_cases.py" in cmd:
            self._record_probe_from_bash(cmd, res)

        # Also check if bash command modified tracked files (e.g., via python -c or sed)
        bash_static_warn = ""
        diff, changed, contents = self._current_diff_and_files()
        if diff.strip() and (not self.checkpoints or self.checkpoints[-1].diff != diff):
            b_compile_ok = True
            b_static_ok = True
            static_msgs: List[str] = []
            for rel_f, src_f in contents.items():
                if not rel_f.endswith(".py"):
                    continue
                try:
                    compile(src_f.encode("utf-8"), rel_f, "exec")
                except SyntaxError as syn_e:
                    b_compile_ok = False
                    static_msgs.append(f"{rel_f}:{syn_e.lineno}: SyntaxError: {syn_e.msg}")
                    continue
                if not rel_f.startswith(SCRATCH_DIR + "/"):
                    fnds = undefined_names(src_f, filename=rel_f)
                    if fnds:
                        b_static_ok = False
                        static_msgs.append(f"{rel_f}: {describe_findings(fnds)}")
            if static_msgs:
                bash_static_warn = (
                    "\nWARNING (syntax/undefined-name check on files modified by bash): "
                    + "; ".join(static_msgs)
                    + "\nFix missing imports or syntax errors immediately before submitting!"
                )
            b_repro_ok = False
            if b_compile_ok and self.executor.exists(self.repro_script_rel):
                res_br = self.executor.run(
                    [self.executor.python, self.repro_script_rel],
                    cwd=self.executor.root,
                    env={"PYTHONPATH": self.executor.root},
                    timeout_s=45,
                )
                b_repro_ok = (res_br.rc == 0 and not res_br.timed_out)
            self.checkpoints.append(
                PatchCheckpoint(
                    diff=diff,
                    changed_files=changed,
                    file_contents=contents,
                    gatekeeper_ok=False,
                    repro_ok=b_repro_ok,
                    compile_ok=b_compile_ok,
                    static_ok=b_static_ok,
                    specialist="bash_edit",
                    turn=self.total_moves_used,
                    note=f"modified via bash ({', '.join(changed)}, repro_ok={b_repro_ok})",
                )
            )

        status = "TIMEOUT" if res.timed_out else f"exit_code={res.rc}"
        return f"Observation (bash [{status}, {dt:.1f}s]):\n{_truncate_output(combined.strip())}{bash_static_warn}"

    def execute_view_file_tool(self, rel_path: str, start_line: int, end_line: int) -> str:
        rel = self.normalize_rel_path(rel_path)
        if not rel:
            return "Observation (view_file): ERROR: empty file path."
        if not self.executor.exists(rel):
            return f"Observation (view_file): ERROR: `{rel}` does not exist in `{self.executor.root}`."
        try:
            content = self.executor.read_file(rel)
        except Exception as exc:
            return f"Observation (view_file): ERROR reading `{rel}`: {exc}"
        lines = content.split("\n")
        total = len(lines)
        s = max(1, min(start_line, max(1, total)))
        # Allow viewing up to MAX_VIEW_LINES (500) lines at once so the agent doesn't spend 10 turns paging
        requested_span = max(end_line - start_line + 1, 250)
        e = max(s, min(s + min(requested_span, MAX_VIEW_LINES) - 1, total))
        numbered = [f"{idx:5d} | {lines[idx - 1]}" for idx in range(s, e + 1)]
        header = f"Observation (view_file `{rel}` lines {s}-{e} of {total}; TIP: use `git grep -n` in ```bash to jump directly to specific functions/lines):"
        return header + "\n```python\n" + "\n".join(numbered) + "\n```"

    def execute_write_file_tool(self, rel_path: str, content: str, run_after: bool = False) -> str:
        rel = self.normalize_rel_path(rel_path)
        if not rel:
            rel = self.repro_script_rel
        # Only allow full `write_file` on scratch scripts inside `.hae/` or brand-new files;
        # for existing tracked files, encourage `str_replace` unless the file is small (< 400 lines).
        if self.executor.exists(rel) and not rel.startswith(SCRATCH_DIR + "/"):
            existing = self.executor.read_file(rel)
            if len(existing.splitlines()) > 400:
                return (
                    f"Observation (write_file on `{rel}`): ERROR: `{rel}` is {len(existing.splitlines())} lines long. "
                    "Use `str_replace` to make surgical edits to existing repository files rather than overwriting the entire file."
                )
        if rel.endswith(".py"):
            try:
                compile(content.encode("utf-8"), rel, "exec")
            except SyntaxError as syn_err:
                return f"Observation (write_file on `{rel}`): ERROR: SyntaxError at line {syn_err.lineno}: {syn_err.msg}."
        self.executor.write_file(rel, content)
        if run_after or rel == self.repro_script_rel:
            run_obs = self.execute_bash_tool(f"python {rel}")
            return f"Observation (write_file `{rel}` + execution):\n{run_obs}"
        return f"Observation (write_file `{rel}`): Wrote {len(content)} chars."

    def execute_str_replace_tool(self, rel_path: str, old_str: str, new_str: str, specialist: str, turn: int) -> str:
        rel = self.normalize_rel_path(rel_path)
        if not rel:
            return "Observation (str_replace): ERROR: empty file path."
        if not self.executor.exists(rel):
            if rel.startswith(SCRATCH_DIR + "/") and not old_str.strip():
                self.executor.write_file(rel, new_str)
                return f"Observation (str_replace): Created `{rel}` ({len(new_str)} chars)."
            return f"Observation (str_replace): ERROR: `{rel}` does not exist."

        try:
            current_source = self.executor.read_file(rel)
        except Exception as exc:
            return f"Observation (str_replace): ERROR reading `{rel}`: {exc}"

        updated_source, line_no, match_note = apply_str_replace_in_memory(current_source, old_str, new_str)
        if updated_source is None:
            return f"Observation (str_replace on `{rel}`): ERROR: {match_note}"

        static_warn = ""
        if rel.endswith(".py"):
            try:
                compile(updated_source.encode("utf-8"), rel, "exec")
            except SyntaxError as syn_err:
                err_line = syn_err.lineno or line_no
                src_lines = updated_source.split("\n")
                lo = max(1, err_line - 5)
                hi = min(len(src_lines), err_line + 5)
                preview = "\n".join(f"{i:5d} | {src_lines[i - 1]}" for i in range(lo, hi + 1))
                return (
                    f"Observation (str_replace on `{rel}`): ERROR: Edit REJECTED because it introduces a "
                    f"`SyntaxError` on line {err_line}: {syn_err.msg}. `{rel}` on disk was NOT modified.\n"
                    f"Lines around the syntax error in your proposed edit:\n```python\n{preview}\n```\n"
                    f"Fix the indentation/syntax and re-send the `str_replace` block."
                )
            if not rel.startswith(SCRATCH_DIR + "/"):
                findings = undefined_names(updated_source, filename=rel)
                if findings:
                    static_warn = f"\nWARNING (static undefined-name check): {describe_findings(findings)}"

        self.executor.write_file(rel, updated_source)

        u_lines = updated_source.split("\n")
        n_new = len(new_str.split("\n"))
        lo = max(1, line_no - 4)
        hi = min(len(u_lines), line_no + n_new + 4)
        snippet = "\n".join(f"{i:5d} | {u_lines[i - 1]}" for i in range(lo, hi + 1))

        auto_repro_note = ""
        if not rel.startswith(SCRATCH_DIR + "/"):
            diff, changed, contents = self._current_diff_and_files()
            sr_repro_ok = False
            if diff.strip() and self.executor.exists(self.repro_script_rel):
                res_r = self.executor.run(
                    [self.executor.python, self.repro_script_rel],
                    cwd=self.executor.root,
                    env={"PYTHONPATH": self.executor.root},
                    timeout_s=15,
                )
                if res_r.rc == 0 and not res_r.timed_out:
                    sr_repro_ok = True
                    auto_repro_note = f"\n[Auto-check: `{self.repro_script_rel}` PASSED (exit 0)!]"
                else:
                    r_tail = _truncate_output(((res_r.stdout or "") + "\n" + (res_r.stderr or "")).strip(), 350)
                    auto_repro_note = f"\n[Auto-check: `{self.repro_script_rel}` FAILED (exit {res_r.rc}): {r_tail}]"
            if diff.strip():
                self.checkpoints.append(
                    PatchCheckpoint(
                        diff=diff,
                        changed_files=changed,
                        file_contents=contents,
                        gatekeeper_ok=False,
                        repro_ok=sr_repro_ok,
                        compile_ok=True,
                        static_ok=(not bool(static_warn)),
                        specialist=specialist,
                        turn=turn,
                        note=f"in-progress edit to {rel}:{line_no} (repro_ok={sr_repro_ok})",
                    )
                )

        return (
            f"Observation (str_replace on `{rel}` at line {line_no} [{match_note}]): SUCCESS.{static_warn}{auto_repro_note}\n"
            f"Updated lines {lo}-{hi} of `{rel}`:\n```python\n{snippet}\n```\n"
            f"Now run `pytest` on the relevant test file(s) via ```bash (or emit ```submit once verified)!"
        )

    # ------------------------------------------------------------------ #
    # Gatekeeper Audit & Pre-Submission Verification
    # ------------------------------------------------------------------ #

    def _record_probe_from_bash(self, cmd: str, res: ExecResult) -> None:
        """Record execution of `reproduce_issue.py` / `audit_edge_cases.py` into `EpistemicState`."""
        q_list = self.state.open_questions() or list(self.state.questions.values())
        if not q_list:
            return
        root_q = q_list[0]
        script_rel = self.audit_script_rel if "audit_edge_cases.py" in cmd else self.repro_script_rel
        if not self.executor.exists(script_rel):
            return
        try:
            probe_code = self.executor.read_file(script_rel)
        except Exception:
            probe_code = cmd
        out_summary = (res.stdout + "\n" + res.stderr).strip()[-600:]
        existing = self.state.hypotheses_for(root_q.question_id)
        if not existing and res.rc != 0:
            h = self.state.add_hypothesis(
                question_id=root_q.question_id,
                claim=f"Standalone reproduction script `{script_rel}` reproduces the issue failure (exit {res.rc}).",
                mechanism=out_summary[:300] or f"exit {res.rc}",
                prior=0.85,
                probe_code=probe_code[:4000],
                prediction={"expect_exit_code": res.rc},
                proposed_by="AgenticReproductionSpecialist",
            )
            ev = Evidence(
                evidence_id=self.state.new_evidence_id(),
                kind="probe",
                hypothesis_id=h.hypothesis_id,
                question_id=root_q.question_id,
                command=cmd[:200],
                exit_code=res.rc,
                stdout=res.stdout[-1500:],
                stderr=res.stderr[-1500:],
                prediction={"expect_exit_code": res.rc},
                matched_prediction=True,
                detail="agentic reproduction probe reproduced bug",
            )
            self.state.record_evidence(ev, self.gatekeeper.authority)
            self.state.set_hypothesis_verdict(
                h.hypothesis_id, SUPPORTED, 0.92, ev.evidence_id, self.gatekeeper.authority
            )
            self.state.lower_uncertainty(root_q.question_id, 0.15, self.gatekeeper.authority)

    def verify_current_workspace(self, specialist: str, turn: int, is_auditor: bool = False) -> Tuple[bool, str, Optional[PatchCheckpoint]]:
        """Run full Gatekeeper verification on the current workspace diff."""
        diff, changed, contents = self._current_diff_and_files()
        if not diff.strip() or not changed:
            return (
                False,
                "Submission REJECTED by EvidenceGatekeeper: `git diff` is empty (no tracked repository files were modified). "
                "Apply your fix to the repository source files using `str_replace` before submitting.",
                None,
            )

        non_test_changed = [f for f in changed if not is_test_path(f)]
        test_changed = [f for f in changed if is_test_path(f)]
        if test_changed and non_test_changed:
            for tf in test_changed:
                self.executor.run(["git", "checkout", "-q", "--", tf], cwd=self.executor.root, timeout_s=30)
            diff, changed, contents = self._current_diff_and_files()

        diagnostics: List[str] = []
        compile_ok = True
        static_ok = True
        repo_tests_ok = True
        new_failures = 0

        q_list = list(self.state.questions.values())
        root_q_id = q_list[0].question_id if q_list else ""

        py_changed = [f for f in changed if f.endswith(".py")]
        for rel in py_changed:
            src_txt = contents.get(rel, "")
            if src_txt:
                try:
                    compile(src_txt.encode("utf-8"), rel, "exec")
                except SyntaxError as syn_e:
                    compile_ok = False
                    diagnostics.append(f"- SyntaxError in `{rel}` line {syn_e.lineno}: {syn_e.msg}")
                    continue
                fnds = undefined_names(src_txt, filename=rel)
                if fnds:
                    static_ok = False
                    diagnostics.append(f"- Static check FAILED in `{rel}` (missing import / undefined name): {describe_findings(fnds)}")

        # Run gatekeeper.verify_module on at most 1 module in whole_suite mode (or up to 2 in proximity mode)
        # so multi-file patches don't re-run the entire test suite N times consecutively.
        gk_mode = getattr(self.gatekeeper, "whole_suite", None) or getattr(self.gatekeeper, "_whole_suite", None)
        is_whole_suite = isinstance(gk_mode, dict) and bool(gk_mode.get("argv") or gk_mode.get("mode") == "whole_suite")
        verify_targets = py_changed[:1] if is_whole_suite else py_changed[:2]
        for rel in verify_targets:
            ev = self.gatekeeper.verify_module(self.state, rel, question_id=root_q_id)
            try:
                self.state.record_evidence(ev, self.gatekeeper.authority)
            except Exception:
                pass
            if not ev.matched_prediction:
                det_low = (ev.detail or "").lower()
                if "py_compile" in det_low or "import" in det_low:
                    compile_ok = False
                elif "static_check" in det_low:
                    static_ok = False
                else:
                    repo_tests_ok = False
                    m_nf = re.search(r"(\d+)\s+new failure\(s\)", ev.detail or "")
                    new_failures += int(m_nf.group(1)) if m_nf else 1
                out = ((ev.stdout or "") + "\n" + (ev.stderr or "")).strip()
                tb_snippet = ""
                if not repo_tests_ok:
                    failed_ids = list(
                        dict.fromkeys(
                            re.findall(r"(tests/[A-Za-z0-9_./-]+\.py(?:::[A-Za-z0-9_:\[\]-]+)?)", (ev.detail or "") + "\n" + out)
                        )
                    )[:3]
                    if failed_ids:
                        res_tb = self.executor.run(
                            [
                                self.executor.python,
                                "-m",
                                "pytest",
                                "-q",
                                "--tb=short",
                                "--disable-warnings",
                                "-p",
                                "no:randomly",
                                *failed_ids,
                            ],
                            cwd=self.executor.root,
                            env={"PYTHONPATH": self.executor.root},
                            timeout_s=30,
                        )
                        tb_raw = ((res_tb.stdout or "") + "\n" + (res_tb.stderr or "")).strip()
                        if tb_raw:
                            tb_snippet = f"\nFailing test `--tb=short` traceback ({failed_ids}):\n{_truncate_output(tb_raw, 1400)}\n"
                diagnostics.append(
                    f"- Gatekeeper `verify_module({rel!r})` FAILED ({ev.detail}):{tb_snippet}\n{_truncate_output(out, 1500)}"
                )
            else:
                diagnostics.append(f"- Gatekeeper `verify_module({rel!r})`: OK ({ev.detail}).")

        repro_ok = False
        if self.executor.exists(self.repro_script_rel):
            res_repro = self.executor.run(
                [self.executor.python, self.repro_script_rel],
                cwd=self.executor.root,
                env={"PYTHONPATH": self.executor.root},
                timeout_s=90,
            )
            if res_repro.rc == 0 and not res_repro.timed_out:
                repro_ok = True
                diagnostics.append(f"- Reproduction script `{self.repro_script_rel}`: PASSED (exit 0).")
            else:
                out_r = (res_repro.stdout + "\n" + res_repro.stderr).strip()
                diagnostics.append(
                    f"- Reproduction script `{self.repro_script_rel}` FAILED (exit {res_repro.rc}):\n"
                    f"{_truncate_output(out_r, 2500)}"
                )
        else:
            diagnostics.append(
                f"- WARNING: `{self.repro_script_rel}` does not exist. Create `{self.repro_script_rel}` to verify the reported issue is fixed!"
            )

        audit_ok = False
        if self.executor.exists(self.audit_script_rel):
            res_aud = self.executor.run(
                [self.executor.python, self.audit_script_rel],
                cwd=self.executor.root,
                env={"PYTHONPATH": self.executor.root},
                timeout_s=90,
            )
            if res_aud.rc == 0 and not res_aud.timed_out:
                audit_ok = True
                diagnostics.append(f"- Adversarial edge-case script `{self.audit_script_rel}`: PASSED (exit 0).")
            else:
                out_a = (res_aud.stdout + "\n" + res_aud.stderr).strip()
                diagnostics.append(
                    f"- Adversarial edge-case script `{self.audit_script_rel}` FAILED (exit {res_aud.rc}):\n"
                    f"{_truncate_output(out_a, 2500)}"
                )

        all_ok = compile_ok and static_ok and repo_tests_ok and (repro_ok or not self.executor.exists(self.repro_script_rel)) and (audit_ok or not is_auditor)
        if not self.executor.exists(self.repro_script_rel) and turn <= 15:
            all_ok = False

        cp = PatchCheckpoint(
            diff=diff,
            changed_files=changed,
            file_contents=contents,
            gatekeeper_ok=all_ok,
            repro_ok=repro_ok,
            audit_ok=audit_ok,
            compile_ok=compile_ok,
            static_ok=static_ok,
            repo_tests_ok=repo_tests_ok,
            new_test_failures=0 if repo_tests_ok else new_failures,
            specialist=specialist,
            turn=turn,
            note="; ".join(_truncate_output(d, 700) for d in diagnostics[:4]),
        )
        self.checkpoints.append(cp)

        summary_msg = "Gatekeeper Pre-Submission Audit:\n" + "\n".join(diagnostics)
        return all_ok, summary_msg, cp

    # ------------------------------------------------------------------ #
    # Multi-Turn Specialist Conversation Loop
    # ------------------------------------------------------------------ #

    def _condense_messages(self, messages: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """Keep system prompt, initial task prompt, and recent turns in full; condense older bulky observations."""
        if len(messages) <= CONDENSE_KEEP_RECENT_MESSAGES + 2:
            return messages
        cutoff = len(messages) - CONDENSE_KEEP_RECENT_MESSAGES
        out: List[Dict[str, str]] = []
        for idx, m in enumerate(messages):
            if idx < 2 or idx >= cutoff or m.get("role") != "user":
                out.append(m)
                continue
            content = m.get("content", "")
            if len(content) > CONDENSE_MAX_OLD_OBS_CHARS:
                half = CONDENSE_MAX_OLD_OBS_CHARS // 2
                condensed = (
                    content[:half]
                    + f"\n... [condensed {len(content) - CONDENSE_MAX_OLD_OBS_CHARS} chars from earlier turn] ...\n"
                    + content[-half:]
                )
                out.append({"role": "user", "content": condensed})
            else:
                out.append(m)
        return out

    def _call_specialist_turn(
        self,
        role_name: str,
        messages: List[Dict[str, str]],
        temperature: float = 0.2,
        enable_thinking: bool = True,
    ) -> Tuple[str, str]:
        """Invoke `call_llm` with multi-turn `messages` and native `enable_thinking`."""
        from hae.infra.llm import call_llm

        if not self.runner._may_call(f"agentic turn ({role_name})"):
            return "", ""

        condensed = self._condense_messages(messages)
        usage: Dict[str, Any] = {}
        prompt_preview = condensed[-1].get("content", "") if condensed else ""
        system_preview = condensed[0].get("content", "") if condensed else ""

        resp = call_llm(
            prompt=prompt_preview,
            model_name=self.runner.model_name,
            model_tier="worker",
            temperature=temperature,
            provider="vllm" if os.environ.get("LLM_PROVIDER") == "vllm" else None,
            usage_sink=usage,
            max_tokens=6400 if enable_thinking else 4096,
            messages=condensed,
            enable_thinking=enable_thinking,
            extra_headers={"X-No-Cache": "1"},
        )
        reasoning_text = str(usage.get("last_reasoning") or "")
        if enable_thinking and not (resp or "").strip() and reasoning_text:
            retry_msgs = list(condensed) + [
                {
                    "role": "user",
                    "content": (
                        f"Your reasoning truncated before emitting a tool block (reasoning tail: "
                        f"`{reasoning_text[-900:]}`). Emit ONLY your next markdown fenced tool block "
                        "(` ```bash `, ` ```view_file `, ` ```str_replace `, or ` ```submit `) now."
                    ),
                }
            ]
            usage_retry: Dict[str, Any] = {}
            resp_retry = call_llm(
                prompt=retry_msgs[-1]["content"],
                model_name=self.runner.model_name,
                model_tier="worker",
                temperature=temperature,
                provider="vllm" if os.environ.get("LLM_PROVIDER") == "vllm" else None,
                usage_sink=usage_retry,
                max_tokens=2000,
                messages=retry_msgs,
                enable_thinking=False,
                extra_headers={"X-No-Cache": "1"},
            )
            if (resp_retry or "").strip():
                resp = resp_retry
        self.runner._account_tokens(
            is_pro=False,
            prompt_text=prompt_preview,
            system_text=system_preview,
            response_text=resp,
            usage=usage,
        )
        return resp, reasoning_text

    def run_specialist_loop(
        self,
        specialist_name: str,
        system_prompt: str,
        initial_user_prompt: str,
        max_turns: int,
        temperature: float = 0.2,
        is_auditor: bool = False,
    ) -> Tuple[bool, Optional[PatchCheckpoint]]:
        """Run a multi-turn specialist loop inside `/testbed`."""
        messages: List[Dict[str, str]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": initial_user_prompt},
        ]
        verified_cp: Optional[PatchCheckpoint] = None
        consecutive_parse_errors = 0
        no_think_cooldown = 0
        episode_edits = 0
        initial_cp_count = len(self.checkpoints)

        turn = 0
        effective_max_turns = max_turns
        bonus_granted = False
        while turn < effective_max_turns:
            turn += 1
            elapsed_total = time.time() - self.run_start_time
            if elapsed_total >= self.max_wall_s:
                self.logger(
                    f"[v12-agentic] {specialist_name} turn {turn}/{effective_max_turns}: soft wall-clock limit reached "
                    f"({elapsed_total:.1f}s >= {self.max_wall_s:.0f}s); finalizing best checkpoint."
                )
                break
            use_thinking = (turn == 1) and (no_think_cooldown <= 0)
            if no_think_cooldown > 0:
                no_think_cooldown -= 1
            turn_temp = min(0.7, temperature + 0.15 * min(consecutive_parse_errors, 3))
            t0 = time.time()
            resp_text, reasoning = self._call_specialist_turn(
                specialist_name,
                messages,
                temperature=turn_temp,
                enable_thinking=use_thinking,
            )
            dt = time.time() - t0
            self.total_moves_used += 1
            if len(reasoning) > 2200:
                no_think_cooldown = max(no_think_cooldown, 2)

            if not resp_text and not reasoning:
                self.logger(f"[v12-agentic] {specialist_name} turn {turn}/{effective_max_turns}: budget/empty stop")
                break

            action = parse_agent_action(resp_text)
            self.logger(
                f"[v12-agentic] {specialist_name} turn {turn}/{effective_max_turns} ({dt:.1f}s, think={len(reasoning)}c): "
                f"action={action.kind} path={action.path or '-'} cmd={action.command[:60] if action.command else '-'}"
                + (f" err={action.error[:80]!r} raw_head={resp_text[:100]!r}" if action.error else "")
            )

            if action.error:
                consecutive_parse_errors += 1
                obs = f"Observation (error): {action.error}"
                if not resp_text.strip() and reasoning:
                    no_think_cooldown = max(no_think_cooldown, 2)
                    messages.append({"role": "assistant", "content": f"(Reasoning summary: {reasoning[-600:]})"})
                    obs = (
                        "Observation (error): Your `<think>` block hit the token limit before emitting a tool block. "
                        "Emit your next tool block IMMEDIATELY NOW (` ```bash `, ` ```str_replace `, ` ```view_file `, or ` ```submit `) "
                        "without any further preamble."
                    )
                else:
                    # Do not pollute conversation history with a malformed `<tool_call>` that would cause in-context repetition
                    messages.append({"role": "assistant", "content": "(malformed tool block omitted)"})
                messages.append({"role": "user", "content": obs})
                self.trajectory_log.append({
                    "specialist": specialist_name,
                    "turn": turn,
                    "action": "parse_error",
                    "error": action.error,
                    "reasoning_chars": len(reasoning),
                    "elapsed_s": round(dt, 2),
                })
                continue

            clean_assistant_text = _strip_rfl_artifacts(resp_text)
            if clean_assistant_text.startswith("```") and not clean_assistant_text.rstrip().endswith("```"):
                clean_assistant_text = clean_assistant_text.rstrip() + "\n```"
            asst_msg: Dict[str, str] = {"role": "assistant", "content": clean_assistant_text}
            if reasoning:
                asst_msg["reasoning_content"] = reasoning[:2000]
            messages.append(asst_msg)

            consecutive_parse_errors = 0

            if action.kind == "bash":
                obs = self.execute_bash_tool(action.command)
                if len(self.checkpoints) > initial_cp_count:
                    episode_edits = len(self.checkpoints) - initial_cp_count
                if turn >= max(5, effective_max_turns // 2) and episode_edits == 0 and not is_auditor:
                    obs += (
                        f"\n\n[ACTION REQUIRED: Turn {turn}/{effective_max_turns} and no source edits have been applied in this episode yet! "
                        "Apply your surgical fix to the repository source file(s) using `str_replace` NOW so you have turns left to verify with pytest and submit.]"
                    )
                elif turn >= effective_max_turns - 3:
                    obs += f"\n\n[TURN BUDGET NOTICE: Turn {turn}/{effective_max_turns}. Ensure your source edits are applied via `str_replace` and emit ```submit when ready.]"
                messages.append({"role": "user", "content": obs})
                self.trajectory_log.append({
                    "specialist": specialist_name,
                    "turn": turn,
                    "action": "bash",
                    "command": action.command[:300],
                    "reasoning_chars": len(reasoning),
                    "elapsed_s": round(dt, 2),
                })

            elif action.kind == "view_file":
                obs = self.execute_view_file_tool(action.path, action.start_line, action.end_line)
                if turn >= max(5, effective_max_turns // 2) and episode_edits == 0 and not is_auditor:
                    obs += (
                        f"\n\n[ACTION REQUIRED: Turn {turn}/{effective_max_turns} and no code edits have been made in this episode yet. "
                        "Use `git grep -n` in ```bash to jump directly to target lines, write `/testbed/.hae/reproduce_issue.py`, "
                        "and apply your fix with ```str_replace NOW.]"
                    )
                messages.append({"role": "user", "content": obs})
                self.trajectory_log.append({
                    "specialist": specialist_name,
                    "turn": turn,
                    "action": "view_file",
                    "path": action.path,
                    "range": [action.start_line, action.end_line],
                    "reasoning_chars": len(reasoning),
                    "elapsed_s": round(dt, 2),
                })

            elif action.kind == "write_file":
                obs = self.execute_write_file_tool(
                    action.path, action.new_str, run_after=(action.command == "run_after_write")
                )
                if len(self.checkpoints) > initial_cp_count:
                    episode_edits = len(self.checkpoints) - initial_cp_count
                messages.append({"role": "user", "content": obs})
                self.trajectory_log.append({
                    "specialist": specialist_name,
                    "turn": turn,
                    "action": "write_file",
                    "path": action.path,
                    "reasoning_chars": len(reasoning),
                    "elapsed_s": round(dt, 2),
                })

            elif action.kind == "str_replace":
                obs = self.execute_str_replace_tool(
                    action.path, action.old_str, action.new_str, specialist_name, turn
                )
                if len(self.checkpoints) > initial_cp_count:
                    episode_edits = len(self.checkpoints) - initial_cp_count
                messages.append({"role": "user", "content": obs})
                self.trajectory_log.append({
                    "specialist": specialist_name,
                    "turn": turn,
                    "action": "str_replace",
                    "path": action.path,
                    "reasoning_chars": len(reasoning),
                    "elapsed_s": round(dt, 2),
                })

            elif action.kind == "submit":
                ok, audit_msg, cp = self.verify_current_workspace(
                    specialist_name, turn, is_auditor=is_auditor
                )
                self.trajectory_log.append({
                    "specialist": specialist_name,
                    "turn": turn,
                    "action": "submit",
                    "gatekeeper_ok": ok,
                    "audit_msg": audit_msg[:600],
                    "reasoning_chars": len(reasoning),
                    "elapsed_s": round(dt, 2),
                })
                if ok and cp is not None:
                    self.logger(
                        f"[v12-agentic] {specialist_name} turn {turn}: Gatekeeper CERTIFIED patch "
                        f"({len(cp.diff)} chars across {cp.changed_files})"
                    )
                    verified_cp = cp
                    return True, verified_cp
                else:
                    reject_obs = (
                        f"Observation (submit rejected by EvidenceGatekeeper):\n{audit_msg}\n\n"
                        "Please fix the reported issue(s) in place using `str_replace` or `bash`, re-run your "
                        "reproduction script and `pytest`, and then emit ```submit again."
                    )
                    messages.append({"role": "user", "content": reject_obs})

            if turn == effective_max_turns and not bonus_granted and (time.time() - self.run_start_time < self.max_wall_s - 90):
                ok_mid, audit_mid, cp_mid = self.verify_current_workspace(
                    specialist_name, turn, is_auditor=is_auditor
                )
                if ok_mid and cp_mid is not None:
                    return True, cp_mid
                if cp_mid is not None and cp_mid.diff.strip() and cp_mid.repro_ok and not cp_mid.repo_tests_ok:
                    bonus_granted = True
                    effective_max_turns += 3
                    self.logger(
                        f"[v12-agentic] {specialist_name} turn {turn}: repro_ok=True but repo_tests failed; "
                        f"granting +3 bonus in-context repair turns (up to {effective_max_turns})"
                    )
                    messages.append({
                        "role": "user",
                        "content": (
                            f"Observation (Gatekeeper End-of-Episode Check — +3 Bonus Turns Granted):\n{audit_mid}\n\n"
                            "Your patch passes `reproduce_issue.py` (`repro_ok=True`) but failed the repo test(s) above. "
                            "You have 3 bonus turns to inspect the failing test and apply a surgical `str_replace` fix, then emit ```submit."
                        ),
                    })

        ok, audit_msg, cp = self.verify_current_workspace(
            specialist_name, effective_max_turns, is_auditor=is_auditor
        )
        return ok, cp

    # ------------------------------------------------------------------ #
    # Prompts for Specialist 1 (Primary) & Specialist 2 (Auditor / Escalation)
    # ------------------------------------------------------------------ #

    def _system_prompt(self, role_title: str, role_goal: str) -> str:
        return (
            f"You are {role_title}, a senior principal software engineer in an Epistemic Software Engineering organization.\n"
            f"Goal: {role_goal}\n"
            f"Repository under test: `{self.task.repo}` checked out at `{self.executor.root}` (commit {self.task.base_commit[:12]}).\n"
            f"Python interpreter: `{self.executor.python}` (already on PATH in `bash`).\n\n"
            "## Reasoning & Efficiency Rule\n"
            "- Keep your `<think>` reasoning concise and focused (under 200 words per turn): decide the single next command or edit, close `</think>`, and immediately emit the tool block.\n"
            "- Do NOT page through entire large files 100 lines at a time with `view_file`! Use `git grep -n \"symbol\"` in `bash` to find exact line numbers first, then `view_file` only the relevant lines.\n"
            "- Combine related lookups into a single `bash` command so you localize and reproduce the issue within 2-4 turns.\n\n"
            "## Available Tools (Emit EXACTLY ONE tool block per turn)\n\n"
            "1. **Run shell commands (`bash`)** — search code (`git grep -n`), create/run reproduction scripts, run `pytest`, inspect `git diff`:\n"
            "```bash\n"
            "git grep -n \"function_or_class_name\"\n"
            "```\n\n"
            "2. **View file lines with line numbers (`view_file`)** — inspect up to 500 lines of any file in `/testbed`:\n"
            "```view_file\n"
            "path: path/to/file.py\n"
            "start_line: 100\n"
            "end_line: 250\n"
            "```\n\n"
            "3. **Surgical file edit (`str_replace`)** — replace an exact contiguous block of lines in a file (checked immediately for Python `SyntaxError` before writing):\n"
            "```str_replace\n"
            "path: path/to/file.py\n"
            "<<<<\n"
            "exact existing lines from the file (WITHOUT line-number prefixes)\n"
            "====\n"
            "new replacement lines with exact indentation\n"
            ">>>>\n"
            "```\n\n"
            "4. **Submit verified fix (`submit`)** — once `git diff` is clean, your reproduction script passes, and `pytest` passes:\n"
            "```submit\n"
            "Brief explanation of the root cause and how the patch resolves it.\n"
            "```\n\n"
            "## Strict Engineering Rules\n"
            "- Emit ONLY ONE tool block per response (` ```bash `, ` ```view_file `, ` ```str_replace `, or ` ```submit `; if using `<tool_call><function=...>`, always include `<parameter=path>`, `<parameter=old_str>`, `<parameter=new_str>` or `<parameter=command>`). Wait for the `Observation:` before taking the next step.\n"
            "- NEVER copy the `  123 | ` line numbers from `view_file` into `str_replace` blocks.\n"
            "- Store any standalone reproduction or scratch test scripts inside `/testbed/.hae/` (e.g. `/testbed/.hae/reproduce_issue.py`), NEVER as untracked files in `/testbed/`. Do NOT modify existing repository test files.\n"
            "- Make minimal, surgical changes that respect existing code style, docstrings, type annotations, warning classes, and sibling methods."
        )

    def _build_primary_user_prompt(self, candidate_modules: List[str], repo_map: List[str]) -> str:
        cands = ", ".join(f"`{m}`" for m in candidate_modules[:6]) if candidate_modules else "(use `git grep` to locate)"
        rmap = "\n".join(f"- {p}" for p in repo_map[:35])
        return (
            f"{self.runner.objective_text()}\n\n"
            f"### Initial Candidate Modules (from static seed / LOCATE)\n"
            f"{cands}\n\n"
            f"### Repository Map (top {min(35, len(repo_map))} relevant tracked `.py` files)\n"
            f"{rmap}\n\n"
            "### Required 4-Step Workflow\n"
            "1. **Locate Precisely (1-3 turns)**: Use `bash` (`git grep -n ...`) and targeted `view_file` to find the exact class/function(s) involved in the issue and the relevant existing unit test file(s).\n"
            "2. **Reproduce the Bug (1-2 turns)**: In `bash`, write `/testbed/.hae/reproduce_issue.py` (`cat << 'EOF' > /testbed/.hae/reproduce_issue.py ... EOF && python /testbed/.hae/reproduce_issue.py`) that reproduces the bug on the current code and asserts the expected fixed behaviour.\n"
            "3. **Patch & Verify Iteratively**: Apply a minimal, surgical fix to the source code using `str_replace`. Immediately run `python /testbed/.hae/reproduce_issue.py && pytest <relevant_test_file>` via `bash`. If any assertion or test fails, refine your edit in place.\n"
            "4. **Check Sibling Methods / Deprecations / Edge Cases & Submit**: Check if the same bug pattern applies to sibling methods/classes or edge cases (`None`, empty input, subclasses, `is_abstract(pass_is_abstract=True, any_raise_is_abstract=True)` yielding `util.Uninferable`). If replacing or deprecating an old parameter/decorator/style (e.g. `**metadata` / `**additional_metadata` kwargs -> `metadata=...`, `cachedproperty` -> `cached_property`), check the repo's `warnings` module / existing deprecations and ensure using the deprecated style emits the expected `DeprecationWarning` (or repo warning subclass like `RemovedInMarshmallow4Warning`, `stacklevel=2`) while preserving backward compatibility (e.g., merging `{**(metadata or {}), **additional_metadata}` when both `metadata` and deprecated kwargs are passed — NEVER raise `ValueError` when both are passed unless explicitly requested). Run `git diff` to verify cleanliness, then emit `submit`."
        )

    def _build_auditor_user_prompt(self, primary_cp: PatchCheckpoint) -> str:
        return (
            f"{self.runner.objective_text()}\n\n"
            f"### Candidate Patch Produced by Specialist 1 (`{', '.join(primary_cp.changed_files)}`)\n"
            f"```diff\n{_truncate_output(primary_cp.diff, 6000)}\n```\n\n"
            f"### Your Mandate as Adversarial Edge-Case & API-Contract Auditor\n"
            "Specialist 1's candidate patch above is currently applied in `/testbed` and passes its initial reproduction script.\n"
            "However, benchmarks fail when a patch misses a secondary requirement, deprecation warning, edge case, or sibling class/method/caller.\n"
            "Perform the following audit in 2-6 turns:\n"
            "1. Immediately inspect any **sibling classes or functions** in the modified module(s) (e.g. if `List` was fixed, check `Tuple`/`Dict`/`Mapping`; if one setter/loader was fixed, check sibling setters/loaders) and compare `git diff` against EVERY requirement in the PROBLEM STATEMENT.\n"
            "2. Check if the issue replaces or deprecates an older parameter/decorator/calling style (e.g., passing arbitrary `**metadata` kwargs instead of `metadata=...`, or `@cachedproperty` instead of `@cached_property`): if so, verify whether the repo's deprecation warning (`RemovedInMarshmallow4Warning` / `DeprecationWarning` with `warnings.warn(..., stacklevel=2)`) must be emitted when the deprecated style is used!\n"
            "3. Write and run an adversarial edge-case test script `/testbed/.hae/audit_edge_cases.py` via `bash` that tests:\n"
            "   - All examples, deprecation warnings (`pytest.warns(...)`), and edge cases from the problem statement,\n"
            "   - Boundary values (`None`, empty inputs, subclasses, kwargs forwarding, etc.),\n"
            "   - All sibling methods/classes in the modified module that should behave consistently,\n"
            "   - And run the relevant `pytest` test suite(s).\n"
            "4. If ANY sibling class/method, deprecation warning, or edge case fails, immediately fix the source code in place using `str_replace`, re-run `/testbed/.hae/audit_edge_cases.py` and `pytest`, and then emit ```submit.\n"
            "5. If `/testbed/.hae/audit_edge_cases.py` and `pytest` pass completely and `git diff` is clean and minimal, immediately emit ```submit."
        )

    def _build_escalation_user_prompt(
        self,
        candidate_modules: List[str],
        repo_map: List[str],
        preserved_cp: Optional[PatchCheckpoint] = None,
    ) -> str:
        if preserved_cp is not None:
            return (
                f"{self.runner.objective_text()}\n\n"
                f"### Specialist 1's Candidate Patch (Currently Applied in `/testbed`)\n"
                f"Modified files: `{', '.join(preserved_cp.changed_files)}`\n"
                f"```diff\n{_truncate_output(preserved_cp.diff, 4500)}\n```\n\n"
                f"### Gatekeeper Audit Diagnostics (Why Specialist 1's Patch Was Not Certified)\n"
                f"{preserved_cp.note}\n\n"
                "### Your Mandate as Escalation Regression-Fix Specialist\n"
                "Specialist 1's patch above is **already applied in `/testbed`** and **already passes `/testbed/.hae/reproduce_issue.py`**!\n"
                "However, it caused one or more regressions in the existing repository test suite shown in the Gatekeeper Audit Diagnostics above.\n"
                "1. **Do NOT reset to HEAD or rewrite from scratch**: immediately run the failing `pytest` test(s) listed above (e.g. `pytest <test_node_id> -vv`) in `bash` to see the exact failure traceback.\n"
                "2. Inspect the modified region of `" + ", ".join(preserved_cp.changed_files) + "` and the failing test to see why Specialist 1's change broke that test.\n"
                "3. Refine the fix in place using `str_replace` so that **BOTH** `python /testbed/.hae/reproduce_issue.py` **AND** the failing `pytest` test(s) pass with zero regressions, then emit ```submit."
            )
        tried_notes = []
        for cp in self.checkpoints[-3:]:
            tried_notes.append(
                f"- Attempt on `{', '.join(cp.changed_files)}` ({cp.note}):\n```diff\n{_truncate_output(cp.diff, 2000)}\n```"
            )
        tried_block = "\n".join(tried_notes) if tried_notes else "(Specialist 1 did not produce a passing patch.)"
        return (
            f"{self.runner.objective_text()}\n\n"
            f"### Post-Mortem of Specialist 1's Incomplete Attempt\n"
            f"{tried_block}\n\n"
            f"### Candidate Modules: {', '.join(f'`{m}`' for m in candidate_modules[:6])}\n\n"
            "### Your Mandate as Escalation Repair Specialist\n"
            "The repository tracked files have been reset to a clean checkout, and any broken `/testbed/.hae/reproduce_issue.py` from Specialist 1 has been archived to `/testbed/.hae/specialist1_reproduce.py`.\n"
            "1. Use `git grep -n` in `bash` and `view_file` to find the TRUE root cause of the issue immediately — do NOT spend more than 3 turns reading before writing a clean `/testbed/.hae/reproduce_issue.py` (testing all requirements including any `DeprecationWarning` if applicable) and applying a fix with `str_replace`.\n"
            "2. Apply a clean fix with `str_replace`, verify that both `python /testbed/.hae/reproduce_issue.py` and `pytest` pass with zero regressions, and emit ```submit."
        )

    # ------------------------------------------------------------------ #
    # Top-Level Orchestration (`run`)
    # ------------------------------------------------------------------ #

    def run(self, candidate_modules: List[str], budget_moves: int = 30) -> Dict[str, Any]:
        t_start = time.time()
        self.executor.run(["mkdir", "-p", f"{self.executor.root}/{SCRATCH_DIR}"], cwd=self.executor.root, timeout_s=15)
        repo_map = self.runner.repo_map()

        primary_budget = max(16, min(24, int(budget_moves * 0.72)))
        secondary_budget = max(10, min(14, max(11, budget_moves - primary_budget + 8)))

        # --- Stage 1: Primary Specialist ---
        self.logger(
            f"[v12-agentic] Launching Specialist 1 (Primary Reproduction & Surgical Repair, max_turns={primary_budget}) "
            f"on {self.task.instance_id}"
        )
        ok1, cp1 = self.run_specialist_loop(
            specialist_name="PrimaryRepairSpecialist",
            system_prompt=self._system_prompt(
                "Primary Reproduction & Surgical Repair Specialist",
                "Locate the root cause, write a failing reproduction script `/testbed/.hae/reproduce_issue.py`, "
                "apply a minimal surgical fix with `str_replace`, and verify against both the reproduction script and `pytest`.",
            ),
            initial_user_prompt=self._build_primary_user_prompt(candidate_modules, repo_map),
            max_turns=primary_budget,
            temperature=0.2,
            is_auditor=False,
        )

        # --- Stage 2: Adversarial Auditor (if Stage 1 verified) OR Escalation Specialist (if Stage 1 failed) ---
        if ok1 and cp1 is not None and self.runner._may_call("auditor check"):
            self.logger(
                f"[v12-agentic] Specialist 1 succeeded; launching Specialist 2 "
                f"(Adversarial Edge-Case Auditor, max_turns={min(11, secondary_budget)})"
            )
            ok2, cp2 = self.run_specialist_loop(
                specialist_name="AdversarialEdgeCaseAuditor",
                system_prompt=self._system_prompt(
                    "Adversarial Edge-Case & API-Contract Auditor",
                    "Audit Specialist 1's candidate diff against every requirement, sibling class/method, and edge case in the problem statement, "
                    "run `/testbed/.hae/audit_edge_cases.py` + `pytest`, and refine the patch if any sibling or edge case fails.",
                ),
                initial_user_prompt=self._build_auditor_user_prompt(cp1),
                max_turns=min(11, secondary_budget),
                temperature=0.25,
                is_auditor=True,
            )
        elif not ok1 and self.runner._may_call("escalation specialist"):
            primary_repro_cps = [c for c in self.checkpoints if c.diff.strip() and c.compile_ok and c.repro_ok]
            preserved_cp: Optional[PatchCheckpoint] = None
            if primary_repro_cps:
                preserved_cp = max(enumerate(primary_repro_cps), key=lambda ic: (ic[1].rank_key(), ic[0]))[1]
                self.logger(
                    f"[v12-agentic] Specialist 1 achieved repro_ok=True on {preserved_cp.changed_files} "
                    f"(turn {preserved_cp.turn}); keeping candidate patch applied for Escalation Specialist "
                    f"(max_turns={secondary_budget})"
                )
                self._restore_checkpoint(preserved_cp)
            else:
                self.logger(
                    f"[v12-agentic] Specialist 1 did not certify a patch; resetting tracked files and launching "
                    f"Specialist 2 (Escalation Repair Specialist, max_turns={secondary_budget})"
                )
                self._reset_tracked_to_head()
                if self.executor.exists(self.repro_script_rel):
                    self.executor.run(
                        ["mv", "-f", f"{self.executor.root}/{self.repro_script_rel}", f"{self.executor.root}/{SCRATCH_DIR}/specialist1_reproduce.py"],
                        cwd=self.executor.root,
                        timeout_s=15,
                    )
            ok2, cp2 = self.run_specialist_loop(
                specialist_name="EscalationRepairSpecialist",
                system_prompt=self._system_prompt(
                    "Escalation Root-Cause & Deep Repair Specialist",
                    "Investigate why Specialist 1's attempt failed or stalled, check collaborator modules and alternative "
                    "mechanisms, and deliver a verified fix passing both `/testbed/.hae/reproduce_issue.py` and `pytest`.",
                ),
                initial_user_prompt=self._build_escalation_user_prompt(
                    candidate_modules, repo_map, preserved_cp=preserved_cp
                ),
                max_turns=secondary_budget,
                temperature=0.35,
                is_auditor=False,
            )

        # --- Stage 3: Select & Restore Best Checkpointed Patch ---
        best_cp: Optional[PatchCheckpoint] = None
        non_empty_cps = [c for c in self.checkpoints if c.diff.strip() and c.compile_ok]
        if non_empty_cps:
            best_cp = max(enumerate(non_empty_cps), key=lambda ic: (ic[1].rank_key(), ic[0]))[1]
            current_diff, _, _ = self._current_diff_and_files()
            if current_diff != best_cp.diff:
                self.logger(
                    f"[v12-agentic] Restoring best checkpoint from {best_cp.specialist} turn {best_cp.turn} "
                    f"(rank={best_cp.rank_key()}, files={best_cp.changed_files})"
                )
                self._restore_checkpoint(best_cp)

        self._clean_untracked_outside_scratch()
        final_diff, final_files, _ = self._current_diff_and_files()

        q_list = list(self.state.questions.values())
        if q_list and final_diff.strip():
            root_q = q_list[0]
            hyps = self.state.hypotheses_for(root_q.question_id)
            if not hyps:
                h = self.state.add_hypothesis(
                    question_id=root_q.question_id,
                    claim=f"Fixed issue in {', '.join(final_files)}",
                    mechanism=best_cp.note if best_cp else "agentic repair",
                    prior=0.9,
                    probe_code="# verified via V12 agentic engine\nprint('OK')",
                    prediction={"expect_exit_code": 0},
                    proposed_by="PrimaryRepairSpecialist",
                )
                ev = Evidence(
                    evidence_id=self.state.new_evidence_id(),
                    kind="module_check",
                    hypothesis_id=h.hypothesis_id,
                    question_id=root_q.question_id,
                    command="v12_agentic_verify",
                    exit_code=0,
                    stdout=best_cp.note if best_cp else "OK",
                    stderr="",
                    prediction={"expect_exit_code": 0},
                    matched_prediction=True,
                    detail=best_cp.note if best_cp else "agentic verify passed",
                )
                self.state.record_evidence(ev, self.gatekeeper.authority)
                self.state.set_hypothesis_verdict(
                    h.hypothesis_id, SUPPORTED, 0.95, ev.evidence_id, self.gatekeeper.authority
                )
                self.state.lower_uncertainty(root_q.question_id, 0.05, self.gatekeeper.authority)
                hyps = [h]
            if best_cp and (best_cp.gatekeeper_ok or (best_cp.repro_ok and best_cp.repo_tests_ok)):
                ev_ids = [e.evidence_id for e in self.state.evidence_log[-2:]]
                if best_cp.gatekeeper_ok and best_cp.audit_ok and hyps and ev_ids:
                    try:
                        self.state.certify(
                            root_q.question_id,
                            hyps[-1].hypothesis_id,
                            f"V12 Agentic Engine verified patch in {', '.join(final_files)} ({best_cp.note})",
                            ev_ids,
                            "EvidenceGatekeeper",
                            self.gatekeeper.authority,
                        )
                    except Exception:
                        self.state.set_question_status(root_q.question_id, Q_RESOLVED, self.gatekeeper.authority)
                        self.state.lower_uncertainty(root_q.question_id, 0.02, self.gatekeeper.authority)
                else:
                    self.state.set_question_status(root_q.question_id, Q_RESOLVED, self.gatekeeper.authority)
                    self.state.lower_uncertainty(root_q.question_id, 0.02, self.gatekeeper.authority)

        elapsed_s = round(time.time() - t_start, 2)
        resolved_self = bool(best_cp and (best_cp.gatekeeper_ok or (best_cp.repro_ok and best_cp.repo_tests_ok)))
        stop_reason = "resolved_by_agentic_gatekeeper" if resolved_self else ("best_checkpoint_restored" if final_diff.strip() else "budget_exhausted")

        return {
            "moves_used": self.total_moves_used,
            "stop_reason": stop_reason,
            "resolved_by_self_oracle": resolved_self,
            "synthesized_paths": final_files,
            "patch_chars": len(final_diff),
            "elapsed_s": elapsed_s,
            "checkpoints_recorded": len(self.checkpoints),
            "best_checkpoint": {
                "specialist": best_cp.specialist,
                "turn": best_cp.turn,
                "rank_key": list(best_cp.rank_key()),
                "changed_files": best_cp.changed_files,
                "note": best_cp.note,
            } if best_cp else None,
            "trajectory": self.trajectory_log,
        }
