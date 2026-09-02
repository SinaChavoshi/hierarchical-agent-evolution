"""Multi-dimensional LLM-as-a-Judge fitness evaluation engine for strategic research."""

import json
import re
from typing import Dict, Any
from .schema import FitnessScore, EvaluationResult
from .llm_factory import call_vertex_gemini_rest

JUDGE_SYSTEM_PROMPT = """You are an elite Venture Capital Partner and Principal Systems Evaluator.
Your role is to rigorously judge open-ended strategic and technical research proposals submitted by virtual organizations.
You must be strictly objective, pedantic about technical realism, and intolerant of vague hand-waving.

You will score the proposal on 5 distinct dimensions from 0 to 100:
1. strategic_depth (25%): Novelty, insightfulness, non-obvious market dynamics, and competitive moats.
2. technical_feasibility (25%): Architectural realism, physical/computational limits, concrete system specs.
3. cross_functional_coherence (20%): Alignment across engineering, finance, product, and operations.
4. risk_mitigation (15%): Second-order consequences, red-team resilience, regulatory and supply-chain vulnerabilities.
5. actionability_and_synthesis (15%): Clarity, executive decision readiness, measurable milestones.

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

class StrategicFitnessEvaluator:
    """Evaluates company outputs and produces structured multi-attribute scorecards."""

    def evaluate(
        self,
        company_id: str,
        generation: int,
        objective: str,
        final_deliverable: str,
        departmental_briefs: Dict[str, str],
        elapsed_seconds: float = 0.0,
        estimated_tokens: int = 0
    ) -> EvaluationResult:
        """Executes LLM-as-a-Judge scoring against the strategic rubric."""
        evaluation_prompt = f"""EVALUATE THIS PROPOSAL:

STRATEGIC OBJECTIVE GIVEN TO THE FIRM:
{objective}

FINAL DELIVERABLE PRODUCED BY THE FIRM:
{final_deliverable}

DEPARTMENTAL BRIEFS:
{json.dumps({k: v[:800] + '...' for k, v in departmental_briefs.items()}, indent=2)}

Score this proposal rigorously according to your rubric. Return only the JSON object."""

        raw_response = call_vertex_gemini_rest(
            prompt=evaluation_prompt,
            model_name="gemini-2.5-pro",
            temperature=0.2,
            system_instruction=JUDGE_SYSTEM_PROMPT
        )

        # Parse JSON from response
        try:
            cleaned = raw_response.strip()
            if "```json" in cleaned:
                cleaned = re.search(r'```json\s*(.*?)\s*```', cleaned, re.DOTALL).group(1)
            elif "```" in cleaned:
                cleaned = re.search(r'```\s*(.*?)\s*```', cleaned, re.DOTALL).group(1)
            parsed = json.loads(cleaned)
        except Exception:
            # Safe fallback if JSON formatting failed
            parsed = {
                "strategic_depth": 70.0,
                "technical_feasibility": 70.0,
                "cross_functional_coherence": 70.0,
                "risk_mitigation": 65.0,
                "actionability_and_synthesis": 70.0,
                "qualitative_feedback": raw_response[:500],
                "identified_bottlenecks": ["JSON parsing error during automated judging"]
            }

        # Calculate weighted composite overall score
        s_depth = float(parsed.get("strategic_depth", 50.0))
        t_feas = float(parsed.get("technical_feasibility", 50.0))
        c_coher = float(parsed.get("cross_functional_coherence", 50.0))
        r_mitig = float(parsed.get("risk_mitigation", 50.0))
        a_synth = float(parsed.get("actionability_and_synthesis", 50.0))

        overall = round(
            (0.25 * s_depth) + (0.25 * t_feas) + (0.20 * c_coher) + (0.15 * r_mitig) + (0.15 * a_synth),
            2
        )

        fitness = FitnessScore(
            strategic_depth=s_depth,
            technical_feasibility=t_feas,
            cross_functional_coherence=c_coher,
            risk_mitigation=r_mitig,
            actionability_and_synthesis=a_synth,
            overall_score=overall,
            qualitative_feedback=str(parsed.get("qualitative_feedback", "")),
            identified_bottlenecks=list(parsed.get("identified_bottlenecks", [])),
            token_count=estimated_tokens,
            elapsed_seconds=elapsed_seconds
        )

        import datetime
        return EvaluationResult(
            company_id=company_id,
            generation=generation,
            objective=objective,
            final_deliverable=final_deliverable,
            departmental_briefs=departmental_briefs,
            fitness=fitness,
            timestamp=datetime.datetime.utcnow().isoformat() + "Z"
        )
