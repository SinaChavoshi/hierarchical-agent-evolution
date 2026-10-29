"""V16: bind the role library and CEO policy to the hierarchical SWE-bench harness.

This module is infrastructure, not part of any evolvable candidate: it is copied into every
candidate worktree (`hae.evolution.workspace.INFRA_SYNC_FILES`) and attaches to the engine
from the outside, so the evolvable harness files carry no org code and V15 patches still apply.

Modes (`epistemic_policy.v16_org`, a string or `{"mode": ...}`):
  off      exactly V15: `attach` is never called, no role text reaches any prompt.
  static   at task start the CEO staffs each layer once (argmax `role_score` per layer).
  dynamic  static staffing, plus a per-branch executor choice and recruitment on stall.

Hook points (all LLM traffic of the hierarchical engine goes through `_call_specialist_turn`):
  `L0_*`                     architect (seed contract + branches)
  `L2_BranchExecutor_<nid>_<locus>`  executor (GEN episode)
  `L2_DeltaRefinement_<nid>` refiner (CONT episode)
  `L1_AdversarialFalsifier`  falsifier
The recon layer is deterministic in the V15 harness (no LLM call), so recon roles are inert.

A generalist role (`is_generalist=True`) adds no text: it *is* the V15 prompt. A specialist role
appends one "Organization assignment" block (title, focus, playbook, failure modes) to the end of
the system message of that specialist's calls. Sampling temperatures are not changed.
"""

from __future__ import annotations

import math
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from hae.genome.schema import CEOPolicyGene, RoleAllele

ORG_MODES = ("off", "static", "dynamic")
HARNESS_LAYERS = ("recon", "architect", "executor", "refiner", "falsifier")
LLM_LAYERS = ("architect", "executor", "refiner", "falsifier")
BRANCH_LOCI = ("consumer_local", "producer_upstream", "codebase_symmetry", "counterfactual_move37")

_PREFIXES: Tuple[Tuple[str, str], ...] = (
    ("L0_", "architect"),
    ("L2_BranchExecutor_", "executor"),
    ("L2_DeltaRefinement_", "refiner"),
    ("L1_AdversarialFalsifier", "falsifier"),
)


def org_mode_from_policy(pol_extra: Optional[Mapping[str, Any]]) -> str:
    raw = (pol_extra or {}).get("v16_org")
    if isinstance(raw, Mapping):
        raw = raw.get("mode")
    mode = str(raw or "off").strip().lower()
    return mode if mode in ORG_MODES else "off"


def layer_of(specialist_name: str) -> str:
    for prefix, layer in _PREFIXES:
        if specialist_name.startswith(prefix):
            return layer
    return ""


def parse_specialist(specialist_name: str) -> Tuple[str, str, str]:
    """`(layer, node_id, locus)` for a specialist name ('' where not applicable)."""
    layer = layer_of(specialist_name)
    if layer == "executor":
        rest = specialist_name[len("L2_BranchExecutor_"):]
        nid, _, locus = rest.partition("_")
        return layer, nid, locus
    if layer == "refiner":
        return layer, specialist_name[len("L2_DeltaRefinement_"):], ""
    return layer, "", ""


def render_persona(role: RoleAllele) -> str:
    """Text appended to a specialist's system prompt. Empty for generalists (= V15 prompt)."""
    if role.is_generalist:
        return ""
    lines = ["", "", "## Organization assignment",
             f"For this episode the CEO has staffed you as **{role.name}**."]
    if role.goal:
        lines.append(f"Focus: {role.goal}")
    if role.backstory:
        lines.append(f"Background: {role.backstory}")
    if role.playbook:
        lines.append("Your playbook (follow it in order, adapting to the evidence):")
        lines += [f"{i}. {step}" for i, step in enumerate(role.playbook, 1)]
    if role.failure_modes:
        lines.append("Failure modes you guard against: " + ", ".join(m.replace("_", " ") for m in role.failure_modes) + ".")
    lines.append("All rules, tools and output formats above still apply.")
    return "\n".join(lines)


def _overlap(a: Sequence[str], b: Sequence[str]) -> float:
    sa, sb = {x.lower() for x in a if x}, {x.lower() for x in b if x}
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / float(min(len(sa), len(sb)))


class HarnessOrg:
    """Staffing, routing, recruitment and credit for one task run."""

    def __init__(self, library: Sequence[RoleAllele], policy: CEOPolicyGene, mode: str,
                 task_tags: Sequence[str], logger: Callable[[str], None] = print) -> None:
        if mode not in ("static", "dynamic"):
            raise ValueError(f"HarnessOrg needs mode static|dynamic, got {mode!r}")
        self.mode = mode
        self.policy = policy
        self.library = [r for r in library if r.layer in HARNESS_LAYERS]
        self.task_tags = [str(t).lower() for t in task_tags]
        self.logger = logger
        self.total_uses = sum(r.uses for r in self.library)
        self.team: Dict[str, RoleAllele] = {}
        self.staffing_scores: Dict[str, Dict[str, float]] = {}
        self.node_role: Dict[str, str] = {}
        self.episodes: List[Dict[str, Any]] = []
        self.swaps: List[Dict[str, Any]] = []
        self.calls: Dict[str, Dict[str, int]] = {}
        self.credits: Dict[str, Dict[str, float]] = {}
        self.stall_counter = 0
        self.last_recruit_episode = -10 ** 9
        self.unstaffed_calls: Dict[str, int] = {}
        self._staff()

    # ------------------------------------------------------------------ #
    def by_id(self, role_id: str) -> Optional[RoleAllele]:
        for r in self.library:
            if r.role_id == role_id:
                return r
        return None

    def layer_roles(self, layer: str) -> List[RoleAllele]:
        return [r for r in self.library if r.layer == layer]

    def generalist(self, layer: str) -> Optional[RoleAllele]:
        for r in self.layer_roles(layer):
            if r.is_generalist:
                return r
        return None

    def score(self, role: RoleAllele, extra_tags: Sequence[str] = ()) -> float:
        """`role_score`: quality x (0.5 + 0.5 overlap) + UCB bonus; overlap also counts failure modes."""
        tags = list(self.task_tags) + [t.lower() for t in extra_tags]
        overlap = _overlap(list(role.domain_tags) + list(role.failure_modes), tags)
        q = role.quality(self.policy.optimistic_prior) * (0.5 + 0.5 * overlap)
        bonus = self.policy.exploration_c * math.sqrt(math.log(self.total_uses + 1.0) / (role.uses + 1.0))
        return q + bonus

    def best_for(self, layer: str, extra_tags: Sequence[str] = (), exclude: Sequence[str] = ()) -> Optional[RoleAllele]:
        """Best role for `layer`. A specialist must have tag overlap and beat the generalist."""
        roles = [r for r in self.layer_roles(layer) if r.role_id not in exclude]
        if not roles:
            return None
        tags = list(self.task_tags) + [t.lower() for t in extra_tags]
        scored = sorted(((self.score(r, extra_tags), r) for r in roles), key=lambda t: (-t[0], t[1].role_id))
        gen = self.generalist(layer)
        gen_score = self.score(gen, extra_tags) if gen is not None and gen.role_id not in exclude else -1.0
        for s, r in scored:
            if r.is_generalist:
                return r
            if _overlap(list(r.domain_tags) + list(r.failure_modes), tags) > 0 and s > gen_score:
                return r
        return gen if (gen is not None and gen.role_id not in exclude) else scored[0][1]

    def _staff(self) -> None:
        for layer in HARNESS_LAYERS:
            role = self.best_for(layer)
            if role is not None:
                self.team[layer] = role
                self.staffing_scores[layer] = {r.role_id: round(self.score(r), 4) for r in self.layer_roles(layer)}

    # ------------------------------------------------------------------ #
    def role_for_call(self, specialist_name: str) -> Optional[RoleAllele]:
        layer, nid, locus = parse_specialist(specialist_name)
        if not layer:
            return None
        if layer == "executor" and nid:
            if nid not in self.node_role:
                role = self.team.get(layer)
                # Dynamic: choose per branch, unless a stall swap has just replaced the executor.
                swapped = any(s["layer"] == "executor" for s in self.swaps)
                if self.mode == "dynamic" and locus and not swapped:
                    role = self.best_for(layer, extra_tags=[locus]) or role
                if role is not None:
                    self.node_role[nid] = role.role_id
            return self.by_id(self.node_role.get(nid, ""))
        if layer == "refiner" and nid:
            if nid not in self.node_role and self.team.get(layer) is not None:
                self.node_role[nid] = self.team[layer].role_id
            return self.by_id(self.node_role.get(nid, ""))
        return self.team.get(layer)

    def inject(self, specialist_name: str, messages: List[Dict[str, str]]) -> List[Dict[str, str]]:
        role = self.role_for_call(specialist_name)
        chars = sum(len(str(m.get("content") or "")) for m in messages)
        if role is None:
            self.unstaffed_calls[specialist_name] = self.unstaffed_calls.get(specialist_name, 0) + 1
            return messages
        st = self.calls.setdefault(role.role_id, {"calls": 0, "prompt_chars": 0})
        st["calls"] += 1
        st["prompt_chars"] += chars
        persona = render_persona(role)
        if not persona or not messages or messages[0].get("role") != "system":
            return messages
        first = dict(messages[0])
        first["content"] = str(first.get("content") or "") + persona
        return [first] + list(messages[1:])

    # ------------------------------------------------------------------ #
    def credit(self, role_id: str, delta_v: float) -> None:
        c = self.credits.setdefault(role_id, {"episodes": 0, "sum_delta_v": 0.0})
        c["episodes"] += 1
        c["sum_delta_v"] = round(c["sum_delta_v"] + float(delta_v), 4)

    def after_episode(self, node_id: str, parent_id: str, parent_value: float, node: Any, episode_idx: int) -> None:
        value = float(getattr(node, "value", 0.0) or 0.0)
        base = 0.0 if parent_id == "root" else float(parent_value)
        delta_v = value - base
        role_id = self.node_role.get(node_id, "")
        layer = "refiner" if parent_id != "root" else "executor"
        if role_id:
            self.credit(role_id, delta_v)
        fals = self.team.get("falsifier")
        falsifier_ok = getattr(node, "falsifier_ok", None)
        self.episodes.append({"episode": episode_idx, "node": node_id, "parent": parent_id, "layer": layer,
                              "role_id": role_id, "value": round(value, 4), "delta_v": round(delta_v, 4),
                              "falsifier_role_id": fals.role_id if fals else "", "falsifier_ok": falsifier_ok})
        if delta_v < self.policy.stall_delta_u:
            self.stall_counter += 1
        else:
            self.stall_counter = 0
        if self.mode == "dynamic":
            self._maybe_recruit(layer, episode_idx)

    def recruit_prior(self) -> float:
        p = self.policy
        x = p.recruit_bias + p.recruit_w_stall * self.stall_counter - p.recruit_w_headcount * len(self.team)
        return 1.0 / (1.0 + math.exp(-x)) if x >= -50 else 0.0

    def _maybe_recruit(self, layer: str, episode_idx: int) -> None:
        if self.stall_counter < self.policy.stall_moves:
            return
        if episode_idx - self.last_recruit_episode < self.policy.recruit_cooldown_moves:
            return
        prior = self.recruit_prior()
        if prior < 0.5:
            return
        # Swap the stalled layer's role (refiner for CONT stalls; executor for GEN stalls).
        current = self.team.get(layer)
        tried = {e["role_id"] for e in self.episodes if e["layer"] == layer}
        exclude = list(tried | ({current.role_id} if current else set()))
        cand = self.best_for(layer, exclude=exclude)
        if cand is None or (current is not None and cand.role_id == current.role_id):
            return
        self.team[layer] = cand
        self.swaps.append({"episode": episode_idx, "layer": layer, "from": current.role_id if current else "",
                           "to": cand.role_id, "reason": f"stall x{self.stall_counter}", "prior": round(prior, 4)})
        self.logger(f"[v16-org] episode {episode_idx}: stall x{self.stall_counter} -> {layer} role "
                    f"{current.name if current else '-'} -> {cand.name} (prior={prior:.2f})")
        self.last_recruit_episode = episode_idx
        self.stall_counter = 0

    # ------------------------------------------------------------------ #
    def attach(self, engine: Any) -> None:
        """Wrap the engine's LLM-call and node-evaluation methods (instance attributes only)."""
        org = self
        orig_call = getattr(engine, "_call_specialist_turn", None)
        orig_eval = getattr(engine, "evaluate_and_register_mcts_node", None)
        if orig_call is None or orig_eval is None:
            self.logger("[v16-org] engine lacks _call_specialist_turn/evaluate_and_register_mcts_node; org inactive")
            return

        def _call(role_name: str, messages: List[Dict[str, str]], *args: Any, **kwargs: Any):
            return orig_call(role_name, org.inject(role_name, messages), *args, **kwargs)

        def _eval(*args: Any, **kwargs: Any):
            names = ("node_id", "parent_id", "locus", "hypothesis_claim", "episode_idx", "run_falsifier_if_gate_ok")
            bound = dict(zip(names, args))
            bound.update(kwargs)
            parent_id = str(bound.get("parent_id", "root"))
            parent = getattr(engine, "mcts_nodes", {}).get(parent_id)
            parent_value = float(getattr(parent, "value", 0.0) or 0.0)
            node = orig_eval(*args, **kwargs)
            try:
                org.after_episode(str(bound.get("node_id", "")), parent_id, parent_value, node,
                                  int(bound.get("episode_idx", 0) or 0))
            except Exception as exc:  # never let bookkeeping break a run
                org.logger(f"[v16-org] credit error: {exc}")
            return node

        engine._call_specialist_turn = _call
        engine.evaluate_and_register_mcts_node = _eval
        self.logger("[v16-org] mode=%s team: %s" % (self.mode, ", ".join(
            f"{layer}={r.name}" for layer, r in self.team.items())))

    def summary(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "team": {layer: {"role_id": r.role_id, "name": r.name, "is_generalist": r.is_generalist}
                     for layer, r in self.team.items()},
            "staffing_scores": self.staffing_scores,
            "node_roles": dict(self.node_role),
            "episodes": list(self.episodes),
            "swaps": list(self.swaps),
            "credits": dict(self.credits),
            "calls": dict(self.calls),
            "unstaffed_calls": dict(self.unstaffed_calls),
        }


def build_harness_org(genome: Any, pol_extra: Optional[Mapping[str, Any]], problem_statement: str, repo: str,
                      logger: Callable[[str], None] = print) -> Optional[HarnessOrg]:
    """`HarnessOrg` for a company genome, or None when `org_mode` is off or the library has no layer roles."""
    mode = org_mode_from_policy(pol_extra)
    if mode == "off":
        return None
    from hae.epistemic.org import TaskFeatures

    library = list(getattr(genome, "role_library", None) or [])
    if not any(r.layer for r in library):
        logger("[v16-org] role library has no layer-bound roles; running as off")
        return None
    policy = getattr(genome, "ceo_policy", None) or CEOPolicyGene()
    feats = TaskFeatures.from_problem_statement(problem_statement or "", repo="")
    return HarnessOrg(library, policy, mode, feats.tags, logger=logger)
