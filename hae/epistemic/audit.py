"""The Epistemic Integrity Audit: what fitness reads instead of prose.

V5 scored a firm's reasoning by asking a second LLM to read the firm's
summary of it. V6 scores the reasoning *trail*: the ledger. This module
reduces a ledger to a flat dictionary of measured quantities that
`hae.evaluation.judge` turns into two scores, and that a human can read.

Everything here is recomputed from the ledger records, not copied from the
search loop's own counters, so a firm cannot improve its audit by reporting
better numbers. The ledger's belief hash is included so the audit can be
tied to the exact trail it describes.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from hae.epistemic.ledger import (
    CERTIFIED, FALSIFIED, Q_CERTIFIED, Q_RESOLVED, SUPPORTED, UNVERIFIED, EpistemicState,
)
from hae.epistemic.moves import MOVE_PROPOSE_HYPOTHESIS, MOVE_RUN_EXPERIMENT, MOVE_SYNTHESIZE
from hae.epistemic.value import calibration_report


def build_epistemic_audit(state: EpistemicState, search_stats: Optional[Mapping[str, Any]] = None,
                          budget_moves: int = 0, token_usage: int = 0) -> Dict[str, Any]:
    """A flat, JSON-safe audit of one firm's ledger."""
    stats = dict(search_stats or {})
    counts = state.counts()
    hyps = list(state.hypotheses.values())
    tested = [h for h in hyps if h.status != UNVERIFIED]

    def _backed(ids) -> bool:
        ids = list(ids)
        return bool(ids) and all(state.evidence_by_id(e) is not None for e in ids)

    backed_hyps = sum(1 for h in tested if _backed(h.evidence_ids))
    backed_facts = sum(1 for f in state.settled_knowledge if _backed(f.evidence_ids))
    backed_ruled = sum(1 for b in state.ruled_out if state.evidence_by_id(b.killing_evidence_id) is not None)
    claims = len(tested) + len(state.settled_knowledge) + len(state.ruled_out)
    backed = backed_hyps + backed_facts + backed_ruled
    evidence_backed_fraction = (backed / claims) if claims else 1.0

    # Status consistency: every FALSIFIED hypothesis must appear in ruled_out,
    # every CERTIFIED question must have a settled fact.
    falsified_ids = {h.hypothesis_id for h in hyps if h.status == FALSIFIED}
    ruled_ids = {b.hypothesis_id for b in state.ruled_out}
    certified_qs = {q.question_id for q in state.questions.values() if q.status == Q_CERTIFIED}
    fact_qs = {f.question_id for f in state.settled_knowledge}
    inconsistencies = len(falsified_ids - ruled_ids) + len(certified_qs - fact_qs)

    moves = list(state.move_log)
    experiments = [m for m in moves if m.move_type == MOVE_RUN_EXPERIMENT]
    proposals = [m for m in moves if m.move_type == MOVE_PROPOSE_HYPOTHESIS]
    syntheses = [m for m in moves if m.move_type == MOVE_SYNTHESIZE]
    delta_u_total = round(sum(m.delta_u for m in moves), 6)
    initial_u = state.initial_uncertainty()
    remaining_u = state.total_uncertainty()
    resolved_fraction = round(1.0 - remaining_u / initial_u, 6) if initial_u > 0 else 0.0
    moves_used = int(stats.get("moves_used") or len(moves))
    budget = int(budget_moves or stats.get("budget_moves") or 0)

    calib = calibration_report(state)
    tabu = int(stats.get("tabu_rejections", 0))
    dup = int(stats.get("duplicate_rejections", 0))
    accepted = int(stats.get("hypotheses_accepted", len(hyps)))
    proposed_total = accepted + tabu + dup

    forced = [m for m in experiments if m.forced_low_prior]
    forced_wins = sum(1 for m in forced if m.delta_u > 0 and m.hypothesis_id in state.hypotheses
                      and state.hypotheses[m.hypothesis_id].status in (SUPPORTED, CERTIFIED))

    return {
        "ledger_hash": state.state_hash(),
        "questions": counts["questions"],
        "questions_open": counts["questions_open"],
        "questions_resolved": counts["questions_resolved"],
        "questions_certified": counts["questions_certified"],
        "questions_exhausted": counts["questions_exhausted"],
        "hypotheses": counts["hypotheses"],
        "hypotheses_tested": len(tested),
        "hypotheses_supported": counts["hypotheses_supported"],
        "hypotheses_falsified": counts["hypotheses_falsified"],
        "hypotheses_certified": counts["hypotheses_certified"],
        "evidence": counts["evidence"],
        "settled_facts": counts["settled_facts"],
        "ruled_out": counts["ruled_out"],
        "evidence_backed_fraction": round(evidence_backed_fraction, 6),
        "unbacked_claims": claims - backed,
        "status_inconsistencies": inconsistencies,
        "calibration_brier": calib["brier"],
        "calibration_tested": calib["tested"],
        "overconfident_falsified": calib["overconfident_falsified"],
        "low_prior_wins": calib["low_prior_wins"],
        "forced_low_prior_picks": len(forced),
        "forced_low_prior_wins": forced_wins,
        "proposed_total": proposed_total,
        "tabu_rejections": tabu,
        "duplicate_rejections": dup,
        "moves_used": moves_used,
        "budget_moves": budget,
        "experiments": len(experiments),
        "proposal_rounds": len(proposals),
        "syntheses": len(syntheses),
        "syntheses_verified": sum(1 for m in syntheses if m.delta_u > 0),
        "delta_u_total": delta_u_total,
        "initial_uncertainty": initial_u,
        "remaining_uncertainty": remaining_u,
        "resolved_fraction": max(0.0, min(1.0, resolved_fraction)),
        "token_usage": int(token_usage),
        "stop_reason": str(stats.get("stop_reason", "")),
    }
