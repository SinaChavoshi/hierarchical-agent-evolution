"""`HarnessGenome`: the V15–V17 unit of evolution.

A genome is (knobs, patch) plus, from V16, an organization (org_mode, roles, ceo_policy)
and, from V17, evolvable red-team genes (`redteam`):
  * `knobs`    typed settings from `hae.swebench.harness_knobs.KNOB_SPACE`;
  * `patch`    a unified diff against the harness source at the experiment's base commit,
               restricted to `EVOLVABLE_FILES`. An empty patch is the unmodified seed harness;
  * `org_mode` off | static | dynamic (`hae.swebench.org_binding`); `roles` the role library
               (`RoleAllele` dicts) and `ceo_policy` (`CEOPolicyGene` dict) used when org_mode != off;
  * `redteam`  V17 red-team genes (`panel_size`, `no_edit`, `lock_repro_test`, `skeptic_default`,
               `evidence_tests`, `continuous_build`, `max_turns`; `hae.swebench.org_binding.REDTEAM_SPACE`).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from hae.swebench.harness_knobs import resolve_knobs
from hae.swebench.org_binding import resolve_redteam

# Harness source files that code mutations may edit. Grading, datasets and the evaluator
# are deliberately excluded so a candidate cannot change how it is scored.
EVOLVABLE_FILES: List[str] = [
    "hae/swebench/hierarchical_mcts.py",
    "hae/swebench/agentic.py",
]


# Role fields written from evaluation evidence; they do not change what a genome *is*.
_ROLE_STAT_KEYS = ("uses", "mean_delta_u", "support_rate", "tasks_resolved")


@dataclass
class TaskResult:
    """Outcome of one genome on one task (plus V17 red-team verdict confusion counts on training)."""

    instance_id: str
    resolved: bool = False
    f2p_passed: int = 0
    f2p_total: int = 0
    tokens: int = 0
    elapsed_s: float = 0.0
    moves_used: int = 0
    stop: str = ""
    status: str = ""
    rt_tp: int = 0
    rt_fp: int = 0
    rt_tn: int = 0
    rt_fn: int = 0

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TaskResult":
        known = {k: d[k] for k in cls.__dataclass_fields__ if k in d}
        return cls(**known)


@dataclass
class HarnessGenome:
    genome_id: str
    generation: int = 0
    parent_ids: List[str] = field(default_factory=list)
    origin: str = "seed"  # seed | mutate_knobs | mutate_code | crossover | random | redteam_*
    knobs: Dict[str, Any] = field(default_factory=dict)
    patch: str = ""
    notes: List[str] = field(default_factory=list)
    results: Dict[str, TaskResult] = field(default_factory=dict)
    org_mode: str = "off"
    roles: List[Dict[str, Any]] = field(default_factory=list)
    ceo_policy: Dict[str, Any] = field(default_factory=dict)
    redteam: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.knobs = resolve_knobs(self.knobs)
        self.org_mode = self.org_mode if self.org_mode in ("off", "static", "dynamic") else "off"
        if self.redteam:
            self.redteam = resolve_redteam(self.redteam)

    # ------------------------------------------------------------------ #
    @property
    def has_org(self) -> bool:
        return self.org_mode != "off" or bool(self.roles) or bool(self.ceo_policy)

    @property
    def has_redteam(self) -> bool:
        return bool(self.redteam)

    @property
    def content_hash(self) -> str:
        content: Dict[str, Any] = {"knobs": self.knobs, "patch": self.patch}
        if self.has_org:  # V15 genomes keep their hashes
            content.update({"org_mode": self.org_mode, "ceo_policy": self.ceo_policy,
                            "roles": [{k: v for k, v in r.items() if k not in _ROLE_STAT_KEYS} for r in self.roles]})
        if self.has_redteam:  # V15/V16 genomes keep their hashes
            content["redteam"] = self.redteam
        blob = json.dumps(content, sort_keys=True)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]

    @property
    def patch_lines(self) -> int:
        return sum(1 for ln in self.patch.splitlines() if ln[:1] in "+-" and not ln.startswith(("+++", "---")))

    def evaluated_on(self, task_ids: List[str]) -> bool:
        return all(t in self.results for t in task_ids)

    def success_rate(self, task_ids: Optional[List[str]] = None) -> float:
        ids = list(task_ids) if task_ids is not None else list(self.results)
        ids = [t for t in ids if t in self.results]
        if not ids:
            return 0.0
        return sum(1 for t in ids if self.results[t].resolved) / len(ids)

    def mean_tokens(self, task_ids: Optional[List[str]] = None) -> float:
        ids = list(task_ids) if task_ids is not None else list(self.results)
        ids = [t for t in ids if t in self.results]
        if not ids:
            return 0.0
        return sum(self.results[t].tokens for t in ids) / len(ids)

    def partial_credit(self, task_ids: Optional[List[str]] = None) -> float:
        """Mean FAIL_TO_PASS fraction over unresolved tasks (0 when there are none)."""
        ids = list(task_ids) if task_ids is not None else list(self.results)
        un = [self.results[t] for t in ids if t in self.results and not self.results[t].resolved]
        if not un:
            return 0.0
        return sum((r.f2p_passed / r.f2p_total) if r.f2p_total else 0.0 for r in un) / len(un)

    def red_team_confusion(self, task_ids: Optional[List[str]] = None) -> Tuple[int, int, int, int]:
        """`(tp, fp, tn, fn)` of red-team verdicts across `task_ids`."""
        ids = list(task_ids) if task_ids is not None else list(self.results)
        rows = [self.results[t] for t in ids if t in self.results]
        tp = sum(r.rt_tp for r in rows)
        fp = sum(r.rt_fp for r in rows)
        tn = sum(r.rt_tn for r in rows)
        fn = sum(r.rt_fn for r in rows)
        return tp, fp, tn, fn

    def red_team_score(self, task_ids: Optional[List[str]] = None) -> float:
        """Balanced accuracy of red-team verdicts against hidden-test labels on training tasks,
        where a false PASS (`fp`) counts double (`tn / (tn + 2*fp)`)."""
        tp, fp, tn, fn = self.red_team_confusion(task_ids)
        sens = (tp / float(tp + fn)) if (tp + fn) > 0 else 0.5
        spec_2x = (tn / float(tn + 2 * fp)) if (tn + fp) > 0 else 0.5
        return 0.5 * (sens + spec_2x)

    def fitness(self, task_ids: Optional[List[str]] = None, token_lambda: float = 0.05,
                partial_weight: float = 0.0, redteam_weight: float = 0.0) -> float:
        """Resolve rate, plus `partial_weight` x partial FAIL_TO_PASS credit on unresolved tasks
        (scaled by their share), plus `redteam_weight` x `red_team_score`, minus `token_lambda`
        per million mean tokens per task."""
        ids = list(task_ids) if task_ids is not None else list(self.results)
        present = [t for t in ids if t in self.results]
        n = max(1, len(present))
        n_unresolved = sum(1 for t in present if not self.results[t].resolved)
        rt_term = redteam_weight * self.red_team_score(task_ids) if redteam_weight else 0.0
        return (self.success_rate(task_ids)
                + partial_weight * self.partial_credit(task_ids) * n_unresolved / n
                + rt_term
                - token_lambda * self.mean_tokens(task_ids) / 1e6)

    # ------------------------------------------------------------------ #
    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["results"] = {k: asdict(v) for k, v in self.results.items()}
        d["content_hash"] = self.content_hash
        if not self.has_redteam:
            d.pop("redteam", None)
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "HarnessGenome":
        g = cls(
            genome_id=d["genome_id"],
            generation=int(d.get("generation", 0)),
            parent_ids=list(d.get("parent_ids") or []),
            origin=d.get("origin", "seed"),
            knobs=dict(d.get("knobs") or {}),
            patch=d.get("patch", "") or "",
            notes=list(d.get("notes") or []),
            org_mode=d.get("org_mode", "off") or "off",
            roles=[dict(r) for r in (d.get("roles") or [])],
            ceo_policy=dict(d.get("ceo_policy") or {}),
            redteam=dict(d.get("redteam") or {}),
        )
        g.results = {k: TaskResult.from_dict(v) for k, v in (d.get("results") or {}).items()}
        return g
