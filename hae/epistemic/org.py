"""Shared V8 organisational types: role alleles, the CEO policy gene, the live
`OrgState`, task triage features and the turn-0 organisation selection.

Why this module exists
----------------------
V5/V6 firms carry a fixed org chart (`CompanyGenome.departments`) and the
epistemic loop binds four fixed agents to its four move kinds
(`company._bind_epistemic_agents`). V8 treats the *organisation* as part of
System 2's search state instead (Graepel framing: the LLM is System 1 and
proposes; the organisation is System 2 and decides who proposes, when to add
someone, and what the evidence says about each role afterwards):

* a genome carries a `role_library` of `RoleAllele`s and a `CEOPolicyGene`;
* at turn 0 `search_initial_organization` picks the smallest capable team;
* during the search an `OrgState` tracks the live team, per-role evidence
  credit and the two pressures that justify recruiting (stalled dU, modules
  no active role covers); `MOVE_RECRUIT_SPECIALIST` (mcts.py) reads them;
* at the generation boundary the breeder promotes recruited roles that earned
  dU and prunes roles that never did (breeder.py).

Everything here is deterministic and free of LLM calls, so the loop's
behaviour with `CEOPolicyGene.enabled == False` is byte-identical to V6: no
code path in this module runs unless a genome opts in.

Honesty notes
-------------
* `search_initial_organization` is an exhaustive, UCB-scored subset selection,
  not a tree search. With a library of a few dozen roles and team sizes 2-4
  the candidate space is a few thousand subsets and the score is additive;
  wrapping that in PUCT would be theatre. The roadmap's learned org heads
  (P_org / V_org) are deferred until trajectories exist: the V7 heads showed
  that training on no signal produces nothing worth wiring in.
* Role statistics (`mean_delta_u`, `support_rate`, `uses`) are summaries of
  gatekeeper verdicts credited to the role that *proposed* the hypothesis or
  wrote the patch. That is end-of-move credit, not causal credit: a role that
  proposes after another role has narrowed the question inherits the easier
  position. Document this wherever the numbers are shown.
"""

from __future__ import annotations

import itertools
import math
import random
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from hae.genome.schema import AgentGenome, GenomeValidationError, MODEL_TIERS, _Model

ROLE_KINDS = ("probe", "synthesis", "both")
RECRUIT_MODES = ("library", "synthesize", "both")
# dU of a single move that counts as "fully productive" when turning a role's
# mean dU into a [0, 1] quality estimate. A SUPPORTED verdict lowers a
# question's uncertainty by roughly this much under the V6 gatekeeper.
DELTA_U_SCALE = 0.30

_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
_PATH_RE = re.compile(r"[\w./-]+\.py\b")
_EXC_RE = re.compile(r"\b([A-Z][A-Za-z]+(?:Error|Exception|Warning))\b")
_STOP = {"the", "and", "for", "with", "that", "this", "from", "when", "not", "are",
         "but", "was", "were", "has", "have", "into", "then", "than", "should", "would",
         "could", "there", "their", "what", "which", "while", "also", "line", "file",
         "module", "test", "tests", "error", "python", "def", "class", "return", "import",
         "self", "none", "true", "false", "oracle", "failed", "failure", "expected", "got"}


def sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def normalise_tags(tags: Iterable[str]) -> List[str]:
    """Lower-cased, de-duplicated, order-preserving tag list."""
    out: List[str] = []
    seen: Set[str] = set()
    for t in tags or []:
        s = str(t).strip().lower()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def tag_overlap(a: Iterable[str], b: Iterable[str]) -> float:
    """Overlap coefficient |a & b| / min(|a|, |b|) in [0, 1].

    Chosen over Jaccard because a role's handful of tags should score 1.0
    against a task that mentions all of them among many other tokens.
    """
    sa, sb = set(normalise_tags(a)), set(normalise_tags(b))
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / float(min(len(sa), len(sb)))


def stable_role_id(name: str, salt: str = "") -> str:
    base = re.sub(r"[^a-z0-9]+", "_", str(name).lower()).strip("_")[:40] or "role"
    if salt:
        base = f"{base}_{re.sub(r'[^a-z0-9]+', '', str(salt).lower())[:8]}"
    return f"r_{base}"


# --------------------------------------------------------------------------- #
# Genome-side types
# --------------------------------------------------------------------------- #

@dataclass
class RoleAllele(_Model):
    """One specialist definition in a company's (or the cross-company) role library.

    The persona fields map 1:1 onto `AgentGenome` so a role can be executed by
    the existing `_execute_agent` adapters. The statistics are written only by
    the breeder from completed trajectories; a running firm reads them.
    """

    role_id: str = ""
    name: str = ""
    goal: str = ""
    backstory: str = ""
    domain_tags: List[str] = field(default_factory=list)
    kind: str = "both"
    model_tier: str = "worker"
    temperature: float = 0.7
    tools_enabled: bool = True
    cost_per_move: float = 1.0
    uses: int = 0
    mean_delta_u: float = 0.0
    support_rate: float = 0.0
    tasks_resolved: int = 0
    origin: str = "seed"
    created_generation: int = 0
    extra: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.name).strip():
            raise GenomeValidationError("RoleAllele.name must be non-empty")
        if not str(self.role_id).strip():
            self.role_id = stable_role_id(self.name)
        if self.kind not in ROLE_KINDS:
            raise GenomeValidationError(f"RoleAllele.kind must be one of {ROLE_KINDS}, got {self.kind!r}")
        if self.model_tier not in MODEL_TIERS:
            raise GenomeValidationError(f"RoleAllele.model_tier must be one of {MODEL_TIERS}, got {self.model_tier!r}")
        self.domain_tags = normalise_tags(self.domain_tags)
        self.temperature = float(self.temperature)
        self.tools_enabled = bool(self.tools_enabled)
        self.cost_per_move = max(0.0, float(self.cost_per_move))
        self.uses = max(0, int(self.uses))
        self.mean_delta_u = float(self.mean_delta_u)
        self.support_rate = min(1.0, max(0.0, float(self.support_rate)))
        self.tasks_resolved = max(0, int(self.tasks_resolved))

    def can(self, kind: str) -> bool:
        """Whether the role may take `probe` (propose/experiment) or `synthesis` moves."""
        return self.kind == "both" or self.kind == kind

    def to_agent_genome(self) -> AgentGenome:
        return AgentGenome(role=self.name, goal=self.goal, backstory=self.backstory,
                           temperature=self.temperature, model_tier=self.model_tier,
                           tools_enabled=self.tools_enabled,
                           extra={"role_id": self.role_id, "domain_tags": list(self.domain_tags)})

    def quality(self, optimistic_prior: float) -> float:
        """[0, 1] estimate of how productive one move by this role has been.

        Never-used roles get the CEO's optimistic prior so a library can grow
        without new roles being starved by the roles that already have data.
        """
        if self.uses <= 0:
            return min(1.0, max(0.0, optimistic_prior))
        return min(1.0, max(0.0, self.mean_delta_u / DELTA_U_SCALE))


CEO_POLICY_BOUNDS: Dict[str, Tuple[float, float, bool]] = {
    "min_initial_roles": (1, 8, True),
    "max_initial_roles": (1, 8, True),
    "max_active_roles": (1, 12, True),
    "headcount_lambda": (0.0, 1.0, False),
    "exploration_c": (0.0, 2.0, False),
    "optimistic_prior": (0.0, 1.0, False),
    "stall_delta_u": (0.0, 0.5, False),
    "stall_moves": (1, 20, True),
    "recruit_w_stall": (0.0, 3.0, False),
    "recruit_w_unmatched": (0.0, 3.0, False),
    "recruit_w_headcount": (0.0, 3.0, False),
    "recruit_bias": (-8.0, 2.0, False),
    "recruit_cooldown_moves": (0, 20, True),
    "temperature": (0.0, 2.0, False),
}


@dataclass
class CEOPolicyGene(_Model):
    """How a firm sizes and grows its team (V8). Evolvable; LLM-free.

    `enabled` defaults to False: every V5/V6 genome keeps its static bindings.
    The recruit prior is
        P(recruit | E_t) = sigmoid(recruit_bias + w_stall * stall_counter
                                   + w_unmatched * |unmatched_modules|
                                   - w_headcount * |active_roles|)
    and the turn-0 objective is V_org(O) = sum_r Q(r | task) - headcount_lambda * |O|.
    """

    enabled: bool = False
    min_initial_roles: int = 2
    max_initial_roles: int = 4
    max_active_roles: int = 6
    headcount_lambda: float = 0.15
    exploration_c: float = 0.5
    optimistic_prior: float = 0.3
    stall_delta_u: float = 0.05
    stall_moves: int = 4
    recruit_w_stall: float = 0.5
    recruit_w_unmatched: float = 0.8
    recruit_w_headcount: float = 0.4
    recruit_bias: float = -2.0
    recruit_cooldown_moves: int = 3
    recruit_mode: str = "both"
    temperature: float = 0.0
    extra: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.enabled = bool(self.enabled)
        for name, (lo, hi, is_int) in CEO_POLICY_BOUNDS.items():
            raw = getattr(self, name)
            try:
                value = int(round(float(raw))) if is_int else float(raw)
            except (TypeError, ValueError):
                raise GenomeValidationError(f"CEOPolicyGene.{name} must be numeric, got {raw!r}")
            if not lo <= value <= hi:
                raise GenomeValidationError(f"CEOPolicyGene.{name} must be within [{lo}, {hi}], got {value}")
            setattr(self, name, value)
        if self.max_initial_roles < self.min_initial_roles:
            raise GenomeValidationError("CEOPolicyGene.max_initial_roles must be >= min_initial_roles")
        if self.max_active_roles < self.max_initial_roles:
            raise GenomeValidationError("CEOPolicyGene.max_active_roles must be >= max_initial_roles")
        if self.recruit_mode not in RECRUIT_MODES:
            raise GenomeValidationError(f"CEOPolicyGene.recruit_mode must be one of {RECRUIT_MODES}, got {self.recruit_mode!r}")


# --------------------------------------------------------------------------- #
# Task triage
# --------------------------------------------------------------------------- #

@dataclass
class TaskFeatures:
    """Cheap, LLM-free triage of a task used to size the initial team.

    `complexity` in [0, 1] is a heuristic (documented inline), not a learned
    quantity; it only moves the target team size between the CEO's min and
    max. `tags` are the tokens the role library is matched against.
    """

    n_failures: int = 0
    n_modules: int = 0
    n_exception_classes: int = 0
    has_import_error: bool = False
    has_assertion: bool = False
    statement_chars: int = 0
    n_paths_mentioned: int = 0
    tags: List[str] = field(default_factory=list)
    complexity: float = 0.0
    source: str = ""

    @staticmethod
    def _tokens(text: str) -> List[str]:
        toks = [t.lower() for t in _TOKEN_RE.findall(text or "")]
        return [t for t in toks if t not in _STOP and not t.isdigit()]

    @classmethod
    def from_failures(cls, failures: Sequence[str], locations: Optional[Mapping[str, str]] = None,
                      objective: str = "") -> "TaskFeatures":
        """Features from oracle failure lines (legacy benchmark path)."""
        text = "\n".join(failures or [])
        modules = set(_PATH_RE.findall(text))
        for loc in (locations or {}).values():
            modules.add(str(loc).split(":")[0])
        excs = set(_EXC_RE.findall(text))
        tags: List[str] = []
        for m in sorted(modules):
            stem = m.rsplit("/", 1)[-1][:-3] if m.endswith(".py") else m
            tags.append(stem.lower())
        tags += [e.lower() for e in sorted(excs)]
        tags += cls._tokens(text)[:40]
        n_f = len(failures or [])
        # Complexity: more failures, more distinct modules and more distinct
        # exception classes all push towards a larger initial team. Saturates
        # at 12 failures / 4 modules / 4 exception classes.
        complexity = min(1.0, (min(n_f, 12) / 12.0) * 0.4 + (min(len(modules), 4) / 4.0) * 0.4
                         + (min(len(excs), 4) / 4.0) * 0.2)
        return cls(n_failures=n_f, n_modules=len(modules), n_exception_classes=len(excs),
                   has_import_error=bool(re.search(r"ImportError|ModuleNotFoundError", text)),
                   has_assertion="AssertionError" in text, statement_chars=len(objective or ""),
                   n_paths_mentioned=len(modules), tags=normalise_tags(tags), complexity=complexity,
                   source="failures")

    @classmethod
    def from_problem_statement(cls, statement: str, repo: str = "",
                               files: Sequence[str] = ()) -> "TaskFeatures":
        """Features from a GitHub-issue style problem statement (SWE-bench path)."""
        text = statement or ""
        paths = set(_PATH_RE.findall(text)) | set(files or [])
        excs = set(_EXC_RE.findall(text))
        tags: List[str] = []
        if repo:
            tags.append(repo.split("/")[-1].lower())
        for p in sorted(paths):
            parts = [x for x in re.split(r"[/.]", p) if x and x != "py"]
            tags += parts[-2:]
        tags += [e.lower() for e in sorted(excs)]
        tags += cls._tokens(text)[:60]
        # Complexity: long statements, several files and several exception
        # classes suggest cross-cutting work. Saturates at 4000 chars / 4 files.
        complexity = min(1.0, (min(len(text), 4000) / 4000.0) * 0.4 + (min(len(paths), 4) / 4.0) * 0.4
                         + (min(len(excs), 3) / 3.0) * 0.2)
        return cls(n_failures=0, n_modules=len(paths), n_exception_classes=len(excs),
                   has_import_error=bool(re.search(r"ImportError|ModuleNotFoundError", text)),
                   has_assertion="AssertionError" in text, statement_chars=len(text),
                   n_paths_mentioned=len(paths), tags=normalise_tags(tags), complexity=complexity,
                   source="problem_statement")

    def to_dict(self) -> Dict[str, Any]:
        return {"n_failures": self.n_failures, "n_modules": self.n_modules,
                "n_exception_classes": self.n_exception_classes, "has_import_error": self.has_import_error,
                "has_assertion": self.has_assertion, "statement_chars": self.statement_chars,
                "n_paths_mentioned": self.n_paths_mentioned, "tags": list(self.tags),
                "complexity": round(self.complexity, 4), "source": self.source}


# --------------------------------------------------------------------------- #
# Turn-0 organisation selection
# --------------------------------------------------------------------------- #

def role_score(role: RoleAllele, task: TaskFeatures, policy: CEOPolicyGene, total_uses: int) -> float:
    """Q(r | task) + UCB exploration bonus, both in roughly [0, 1].

    Q = quality(r) * (0.5 + 0.5 * overlap(r.tags, task.tags)): a role with
    no tag overlap keeps half its credit (generalists exist), a perfect
    overlap keeps all of it. The bonus favours roles with few uses relative
    to the library's total, so new alleles get tried.
    """
    q = role.quality(policy.optimistic_prior) * (0.5 + 0.5 * tag_overlap(role.domain_tags, task.tags))
    bonus = policy.exploration_c * math.sqrt(math.log(total_uses + 1.0) / (role.uses + 1.0))
    return q + bonus


def search_initial_organization(library: Sequence[RoleAllele], task: TaskFeatures,
                                policy: CEOPolicyGene, rng: Optional[random.Random] = None,
                                ) -> Tuple[List[RoleAllele], Dict[str, Any]]:
    """Pick the initial team: argmax over subsets of V_org(O) = sum score - lambda * |O|.

    Constraints: |O| within [min_initial_roles, max_initial_roles]; O must
    contain at least one role able to probe and one able to synthesise (one
    `both` role satisfies both). The target size is nudged by task complexity
    through the headcount penalty: lambda_eff = lambda * (1.5 - complexity),
    so simple tasks pay more per head. With `temperature > 0` the team is
    sampled from the top-8 subsets by softmax(V / temperature) using `rng`
    (seed diversity); otherwise the best subset is returned, ties broken by
    the sorted tuple of role ids so the choice is reproducible.

    Returns the team and an audit dict (per-role scores, candidate count,
    chosen V_org, lambda_eff) for the ledger.
    """
    roles = [r for r in library]
    if not roles:
        return [], {"candidates": 0, "reason": "empty role library"}
    total_uses = sum(r.uses for r in roles)
    scores = {r.role_id: role_score(r, task, policy, total_uses) for r in roles}
    lam = policy.headcount_lambda * (1.5 - min(1.0, max(0.0, task.complexity)))
    lo = max(1, min(policy.min_initial_roles, len(roles)))
    hi = max(lo, min(policy.max_initial_roles, len(roles)))
    cands: List[Tuple[float, Tuple[str, ...]]] = []
    by_id = {r.role_id: r for r in roles}
    for n in range(lo, hi + 1):
        for combo in itertools.combinations(sorted(by_id), n):
            members = [by_id[i] for i in combo]
            if not any(m.can("probe") for m in members) or not any(m.can("synthesis") for m in members):
                continue
            v = sum(scores[i] for i in combo) - lam * n
            cands.append((v, combo))
    if not cands:
        # Library cannot satisfy the probe+synthesis constraint: take the
        # best-scoring roles regardless so the firm can still run.
        ranked = sorted(roles, key=lambda r: (-scores[r.role_id], r.role_id))[:lo]
        return ranked, {"candidates": 0, "reason": "no subset satisfies probe+synthesis", "lambda_eff": lam,
                        "scores": scores}
    cands.sort(key=lambda c: (-c[0], c[1]))
    chosen_v, chosen = cands[0]
    if policy.temperature > 0 and rng is not None and len(cands) > 1:
        top = cands[:8]
        m = max(v for v, _ in top)
        weights = [math.exp((v - m) / policy.temperature) for v, _ in top]
        chosen_v, chosen = rng.choices(top, weights=weights, k=1)[0]
    team = [by_id[i] for i in chosen]
    return team, {"candidates": len(cands), "chosen": list(chosen), "v_org": round(chosen_v, 4),
                  "lambda_eff": round(lam, 4), "scores": {k: round(v, 4) for k, v in scores.items()},
                  "task": task.to_dict()}


# --------------------------------------------------------------------------- #
# Live organisation state
# --------------------------------------------------------------------------- #

@dataclass
class RoleStats:
    visits: int = 0
    cumulative_delta_u: float = 0.0
    supported: int = 0
    falsified: int = 0
    untestable: int = 0
    syntheses_written: int = 0
    consecutive_stalls: int = 0
    recruited_at_move: int = -1  # -1: part of the turn-0 team

    def to_dict(self) -> Dict[str, Any]:
        return {"visits": self.visits, "cumulative_delta_u": round(self.cumulative_delta_u, 4),
                "supported": self.supported, "falsified": self.falsified, "untestable": self.untestable,
                "syntheses_written": self.syntheses_written, "consecutive_stalls": self.consecutive_stalls,
                "recruited_at_move": self.recruited_at_move}

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "RoleStats":
        return cls(**{k: d.get(k, getattr(cls, k)) for k in cls.__dataclass_fields__})  # type: ignore[arg-type]


class OrgState:
    """The live team during one epistemic search, plus the evidence credited to each role.

    Owned by the search loop (mcts.py). Written by the loop only: roles are
    credited when the gatekeeper returns a verdict for a hypothesis they
    proposed or a patch they wrote; the recruit move appends a role. Nothing
    here is a belief about the code; that stays in the ledger.
    """

    def __init__(self, active_roles: Sequence[RoleAllele], policy: CEOPolicyGene,
                 library: Sequence[RoleAllele] = ()) -> None:
        self.policy = policy
        self.active_roles: List[RoleAllele] = list(active_roles)
        self.library: List[RoleAllele] = list(library)
        self.stats: Dict[str, RoleStats] = {r.role_id: RoleStats() for r in self.active_roles}
        self.stall_counter = 0
        self.unmatched_modules: List[str] = []
        self.recruit_log: List[Dict[str, Any]] = []
        self.last_recruit_move = -10 ** 9
        self.moves_seen = 0

    # -- lookups ------------------------------------------------------------
    def role(self, role_id: str) -> Optional[RoleAllele]:
        for r in self.active_roles:
            if r.role_id == role_id:
                return r
        return None

    @property
    def active_ids(self) -> List[str]:
        return [r.role_id for r in self.active_roles]

    def covered_tags(self) -> Set[str]:
        out: Set[str] = set()
        for r in self.active_roles:
            out.update(r.domain_tags)
        return out

    # -- evidence credit ----------------------------------------------------
    def credit(self, role_id: str, delta_u: float, verdict: str = "", wrote: bool = False,
               move_index: int = 0) -> None:
        """Credit one move's outcome to a role and update the stall counter.

        A move is a stall when its dU is below `policy.stall_delta_u` and it
        did not write a patch; consecutive stalls across *all* roles feed the
        recruit prior, per-role consecutive stalls feed routing.
        """
        self.moves_seen = max(self.moves_seen, move_index + 1)
        st = self.stats.setdefault(role_id, RoleStats())
        st.visits += 1
        st.cumulative_delta_u += float(delta_u)
        v = (verdict or "").upper()
        if v in ("SUPPORTED", "CERTIFIED"):
            st.supported += 1
        elif v == "FALSIFIED":
            st.falsified += 1
        elif v == "UNTESTABLE":
            st.untestable += 1
        if wrote:
            st.syntheses_written += 1
        stalled = (float(delta_u) < self.policy.stall_delta_u) and not wrote
        if stalled:
            st.consecutive_stalls += 1
            self.stall_counter += 1
        else:
            st.consecutive_stalls = 0
            self.stall_counter = 0

    def observe_modules(self, modules: Iterable[str]) -> List[str]:
        """Record modules surfaced by tracebacks/imports that no active role's tags cover.

        A module `pkg/sub/name.py` is covered when any active role carries the
        tag `name`, `sub` or `pkg`. Returns the newly added unmatched modules.
        """
        covered = self.covered_tags()
        new: List[str] = []
        for m in modules or []:
            m = str(m).strip()
            if not m or m in self.unmatched_modules:
                continue
            parts = [p.lower() for p in re.split(r"[/.]", m) if p and p != "py"]
            if any(p in covered for p in parts):
                continue
            self.unmatched_modules.append(m)
            new.append(m)
        return new

    # -- recruitment --------------------------------------------------------
    def recruit_prior(self) -> float:
        p = self.policy
        x = (p.recruit_bias + p.recruit_w_stall * self.stall_counter
             + p.recruit_w_unmatched * len(self.unmatched_modules)
             - p.recruit_w_headcount * len(self.active_roles))
        return sigmoid(x)

    def can_recruit(self, move_index: int) -> bool:
        if len(self.active_roles) >= self.policy.max_active_roles:
            return False
        return (move_index - self.last_recruit_move) >= self.policy.recruit_cooldown_moves

    def library_candidates(self, task_tags: Iterable[str]) -> List[Tuple[float, RoleAllele]]:
        """Library roles not yet active, ranked for the current gaps (unmatched modules + task tags)."""
        gap_tags = list(task_tags or [])
        for m in self.unmatched_modules:
            gap_tags += [p for p in re.split(r"[/.]", m) if p and p != "py"]
        active = set(self.active_ids)
        total = sum(r.uses for r in self.library) or 0
        out: List[Tuple[float, RoleAllele]] = []
        for r in self.library:
            if r.role_id in active:
                continue
            q = r.quality(self.policy.optimistic_prior) * (0.5 + 0.5 * tag_overlap(r.domain_tags, gap_tags))
            q += self.policy.exploration_c * math.sqrt(math.log(total + 1.0) / (r.uses + 1.0))
            out.append((q, r))
        out.sort(key=lambda t: (-t[0], t[1].role_id))
        return out

    def recruit(self, role: RoleAllele, move_index: int, reason: str, source: str) -> None:
        if self.role(role.role_id) is not None:
            raise ValueError(f"role {role.role_id!r} is already active")
        self.active_roles.append(role)
        self.stats[role.role_id] = RoleStats(recruited_at_move=move_index)
        self.recruit_log.append({"move_index": move_index, "role_id": role.role_id, "name": role.name,
                                 "source": source, "reason": reason,
                                 "stall_counter": self.stall_counter,
                                 "unmatched_modules": list(self.unmatched_modules),
                                 "prior": round(self.recruit_prior(), 4)})
        self.last_recruit_move = move_index
        self.stall_counter = 0
        covered = self.covered_tags()
        self.unmatched_modules = [m for m in self.unmatched_modules
                                  if not any(p.lower() in covered for p in re.split(r"[/.]", m) if p)]

    # -- routing ------------------------------------------------------------
    def route_scores(self, kind: str) -> Dict[str, float]:
        """UCB over active roles able to take `kind` moves: mean dU + c * sqrt(ln N / n).

        A freshly recruited role (0 visits) gets the full exploration bonus,
        which is the roadmap's "optimistic prior so PUCT routes the very next
        move to the new expert".
        """
        total = sum(s.visits for s in self.stats.values())
        out: Dict[str, float] = {}
        for r in self.active_roles:
            if not r.can(kind):
                continue
            st = self.stats[r.role_id]
            mean = (st.cumulative_delta_u / st.visits) if st.visits else self.policy.optimistic_prior * DELTA_U_SCALE
            bonus = self.policy.exploration_c * math.sqrt(math.log(total + 1.0) / (st.visits + 1.0))
            out[r.role_id] = mean / DELTA_U_SCALE + bonus
        return out

    def pick_role(self, kind: str) -> Optional[RoleAllele]:
        scores = self.route_scores(kind)
        if not scores:
            return None
        best = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        return self.role(best)

    # -- serialisation ------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return {"active_roles": [r.to_dict() for r in self.active_roles],
                "stats": {k: v.to_dict() for k, v in self.stats.items()},
                "stall_counter": self.stall_counter,
                "unmatched_modules": list(self.unmatched_modules),
                "recruit_log": list(self.recruit_log),
                "recruit_prior": round(self.recruit_prior(), 4),
                "policy": self.policy.to_dict()}

    @classmethod
    def from_dict(cls, d: Mapping[str, Any], library: Sequence[RoleAllele] = ()) -> "OrgState":
        policy = CEOPolicyGene.from_dict(d.get("policy") or {})
        obj = cls([RoleAllele.from_dict(r) for r in d.get("active_roles", [])], policy, library)
        obj.stats = {k: RoleStats.from_dict(v) for k, v in (d.get("stats") or {}).items()}
        for r in obj.active_roles:
            obj.stats.setdefault(r.role_id, RoleStats())
        obj.stall_counter = int(d.get("stall_counter", 0))
        obj.unmatched_modules = list(d.get("unmatched_modules", []))
        obj.recruit_log = list(d.get("recruit_log", []))
        if obj.recruit_log:
            obj.last_recruit_move = max(int(e.get("move_index", -1)) for e in obj.recruit_log)
        return obj
