"""Adaptive Branching MCTS (AB-MCTS-A) step selection for the epistemic loop (V9).

Reference: Inoue, Misaki, Imajuku, Kuroki, Nakamura, Akiba, "Wider or Deeper?
Scaling LLM Inference-Time Compute with Adaptive Branching Tree Search",
arXiv:2503.04412.

The problem this solves
-----------------------
At an open question the loop can go *deeper* -- run the probe of a hypothesis
it already has, or synthesise a patch from one that survived -- or *wider* --
ask for a new hypothesis, or hire a specialist who would ask differently. The
PUCT loop (mcts.py, `_choose_action`) cannot compare an unexpanded sibling
against existing children, so it hard-codes the answer: synthesise before
experimenting before proposing, propose `branching_k` at a time, at most
`max_hypothesis_rounds` times, and ask the CEO about hiring with a coin flip
outside the tree. Easy bugs pay for siblings they never needed; hard bugs hit
the round cap with moves to spare.

AB-MCTS puts a virtual GEN node ("make a new child") next to the real children
and lets Thompson sampling over Bayesian posteriors decide, every step, whether
widening or deepening looks more promising given what the rewards so far say.
The AB-MCTS-A variant ("node aggregation") gives the GEN node its own
posterior, aggregated from the rewards of the children it has produced. This
module is that rule for one question of the epistemic tree, with two GEN
nodes instead of one: an epistemic GEN per active role (a new hypothesis from
that specialist) and an organisational GEN for the CEO (a new specialist).

Arms at an open question q
--------------------------
  ("experiment", h)        an UNVERIFIED hypothesis h. Prior mean: h.prior,
                           the proposer's stated probability. Evidence: the
                           reward of every pull (an inconclusive probe leaves
                           h untested but lowers the arm).
  ("synthesize", h)        a SUPPORTED, unpatched h with fewer than
                           MAX_SYNTHESIS_FAILURES failures. Prior mean:
                           h.posterior, re-read every step. Evidence: the
                           reward of every synthesis attempt on h.
  ("gen_hypothesis", r)    one per active role able to probe; a single unnamed
                           arm when the loop runs without an organisation or
                           no active role can probe (the bound proposer).
                           Prior mean: r.support_rate when the breeder has
                           data on r, else the CEO's optimistic_prior (0.5
                           for the unnamed arm). Evidence: the reward of every
                           tested hypothesis r proposed on q; 0 for a draw that
                           added nothing (every proposal tabu or duplicate, or
                           a proposer error).
  ("gen_specialist", ceo)  present when the loop has a recruit callback,
                           `OrgState.can_recruit` allows it and the previous
                           move was not itself a recruit. When a question has
                           no other arm left (nothing to deepen, GEN arms
                           exhausted) the cooldown is waived, headcount and the
                           no-two-in-a-row rule are not: hiring is then the one
                           move that can revive the question. Prior mean:
                           `OrgState.recruit_prior()` -- the CEO gene's
                           sigmoid over stall, uncovered modules and
                           headcount -- re-read every step, so pressure moves
                           the prior instead of gating a coin flip. Evidence:
                           0 per declined recruit; the reward of every tested
                           hypothesis proposed by a role hired during this
                           search (the CEO's children, aggregated).

Every arm is Beta(1 + tau * p + wins, 1 + tau * (1 - p) + losses) with
tau = `EpistemicPolicyGene.ab_prior_strength` and p the prior mean. Selection
draws theta ~ Beta(alpha, beta) for each arm with the loop's seeded rng and
takes the arm with the largest draw; ties fall to the smaller key.

Rewards are in [0, 1]:
    tested hypothesis   y = clamp(dU + 0.5 * [verdict is SUPPORTED], 0, 1)
    synthesis           y = clamp(dU + 0.5 * [module check passed], 0, 1)
where dU is the move's `delta_u` from the ledger (uncertainty actually
resolved). A falsification early on carries real dU and is rewarded for it; a
late one that resolves nothing scores 0, and that is what makes a role's GEN
arm decay until the CEO arm, or another role's, wins.

When is a question out of work?
------------------------------
Never by a round cap. A question keeps GEN arms until `GEN_EMPTY_LIMIT`
consecutive GEN draws added nothing (the proposer cannot find a new
mechanism); a recruit resets that. The loop's budget and stagnation stops are
unchanged. Thompson sampling needs no `min_hypotheses_before_synthesis`,
`low_prior_quota` or `max_hypothesis_rounds`; those fields are PUCT's and are
ignored here.

Nothing in this module calls an LLM, runs code or writes a belief: it only
decides which of the loop's existing moves runs next and keeps the posteriors.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from hae.epistemic.ledger import SUPPORTED, UNVERIFIED, EpistemicState, Hypothesis, Question
from hae.epistemic.org import OrgState

ARM_EXPERIMENT = "experiment"
ARM_SYNTHESIZE = "synthesize"
ARM_GEN_HYPOTHESIS = "gen_hypothesis"
ARM_GEN_SPECIALIST = "gen_specialist"
CEO_IDENT = "ceo"

# Prior mean of the unnamed GEN arm (no organisation, or no role able to probe).
UNNAMED_GEN_PRIOR = 0.5
# Consecutive GEN draws on one question that add no hypothesis before the
# question's GEN arms are considered exhausted.
GEN_EMPTY_LIMIT = 2
# Mirrors mcts.MAX_SYNTHESIS_FAILURES without importing the loop (no cycle).
MAX_SYNTHESIS_FAILURES = 2
# Keep the per-step trace bounded in long searches.
TRACE_LIMIT = 400


def clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def hypothesis_reward(delta_u: float, status: str) -> float:
    """y = clamp(dU + 0.5 * [SUPPORTED], 0, 1) for a tested (or refused) hypothesis."""
    return clamp01(float(delta_u) + (0.5 if status == SUPPORTED else 0.0))


def synthesis_reward(delta_u: float, verified: bool) -> float:
    """y = clamp(dU + 0.5 * [module check passed], 0, 1) for a synthesis attempt."""
    return clamp01(float(delta_u) + (0.5 if verified else 0.0))


@dataclass
class BetaArm:
    """A conjugate Beta posterior: prior Beta(1 + tau*p, 1 + tau*(1-p)) plus observed rewards."""

    kind: str
    ident: str
    prior_mean: float
    tau: float
    wins: float = 0.0
    losses: float = 0.0
    pulls: int = 0
    observations: int = 0
    last_theta: float = 0.0
    history: List[float] = field(default_factory=list)

    @property
    def key(self) -> Tuple[str, str]:
        return (self.kind, self.ident)

    @property
    def alpha(self) -> float:
        return 1.0 + self.tau * clamp01(self.prior_mean) + self.wins

    @property
    def beta(self) -> float:
        return 1.0 + self.tau * (1.0 - clamp01(self.prior_mean)) + self.losses

    @property
    def mean(self) -> float:
        return self.alpha / (self.alpha + self.beta)

    def sample(self, rng: random.Random) -> float:
        self.last_theta = float(rng.betavariate(self.alpha, self.beta))
        return self.last_theta

    def observe(self, y: float) -> float:
        y = clamp01(y)
        self.wins += y
        self.losses += 1.0 - y
        self.observations += 1
        if len(self.history) < 64:
            self.history.append(round(y, 4))
        return y

    def to_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "ident": self.ident, "prior_mean": round(self.prior_mean, 4),
                "alpha": round(self.alpha, 4), "beta": round(self.beta, 4), "mean": round(self.mean, 4),
                "pulls": self.pulls, "observations": self.observations, "rewards": list(self.history)}


class AdaptiveBranchingController:
    """Keeps the Beta arms of every question the loop touched and picks the next move."""

    def __init__(self, prior_strength: float, rng: random.Random) -> None:
        self.tau = max(0.0, float(prior_strength))
        self.rng = rng
        self._arms: Dict[Tuple[str, str, str], BetaArm] = {}
        self.gen_empty: Dict[str, int] = {}
        self.trace: List[Dict[str, Any]] = []
        self.counts: Dict[str, int] = {
            "steps": 0, "deepen_experiment": 0, "deepen_synthesize": 0, "widen_hypothesis": 0,
            "widen_specialist": 0, "gen_empty_draws": 0, "gen_exhausted_questions": 0,
            "widest_question": 0,
        }

    # ------------------------------------------------------------------ #
    # Arms
    # ------------------------------------------------------------------ #

    def arm(self, question_id: str, kind: str, ident: str, prior_mean: float,
            live_prior: bool = False) -> BetaArm:
        """The arm for (question, kind, ident); created with `prior_mean` on first use.

        `live_prior=True` re-reads the prior every step (a synthesis arm
        follows the hypothesis' posterior, the CEO arm the live recruit prior);
        the evidence already accumulated on the arm is kept.
        """
        key = (question_id, kind, ident)
        a = self._arms.get(key)
        if a is None:
            a = BetaArm(kind=kind, ident=ident, prior_mean=clamp01(prior_mean), tau=self.tau)
            self._arms[key] = a
        elif live_prior:
            a.prior_mean = clamp01(prior_mean)
        return a

    def arms_for(self, question_id: str) -> List[BetaArm]:
        return [a for (qid, _k, _i), a in self._arms.items() if qid == question_id]

    def gen_exhausted(self, question_id: str) -> bool:
        return self.gen_empty.get(question_id, 0) >= GEN_EMPTY_LIMIT

    @staticmethod
    def role_gen_prior(role: Any, org: Optional[OrgState]) -> float:
        """support_rate when the breeder has data on the role, else the CEO's optimistic prior."""
        if role is None:
            return UNNAMED_GEN_PRIOR
        if int(getattr(role, "uses", 0) or 0) > 0:
            return clamp01(getattr(role, "support_rate", 0.0))
        return clamp01(org.policy.optimistic_prior) if org is not None else UNNAMED_GEN_PRIOR

    def candidate_arms(self, q: Question, state: EpistemicState, org: Optional[OrgState],
                       recruit_eligible: bool) -> List[BetaArm]:
        """The arm set A(q) for this step. Order is deterministic (ties break on it)."""
        qid = q.question_id
        hyps = state.hypotheses_for(qid)
        arms: List[BetaArm] = []
        for h in hyps:
            if h.status == UNVERIFIED:
                arms.append(self.arm(qid, ARM_EXPERIMENT, h.hypothesis_id, h.prior))
        for h in hyps:
            if h.status == SUPPORTED and not h.patch_applied and h.synthesis_failures < MAX_SYNTHESIS_FAILURES:
                arms.append(self.arm(qid, ARM_SYNTHESIZE, h.hypothesis_id, h.posterior, live_prior=True))
        if not self.gen_exhausted(qid):
            probers = [r for r in (org.active_roles if org is not None else []) if r.can("probe")]
            if probers:
                for r in probers:
                    arms.append(self.arm(qid, ARM_GEN_HYPOTHESIS, r.role_id, self.role_gen_prior(r, org)))
            else:
                arms.append(self.arm(qid, ARM_GEN_HYPOTHESIS, "", UNNAMED_GEN_PRIOR))
        if recruit_eligible and org is not None:
            arms.append(self.arm(qid, ARM_GEN_SPECIALIST, CEO_IDENT, org.recruit_prior(), live_prior=True))
        return arms

    def can_widen(self, q: Question, recruit_eligible: bool) -> bool:
        """Whether q still has a widening move: a GEN arm not yet exhausted, or an eligible recruit."""
        return (not self.gen_exhausted(q.question_id)) or bool(recruit_eligible)

    # ------------------------------------------------------------------ #
    # Selection
    # ------------------------------------------------------------------ #

    def select(self, arms: Sequence[BetaArm]) -> Tuple[BetaArm, Dict[str, float]]:
        """Thompson sampling: one Beta draw per arm, argmax; ties fall to the earlier arm."""
        if not arms:
            raise ValueError("AB-MCTS: no candidate arms")
        draws: Dict[str, float] = {}
        best: Optional[BetaArm] = None
        best_theta = -1.0
        for a in arms:
            theta = a.sample(self.rng)
            draws[f"{a.kind}:{a.ident or '-'}"] = round(theta, 6)
            if theta > best_theta:
                best, best_theta = a, theta
        assert best is not None
        best.pulls += 1
        self.counts["steps"] += 1
        return best, draws

    # ------------------------------------------------------------------ #
    # Rewards (called by the loop after the chosen move ran)
    # ------------------------------------------------------------------ #

    def _recruited_this_search(self, org: Optional[OrgState], role_id: str) -> bool:
        if org is None or not role_id:
            return False
        st = org.stats.get(role_id)
        return st is not None and int(st.recruited_at_move) >= 0

    def observe_experiment(self, q: Question, h: Hypothesis, delta_u: float, org: Optional[OrgState]) -> float:
        """Reward the experiment arm, the proposer's GEN arm and, for a recruit's hypothesis, the CEO arm."""
        y = hypothesis_reward(delta_u, h.status)
        qid = q.question_id
        self.counts["deepen_experiment"] += 1
        exp = self._arms.get((qid, ARM_EXPERIMENT, h.hypothesis_id))
        if exp is not None:
            exp.observe(y)
        gen = self._arms.get((qid, ARM_GEN_HYPOTHESIS, h.role_id or ""))
        if gen is not None:
            gen.observe(y)
        if self._recruited_this_search(org, h.role_id):
            ceo = self._arms.get((qid, ARM_GEN_SPECIALIST, CEO_IDENT))
            if ceo is not None:
                ceo.observe(y)
        return y

    def observe_synthesis(self, q: Question, h: Hypothesis, delta_u: float, verified: bool) -> float:
        y = synthesis_reward(delta_u, verified)
        self.counts["deepen_synthesize"] += 1
        arm = self._arms.get((q.question_id, ARM_SYNTHESIZE, h.hypothesis_id))
        if arm is not None:
            arm.observe(y)
        return y

    def observe_generation(self, q: Question, role_id: str, added: int, width: int = 0) -> float:
        """A GEN draw that added or repaired nothing scores 0 and counts towards exhaustion.

        `width` is the question's hypothesis count after the draw (telemetry:
        the widest question the search grew).
        """
        qid = q.question_id
        self.counts["widen_hypothesis"] += 1
        self.counts["widest_question"] = max(self.counts["widest_question"], int(width))
        if added > 0:
            self.gen_empty[qid] = 0
            return 0.0  # proposals are free until tested
        self.counts["gen_empty_draws"] += 1
        self.gen_empty[qid] = self.gen_empty.get(qid, 0) + 1
        if self.gen_exhausted(qid):
            self.counts["gen_exhausted_questions"] += 1
        arm = self._arms.get((qid, ARM_GEN_HYPOTHESIS, role_id or ""))
        if arm is not None:
            arm.observe(0.0)
        return 0.0

    def observe_recruit(self, q: Question, hired: bool) -> float:
        """A declined recruit scores 0 on the CEO arm; a hire resets the question's GEN exhaustion."""
        self.counts["widen_specialist"] += 1
        if hired:
            self.gen_empty[q.question_id] = 0
            return 0.0  # the hire is judged by what its hypotheses earn
        arm = self._arms.get((q.question_id, ARM_GEN_SPECIALIST, CEO_IDENT))
        if arm is not None:
            arm.observe(0.0)
        return 0.0

    # ------------------------------------------------------------------ #
    # Telemetry
    # ------------------------------------------------------------------ #

    def note_step(self, q: Question, chosen: BetaArm, draws: Mapping[str, float], y: float,
                  move_index: int, extra: Optional[Dict[str, float]] = None) -> Dict[str, float]:
        """Append a trace entry and return the numeric fields for the move's `extra`."""
        fields = {"ab_theta": round(chosen.last_theta, 6), "ab_mean": round(chosen.mean, 6),
                  "ab_arms": float(len(draws)), "ab_y": round(float(y), 6)}
        if len(self.trace) < TRACE_LIMIT:
            self.trace.append({"move_index": int(move_index), "question_id": q.question_id,
                               "chosen": f"{chosen.kind}:{chosen.ident or '-'}", "theta": fields["ab_theta"],
                               "mean": fields["ab_mean"], "y": fields["ab_y"], "draws": dict(draws)})
        if extra is not None:
            extra.update(fields)
        return fields

    def to_dict(self) -> Dict[str, Any]:
        per_q: Dict[str, List[Dict[str, Any]]] = {}
        for (qid, _k, _i), a in self._arms.items():
            per_q.setdefault(qid, []).append(a.to_dict())
        return {"algorithm": "ab_mcts", "prior_strength": self.tau, "counts": dict(self.counts),
                "gen_empty": dict(self.gen_empty), "arms": per_q, "trace": list(self.trace)}
