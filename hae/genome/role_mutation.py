"""LLM revision of underperforming role alleles between generations (V8, roadmap
section 1.4 item 3: "guide LLM allelic mutation of role descriptions").

Until this module existed the only text mutation a `RoleAllele` received at
the generation boundary was one domain tag swapped or added
(`hae.genome.mutator.mutate_role_library`); a role's `goal` and `backstory`
could change only when a brand-new persona was synthesised mid-run by
`MOVE_RECRUIT_SPECIALIST`. An external audit flagged that gap: the roles that
keep proposing mechanisms the gatekeeper falsifies are exactly the ones whose
*description* should move, and nothing moved it.

What happens here
-----------------
* `RoleEvidence` is what one generation's trajectories say about one role in
  words rather than numbers: the claims it proposed that were FALSIFIED, the
  probes the gatekeeper refused, the questions it was routed to, the modules
  it met and the modules no active role covered, how many of its syntheses
  were reverted, and the few claims that were SUPPORTED (so a rewrite keeps
  what worked). The breeder distils it from result records
  (`hae.orchestration.breeder.distill_role_evidence`), tolerating the absence
  of every key -- a V6 record carries no `role_id` anywhere and yields nothing.
* `build_role_revision_prompt` shows the role as it is, its numbers, and that
  evidence, and asks for a rewrite under which the specialist would NOT have
  proposed the falsified mechanisms and WOULD cover the unmatched modules.
* `ROLE_REVISION_SCHEMA` is the grammar the constrained decoder enforces on
  the reply (same envelope as `V8_RECRUIT_SCHEMA`); `parse_role_revision` is
  the tolerant parser (fences, prose, string tags, unknown kind all survive;
  a missing or empty `goal` does not).
* `revise_role` makes the call and builds a NEW allele: `role_id`
  `<parent>__g<generation>`, origin `mutated:<parent>:g<generation>`, lifetime
  statistics reset to zero, `extra` carrying `parent_role_id`,
  `revision_rationale` and `revision_source`. It never raises: an exception,
  an unparseable reply or a reply that leaves goal and backstory unchanged
  comes back as `(None, reason)` and the caller falls back to the tag mutation.

Selection (which role is revised) and wiring into the breeder live in
`hae.genome.mutator.select_roles_for_revision` / `mutate_role_text`; the
generation spec chooses the mode (`GenerationSpec.role_text_mutation`, default
`tags`, i.e. this module is off unless a spec asks for `llm`).

Determinism and the record
--------------------------
Every other V8 operator is reproducible from the spec and its seeds. An LLM
reply is not: the same prompt at temperature 0.7 yields a different persona
each time, and `hae.infra.llm.call_llm` exposes no seed parameter (a seed is
drawn from the breeder's rng and passed only to a callable whose signature
declares one). So for `llm` mode the population JSON the breeder writes IS the
record: the revised goal, backstory, tags and rationale are in the child's
`role_library` and the one-line note in its `mutation_history`, and a run is
reproduced from that file (`HAE_REUSE_EXISTING_POPULATION=1`), not re-bred
from the spec. Nothing here has been exercised against a live model; the
behaviour is unit-tested with a fake LLM only.
"""

from __future__ import annotations

import hashlib
import inspect
import random
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

# `extract_json_object` and `normalise_recruit_tags` are the tolerant readers
# every other packet goes through; two parsers that drift would be worse than
# one cross-layer import. `hae.epistemic.moves` imports only the ledger, so
# this is a one-way edge (precedent: `hae.genome.role_seeds` -> `hae.epistemic.org`).
from hae.epistemic.moves import extract_json_object, normalise_recruit_tags
from hae.genome.schema import ROLE_KINDS, RoleAllele, normalise_tags
from hae.infra.llm import call_llm

# Caps on what one role's evidence carries into the prompt. The prompt must
# stay a few hundred tokens: the point is the pattern of failure, not the log.
MAX_FALSIFIED_CLAIMS = 8
MAX_REFUSED_PROBE_REASONS = 5
MAX_QUESTIONS_SEEN = 5
MAX_SUPPORTED_CLAIMS = 3
MAX_MODULES = 8
CLAIM_CHARS = 200

# Schema limits (also enforced by the parser, which truncates rather than rejects).
MAX_NAME_CHARS = 60
MAX_GOAL_CHARS = 400
MAX_BACKSTORY_CHARS = 600
MAX_RATIONALE_CHARS = 300
MIN_REVISION_TAGS = 3
MAX_REVISION_TAGS = 8

REVISION_TEMPERATURE = 0.7
REVISION_MODEL_TIER = "executive"
REVISION_SYSTEM_INSTRUCTION = (
    "You are the chief executive of a software-repair organisation revising ONE specialist's job "
    "description from the evidence of the last generation. Reply with the ROLE_REVISION JSON packet only.")

ROLE_REVISION_SCHEMA: Dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "RoleRevisionPacket",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "packet": {"type": "string", "enum": ["ROLE_REVISION"]},
                "name": {"type": "string", "maxLength": MAX_NAME_CHARS},
                "goal": {"type": "string", "maxLength": MAX_GOAL_CHARS},
                "backstory": {"type": "string", "maxLength": MAX_BACKSTORY_CHARS},
                "domain_tags": {
                    "type": "array",
                    "minItems": MIN_REVISION_TAGS,
                    "maxItems": MAX_REVISION_TAGS,
                    "items": {"type": "string", "maxLength": 40},
                },
                "kind": {"type": "string", "enum": list(ROLE_KINDS)},
                "rationale": {"type": "string", "maxLength": MAX_RATIONALE_CHARS},
            },
            "required": ["packet", "name", "goal", "backstory", "domain_tags", "kind", "rationale"],
            "additionalProperties": False,
        },
    },
}


def _clip(text: Any, limit: int = CLAIM_CHARS) -> str:
    return " ".join(str(text or "").split())[:limit]


# --------------------------------------------------------------------------- #
# Evidence
# --------------------------------------------------------------------------- #

@dataclass
class RoleEvidence:
    """What one generation's trajectories say about one role, in words.

    Every list is de-duplicated, capped (see the `MAX_*` constants) and each
    entry clipped to `CLAIM_CHARS`. Filled by the breeder's
    `distill_role_evidence`; read by `build_role_revision_prompt`.
    """

    role_id: str
    falsified_claims: List[str] = field(default_factory=list)
    refused_probe_reasons: List[str] = field(default_factory=list)
    questions_seen: List[str] = field(default_factory=list)
    modules_seen: List[str] = field(default_factory=list)
    unmatched_modules: List[str] = field(default_factory=list)
    syntheses_reverted: int = 0
    supported_claims: List[str] = field(default_factory=list)

    _CAPS = {
        "falsified_claims": MAX_FALSIFIED_CLAIMS,
        "refused_probe_reasons": MAX_REFUSED_PROBE_REASONS,
        "questions_seen": MAX_QUESTIONS_SEEN,
        "modules_seen": MAX_MODULES,
        "unmatched_modules": MAX_MODULES,
        "supported_claims": MAX_SUPPORTED_CLAIMS,
    }

    def add(self, name: str, text: Any) -> bool:
        """Appends a clipped, de-duplicated entry to list `name`; False when dropped (empty, seen, or at cap)."""
        cap = self._CAPS[name]
        value = _clip(text)
        bucket: List[str] = getattr(self, name)
        if not value or value in bucket or len(bucket) >= cap:
            return False
        bucket.append(value)
        return True

    def is_empty(self) -> bool:
        return not (self.falsified_claims or self.refused_probe_reasons or self.questions_seen
                    or self.modules_seen or self.unmatched_modules or self.syntheses_reverted
                    or self.supported_claims)

    def to_dict(self) -> Dict[str, Any]:
        return {"role_id": self.role_id, "falsified_claims": list(self.falsified_claims),
                "refused_probe_reasons": list(self.refused_probe_reasons),
                "questions_seen": list(self.questions_seen), "modules_seen": list(self.modules_seen),
                "unmatched_modules": list(self.unmatched_modules), "syntheses_reverted": self.syntheses_reverted,
                "supported_claims": list(self.supported_claims)}


# --------------------------------------------------------------------------- #
# Packet
# --------------------------------------------------------------------------- #

@dataclass
class RoleRevision:
    """A parsed ROLE_REVISION packet. Empty `name`/`backstory`/`kind`/`domain_tags` mean "keep the parent's"."""

    goal: str
    name: str = ""
    backstory: str = ""
    domain_tags: List[str] = field(default_factory=list)
    kind: str = ""
    rationale: str = ""


def parse_role_revision(raw: str) -> Optional[RoleRevision]:
    """Parses a ROLE_REVISION packet; None when there is no usable `goal`.

    Tolerant like `parse_recruit_packet`: a bare JSON object, a fenced one or
    one wrapped in prose all parse; over-long fields are truncated to the
    schema limits; tags may arrive as a list or a comma-separated string; an
    unknown or missing `kind` is coerced to "" (`revise_role` keeps the
    parent's kind). Strict about one thing: a revision without a goal is not
    a revision.
    """
    data = extract_json_object(raw or "")
    if not data:
        return None
    goal = _clip(data.get("goal"), MAX_GOAL_CHARS)
    if not goal:
        return None
    kind = str(data.get("kind", "") or "").strip().lower()
    if kind not in ROLE_KINDS:
        kind = ""
    return RoleRevision(
        goal=goal,
        name=_clip(data.get("name"), MAX_NAME_CHARS),
        backstory=_clip(data.get("backstory"), MAX_BACKSTORY_CHARS),
        domain_tags=normalise_recruit_tags(data.get("domain_tags"))[:MAX_REVISION_TAGS],
        kind=kind,
        rationale=_clip(data.get("rationale"), MAX_RATIONALE_CHARS),
    )


# --------------------------------------------------------------------------- #
# Prompt
# --------------------------------------------------------------------------- #

def _bullets(items: Sequence[str], empty: str) -> str:
    return "\n".join(f"  - {x}" for x in items) if items else f"  ({empty})"


def build_role_revision_prompt(role: RoleAllele, stats: Optional[Any], evidence: Optional[RoleEvidence],
                               generation: int) -> str:
    """Plain-text prompt: the role as it is, its numbers, the evidence, the instruction.

    `stats` is the generation's `hae.genome.mutator.RoleStatsSummary` for the
    role (read by attribute, so None or any object with the same fields works);
    the lifetime numbers come from the allele itself. The numbers are
    end-of-move credit (org.py "Honesty notes") and the prompt says so.
    """
    ev = evidence if evidence is not None else RoleEvidence(role_id=role.role_id)
    lines: List[str] = [
        f"ROLE REVISION (breeding generation {int(generation)}): rewrite ONE specialist role so that it stops "
        "proposing what the evidence falsified and starts covering what nobody covered.",
        "",
        "CURRENT ROLE",
        f"  role_id: {role.role_id}",
        f"  name: {role.name}",
        f"  kind: {role.kind}",
        f"  domain_tags: {', '.join(role.domain_tags) or '(none)'}",
        f"  goal: {role.goal or '(empty)'}",
        f"  backstory: {role.backstory or '(empty)'}",
        "",
        "RECORD (end-of-move credit: the role holding the move when the gatekeeper ruled)",
        f"  lifetime: {role.uses} uses, mean dU per move {role.mean_delta_u:+.3f}, support rate "
        f"{role.support_rate:.2f}, tasks resolved {role.tasks_resolved}",
    ]
    if stats is not None:
        lines.append(
            f"  last generation: {int(getattr(stats, 'uses', 0) or 0)} moves, dU "
            f"{float(getattr(stats, 'cumulative_delta_u', 0.0) or 0.0):+.3f}, supported "
            f"{int(getattr(stats, 'supported', 0) or 0)}, falsified {int(getattr(stats, 'falsified', 0) or 0)}, "
            f"untestable {int(getattr(stats, 'untestable', 0) or 0)}, syntheses written "
            f"{int(getattr(stats, 'syntheses_written', 0) or 0)}, across {int(getattr(stats, 'firms', 0) or 0)} firm(s)")
    else:
        lines.append("  last generation: no per-role statistics recorded")
    lines += [
        "",
        "CLAIMS THIS ROLE PROPOSED THAT WERE FALSIFIED (the probe ran and the prediction failed):",
        _bullets(ev.falsified_claims, "none recorded"),
        "PROBES THE GATEKEEPER REFUSED / UNTESTABLE:",
        _bullets(ev.refused_probe_reasons, "none recorded"),
        "CLAIMS THAT WERE SUPPORTED (keep whatever expertise produced these):",
        _bullets(ev.supported_claims, "none recorded"),
        "QUESTIONS THE ROLE WAS ROUTED TO:",
        _bullets(ev.questions_seen, "none recorded"),
        f"MODULES SEEN: {', '.join(ev.modules_seen) or '(none recorded)'}",
        f"MODULES NO ACTIVE ROLE COVERED (unmatched): {', '.join(ev.unmatched_modules) or '(none recorded)'}",
        f"SYNTHESES BY THIS ROLE THAT BROKE THE MODULE AND WERE REVERTED: {ev.syntheses_reverted}",
        "",
        "INSTRUCTIONS",
        "Reply with ONE ROLE_REVISION packet (JSON object) with these fields:",
        f"  name: the job title; keep it unless the expertise itself changes (<= {MAX_NAME_CHARS} chars).",
        f"  goal: one or two sentences on what this specialist is accountable for, written so that a specialist "
        f"with this goal would NOT have proposed the falsified mechanisms above and WOULD bring expertise for "
        f"the unmatched modules (<= {MAX_GOAL_CHARS} chars).",
        f"  backstory: 2-3 sentences of concrete professional experience that explain the new goal; name the "
        f"techniques, subsystems and failure classes the evidence shows were missing (<= {MAX_BACKSTORY_CHARS} chars).",
        f"  domain_tags: {MIN_REVISION_TAGS}-{MAX_REVISION_TAGS} lower-case tokens the role is matched against "
        "(module or package stems, exception classes, techniques); include stems of the unmatched modules.",
        "  kind: `probe` (proposes and tests hypotheses), `synthesis` (writes repairs) or `both`.",
        f"  rationale: what changed and why, in one or two sentences (<= {MAX_RATIONALE_CHARS} chars).",
        "Do not restate the evidence; change the persona. Do not exceed the limits.",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Revision
# --------------------------------------------------------------------------- #

def _accepts_seed(fn: Callable[..., Any]) -> bool:
    """Whether `fn` declares an explicit `seed` parameter (`**kwargs` does not count)."""
    try:
        return "seed" in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False


def revise_role(role: RoleAllele, stats: Optional[Any], evidence: Optional[RoleEvidence], generation: int,
                rng: Optional[random.Random], llm: Optional[Callable[..., str]] = None,
                model: Optional[str] = None, max_tokens: int = 700,
                existing_ids: Iterable[str] = ()) -> Tuple[Optional[RoleAllele], str]:
    """Asks the LLM for a revision of `role` and returns `(new_allele, note)` or `(None, reason)`.

    `llm` defaults to `hae.infra.llm.call_llm`, looked up at call time so a
    test can patch `hae.genome.role_mutation.call_llm`. The call passes the
    prompt, `temperature=0.7`, the executive tier (or `model`),
    `response_format=ROLE_REVISION_SCHEMA` and `max_tokens`; a `seed` drawn
    from `rng` is passed only when the callable declares one (`call_llm`
    does not). The draw happens regardless, so the rng stream downstream does
    not depend on which callable was used.

    The new allele's id is `<parent>__g<generation>` (plus a 6-hex-char hash
    of the new goal if that id is already in `existing_ids`), its origin
    `mutated:<parent>:g<generation>`, its lifetime statistics zero, and its
    `extra` the parent's plus `parent_role_id`, `revision_rationale`,
    `revision_source="llm"`. Fields the packet left empty (`name`,
    `backstory`, `kind`, `domain_tags`) keep the parent's values. A reply
    whose goal AND backstory equal the parent's is not a revision. Never
    raises.
    """
    try:
        seed = rng.randrange(2 ** 31) if rng is not None else None
        fn = llm if llm is not None else call_llm
        prompt = build_role_revision_prompt(role, stats, evidence, generation)
        kwargs: Dict[str, Any] = dict(temperature=REVISION_TEMPERATURE, model_tier=REVISION_MODEL_TIER,
                                      system_instruction=REVISION_SYSTEM_INSTRUCTION,
                                      response_format=ROLE_REVISION_SCHEMA, max_tokens=max_tokens)
        if model:
            kwargs["model_name"] = model
        if seed is not None and _accepts_seed(fn):
            kwargs["seed"] = seed
        raw = fn(prompt, **kwargs)
        raw = raw if isinstance(raw, str) else str(raw or "")
        packet = parse_role_revision(raw)
        if packet is None:
            return None, f"no usable ROLE_REVISION packet ({len(raw)} chars; head: {raw[:80]!r})"
        name = packet.name or role.name
        goal = packet.goal
        backstory = packet.backstory or role.backstory
        kind = packet.kind or role.kind
        tags = normalise_tags(packet.domain_tags) or list(role.domain_tags)
        if (goal == _clip(role.goal, MAX_GOAL_CHARS)
                and _clip(backstory, MAX_BACKSTORY_CHARS) == _clip(role.backstory, MAX_BACKSTORY_CHARS)):
            return None, "revision left goal and backstory unchanged"
        new_id = f"{role.role_id}__g{int(generation)}"
        if new_id in set(existing_ids):
            new_id = f"{new_id}_{hashlib.sha256(goal.encode('utf-8')).hexdigest()[:6]}"
        extra = dict(role.extra or {})
        extra.update(parent_role_id=role.role_id, revision_rationale=packet.rationale, revision_source="llm")
        new = RoleAllele(
            role_id=new_id, name=name, goal=goal, backstory=backstory, domain_tags=tags, kind=kind,
            model_tier=role.model_tier, temperature=role.temperature, tools_enabled=role.tools_enabled,
            cost_per_move=role.cost_per_move, uses=0, mean_delta_u=0.0, support_rate=0.0, tasks_resolved=0,
            origin=f"mutated:{role.role_id}:g{int(generation)}", created_generation=int(generation), extra=extra)
        return new, f"revised: {packet.rationale[:140] or 'no rationale given'}"
    except Exception as exc:  # the operator must never be able to abort a breed
        return None, f"revision failed: {type(exc).__name__}: {exc}"
