"""V6 Epistemic Tree-Search: System 2 for an organisation of System 1 agents.

    ledger      -- the inspectable belief state (questions, hypotheses,
                   settled knowledge, ruled-out mechanisms, evidence, moves)
    moves       -- the typed proposals a System 1 agent may emit, and the
                   xgrammar schemas that constrain it to them
    gatekeeper  -- the only writer of beliefs; runs probes, never prompts
    value       -- ledger features, the heuristic/learned value head, Brier
    mcts        -- PUCT over hypotheses with forced low-prior exploration
    genes       -- mutation and crossover for `EpistemicPolicyGene`
    audit       -- the Epistemic Integrity Audit fitness reads

Design authority: Graepel, "Don't be fooled -- LLMs don't reason", MIT
Technology Review, 2 Oct 2026. See experiments/v6_epistemic_search/README.md.
"""

from hae.epistemic.audit import build_epistemic_audit
from hae.epistemic.gatekeeper import EvidenceGatekeeper, failure_key
from hae.epistemic.ledger import (
    CERTIFIED, FALSIFIED, SUPPORTED, UNTESTABLE, UNVERIFIED,
    Q_CERTIFIED, Q_EXHAUSTED, Q_OPEN, Q_RESOLVED,
    CertifiedFact, EpistemicState, Evidence, FalsifiedBelief, GatekeeperAuthority,
    Hypothesis, LedgerError, MoveRecord, Question, mechanism_signature,
)
from hae.epistemic.mcts import EpistemicSearchLoop, SearchResult
from hae.epistemic.moves import (
    V6_HYPOTHESIS_SCHEMA, V6_QUESTION_SCHEMA,
    HypothesisProposal, QuestionProposal,
    parse_hypothesis_packet, parse_question_packet,
)
from hae.epistemic.value import (
    FEATURE_NAMES, EpistemicValueFunction, LearnedValueHead,
    brier_score, calibration_report, epistemic_features,
)
from hae.epistemic.genes import crossover_epistemic_policy, mutate_epistemic_policy
from hae.genome.schema import EpistemicPolicyGene

__all__ = [
    "CERTIFIED", "FALSIFIED", "SUPPORTED", "UNTESTABLE", "UNVERIFIED",
    "Q_CERTIFIED", "Q_EXHAUSTED", "Q_OPEN", "Q_RESOLVED",
    "CertifiedFact", "EpistemicState", "Evidence", "FalsifiedBelief", "GatekeeperAuthority",
    "Hypothesis", "LedgerError", "MoveRecord", "Question", "mechanism_signature",
    "EvidenceGatekeeper", "failure_key",
    "EpistemicSearchLoop", "SearchResult",
    "V6_HYPOTHESIS_SCHEMA", "V6_QUESTION_SCHEMA", "HypothesisProposal", "QuestionProposal",
    "parse_hypothesis_packet", "parse_question_packet",
    "FEATURE_NAMES", "EpistemicValueFunction", "LearnedValueHead",
    "brier_score", "calibration_report", "epistemic_features",
    "EpistemicPolicyGene", "crossover_epistemic_policy", "mutate_epistemic_policy",
    "build_epistemic_audit",
]
