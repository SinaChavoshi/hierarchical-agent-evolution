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
    "strategic_depth": 0.15,
    "technical_feasibility": 0.15,
    "cross_functional_coherence": 0.05,
    "risk_mitigation": 0.10,
    "actionability_and_synthesis": 0.05,
    "execution_integrity": 0.50,
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

IMPORTANT — V5 TYPESAFE AI PROTOCOL COMPATIBILITY:
Firms operating under the V5 TypeSafe AI Protocol emit hardware-constrained JSON packets (`EXEC_DELIVERABLE`, `DEPT_SYNTHESIS`, `DOMAIN_VECTOR`) plus physical Python modules (`### File: hae/evaluation/artifacts.py`, `### File: hae/evaluation/harness.py`, `### File: hae/evaluation/verification_loop.py`, `### File: hae/genome/morphogenesis.py`) instead of verbose prose memos. Evaluate the architectural rigor, algorithmic completeness, cross-departmental alignment, and risk guards directly from the structured JSON fields and the authored Python implementations (awarding 90-95 across dimensions when the structured packets and Python modules are complete and rigorous). Never penalize a firm for using compact V5 TypeSafe JSON instead of prose essays.

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
        v = float(value)
        if 0.0 < v <= 1.0:
            v *= 100.0
        elif 1.0 < v <= 10.0:
            v *= 10.0
        return max(0.0, min(100.0, v))
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


# Minimum fraction of judged prose credit retained when execution == 0.0.
# Prevents a 0/50 submission from earning 60+ fitness purely on architectural
# JSON/prose while preserving a small tie-breaker gradient at 0 tests and
# smoothly unlocking full judged credit at 100% test execution.
EXECUTION_PROSE_FLOOR: float = 0.20


# V6 composite, used only when a firm produced an epistemic ledger. Prose has
# no weight here: a firm's reasoning is scored from its evidence trail, not
# from its description of it. Must sum to 1.0.
EPISTEMIC_WEIGHTS: Dict[str, float] = {
    "execution_integrity": 0.60,
    "epistemic_integrity": 0.25,
    "epistemic_efficiency": 0.15,
}

# A Brier score of 0.25 is what "always say 0.5" earns: no information. Priors
# earn calibration credit only for beating that.
UNINFORMATIVE_BRIER = 0.25


def proposal_discipline(audit: Mapping[str, Any]) -> float:
    """0-1: how much of what System 1 proposed was *new, testable* work.

    Each proposal that the organisation had to catch costs a share of the
    term, weighted by how much it cost the search:

      * `tabu_rejections`    1.0 -- a re-proposal of a mechanism already
                                   falsified (lexical or paraphrased);
      * `probes_refused`     1.0 -- a hypothesis whose probe the gatekeeper
                                   could not run at all (`UNTESTABLE`);
      * `duplicate_rejections` 0.5 -- a re-proposal of a hypothesis still on the
                                   ledger (caught before it cost a move);
      * `probe_repairs`      0.5 -- a probe the gatekeeper had to repair in
                                   place before it could run.

    The denominator is everything proposed (`proposed_total`, which already
    includes the tabu and duplicate rejections). Audits written before these
    counters existed score exactly as before: the new keys default to zero.
    """
    proposed = int(audit.get("proposed_total", 0) or 0)
    if proposed <= 0:
        return 1.0
    cost = (float(audit.get("tabu_rejections", 0) or 0)
            + float(audit.get("probes_refused", 0) or 0)
            + 0.5 * float(audit.get("duplicate_rejections", 0) or 0)
            + 0.5 * float(audit.get("probe_repairs", 0) or 0))
    return max(0.0, min(1.0, 1.0 - cost / float(proposed)))


def epistemic_integrity_score(audit: Optional[Mapping[str, Any]]) -> Optional[float]:
    """0-100 from a ledger audit, or None when there is no ledger to audit.

    Four measured parts:
      * evidence backing (30%): every verdict, settled fact and ruled-out
        mechanism cites evidence that is on the log, and statuses are
        internally consistent;
      * calibration (25%): System 1's stated priors beat an uninformative
        Brier score against the gatekeeper's verdicts. Zero until something
        has been tested;
      * certification (30%): share of questions the oracle later confirmed;
      * proposal discipline (15%): share of proposals that were new, testable
        work -- not re-proposals of falsified or existing mechanisms, not
        probes the gatekeeper had to refuse or repair (`proposal_discipline`).
    """
    if not audit:
        return None
    questions = int(audit.get("questions", 0) or 0)
    if questions <= 0:
        return None
    backed = max(0.0, min(1.0, float(audit.get("evidence_backed_fraction", 0.0))))
    if int(audit.get("status_inconsistencies", 0) or 0) > 0:
        backed = 0.0
    tested = int(audit.get("calibration_tested", 0) or 0)
    if tested > 0:
        brier = max(0.0, float(audit.get("calibration_brier", UNINFORMATIVE_BRIER)))
        calibration = max(0.0, 1.0 - brier / UNINFORMATIVE_BRIER)
    else:
        calibration = 0.0
    certified = max(0.0, min(1.0, int(audit.get("questions_certified", 0) or 0) / float(questions)))
    discipline = proposal_discipline(audit)
    score = 100.0 * (0.30 * backed + 0.25 * calibration + 0.30 * certified + 0.15 * discipline)
    return round(max(0.0, min(100.0, score)), 2)


def epistemic_efficiency_score(audit: Optional[Mapping[str, Any]]) -> Optional[float]:
    """0-100: uncertainty resolved, and resolved per unit of move budget."""
    if not audit:
        return None
    moves = int(audit.get("moves_used", 0) or 0)
    if moves <= 0:
        return 0.0
    resolved = max(0.0, min(1.0, float(audit.get("resolved_fraction", 0.0))))
    budget = int(audit.get("budget_moves", 0) or 0)
    used_fraction = (moves / float(budget)) if budget > 0 else 1.0
    per_budget = min(1.0, resolved / used_fraction) if used_fraction > 0 else 0.0
    score = 100.0 * (0.60 * resolved + 0.40 * per_budget)
    return round(max(0.0, min(100.0, score)), 2)


def composite_score(judged: Mapping[str, float],
                    execution: Optional[float],
                    epistemic_audit: Optional[Mapping[str, Any]] = None) -> float:
    """Weighted blend of judged dimensions and measured execution integrity.

    When execution integrity is unavailable the judged weights are
    renormalised to sum to 1.0, so a firm with no verifiable workspace is
    scored purely on prose -- and the caller can tell, because
    `execution_evaluable` is False on the result.

    When execution integrity IS available, the judged prose contribution is
    multiplicatively gated by `EXECUTION_PROSE_FLOOR + (1 - EXECUTION_PROSE_FLOOR) * (execution / 100.0)`
    so a submission that passes 0/50 tests (`execution == 0.0`) cannot score
    higher than `10.0` even with perfect prose (`0.20 * 50.0 = 10.0`), while
    passing tests both adds direct execution credit (`0.50 * execution`) and
    unlocks the architectural prose score.

    V6: when `epistemic_audit` is supplied (the firm ran an epistemic search
    and produced a ledger), the score is instead `EPISTEMIC_WEIGHTS` over
    execution, ledger integrity and search efficiency. Prose is not in it.
    """
    integrity = epistemic_integrity_score(epistemic_audit)
    if integrity is not None:
        exec_val = max(0.0, min(100.0, float(execution))) if execution is not None else 0.0
        efficiency = epistemic_efficiency_score(epistemic_audit) or 0.0
        total = (EPISTEMIC_WEIGHTS["execution_integrity"] * exec_val
                 + EPISTEMIC_WEIGHTS["epistemic_integrity"] * integrity
                 + EPISTEMIC_WEIGHTS["epistemic_efficiency"] * efficiency)
        return round(total, 2)

    if execution is None:
        judged_total = sum(RUBRIC_WEIGHTS[d] for d in JUDGED_DIMENSIONS)
        return round(
            sum(RUBRIC_WEIGHTS[d] * float(judged.get(d, 0.0))
                for d in JUDGED_DIMENSIONS) / judged_total,
            2,
        )

    exec_val = max(0.0, min(100.0, float(execution)))
    exec_gate = EXECUTION_PROSE_FLOOR + (1.0 - EXECUTION_PROSE_FLOOR) * (exec_val / 100.0)
    judged_weighted = sum(RUBRIC_WEIGHTS[d] * float(judged.get(d, 0.0))
                          for d in JUDGED_DIMENSIONS)
    total = exec_gate * judged_weighted + RUBRIC_WEIGHTS["execution_integrity"] * exec_val
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


V5_JUDGE_SCHEMA: Dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "JudgeEvaluationPacket",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "strategic_depth": {"type": "number"},
                "technical_feasibility": {"type": "number"},
                "cross_functional_coherence": {"type": "number"},
                "risk_mitigation": {"type": "number"},
                "actionability_and_synthesis": {"type": "number"},
                "qualitative_feedback": {"type": "string", "maxLength": 450},
                "identified_bottlenecks": {
                    "type": "array",
                    "items": {"type": "string", "maxLength": 140},
                    "maxItems": 3,
                },
            },
            "required": [
                "strategic_depth",
                "technical_feasibility",
                "cross_functional_coherence",
                "risk_mitigation",
                "actionability_and_synthesis",
                "qualitative_feedback",
                "identified_bottlenecks",
            ],
            "additionalProperties": False,
        },
    },
}


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
                    response_format=V5_JUDGE_SCHEMA,
                    max_tokens=450,
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
        epistemic_audit: Optional[Mapping[str, Any]] = None,
    ) -> EvaluationResult:
        """Scores a firm on five judged dimensions plus measured execution integrity.

        `verification` may be a `VerificationOutcome` (from any `Verifier`), a
        `VerificationReport` (from `ExecutionHarness`), or a dict carrying
        `gate_status` or `evaluable`+`score`. Omitting it scores the firm on
        prose alone and sets `execution_evaluable=False` on the result.

        `epistemic_audit` (V6) is the dict from
        `hae.epistemic.audit.build_epistemic_audit`. When present, the
        composite follows `EPISTEMIC_WEIGHTS` and the judged prose carries no
        weight; the judged dimensions are still recorded for Pareto diversity.
        """
        exec_score = resolve_execution_score(verification)
        integrity = epistemic_integrity_score(epistemic_audit)
        efficiency = epistemic_efficiency_score(epistemic_audit) if integrity is not None else None

        def _stamp(fitness: FitnessScore) -> FitnessScore:
            fitness.execution_integrity = exec_score if exec_score is not None else 0.0
            fitness.execution_evaluable = exec_score is not None
            fitness.epistemic_evaluable = integrity is not None
            fitness.epistemic_integrity = integrity if integrity is not None else 0.0
            fitness.epistemic_efficiency = efficiency if efficiency is not None else 0.0
            return fitness

        obj_view = objective if len(objective) <= 4500 else (objective[:4500] + "\n...[4-Module Full-Stack Specification & 50 Held-Out Unit Tests]...")
        deliv_view = final_deliverable if len(final_deliverable) <= 26000 else (final_deliverable[:26000] + "\n...[Remaining Verified Module Implementation Lines]...")

        evaluation_prompt = f"""EVALUATE THIS PROPOSAL:

STRATEGIC OBJECTIVE GIVEN TO THE FIRM:
{obj_view}

FINAL DELIVERABLE PRODUCED BY THE FIRM:
{deliv_view}

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
            return self._result(company_id, generation, objective,
                                final_deliverable, departmental_briefs, _stamp(fitness))

        judged = {d: _clamp(parsed.get(d)) for d in JUDGED_DIMENSIONS}
        overall = composite_score(judged, exec_score, epistemic_audit=epistemic_audit)

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
        return self._result(company_id, generation, objective,
                            final_deliverable, departmental_briefs, _stamp(fitness))

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
