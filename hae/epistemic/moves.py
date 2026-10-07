"""Typed moves: the only things a System 1 agent may say to the ledger.

A proposer does not write beliefs; it emits one of six typed proposals,
each of which the search loop turns into a ledger operation:

  * `QuestionProposal`    -> `EpistemicState.add_question`
  * `HypothesisProposal`  -> `EpistemicState.add_hypothesis` (always UNVERIFIED)
  * `ExperimentProposal`  -> `EvidenceGatekeeper.run_experiment`
  * `CodeDiffProposal`    -> a workspace write, then `verify_module`
  * `SynthesisProposal`   -> the decision to turn a SUPPORTED hypothesis into code
  * `RecruitProposal`     -> `OrgState.recruit` (V8: a new specialist joins the
                             live team; `MOVE_RECRUIT_SPECIALIST` in mcts.py)

The xgrammar schemas below are what the vLLM constrained decoder enforces
when a proposer is asked for questions, hypotheses or a synthesis. Each
hypothesis must carry a *probe* (a short Python script) and a *prediction*
(what the probe prints or exits with if the hypothesis is true). A hypothesis
without a prediction is unfalsifiable and the gatekeeper will not support it.

Synthesis is a ladder of packets the runner applies itself, cheapest first:
a `REPAIR_PLAN` (exact lines to replace; `apply_repair_plan`), then a
`FUNCTION_REWRITE` (one definition, replaced whole; `apply_function_rewrite`),
and only then the tool-loop rewrite of the whole module. Each applier refuses
a packet that changes nothing, so a no-op is a verdict rather than a write.

A recruit packet (`V8_RECRUIT_SCHEMA`, `parse_recruit_packet`) is the CEO's
description of ONE missing specialist; like every other packet it is a
proposal: the loop decides whether the role is admitted, and the role's
record is only ever written by gatekeeper verdicts credited to it.
"""

from __future__ import annotations

import ast
import difflib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple

from hae.epistemic.ledger import clamp_prior, mechanism_signature

MOVE_ASK_QUESTION = "ask_question"
MOVE_PROPOSE_HYPOTHESIS = "propose_hypothesis"
MOVE_RUN_EXPERIMENT = "run_experiment"
MOVE_SYNTHESIZE = "synthesize"
MOVE_RECONCILE = "reconcile_oracle"
# V8: the organisation itself is search state. Appends a specialist to the
# live team (`hae.epistemic.org.OrgState`); taken only when the team has
# stalled or surfaced modules nobody covers. See `EpistemicSearchLoop._maybe_recruit`.
MOVE_RECRUIT_SPECIALIST = "recruit_specialist"
MOVE_TYPES = (MOVE_ASK_QUESTION, MOVE_PROPOSE_HYPOTHESIS, MOVE_RUN_EXPERIMENT,
              MOVE_SYNTHESIZE, MOVE_RECONCILE, MOVE_RECRUIT_SPECIALIST)

MAX_HYPOTHESES_PER_PACKET = 4
MAX_QUESTIONS_PER_PACKET = 4


@dataclass
class QuestionProposal:
    text: str
    module: str = ""
    uncertainty: float = 1.0


@dataclass
class HypothesisProposal:
    claim: str
    mechanism: str = ""
    prior: float = 0.5
    probe_code: str = ""
    prediction: Dict[str, Any] = field(default_factory=dict)

    @property
    def signature(self) -> str:
        return mechanism_signature(self.claim, self.mechanism)


@dataclass
class ExperimentProposal:
    hypothesis_id: str
    probe_code: str
    prediction: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CodeDiffProposal:
    path: str
    content: str
    rationale: str = ""


@dataclass
class SynthesisProposal:
    question_id: str
    hypothesis_id: str


@dataclass
class RecruitProposal:
    """The CEO's description of ONE specialist the live team lacks (V8).

    Maps 1:1 onto the persona fields of `hae.epistemic.org.RoleAllele`;
    `domain_tags` are the lower-case tokens the role is matched against
    (module stems, exception classes, techniques), `kind` says which moves
    the role may take (`probe`, `synthesis` or `both`).
    """

    name: str
    goal: str = ""
    backstory: str = ""
    domain_tags: List[str] = field(default_factory=list)
    kind: str = "both"
    rationale: str = ""


# ---------------------------------------------------------------------------
# xgrammar schemas (vLLM constrained decoding; same envelope as the V5 packets)
# ---------------------------------------------------------------------------

_PREDICTION_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "expect_exit_code": {"type": "integer"},
        "expect_stdout_contains": {"type": "string", "maxLength": 120},
    },
    "required": ["expect_exit_code", "expect_stdout_contains"],
    "additionalProperties": False,
}

MAX_PROBE_LINES_SCHEMA = 60

V6_HYPOTHESIS_SCHEMA: Dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "HypothesisSetPacket",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "packet": {"type": "string", "enum": ["HYPOTHESIS_SET"]},
                "question_id": {"type": "string", "maxLength": 16},
                "hypotheses": {
                    "type": "array",
                    "maxItems": MAX_HYPOTHESES_PER_PACKET,
                    "items": {
                        "type": "object",
                        "properties": {
                            "claim": {"type": "string", "maxLength": 200},
                            "mechanism": {"type": "string", "maxLength": 260},
                            "prior": {"type": "number"},
                            # The probe is an ARRAY OF SOURCE LINES, not one string.
                            # Gen 16 pilot (2026-10-06): with `probe_code` as a single
                            # string the grammar forbids raw newlines inside it, and
                            # Qwen3.8 degenerated into a repetition loop
                            # (`import inspect, sys, ..., _base._base._base...`) until
                            # max_tokens on 3 of 4 firms, starving the search of
                            # hypotheses. A line array makes the newline a structural
                            # separator the grammar allows.
                            "probe_lines": {
                                "type": "array",
                                "maxItems": MAX_PROBE_LINES_SCHEMA,
                                "items": {"type": "string", "maxLength": 200},
                            },
                            "prediction": _PREDICTION_SCHEMA,
                        },
                        "required": ["claim", "mechanism", "prior", "probe_lines", "prediction"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["packet", "question_id", "hypotheses"],
            "additionalProperties": False,
        },
    },
}

V6_QUESTION_SCHEMA: Dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "QuestionSetPacket",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "packet": {"type": "string", "enum": ["QUESTION_SET"]},
                "questions": {
                    "type": "array",
                    "maxItems": MAX_QUESTIONS_PER_PACKET,
                    "items": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string", "maxLength": 240},
                            "module": {"type": "string", "maxLength": 60},
                            "uncertainty": {"type": "number"},
                        },
                        "required": ["text", "module", "uncertainty"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["packet", "questions"],
            "additionalProperties": False,
        },
    },
}

# Synthesis as an anchored change (Gen 16 pilot finding #5). Asked for a
# complete module rewrite, the synthesiser re-emitted the current file byte
# for byte on 7 of 9 moves. A REPAIR_PLAN names the function, quotes the
# exact lines to replace (`old_lines`, verbatim from the current module) and
# the lines that replace them (`new_lines`). The runner applies the edit
# itself when the anchor matches exactly once; the LLM rewrite is only the
# fallback. Line arrays for the same reason as `probe_lines`.
MAX_REPAIR_OLD_LINES = 40
MAX_REPAIR_NEW_LINES = 120

V6_REPAIR_PLAN_SCHEMA: Dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "RepairPlanPacket",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "packet": {"type": "string", "enum": ["REPAIR_PLAN"]},
                "hypothesis_id": {"type": "string", "maxLength": 16},
                "function": {"type": "string", "maxLength": 80},
                "rationale": {"type": "string", "maxLength": 300},
                "old_lines": {
                    "type": "array",
                    "maxItems": MAX_REPAIR_OLD_LINES,
                    "items": {"type": "string", "maxLength": 200},
                },
                "new_lines": {
                    "type": "array",
                    "maxItems": MAX_REPAIR_NEW_LINES,
                    "items": {"type": "string", "maxLength": 200},
                },
            },
            "required": ["packet", "hypothesis_id", "function", "rationale", "old_lines", "new_lines"],
            "additionalProperties": False,
        },
    },
}


@dataclass
class RepairPlan:
    """An anchored edit: replace `old_lines` (verbatim) with `new_lines`."""

    function: str
    rationale: str
    old_lines: List[str]
    new_lines: List[str]
    hypothesis_id: str = ""

    @property
    def old_text(self) -> str:
        return "\n".join(self.old_lines)

    @property
    def new_text(self) -> str:
        return "\n".join(self.new_lines)


def parse_repair_plan(raw: str) -> Optional[RepairPlan]:
    """Parses a REPAIR_PLAN packet; None when there is no usable anchor."""
    data = extract_json_object(raw)
    if not data:
        return None
    old = data.get("old_lines")
    new = data.get("new_lines")
    if not isinstance(old, list) or not isinstance(new, list):
        return None
    old_lines = [str(x) for x in old if x is not None]
    new_lines = [str(x) for x in new if x is not None]
    if not any(ln.strip() for ln in old_lines):
        return None
    return RepairPlan(
        function=" ".join(str(data.get("function", "")).split()),
        rationale=" ".join(str(data.get("rationale", "")).split()),
        old_lines=old_lines, new_lines=new_lines,
        hypothesis_id=str(data.get("hypothesis_id", "") or ""),
    )


def _strip_common_indent(lines: List[str]) -> List[str]:
    indents = [len(ln) - len(ln.lstrip()) for ln in lines if ln.strip()]
    cut = min(indents) if indents else 0
    return [ln[cut:] if ln.strip() else "" for ln in lines]


FUZZY_ANCHOR_MIN_RATIO = 0.92     # SequenceMatcher ratio a window must reach to count as the anchor
FUZZY_ANCHOR_MIN_MARGIN = 0.05    # ... and by how much it must beat the best non-overlapping window


def _dedent_rstrip(lines: List[str]) -> List[str]:
    return [ln.rstrip() for ln in _strip_common_indent(list(lines))]


def _closest_windows(src_lines: List[str], old: List[str]) -> Tuple[int, float, float]:
    """(start index, ratio, runner-up ratio) of the window most similar to the anchor.

    Windows are compared as rstripped, dedented text. The runner-up is the best
    window that does NOT overlap the winner: windows shifted by a line or two
    are the same place in the file, not a competing anchor site, and for a
    long anchor they score almost as high as the winner. `quick_ratio` is an
    upper bound on `ratio`, so windows are visited in bound order and the
    quadratic pass stops once no remaining window can matter; the ratios
    returned are exact.
    """
    n = len(old)
    matcher = difflib.SequenceMatcher(None, "", "\n".join(_dedent_rstrip(old)), autojunk=False)
    texts = ["\n".join(_dedent_rstrip(src_lines[i:i + n])) for i in range(len(src_lines) - n + 1)]
    bounds: List[float] = []
    for text in texts:
        matcher.set_seq1(text)
        bounds.append(matcher.quick_ratio())
    order = sorted(range(len(texts)), key=lambda k: (-bounds[k], k))
    exact: Dict[int, float] = {}

    def ratio(k: int) -> float:
        if k not in exact:
            matcher.set_seq1(texts[k])
            exact[k] = matcher.ratio()
        return exact[k]

    best, best_ratio = order[0], -1.0
    for k in order:
        if bounds[k] < best_ratio:
            break
        if ratio(k) > best_ratio:
            best, best_ratio = k, exact[k]
    runner_up = 0.0
    for k in order:
        if bounds[k] < best_ratio - FUZZY_ANCHOR_MIN_MARGIN:
            break  # nothing further down can reach the margin that decides uniqueness
        if abs(k - best) >= n:
            runner_up = max(runner_up, ratio(k))
    return best, best_ratio, runner_up


def apply_repair_plan(source: str, plan: RepairPlan) -> Tuple[Optional[str], str]:
    """Applies the plan to `source` when its anchor is unambiguous.

    Returns `(new_source, note)`; `new_source` is None when the anchor was not
    found or matched more than once. Four match strategies, strictest first:
    exact text; lines compared with trailing whitespace stripped; lines
    compared with their common indentation removed (the replacement is then
    re-indented to the anchor's indentation); and a *fuzzy* window match for
    anchors the model quoted with small drift (a renamed variable, a dropped
    comment, a reflowed argument list): every same-length window is compared
    with the anchor as rstripped, dedented text by
    `difflib.SequenceMatcher(...).ratio()`, and the best window is accepted
    only if its ratio is >= `FUZZY_ANCHOR_MIN_RATIO` (0.92) AND it beats the
    best non-overlapping window by `FUZZY_ANCHOR_MIN_MARGIN` (0.05), i.e. it
    is unique. The fuzzy strategy re-indents like the dedent one and says so
    in its note (`fuzzy match, ratio 0.95`). When nothing matches, the note
    names the closest window (`anchor not found; closest is lines 40-44
    (ratio 0.71)`) so the next synthesis step can be told where the model was
    aiming. A plan whose new text equals its old text is a no-op and is
    refused here, before it costs a write.
    """
    if plan.new_text == plan.old_text:
        return None, "plan is a no-op (new_lines == old_lines)"
    src_lines = source.split("\n")
    old = plan.old_lines
    n = len(old)
    if n == 0 or n > len(src_lines):
        return None, "anchor longer than the module"

    def find(norm) -> List[int]:
        target = norm(old)
        return [i for i in range(len(src_lines) - n + 1) if norm(src_lines[i:i + n]) == target]

    def splice(i: int, reindent: bool, label: str) -> Tuple[Optional[str], str]:
        new_lines = list(plan.new_lines)
        if reindent:
            window = src_lines[i:i + n]
            indents = [len(ln) - len(ln.lstrip()) for ln in window if ln.strip()]
            anchor_indent = " " * (min(indents) if indents else 0)
            new_lines = [(anchor_indent + ln) if ln.strip() else "" for ln in _strip_common_indent(new_lines)]
        out = src_lines[:i] + new_lines + src_lines[i + n:]
        new_source = "\n".join(out)
        if new_source == source:
            return None, "edit leaves the module unchanged"
        return new_source, f"replaced {n} line(s) at line {i + 1} ({label}) with {len(new_lines)}"

    strategies = (
        ("exact", lambda lines: list(lines), False),
        ("rstrip", lambda lines: [ln.rstrip() for ln in lines], False),
        ("dedent", _dedent_rstrip, True),
    )
    for label, norm, reindent in strategies:
        hits = find(norm)
        if len(hits) > 1:
            return None, f"anchor is ambiguous ({len(hits)} matches, {label})"
        if len(hits) == 1:
            return splice(hits[0], reindent, f"{label} match")

    best, ratio, runner_up = _closest_windows(src_lines, old)
    where = f"lines {best + 1}-{best + n} (ratio {ratio:.2f})"
    if ratio >= FUZZY_ANCHOR_MIN_RATIO:
        if runner_up >= ratio - FUZZY_ANCHOR_MIN_MARGIN:
            return None, (f"anchor is ambiguous (fuzzy: {where} vs another window at ratio {runner_up:.2f})")
        return splice(best, True, f"fuzzy match, ratio {ratio:.2f}")
    return None, f"anchor not found; closest is {where}"


# ---------------------------------------------------------------------------
# FUNCTION_REWRITE: the middle rung of the synthesis ladder (Gen 16 cohort,
# run 2). Of 151 synthesis moves, 102 fell through to the whole-module rewrite
# (~10k tokens at 20-40 tok/s); the 28 that wrote nothing cost 17 min each
# (max 43.6) because a plan that was a no-op or missed its anchor was followed
# by a rewrite -- and a retry -- that re-emitted the module unchanged. A
# FUNCTION_REWRITE names ONE definition of the module (the grammar constrains
# `function` to the names that exist) and gives its complete replacement; the
# runner splices it in, re-indented, and refuses it if it does not parse,
# renames the definition or changes nothing. ~4k tokens instead of ~10k, and
# a refusal is a verdict the ladder can act on.
# ---------------------------------------------------------------------------

MAX_FUNCTION_REWRITE_LINES = 200
MAX_FUNCTION_REWRITE_IMPORTS = 8
MAX_FUNCTION_ENUM = 120   # definitions offered to the grammar; beyond that `function` is a free string


def function_rewrite_schema(definitions: List[str]) -> Dict[str, Any]:
    """Builds the FUNCTION_REWRITE response format for one module.

    `definitions` are the qualified names from `list_definitions`; when given,
    `function` is an enum of them so the decoder cannot name something that
    does not exist. Built per call because the enum is per module.
    """
    names: List[str] = []
    for name in definitions:
        name = str(name).strip()
        if name and name not in names:
            names.append(name)
    function_schema: Dict[str, Any] = (
        {"type": "string", "enum": names[:MAX_FUNCTION_ENUM]} if 0 < len(names) <= MAX_FUNCTION_ENUM
        else {"type": "string", "maxLength": 80})
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "FunctionRewritePacket",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "packet": {"type": "string", "enum": ["FUNCTION_REWRITE"]},
                    "hypothesis_id": {"type": "string", "maxLength": 16},
                    "function": function_schema,
                    "rationale": {"type": "string", "maxLength": 300},
                    "new_source": {
                        "type": "array",
                        "maxItems": MAX_FUNCTION_REWRITE_LINES,
                        "items": {"type": "string", "maxLength": 200},
                    },
                    "add_imports": {
                        "type": "array",
                        "maxItems": MAX_FUNCTION_REWRITE_IMPORTS,
                        "items": {"type": "string", "maxLength": 120},
                    },
                },
                "required": ["packet", "hypothesis_id", "function", "rationale", "new_source", "add_imports"],
                "additionalProperties": False,
            },
        },
    }


V6_FUNCTION_REWRITE_SCHEMA: Dict[str, Any] = function_rewrite_schema([])


# ---------------------------------------------------------------------------
# RECRUIT_SPECIALIST (V8). The CEO names ONE specialist the live team lacks.
# Same envelope as the V6 packets: short strings with maxLength, arrays with
# item caps, `kind` an enum. `domain_tags` is what the role is matched against
# (`OrgState.observe_modules`, `library_candidates`), so the grammar asks for
# 3-8 short tokens; the parser lower-cases and de-duplicates them regardless.
# ---------------------------------------------------------------------------

RECRUIT_KINDS = ("probe", "synthesis", "both")   # mirrors hae.epistemic.org.ROLE_KINDS
MIN_RECRUIT_TAGS = 3
MAX_RECRUIT_TAGS = 8

V8_RECRUIT_SCHEMA: Dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "RecruitSpecialistPacket",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "packet": {"type": "string", "enum": ["RECRUIT_SPECIALIST"]},
                "name": {"type": "string", "maxLength": 80},
                "goal": {"type": "string", "maxLength": 300},
                "backstory": {"type": "string", "maxLength": 600},
                "domain_tags": {
                    "type": "array",
                    "minItems": MIN_RECRUIT_TAGS,
                    "maxItems": MAX_RECRUIT_TAGS,
                    "items": {"type": "string", "maxLength": 40},
                },
                "kind": {"type": "string", "enum": list(RECRUIT_KINDS)},
                "rationale": {"type": "string", "maxLength": 300},
            },
            "required": ["packet", "name", "goal", "backstory", "domain_tags", "kind", "rationale"],
            "additionalProperties": False,
        },
    },
}


@dataclass
class FunctionRewrite:
    """A complete replacement for one definition (`function`, qualified `Class.method` for methods)."""

    function: str
    rationale: str
    new_source: List[str]
    add_imports: List[str] = field(default_factory=list)
    hypothesis_id: str = ""

    @property
    def new_text(self) -> str:
        return "\n".join(self.new_source)


def parse_function_rewrite(raw: str) -> Optional[FunctionRewrite]:
    """Parses a FUNCTION_REWRITE packet; None when there is no usable replacement."""
    data = extract_json_object(raw)
    if not data:
        return None
    function = " ".join(str(data.get("function", "") or "").split())
    new = data.get("new_source")
    if not function or not isinstance(new, list):
        return None
    new_source = [str(x) for x in new if x is not None]
    if not any(ln.strip() for ln in new_source):
        return None
    imports = data.get("add_imports")
    add_imports = [str(x).strip() for x in imports if x is not None and str(x).strip()] \
        if isinstance(imports, list) else []
    return FunctionRewrite(
        function=function,
        rationale=" ".join(str(data.get("rationale", "") or "").split()),
        new_source=new_source, add_imports=add_imports,
        hypothesis_id=str(data.get("hypothesis_id", "") or ""),
    )


_DEF_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
_Definition = Tuple[str, int, int, ast.stmt]   # qualified name, start line, end line, node


def _definitions_of(body: List[ast.stmt], prefix: str = "") -> List[_Definition]:
    out: List[_Definition] = []
    for node in body:
        if not isinstance(node, _DEF_NODES):
            continue
        start = min([node.lineno] + [d.lineno for d in node.decorator_list])
        name = f"{prefix}{node.name}"
        out.append((name, start, int(node.end_lineno or node.lineno), node))
        if isinstance(node, ast.ClassDef):
            out.extend(_definitions_of(node.body, prefix=f"{name}."))
    return out


def list_definitions(source: str) -> List[Tuple[str, int, int]]:
    """`(qualified name, start line, end line)` for every top-level `def`,
    `async def` and `class` in `source`, plus class members as `Class.method`
    (nested classes recurse: `Outer.Inner.method`). Lines are 1-based and
    inclusive; the start line is the first decorator's when there are any.
    Returns [] when the module does not parse.
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return []
    return [(name, start, end) for name, start, end, _node in _definitions_of(tree.body)]


def _resolve_definition(name: str, defs: List[_Definition]) -> Optional[_Definition]:
    """Exact qualified name first; else a bare method name that is unique in the module."""
    exact = [d for d in defs if d[0] == name]
    if len(exact) == 1:
        return exact[0]
    if exact:
        return None  # redefined in the module: the name is ambiguous
    suffix = [d for d in defs if d[0].endswith("." + name)]
    return suffix[0] if len(suffix) == 1 else None


def _dedent_to_first_line(lines: List[str]) -> List[str]:
    """Removes the first non-blank line's indentation from every line (at most
    what each line has): a packet that quotes a method with its class-body
    indentation becomes a top-level definition, and a docstring line the
    model left at column 0 does not drag the `def` line back in."""
    lead = next((len(ln) - len(ln.lstrip()) for ln in lines if ln.strip()), 0)
    return [ln[min(lead, len(ln) - len(ln.lstrip())):] if ln.strip() else "" for ln in lines]


def _import_insertion_line(tree: ast.Module) -> int:
    """0-based line index at which a new import goes: after the last top-level
    import, else after the module docstring, else at the top."""
    imports = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    if imports:
        return int(max(n.end_lineno or n.lineno for n in imports))
    first = tree.body[0] if tree.body else None
    if isinstance(first, ast.Expr) and isinstance(getattr(first, "value", None), ast.Constant) \
            and isinstance(first.value.value, str):
        return int(first.end_lineno or first.lineno)
    return 0


def apply_function_rewrite(source: str, rewrite: FunctionRewrite) -> Tuple[Optional[str], str]:
    """Splices `rewrite.new_source` over the named definition of `source`.

    Returns `(new_source, note)`; `new_source` is None with a descriptive note
    whenever the packet is refused: the module does not parse, the function is
    not defined (`function not found: X; module defines: a, B.c, ...`), the
    replacement does not parse on its own (dedented), is not a single
    `def`/`class`, defines another name (`replacement defines Y, not X`) or
    another kind, is identical to the current definition (`new source
    identical to the current definition`), or leaves a module that no longer
    parses. The replacement is re-indented to the original definition's
    indentation; `add_imports` statements not already present verbatim are
    inserted once after the last top-level import (or after the module
    docstring). The result is guaranteed to `ast.parse`.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return None, f"module does not parse: {exc.msg} (line {exc.lineno})"
    defs = _definitions_of(tree.body)
    found = _resolve_definition(rewrite.function, defs)
    if found is None:
        names = ", ".join(d[0] for d in defs[:40]) + (", ..." if len(defs) > 40 else "")
        return None, f"function not found: {rewrite.function}; module defines: {names or '(nothing)'}"
    qualified, start, end, original = found
    short = qualified.rsplit(".", 1)[-1]

    new_lines = _dedent_to_first_line([ln.rstrip() for ln in rewrite.new_source])
    while new_lines and not new_lines[0].strip():
        new_lines.pop(0)
    while new_lines and not new_lines[-1].strip():
        new_lines.pop()
    try:
        replacement = ast.parse("\n".join(new_lines))
    except SyntaxError as exc:
        return None, f"replacement does not parse: {exc.msg} (line {exc.lineno})"
    if len(replacement.body) != 1:
        return None, f"replacement must be a single def/class, got {len(replacement.body)} statements"
    node = replacement.body[0]
    if not isinstance(node, _DEF_NODES):
        return None, f"replacement is a {type(node).__name__}, not a def or class"
    if node.name != short:
        return None, f"replacement defines {node.name}, not {short}"
    if isinstance(original, ast.ClassDef) != isinstance(node, ast.ClassDef):
        kind_new = "class" if isinstance(node, ast.ClassDef) else "def"
        kind_old = "class" if isinstance(original, ast.ClassDef) else "def"
        return None, f"replacement is a {kind_new}, but {qualified} is a {kind_old}"

    src_lines = source.split("\n")
    first = src_lines[start - 1]
    indent = first[:len(first) - len(first.lstrip())]
    block = [(indent + ln) if ln.strip() else "" for ln in new_lines]
    old_block = [ln.rstrip() for ln in src_lines[start - 1:end]]
    if old_block == block:
        return None, "new source identical to the current definition"
    out_lines = src_lines[:start - 1] + block + src_lines[end:]
    try:
        new_tree = ast.parse("\n".join(out_lines))
    except SyntaxError as exc:
        return None, f"result does not parse after the splice: {exc.msg} (line {exc.lineno})"

    present = {ln.strip() for ln in out_lines}
    wanted: List[str] = []
    ignored: List[str] = []
    for stmt in rewrite.add_imports:
        stmt = " ".join(stmt.split())
        if not stmt or stmt in present or stmt in wanted:
            continue
        try:
            parsed = ast.parse(stmt)
        except SyntaxError:
            ignored.append(stmt)
            continue
        if len(parsed.body) == 1 and isinstance(parsed.body[0], (ast.Import, ast.ImportFrom)):
            wanted.append(stmt)
        else:
            ignored.append(stmt)
    if wanted:
        at = _import_insertion_line(new_tree)
        out_lines = out_lines[:at] + wanted + out_lines[at:]
        try:
            ast.parse("\n".join(out_lines))
        except SyntaxError as exc:
            return None, f"result does not parse after adding imports: {exc.msg} (line {exc.lineno})"
    new_source = "\n".join(out_lines)
    if new_source == source:
        return None, "edit leaves the module unchanged"
    note = f"rewrote `{qualified}` (lines {start}-{end}, {end - start + 1} -> {len(block)} lines)"
    if wanted:
        note += f"; added import(s): {'; '.join(wanted)}"
    if ignored:
        note += f"; ignored non-import add_imports: {'; '.join(ignored)[:120]}"
    return new_source, note


# ---------------------------------------------------------------------------
# Parsing (tolerant of fences and prefixed prose; strict about content)
# ---------------------------------------------------------------------------

def extract_json_object(raw: str) -> Optional[Dict[str, Any]]:
    """Pulls the first JSON object out of a model reply, or None."""
    if not raw or not raw.strip():
        return None
    text = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidates = [fenced.group(1)] if fenced else []
    first, last = text.find("{"), text.rfind("}")
    if first != -1 and last > first:
        candidates.append(text[first:last + 1])
    for cand in candidates:
        try:
            parsed = json.loads(cand)
        except Exception:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def normalise_prediction(pred: Any) -> Dict[str, Any]:
    """Keeps only the prediction keys the gatekeeper knows how to check."""
    out: Dict[str, Any] = {}
    if not isinstance(pred, Mapping):
        return out
    if "expect_exit_code" in pred:
        try:
            out["expect_exit_code"] = int(pred["expect_exit_code"])
        except (TypeError, ValueError):
            pass
    for key in ("expect_stdout_contains", "expect_stderr_contains", "expect_stdout_regex"):
        val = pred.get(key)
        if isinstance(val, str) and val.strip():
            out[key] = val.strip()
    return out


def salvage_truncated_items(raw: str, key: str) -> List[Dict[str, Any]]:
    """Recovers the complete objects of `"<key>": [ {...}, {...}, <cut>` from a
    reply that was truncated by max_tokens (or ran into a degenerate loop).

    A proposer that produced two whole hypotheses before the decoder was cut
    off should not lose both; only the object that was still open is dropped.
    """
    if not raw:
        return []
    m = re.search(r'"%s"\s*:\s*\[' % re.escape(key), raw)
    if not m:
        return []
    decoder = json.JSONDecoder()
    pos = m.end()
    items: List[Dict[str, Any]] = []
    n = len(raw)
    while True:
        while pos < n and raw[pos] in " \t\r\n,":
            pos += 1
        if pos >= n or raw[pos] != "{":
            break
        try:
            obj, end = decoder.raw_decode(raw, pos)
        except ValueError:
            break  # the object that was open when the stream was cut
        if isinstance(obj, dict):
            items.append(obj)
        pos = end
    return items


def _probe_source(item: Mapping) -> str:
    """Probe text from either the line-array form or the legacy string form."""
    lines = item.get("probe_lines")
    if isinstance(lines, list):
        return "\n".join(str(ln) for ln in lines if ln is not None)
    probe = str(item.get("probe_code", "") or "")
    if "\n" not in probe and "\\n" in probe:
        probe = probe.replace("\\n", "\n")
    return probe


def parse_hypothesis_packet(raw: str, max_items: int = MAX_HYPOTHESES_PER_PACKET
                            ) -> List[HypothesisProposal]:
    """Parses a HYPOTHESIS_SET packet. Drops malformed or duplicate entries.

    Dropping is the right failure mode: a proposer that produced three good
    hypotheses and one without a probe should not lose the three. The same
    holds for a packet the decoder cut off: whole items before the cut are kept.
    """
    data = extract_json_object(raw)
    items: Any = data.get("hypotheses") if data else None
    if not isinstance(items, list):
        items = salvage_truncated_items(raw, "hypotheses")
    if not items:
        return []
    out: List[HypothesisProposal] = []
    seen: set = set()
    for item in items:
        if not isinstance(item, Mapping):
            continue
        claim = " ".join(str(item.get("claim", "")).split())
        probe = _probe_source(item)
        if not claim or not probe.strip():
            continue
        prop = HypothesisProposal(
            claim=claim,
            mechanism=" ".join(str(item.get("mechanism", "")).split()),
            prior=clamp_prior(item.get("prior", 0.5)),
            probe_code=probe,
            prediction=normalise_prediction(item.get("prediction")),
        )
        if prop.signature in seen:
            continue
        seen.add(prop.signature)
        out.append(prop)
        if len(out) >= max_items:
            break
    return out


def parse_question_packet(raw: str, max_items: int = MAX_QUESTIONS_PER_PACKET
                          ) -> List[QuestionProposal]:
    data = extract_json_object(raw)
    items: Any = data.get("questions") if data else None
    if not isinstance(items, list):
        items = salvage_truncated_items(raw, "questions")
    if not items:
        return []
    out: List[QuestionProposal] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        text = " ".join(str(item.get("text", "")).split())
        if not text:
            continue
        try:
            unc = float(item.get("uncertainty", 1.0))
        except (TypeError, ValueError):
            unc = 1.0
        out.append(QuestionProposal(
            text=text,
            module=str(item.get("module", "") or "").strip(),
            uncertainty=max(0.05, min(1.0, unc)),
        ))
        if len(out) >= max_items:
            break
    return out


_TAG_CLEAN_RE = re.compile(r"[^a-z0-9_]+")


def normalise_recruit_tags(tags: Any) -> List[str]:
    """Lower-cased `[a-z0-9_]` tokens, de-duplicated in order, at most `MAX_RECRUIT_TAGS`.

    Accepts a list or a comma/whitespace separated string (a model that
    ignored the array shape should not cost the firm the whole packet).
    """
    if isinstance(tags, str):
        tags = re.split(r"[,\s]+", tags)
    if not isinstance(tags, (list, tuple)):
        return []
    out: List[str] = []
    for raw in tags:
        if raw is None:
            continue
        tok = _TAG_CLEAN_RE.sub("_", str(raw).strip().lower()).strip("_")
        if tok and tok not in out:
            out.append(tok)
        if len(out) >= MAX_RECRUIT_TAGS:
            break
    return out


def parse_recruit_packet(raw: str) -> Optional[RecruitProposal]:
    """Parses a RECRUIT_SPECIALIST packet; None when there is no usable name.

    Tolerant like the other parsers: fences and prose around the JSON are
    fine, every field but `name` has a default, an unknown `kind` becomes
    `both`, and tags are normalised rather than rejected. Whether the role is
    a duplicate of an active one is the adapter's decision, not the parser's.
    """
    data = extract_json_object(raw)
    if not data:
        return None
    name = " ".join(str(data.get("name", "") or "").split())
    if not name:
        return None
    kind = str(data.get("kind", "both") or "both").strip().lower()
    if kind not in RECRUIT_KINDS:
        kind = "both"
    return RecruitProposal(
        name=name[:80],
        goal=" ".join(str(data.get("goal", "") or "").split()),
        backstory=" ".join(str(data.get("backstory", "") or "").split()),
        domain_tags=normalise_recruit_tags(data.get("domain_tags")),
        kind=kind,
        rationale=" ".join(str(data.get("rationale", "") or "").split()),
    )
