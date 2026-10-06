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
HYPOTHESIS_STATUSES = (UNVERIFIED, SUPPORTED, FALSIFIED, CERTIFIED)

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


TABU_SIMILARITY = 0.8


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


@dataclass
class Question:
    """Something the firm does not know, with how much it does not know it."""

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
    created_at: float = field(default_factory=_now)

    def __post_init__(self) -> None:
        if self.status not in QUESTION_STATUSES:
            raise LedgerError(f"Question.status must be one of {QUESTION_STATUSES}")
        self.uncertainty = max(0.0, min(1.0, float(self.uncertainty)))
        self.initial_uncertainty = max(0.0, min(1.0, float(self.initial_uncertainty)))

    @property
    def is_open(self) -> bool:
        return self.status in (Q_OPEN, Q_EXHAUSTED)

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
    created_at: float = field(default_factory=_now)

    def __post_init__(self) -> None:
        if self.status not in HYPOTHESIS_STATUSES:
            raise LedgerError(f"Hypothesis.status must be one of {HYPOTHESIS_STATUSES}")
        self.prior = clamp_prior(self.prior)
        self.posterior = max(0.0, min(1.0, float(self.posterior)))
        if not self.mechanism_signature:
            self.mechanism_signature = mechanism_signature(self.claim, self.mechanism)

    @property
    def q_value(self) -> float:
        return self.value_sum / self.visits if self.visits else 0.0

    @property
    def tested(self) -> bool:
        return self.status != UNVERIFIED

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
                     source_failure: str = "") -> Question:
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

    def evidence_by_id(self, evidence_id: str) -> Optional[Evidence]:
        for e in self.evidence_log:
            if e.evidence_id == evidence_id:
                return e
        return None

    def tabu_match(self, signature: str, question_id: Optional[str] = None) -> Optional[FalsifiedBelief]:
        """The ruled-out belief this signature collides with, if any.

        Scoped to the question by default: the same mechanism may be a live
        hypothesis for a different failure. Pass `question_id=None` to match
        across the whole ledger.
        """
        if not signature:
            return None
        for belief in self.ruled_out:
            if question_id is not None and belief.question_id != question_id:
                continue
            if belief.mechanism_signature == signature:
                return belief
            if signature_similarity(belief.mechanism_signature, signature) >= TABU_SIMILARITY:
                return belief
        return None

    def is_tabu(self, signature: str, question_id: Optional[str] = None) -> bool:
        return self.tabu_match(signature, question_id) is not None

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
                ))
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
            "evidence": len(self.evidence_log),
            "settled_facts": len(self.settled_knowledge),
            "ruled_out": len(self.ruled_out),
            "moves": len(self.move_log),
        }

    def summary(self, question_id: Optional[str] = None, max_items: int = 8) -> str:
        """A compact, human-readable ledger view for prompts and logs.

        This is what a System 1 proposer is shown: what is settled, what has
        been ruled out (so it does not propose it again), and what is open.
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
        if ruled:
            lines.append("RULED OUT (falsified; do NOT propose again):")
            for b in ruled[-max_items:]:
                lines.append(f"  - {b.claim}")
        qs = [q for q in self.questions.values()
              if (question_id is None and q.is_open) or q.question_id == question_id]
        if qs:
            lines.append("OPEN QUESTIONS:")
            for q in qs[:max_items]:
                lines.append(f"  ? {q.question_id} [{q.module}] u={q.uncertainty:.2f} {q.text}")
                for h in self.hypotheses_for(q.question_id)[-max_items:]:
                    lines.append(f"      {h.hypothesis_id} {h.status:<10} prior={h.prior:.2f} "
                                 f"post={h.posterior:.2f} {h.claim}")
        return "\n".join(lines)
