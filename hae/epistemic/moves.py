"""Typed moves: the only things a System 1 agent may say to the ledger.

A proposer does not write beliefs; it emits one of five typed proposals,
each of which the search loop turns into a ledger operation:

  * `QuestionProposal`    -> `EpistemicState.add_question`
  * `HypothesisProposal`  -> `EpistemicState.add_hypothesis` (always UNVERIFIED)
  * `ExperimentProposal`  -> `EvidenceGatekeeper.run_experiment`
  * `CodeDiffProposal`    -> a workspace write, then `verify_module`
  * `SynthesisProposal`   -> the decision to turn a SUPPORTED hypothesis into code

The two xgrammar schemas below are what the vLLM constrained decoder enforces
when a proposer is asked for questions or hypotheses. Each hypothesis must
carry a *probe* (a short Python script) and a *prediction* (what the probe
prints or exits with if the hypothesis is true). A hypothesis without a
prediction is unfalsifiable and the gatekeeper will not support it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional

from hae.epistemic.ledger import clamp_prior, mechanism_signature

MOVE_ASK_QUESTION = "ask_question"
MOVE_PROPOSE_HYPOTHESIS = "propose_hypothesis"
MOVE_RUN_EXPERIMENT = "run_experiment"
MOVE_SYNTHESIZE = "synthesize"
MOVE_RECONCILE = "reconcile_oracle"
MOVE_TYPES = (MOVE_ASK_QUESTION, MOVE_PROPOSE_HYPOTHESIS, MOVE_RUN_EXPERIMENT,
              MOVE_SYNTHESIZE, MOVE_RECONCILE)

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
