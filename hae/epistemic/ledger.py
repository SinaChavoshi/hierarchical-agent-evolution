"""The epistemic ledger: a firm's explicit, inspectable System 2 state.

V6 rests on one distinction (Graepel, *Don't be fooled -- LLMs don't reason*,
MIT Technology Review, Oct 2026): a language model is a fast associative
proposer, not a reasoner. If the organisation is to reason, the reasoning has
to live somewhere other than inside the model's next-token distribution. It
lives here.

The ledger holds four kinds of belief, each with a different epistemic status:

  * **open questions** -- what the firm does not yet know, each with a scalar
    uncertainty that only evidence may lower;
  * **hypotheses** -- candidate answers proposed by System 1 agents, each with
    a *prior* (the proposer's confidence, which is recorded so it can later be
    scored for calibration) and a *posterior* that only the gatekeeper writes;
  * **settled knowledge** -- facts certified by an experiment or by the oracle;
  * **ruled out** -- mechanisms that evidence has falsified. Proposing one of
    these again is *tabu*: it is rejected before it costs a turn.

Two invariants are enforced structurally rather than by convention:

  1. **No LLM output can set a belief.** `add_hypothesis` accepts a claim, a
     mechanism, a prior and a probe; it always registers the hypothesis as
     `UNVERIFIED`. The only methods that change a status, a posterior or a
     question's uncertainty require a `GatekeeperAuthority` token, which is
     constructed only inside `hae.epistemic.gatekeeper`.
  2. **Uncertainty is monotone.** Evidence can only lower a question's
     uncertainty, never raise it -- with one exception, oracle feedback, which
     outranks any probe and may reopen a question the firm believed settled.

Everything is plain dataclasses and JSON. A ledger must be readable by a
human auditor after the run, and hashable so the fitness audit can prove the
trail it scored is the trail the firm produced.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

# ---------------------------------------------------------------------------
# Status vocabularies
# ---------------------------------------------------------------------------

UNVERIFIED = "UNVERIFIED"
SUPPORTED = "SUPPORTED"
FALSIFIED = "FALSIFIED"
CERTIFIED = "CERTIFIED"
# The gatekeeper refused to run the probe (invalid Python, forbidden import,
# too long...). Says nothing about the mechanism, so it is never tabu; but
# the hypothesis must leave the "untested" pool or PUCT re-selects it
# forever (Gen 16 pilot pass 2: one hypothesis re-selected 17 times, 32
# moves burnt on the same syntax error). A proposer may repair the probe.
UNTESTABLE = "UNTESTABLE"
HYPOTHESIS_STATUSES = (UNVERIFIED, SUPPORTED, FALSIFIED, CERTIFIED, UNTESTABLE)

Q_OPEN = "OPEN"
# A supported hypothesis was synthesised into the module and the module's own
# checks passed. Not yet confirmed by the oracle.
Q_RESOLVED = "RESOLVED"
# The oracle confirmed the linked failure is gone.
Q_CERTIFIED = "CERTIFIED"
# Every proposed hypothesis was falsified; the question needs new hypotheses.
Q_EXHAUSTED = "EXHAUSTED"
QUESTION_STATUSES = (Q_OPEN, Q_RESOLVED, Q_CERTIFIED, Q_EXHAUSTED)

EVIDENCE_KINDS = ("probe", "probe_rejected", "module_check", "oracle", "internal_tests")

# Priors are clamped away from 0 and 1: a hypothesis with prior exactly 0 or
# 1 is not a hypothesis, and it would break the calibration score.
MIN_PRIOR = 0.01
MAX_PRIOR = 0.99


class LedgerError(ValueError):
    """Raised when a ledger operation violates an epistemic invariant."""


class GatekeeperAuthority:
    """Capability token required to write a belief.

    Only `hae.epistemic.gatekeeper.EvidenceGatekeeper` constructs one. Any
    other caller that wants to set a status must go through the gatekeeper,
    which means it must produce evidence first.
    """

    __slots__ = ("_issuer",)

    def __init__(self, issuer: str = "gatekeeper") -> None:
        self._issuer = issuer

    @property
    def issuer(self) -> str:
        return self._issuer

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return f"GatekeeperAuthority({self._issuer!r})"


def _require_authority(authority: Any, action: str) -> None:
    if not isinstance(authority, GatekeeperAuthority):
        raise LedgerError(
            f"{action} requires a GatekeeperAuthority token. Beliefs are "
            "written only by the evidence gatekeeper; a proposer may not "
            "certify its own claim.")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def canonical_json(obj: Any) -> str:
    """Deterministic JSON: sorted keys, no whitespace variance."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def clamp_prior(value: Any) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        v = 0.5
    if v != v:  # NaN
        v = 0.5
    return max(MIN_PRIOR, min(MAX_PRIOR, v))


_STOPWORDS = frozenset("""
a an the of to in on for and or is are was were be been being this that these
those it its with as by from at into than then when while which who whom whose
because so such not no nor but if does do did done has have had having can
could should would may might must will shall there here where what why how
all any each every some most more less very just also only own same other
module function class method test tests fails fail failing failure failed
returns return returned value values expected actual assert assertion error
code line lines
""".split())

_TOKEN_RE = re.compile(r"[a-z_][a-z0-9_]{2,}")


def _stem(token: str) -> str:
    """Light suffix stripping so 'skips', 'skipped' and 'skipping' collide.

    Identifiers (anything with an underscore or digit) are left alone; a
    test name is a precise reference and must not be blurred.
    """
    if "_" in token or any(ch.isdigit() for ch in token) or len(token) <= 4:
        return token
    for suffix in ("ingly", "ing", "edly", "ed", "ies", "es", "ly", "s"):
        if token.endswith(suffix) and len(token) - len(suffix) >= 3:
            if suffix == "s" and token.endswith("ss"):
                return token
            stem = token[: -len(suffix)]
            if suffix == "ies":
                return stem + "y"
            # skipping -> skipp -> skip; but roll -> roll, pass -> pass.
            if (len(stem) >= 4 and stem[-1] == stem[-2]
                    and stem[-1] not in "aeioulsz"):
                stem = stem[:-1]
            return stem
    return token


def mechanism_signature(claim: str, mechanism: str = "") -> str:
    """A normalised, order-independent signature of a hypothesis mechanism.

    Used for tabu matching: two proposals that name the same mechanism in
    different words should collide. Tokens are lower-cased, stop-words and
    very short words are dropped, common suffixes are stripped, identifiers
    are kept whole, and the result is the sorted, de-duplicated token set.
    Not a hash, so an auditor can read why two hypotheses were judged the same.
    """
    text = f"{claim} {mechanism}".lower().replace("`", " ")
    tokens = sorted({_stem(t) for t in _TOKEN_RE.findall(text) if t not in _STOPWORDS})
    return " ".join(tokens)


def signature_similarity(a: str, b: str) -> float:
    """Jaccard similarity between two mechanism signatures."""
    sa, sb = set(a.split()), set(b.split())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / float(len(sa | sb))


def signature_overlap(a: str, b: str) -> float:
    """Overlap coefficient |A∩B| / min(|A|,|B|): 1.0 when one signature is
    contained in the other. Robust to one text being a longer elaboration of
    the same mechanism, which Jaccard punishes."""
    sa, sb = set(a.split()), set(b.split())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / float(min(len(sa), len(sb)))


TABU_SIMILARITY = 0.8        # Jaccard on the full (claim + mechanism) signature
TABU_OVERLAP = 0.75          # containment of the shorter signature in the longer
TABU_CLAIM_SIMILARITY = 0.8  # Jaccard on the claim alone
TABU_MIN_TOKENS = 6          # containment needs a non-trivial shorter signature


def same_mechanism(sig_a: str, sig_b: str, claim_a: str = "", claim_b: str = "") -> bool:
    """Do two hypotheses name the same mechanism?

    Three lexical tests, any of which suffices: near-identical full
    signatures (Jaccard); one signature contained in the other (a proposer
    restating a mechanism with a longer or shorter explanation); or
    near-identical claims. Thresholds were set from the Gen 16 pilot ledgers,
    where seven paraphrased re-proposals of already-falsified mechanisms had
    Jaccard 0.46-0.71 (all slipped past the 0.8 tabu test) but claim-Jaccard
    1.00 and overlap 0.83-0.88, while every genuinely distinct pair on the
    same question scored <= 0.48 and <= 0.67.
    """
    if not sig_a or not sig_b:
        return False
    if sig_a == sig_b or signature_similarity(sig_a, sig_b) >= TABU_SIMILARITY:
        return True
    if (min(len(sig_a.split()), len(sig_b.split())) >= TABU_MIN_TOKENS
            and signature_overlap(sig_a, sig_b) >= TABU_OVERLAP):
        return True
    if claim_a and claim_b:
        ca, cb = mechanism_signature(claim_a), mechanism_signature(claim_b)
        if ca and cb and signature_similarity(ca, cb) >= TABU_CLAIM_SIMILARITY:
            return True
    return False


def _now() -> float:
    return round(time.time(), 3)


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------

@dataclass
class Evidence:
    """One observation produced by running something, never by saying something."""

    evidence_id: str
    kind: str
    hypothesis_id: str = ""
    question_id: str = ""
    command: str = ""
    exit_code: int = -1
    stdout: str = ""
    stderr: str = ""
    prediction: Dict[str, Any] = field(default_factory=dict)
    matched_prediction: bool = False
    detail: str = ""
    network_isolated: Optional[bool] = None
    sha256: str = ""
    created_at: float = field(default_factory=_now)

    def __post_init__(self) -> None:
        if self.kind not in EVIDENCE_KINDS:
            raise LedgerError(f"Evidence.kind must be one of {EVIDENCE_KINDS}, got {self.kind!r}")
        if not self.sha256:
            self.sha256 = sha256_text(canonical_json({
                "kind": self.kind, "command": self.command,
                "exit_code": self.exit_code, "stdout": self.stdout,
                "stderr": self.stderr, "prediction": self.prediction,
            }))

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Evidence":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


def failure_summary(failure: str, limit: int = 220) -> str:
    """The exception part of an oracle line (`... -> ExcClass: message`), for display."""
    failure = failure or ""
    tail = failure.split("->", 1)[1].strip() if "->" in failure else failure.strip()
    return " ".join(tail.split())[:limit]


@dataclass
class Question:
    """Something the firm does not know, with how much it does not know it.

    `failure_epoch` counts how many times the oracle's failure behind this
    question has *changed shape* (Gen 16 cohort run 2, finding #8: a synthesis
    turned an `UnboundLocalError` into a `NameError`, the question kept its old
    text, and the firm spent three iterations hypothesising about an error
    that no longer existed). `failure_history` keeps the superseded oracle
    lines, one per earlier epoch, so an auditor can read what each falsified
    hypothesis was actually tested against. `location` is where the oracle's
    traceback ended inside the firm's own code, when the benchmark could
    tell: `hae/evaluation/harness.py:212 in _run_pytest`.
    """

    question_id: str
    text: str
    module: str = ""
    uncertainty: float = 1.0
    initial_uncertainty: float = 1.0
    source_failure_tag: str = ""
    source_failure_key: str = ""
    source_failure: str = ""
    status: str = Q_OPEN
    hypothesis_ids: List[str] = field(default_factory=list)
    hypothesis_rounds: int = 0
    failure_epoch: int = 0
    failure_history: List[str] = field(default_factory=list)
    location: str = ""
    created_at: float = field(default_factory=_now)

    def __post_init__(self) -> None:
        if self.status not in QUESTION_STATUSES:
            raise LedgerError(f"Question.status must be one of {QUESTION_STATUSES}")
        self.uncertainty = max(0.0, min(1.0, float(self.uncertainty)))
        self.initial_uncertainty = max(0.0, min(1.0, float(self.initial_uncertainty)))
        self.failure_epoch = max(0, int(self.failure_epoch or 0))
        self.failure_history = [str(f) for f in (self.failure_history or [])]
        self.location = str(self.location or "")

    @property
    def is_open(self) -> bool:
        return self.status in (Q_OPEN, Q_EXHAUSTED)

    def failure_for_epoch(self, epoch: int) -> str:
        """The oracle line this question carried during `epoch` ("" if unknown)."""
        if epoch == self.failure_epoch:
            return self.source_failure
        if 0 <= epoch < len(self.failure_history):
            return self.failure_history[epoch]
        return ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Question":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class Hypothesis:
    """A candidate answer, with a falsifiable probe and the proposer's prior."""

    hypothesis_id: str
    question_id: str
    claim: str
    mechanism: str = ""
    prior: float = 0.5
    posterior: float = 0.5
    status: str = UNVERIFIED
    probe_code: str = ""
    prediction: Dict[str, Any] = field(default_factory=dict)
    evidence_ids: List[str] = field(default_factory=list)
    # Search statistics (PUCT). Written by the search loop, not by System 1.
    visits: int = 0
    value_sum: float = 0.0
    mechanism_signature: str = ""
    proposed_by: str = ""
    patch_applied: bool = False
    synthesis_failures: int = 0
    probe_rejections: int = 0
    last_rejection: str = ""
    # The question's `failure_epoch` when this hypothesis was proposed. A
    # falsification only rules the mechanism out for the oracle failure it was
    # tested against; once that failure changes shape the mechanism is
    # proposable again (see `EpistemicState.tabu_match`).
    epoch: int = 0
    created_at: float = field(default_factory=_now)

    def __post_init__(self) -> None:
        if self.status not in HYPOTHESIS_STATUSES:
            raise LedgerError(f"Hypothesis.status must be one of {HYPOTHESIS_STATUSES}")
        self.prior = clamp_prior(self.prior)
        self.posterior = max(0.0, min(1.0, float(self.posterior)))
        self.epoch = max(0, int(self.epoch or 0))
        if not self.mechanism_signature:
            self.mechanism_signature = mechanism_signature(self.claim, self.mechanism)

    @property
    def q_value(self) -> float:
        return self.value_sum / self.visits if self.visits else 0.0

    @property
    def tested(self) -> bool:
        """True only once a probe actually ran and produced a verdict."""
        return self.status in (SUPPORTED, FALSIFIED, CERTIFIED)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Hypothesis":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class CertifiedFact:
    fact_id: str
    question_id: str
    hypothesis_id: str
    statement: str
    evidence_ids: List[str] = field(default_factory=list)
    certified_by: str = "gatekeeper"
    created_at: float = field(default_factory=_now)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CertifiedFact":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class FalsifiedBelief:
    belief_id: str
    hypothesis_id: str
    question_id: str
    claim: str
    mechanism_signature: str
    killing_evidence_id: str
    # Copied from the hypothesis: which of the question's oracle failures the
    # mechanism was ruled out against. Tabu only for that epoch.
    epoch: int = 0
    created_at: float = field(default_factory=_now)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "FalsifiedBelief":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class MoveRecord:
    """One step of the search, with everything a value head would train on."""

    move_index: int
    move_type: str
    question_id: str = ""
    hypothesis_id: str = ""
    agent_role: str = ""
    delta_u: float = 0.0
    value_before: float = 0.0
    value_after: float = 0.0
    features: Dict[str, float] = field(default_factory=dict)
    state_hash_before: str = ""
    state_hash_after: str = ""
    forced_low_prior: bool = False
    note: str = ""
    created_at: float = field(default_factory=_now)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "MoveRecord":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# The ledger
# ---------------------------------------------------------------------------

class EpistemicState:
    """The complete, serialisable belief state of one firm.

    Proposers (System 1) may *add* questions and hypotheses. Only a holder of
    `GatekeeperAuthority` may change what the firm believes about them.
    """

    def __init__(self, company_id: str) -> None:
        self.company_id = company_id
        self.questions: Dict[str, Question] = {}
        self.hypotheses: Dict[str, Hypothesis] = {}
        self.settled_knowledge: List[CertifiedFact] = []
        self.ruled_out: List[FalsifiedBelief] = []
        self.evidence_log: List[Evidence] = []
        self.move_log: List[MoveRecord] = []
        self.created_at: float = _now()
        self._counters: Dict[str, int] = {}

    # ----------------------------------------------------------------- ids

    def _next_id(self, prefix: str) -> str:
        n = self._counters.get(prefix, 0) + 1
        self._counters[prefix] = n
        return f"{prefix}{n}"

    # ------------------------------------------------------ proposer surface

    def add_question(self, text: str, module: str = "", uncertainty: float = 1.0,
                     source_failure_tag: str = "", source_failure_key: str = "",
                     source_failure: str = "", location: str = "") -> Question:
        """Registers a question. Idempotent on (module, failure key | text)."""
        text = " ".join(str(text).split())
        if not text:
            raise LedgerError("A question needs text")
        for q in self.questions.values():
            same_key = source_failure_key and q.source_failure_key == source_failure_key
            same_text = q.module == module and q.text.lower() == text.lower()
            if same_key or same_text:
                return q
        q = Question(
            question_id=self._next_id("q"),
            text=text, module=module,
            uncertainty=uncertainty, initial_uncertainty=uncertainty,
            source_failure_tag=source_failure_tag,
            source_failure_key=source_failure_key,
            source_failure=source_failure,
            location=str(location or ""),
        )
        self.questions[q.question_id] = q
        return q

    def add_hypothesis(self, question_id: str, claim: str, mechanism: str = "",
                       prior: float = 0.5, probe_code: str = "",
                       prediction: Optional[Mapping[str, Any]] = None,
                       proposed_by: str = "") -> Hypothesis:
        """Registers a hypothesis as UNVERIFIED. Always UNVERIFIED.

        There is deliberately no `status` or `posterior` argument. A proposer
        that is certain is still a proposer; its certainty is recorded as the
        prior and scored for calibration later.
        """
        if question_id not in self.questions:
            raise LedgerError(f"Unknown question {question_id!r}")
        claim = " ".join(str(claim).split())
        if not claim:
            raise LedgerError("A hypothesis needs a claim")
        h = Hypothesis(
            hypothesis_id=self._next_id("h"),
            question_id=question_id,
            claim=claim,
            mechanism=" ".join(str(mechanism or "").split()),
            prior=clamp_prior(prior),
            posterior=clamp_prior(prior),
            status=UNVERIFIED,
            probe_code=str(probe_code or ""),
            prediction=dict(prediction or {}),
            proposed_by=proposed_by,
            epoch=self.questions[question_id].failure_epoch,
        )
        self.hypotheses[h.hypothesis_id] = h
        self.questions[question_id].hypothesis_ids.append(h.hypothesis_id)
        return h

    # ----------------------------------------------------------- queries

    def open_questions(self) -> List[Question]:
        return [q for q in self.questions.values() if q.is_open]

    def questions_with_status(self, *statuses: str) -> List[Question]:
        return [q for q in self.questions.values() if q.status in statuses]

    def hypotheses_for(self, question_id: str,
                       status: Optional[str] = None) -> List[Hypothesis]:
        q = self.questions.get(question_id)
        if q is None:
            return []
        out = [self.hypotheses[h] for h in q.hypothesis_ids if h in self.hypotheses]
        if status is not None:
            out = [h for h in out if h.status == status]
        return out

    def untested_hypotheses(self, question_id: str) -> List[Hypothesis]:
        return self.hypotheses_for(question_id, status=UNVERIFIED)

    def untestable_hypotheses(self, question_id: str) -> List[Hypothesis]:
        return self.hypotheses_for(question_id, status=UNTESTABLE)

    def repair_probe(self, hypothesis_id: str, probe_code: str,
                     prediction: Optional[Mapping[str, Any]] = None) -> Hypothesis:
        """Replaces the probe of an UNTESTABLE hypothesis and returns it to UNVERIFIED.

        A proposal-level operation, like `add_hypothesis`: no belief changes
        hands. The mechanism, prior and id are kept so calibration still scores
        the original prior once the repaired probe finally runs.
        """
        h = self.hypotheses.get(hypothesis_id)
        if h is None:
            raise LedgerError(f"Unknown hypothesis {hypothesis_id!r}")
        if h.status != UNTESTABLE:
            raise LedgerError(f"Only an UNTESTABLE hypothesis may have its probe repaired, {hypothesis_id} is {h.status}")
        if not str(probe_code or "").strip():
            raise LedgerError("A repaired probe must not be empty")
        h.probe_code = str(probe_code)
        if prediction is not None:
            h.prediction = dict(prediction)
        h.status = UNVERIFIED
        return h

    def evidence_by_id(self, evidence_id: str) -> Optional[Evidence]:
        for e in self.evidence_log:
            if e.evidence_id == evidence_id:
                return e
        return None

    def belief_is_current(self, belief: FalsifiedBelief) -> bool:
        """Was this mechanism ruled out against the question's *current* oracle failure?

        A belief whose question has since changed epoch (the oracle now fails
        differently) is history, not tabu: the probe that killed it was run
        against a symptom that no longer exists.
        """
        q = self.questions.get(belief.question_id)
        return q is None or int(belief.epoch) == int(q.failure_epoch)

    def tabu_match(self, signature: str, question_id: Optional[str] = None,
                   claim: str = "") -> Optional[FalsifiedBelief]:
        """The ruled-out belief this signature collides with, if any.

        Scoped to the question by default: the same mechanism may be a live
        hypothesis for a different failure. Pass `question_id=None` to match
        across the whole ledger. Pass the proposal's `claim` as well so a
        re-proposal that keeps the claim but rewords the mechanism is caught.
        Only beliefs falsified against the question's current oracle failure
        count (`belief_is_current`); earlier epochs stay in `ruled_out` as
        history but no longer block a proposal.
        """
        if not signature:
            return None
        for belief in self.ruled_out:
            if question_id is not None and belief.question_id != question_id:
                continue
            if not self.belief_is_current(belief):
                continue
            if same_mechanism(belief.mechanism_signature, signature, belief.claim, claim):
                return belief
        return None

    def is_tabu(self, signature: str, question_id: Optional[str] = None, claim: str = "") -> bool:
        return self.tabu_match(signature, question_id, claim) is not None

    def total_uncertainty(self) -> float:
        return round(sum(q.uncertainty for q in self.questions.values()), 6)

    def initial_uncertainty(self) -> float:
        return round(sum(q.initial_uncertainty for q in self.questions.values()), 6)

    def stagnation_depth(self) -> int:
        """How many consecutive trailing moves resolved nothing."""
        depth = 0
        for move in reversed(self.move_log):
            if move.delta_u > 0.0:
                break
            depth += 1
        return depth

    # ------------------------------------------------ gatekeeper surface

    def record_evidence(self, evidence: Evidence, authority: Any) -> None:
        _require_authority(authority, "record_evidence")
        if any(e.evidence_id == evidence.evidence_id for e in self.evidence_log):
            raise LedgerError(f"Duplicate evidence id {evidence.evidence_id!r}")
        self.evidence_log.append(evidence)
        if evidence.hypothesis_id and evidence.hypothesis_id in self.hypotheses:
            self.hypotheses[evidence.hypothesis_id].evidence_ids.append(evidence.evidence_id)

    def new_evidence_id(self) -> str:
        return self._next_id("e")

    def set_hypothesis_verdict(self, hypothesis_id: str, status: str,
                               posterior: float, evidence_id: str,
                               authority: Any) -> Hypothesis:
        """Writes a verdict. Requires evidence that is already on the log."""
        _require_authority(authority, "set_hypothesis_verdict")
        if status not in (SUPPORTED, FALSIFIED, CERTIFIED):
            raise LedgerError(f"A verdict must be SUPPORTED, FALSIFIED or CERTIFIED, got {status!r}")
        h = self.hypotheses.get(hypothesis_id)
        if h is None:
            raise LedgerError(f"Unknown hypothesis {hypothesis_id!r}")
        if self.evidence_by_id(evidence_id) is None:
            raise LedgerError(
                f"Verdict on {hypothesis_id} cites evidence {evidence_id!r} that is "
                "not on the log. A belief without recorded evidence is an opinion.")
        if evidence_id not in h.evidence_ids:
            h.evidence_ids.append(evidence_id)
        h.status = status
        h.posterior = max(0.0, min(1.0, float(posterior)))
        if status == FALSIFIED:
            if not any(b.hypothesis_id == hypothesis_id for b in self.ruled_out):
                self.ruled_out.append(FalsifiedBelief(
                    belief_id=self._next_id("ro"),
                    hypothesis_id=hypothesis_id,
                    question_id=h.question_id,
                    claim=h.claim,
                    mechanism_signature=h.mechanism_signature,
                    killing_evidence_id=evidence_id,
                    epoch=h.epoch,
                ))
        return h

    def mark_untestable(self, hypothesis_id: str, evidence_id: str, reason: str,
                        authority: Any) -> Hypothesis:
        """Parks a hypothesis whose probe the gatekeeper refused to run.

        Not a verdict: posterior is untouched, nothing enters `ruled_out`, the
        mechanism stays proposable. The hypothesis simply leaves the untested
        pool until a proposer repairs its probe (`repair_probe`).
        """
        _require_authority(authority, "mark_untestable")
        h = self.hypotheses.get(hypothesis_id)
        if h is None:
            raise LedgerError(f"Unknown hypothesis {hypothesis_id!r}")
        ev = self.evidence_by_id(evidence_id)
        if ev is None or ev.kind != "probe_rejected":
            raise LedgerError("mark_untestable needs probe_rejected evidence on the log")
        if h.status != UNVERIFIED:
            raise LedgerError(f"Only an UNVERIFIED hypothesis can become UNTESTABLE, {hypothesis_id} is {h.status}")
        if evidence_id not in h.evidence_ids:
            h.evidence_ids.append(evidence_id)
        h.status = UNTESTABLE
        h.probe_rejections += 1
        h.last_rejection = " ".join(str(reason or "").split())[:300]
        return h

    def lower_uncertainty(self, question_id: str, new_value: float, authority: Any) -> float:
        """Lowers a question's uncertainty. Never raises it. Returns the drop."""
        _require_authority(authority, "lower_uncertainty")
        q = self.questions[question_id]
        new_value = max(0.0, min(1.0, float(new_value)))
        if new_value >= q.uncertainty:
            return 0.0
        drop = q.uncertainty - new_value
        q.uncertainty = new_value
        return round(drop, 6)

    def set_question_status(self, question_id: str, status: str, authority: Any) -> Question:
        _require_authority(authority, "set_question_status")
        if status not in QUESTION_STATUSES:
            raise LedgerError(f"Unknown question status {status!r}")
        q = self.questions[question_id]
        q.status = status
        return q

    def reopen_question(self, question_id: str, uncertainty: float,
                        evidence_id: str, authority: Any) -> Question:
        """The one path by which uncertainty rises: oracle evidence.

        A probe can support a hypothesis and a synthesised patch can pass the
        module's own checks, and the held-out oracle can still fail. When it
        does, the oracle outranks everything below it.
        """
        _require_authority(authority, "reopen_question")
        if self.evidence_by_id(evidence_id) is None or self.evidence_by_id(evidence_id).kind != "oracle":
            raise LedgerError("Only oracle evidence may reopen a question")
        q = self.questions[question_id]
        q.status = Q_OPEN
        q.uncertainty = max(q.uncertainty, max(0.0, min(1.0, float(uncertainty))))
        return q

    def refresh_question(self, question_id: str, text: str, source_failure: str,
                         uncertainty: float, evidence_id: str, authority: Any,
                         location: Optional[str] = None) -> Question:
        """The oracle's failure behind a question has changed shape: start a new epoch.

        Gen 16 cohort run 2, finding #8. The question's text is rebuilt around
        the new oracle line, the old line is archived in `failure_history`,
        `failure_epoch` advances (so mechanisms falsified against the old
        symptom stop being tabu and the summary files them as history),
        uncertainty rises to at least `uncertainty` -- oracle evidence is the
        one source allowed to raise it -- an EXHAUSTED or RESOLVED question is
        OPEN again, and `hypothesis_rounds` resets so a proposer may propose.
        Requires oracle evidence on the log, like `reopen_question`.
        """
        _require_authority(authority, "refresh_question")
        ev = self.evidence_by_id(evidence_id)
        if ev is None or ev.kind != "oracle":
            raise LedgerError("Only oracle evidence may refresh a question")
        q = self.questions[question_id]
        if q.status == Q_CERTIFIED:
            raise LedgerError(f"{question_id} is CERTIFIED; a certified question is not refreshed")
        text = " ".join(str(text or "").split())
        if not text:
            raise LedgerError("A refreshed question needs text")
        q.failure_history.append(q.source_failure)
        q.failure_epoch += 1
        q.source_failure = str(source_failure or "")
        q.text = text
        if location is not None:
            q.location = str(location or "")
        q.uncertainty = max(q.uncertainty, max(0.0, min(1.0, float(uncertainty))))
        q.status = Q_OPEN
        q.hypothesis_rounds = 0
        return q

    def certify(self, question_id: str, hypothesis_id: str, statement: str,
                evidence_ids: Iterable[str], certified_by: str, authority: Any) -> CertifiedFact:
        _require_authority(authority, "certify")
        ids = list(evidence_ids)
        for eid in ids:
            if self.evidence_by_id(eid) is None:
                raise LedgerError(f"Certification cites unknown evidence {eid!r}")
        fact = CertifiedFact(
            fact_id=self._next_id("f"),
            question_id=question_id,
            hypothesis_id=hypothesis_id,
            statement=" ".join(str(statement).split()),
            evidence_ids=ids,
            certified_by=certified_by,
        )
        self.settled_knowledge.append(fact)
        q = self.questions[question_id]
        q.status = Q_CERTIFIED
        q.uncertainty = 0.0
        if hypothesis_id in self.hypotheses:
            h = self.hypotheses[hypothesis_id]
            h.status = CERTIFIED
            h.posterior = 1.0
        return fact

    # -------------------------------------------------------- search log

    def record_move(self, move: MoveRecord) -> None:
        self.move_log.append(move)

    def mark_visit(self, hypothesis_id: str, reward: float) -> None:
        """PUCT back-propagation. Statistics, not beliefs."""
        h = self.hypotheses[hypothesis_id]
        h.visits += 1
        h.value_sum += float(reward)

    # ------------------------------------------------------ serialisation

    def belief_dict(self) -> Dict[str, Any]:
        """Everything that constitutes a belief. Hashed by `state_hash`."""
        return {
            "company_id": self.company_id,
            "questions": [self.questions[k].to_dict() for k in sorted(self.questions)],
            "hypotheses": [self.hypotheses[k].to_dict() for k in sorted(self.hypotheses)],
            "settled_knowledge": [f.to_dict() for f in self.settled_knowledge],
            "ruled_out": [b.to_dict() for b in self.ruled_out],
            "evidence_log": [e.to_dict() for e in self.evidence_log],
        }

    def state_hash(self) -> str:
        payload = self.belief_dict()
        # Timestamps and search statistics are not beliefs.
        for coll in ("questions", "hypotheses", "settled_knowledge", "ruled_out", "evidence_log"):
            for item in payload[coll]:
                item.pop("created_at", None)
                item.pop("visits", None)
                item.pop("value_sum", None)
        return sha256_text(canonical_json(payload))

    def to_dict(self) -> Dict[str, Any]:
        out = self.belief_dict()
        out["move_log"] = [m.to_dict() for m in self.move_log]
        out["created_at"] = self.created_at
        out["counters"] = dict(self._counters)
        out["state_hash"] = self.state_hash()
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EpistemicState":
        st = cls(str(data.get("company_id", "")))
        for qd in data.get("questions", []):
            q = Question.from_dict(qd)
            st.questions[q.question_id] = q
        for hd in data.get("hypotheses", []):
            h = Hypothesis.from_dict(hd)
            st.hypotheses[h.hypothesis_id] = h
        st.settled_knowledge = [CertifiedFact.from_dict(f) for f in data.get("settled_knowledge", [])]
        st.ruled_out = [FalsifiedBelief.from_dict(b) for b in data.get("ruled_out", [])]
        st.evidence_log = [Evidence.from_dict(e) for e in data.get("evidence_log", [])]
        st.move_log = [MoveRecord.from_dict(m) for m in data.get("move_log", [])]
        st.created_at = float(data.get("created_at", _now()))
        st._counters = {str(k): int(v) for k, v in dict(data.get("counters", {})).items()}
        if not st._counters:
            # Rebuild from the records so new ids never collide.
            for prefix, coll in (("q", st.questions), ("h", st.hypotheses)):
                nums = [int(k[len(prefix):]) for k in coll if k[len(prefix):].isdigit()]
                st._counters[prefix] = max(nums) if nums else 0
            st._counters["e"] = len(st.evidence_log)
            st._counters["f"] = len(st.settled_knowledge)
            st._counters["ro"] = len(st.ruled_out)
        return st

    # ------------------------------------------------------------ views

    def counts(self) -> Dict[str, int]:
        hs = list(self.hypotheses.values())
        return {
            "questions": len(self.questions),
            "questions_open": len(self.open_questions()),
            "questions_resolved": len(self.questions_with_status(Q_RESOLVED)),
            "questions_certified": len(self.questions_with_status(Q_CERTIFIED)),
            "questions_exhausted": len(self.questions_with_status(Q_EXHAUSTED)),
            "hypotheses": len(hs),
            "hypotheses_untested": sum(1 for h in hs if h.status == UNVERIFIED),
            "hypotheses_supported": sum(1 for h in hs if h.status == SUPPORTED),
            "hypotheses_falsified": sum(1 for h in hs if h.status == FALSIFIED),
            "hypotheses_certified": sum(1 for h in hs if h.status == CERTIFIED),
            "hypotheses_untestable": sum(1 for h in hs if h.status == UNTESTABLE),
            "evidence": len(self.evidence_log),
            "settled_facts": len(self.settled_knowledge),
            "ruled_out": len(self.ruled_out),
            "moves": len(self.move_log),
        }

    def summary(self, question_id: Optional[str] = None, max_items: int = 8) -> str:
        """A compact, human-readable ledger view for prompts and logs.

        This is what a System 1 proposer is shown: what is settled, what has
        been ruled out (so it does not propose it again), what is open -- with
        the oracle's *current* failure line and, when known, where its
        traceback ended -- and which probes the gatekeeper refused to run (so
        it can repair them). Mechanisms falsified against an earlier shape of
        a question's oracle failure are history, not tabu, and are collapsed
        into one line so the proposer is not told to avoid them.
        """
        lines: List[str] = [f"EPISTEMIC LEDGER for {self.company_id} "
                            f"(uncertainty {self.total_uncertainty():.2f}/{self.initial_uncertainty():.2f})"]
        facts = [f for f in self.settled_knowledge
                 if question_id is None or f.question_id == question_id]
        if facts:
            lines.append("SETTLED (certified by evidence):")
            for f in facts[-max_items:]:
                lines.append(f"  + [{f.certified_by}] {f.statement}")
        ruled = [b for b in self.ruled_out
                 if question_id is None or b.question_id == question_id]
        current = [b for b in ruled if self.belief_is_current(b)]
        earlier = [b for b in ruled if not self.belief_is_current(b)]
        if current:
            lines.append("RULED OUT (falsified; do NOT propose again):")
            for b in current[-max_items:]:
                lines.append(f"  - {b.claim}")
        if earlier:
            old_summaries: List[str] = []
            for b in earlier:
                q_old = self.questions.get(b.question_id)
                s = failure_summary(q_old.failure_for_epoch(b.epoch), limit=120) if q_old else ""
                if s and s not in old_summaries:
                    old_summaries.append(s)
            lines.append(f"RULED OUT (history): {len(earlier)} earlier mechanism(s) were falsified against a "
                         f"previous oracle failure ({' / '.join(old_summaries[:2]) or 'unknown'}) and are not "
                         f"listed; they may be proposed again if they explain the CURRENT failure.")
        qs = [q for q in self.questions.values()
              if (question_id is None and q.is_open) or q.question_id == question_id]
        if qs:
            lines.append("OPEN QUESTIONS:")
            for q in qs[:max_items]:
                lines.append(f"  ? {q.question_id} [{q.module}] u={q.uncertainty:.2f} {q.text}")
                if q.source_failure:
                    lines.append(f"      CURRENT ORACLE FAILURE: {q.source_failure[:400]}")
                if q.location:
                    lines.append(f"      Oracle traceback ends at: {q.location}")
                if q.failure_epoch > 0:
                    prev = failure_summary(q.failure_for_epoch(q.failure_epoch - 1), limit=160) or "unknown"
                    lines.append(f"      ORACLE FAILURE CHANGED {q.failure_epoch} time(s) (epoch {q.failure_epoch}); "
                                 f"it previously read: {prev}. Reason about the CURRENT failure above.")
                for h in self.hypotheses_for(q.question_id)[-max_items:]:
                    superseded = h.epoch != q.failure_epoch
                    if superseded and h.status == FALSIFIED:
                        continue  # counted in the RULED OUT history line
                    lines.append(f"      {h.hypothesis_id} {h.status:<10} prior={h.prior:.2f} "
                                 f"post={h.posterior:.2f} {h.claim}"
                                 + (" (proposed against the previous oracle failure)" if superseded else ""))
                    if h.status == UNTESTABLE and h.last_rejection:
                        lines.append(f"          PROBE REFUSED ({h.last_rejection}). Re-propose this mechanism "
                                     f"with a corrected probe_lines array if you still believe it.")
        return "\n".join(lines)
