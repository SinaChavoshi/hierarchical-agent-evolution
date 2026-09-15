"""Multi-dimensional fitness evaluation engine for strategic research.

The score is a blend of two very different kinds of evidence:

  * five *judged* dimensions, supplied by an LLM reading the firm's prose
    deliverable, and
  * one *measured* dimension, `execution_integrity`, computed deterministically
    from `src/execution_harness.py` gate results. The LLM never sees it and
    cannot influence it.

Two defects in the previous version are fixed here.

**The silent fallback.** When the judge's JSON failed to parse, the old code
substituted a hardcoded `70/70/70/65/70` and returned it as though it were a
real evaluation. A firm whose judging call failed therefore entered the
breeding pool with a plausible mid-range score and no marker of any kind. That
fallback is gone: a parse failure now triggers one repair attempt, and if that
also fails the result is marked `evaluation_failed=True` with a score of 0.0.

**No execution dimension.** The rubric scored architecture entirely on the
firm's *description* of it. Measured over Generations 5-10, the resulting gross
score correlated with whether the code actually ran at r = +0.20. The two
weakest-signal dimensions -- `cross_functional_coherence` and
`actionability_and_synthesis`, both saturated at a mean of 99.0 with sigma 1.67
in Generation 10 -- are cut from a combined 35% to 20%, and the 30% freed up
goes to `execution_integrity`.
"""

import json
import re
from typing import Any, Dict, Mapping, Optional, Tuple

from hae.infra.llm import call_llm
from hae.genome.schema import EvaluationResult, FitnessScore

# Judged dimensions plus the one measured dimension. Must sum to 1.0.
RUBRIC_WEIGHTS: Dict[str, float] = {
    "strategic_depth": 0.20,
    "technical_feasibility": 0.20,
    "cross_functional_coherence": 0.10,
    "risk_mitigation": 0.10,
    "actionability_and_synthesis": 0.10,
    "execution_integrity": 0.30,
}

JUDGED_DIMENSIONS: Tuple[str, ...] = (
    "strategic_depth",
    "technical_feasibility",
    "cross_functional_coherence",
    "risk_mitigation",
    "actionability_and_synthesis",
)

# Relative worth of each execution gate, out of 100. Tests carry the most
# weight because a green suite is the strongest available evidence that the
# package does something; telemetry the least because it is the easiest to
# satisfy incidentally.
GATE_WEIGHTS: Dict[str, float] = {
    "syntax": 20.0,
    "build": 15.0,
    "smoke": 20.0,
    "tests": 30.0,
    "telemetry": 15.0,
}

JUDGE_SYSTEM_PROMPT = """You are an elite Venture Capital Partner and Principal Systems Evaluator.
Your role is to rigorously judge open-ended strategic and technical research proposals submitted by virtual organizations.
You must be strictly objective, pedantic about technical realism, and intolerant of vague hand-waving.

You will score the proposal on 5 distinct dimensions from 0 to 100:
1. strategic_depth (20%): Novelty, insightfulness, non-obvious market dynamics, and competitive moats.
2. technical_feasibility (20%): Architectural realism, physical/computational limits, concrete system specs.
3. cross_functional_coherence (10%): Alignment across engineering, finance, product, and operations.
4. risk_mitigation (10%): Second-order consequences, red-team resilience, regulatory and supply-chain vulnerabilities.
5. actionability_and_synthesis (10%): Clarity, executive decision readiness, measurable milestones.

The remaining 30% of this proposal's score is NOT yours to award. It is
`execution_integrity`, measured by actually parsing, installing, importing, and
running the code the firm wrote. You cannot see that result and must not try to
predict it. In particular, do NOT inflate technical_feasibility because a
proposal *claims* its code is tested, packaged, or instrumented; score only the
engineering reasoning you can read.

Use the full 0-100 range. A score above 95 on any dimension should be rare and
must be justified in your critique. Scoring every dimension in the high 90s is
a failure of discrimination on your part, not a compliment to the proposal.

You MUST reply with ONLY valid JSON matching this schema:
{
  "strategic_depth": <float 0-100>,
  "technical_feasibility": <float 0-100>,
  "cross_functional_coherence": <float 0-100>,
  "risk_mitigation": <float 0-100>,
  "actionability_and_synthesis": <float 0-100>,
  "qualitative_feedback": "<Detailed critique highlighting specific strengths and deficiencies>",
  "identified_bottlenecks": [
    "<Bottleneck 1: specific flaw in reasoning, architecture, or organization>",
    "<Bottleneck 2: missing perspective or inadequate domain depth>"
  ]
}
"""

_REPAIR_INSTRUCTION = """Your previous reply was not valid JSON and could not be parsed.
Reply again with ONLY the JSON object, no prose, no markdown fence, no trailing commentary."""


def _extract_json(raw: str) -> Dict[str, Any]:
    """Pulls a JSON object out of a model reply.

    Raises rather than guessing. Callers are responsible for handling failure
    explicitly; silently substituting defaults is what this module exists to
    stop doing.
    """
    if not raw or not raw.strip():
        raise ValueError("judge returned an empty response")

    cleaned = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", cleaned, re.DOTALL)
    if fenced:
        cleaned = fenced.group(1)
    else:
        # Fall back to the outermost brace pair, which survives a model that
        # prefixes its JSON with a sentence.
        first, last = cleaned.find("{"), cleaned.rfind("}")
        if first != -1 and last > first:
            cleaned = cleaned[first:last + 1]

    parsed = json.loads(cleaned)
    if not isinstance(parsed, dict):
        raise ValueError(f"judge returned a {type(parsed).__name__}, not an object")

    missing = [d for d in JUDGED_DIMENSIONS if d not in parsed]
    if missing:
        raise ValueError(f"judge response missing dimensions: {', '.join(missing)}")
    return parsed


def _clamp(value: Any, default: float = 0.0) -> float:
    try:
        return max(0.0, min(100.0, float(value)))
    except (TypeError, ValueError):
        return default


def execution_integrity(gate_status: Optional[Mapping[str, str]]) -> Optional[float]:
    """Scores 0-100 from harness gate results, or None if nothing was evaluable.

    A `skipped` gate is excluded from the denominator rather than counted
    against the firm. Skips mean the harness could not reach a verdict -- an
    uninstalled third-party dependency, usually -- and penalising a firm for
    the environment would reintroduce exactly the kind of unearned signal this
    rewrite removes. A gate that was evaluated and did not pass scores zero.
    """
    if not gate_status:
        return None

    earned = 0.0
    available = 0.0
    for gate, weight in GATE_WEIGHTS.items():
        status = str(gate_status.get(gate, "skipped")).lower()
        if status == "skipped":
            continue
        available += weight
        if status == "passed":
            earned += weight

    if available <= 0.0:
        return None
    return round(100.0 * earned / available, 2)


def composite_score(judged: Mapping[str, float],
                    execution: Optional[float]) -> float:
    """Weighted blend of judged dimensions and measured execution integrity.

    When execution integrity is unavailable the judged weights are
    renormalised to sum to 1.0, so a firm with no verifiable workspace is
    scored purely on prose -- and the caller can tell, because
    `execution_evaluable` is False on the result.
    """
    if execution is None:
        judged_total = sum(RUBRIC_WEIGHTS[d] for d in JUDGED_DIMENSIONS)
        return round(
            sum(RUBRIC_WEIGHTS[d] * float(judged.get(d, 0.0))
                for d in JUDGED_DIMENSIONS) / judged_total,
            2,
        )

    total = sum(RUBRIC_WEIGHTS[d] * float(judged.get(d, 0.0))
                for d in JUDGED_DIMENSIONS)
    total += RUBRIC_WEIGHTS["execution_integrity"] * float(execution)
    return round(total, 2)


def resolve_execution_score(verification: Any) -> Optional[float]:
    """Resolves the measured execution score on [0, 100], or None if unevaluable.

    Two kinds of verifier reach the judge:

      * Gate verifiers (`ExecutionGateVerifier` / `VerificationReport`), which
        populate `gate_status` and are weighted across the five gates via
        `execution_integrity(gate_status)`.
      * Direct verifiers (`BenchmarkVerifier`, `CompositeVerifier`), which
        supply an authoritative `score` on [0, 100] with `evaluable=True` and
        an empty `gate_status`.

    Checking only `gate_status` silently discarded every benchmark score and
    treated the run as unevaluable -- which dropped the 30% execution weight
    and rescaled the five LLM-judged prose dimensions to 100%. Found live in
    the in-cluster smoke firm: a submission scoring 0/7 on the held-out suite
    received `execution_evaluable=False` and an overall fitness of 77.42.
    """
    if verification is None:
        return None
    gate_status = getattr(verification, "gate_status", None)
    if gate_status is None and isinstance(verification, Mapping):
        gate_status = verification.get("gate_status")
    from_gates = execution_integrity(gate_status)
    if from_gates is not None:
        return from_gates

    evaluable = getattr(verification, "evaluable", None)
    score = getattr(verification, "score", None)
    if isinstance(verification, Mapping):
        if evaluable is None:
            evaluable = verification.get("evaluable")
        if score is None:
            score = verification.get("score")
    if evaluable is True and isinstance(score, (int, float)):
        return _clamp(score)
    return None


class StrategicFitnessEvaluator:
    """Evaluates company outputs and produces structured multi-attribute scorecards."""

    def __init__(self, model_name: str = "gemini-2.5-pro", repair_attempts: int = 1):
        self.model_name = model_name
        self.repair_attempts = repair_attempts

    def _judge(self, evaluation_prompt: str) -> Tuple[Optional[Dict[str, Any]], str, str]:
        """Calls the judge, retrying once on unparseable output.

        Returns `(parsed_or_None, last_raw_response, failure_reason)`.
        """
        prompt = evaluation_prompt
        raw = ""
        reason = ""
        for attempt in range(self.repair_attempts + 1):
            try:
                raw = call_llm(
                    prompt=prompt,
                    model_name=self.model_name,
                    temperature=0.2,
                    system_instruction=JUDGE_SYSTEM_PROMPT,
                )
            except Exception as exc:  # transport, auth, quota
                reason = f"judge call raised {type(exc).__name__}: {exc}"
                prompt = f"{evaluation_prompt}\n\n{_REPAIR_INSTRUCTION}"
                continue

            try:
                return _extract_json(raw), raw, ""
            except Exception as exc:
                reason = f"{type(exc).__name__}: {exc}"
                prompt = (
                    f"{evaluation_prompt}\n\n{_REPAIR_INSTRUCTION}\n\n"
                    f"Parser error was: {reason}"
                )
        return None, raw, reason

    def evaluate(
        self,
        company_id: str,
        generation: int,
        objective: str,
        final_deliverable: str,
        departmental_briefs: Dict[str, str],
        elapsed_seconds: float = 0.0,
        token_usage: int = 0,
        verification: Any = None,
    ) -> EvaluationResult:
        """Scores a firm on five judged dimensions plus measured execution integrity.

        `verification` may be a `VerificationOutcome` (from any `Verifier`), a
        `VerificationReport` (from `ExecutionHarness`), or a dict carrying
        `gate_status` or `evaluable`+`score`. Omitting it scores the firm on
        prose alone and sets `execution_evaluable=False` on the result.
        """
        exec_score = resolve_execution_score(verification)

        evaluation_prompt = f"""EVALUATE THIS PROPOSAL:

STRATEGIC OBJECTIVE GIVEN TO THE FIRM:
{objective}

FINAL DELIVERABLE PRODUCED BY THE FIRM:
{final_deliverable}

DEPARTMENTAL BRIEFS:
{json.dumps({k: v[:800] + '...' for k, v in departmental_briefs.items()}, indent=2)}

Score this proposal rigorously according to your rubric. Return only the JSON object."""

        parsed, raw_response, failure_reason = self._judge(evaluation_prompt)

        if parsed is None:
            # Previously this branch quietly returned 70/70/70/65/70. It now
            # returns a zero marked as failed, so a firm cannot be bred forward
            # on the strength of an evaluation that never happened.
            print(f" [JUDGE FAILED] {company_id}: {failure_reason}")
            fitness = FitnessScore(
                strategic_depth=0.0,
                technical_feasibility=0.0,
                cross_functional_coherence=0.0,
                risk_mitigation=0.0,
                actionability_and_synthesis=0.0,
                fitness_score=0.0,
                qualitative_feedback=(
                    f"EVALUATION FAILED after {self.repair_attempts + 1} attempt(s): "
                    f"{failure_reason}. Raw response head: {raw_response[:500]}"
                ),
                identified_bottlenecks=[f"Judge evaluation failed: {failure_reason}"],
                token_usage=token_usage,
                elapsed_seconds=elapsed_seconds,
            )
            fitness.evaluation_failed = True
            fitness.execution_integrity = exec_score if exec_score is not None else 0.0
            fitness.execution_evaluable = exec_score is not None
            return self._result(company_id, generation, objective,
                                final_deliverable, departmental_briefs, fitness)

        judged = {d: _clamp(parsed.get(d)) for d in JUDGED_DIMENSIONS}
        overall = composite_score(judged, exec_score)

        fitness = FitnessScore(
            strategic_depth=judged["strategic_depth"],
            technical_feasibility=judged["technical_feasibility"],
            cross_functional_coherence=judged["cross_functional_coherence"],
            risk_mitigation=judged["risk_mitigation"],
            actionability_and_synthesis=judged["actionability_and_synthesis"],
            fitness_score=overall,
            qualitative_feedback=str(parsed.get("qualitative_feedback", "")),
            identified_bottlenecks=list(parsed.get("identified_bottlenecks", [])),
            token_usage=token_usage,
            elapsed_seconds=elapsed_seconds,
        )
        fitness.evaluation_failed = False
        fitness.execution_integrity = exec_score if exec_score is not None else 0.0
        fitness.execution_evaluable = exec_score is not None
        return self._result(company_id, generation, objective,
                            final_deliverable, departmental_briefs, fitness)

    @staticmethod
    def _result(company_id, generation, objective, final_deliverable,
                departmental_briefs, fitness) -> EvaluationResult:
        import datetime
        return EvaluationResult(
            company_id=company_id,
            generation=generation,
            objective=objective,
            final_deliverable=final_deliverable,
            departmental_briefs=departmental_briefs,
            fitness=fitness,
            timestamp=datetime.datetime.now(datetime.timezone.utc)
            .replace(tzinfo=None).isoformat() + "Z",
        )
