"""The epistemic search loop: System 2's control flow over System 1's proposals.

This is where "search" happens, and it is worth being precise about what is
searched. The tree is not a tree of code edits; it is a tree of *questions*
and *hypotheses*. A node is a question with some uncertainty; its children
are hypotheses; expanding a hypothesis means running its probe through the
gatekeeper and observing what happened. The reward for a move is the
uncertainty it actually resolved (`delta_u`), which only evidence can
produce. Synthesising code is a move the loop makes *after* a hypothesis has
survived a test -- and, by policy, only after enough competing hypotheses
have been tested that the survivor is not merely the first plausible story.

Selection
---------
  * Question: the open question with the most uncertainty.
  * Hypothesis: PUCT over the question's untested hypotheses,
        score = Q + c_puct * P * sqrt(N_parent) / (1 + N)
    where P is the proposer's prior normalised over siblings. The prior is a
    *tie-breaker for exploration order*. It never removes a hypothesis.
  * Forced exploration (the Move-37 rule): a fraction `low_prior_quota` of
    all expansions must pick the *lowest*-prior untested hypothesis. The
    thing System 1 finds least plausible gets tested anyway. Gen 15 showed
    why: eight firms converged on the same plausible repair for the same two
    tests and plateaued at 46-49/50 for five iterations.
  * Tabu: a proposal whose mechanism matches one already falsified for this
    question is rejected before it costs a turn.

The loop never calls an LLM itself. It receives three adapters -- propose
hypotheses, synthesise a patch, (optionally) ask questions -- and treats
their output as proposals. Every move records the ledger's feature vector
before and after, so V7 can train a value head on certified trajectories.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from hae.epistemic.gatekeeper import EvidenceGatekeeper
from hae.epistemic.ledger import (
    CERTIFIED, FALSIFIED, SUPPORTED, UNVERIFIED, TABU_SIMILARITY,
    EpistemicState, Hypothesis, MoveRecord, Question, signature_similarity,
)
from hae.epistemic.moves import (
    MOVE_ASK_QUESTION, MOVE_PROPOSE_HYPOTHESIS, MOVE_RUN_EXPERIMENT, MOVE_SYNTHESIZE,
    HypothesisProposal, QuestionProposal,
)
from hae.epistemic.value import EpistemicValueFunction, epistemic_features
from hae.genome.schema import EpistemicPolicyGene

ProposeHypothesesFn = Callable[[Question, EpistemicState, int], Sequence[HypothesisProposal]]
SynthesizeFn = Callable[[Question, Hypothesis, EpistemicState], Mapping[str, Any]]
ProposeQuestionsFn = Callable[[EpistemicState], Sequence[QuestionProposal]]

MAX_SYNTHESIS_FAILURES = 2

STOP_BUDGET = "budget_moves"
STOP_TOKENS = "token_budget"
STOP_STAGNATION = "stagnation"
STOP_RESOLVED = "all_resolved"
STOP_EXHAUSTED = "exhausted"


@dataclass
class SearchResult:
    trajectory: List[MoveRecord]
    final_state: EpistemicState
    moves_used: int
    delta_u_total: float
    stop_reason: str
    synthesized_paths: List[str] = field(default_factory=list)
    stats: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "moves_used": self.moves_used,
            "delta_u_total": round(self.delta_u_total, 6),
            "stop_reason": self.stop_reason,
            "synthesized_paths": list(self.synthesized_paths),
            "stats": dict(self.stats),
            "trajectory": [m.to_dict() for m in self.trajectory],
        }


class EpistemicSearchLoop:
    """Runs one epistemic search over a ledger within a move budget."""

    def __init__(self, state: EpistemicState, gatekeeper: EvidenceGatekeeper,
                 value_fn: EpistemicValueFunction, policy: EpistemicPolicyGene,
                 propose_hypotheses: ProposeHypothesesFn, synthesize_patch: SynthesizeFn,
                 propose_questions: Optional[ProposeQuestionsFn] = None,
                 may_continue: Optional[Callable[[], bool]] = None,
                 agent_roles: Optional[Mapping[str, str]] = None,
                 rng_seed: int = 0, logger: Callable[[str], None] = print) -> None:
        self.state = state
        self.gatekeeper = gatekeeper
        self.value_fn = value_fn
        self.policy = policy
        self.propose_hypotheses = propose_hypotheses
        self.synthesize_patch = synthesize_patch
        self.propose_questions = propose_questions
        self.may_continue = may_continue or (lambda: True)
        self.agent_roles = dict(agent_roles or {})
        self.rng = random.Random(rng_seed)
        self.logger = logger
        self.moves_used = 0
        self.expansions = 0
        self.forced_picks = 0
        self.question_rounds = 0
        self.trajectory: List[MoveRecord] = []
        self.synthesized_paths: List[str] = []
        self.stats: Dict[str, Any] = {
            "proposal_rounds": 0, "hypotheses_accepted": 0, "tabu_rejections": 0,
            "duplicate_rejections": 0, "experiments": 0, "supported": 0, "falsified": 0,
            "inconclusive": 0, "rejected_probes": 0, "forced_low_prior_picks": 0,
            "forced_low_prior_wins": 0, "syntheses": 0, "syntheses_verified": 0,
            "syntheses_unwritten": 0, "questions_asked": 0, "proposer_errors": 0,
        }

    # ------------------------------------------------------------------ #
    # Main loop
    # ------------------------------------------------------------------ #

    def run(self, budget_moves: Optional[int] = None) -> SearchResult:
        budget = int(budget_moves if budget_moves is not None else self.policy.search_budget_moves)
        stop_reason = ""
        while True:
            if self.moves_used >= budget:
                stop_reason = STOP_BUDGET
                break
            if not self.may_continue():
                stop_reason = STOP_TOKENS
                break
            if self.moves_used > 0 and self._stagnant_moves() >= self.policy.max_stagnant_moves:
                stop_reason = STOP_STAGNATION
                break
            question = self._select_question()
            if question is None:
                if self.state.open_questions():
                    stop_reason = STOP_EXHAUSTED
                    break
                if self.propose_questions is not None and self.question_rounds < 1:
                    self._ask_questions()
                    continue
                stop_reason = STOP_RESOLVED
                break
            action, hypothesis, forced = self._choose_action(question)
            if action == "synthesize":
                self._do_synthesize(question, hypothesis)
            elif action == "experiment":
                self._do_experiment(question, hypothesis, forced)
            elif action == "propose":
                self._do_propose(question)
            else:  # pragma: no cover - _select_question excludes these
                stop_reason = STOP_EXHAUSTED
                break
        self.stats.update({"moves_used": self.moves_used, "budget_moves": budget, "stop_reason": stop_reason})
        return SearchResult(
            trajectory=list(self.trajectory), final_state=self.state, moves_used=self.moves_used,
            delta_u_total=round(sum(m.delta_u for m in self.trajectory), 6),
            stop_reason=stop_reason, synthesized_paths=list(self.synthesized_paths),
            stats=dict(self.stats))

    # ------------------------------------------------------------------ #
    # Selection
    # ------------------------------------------------------------------ #

    def _stagnant_moves(self) -> int:
        depth = 0
        for move in reversed(self.trajectory):
            if move.delta_u > 0.0:
                break
            depth += 1
        return depth

    def _question_has_work(self, q: Question) -> bool:
        if self.state.untested_hypotheses(q.question_id):
            return True
        if any(h.status == SUPPORTED and not h.patch_applied and h.synthesis_failures < MAX_SYNTHESIS_FAILURES
               for h in self.state.hypotheses_for(q.question_id)):
            return True
        return q.hypothesis_rounds < self.policy.max_hypothesis_rounds

    def _select_question(self) -> Optional[Question]:
        candidates = [q for q in self.state.open_questions() if self._question_has_work(q)]
        if not candidates:
            return None
        candidates.sort(key=lambda q: (-q.uncertainty, len(q.hypothesis_ids), q.question_id))
        return candidates[0]

    def _choose_action(self, q: Question) -> Tuple[str, Optional[Hypothesis], bool]:
        hyps = self.state.hypotheses_for(q.question_id)
        untested = [h for h in hyps if h.status == UNVERIFIED]
        tested = [h for h in hyps if h.status != UNVERIFIED]
        ready = [h for h in hyps if h.status == SUPPORTED and not h.patch_applied
                 and h.synthesis_failures < MAX_SYNTHESIS_FAILURES]
        if ready and (len(tested) >= self.policy.min_hypotheses_before_synthesis or not untested):
            best = max(ready, key=lambda h: (h.posterior, -h.synthesis_failures, h.hypothesis_id))
            return "synthesize", best, False
        if untested:
            chosen, forced = self._select_hypothesis(hyps, untested)
            return "experiment", chosen, forced
        if q.hypothesis_rounds < self.policy.max_hypothesis_rounds:
            return "propose", None, False
        return "none", None, False

    def _select_hypothesis(self, siblings: Sequence[Hypothesis],
                           untested: Sequence[Hypothesis]) -> Tuple[Hypothesis, bool]:
        self.expansions += 1
        quota = float(self.policy.low_prior_quota)
        if quota > 0.0 and len(untested) > 1 and (self.forced_picks + 1) / float(self.expansions) <= quota + 1e-9:
            self.forced_picks += 1
            chosen = min(untested, key=lambda h: (h.prior, h.hypothesis_id))
            return chosen, True
        n_parent = sum(h.visits for h in siblings) + 1
        prior_mass = sum(h.prior for h in siblings) or 1.0
        c = float(self.policy.c_puct)

        def score(h: Hypothesis) -> float:
            p = h.prior / prior_mass
            return h.q_value + c * p * math.sqrt(n_parent) / (1.0 + h.visits)

        best_score = max(score(h) for h in untested)
        top = [h for h in untested if abs(score(h) - best_score) < 1e-12]
        chosen = top[0] if len(top) == 1 else self.rng.choice(sorted(top, key=lambda h: h.hypothesis_id))
        return chosen, False

    # ------------------------------------------------------------------ #
    # Moves
    # ------------------------------------------------------------------ #

    def _record(self, move_type: str, question_id: str = "", hypothesis_id: str = "",
                delta_u: float = 0.0, note: str = "", forced: bool = False,
                features_before: Optional[Dict[str, float]] = None,
                value_before: float = 0.0, hash_before: str = "") -> MoveRecord:
        self.moves_used += 1
        move = MoveRecord(
            move_index=len(self.state.move_log) + 1, move_type=move_type,
            question_id=question_id, hypothesis_id=hypothesis_id,
            agent_role=self.agent_roles.get(move_type, ""),
            delta_u=round(float(delta_u), 6),
            value_before=value_before, value_after=self.value_fn.value(self.state),
            features=dict(features_before or {}),
            state_hash_before=hash_before, state_hash_after=self.state.state_hash(),
            forced_low_prior=forced, note=note[:300])
        self.state.record_move(move)
        self.trajectory.append(move)
        return move

    def _snapshot(self) -> Tuple[Dict[str, float], float, str]:
        feats = epistemic_features(self.state)
        return feats, self.value_fn.value_from_features(feats), self.state.state_hash()

    def _do_propose(self, q: Question) -> None:
        feats, v0, h0 = self._snapshot()
        q.hypothesis_rounds += 1
        self.stats["proposal_rounds"] += 1
        try:
            proposals = list(self.propose_hypotheses(q, self.state, int(self.policy.branching_k)) or [])
        except Exception as exc:  # the proposer is an LLM call; it may fail
            self.stats["proposer_errors"] += 1
            self._record(MOVE_PROPOSE_HYPOTHESIS, q.question_id, note=f"proposer error: {type(exc).__name__}: {exc}",
                         features_before=feats, value_before=v0, hash_before=h0)
            return
        accepted = tabu = dup = 0
        existing = self.state.hypotheses_for(q.question_id)
        for prop in proposals[: int(self.policy.branching_k)]:
            sig = prop.signature
            if self.state.is_tabu(sig, q.question_id):
                tabu += 1
                continue
            if any(signature_similarity(sig, h.mechanism_signature) >= TABU_SIMILARITY for h in existing):
                dup += 1
                continue
            h = self.state.add_hypothesis(
                q.question_id, prop.claim, prop.mechanism, prop.prior, prop.probe_code,
                prop.prediction, proposed_by=self.agent_roles.get(MOVE_PROPOSE_HYPOTHESIS, ""))
            existing.append(h)
            accepted += 1
        self.stats["hypotheses_accepted"] += accepted
        self.stats["tabu_rejections"] += tabu
        self.stats["duplicate_rejections"] += dup
        self._record(MOVE_PROPOSE_HYPOTHESIS, q.question_id,
                     note=f"{accepted} accepted, {tabu} tabu, {dup} duplicate of {len(proposals)} proposed",
                     features_before=feats, value_before=v0, hash_before=h0)
        self.logger(f"[epistemic] {q.question_id}: proposed {len(proposals)} -> {accepted} accepted, "
                    f"{tabu} tabu, {dup} duplicate")

    def _do_experiment(self, q: Question, h: Hypothesis, forced: bool) -> None:
        feats, v0, h0 = self._snapshot()
        evidence = self.gatekeeper.run_experiment(self.state, h)
        delta = self.gatekeeper.apply(self.state, h, evidence)
        self.state.mark_visit(h.hypothesis_id, delta)
        self.stats["experiments"] += 1
        if forced:
            self.stats["forced_low_prior_picks"] += 1
        if evidence.kind == "probe_rejected":
            self.stats["rejected_probes"] += 1
        elif h.status == SUPPORTED:
            self.stats["supported"] += 1
            if forced:
                self.stats["forced_low_prior_wins"] += 1
        elif h.status == FALSIFIED:
            self.stats["falsified"] += 1
        else:
            self.stats["inconclusive"] += 1
        self._record(MOVE_RUN_EXPERIMENT, q.question_id, h.hypothesis_id, delta_u=delta,
                     note=f"{h.status}: {evidence.detail}", forced=forced,
                     features_before=feats, value_before=v0, hash_before=h0)
        self.logger(f"[epistemic] {q.question_id}/{h.hypothesis_id} prior={h.prior:.2f} "
                    f"{'FORCED ' if forced else ''}-> {h.status} (dU={delta:.3f}) {evidence.detail[:120]}")

    def _do_synthesize(self, q: Question, h: Hypothesis) -> None:
        feats, v0, h0 = self._snapshot()
        self.stats["syntheses"] += 1
        try:
            result = dict(self.synthesize_patch(q, h, self.state) or {})
        except Exception as exc:
            self.stats["proposer_errors"] += 1
            h.synthesis_failures += 1
            self._record(MOVE_SYNTHESIZE, q.question_id, h.hypothesis_id,
                         note=f"synthesis error: {type(exc).__name__}: {exc}",
                         features_before=feats, value_before=v0, hash_before=h0)
            return
        path = str(result.get("path") or q.module or "")
        if not result.get("written", True) or not path:
            h.synthesis_failures += 1
            self.stats["syntheses_unwritten"] += 1
            self._record(MOVE_SYNTHESIZE, q.question_id, h.hypothesis_id,
                         note=f"synthesis wrote nothing: {str(result.get('summary', ''))[:160]}",
                         features_before=feats, value_before=v0, hash_before=h0)
            return
        evidence = self.gatekeeper.verify_module(self.state, path, q.question_id, h.hypothesis_id)
        delta = self.gatekeeper.apply_synthesis(self.state, q, h, evidence)
        if evidence.matched_prediction:
            self.stats["syntheses_verified"] += 1
            if path not in self.synthesized_paths:
                self.synthesized_paths.append(path)
        self._record(MOVE_SYNTHESIZE, q.question_id, h.hypothesis_id, delta_u=delta,
                     note=f"{path}: {evidence.detail}",
                     features_before=feats, value_before=v0, hash_before=h0)
        self.logger(f"[epistemic] {q.question_id}/{h.hypothesis_id} synthesized {path}: "
                    f"{evidence.detail[:120]} (dU={delta:.3f})")

    def _ask_questions(self) -> None:
        feats, v0, h0 = self._snapshot()
        self.question_rounds += 1
        try:
            proposals = list(self.propose_questions(self.state) or [])  # type: ignore[misc]
        except Exception as exc:
            self.stats["proposer_errors"] += 1
            proposals = []
            note = f"question proposer error: {type(exc).__name__}: {exc}"
        else:
            note = f"{len(proposals)} question(s) proposed"
        added = 0
        for prop in proposals:
            before = len(self.state.questions)
            self.state.add_question(prop.text, prop.module, prop.uncertainty)
            added += int(len(self.state.questions) > before)
        self.stats["questions_asked"] += added
        self._record(MOVE_ASK_QUESTION, note=f"{note}; {added} new",
                     features_before=feats, value_before=v0, hash_before=h0)
