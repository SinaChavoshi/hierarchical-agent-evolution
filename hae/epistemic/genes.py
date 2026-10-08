"""Heritable operators for `EpistemicPolicyGene`.

The gene itself is declared in `hae.genome.schema` (a `CompanyGenome` embeds
it, and the schema module must not import the search engine). What lives
here is how the gene changes between generations:

  * `mutate_epistemic_policy` perturbs one to three numeric fields by a
    Gaussian step scaled to each field's declared range, and occasionally
    re-routes one move kind to a different department keyword;
  * `crossover_epistemic_policy` is uniform crossover, field by field.

Neither operator touches `enabled`. Whether a generation searches is an
experimental control the generation config sets for the whole cohort; a
mutant that silently switched the search off would be an uncontrolled
comparison dressed up as a child.
"""

from __future__ import annotations

import random
from typing import Dict, Optional

from hae.genome.schema import (
    DEFAULT_EPISTEMIC_ROLE_BINDINGS, EPISTEMIC_MOVE_KINDS, EPISTEMIC_POLICY_BOUNDS,
    EpistemicPolicyGene,
)

# Department keywords a move kind may be routed to. Matched case-insensitively
# against a department's id, name and mandate by the runner.
ROLE_VOCABULARY = ("engineering", "qa", "verification", "redteam", "security",
                   "systems", "platform", "formal", "test")

NUMERIC_FIELDS = tuple(EPISTEMIC_POLICY_BOUNDS.keys())


def _clamp(name: str, value: float):
    lo, hi, is_int = EPISTEMIC_POLICY_BOUNDS[name]
    v = max(lo, min(hi, value))
    return int(round(v)) if is_int else round(float(v), 4)


def mutate_epistemic_policy(gene: Optional[EpistemicPolicyGene], rng: random.Random,
                            strength: float = 0.15, rebind_probability: float = 0.15
                            ) -> EpistemicPolicyGene:
    """A perturbed copy. Always within bounds; never flips `enabled` or `search_algorithm`."""
    base = gene if gene is not None else EpistemicPolicyGene()
    data = base.to_dict()
    n_fields = rng.randint(1, 3)
    for name in rng.sample(NUMERIC_FIELDS, n_fields):
        lo, hi, is_int = EPISTEMIC_POLICY_BOUNDS[name]
        span = float(hi - lo)
        step = rng.gauss(0.0, strength * span)
        if is_int and abs(step) < 1.0:
            step = 1.0 if step >= 0 else -1.0
        # Read from the gene, not the dict: `to_dict` omits V9 fields at their defaults.
        data[name] = _clamp(name, float(getattr(base, name)) + step)
    if rng.random() < rebind_probability:
        kind = rng.choice(EPISTEMIC_MOVE_KINDS)
        bindings: Dict[str, str] = dict(data.get("role_bindings") or DEFAULT_EPISTEMIC_ROLE_BINDINGS)
        choices = [kw for kw in ROLE_VOCABULARY if kw != bindings.get(kind)]
        bindings[kind] = rng.choice(choices)
        data["role_bindings"] = bindings
    data["enabled"] = base.enabled
    data["search_algorithm"] = base.search_algorithm
    return EpistemicPolicyGene.from_dict(data)


def crossover_epistemic_policy(a: Optional[EpistemicPolicyGene], b: Optional[EpistemicPolicyGene],
                               rng: random.Random) -> EpistemicPolicyGene:
    """Uniform crossover. `enabled` and `search_algorithm` are inherited from parent `a`."""
    pa = a if a is not None else EpistemicPolicyGene()
    pb = b if b is not None else EpistemicPolicyGene()
    da, db = pa.to_dict(), pb.to_dict()
    child = {}
    for name in NUMERIC_FIELDS:
        child[name] = getattr(pa, name) if rng.random() < 0.5 else getattr(pb, name)
    bindings = {}
    for kind in EPISTEMIC_MOVE_KINDS:
        src = da if rng.random() < 0.5 else db
        bindings[kind] = (src.get("role_bindings") or DEFAULT_EPISTEMIC_ROLE_BINDINGS).get(
            kind, DEFAULT_EPISTEMIC_ROLE_BINDINGS[kind])
    child["role_bindings"] = bindings
    child["enabled"] = pa.enabled
    # V9: the search algorithm is a cohort-level control like `enabled`;
    # inherited from parent `a`, never recombined.
    child["search_algorithm"] = pa.search_algorithm
    return EpistemicPolicyGene.from_dict(child)


def policy_distance(a: EpistemicPolicyGene, b: EpistemicPolicyGene) -> float:
    """Mean normalised absolute difference over numeric fields. 0 means identical."""
    total = 0.0
    for name in NUMERIC_FIELDS:
        lo, hi, _ = EPISTEMIC_POLICY_BOUNDS[name]
        total += abs(float(getattr(a, name)) - float(getattr(b, name))) / float(hi - lo)
    return round(total / len(NUMERIC_FIELDS), 6)
