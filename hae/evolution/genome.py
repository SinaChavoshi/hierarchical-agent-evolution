"""`HarnessGenome`: the V15 unit of evolution.

A genome is (knobs, patch):
  * `knobs`  typed settings from `hae.swebench.harness_knobs.KNOB_SPACE`;
  * `patch`  a unified diff against the harness source at the experiment's base commit,
             restricted to `EVOLVABLE_FILES`. An empty patch is the unmodified seed harness.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from hae.swebench.harness_knobs import resolve_knobs

# Harness source files that code mutations may edit. Grading, datasets and the evaluator
# are deliberately excluded so a candidate cannot change how it is scored.
EVOLVABLE_FILES: List[str] = [
    "hae/swebench/hierarchical_mcts.py",
    "hae/swebench/agentic.py",
]


@dataclass
class TaskResult:
    """Outcome of one genome on one task."""

    instance_id: str
    resolved: bool = False
    f2p_passed: int = 0
    f2p_total: int = 0
    tokens: int = 0
    elapsed_s: float = 0.0
    moves_used: int = 0
    stop: str = ""
    status: str = ""

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TaskResult":
        known = {k: d[k] for k in cls.__dataclass_fields__ if k in d}
        return cls(**known)


@dataclass
class HarnessGenome:
    genome_id: str
    generation: int = 0
    parent_ids: List[str] = field(default_factory=list)
    origin: str = "seed"  # seed | mutate_knobs | mutate_code | crossover | random
    knobs: Dict[str, Any] = field(default_factory=dict)
    patch: str = ""
    notes: List[str] = field(default_factory=list)
    results: Dict[str, TaskResult] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.knobs = resolve_knobs(self.knobs)

    # ------------------------------------------------------------------ #
    @property
    def content_hash(self) -> str:
        blob = json.dumps({"knobs": self.knobs, "patch": self.patch}, sort_keys=True)
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

    def fitness(self, task_ids: Optional[List[str]] = None, token_lambda: float = 0.05,
                partial_weight: float = 0.0) -> float:
        """Resolve rate, plus `partial_weight` x partial FAIL_TO_PASS credit on unresolved tasks
        (scaled by their share), minus `token_lambda` per million mean tokens per task."""
        ids = list(task_ids) if task_ids is not None else list(self.results)
        present = [t for t in ids if t in self.results]
        n = max(1, len(present))
        n_unresolved = sum(1 for t in present if not self.results[t].resolved)
        return (self.success_rate(task_ids)
                + partial_weight * self.partial_credit(task_ids) * n_unresolved / n
                - token_lambda * self.mean_tokens(task_ids) / 1e6)

    # ------------------------------------------------------------------ #
    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["results"] = {k: asdict(v) for k, v in self.results.items()}
        d["content_hash"] = self.content_hash
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
        )
        g.results = {k: TaskResult.from_dict(v) for k, v in (d.get("results") or {}).items()}
        return g
