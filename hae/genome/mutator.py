"""Evolutionary Mutation & Crossover Engine ('Mutate Everything').

Two generations of operators live here:

* `OrganizationalMutator` -- the V1 LLM-driven whole-genome mutator (kept for
  the legacy pipeline).
* The V8 role-library operators below it -- deterministic, LLM-free functions
  the breeder applies to `CompanyGenome.role_library` and `ceo_policy` at the
  generation boundary: running-mean statistics updates, promotion of roles
  recruited mid-run, pruning of roles that never earned dU, crossover of two
  libraries, bounded jitter of the CEO gene, and a one-tag text mutation.
  Every one of them takes the breeder's `random.Random` so the same spec
  breeds the same child.
"""

import copy
import json
import random
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from hae.genome.schema import (
    CEO_POLICY_BOUNDS, DELTA_U_SCALE, MAX_ROLE_LIBRARY, RECRUIT_MODES, CEOPolicyGene, CompanyGenome,
    DepartmentGenome, AgentGenome, EvaluationResult, RoleAllele, normalise_tags,
)
from hae.infra.llm import call_llm

MUTATOR_SYSTEM_PROMPT = """You are an Evolutionary Organizational Architect and Meta-Prompt Engineer.
Your task is to analyze the performance, score, and identified bottlenecks of a virtual agent organization,
and MUTATE EVERYTHING about its genome to produce a superior next-generation company.

You can and should mutate:
1. Agent Persona Prompts & Backstories: Inject sharper mental models, specialized technical expertise, and domain biases.
2. Department Topology & Roles: Add new critical specialized roles, remove redundant ones, or adjust headcount.
3. Coordination & Delegation Protocols: Alter how managers synthesize and how the CEO resolves trade-offs.
4. Hyperparameters: Adjust sampling temperatures for each role (e.g. lower for auditing/finance, higher for ideation).

Return ONLY valid JSON representing the mutated CompanyGenome matching the schema:
{
  "ceo": {
    "role": "...",
    "goal": "...",
    "backstory": "...",
    "temperature": <float>,
    "model_tier": "executive",
    "system_instructions": "..."
  },
  "executive_deliberation_rules": "...",
  "departments": [
    {
      "dept_id": "...",
      "name": "...",
      "mandate": "...",
      "delegation_rules": "...",
      "manager": { "role": "...", "goal": "...", "backstory": "...", "temperature": <float>, "model_tier": "executive" },
      "agents": [
        { "role": "...", "goal": "...", "backstory": "...", "temperature": <float>, "model_tier": "worker" }
      ]
    }
  ],
  "mutation_summary": "<One sentence explaining the strategic mutation applied>"
}
"""

class OrganizationalMutator:
    """Applies genetic operators (mutation, crossover, topology adaptation) across generations."""

    def mutate(
        self,
        parent: CompanyGenome,
        eval_result: EvaluationResult,
        new_company_id: str,
        target_generation: int
    ) -> CompanyGenome:
        """Mutates an entire company genome conditioned on evaluation feedback."""
        prompt = f"""PERFORM ORGANIZATIONAL MUTATION:

PARENT COMPANY ID: {parent.company_id}
CURRENT OVERALL FITNESS SCORE: {eval_result.fitness.fitness_score}/100
IDENTIFIED BOTTLENECKS:
{json.dumps(eval_result.fitness.identified_bottlenecks, indent=2)}

QUALITATIVE JUDGE FEEDBACK:
{eval_result.fitness.qualitative_feedback}

CURRENT PARENT GENOME STRUCTURE:
{parent.to_json(indent=2)}

Design a mutated, upgraded organization that systematically overcomes these bottlenecks. Mutate backstories, roles, and rules. Return only the JSON."""

        raw_response = call_llm(
            prompt=prompt,
            model_name="gemini-2.5-pro",
            temperature=0.7,
            system_instruction=MUTATOR_SYSTEM_PROMPT
        )

        try:
            cleaned = raw_response.strip()
            if "```json" in cleaned:
                cleaned = re.search(r'```json\s*(.*?)\s*```', cleaned, re.DOTALL).group(1)
            elif "```" in cleaned:
                cleaned = re.search(r'```\s*(.*?)\s*```', cleaned, re.DOTALL).group(1)
            mutated_data = json.loads(cleaned)

            # Reconstruct DepartmentGenome list
            departments = []
            for d in mutated_data.get("departments", []):
                manager = AgentGenome(**d["manager"])
                agents = [AgentGenome(**a) for a in d.get("agents", [])]
                departments.append(DepartmentGenome(
                    dept_id=d["dept_id"],
                    name=d["name"],
                    mandate=d["mandate"],
                    manager=manager,
                    agents=agents,
                    delegation_rules=d.get("delegation_rules", "Sequential review")
                ))

            ceo = AgentGenome(**mutated_data["ceo"])
            mutation_note = mutated_data.get("mutation_summary", "Holistic prompt and topology mutation")

            history = list(parent.mutation_history)
            history.append(f"Gen {target_generation} from {parent.company_id}: {mutation_note}")

            return CompanyGenome(
                company_id=new_company_id,
                generation=target_generation,
                parent_ids=[parent.company_id],
                mutation_history=history,
                ceo=ceo,
                departments=departments,
                executive_deliberation_rules=mutated_data.get(
                    "executive_deliberation_rules",
                    parent.executive_deliberation_rules
                )
            )
        except Exception as e:
            # Safe programmatic fallback mutation if LLM JSON format had flaws
            return self._fallback_programmatic_mutation(parent, new_company_id, target_generation, str(e))

    def _fallback_programmatic_mutation(
        self,
        parent: CompanyGenome,
        new_company_id: str,
        target_generation: int,
        err_msg: str
    ) -> CompanyGenome:
        """Applies stochastic local mutations if LLM restructuring encountered parsing issues."""
        child = copy.deepcopy(parent)
        child.company_id = new_company_id
        child.generation = target_generation
        child.parent_ids = [parent.company_id]
        
        # Jitter temperatures
        child.ceo.temperature = max(0.2, min(1.2, child.ceo.temperature + random.uniform(-0.15, 0.15)))
        for dept in child.departments:
            dept.manager.temperature = max(0.2, min(1.2, dept.manager.temperature + random.uniform(-0.1, 0.1)))
            for agent in dept.agents:
                agent.temperature = max(0.2, min(1.2, agent.temperature + random.uniform(-0.15, 0.15)))

        child.mutation_history.append(f"Gen {target_generation}: Stochastic temperature & prompt jitter (fallback: {err_msg[:40]})")
        return child

    def crossover(
        self,
        parent_a: CompanyGenome,
        parent_b: CompanyGenome,
        new_company_id: str,
        target_generation: int
    ) -> CompanyGenome:
        """Recombines the highest performing departments from two parent firms."""
        child_departments = []
        dept_ids_a = {d.dept_id: d for d in parent_a.departments}
        dept_ids_b = {d.dept_id: d for d in parent_b.departments}
        all_ids = list(set(list(dept_ids_a.keys()) + list(dept_ids_b.keys())))

        for dept_id in all_ids:
            if dept_id in dept_ids_a and dept_id in dept_ids_b:
                # Randomly pick from Parent A or Parent B
                chosen = copy.deepcopy(random.choice([dept_ids_a[dept_id], dept_ids_b[dept_id]]))
            elif dept_id in dept_ids_a:
                chosen = copy.deepcopy(dept_ids_a[dept_id])
            else:
                chosen = copy.deepcopy(dept_ids_b[dept_id])
            child_departments.append(chosen)

        # CEO inherited from either Parent A or Parent B with crossover tweaks
        chosen_ceo = copy.deepcopy(random.choice([parent_a.ceo, parent_b.ceo]))

        return CompanyGenome(
            company_id=new_company_id,
            generation=target_generation,
            parent_ids=[parent_a.company_id, parent_b.company_id],
            mutation_history=[f"Gen {target_generation}: Sexual crossover of {parent_a.company_id} and {parent_b.company_id}"],
            ceo=chosen_ceo,
            departments=child_departments,
            executive_deliberation_rules=parent_a.executive_deliberation_rules
        )


# --------------------------------------------------------------------------- #
# V8: role-library and CEO-policy operators (LLM-free, deterministic)
# --------------------------------------------------------------------------- #
#
# Thresholds follow the roadmap (docs/v8_role_evolution_and_swebench_roadmap.md
# section 1.4): a role recruited mid-run is promoted into the persistent
# library when it earned a verified SUPPORTED probe or >= 0.20 cumulative dU
# (or resolved a task); a library role is pruned when it was tried often and
# earned net-zero dU. Statistics are end-of-move credit (see the honesty note
# in hae/epistemic/org.py): they rank roles, they do not prove causation.

PROMOTE_MIN_SUPPORTED = 1
PROMOTE_MIN_DELTA_U = 0.20
PRUNE_MIN_USES = 6
PRUNE_MAX_MEAN_DELTA_U = 0.0
CEO_NUMERIC_FIELDS = tuple(CEO_POLICY_BOUNDS.keys())


@dataclass
class RoleStatsSummary:
    """What one generation's trajectories say about one role, summed over firms.

    `allele` is the serialised `RoleAllele` as last seen in a trajectory; for
    a role synthesised mid-run it is the only place the definition exists, so
    promotion reads it from here.
    """

    role_id: str
    name: str = ""
    uses: int = 0
    cumulative_delta_u: float = 0.0
    supported: int = 0
    falsified: int = 0
    untestable: int = 0
    syntheses_written: int = 0
    firms: int = 0            # result records in which the role was active
    recruited: int = 0        # ... in which it joined mid-run rather than at turn 0
    tasks_resolved: int = 0   # ... that passed the oracle while this role wrote a patch or earned a SUPPORTED
    allele: Optional[Dict[str, Any]] = None

    @property
    def mean_delta_u(self) -> float:
        return self.cumulative_delta_u / self.uses if self.uses else 0.0

    @property
    def support_rate(self) -> float:
        return self.supported / self.uses if self.uses else 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {"role_id": self.role_id, "name": self.name, "uses": self.uses,
                "cumulative_delta_u": round(self.cumulative_delta_u, 4), "mean_delta_u": round(self.mean_delta_u, 4),
                "supported": self.supported, "falsified": self.falsified, "untestable": self.untestable,
                "syntheses_written": self.syntheses_written, "support_rate": round(self.support_rate, 4),
                "firms": self.firms, "recruited": self.recruited, "tasks_resolved": self.tasks_resolved}


def _clamp_ceo(name: str, value: float):
    lo, hi, is_int = CEO_POLICY_BOUNDS[name]
    v = max(lo, min(hi, value))
    return int(round(v)) if is_int else round(float(v), 4)


def _repair_ceo_ordering(data: Dict[str, Any]) -> List[str]:
    """Restores min_initial <= max_initial <= max_active after a perturbation."""
    notes: List[str] = []
    if data["max_initial_roles"] < data["min_initial_roles"]:
        notes.append(f"ceo_policy.max_initial_roles raised to {data['min_initial_roles']} (>= min_initial_roles)")
        data["max_initial_roles"] = data["min_initial_roles"]
    if data["max_active_roles"] < data["max_initial_roles"]:
        notes.append(f"ceo_policy.max_active_roles raised to {data['max_initial_roles']} (>= max_initial_roles)")
        data["max_active_roles"] = data["max_initial_roles"]
    return notes


def library_rank_key(role: RoleAllele, policy: Optional[CEOPolicyGene] = None) -> Tuple[int, float, str]:
    """Sort key, best first: tasks resolved, then estimated move quality, then id."""
    prior = policy.optimistic_prior if policy is not None else CEOPolicyGene().optimistic_prior
    return (-role.tasks_resolved, -round(role.quality(prior), 6), role.role_id)


def cap_library(library: Sequence[RoleAllele], policy: Optional[CEOPolicyGene] = None,
                cap: int = MAX_ROLE_LIBRARY) -> List[RoleAllele]:
    """At most `cap` roles, keeping the best by `library_rank_key`.

    If the cut would leave no probe-capable or no synthesis-capable role, the
    best dropped role of the missing kind replaces the worst kept one.
    """
    roles = list(library)
    if len(roles) <= cap:
        return roles
    ranked = sorted(roles, key=lambda r: library_rank_key(r, policy))
    kept, dropped = ranked[:cap], ranked[cap:]
    for need in ("probe", "synthesis"):
        if any(r.can(need) for r in kept):
            continue
        swap = next((r for r in dropped if r.can(need)), None)
        if swap is not None and kept:
            kept[-1] = swap
    return kept


def update_role_statistics(library: Sequence[RoleAllele], stats: Mapping[str, RoleStatsSummary]
                           ) -> Tuple[List[RoleAllele], List[str]]:
    """Folds one generation's per-role evidence into each role's lifetime statistics.

    `mean_delta_u` and `support_rate` are running means weighted by `uses`,
    so a role with 60 historical moves is nudged, not overwritten, by 6 new
    ones. Credit is end-of-move credit (org.py): the role that proposed the
    hypothesis or wrote the patch at the moment the gatekeeper ruled.
    """
    out: List[RoleAllele] = []
    notes: List[str] = []
    for role in library:
        r = role.copy()
        s = stats.get(r.role_id)
        if s is not None and s.uses > 0:
            total = r.uses + s.uses
            r.mean_delta_u = round((r.mean_delta_u * r.uses + s.cumulative_delta_u) / total, 4)
            r.support_rate = round(min(1.0, (r.support_rate * r.uses + s.supported) / total), 4)
            r.uses = total
            r.tasks_resolved += s.tasks_resolved
            notes.append(f"{r.role_id}: +{s.uses} uses (gen dU/move {s.mean_delta_u:+.3f}, "
                         f"supported {s.supported}) -> mean dU {r.mean_delta_u:+.3f} over {r.uses}")
        out.append(r)
    return out, notes


def promoted_roles(stats: Mapping[str, RoleStatsSummary], generation: int,
                   existing_ids: Iterable[str] = ()) -> List[RoleAllele]:
    """Roles recruited mid-run that earned their place (roadmap section 1.4).

    A recruited role is promoted when the generation credited it with at least
    `PROMOTE_MIN_SUPPORTED` SUPPORTED verdicts, or `PROMOTE_MIN_DELTA_U`
    cumulative dU, or a resolved task. Its statistics are filled from the
    trajectory and `created_generation` is the generation being bred.
    """
    skip = set(existing_ids)
    out: List[RoleAllele] = []
    for rid in sorted(stats):
        s = stats[rid]
        if rid in skip or s.recruited <= 0 or not isinstance(s.allele, Mapping):
            continue
        earned = (s.supported >= PROMOTE_MIN_SUPPORTED or s.cumulative_delta_u >= PROMOTE_MIN_DELTA_U
                  or s.tasks_resolved >= 1)
        if not earned:
            continue
        data = dict(s.allele)
        origin = str(data.get("origin") or "")
        if not origin.startswith("recruited:"):
            data["origin"] = f"recruited:{origin or 'unknown'}"
        data.update(uses=s.uses, mean_delta_u=round(s.mean_delta_u, 4), support_rate=round(s.support_rate, 4),
                    tasks_resolved=s.tasks_resolved, created_generation=int(generation))
        try:
            out.append(RoleAllele.from_dict(data))
        except Exception:  # a malformed trajectory entry must not abort the breed
            continue
    return out


def prune_roles(library: Sequence[RoleAllele], stats: Mapping[str, RoleStatsSummary],
                policy: Optional[CEOPolicyGene] = None, protected_ids: Iterable[str] = ()
                ) -> Tuple[List[RoleAllele], List[str]]:
    """Drops roles the generation tried >= `PRUNE_MIN_USES` times for net-zero dU.

    Never prunes below `policy.max_initial_roles` roles, never a role that
    resolved a task (this generation or ever), never the last probe-capable
    or synthesis-capable role, and never a role in `protected_ids`.
    """
    floor = max(1, policy.max_initial_roles if policy is not None else CEOPolicyGene().max_initial_roles)
    keep = list(library)
    notes: List[str] = []
    protected = set(protected_ids)

    def prunable(r: RoleAllele) -> bool:
        s = stats.get(r.role_id)
        return (s is not None and s.uses >= PRUNE_MIN_USES and s.mean_delta_u <= PRUNE_MAX_MEAN_DELTA_U
                and s.tasks_resolved == 0 and r.tasks_resolved == 0 and r.role_id not in protected)

    candidates = sorted((r for r in keep if prunable(r)),
                        key=lambda r: (stats[r.role_id].mean_delta_u, -stats[r.role_id].uses, r.role_id))
    for r in candidates:
        if len(keep) <= floor:
            break
        remaining = [x for x in keep if x.role_id != r.role_id]
        if not any(x.can("probe") for x in remaining) or not any(x.can("synthesis") for x in remaining):
            continue
        keep = remaining
        s = stats[r.role_id]
        notes.append(f"pruned {r.role_id} ({s.uses} uses this generation, mean dU {s.mean_delta_u:+.3f})")
    return keep, notes


def evolve_role_library(library: Sequence[RoleAllele], stats: Mapping[str, RoleStatsSummary],
                        policy: Optional[CEOPolicyGene], generation: int,
                        promoted: Optional[Sequence[RoleAllele]] = None,
                        cap: int = MAX_ROLE_LIBRARY,
                        summarise_updates: bool = False) -> Tuple[List[RoleAllele], List[str]]:
    """Statistics update -> prune -> promote -> cap. Pure: returns copies and notes.

    With `summarise_updates` the per-role statistics notes collapse into one
    line ("statistics updated for N role(s)"); the breeder uses that so a
    genome's mutation history does not grow by a library's length per
    generation. Prune, promote and cap notes are always itemised.
    """
    lib, notes = update_role_statistics(library, stats)
    if summarise_updates and notes:
        notes = [f"statistics updated for {len(notes)} role(s)"]
    lib, pruned = prune_roles(lib, stats, policy)
    notes += pruned
    existing = {r.role_id for r in lib}
    adds = [r for r in (promoted if promoted is not None else promoted_roles(stats, generation, existing))
            if r.role_id not in existing]
    for r in adds:
        notes.append(f"promoted {r.role_id} ({r.origin}; {r.uses} uses, mean dU {r.mean_delta_u:+.3f}, "
                     f"support rate {r.support_rate:.2f}, resolved {r.tasks_resolved})")
    before = [r.role_id for r in lib] + [r.role_id for r in adds]
    lib = cap_library(lib + adds, policy, cap)
    lost = [rid for rid in before if rid not in {r.role_id for r in lib}]
    if lost:
        notes.append(f"library capped at {cap}: dropped {lost}")
    return lib, notes


def crossover_role_library(a: Sequence[RoleAllele], b: Sequence[RoleAllele], rng: random.Random,
                           policy: Optional[CEOPolicyGene] = None, cap: int = MAX_ROLE_LIBRARY
                           ) -> List[RoleAllele]:
    """Union of two libraries de-duplicated by role id, capped at `cap` by score.

    When both parents carry the same role id the allele with more evidence
    (`uses`) wins; on a tie `rng` picks, so the choice is reproducible from
    the breeder's seed.
    """
    merged: Dict[str, RoleAllele] = {}
    for r in a:
        merged[r.role_id] = r.copy()
    for r in b:
        cur = merged.get(r.role_id)
        if cur is None:
            merged[r.role_id] = r.copy()
        elif r.uses > cur.uses or (r.uses == cur.uses and rng.random() < 0.5):
            merged[r.role_id] = r.copy()
    return cap_library(list(merged.values()), policy, cap)


def crossover_ceo_policy(a: Optional[CEOPolicyGene], b: Optional[CEOPolicyGene],
                         rng: random.Random) -> CEOPolicyGene:
    """Uniform crossover, gene by gene. `enabled` is inherited from parent `a`.

    `enabled` is a cohort-level experimental control (the same rule
    `crossover_epistemic_policy` applies), not a trait to recombine.
    """
    pa = a if a is not None else CEOPolicyGene()
    pb = b if b is not None else CEOPolicyGene()
    da, db = pa.to_dict(), pb.to_dict()
    child: Dict[str, Any] = {}
    for name in CEO_NUMERIC_FIELDS + ("recruit_mode",):
        child[name] = da[name] if rng.random() < 0.5 else db[name]
    _repair_ceo_ordering(child)
    child["enabled"] = pa.enabled
    for k, v in pa.extra.items():
        child.setdefault(k, v)
    return CEOPolicyGene.from_dict(child)


def mutate_ceo_policy(gene: Optional[CEOPolicyGene], rng: random.Random, strength: float = 0.15,
                      mode_flip_probability: float = 0.08) -> Tuple[CEOPolicyGene, List[str]]:
    """A perturbed copy: one to three numeric genes jittered within `CEO_POLICY_BOUNDS`,
    `recruit_mode` flipped with low probability. Never flips `enabled`.
    Returns the gene and one note per change for the mutation history."""
    base = gene if gene is not None else CEOPolicyGene()
    data = base.to_dict()
    notes: List[str] = []
    for name in rng.sample(CEO_NUMERIC_FIELDS, rng.randint(1, 3)):
        lo, hi, is_int = CEO_POLICY_BOUNDS[name]
        step = rng.gauss(0.0, strength * float(hi - lo))
        if is_int and abs(step) < 1.0:
            step = 1.0 if step >= 0 else -1.0
        new = _clamp_ceo(name, float(data[name]) + step)
        if new != data[name]:
            notes.append(f"ceo_policy.{name} {data[name]} -> {new}")
            data[name] = new
    if rng.random() < mode_flip_probability:
        new_mode = rng.choice([m for m in RECRUIT_MODES if m != data["recruit_mode"]])
        notes.append(f"ceo_policy.recruit_mode {data['recruit_mode']} -> {new_mode}")
        data["recruit_mode"] = new_mode
    notes += _repair_ceo_ordering(data)
    data["enabled"] = base.enabled
    return CEOPolicyGene.from_dict(data), notes


def mutate_role_library(library: Sequence[RoleAllele], rng: random.Random, tag_pool: Iterable[str] = (),
                        generation: int = 0) -> Tuple[List[RoleAllele], List[str]]:
    """ONE role gets a domain tag swapped or added -- the LLM-free text mutation.

    The tag is drawn from `tag_pool`, which the breeder fills with the
    generation's unmatched modules and task tags (what the firms met and no
    role covered). When the pool is empty (the first V8 generation, bred from
    V6 results) the pool is the union of the library's own tags, so tags
    recombine between roles. Returns copies and a note saying what changed.
    """
    if not library:
        return [], []
    roles = [r.copy() for r in library]
    pool = normalise_tags(tag_pool)
    source = "generation"
    if not pool:
        pool = sorted({t for r in roles for t in r.domain_tags})
        source = "library"
    idx = rng.randrange(len(roles))
    role = roles[idx]
    candidates = [t for t in pool if t not in role.domain_tags]
    if not candidates:
        return roles, [f"role tag mutation skipped for {role.role_id}: no new tag in the {source} pool"]
    new_tag = rng.choice(candidates)
    if role.domain_tags and rng.random() < 0.5:
        old = rng.choice(role.domain_tags)
        role.domain_tags = normalise_tags([new_tag if t == old else t for t in role.domain_tags])
        note = f"{role.role_id}: tag {old!r} -> {new_tag!r} ({source} pool)"
    else:
        role.domain_tags = normalise_tags(list(role.domain_tags) + [new_tag])
        note = f"{role.role_id}: +tag {new_tag!r} ({source} pool)"
    return roles, [note]
