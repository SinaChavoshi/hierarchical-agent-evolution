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
  * Frontier: at most `frontier_size` open questions are worked on at once,
    and at most one per *cluster* (same module, same normalised oracle
    failure signature). Inside the frontier the next move goes to the
    question that is closest to paying off: one with a supported-but-
    unsynthesised hypothesis first, then one with experiments still pending,
    then the most uncertain. Gen 16 pilot finding #6 is why: with eighteen
    oracle-seeded questions and a 40-move budget, most-uncertain-first spent
    the whole budget proposing breadth-first and never ran an experiment,
    while a supported hypothesis on a quieter question starved.
  * Triage: once a question is resolved in this run, its cluster siblings
    are deferred -- the oracle will certify (or reopen) them next iteration
    for free, so spending moves on them now is waste.
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
their output as proposals; an optional fourth, `revert_patch`, lets it undo
a synthesis whose module check failed. Every move records the ledger's
feature vector before and after, so V7 can train a value head on certified
trajectories.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from hae.epistemic.gatekeeper import EvidenceGatekeeper, failure_signature
from hae.epistemic.ledger import (
    CERTIFIED, FALSIFIED, Q_RESOLVED, SUPPORTED, UNTESTABLE, UNVERIFIED,
    EpistemicState, Hypothesis, LedgerError, MoveRecord, Question, same_mechanism,
    signature_similarity,
)
from hae.epistemic.moves import (
    MOVE_ASK_QUESTION, MOVE_PROPOSE_HYPOTHESIS, MOVE_RUN_EXPERIMENT, MOVE_SYNTHESIZE,
    HypothesisProposal, QuestionProposal,
)
from hae.epistemic.value import (
    EpistemicValueFunction, HypothesisPriorHead, LearnedValueHead, epistemic_features,
    hypothesis_features, rank_among,
)
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
# Every question still open with work left is a sibling of one resolved in
# this run; the oracle settles them next iteration, so the loop stops early.
STOP_DEFERRED = "deferred_to_oracle"


def question_cluster(q: Question) -> str:
    """The triage key: questions in one cluster most likely share a root cause.

    Oracle-seeded questions cluster by (module, normalised failure signature),
    see `failure_signature`. A question with no exception text -- including
    every question the question-proposer asked -- is its own cluster, so the
    frontier never defers it on another question's account.
    """
    sig = failure_signature(q.source_failure) if q.source_failure else ""
    if not sig:
        return f"q:{q.question_id}"
    return f"{q.module}|{sig}"


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
                 rng_seed: int = 0, logger: Callable[[str], None] = print,
                 revert_patch: Optional[Callable[[str, str], Any]] = None,
                 prior_head: Optional[HypothesisPriorHead] = None,
                 prior_head_weight: float = 0.0,
                 value_head: Optional[LearnedValueHead] = None) -> None:
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
        # `revert_patch(path, previous_source)` undoes a synthesis whose module
        # check failed, when the adapter returned the pre-write text as
        # `previous_source`. Optional: without it a broken write stays in the
        # workspace (the pre-A4 behaviour) and only the ledger records the failure.
        self.revert_patch = revert_patch
        # V7 heads (shadow mode by default). `prior_head` scores every untested
        # sibling at selection time; with `prior_head_weight == 0` the choice is
        # the V6 computation verbatim and the scores are only recorded.
        # `value_head` here is for *recording* `head_v`; whether the value
        # function itself uses it is decided by whoever built `value_fn`.
        self.prior_head = prior_head
        self.prior_head_weight = max(0.0, min(1.0, float(prior_head_weight or 0.0)))
        self.value_head = value_head
        self._last_selection: Dict[str, float] = {}
        self.moves_used = 0
        self.expansions = 0
        self.forced_picks = 0
        self.question_rounds = 0
        self.trajectory: List[MoveRecord] = []
        self.synthesized_paths: List[str] = []
        # Frontier/triage state (per run; see `_select_question`).
        self.frontier: List[str] = []
        self.deferred: Dict[str, str] = {}            # question_id -> resolved sibling's id
        self.resolved_clusters: Dict[str, str] = {}   # cluster key -> question resolved this run
        self.stats: Dict[str, Any] = {
            "proposal_rounds": 0, "hypotheses_accepted": 0, "tabu_rejections": 0,
            "duplicate_rejections": 0, "probe_repairs": 0, "experiments": 0, "supported": 0,
            "falsified": 0, "inconclusive": 0, "rejected_probes": 0, "forced_low_prior_picks": 0,
            "forced_low_prior_wins": 0, "syntheses": 0, "syntheses_verified": 0,
            "syntheses_unwritten": 0, "questions_asked": 0, "proposer_errors": 0,
            "frontier_admissions": 0, "questions_deferred": 0,
            "prior_head_loaded": prior_head is not None, "value_head_loaded": value_head is not None,
            "prior_head_steered": 0,
        }

    # ------------------------------------------------------------------ #
    # Main loop
    # ------------------------------------------------------------------ #

    def run(self, budget_moves: Optional[int] = None) -> SearchResult:
        budget = int(budget_moves if budget_moves is not None else self.policy.search_budget_moves)
        stop_reason = ""
        # `max_hypothesis_rounds` is a per-search allowance, not a lifetime cap.
        # Gen 16 pilot pass 1: every question spent both rounds on replies the
        # decoder had cut off during iteration 2, and iterations 3-5 then ran
        # 0 moves ("exhausted") although the oracle still reported failures.
        # What must persist across iterations is the memory of *falsified*
        # mechanisms, and that lives in the tabu list, not in this counter.
        for q in self.state.open_questions():
            q.hypothesis_rounds = 0
        self.frontier, self.deferred, self.resolved_clusters = [], {}, {}
        open_now = self.state.open_questions()
        if open_now:
            clusters = {question_cluster(q) for q in open_now}
            self.logger(f"[epistemic] frontier size {int(self.policy.frontier_size)}: "
                        f"{len(open_now)} open question(s) in {len(clusters)} cluster(s)")
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
                open_questions = self.state.open_questions()
                if any(q.question_id in self.deferred and self._question_has_work(q) for q in open_questions):
                    stop_reason = STOP_DEFERRED
                    break
                if open_questions:
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

    def _has_ready_synthesis(self, q: Question) -> bool:
        return any(h.status == SUPPORTED and not h.patch_applied and h.synthesis_failures < MAX_SYNTHESIS_FAILURES
                   for h in self.state.hypotheses_for(q.question_id))

    def _rank_key(self, q: Question) -> Tuple[int, int, float, int, str]:
        """Smaller is more urgent: finish syntheses, then pending experiments,
        then the most uncertain question, then the one with fewer hypotheses."""
        return (0 if self._has_ready_synthesis(q) else 1,
                0 if self.state.untested_hypotheses(q.question_id) else 1,
                -q.uncertainty, len(q.hypothesis_ids), q.question_id)

    def _select_question(self) -> Optional[Question]:
        """Frontier selection with cluster triage (Gen 16 pilot finding #6).

        1. Siblings of a cluster resolved in this run are deferred: the oracle
           will certify or reopen them next iteration without spending moves.
        2. Frontier members that closed, ran out of work or were deferred leave.
        3. Free slots admit the most urgent waiting questions, at most one open
           question per cluster at a time, up to `policy.frontier_size`.
        4. The most urgent frontier member gets the move.
        """
        workable = {q.question_id: q for q in self.state.open_questions() if self._question_has_work(q)}
        for qid, q in workable.items():
            if qid in self.deferred:
                continue
            cluster = question_cluster(q)
            if cluster in self.resolved_clusters:
                self.deferred[qid] = self.resolved_clusters[cluster]
                self.stats["questions_deferred"] += 1
                self.logger(f"[epistemic] {qid} deferred to the oracle: same cluster as resolved "
                            f"{self.resolved_clusters[cluster]}")
        self.frontier = [qid for qid in self.frontier if qid in workable and qid not in self.deferred]
        size = max(1, int(self.policy.frontier_size))
        represented = {question_cluster(workable[qid]) for qid in self.frontier}
        waiting = sorted((q for qid, q in workable.items()
                          if qid not in self.frontier and qid not in self.deferred), key=self._rank_key)
        for q in waiting:
            if len(self.frontier) >= size:
                break
            cluster = question_cluster(q)
            if cluster in represented:
                continue
            self.frontier.append(q.question_id)
            represented.add(cluster)
            self.stats["frontier_admissions"] += 1
            self.logger(f"[epistemic] frontier += {q.question_id} ({len(self.frontier)}/{size}; "
                        f"u={q.uncertainty:.2f}; cluster {cluster[:70]!r})")
        if not self.frontier:
            return None
        return min((workable[qid] for qid in self.frontier), key=self._rank_key)

    def _note_resolution(self, q: Question) -> None:
        """Remember the cluster of a question resolved in this run for triage."""
        if q.status == Q_RESOLVED:
            self.resolved_clusters.setdefault(question_cluster(q), q.question_id)

    def _choose_action(self, q: Question) -> Tuple[str, Optional[Hypothesis], bool]:
        hyps = self.state.hypotheses_for(q.question_id)
        untested = [h for h in hyps if h.status == UNVERIFIED]
        # Only verdicts count towards the synthesis gate; an UNTESTABLE sibling
        # (probe refused, never ran) is not evidence of anything.
        tested = [h for h in hyps if h.tested]
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
        self._last_selection = {}
        # Shadow scoring: what the prior head says about every untested
        # sibling, recorded on the move whether or not it is allowed to steer.
        head_p: Dict[str, float] = {}
        if self.prior_head is not None:
            head_p = self._head_priors(siblings)
        quota = float(self.policy.low_prior_quota)
        if quota > 0.0 and len(untested) > 1 and (self.forced_picks + 1) / float(self.expansions) <= quota + 1e-9:
            self.forced_picks += 1
            chosen = min(untested, key=lambda h: (h.prior, h.hypothesis_id))
            self._note_selection(chosen, untested, head_p)
            return chosen, True
        n_parent = sum(h.visits for h in siblings) + 1
        prior_mass = sum(h.prior for h in siblings) or 1.0
        c = float(self.policy.c_puct)

        def score(h: Hypothesis) -> float:
            p = h.prior / prior_mass
            return h.q_value + c * p * math.sqrt(n_parent) / (1.0 + h.visits)

        best_score = max(score(h) for h in untested)
        top = [h for h in untested if abs(score(h) - best_score) < 1e-12]
        w = self.prior_head_weight if head_p else 0.0
        if w <= 0.0:
            # V6 computation, verbatim: a loaded head with weight 0 changes nothing.
            chosen = top[0] if len(top) == 1 else self.rng.choice(sorted(top, key=lambda h: h.hypothesis_id))
            self._note_selection(chosen, untested, head_p)
            return chosen, False
        # PUCT prior = (1 - w) * stated prior + w * head, normalised over siblings.
        mixed = {h.hypothesis_id: (1.0 - w) * h.prior + w * head_p.get(h.hypothesis_id, h.prior) for h in siblings}
        mixed_mass = sum(mixed.values()) or 1.0

        def mixed_score(h: Hypothesis) -> float:
            p = mixed[h.hypothesis_id] / mixed_mass
            return h.q_value + c * p * math.sqrt(n_parent) / (1.0 + h.visits)

        best_mixed = max(mixed_score(h) for h in untested)
        top_mixed = [h for h in untested if abs(mixed_score(h) - best_mixed) < 1e-12]
        chosen = top_mixed[0] if len(top_mixed) == 1 else self.rng.choice(sorted(top_mixed, key=lambda h: h.hypothesis_id))
        if all(chosen.hypothesis_id != h.hypothesis_id for h in top):
            self.stats["prior_head_steered"] += 1
        self._note_selection(chosen, untested, head_p)
        return chosen, False

    def _head_priors(self, siblings: Sequence[Hypothesis]) -> Dict[str, float]:
        """`HypothesisPriorHead` scores for every sibling of one question; {} on any failure."""
        if self.prior_head is None or not siblings:
            return {}
        q = self.state.questions.get(siblings[0].question_id)
        if q is None:
            return {}
        try:
            state_feats = epistemic_features(self.state)
            return {h.hypothesis_id: float(self.prior_head.predict(hypothesis_features(q, h, siblings, state_feats)))
                    for h in siblings}
        except Exception as exc:  # a head must never be able to stop the search
            self.logger(f"[epistemic] prior head failed on {q.question_id}: {type(exc).__name__}: {exc}")
            return {}

    def _note_selection(self, chosen: Hypothesis, untested: Sequence[Hypothesis],
                        head_p: Mapping[str, float]) -> None:
        """Stash the shadow telemetry `_do_experiment` writes into `MoveRecord.extra`."""
        if not head_p or chosen.hypothesis_id not in head_p:
            return
        others = [h for h in untested if h.hypothesis_id != chosen.hypothesis_id]
        self._last_selection = {
            "head_p": round(head_p[chosen.hypothesis_id], 6),
            "head_rank": round(rank_among(head_p[chosen.hypothesis_id], [head_p.get(h.hypothesis_id, 0.0) for h in others]), 6),
            "stated_rank": round(rank_among(chosen.prior, [h.prior for h in others]), 6),
            "prior_head_weight": round(self.prior_head_weight, 6),
        }

    # ------------------------------------------------------------------ #
    # Moves
    # ------------------------------------------------------------------ #

    def _record(self, move_type: str, question_id: str = "", hypothesis_id: str = "",
                delta_u: float = 0.0, note: str = "", forced: bool = False,
                features_before: Optional[Dict[str, float]] = None,
                value_before: float = 0.0, hash_before: str = "",
                extra: Optional[Mapping[str, float]] = None) -> MoveRecord:
        self.moves_used += 1
        shadow: Dict[str, float] = dict(extra or {})
        if self.value_head is not None:
            # Shadow value head: recorded next to the live value, never in it
            # (unless the caller also handed the head to `value_fn`).
            try:
                shadow["head_v"] = round(float(self.value_head.predict(features_before or {})), 6)
            except Exception as exc:  # pragma: no cover - defensive, a head must not stop the search
                self.logger(f"[epistemic] value head failed: {type(exc).__name__}: {exc}")
        move = MoveRecord(
            move_index=len(self.state.move_log) + 1, move_type=move_type,
            question_id=question_id, hypothesis_id=hypothesis_id,
            agent_role=self.agent_roles.get(move_type, ""),
            delta_u=round(float(delta_u), 6),
            value_before=value_before, value_after=self.value_fn.value(self.state),
            features=dict(features_before or {}),
            state_hash_before=hash_before, state_hash_after=self.state.state_hash(),
            forced_low_prior=forced, note=note[:300], extra=shadow)
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
        accepted = tabu = dup = repaired = 0
        existing = self.state.hypotheses_for(q.question_id)
        for prop in proposals[: int(self.policy.branching_k)]:
            sig = prop.signature
            if self.state.is_tabu(sig, q.question_id, claim=prop.claim):
                tabu += 1
                continue
            # A proposal that restates a mechanism whose probe the gatekeeper
            # refused is a repair, not a duplicate: the new probe is swapped in
            # and the hypothesis returns to the untested pool. Prior and id are
            # kept, so calibration still scores the belief that was originally
            # declared. Re-submitting the identical broken probe is a duplicate.
            parked = [h for h in existing if h.status == UNTESTABLE
                      and same_mechanism(sig, h.mechanism_signature, prop.claim, h.claim)]
            if parked:
                target = max(parked, key=lambda h: (signature_similarity(sig, h.mechanism_signature),
                                                    h.hypothesis_id))
                if prop.probe_code.strip() == target.probe_code.strip():
                    dup += 1
                    continue
                try:
                    self.state.repair_probe(target.hypothesis_id, prop.probe_code, prop.prediction)
                except LedgerError:
                    dup += 1
                    continue
                repaired += 1
                continue
            # A mechanism falsified against a *previous* shape of this
            # question's oracle failure is history (tabu released it above);
            # the duplicate check must not re-block it on the same grounds.
            if any(same_mechanism(sig, h.mechanism_signature, prop.claim, h.claim)
                   for h in existing if h.status != UNTESTABLE
                   and not (h.status == FALSIFIED and h.epoch != q.failure_epoch)):
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
        self.stats["probe_repairs"] += repaired
        summary = (f"{accepted} accepted, {repaired} repaired, {tabu} tabu, {dup} duplicate "
                   f"of {len(proposals)} proposed")
        self._record(MOVE_PROPOSE_HYPOTHESIS, q.question_id, note=summary,
                     features_before=feats, value_before=v0, hash_before=h0)
        self.logger(f"[epistemic] {q.question_id}: proposed {len(proposals)} -> {accepted} accepted, "
                    f"{repaired} repaired, {tabu} tabu, {dup} duplicate")

    def _do_experiment(self, q: Question, h: Hypothesis, forced: bool) -> None:
        feats, v0, h0 = self._snapshot()
        shadow, self._last_selection = dict(self._last_selection), {}
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
                     features_before=feats, value_before=v0, hash_before=h0, extra=shadow)
        head_note = f" head_p={shadow['head_p']:.2f}" if "head_p" in shadow else ""
        self.logger(f"[epistemic] {q.question_id}/{h.hypothesis_id} prior={h.prior:.2f}{head_note} "
                    f"{'FORCED ' if forced else ''}-> {h.status} (dU={delta:.3f}) {evidence.detail[:120]}")

    def _do_synthesize(self, q: Question, h: Hypothesis) -> None:
        feats, v0, h0 = self._snapshot()
        self.stats["syntheses"] += 1
        try:
            result = dict(self.synthesize_patch(q, h, self.state) or {})
        except Exception as exc:
            self.stats["proposer_errors"] += 1
            h.synthesis_failures += 1
            note = f"synthesis error: {type(exc).__name__}: {exc}"
            self._record(MOVE_SYNTHESIZE, q.question_id, h.hypothesis_id, note=note,
                         features_before=feats, value_before=v0, hash_before=h0)
            self.logger(f"[epistemic] {q.question_id}/{h.hypothesis_id} {note[:160]} "
                        f"(failure {h.synthesis_failures}/{MAX_SYNTHESIS_FAILURES})")
            return
        path = str(result.get("path") or q.module or "")
        # Optional telemetry from the runner's adapter (A2, A4): which rung of
        # the synthesis ladder wrote, whether the whole-module rewrite was
        # skipped on a double no-op, how long the move took, and whether a
        # silent no-op had to be re-prompted (author mode only since A4).
        mode = str(result.get("mode") or "")
        if mode == "plan":
            self.stats["syntheses_by_plan"] = self.stats.get("syntheses_by_plan", 0) + 1
        if mode == "function":
            self.stats["syntheses_by_function"] = self.stats.get("syntheses_by_function", 0) + 1
        if mode == "author":   # the target module did not exist; the move created it
            self.stats["syntheses_authored"] = self.stats.get("syntheses_authored", 0) + 1
        if result.get("rewrite_skipped"):
            self.stats["synthesis_rewrites_skipped"] = self.stats.get("synthesis_rewrites_skipped", 0) + 1
        if result.get("retried"):
            self.stats["synthesis_noop_retries"] = self.stats.get("synthesis_noop_retries", 0) + 1
        if result.get("noop_recovered"):
            self.stats["synthesis_noop_recoveries"] = self.stats.get("synthesis_noop_recoveries", 0) + 1
        try:
            self.stats["synthesis_seconds"] = round(
                float(self.stats.get("synthesis_seconds", 0.0)) + float(result.get("elapsed_s") or 0.0), 3)
        except (TypeError, ValueError):
            pass
        if not result.get("written", True) or not path:
            h.synthesis_failures += 1
            self.stats["syntheses_unwritten"] += 1
            note = f"synthesis wrote nothing: {str(result.get('summary', ''))[:160]}"
            self._record(MOVE_SYNTHESIZE, q.question_id, h.hypothesis_id, note=note,
                         features_before=feats, value_before=v0, hash_before=h0)
            self.logger(f"[epistemic] {q.question_id}/{h.hypothesis_id} {note[:200]} "
                        f"(failure {h.synthesis_failures}/{MAX_SYNTHESIS_FAILURES})")
            return
        evidence = self.gatekeeper.verify_module(self.state, path, q.question_id, h.hypothesis_id)
        delta = self.gatekeeper.apply_synthesis(self.state, q, h, evidence)
        self._note_resolution(q)
        reverted = False
        if evidence.matched_prediction:
            self.stats["syntheses_verified"] += 1
            if path not in self.synthesized_paths:
                self.synthesized_paths.append(path)
        elif self.revert_patch is not None and isinstance(result.get("previous_source"), str):
            # The write broke the module (compile/import/firm tests): put the
            # pre-synthesis text back so the next move -- and the oracle --
            # see the last known state, not the failed experiment.
            reverted = self._revert(path, result["previous_source"], q, h)
        note = f"{path} [{mode or 'unknown'}]: {evidence.detail}"
        if reverted:
            note += "; reverted to pre-synthesis module"
        self._record(MOVE_SYNTHESIZE, q.question_id, h.hypothesis_id, delta_u=delta, note=note,
                     features_before=feats, value_before=v0, hash_before=h0)
        self.logger(f"[epistemic] {q.question_id}/{h.hypothesis_id} synthesized {path} [{mode or 'unknown'}]: "
                    f"{evidence.detail[:120]}{' (reverted)' if reverted else ''} (dU={delta:.3f})")

    def _revert(self, path: str, previous_source: str, q: Question, h: Hypothesis) -> bool:
        """Calls `revert_patch`; a refusal (error status) or exception is logged, never raised."""
        try:
            outcome = self.revert_patch(path, previous_source)  # type: ignore[misc]
        except Exception as exc:
            self.logger(f"[epistemic] {q.question_id}/{h.hypothesis_id} revert of {path} failed: "
                        f"{type(exc).__name__}: {exc}")
            return False
        if isinstance(outcome, Mapping) and str(outcome.get("status", "ok")) not in ("ok", "success"):
            self.logger(f"[epistemic] {q.question_id}/{h.hypothesis_id} revert of {path} refused: "
                        f"{str(outcome.get('error') or outcome.get('message') or outcome.get('status'))[:160]}")
            return False
        self.stats["syntheses_reverted"] = self.stats.get("syntheses_reverted", 0) + 1
        self.logger(f"[epistemic] {q.question_id}/{h.hypothesis_id} reverted {path} to its pre-synthesis "
                    "content after a failed module check")
        return True

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
