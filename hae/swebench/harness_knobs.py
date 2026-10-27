"""V15 evolvable harness knobs.

Every numeric or boolean setting that V14 hard-coded inside
`HierarchicalAgenticMCTSEngine` is declared here as a typed gene. The
engine reads its settings through `resolve_knobs`, so a genome can override
any subset (via `epistemic_policy.extra["v15_knobs"]`) and anything it leaves
out falls back to the V14 default.

Defaults reproduce the V14 behavior exactly, so an empty knob dict is the
hand-designed baseline.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional


@dataclass(frozen=True)
class KnobSpec:
    """One evolvable setting: name, type, inclusive range, default and description."""

    name: str
    kind: str  # "int" | "float" | "bool"
    default: Any
    low: Any = None
    high: Any = None
    description: str = ""

    def clip(self, value: Any) -> Any:
        if self.kind == "bool":
            if isinstance(value, str):
                return value.strip().lower() in ("1", "true", "yes", "on")
            return bool(value)
        try:
            v = float(value)
        except (TypeError, ValueError):
            return self.default
        if math.isnan(v):
            return self.default
        v = min(max(v, float(self.low)), float(self.high))
        return int(round(v)) if self.kind == "int" else round(v, 4)

    def sample(self, rng: random.Random) -> Any:
        if self.kind == "bool":
            return rng.random() < 0.5
        if self.kind == "int":
            return rng.randint(int(self.low), int(self.high))
        return round(rng.uniform(float(self.low), float(self.high)), 4)

    def mutate(self, value: Any, rng: random.Random, strength: float = 0.25) -> Any:
        """Gaussian step scaled to the range (ints and floats); flip for bools."""
        if self.kind == "bool":
            return not bool(value)
        span = float(self.high) - float(self.low)
        step = rng.gauss(0.0, max(strength * span, 1.0 if self.kind == "int" else 1e-3))
        new = self.clip(float(value) + step)
        if new == value and self.kind == "int":
            new = self.clip(float(value) + (1 if rng.random() < 0.5 else -1))
        return new


KNOB_SPACE: Dict[str, KnobSpec] = {
    spec.name: spec
    for spec in (
        KnobSpec("min_budget_moves", "int", 68, 30, 110, "Floor on the total L2 move budget per instance."),
        KnobSpec("max_episodes", "int", 4, 1, 7, "Maximum AB-MCTS episodes (GEN or CONT) per instance."),
        KnobSpec("recon_max_turns", "int", 3, 1, 8, "Turns given to the L1-A reconnaissance episode."),
        KnobSpec("gen1_turns_min", "int", 14, 6, 24, "Minimum turns for the first GEN branch."),
        KnobSpec("gen1_turns_max", "int", 18, 8, 30, "Maximum turns for the first GEN branch."),
        KnobSpec("genk_turns_min", "int", 10, 4, 20, "Minimum turns for later GEN branches."),
        KnobSpec("genk_turns_max", "int", 15, 6, 26, "Maximum turns for later GEN branches."),
        KnobSpec("cont_turns_min", "int", 8, 3, 18, "Minimum turns for a CONT refinement episode."),
        KnobSpec("cont_turns_max", "int", 12, 4, 24, "Maximum turns for a CONT refinement episode."),
        KnobSpec("falsifier_max_turns", "int", 6, 2, 12, "Turns given to the L1-B adversarial falsifier."),
        KnobSpec("gen1_temperature", "float", 0.2, 0.0, 0.9, "Sampling temperature for the first GEN branch."),
        KnobSpec("genk_temperature", "float", 0.35, 0.0, 1.0, "Sampling temperature for later GEN branches."),
        KnobSpec("cont_temperature", "float", 0.25, 0.0, 0.9, "Sampling temperature for CONT refinement."),
        KnobSpec("cont_near_miss_min_diff_chars", "int", 1500, 200, 6000,
                 "Greenfield near-miss threshold: diff size above which a compiling node is refined (CONT)."),
        KnobSpec("diff_bloat_threshold", "int", 120, 40, 600, "Diff lines above which a checkpoint is penalized."),
        KnobSpec("enable_recon", "bool", True, description="Run the L1-A reconnaissance episode."),
        KnobSpec("enable_falsifier", "bool", True, description="Run the L1-B adversarial falsifier."),
        KnobSpec("enable_cont_refinement", "bool", True, description="Allow CONT refinement of near-miss nodes."),
    )
}

V14_DEFAULT_KNOBS: Dict[str, Any] = {name: spec.default for name, spec in KNOB_SPACE.items()}


def resolve_knobs(overrides: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """Merge `overrides` onto the V14 defaults, clipping each value to its range.

    Unknown keys are ignored. Paired min/max knobs are reordered so min <= max.
    """
    knobs = dict(V14_DEFAULT_KNOBS)
    for key, value in dict(overrides or {}).items():
        spec = KNOB_SPACE.get(key)
        if spec is not None:
            knobs[key] = spec.clip(value)
    for lo, hi in (("gen1_turns_min", "gen1_turns_max"), ("genk_turns_min", "genk_turns_max"),
                   ("cont_turns_min", "cont_turns_max")):
        if knobs[lo] > knobs[hi]:
            knobs[lo], knobs[hi] = knobs[hi], knobs[lo]
    return knobs


def sample_knobs(rng: random.Random) -> Dict[str, Any]:
    """Uniform random sample from the full knob space (used by the random-search control)."""
    return resolve_knobs({name: spec.sample(rng) for name, spec in KNOB_SPACE.items()})


def mutate_knobs(knobs: Mapping[str, Any], rng: random.Random, n_genes: int = 2,
                 strength: float = 0.25) -> Dict[str, Any]:
    """Mutate `n_genes` randomly chosen knobs."""
    base = resolve_knobs(knobs)
    for name in rng.sample(sorted(KNOB_SPACE), k=min(n_genes, len(KNOB_SPACE))):
        base[name] = KNOB_SPACE[name].mutate(base[name], rng, strength=strength)
    return resolve_knobs(base)


def crossover_knobs(a: Mapping[str, Any], b: Mapping[str, Any], rng: random.Random) -> Dict[str, Any]:
    """Uniform crossover."""
    ka, kb = resolve_knobs(a), resolve_knobs(b)
    return resolve_knobs({name: (ka if rng.random() < 0.5 else kb)[name] for name in KNOB_SPACE})
