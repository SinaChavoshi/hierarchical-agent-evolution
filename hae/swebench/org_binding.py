"""V16/V17: bind the role library, CEO policy, and V17 Red-Team foundation to the hierarchical harness.

This module is infrastructure, not part of any evolvable candidate: it is copied into every
candidate worktree (`hae.evolution.workspace.INFRA_SYNC_FILES`) and attaches to the engine
from the outside, so the evolvable harness files carry no org code and V15 patches still apply.

Modes (`epistemic_policy.v16_org`, a string or `{"mode": ...}`):
  off      exactly V15 when `v17_redteam` is absent; when `v17_redteam` is present, runs generalist
           prompts with V17 red-team enforcement.
  static   at task start the CEO staffs each layer once (argmax `role_score` per layer).
  dynamic  static staffing, plus a per-branch executor choice and recruitment on stall.

V17 Red-Team Foundation (`epistemic_policy.v17_redteam`, a dict of genes from `REDTEAM_SPACE`):
  * `panel_size` (1..3): independent red-team panelists run in turn; patch accepted only if all PASS.
  * `no_edit` (bool): red team is strictly read/test-only on repository files (cannot self-edit & certify).
  * `lock_repro_test` (bool): locks `/testbed/.hae/reproduce_issue.py` once verified failing on HEAD.
  * `skeptic_default` (bool): defaults to FAIL unless explicit `VERDICT: PASS` AND executed bash test evidence;
    injects log-calibrated anti-patterns against superficial approval.
  * `evidence_tests` (bool): runs up to 6 discovered repo test files for touched modules.
  * `continuous_build` (bool): prioritizes continuous CONT refinement on compiling branches over HEAD resets.
  * `max_turns` (6..20): turn budget per red-team panelist.
"""

from __future__ import annotations

import copy
import hashlib
import math
import random
import re
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from hae.genome.schema import CEOPolicyGene, RoleAllele
from hae.swebench.harness_knobs import KnobSpec

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

# --------------------------------------------------------------------------- #
# V17 Red-Team Gene Space
# --------------------------------------------------------------------------- #

REDTEAM_SPACE: Dict[str, KnobSpec] = {
    spec.name: spec
    for spec in (
        KnobSpec("panel_size", "int", 1, 1, 3,
                 "Independent red-team panelists run in sequence; all must PASS."),
        KnobSpec("no_edit", "bool", True,
                 description="Forbid red team from editing repository source files."),
        KnobSpec("lock_repro_test", "bool", True,
                 description="Lock .hae/reproduce_issue.py once verified failing on HEAD."),
        KnobSpec("skeptic_default", "bool", True,
                 description="Require explicit VERDICT: PASS plus executed bash test evidence; inject calibrated QA rules."),
        KnobSpec("evidence_tests", "bool", True,
                 description="Run up to 6 discovered repository test files for modified modules."),
        KnobSpec("continuous_build", "bool", False,
                 description="Prefer continuous CONT refinement over resetting to HEAD across episodes."),
        KnobSpec("max_turns", "int", 10, 6, 20,
                 "Turn budget per red-team falsifier panelist."),
    )
}

V17_DEFAULT_REDTEAM: Dict[str, Any] = {name: spec.default for name, spec in REDTEAM_SPACE.items()}

V17_PERMISSIVE_REDTEAM: Dict[str, Any] = {
    "panel_size": 1,
    "no_edit": False,
    "lock_repro_test": False,
    "skeptic_default": False,
    "evidence_tests": False,
    "continuous_build": False,
    "max_turns": 10,
}

V17_STRICT_REDTEAM: Dict[str, Any] = {
    "panel_size": 2,
    "no_edit": True,
    "lock_repro_test": True,
    "skeptic_default": True,
    "evidence_tests": True,
    "continuous_build": False,
    "max_turns": 12,
}

# Log-calibrated anti-patterns distilled from V16 false-PASS divergence audit.
CALIBRATED_SKEPTIC_PROMPT = (
    "\n\n## V17 Calibrated Red-Team QA Protocol (Mandatory)\n"
    "In prior audits, 69% of PASS verdicts were FALSE PASSES because the auditor tested superficially or "
    "identified a discrepancy and rationalized it away as minor.\n"
    "1. **Read/Test-Only Separation**: You are strictly an independent evaluator. NEVER edit repository source files "
    "(`str_replace`/`write_file` on repo files are blocked). If anything is wrong or incomplete, return `VERDICT: FAIL: <reason>`.\n"
    "2. **Never Rationalize Discrepancies**: If you notice a missing edge case, a sibling method/class that was not updated, "
    "a subtly wrong warning/exception type, or a return type mismatch, DO NOT approve the patch.\n"
    "3. **Mandatory Executed Evidence**: Before emitting `VERDICT: PASS`, you MUST execute at least one `bash` command that runs:\n"
    "   - `/testbed/.hae/reproduce_issue.py` AND a new adversarial test script `/testbed/.hae/falsification_suite.py` probing "
    "boundary values (`None`, empty inputs, subclasses, combined kwargs, round-trip invariants);\n"
    "   - `pytest` on the repository's existing test file(s) covering every modified module.\n"
    "4. **Default is FAIL**: If you cannot verify every requirement with executed test output, emit `VERDICT: FAIL: <what failed or could not be verified>`."
)


def resolve_redteam(overrides: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """Merge `overrides` onto `V17_DEFAULT_REDTEAM`, clipping each gene to its range."""
    cfg = dict(V17_DEFAULT_REDTEAM)
    for k, v in dict(overrides or {}).items():
        spec = REDTEAM_SPACE.get(k)
        if spec is not None:
            cfg[k] = spec.clip(v)
    return cfg


def sample_redteam(rng: random.Random) -> Dict[str, Any]:
    return resolve_redteam({name: spec.sample(rng) for name, spec in REDTEAM_SPACE.items()})


def mutate_redteam(cfg: Mapping[str, Any], rng: random.Random, n_genes: int = 2) -> Tuple[Dict[str, Any], str]:
    base = resolve_redteam(cfg)
    chosen = rng.sample(sorted(REDTEAM_SPACE), k=min(n_genes, len(REDTEAM_SPACE)))
    notes = []
    for name in chosen:
        base[name] = REDTEAM_SPACE[name].mutate(base[name], rng, strength=0.3)
        notes.append(f"{name}={base[name]}")
    return resolve_redteam(base), "redteam_mutate " + ", ".join(notes)


def crossover_redteam(a: Mapping[str, Any], b: Mapping[str, Any], rng: random.Random) -> Dict[str, Any]:
    ra, rb = resolve_redteam(a), resolve_redteam(b)
    return resolve_redteam({name: (ra if rng.random() < 0.5 else rb)[name] for name in REDTEAM_SPACE})


def org_mode_from_policy(pol_extra: Optional[Mapping[str, Any]]) -> str:
    raw = (pol_extra or {}).get("v16_org")
    if isinstance(raw, Mapping):
        raw = raw.get("mode")
    mode = str(raw or "off").strip().lower()
    return mode if mode in ORG_MODES else "off"


def redteam_from_policy(pol_extra: Optional[Mapping[str, Any]]) -> Optional[Dict[str, Any]]:
    raw = (pol_extra or {}).get("v17_redteam")
    if isinstance(raw, Mapping) and raw:
        return resolve_redteam(raw)
    return None


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
    """Staffing, routing, recruitment, credit, and V17 red-team enforcement for one task run."""

    def __init__(self, library: Sequence[RoleAllele], policy: CEOPolicyGene, mode: str,
                 task_tags: Sequence[str], logger: Callable[[str], None] = print,
                 redteam: Optional[Mapping[str, Any]] = None) -> None:
        self.has_redteam = bool(redteam)
        self.redteam: Dict[str, Any] = resolve_redteam(redteam) if redteam else {}
        if mode not in ("static", "dynamic") and not self.has_redteam:
            raise ValueError(f"HarnessOrg needs mode static|dynamic, got {mode!r}")
        self.mode = mode if mode in ORG_MODES else "off"
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
        # V17 red-team state
        self.verdicts: List[Dict[str, Any]] = []
        self.edit_blocks_refused: int = 0
        self.repro_restorations: int = 0
        self._locked_repro: Optional[str] = None
        self._in_falsifier: bool = False
        self._active_falsifier_role: Optional[RoleAllele] = None
        self._falsifier_bash_count: int = 0
        self._falsifier_cp: Optional[Any] = None
        self._last_falsifier_text: str = ""
        self._current_node_id: str = ""
        self._current_episode_idx: int = 0
        if self.mode in ("static", "dynamic") and self.library:
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
        """`role_score`: quality x (0.5 + 0.5 overlap) + UCB bonus.
        In V17 (`has_redteam`), falsifier roles are scored by measured verdict accuracy (UCB)
        without keyword-overlap discounting, because adversarial QA is task-independent."""
        bonus = self.policy.exploration_c * math.sqrt(math.log(self.total_uses + 1.0) / (role.uses + 1.0))
        if self.has_redteam and role.layer == "falsifier":
            return role.quality(self.policy.optimistic_prior) + bonus
        tags = list(self.task_tags) + [t.lower() for t in extra_tags]
        overlap = _overlap(list(role.domain_tags) + list(role.failure_modes), tags)
        q = role.quality(self.policy.optimistic_prior) * (0.5 + 0.5 * overlap)
        return q + bonus

    def best_for(self, layer: str, extra_tags: Sequence[str] = (), exclude: Sequence[str] = ()) -> Optional[RoleAllele]:
        """Best role for `layer`. A specialist must have tag overlap and beat the generalist,
        except for V17 `falsifier` where the CEO picks purely by UCB verdict accuracy."""
        roles = [r for r in self.layer_roles(layer) if r.role_id not in exclude]
        if not roles:
            return None
        scored = sorted(((self.score(r, extra_tags), r) for r in roles), key=lambda t: (-t[0], t[1].role_id))
        if self.has_redteam and layer == "falsifier":
            return scored[0][1]
        tags = list(self.task_tags) + [t.lower() for t in extra_tags]
        gen = self.generalist(layer)
        gen_score = self.score(gen, extra_tags) if gen is not None and gen.role_id not in exclude else -1.0
        for s, r in scored:
            if r.is_generalist:
                return r
            if _overlap(list(r.domain_tags) + list(r.failure_modes), tags) > 0 and s > gen_score:
                return r
        return gen if (gen is not None and gen.role_id not in exclude) else scored[0][1]

    def panel_roles(self, k: int) -> List[Optional[RoleAllele]]:
        """Top `k` red-team roles for a multi-member falsifier panel (V17)."""
        k = max(1, int(k))
        if self.mode == "off" or not self.layer_roles("falsifier"):
            return [None] * k
        roles = self.layer_roles("falsifier")
        scored = [r for _, r in sorted(((self.score(r), r) for r in roles), key=lambda t: (-t[0], t[1].role_id))]
        primary = self.team.get("falsifier")
        if primary is not None:
            scored = [primary] + [r for r in scored if r.role_id != primary.role_id]
        out: List[Optional[RoleAllele]] = []
        for i in range(k):
            out.append(scored[i % len(scored)])
        return out

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
        if layer == "falsifier" and self._active_falsifier_role is not None:
            return self._active_falsifier_role
        if self.mode == "off":
            return None
        if layer == "executor" and nid:
            if nid not in self.node_role:
                role = self.team.get(layer)
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
        layer = layer_of(specialist_name)
        role = self.role_for_call(specialist_name)
        chars = sum(len(str(m.get("content") or "")) for m in messages)
        if role is None:
            if self.mode != "off":
                self.unstaffed_calls[specialist_name] = self.unstaffed_calls.get(specialist_name, 0) + 1
        else:
            st = self.calls.setdefault(role.role_id, {"calls": 0, "prompt_chars": 0})
            st["calls"] += 1
            st["prompt_chars"] += chars
        persona = render_persona(role) if role is not None else ""
        extra_sys = ""
        if layer == "falsifier" and self.has_redteam and self.redteam.get("skeptic_default"):
            extra_sys = CALIBRATED_SKEPTIC_PROMPT
        if (not persona and not extra_sys) or not messages or messages[0].get("role") != "system":
            return messages
        first = dict(messages[0])
        first["content"] = str(first.get("content") or "") + persona + extra_sys
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
    def _run_expanded_evidence_tests(self, engine: Any, cp: Any) -> Tuple[bool, str]:
        """V17 `evidence_tests`: run up to 6 discovered repository test files for modified modules."""
        discover = getattr(engine, "_discover_targeted_test_files", None)
        if discover is None or cp is None:
            return True, ""
        try:
            tfiles = list(discover(cp) or [])
        except Exception:
            return True, ""
        extra_files = tfiles[2:6]
        if not extra_files:
            return True, ""
        res_pt = engine.executor.run(
            [
                engine.executor.python, "-m", "pytest", "-q", "--tb=short",
                "--color=no", "--disable-warnings", "-p", "no:randomly",
                *extra_files,
            ],
            cwd=engine.executor.root,
            env={"PYTHONPATH": engine.executor.root},
            timeout_s=45,
        )
        pt_raw = ((res_pt.stdout or "") + "\n" + (res_pt.stderr or "")).strip()
        if (
            res_pt.rc != 0
            and res_pt.rc not in (5, 139, -11)
            and "No module named pytest" not in pt_raw
        ):
            chk_base = getattr(engine, "_check_only_baseline_test_failures", None)
            if chk_base is not None:
                try:
                    only_base, _, _ = chk_base(extra_files, pt_raw, getattr(cp, "file_contents", {}))
                    if only_base:
                        return True, ""
                except Exception:
                    pass
            return False, f"Expanded evidence_tests pytest ({' '.join(extra_files)}) FAILED (exit {res_pt.rc}):\n{pt_raw[-1200:]}"
        return True, ""

    def attach(self, engine: Any) -> None:
        """Wrap the engine's LLM-call, node-evaluation, and (in V17) red-team & reproduction hooks."""
        org = self
        orig_call = getattr(engine, "_call_specialist_turn", None)
        orig_eval = getattr(engine, "evaluate_and_register_mcts_node", None)
        if orig_call is None or orig_eval is None:
            self.logger("[v16-org] engine lacks _call_specialist_turn/evaluate_and_register_mcts_node; org inactive")
            return

        def _call(role_name: str, messages: List[Dict[str, str]], *args: Any, **kwargs: Any):
            res = orig_call(role_name, org.inject(role_name, messages), *args, **kwargs)
            if org._in_falsifier and isinstance(res, tuple) and len(res) >= 1:
                org._last_falsifier_text = str(res[0] or "")
            return res

        def _eval(*args: Any, **kwargs: Any):
            names = ("node_id", "parent_id", "locus", "hypothesis_claim", "episode_idx", "run_falsifier_if_gate_ok")
            bound = dict(zip(names, args))
            bound.update(kwargs)
            parent_id = str(bound.get("parent_id", "root"))
            org._current_node_id = str(bound.get("node_id", ""))
            org._current_episode_idx = int(bound.get("episode_idx", 0) or 0)
            parent = getattr(engine, "mcts_nodes", {}).get(parent_id)
            parent_value = float(getattr(parent, "value", 0.0) or 0.0)
            node = orig_eval(*args, **kwargs)
            try:
                org.after_episode(org._current_node_id, parent_id, parent_value, node, org._current_episode_idx)
            except Exception as exc:  # never let bookkeeping break a run
                org.logger(f"[v16-org] credit error: {exc}")
            return node

        engine._call_specialist_turn = _call
        engine.evaluate_and_register_mcts_node = _eval

        if self.has_redteam:
            self._attach_redteam_hooks(engine)

        if self.mode != "off":
            self.logger("[v16-org] mode=%s team: %s" % (self.mode, ", ".join(
                f"{layer}={r.name}" for layer, r in self.team.items())))
        if self.has_redteam:
            self.logger(f"[v17-redteam] active genes: {self.redteam}")

    def _attach_redteam_hooks(self, engine: Any) -> None:
        org = self
        orig_recon = getattr(engine, "run_recon_episode", None)
        orig_verify = getattr(engine, "verify_current_workspace", None)
        orig_falsifier = getattr(engine, "run_falsifier_episode", None)
        orig_sr = getattr(engine, "execute_str_replace_tool", None)
        orig_wf = getattr(engine, "execute_write_file_tool", None)
        orig_bash = getattr(engine, "execute_bash_tool", None)
        orig_select = getattr(engine, "select_next_mcts_operation", None)

        # 1. Lock reproduction script once verified failing on HEAD.
        if orig_recon is not None:
            def _recon(*args: Any, **kwargs: Any):
                brief = orig_recon(*args, **kwargs)
                if org.redteam.get("lock_repro_test") and getattr(brief, "repro_fails_on_head", False):
                    try:
                        rel = getattr(engine, "repro_script_rel", ".hae/reproduce_issue.py")
                        if engine.executor.exists(rel):
                            org._locked_repro = engine.executor.read_file(rel)
                    except Exception:
                        pass
                return brief
            engine.run_recon_episode = _recon

        if orig_verify is not None:
            def _verify(*args: Any, **kwargs: Any):
                rel = getattr(engine, "repro_script_rel", ".hae/reproduce_issue.py")
                if org.redteam.get("lock_repro_test") and org._locked_repro:
                    try:
                        curr = engine.executor.read_file(rel) if engine.executor.exists(rel) else ""
                        if curr != org._locked_repro:
                            engine.executor.write_file(rel, org._locked_repro)
                            org.repro_restorations += 1
                    except Exception:
                        pass
                res = orig_verify(*args, **kwargs)
                if org.redteam.get("lock_repro_test") and not org._locked_repro:
                    try:
                        cache = getattr(engine, "_repro_head_fails_cache", {}) or {}
                        if any(cache.values()) and engine.executor.exists(rel):
                            org._locked_repro = engine.executor.read_file(rel)
                    except Exception:
                        pass
                return res
            engine.verify_current_workspace = _verify

        # 2. Enforce no_edit and bash tracking during falsifier turns.
        def _is_scratch(path: str) -> bool:
            rel = str(path or "").strip()
            while rel.startswith("./"):
                rel = rel[2:]
            return rel.startswith(".hae/") or "/.hae/" in ("/" + rel)

        if orig_sr is not None:
            def _sr(path: str, old_str: str, new_str: str, *args: Any, **kwargs: Any):
                rel = str(path or "").strip()
                if org._in_falsifier and org.redteam.get("no_edit") and not _is_scratch(rel):
                    org.edit_blocks_refused += 1
                    return (
                        "Observation (refused by V17 Red-Team no_edit policy): As the independent Red-Team Falsifier, "
                        "you are strictly forbidden from modifying repository source files. Write reproduction or "
                        "adversarial tests in `/testbed/.hae/falsification_suite.py` via `bash`, and emit `submit` "
                        "with `VERDICT: FAIL: <exact failing case>` so the builder fixes it."
                    )
                if org.redteam.get("lock_repro_test") and org._locked_repro and rel.endswith("reproduce_issue.py"):
                    org.repro_restorations += 1
                    return (
                        "Observation (refused by V17 lock_repro_test policy): `/testbed/.hae/reproduce_issue.py` "
                        "has already been verified failing on HEAD and is locked. Fix the repository code instead "
                        "of weakening or editing the reproduction test."
                    )
                return orig_sr(path, old_str, new_str, *args, **kwargs)
            engine.execute_str_replace_tool = _sr

        if orig_wf is not None:
            def _wf(path: str, content: str, *args: Any, **kwargs: Any):
                rel = str(path or "").strip()
                if org._in_falsifier and org.redteam.get("no_edit") and not _is_scratch(rel):
                    org.edit_blocks_refused += 1
                    return (
                        "Observation (refused by V17 Red-Team no_edit policy): As the independent Red-Team Falsifier, "
                        "you are strictly forbidden from modifying repository source files."
                    )
                if org.redteam.get("lock_repro_test") and org._locked_repro and rel.endswith("reproduce_issue.py"):
                    org.repro_restorations += 1
                    return (
                        "Observation (refused by V17 lock_repro_test policy): `/testbed/.hae/reproduce_issue.py` "
                        "is locked and cannot be overwritten."
                    )
                return orig_wf(path, content, *args, **kwargs)
            engine.execute_write_file_tool = _wf

        if orig_bash is not None:
            def _bash(command: str, *args: Any, **kwargs: Any):
                if org._in_falsifier:
                    org._falsifier_bash_count += 1
                obs = orig_bash(command, *args, **kwargs)
                if org._in_falsifier and org.redteam.get("no_edit") and org._falsifier_cp is not None:
                    try:
                        curr_diff, _, _ = engine._current_diff_and_files()
                        if curr_diff != org._falsifier_cp.diff:
                            engine._restore_checkpoint(org._falsifier_cp)
                            org.edit_blocks_refused += 1
                    except Exception:
                        pass
                return obs
            engine.execute_bash_tool = _bash

        # 3. Wrap run_falsifier_episode for panel_size, max_turns, skeptic_default, evidence_tests, and verdict logging.
        if orig_falsifier is not None:
            def _falsifier(cp: Any, max_turns: int = 8) -> Tuple[bool, str]:
                turns = int(org.redteam.get("max_turns") or max_turns)
                panel_k = max(1, int(org.redteam.get("panel_size") or 1))
                panelists = org.panel_roles(panel_k)
                panel_results: List[Tuple[str, bool, str]] = []
                final_ok = True
                final_msg = ""
                snap_cp = copy.deepcopy(cp)

                for idx, role in enumerate(panelists, 1):
                    org._active_falsifier_role = role
                    org._in_falsifier = True
                    org._falsifier_bash_count = 0
                    org._last_falsifier_text = ""
                    org._falsifier_cp = copy.deepcopy(snap_cp)
                    try:
                        ok, msg = orig_falsifier(cp, max_turns=turns)
                    finally:
                        org._in_falsifier = False
                        org._active_falsifier_role = None

                    # Enforce no_edit post-check: never keep a falsifier code edit.
                    if org.redteam.get("no_edit") and getattr(cp, "diff", "") != snap_cp.diff:
                        try:
                            cp.diff = snap_cp.diff
                            cp.changed_files = list(snap_cp.changed_files)
                            cp.file_contents = dict(snap_cp.file_contents)
                            cp.created_files = list(snap_cp.created_files)
                            engine._restore_checkpoint(snap_cp)
                        except Exception:
                            pass

                    # Enforce skeptic_default: require explicit VERDICT: PASS and executed test evidence.
                    if ok and org.redteam.get("skeptic_default"):
                        has_explicit_pass = bool(
                            re.search(r"VERDICT\s*:\s*PASS", msg or "", flags=re.I)
                            or re.search(r"VERDICT\s*:\s*PASS", org._last_falsifier_text or "", flags=re.I)
                        )
                        has_fail = bool(
                            re.search(r"VERDICT\s*:\s*FAIL", msg or "", flags=re.I)
                            or re.search(r"VERDICT\s*:\s*FAIL", org._last_falsifier_text or "", flags=re.I)
                        )
                        suite_rel = getattr(engine, "falsification_script_rel", ".hae/falsification_suite.py")
                        ran_evidence = org._falsifier_bash_count > 0 or engine.executor.exists(suite_rel)
                        if has_fail or not has_explicit_pass or not ran_evidence:
                            ok = False
                            reason = (
                                "did not execute any bash verification test"
                                if not ran_evidence
                                else "did not emit an explicit VERDICT: PASS before turn limit"
                            )
                            msg = f"Adversarial Falsifier rejected patch (skeptic_default: {reason}). {msg}"

                    rid = role.role_id if role is not None else "generalist_falsifier"
                    panel_results.append((rid, bool(ok), str(msg)))
                    final_ok = bool(ok)
                    final_msg = f"[Panel {idx}/{panel_k} ({role.name if role else 'Generalist'})] {msg}"
                    if not ok:
                        break

                if final_ok and org.redteam.get("evidence_tests"):
                    ev_ok, ev_msg = org._run_expanded_evidence_tests(engine, cp)
                    if not ev_ok:
                        final_ok = False
                        final_msg = ev_msg
                        if panel_results:
                            last_rid, _, _ = panel_results[-1]
                            panel_results[-1] = (last_rid, False, ev_msg)

                diff_str = str(getattr(cp, "diff", "") or "")
                diff_sha = hashlib.sha1(diff_str.encode("utf-8", errors="replace")).hexdigest()[:12]
                org.verdicts.append({
                    "episode": org._current_episode_idx,
                    "node": org._current_node_id,
                    "diff_sha": diff_sha,
                    "diff": diff_str,
                    "panel": [{"role_id": rid, "passed": bool(p_ok), "critique": crit[:400]}
                              for rid, p_ok, crit in panel_results],
                    "falsifier_ok": bool(final_ok),
                })
                return final_ok, final_msg

            engine.run_falsifier_episode = _falsifier

        # 4. Optional continuous_build: prefer refining compiling uncertified nodes over resetting to HEAD.
        if orig_select is not None and org.redteam.get("continuous_build"):
            def _select(candidate_modules: List[str]):
                nodes = getattr(engine, "mcts_nodes", {}) or {}
                cont_cands = [
                    n for nid, n in nodes.items()
                    if nid != "root"
                    and getattr(n, "checkpoint", None) is not None
                    and n.checkpoint.diff.strip()
                    and n.checkpoint.compile_ok
                    and not getattr(n, "is_certified_global_optimum", False)
                    and getattr(n, "stagnant_steps", 0) < 3
                    and len(getattr(n, "children", [])) < 4
                ]
                if cont_cands:
                    best = max(cont_cands, key=lambda n: (n.value, n.episode_index))
                    return "CONT", best.node_id, None
                return orig_select(candidate_modules)
            engine.select_next_mcts_operation = _select

    def summary(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
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
        if self.has_redteam:
            out["redteam"] = dict(self.redteam)
            out["verdicts"] = [
                {k: v for k, v in vd.items() if k != "diff"} | {"diff_chars": len(vd.get("diff") or "")}
                for vd in self.verdicts
            ]
            out["verdict_patches"] = {
                vd["diff_sha"]: vd["diff"] for vd in self.verdicts if vd.get("diff_sha") and vd.get("diff")
            }
            out["edit_blocks_refused"] = self.edit_blocks_refused
            out["repro_restorations"] = self.repro_restorations
        return out


def build_harness_org(genome: Any, pol_extra: Optional[Mapping[str, Any]], problem_statement: str, repo: str,
                      logger: Callable[[str], None] = print) -> Optional[HarnessOrg]:
    """`HarnessOrg` for a company genome, or None when `org_mode` is off and `v17_redteam` is absent."""
    mode = org_mode_from_policy(pol_extra)
    redteam = redteam_from_policy(pol_extra)
    if mode == "off" and not redteam:
        return None
    from hae.epistemic.org import TaskFeatures

    library = list(getattr(genome, "role_library", None) or [])
    if mode != "off" and not any(r.layer for r in library):
        logger("[v16-org] role library has no layer-bound roles; running as off")
        if not redteam:
            return None
        mode = "off"
    policy = getattr(genome, "ceo_policy", None) or CEOPolicyGene()
    feats = TaskFeatures.from_problem_statement(problem_statement or "", repo="")
    return HarnessOrg(library, policy, mode, feats.tags, logger=logger, redteam=redteam)
