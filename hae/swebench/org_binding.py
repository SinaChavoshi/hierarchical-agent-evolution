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
import json
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
    ("L2_SynthesisRoundtable", "executor"),
    ("L2_BuilderHuddle_", "executor"),
    ("L2_DeltaRefinement_", "refiner"),
    ("L1_AdversarialFalsifier", "falsifier"),
)

# --------------------------------------------------------------------------- #
# V17 Red-Team & V18 Collaborative Gene Space
# --------------------------------------------------------------------------- #

COLLAB_SPACE: Dict[str, KnobSpec] = {
    spec.name: spec
    for spec in (
        KnobSpec("sub_blackboards_on", "bool", False,
                 description="Route entries into 4 scoped sub-blackboards (#recon-map, #builders-room, #redteam-chamber, #verdicts-and-repros)."),
        KnobSpec("entry_char_limit", "int", 320, 320, 2400,
                 "Max characters per blackboard entry (320 in V17, 1800 in V18)."),
        KnobSpec("auto_index_views", "bool", False,
                 description="Automatically index viewed/edited file ranges and top def/class headers into #recon-map."),
        KnobSpec("pre_huddle_rounds", "int", 0, 0, 2,
                 "Pre-Edit Builder Huddle rounds in #builders-room with contract-override authority."),
        KnobSpec("synthesis_roundtable", "bool", False,
                 description="Convene a Post-Prototype Synthesis Roundtable when 2+ distinct candidate patches exist."),
        KnobSpec("pool_ranker", "bool", False,
                 description="Retain all repro+pytest-passing patches in a candidate pool and rank continuously via Red-Team Ranker."),
        KnobSpec("ranker_falsifier_weight", "float", 0.45, 0.1, 0.9,
                 "Weight of Red-Team panelist pass fraction in continuous candidate-pool ranking."),
        KnobSpec("ranker_simplicity_weight", "float", 0.15, 0.0, 0.5,
                 "Weight of diff parsimony (1 / (1 + diff_lines/80)) in continuous candidate-pool ranking."),
        KnobSpec("ranker_synthesis_bonus", "float", 0.08, 0.0, 0.3,
                 "Tie-break bonus for a verified hybrid synthesis patch (`b_synth`) that passes all checks."),
    )
}

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
        KnobSpec("blackboard_on", "bool", True,
                 description="Maintain shared /testbed/.hae/blackboard.jsonl with typed findings, ruled_out, and decisions."),
        KnobSpec("max_questions", "int", 2, 0, 6,
                 "Maximum directed cross-role questions answered per task."),
        KnobSpec("answer_turns", "int", 2, 1, 4,
                 "Maximum turns when waking a role to answer a directed question."),
        KnobSpec("falsifier_shares_tests", "bool", True,
                 description="Post executable failing test cases from the falsifier to the blackboard on rejection."),
        *COLLAB_SPACE.values(),
    )
}

V17_DEFAULT_REDTEAM: Dict[str, Any] = {name: spec.default for name, spec in REDTEAM_SPACE.items()}

V17_PERMISSIVE_REDTEAM: Dict[str, Any] = {
    **V17_DEFAULT_REDTEAM,
    "panel_size": 1,
    "no_edit": False,
    "lock_repro_test": False,
    "skeptic_default": False,
    "evidence_tests": False,
    "continuous_build": False,
    "max_turns": 10,
    "blackboard_on": False,
    "max_questions": 0,
    "answer_turns": 2,
    "falsifier_shares_tests": False,
}

V17_STRICT_REDTEAM: Dict[str, Any] = {
    **V17_DEFAULT_REDTEAM,
    "panel_size": 2,
    "no_edit": True,
    "lock_repro_test": True,
    "skeptic_default": True,
    "evidence_tests": True,
    "continuous_build": False,
    "max_turns": 12,
    "blackboard_on": False,
    "max_questions": 0,
    "answer_turns": 2,
    "falsifier_shares_tests": False,
}

V17_BLACKBOARD_ONLY: Dict[str, Any] = {
    **V17_STRICT_REDTEAM,
    "blackboard_on": True,
    "falsifier_shares_tests": True,
    "max_questions": 0,
}

V17_BLACKBOARD_QUESTIONS: Dict[str, Any] = {
    **V17_STRICT_REDTEAM,
    "blackboard_on": True,
    "falsifier_shares_tests": True,
    "max_questions": 3,
    "answer_turns": 2,
}

V18_COLLAB_REDTEAM: Dict[str, Any] = {
    **V17_BLACKBOARD_QUESTIONS,
    "panel_size": 2,
    "max_turns": 10,
    "sub_blackboards_on": True,
    "entry_char_limit": 1800,
    "auto_index_views": True,
    "pre_huddle_rounds": 1,
    "synthesis_roundtable": True,
    "pool_ranker": True,
    "ranker_falsifier_weight": 0.45,
    "ranker_simplicity_weight": 0.15,
    "ranker_synthesis_bonus": 0.08,
}

BLACKBOARD_REL = ".hae/blackboard.jsonl"
SUB_BLACKBOARD_DIR_REL = ".hae/blackboards"
SUB_BLACKBOARDS = ("recon-map", "builders-room", "redteam-chamber", "verdicts-and-repros")
BLACKBOARD_TYPES = (
    "finding", "hypothesis", "ruled_out", "failing_case",
    "question", "answer", "decision", "design_note",
    "critique", "amendment", "synthesis",
)
FALSIFIER_VISIBLE_TYPES = ("finding", "ruled_out", "decision", "failing_case", "amendment")
MAX_BB_ENTRY_CHARS = 320
MAX_BB_ENTRIES = 48
_BB_BLOCK_RE = re.compile(r"```blackboard\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)


def infer_blackboard_channel(entry_type: str, author: str = "", to: str = "", explicit_channel: str = "") -> str:
    """Route a blackboard entry to one of the 4 scoped sub-blackboards."""
    ch = str(explicit_channel or "").strip().lower().lstrip("#")
    if ch in SUB_BLACKBOARDS:
        return ch
    et = str(entry_type or "").strip().lower()
    au = str(author or "").strip().lower()
    tg = str(to or "").strip().lower()
    if et == "finding" or au == "recon":
        return "recon-map"
    if et in ("failing_case", "ruled_out"):
        return "verdicts-and-repros"
    if et in ("question", "answer"):
        if tg == "falsifier" or au == "falsifier":
            return "verdicts-and-repros"
        if tg == "recon" or au == "recon":
            return "recon-map"
        return "builders-room"
    if au == "falsifier":
        return "redteam-chamber"
    return "builders-room"


def score_candidate_patch(entry: Mapping[str, Any], redteam: Optional[Mapping[str, Any]] = None) -> float:
    """Continuous Candidate-Pool Red-Team Ranker score for a candidate checkpoint."""
    cfg = redteam or V18_COLLAB_REDTEAM
    repro_ok = bool(entry.get("repro_ok"))
    repo_tests_ok = bool(entry.get("repo_tests_ok"))
    compile_ok = bool(entry.get("compile_ok", True))
    static_ok = bool(entry.get("static_ok", True))
    expanded_ok = bool(entry.get("expanded_tests_ok", True))
    new_fails = int(entry.get("new_test_failures") or 0)
    panel_pass_rate = float(entry.get("panel_pass_rate") or 0.0)
    ran_ev = float(entry.get("ran_evidence") or 0.0)
    diff_lines = max(1, int(entry.get("diff_lines") or 1))
    episode = max(1, int(entry.get("episode") or 1))
    is_synth = bool(entry.get("is_synthesis"))

    base_gate = (
        (1.5 if repro_ok else 0.0)
        + (1.5 if repo_tests_ok else 0.0)
        + (0.4 if (compile_ok and static_ok) else 0.0)
        + (0.3 if (expanded_ok and repo_tests_ok) else 0.0)
        - 0.25 * min(4, max(0, new_fails))
    )
    w_fals = float(cfg.get("ranker_falsifier_weight", 0.45))
    w_simp = float(cfg.get("ranker_simplicity_weight", 0.15))
    w_syn = float(cfg.get("ranker_synthesis_bonus", 0.08))
    # Only penalize excessive diff bloat (>100 lines) so complete multi-hunk fixes (10-60 lines)
    # are not penalized relative to incomplete 2-line symptom patches; reward later refined episodes.
    bloat_penalty = max(0.0, float(diff_lines - 100) / 100.0)
    simplicity = 1.0 / (1.0 + bloat_penalty)
    refinement_bonus = 0.02 * min(6, episode)
    fals_term = w_fals * (0.85 * panel_pass_rate + 0.15 * ran_ev)
    synth_term = w_syn if (is_synth and repro_ok and repo_tests_ok) else 0.0
    return round(base_gate + fals_term + w_simp * simplicity + refinement_bonus + synth_term, 5)

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
    if "blackboard_on" not in chosen and (
        ("max_questions" in chosen and int(base.get("max_questions") or 0) > 0)
        or ("falsifier_shares_tests" in chosen and bool(base.get("falsifier_shares_tests")))
    ):
        base["blackboard_on"] = True
        notes.append("blackboard_on=True")
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
        # V17/V18 red-team, scoped sub-blackboards, huddle, synthesis & candidate-pool ranker state
        self.verdicts: List[Dict[str, Any]] = []
        self.edit_blocks_refused: int = 0
        self.repro_restorations: int = 0
        self.blackboard: List[Dict[str, Any]] = []
        self._bb_dedup_keys: set = set()
        self.questions_asked: int = 0
        self.questions_answered: int = 0
        self.failing_cases_shared: int = 0
        self.candidate_shas: List[str] = []
        self.duplicate_patches: int = 0
        self.candidate_pool: List[Dict[str, Any]] = []
        self.huddle_notes: List[Dict[str, Any]] = []
        self.contract_overrides: int = 0
        self.synthesis_runs: int = 0
        self._synthesis_ran: bool = False
        self._in_synthesis: bool = False
        self._last_panel_results: List[Tuple[str, bool, str, bool]] = []
        self._last_expanded_ok: bool = True
        self._locked_repro: Optional[str] = None
        self._in_falsifier: bool = False
        self._in_question_answer: bool = False
        self._attempted_question_ids: set = set()
        self._active_falsifier_role: Optional[RoleAllele] = None
        self._falsifier_bash_count: int = 0
        self._falsifier_cp: Optional[Any] = None
        self._last_falsifier_text: str = ""
        self._current_node_id: str = ""
        self._current_episode_idx: int = 0
        self._engine_ref: Optional[Any] = None
        self._orig_call_ref: Optional[Callable] = None
        if self.mode in ("static", "dynamic") and self.library:
            self._staff()

    # ------------------------------------------------------------------ #
    # V17/V18 Structured Shared Blackboard, 4 Sub-Blackboards & Directed Q&A
    # ------------------------------------------------------------------ #
    def post_blackboard(self, entry_type: str, content: str, author: str = "controller",
                        to: str = "", ref_id: str = "", episode: Optional[int] = None,
                        channel: str = "") -> Optional[Dict[str, Any]]:
        """Append a typed, size-capped, deduplicated entry to the shared blackboard and flush to disk."""
        if not self.has_redteam or not self.redteam.get("blackboard_on"):
            return None
        etype = str(entry_type or "").strip().lower()
        if etype not in BLACKBOARD_TYPES:
            return None
        norm = re.sub(r"\s+", " ", str(content or "")).strip()
        if not norm:
            return None
        char_limit = int(self.redteam.get("entry_char_limit") or MAX_BB_ENTRY_CHARS)
        norm = norm[:max(MAX_BB_ENTRY_CHARS, char_limit)]
        target = str(to or "").strip().lower()
        resolved_channel = infer_blackboard_channel(etype, author=author, to=target, explicit_channel=channel)
        dedup_key = (resolved_channel, etype, target, norm[:180].lower())
        if dedup_key in self._bb_dedup_keys:
            return None
        self._bb_dedup_keys.add(dedup_key)
        entry = {
            "id": f"bb_{len(self.blackboard) + 1}",
            "channel": resolved_channel,
            "type": etype,
            "author": str(author or "controller").strip().lower(),
            "to": target,
            "ref_id": str(ref_id or "").strip(),
            "content": norm,
            "episode": self._current_episode_idx if episode is None else int(episode),
        }
        self.blackboard.append(entry)
        if len(self.blackboard) > MAX_BB_ENTRIES:
            # Preserve decisions, amendments, syntheses, findings, and failing_cases preferentially when evicting
            protected = ("decision", "amendment", "synthesis", "finding", "failing_case")
            for i, old in enumerate(self.blackboard):
                if old["type"] not in protected:
                    self.blackboard.pop(i)
                    break
            else:
                self.blackboard.pop(0)
        self._flush_blackboard_to_disk()
        return entry

    def _flush_blackboard_to_disk(self) -> None:
        eng = self._engine_ref
        if eng is None or not hasattr(eng, "executor"):
            return
        try:
            payload = "\n".join(json.dumps(e, ensure_ascii=False) for e in self.blackboard) + "\n"
            eng.executor.write_file(BLACKBOARD_REL, payload)
            if self.redteam.get("sub_blackboards_on"):
                for ch in SUB_BLACKBOARDS:
                    ch_entries = [e for e in self.blackboard if e.get("channel") == ch]
                    ch_payload = "\n".join(json.dumps(e, ensure_ascii=False) for e in ch_entries) + ("\n" if ch_entries else "")
                    eng.executor.write_file(f"{SUB_BLACKBOARD_DIR_REL}/{ch}.jsonl", ch_payload)
        except Exception:
            pass

    def sync_blackboard_from_disk(self) -> List[Dict[str, Any]]:
        """Ingest any JSONL entries appended directly to `/testbed/.hae/blackboard.jsonl` by an agent."""
        if not self.has_redteam or not self.redteam.get("blackboard_on"):
            return []
        eng = self._engine_ref
        if eng is None or not hasattr(eng, "executor"):
            return []
        new_questions: List[Dict[str, Any]] = []
        try:
            if not eng.executor.exists(BLACKBOARD_REL):
                return []
            raw = eng.executor.read_file(BLACKBOARD_REL)
            known_ids = {e["id"] for e in self.blackboard}
            for line in (raw or "").splitlines():
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                if not isinstance(obj, Mapping) or obj.get("id") in known_ids:
                    continue
                added = self.post_blackboard(
                    str(obj.get("type") or "finding"),
                    str(obj.get("content") or ""),
                    author=str(obj.get("author") or "agent"),
                    to=str(obj.get("to") or ""),
                    ref_id=str(obj.get("ref_id") or ""),
                    channel=str(obj.get("channel") or ""),
                )
                if added and added["type"] == "question":
                    new_questions.append(added)
        except Exception:
            pass
        return new_questions

    def ingest_blackboard_text(self, text: str, author: str) -> List[Dict[str, Any]]:
        """Parse ```blackboard ... ``` JSON blocks from a specialist response."""
        if not self.has_redteam or not self.redteam.get("blackboard_on") or not text:
            return []
        new_questions: List[Dict[str, Any]] = []
        for block in _BB_BLOCK_RE.findall(text):
            for line in block.splitlines():
                line = line.strip()
                if not line or not line.startswith("{"):
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if not isinstance(obj, Mapping):
                    continue
                added = self.post_blackboard(
                    str(obj.get("type") or "finding"),
                    str(obj.get("content") or ""),
                    author=author,
                    to=str(obj.get("to") or ""),
                    ref_id=str(obj.get("ref_id") or ""),
                    channel=str(obj.get("channel") or ""),
                )
                if added and added["type"] == "question":
                    new_questions.append(added)
        return new_questions

    def render_blackboard_for(self, specialist_name: str) -> str:
        """Compact blackboard digest injected at episode start; preserves falsifier independence."""
        if not self.has_redteam or not self.redteam.get("blackboard_on"):
            return ""
        layer = layer_of(specialist_name) or ("recon" if "Recon" in specialist_name else "agent")
        sub_on = bool(self.redteam.get("sub_blackboards_on"))
        visible: List[Dict[str, Any]] = []
        for e in self.blackboard:
            et = e["type"]
            ch = e.get("channel") or infer_blackboard_channel(et, e.get("author", ""), e.get("to", ""))
            if layer == "falsifier":
                # Falsifier sees ONLY objective facts (#recon-map), contract decisions/amendments,
                # #redteam-chamber notes, and #verdicts-and-repros failing cases/ruled_out/questions to falsifier.
                # NEVER builder hypotheses, design_notes, syntheses, or persuasion.
                if ch == "builders-room" and et not in ("decision", "amendment"):
                    continue
                if et in FALSIFIER_VISIBLE_TYPES or ch == "redteam-chamber" or (et in ("question", "answer") and (e.get("to") == "falsifier" or e.get("author") == "falsifier")):
                    visible.append(e)
            else:
                # Builders/Architect/Refiners never see internal #redteam-chamber notes, only published #verdicts-and-repros.
                if ch == "redteam-chamber":
                    continue
                if et == "failing_case" and not self.redteam.get("falsifier_shares_tests"):
                    continue
                visible.append(e)

        max_render_chars = 4200 if sub_on else 2600
        if not sub_on:
            lines = ["", "", "## Shared Blackboard (`/testbed/.hae/blackboard.jsonl`)"]
            if visible:
                for e in visible[-18:]:
                    tag = f"[{e['id']} {e['type'].upper()} by {e['author']}"
                    if e.get("to"):
                        tag += f" -> {e['to']}"
                    if e.get("ref_id"):
                        tag += f" re:{e['ref_id']}"
                    tag += "]"
                    lines.append(f"- {tag} {e['content']}")
            else:
                lines.append("- (No entries recorded yet.)")
        else:
            lines = ["", "", "## Scoped Team Sub-Blackboards (`/testbed/.hae/blackboards/*.jsonl`)"]
            allowed_channels = (
                ("recon-map", "redteam-chamber", "verdicts-and-repros", "builders-room")
                if layer == "falsifier"
                else ("recon-map", "builders-room", "verdicts-and-repros")
            )
            any_shown = False
            for ch in allowed_channels:
                ch_items = [e for e in visible if (e.get("channel") or "builders-room") == ch][-10:]
                if not ch_items:
                    continue
                any_shown = True
                lines.append(f"### #{ch}")
                for e in ch_items:
                    tag = f"[{e['id']} {e['type'].upper()} by {e['author']}"
                    if e.get("to"):
                        tag += f" -> {e['to']}"
                    if e.get("ref_id"):
                        tag += f" re:{e['ref_id']}"
                    tag += "]"
                    lines.append(f"- {tag} {e['content']}")
            if not any_shown:
                lines.append("- (No entries recorded yet.)")

        if layer == "falsifier":
            lines.append(
                "Blackboard rule (Falsifier Independence): Judge the candidate patch cold. "
                "Reply ONLY with executable failing cases (`failing_case` on `#verdicts-and-repros`), never opinions."
            )
        else:
            q_budget = max(0, int(self.redteam.get("max_questions") or 0) - self.questions_asked)
            ch_hint = '`"channel": "recon-map|builders-room|verdicts-and-repros", `' if sub_on else ""
            lines.append(
                "Blackboard rules: Do NOT re-grep files already documented in `#recon-map` (`FINDING`), do NOT repeat approaches "
                "in `RULED_OUT`, and ensure your patch passes every `FAILING_CASE`. You may emit a ` ```blackboard ` block "
                "alongside your tool block in the same turn: "
                f'`{{{ch_hint}"type": "finding|hypothesis|design_note|critique|amendment|ruled_out", "content": "..."}}`'
                + (
                    f' or ask a targeted question ({q_budget} remaining): '
                    '`{"type": "question", "to": "falsifier|recon|architect|executor", "content": "..."}`.'
                    if q_budget > 0 else "."
                )
            )
        return "\n".join(lines)[:max_render_chars]

    def answer_pending_questions(self) -> int:
        """Wake target roles in short (`answer_turns`) read-only episodes to answer directed questions."""
        if (
            not self.has_redteam
            or not self.redteam.get("blackboard_on")
            or self._in_question_answer
            or self._orig_call_ref is None
        ):
            return 0
        max_q = int(self.redteam.get("max_questions") or 0)
        if self.questions_asked >= max_q:
            return 0
        answered_refs = {e.get("ref_id") for e in self.blackboard if e.get("type") == "answer" and e.get("ref_id")}
        pending = [
            e for e in self.blackboard
            if e.get("type") == "question"
            and e.get("id") not in answered_refs
            and e.get("id") not in self._attempted_question_ids
        ]
        if not pending:
            return 0

        from hae.swebench.agentic import _strip_rfl_artifacts, parse_agent_action

        ans_turns = max(1, int(self.redteam.get("answer_turns") or 2))
        answered_now = 0
        eng = self._engine_ref
        for q in pending:
            if self.questions_asked >= max_q:
                break
            self._attempted_question_ids.add(str(q.get("id") or ""))
            self.questions_asked += 1
            target = str(q.get("to") or "falsifier").strip().lower()
            if target not in ("falsifier", "recon", "architect", "executor", "refiner"):
                target = "falsifier"
            spec_map = {
                "falsifier": "L1_AdversarialFalsifier",
                "recon": "L1_Reconnaissance",
                "architect": "L0_StrategicArchitect",
                "executor": "L2_BranchExecutor_b1_general",
                "refiner": "L2_DeltaRefinement_b1",
            }
            spec_name = spec_map[target]
            sys_prompt = (
                f"You are **{target.upper()}** woken for at most {ans_turns} turn(s) to answer a directed question "
                f"on the shared blackboard.\n"
                + (
                    "CRITICAL (Falsifier Independence): Reply ONLY with a concrete executable failing test case "
                    "(exact Python input, command, and expected vs actual output) that breaks the patch. "
                    "Do NOT offer opinions or design advice.\n"
                    if target == "falsifier"
                    else "Provide a concise, factual answer (file paths, line numbers, or concrete behavior).\n"
                )
                + "You may optionally run ONE `bash` or `view_file` block first, then give your final concise answer."
                + self.render_blackboard_for(spec_name)
            )
            msgs: List[Dict[str, str]] = [
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": f"Question [{q['id']}] from {q['author']}: {q['content']}"},
            ]
            self._in_question_answer = True
            prev_fals = self._in_falsifier
            if target == "falsifier":
                self._in_falsifier = True
            ans_text = ""
            last_tool_obs = ""
            try:
                for _t in range(ans_turns):
                    try:
                        resp = self._orig_call_ref(spec_name, msgs, temperature=0.1, enable_thinking=False)
                    except TypeError:
                        resp = self._orig_call_ref(spec_name, msgs, temperature=0.1)
                    txt = _strip_rfl_artifacts(str(resp[0] if isinstance(resp, tuple) else resp or "")).strip()
                    if not txt:
                        break
                    ans_text = txt
                    act = parse_agent_action(txt)
                    obs = ""
                    if act.kind == "bash" and act.command and eng is not None:
                        if hasattr(eng, "execute_bash_tool"):
                            try:
                                obs = eng.execute_bash_tool(act.command, timeout_s=30.0)
                            except TypeError:
                                obs = eng.execute_bash_tool(act.command)
                        elif hasattr(eng, "executor") and hasattr(eng.executor, "run_bash"):
                            res_b = eng.executor.run_bash(act.command, timeout_s=30)
                            obs = f"exit={res_b.rc}\n{(res_b.stdout or '')}\n{(res_b.stderr or '')}".strip()
                    elif act.kind == "view_file" and act.path and eng is not None and hasattr(eng, "execute_view_file_tool"):
                        obs = eng.execute_view_file_tool(act.path, act.start_line, act.end_line)
                    if obs:
                        last_tool_obs = f"[Executed {act.kind}: `{act.command or act.path}`] -> {str(obs)[:600]}"
                        if _t + 1 < ans_turns:
                            msgs.append({"role": "assistant", "content": txt})
                            msgs.append({"role": "user", "content": str(obs)[:1400] + "\nNow state your concise factual answer without emitting another tool block."})
                            continue
                    break
            except Exception:
                pass
            finally:
                self._in_falsifier = prev_fals
                self._in_question_answer = False

            if ans_text or last_tool_obs:
                clean = _strip_rfl_artifacts(ans_text)
                clean = re.sub(r"```(?:bash|sh|shell|view_file|blackboard|submit).*?```", "", clean, flags=re.DOTALL).strip()
                clean = re.sub(r"<tool_call>.*?(?:</tool_call>|\Z)", "", clean, flags=re.DOTALL | re.IGNORECASE).strip()
                if not clean and last_tool_obs:
                    clean = last_tool_obs
                elif last_tool_obs and len(clean) < 40:
                    clean = f"{clean} ({last_tool_obs})"
                elif not clean:
                    clean = _strip_rfl_artifacts(ans_text).strip()
                posted = self.post_blackboard(
                    "answer",
                    clean,
                    author=target,
                    to=str(q.get("author") or ""),
                    ref_id=str(q["id"]),
                    channel=str(q.get("channel") or ""),
                )
                if posted is not None:
                    self.questions_answered += 1
                    answered_now += 1
        return answered_now

    def duplicate_patch_rate(self) -> float:
        if not self.candidate_shas:
            return 0.0
        return round(self.duplicate_patches / float(len(self.candidate_shas)), 4)

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
        bb_sys = self.render_blackboard_for(specialist_name) if not self._in_question_answer else ""
        if (not persona and not extra_sys and not bb_sys) or not messages or messages[0].get("role") != "system":
            return messages
        first = dict(messages[0])
        first["content"] = str(first.get("content") or "") + persona + extra_sys + bb_sys
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
        if self.has_redteam and self.redteam.get("blackboard_on"):
            self.sync_blackboard_from_disk()
            locus = str(getattr(node, "locus", "") or "")
            claim = str(getattr(node, "hypothesis_claim", "") or "")[:160]
            if value < 0.5 or falsifier_ok is False:
                diag = str(getattr(node, "falsifier_critique", "") or "")[:140]
                self.post_blackboard(
                    "ruled_out",
                    f"Node {node_id} ({locus}) rejected (v={value:.2f}): {claim} | {diag}",
                    author=layer,
                    episode=episode_idx,
                )
            elif claim:
                self.post_blackboard(
                    "hypothesis",
                    f"Node {node_id} ({locus}, v={value:.2f}): {claim}",
                    author=layer,
                    episode=episode_idx,
                )
            self.answer_pending_questions()
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

    # ------------------------------------------------------------------ #
    # V18 Pre-Edit Builder Huddle, Candidate-Pool Ranker & Synthesis Roundtable
    # ------------------------------------------------------------------ #
    def run_pre_edit_builder_huddle(self, engine: Any, contract: Any, branches: List[Any]) -> Tuple[Any, List[Any]]:
        """Run a short Pre-Edit Builder Huddle in `#builders-room` with authority to amend/override the L0 contract."""
        rounds = int(self.redteam.get("pre_huddle_rounds") or 0)
        if rounds <= 0 or self._orig_call_ref is None:
            return contract, branches

        recon = getattr(engine, "recon_brief", None)
        recon_txt = recon.to_compact_prompt_block() if (recon is not None and hasattr(recon, "to_compact_prompt_block")) else ""
        contract_txt = contract.to_prompt_block() if (contract is not None and hasattr(contract, "to_prompt_block")) else str(contract)
        branch_summary = [
            {
                "locus": getattr(b, "locus", ""),
                "title": getattr(b, "title", ""),
                "target_files": list(getattr(b, "target_files", []) or []),
                "plan": str(getattr(b, "mechanism_and_plan", ""))[:260],
            }
            for b in (branches or [])[:3]
        ]
        obj_txt = engine.runner.objective_text() if hasattr(engine, "runner") and hasattr(engine.runner, "objective_text") else ""

        for r_idx in range(1, rounds + 1):
            # Persona 1: Upstream State-Invariant Builder
            sys_a = (
                "You are **Builder-A (Upstream State-Invariant & Producer Architect)** in the Pre-Edit Builder Huddle (`#builders-room`).\n"
                "Critically examine the Reconnaissance code excerpts and the L0 Seed Architect's contract/branch plan BEFORE any code is edited.\n"
                "Respond with a single JSON object:\n"
                "{\n"
                '  "diagnosis_critique": "Does L0\'s diagnosis match the actual code in Recon, or did L0 misdiagnose the root cause / miss an upstream caller?",\n'
                '  "branch1_target_files": ["path/to/file.py"],\n'
                '  "branch1_plan": "Concrete surgical fix plan for Branch 1 (primary/upstream invariant)"\n'
                "}"
            )
            user_a = (
                f"{obj_txt[:2200]}\n\n"
                f"### Reconnaissance Code Map (`#recon-map`)\n{recon_txt[:2200]}\n\n"
                f"### L0 Proposed Contract & Branches\n{contract_txt[:1200]}\n"
                f"Branches: {json.dumps(branch_summary, ensure_ascii=False)}\n\n"
                "Emit your JSON huddle assessment for `#builders-room` now."
            )
            try:
                resp_a = self._orig_call_ref(
                    "L2_BuilderHuddle_Upstream",
                    [{"role": "system", "content": sys_a}, {"role": "user", "content": user_a}],
                    temperature=0.2,
                    enable_thinking=False,
                )
            except TypeError:
                resp_a = self._orig_call_ref(
                    "L2_BuilderHuddle_Upstream",
                    [{"role": "system", "content": sys_a}, {"role": "user", "content": user_a}],
                    temperature=0.2,
                )
            txt_a = str(resp_a[0] if isinstance(resp_a, tuple) else resp_a or "").strip()
            if txt_a:
                self.huddle_notes.append({"round": r_idx, "role": "builder_upstream", "text": txt_a[:1200]})
                self.post_blackboard(
                    "design_note",
                    f"[Huddle R{r_idx} Upstream Builder] {txt_a}",
                    author="builder_upstream",
                    channel="builders-room",
                    episode=0,
                )

            # Persona 2: Call-Site & Backward-Compatibility Surgeon (with Contract-Override Authority)
            sys_b = (
                "You are **Builder-B (Call-Site & Backward-Compatibility Surgeon)** in the Pre-Edit Builder Huddle (`#builders-room`).\n"
                "You have explicit authority to AMEND or OVERRIDE a flawed L0 Architect contract or branch assignment if the Reconnaissance code shows L0's assumption is wrong or over-constrained.\n"
                "Respond with a single JSON object:\n"
                "{\n"
                '  "override_contract": true,\n'
                '  "amended_invariants": ["revised falsifiable invariant 1", "revised invariant 2"],\n'
                '  "branch1_plan": "Final agreed plan for Branch 1 (`b1`)",\n'
                '  "branch2_target_files": ["path/to/file.py"],\n'
                '  "branch2_plan": " genuinely orthogonal plan for Branch 2 (`b2`: e.g. minimal call-site / sibling / backward-compat fix)"\n'
                "}"
            )
            user_b = (
                f"{obj_txt[:2000]}\n\n"
                f"### Reconnaissance Code Map (`#recon-map`)\n{recon_txt[:1800]}\n\n"
                f"### Builder-A Huddle Note\n{txt_a[:1200]}\n\n"
                "Emit your JSON response (with `override_contract`, `amended_invariants`, `branch1_plan`, and `branch2_plan`) now."
            )
            try:
                resp_b = self._orig_call_ref(
                    "L2_BuilderHuddle_Surgeon",
                    [{"role": "system", "content": sys_b}, {"role": "user", "content": user_b}],
                    temperature=0.2,
                    enable_thinking=False,
                )
            except TypeError:
                resp_b = self._orig_call_ref(
                    "L2_BuilderHuddle_Surgeon",
                    [{"role": "system", "content": sys_b}, {"role": "user", "content": user_b}],
                    temperature=0.2,
                )
            txt_b = str(resp_b[0] if isinstance(resp_b, tuple) else resp_b or "").strip()
            if txt_b:
                self.huddle_notes.append({"round": r_idx, "role": "builder_surgeon", "text": txt_b[:1200]})
                m_json = re.search(r"\{.*\}", txt_b, flags=re.DOTALL)
                parsed_b = {}
                if m_json:
                    try:
                        parsed_b = json.loads(m_json.group(0))
                    except Exception:
                        parsed_b = {}
                if isinstance(parsed_b, dict):
                    if parsed_b.get("override_contract") and isinstance(parsed_b.get("amended_invariants"), list):
                        new_invs = [str(x).strip() for x in parsed_b["amended_invariants"] if str(x).strip()][:6]
                        if new_invs and contract is not None and hasattr(contract, "invariants"):
                            contract.invariants = new_invs
                            self.contract_overrides += 1
                            self.post_blackboard(
                                "amendment",
                                f"Builder Huddle amended L0 contract invariants: {new_invs}",
                                author="builder_surgeon",
                                channel="builders-room",
                                episode=0,
                            )
                    b1_plan = str(parsed_b.get("branch1_plan") or "").strip()
                    b2_plan = str(parsed_b.get("branch2_plan") or "").strip()
                    b2_files = parsed_b.get("branch2_target_files")
                    if b1_plan and branches and hasattr(branches[0], "mechanism_and_plan"):
                        branches[0].mechanism_and_plan = b1_plan
                    if b2_plan and len(branches) >= 2 and hasattr(branches[1], "mechanism_and_plan"):
                        branches[1].mechanism_and_plan = b2_plan
                        if isinstance(b2_files, list) and b2_files:
                            branches[1].target_files = [str(f).strip() for f in b2_files if str(f).strip()]
                self.post_blackboard(
                    "design_note",
                    f"[Huddle R{r_idx} Surgeon Decision] {txt_b}",
                    author="builder_surgeon",
                    channel="builders-room",
                    episode=0,
                )
        return contract, branches

    def register_candidate_in_pool(
        self,
        node_id: str,
        locus: str,
        cp: Any,
        panel_results: Sequence[Tuple[str, bool, str, bool]],
        expanded_ok: bool = True,
        is_synthesis: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """Store a compiling non-empty checkpoint in `self.candidate_pool` with its continuous Red-Team Ranker score."""
        if cp is None:
            return None
        diff_str = str(getattr(cp, "diff", "") or "")
        if not diff_str.strip() or not getattr(cp, "compile_ok", True):
            return None
        diff_sha = hashlib.sha1(diff_str.encode("utf-8", errors="replace")).hexdigest()[:12]
        diff_lines = sum(
            1 for ln in diff_str.splitlines()
            if ln[:1] in "+-" and not ln.startswith(("+++", "---"))
        )
        if panel_results:
            pass_rate = sum(1.0 for _, ok, _, _ in panel_results if ok) / float(len(panel_results))
            ran_ev = sum(1.0 for _, _, _, ev in panel_results if ev) / float(len(panel_results))
            critiques = " | ".join(f"{rid}: {crit[:220]}" for rid, ok, crit, _ in panel_results if not ok)
        else:
            pass_rate = 1.0 if getattr(cp, "audit_ok", False) else 0.5
            ran_ev = 1.0 if getattr(cp, "audit_ok", False) else 0.0
            critiques = ""

        preserved_cp = copy.deepcopy(cp)
        if preserved_cp.repro_ok and preserved_cp.repo_tests_ok and self.redteam.get("pool_ranker"):
            preserved_cp.gatekeeper_ok = True

        entry: Dict[str, Any] = {
            "node_id": str(node_id),
            "locus": str(locus),
            "episode": max(1, int(self._current_episode_idx or 1)),
            "diff_sha": diff_sha,
            "diff": diff_str,
            "changed_files": list(getattr(cp, "changed_files", []) or []),
            "diff_lines": max(1, diff_lines),
            "repro_ok": bool(getattr(cp, "repro_ok", False)),
            "repo_tests_ok": bool(getattr(cp, "repo_tests_ok", False)),
            "compile_ok": bool(getattr(cp, "compile_ok", True)),
            "static_ok": bool(getattr(cp, "static_ok", True)),
            "expanded_tests_ok": bool(expanded_ok),
            "new_test_failures": int(getattr(cp, "new_test_failures", 0) or 0),
            "panel_pass_rate": round(pass_rate, 4),
            "ran_evidence": round(ran_ev, 4),
            "is_synthesis": bool(is_synthesis),
            "critique": critiques[:500],
            "checkpoint": preserved_cp,
        }
        entry["rank_score"] = score_candidate_patch(entry, self.redteam)
        # Update existing entry with same diff_sha if higher score or keep distinct
        for idx, existing in enumerate(self.candidate_pool):
            if existing["diff_sha"] == diff_sha:
                if entry["rank_score"] >= existing["rank_score"]:
                    self.candidate_pool[idx] = entry
                return self.candidate_pool[idx]
        self.candidate_pool.append(entry)
        return entry

    def distinct_pool_candidates(self) -> List[Dict[str, Any]]:
        """Return candidate pool entries sorted by descending `rank_score`."""
        return sorted(
            self.candidate_pool,
            key=lambda e: (
                float(e.get("rank_score") or 0.0),
                1 if e.get("repro_ok") and e.get("repo_tests_ok") else 0,
                float(e.get("panel_pass_rate") or 0.0),
                int(e.get("episode") or 1),
                -int(e.get("diff_lines") or 999),
            ),
            reverse=True,
        )

    def best_pool_candidate(self) -> Optional[Dict[str, Any]]:
        ranked = self.distinct_pool_candidates()
        return ranked[0] if ranked else None

    def apply_best_pool_candidate(self, engine: Any) -> Optional[Dict[str, Any]]:
        """Restore the highest-scoring candidate from `self.candidate_pool` into `/testbed` and `engine.checkpoints`."""
        best = self.best_pool_candidate()
        if best is None or best.get("checkpoint") is None:
            return None
        cp = best["checkpoint"]
        try:
            curr_diff, _, _ = engine._current_diff_and_files()
            if curr_diff != cp.diff:
                engine._restore_checkpoint(cp)
            if hasattr(engine, "checkpoints"):
                engine.checkpoints.append(cp)
        except Exception:
            pass
        return best

    def run_synthesis_roundtable(self, engine: Any) -> Optional[Dict[str, Any]]:
        """Convene a Post-Prototype Synthesis Roundtable over the top 2 distinct candidate diffs (`b1`, `b2`)."""
        if (
            not self.has_redteam
            or not self.redteam.get("synthesis_roundtable")
            or self._synthesis_ran
            or self._in_synthesis
        ):
            return None
        ranked = self.distinct_pool_candidates()
        if len(ranked) < 2:
            return None
        cand1, cand2 = ranked[0], ranked[1]
        # If cand1 already passed repro, repo_tests, and 100% of the Red-Team panel, no synthesis needed unless forced
        if cand1.get("repro_ok") and cand1.get("repo_tests_ok") and float(cand1.get("panel_pass_rate") or 0.0) >= 1.0:
            return cand1

        self._synthesis_ran = True
        self._in_synthesis = True
        self.synthesis_runs += 1
        try:
            self.logger(
                f"[v18-collab] Launching Post-Prototype Synthesis Roundtable over `{cand1['node_id']}` "
                f"(score={cand1['rank_score']:.3f}) and `{cand2['node_id']}` (score={cand2['rank_score']:.3f})"
            )
            self.post_blackboard(
                "synthesis",
                f"Convening Synthesis Roundtable comparing {cand1['node_id']} ({cand1['changed_files']}, score={cand1['rank_score']:.2f}) "
                f"and {cand2['node_id']} ({cand2['changed_files']}, score={cand2['rank_score']:.2f}).",
                author="architect",
                channel="builders-room",
            )
            if cand1.get("checkpoint") is not None:
                engine._restore_checkpoint(cand1["checkpoint"])
            obj_txt = engine.runner.objective_text() if hasattr(engine, "runner") and hasattr(engine.runner, "objective_text") else ""
            repro_rel = getattr(engine, "repro_script_rel", ".hae/reproduce_issue.py")
            synth_prompt = (
                f"{obj_txt}\n\n"
                f"### Post-Prototype Synthesis Roundtable (`#builders-room`)\n"
                f"Two builders independently prototyped candidate patches for this issue:\n\n"
                f"#### Candidate 1 (`{cand1['node_id']}`, `{cand1['locus']}`, score={cand1['rank_score']:.2f}, "
                f"repro_ok={cand1['repro_ok']}, repo_tests_ok={cand1['repo_tests_ok']}, panel_pass={cand1['panel_pass_rate']:.2f}) "
                f"— **CURRENTLY APPLIED IN `/testbed`**\n"
                f"Critique / Notes: {cand1.get('critique') or 'Passed local gatekeeper'}\n"
                f"```diff\n{cand1['diff'][:3000]}\n```\n\n"
                f"#### Candidate 2 (`{cand2['node_id']}`, `{cand2['locus']}`, score={cand2['rank_score']:.2f}, "
                f"repro_ok={cand2['repro_ok']}, repo_tests_ok={cand2['repo_tests_ok']}, panel_pass={cand2['panel_pass_rate']:.2f})\n"
                f"Critique / Notes: {cand2.get('critique') or 'Alternative prototype'}\n"
                f"```diff\n{cand2['diff'][:3000]}\n```\n\n"
                "### Your Mandate as Synthesis Roundtable Lead (4-8 Turns)\n"
                "1. Candidate 1 is currently applied in `/testbed`. Compare Candidate 1 and Candidate 2 against every failing case in `#verdicts-and-repros`.\n"
                "2. If Candidate 2 handles an upstream caller, sibling class, or edge case that Candidate 1 missed (or vice versa), merge the best parts using `str_replace`.\n"
                f"3. Run `python {repro_rel}` and `pytest` via `bash`, post a brief `synthesis` note on ` ```blackboard `, and emit ` ```submit `."
            )
            if hasattr(engine, "run_specialist_loop") and hasattr(engine, "_system_prompt"):
                engine.run_specialist_loop(
                    specialist_name="L2_SynthesisRoundtable_b_synth",
                    system_prompt=engine._system_prompt(
                        "Post-Prototype Synthesis Roundtable Lead",
                        "Synthesize the strongest elements of Candidate 1 and Candidate 2 so all reproduction, pytest, and Red-Team edge cases pass.",
                    ),
                    initial_user_prompt=synth_prompt,
                    max_turns=8,
                    temperature=0.2,
                    is_auditor=False,
                )
            gate_ok, _msg, cp_syn = engine.verify_current_workspace(
                specialist="L2_SynthesisRoundtable_b_synth",
                turn=99,
                is_auditor=False,
            )
            if cp_syn is not None and cp_syn.diff.strip() and cp_syn.compile_ok:
                panel_res: List[Tuple[str, bool, str, bool]] = []
                exp_ok = True
                if cp_syn.repro_ok and cp_syn.repo_tests_ok and getattr(engine, "enable_falsifier", True):
                    self._current_node_id = "b_synth"
                    engine.run_falsifier_episode(cp_syn, max_turns=int(self.redteam.get("max_turns") or 8))
                    panel_res = list(self._last_panel_results)
                    exp_ok = self._last_expanded_ok
                syn_entry = self.register_candidate_in_pool(
                    node_id="b_synth",
                    locus="synthesis_roundtable",
                    cp=cp_syn,
                    panel_results=panel_res,
                    expanded_ok=exp_ok,
                    is_synthesis=True,
                )
                if syn_entry is not None:
                    self.post_blackboard(
                        "synthesis",
                        f"Synthesized candidate `b_synth` on {cp_syn.changed_files}: repro_ok={cp_syn.repro_ok}, "
                        f"repo_tests_ok={cp_syn.repo_tests_ok}, rank_score={syn_entry['rank_score']:.3f}.",
                        author="executor",
                        channel="builders-room",
                    )
                    return syn_entry
        except Exception as exc:
            self.logger(f"[v18-collab] synthesis roundtable error: {exc}")
        finally:
            self._in_synthesis = False
        return self.best_pool_candidate()

    def attach(self, engine: Any) -> None:
        """Wrap the engine's LLM-call, node-evaluation, and (in V17/V18) red-team & collaborative hooks."""
        org = self
        self._engine_ref = engine
        orig_call = getattr(engine, "_call_specialist_turn", None)
        orig_eval = getattr(engine, "evaluate_and_register_mcts_node", None)
        if orig_call is None or orig_eval is None:
            self.logger("[v16-org] engine lacks _call_specialist_turn/evaluate_and_register_mcts_node; org inactive")
            return
        self._orig_call_ref = orig_call

        def _call(role_name: str, messages: List[Dict[str, str]], *args: Any, **kwargs: Any):
            res = orig_call(role_name, org.inject(role_name, messages), *args, **kwargs)
            txt = str(res[0] if isinstance(res, tuple) and len(res) >= 1 else (res or ""))
            if org._in_falsifier:
                org._last_falsifier_text = txt
            if org.has_redteam and org.redteam.get("blackboard_on") and not org._in_question_answer:
                author = layer_of(role_name) or ("recon" if "Recon" in role_name else "agent")
                new_qs = org.ingest_blackboard_text(txt, author=author)
                if new_qs:
                    org.answer_pending_questions()
            return res

        def _eval(*args: Any, **kwargs: Any):
            names = ("node_id", "parent_id", "locus", "hypothesis_claim", "episode_idx", "run_falsifier_if_gate_ok")
            bound = dict(zip(names, args))
            bound.update(kwargs)
            parent_id = str(bound.get("parent_id", "root"))
            org._current_node_id = str(bound.get("node_id", ""))
            org._current_episode_idx = int(bound.get("episode_idx", 0) or 0)
            org._last_panel_results = []
            org._last_expanded_ok = True
            parent = getattr(engine, "mcts_nodes", {}).get(parent_id)
            parent_value = float(getattr(parent, "value", 0.0) or 0.0)
            node = orig_eval(*args, **kwargs)
            try:
                cp = getattr(node, "checkpoint", None)
                if org.has_redteam and cp is not None and getattr(cp, "diff", "").strip() and getattr(cp, "compile_ok", True):
                    entry = org.register_candidate_in_pool(
                        node_id=org._current_node_id,
                        locus=str(getattr(node, "locus", "") or ""),
                        cp=cp,
                        panel_results=org._last_panel_results,
                        expanded_ok=org._last_expanded_ok,
                        is_synthesis=False,
                    )
                    if entry is not None and org.redteam.get("pool_ranker") and cp.repro_ok and cp.repo_tests_ok:
                        # Under pool_ranker, preserve empirical value proportional to rank_score so a false rejection
                        # by one panelist does not destroy a repro+pytest-verified checkpoint.
                        node.value = max(float(getattr(node, "value", 0.0) or 0.0), min(0.95, 0.78 + 0.17 * float(entry["panel_pass_rate"])))
                        cp.gatekeeper_ok = True
                org.after_episode(org._current_node_id, parent_id, parent_value, node, org._current_episode_idx)
                if (
                    org.has_redteam
                    and org.redteam.get("synthesis_roundtable")
                    and not org._synthesis_ran
                    and len(org.distinct_pool_candidates()) >= 2
                ):
                    org.run_synthesis_roundtable(engine)
                    if org.redteam.get("pool_ranker"):
                        org.apply_best_pool_candidate(engine)
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
        orig_arch = getattr(engine, "run_architect_episode", None)
        orig_seed_arch = getattr(engine, "formulate_seed_contract_and_branches", None)
        orig_verify = getattr(engine, "verify_current_workspace", None)
        orig_falsifier = getattr(engine, "run_falsifier_episode", None)
        orig_view = getattr(engine, "execute_view_file_tool", None)
        orig_sr = getattr(engine, "execute_str_replace_tool", None)
        orig_wf = getattr(engine, "execute_write_file_tool", None)
        orig_bash = getattr(engine, "execute_bash_tool", None)
        orig_select = getattr(engine, "select_next_mcts_operation", None)
        orig_run = getattr(engine, "run", None)

        # 1. Lock reproduction script once verified failing on HEAD & populate #recon-map / #builders-room.
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
                if org.redteam.get("blackboard_on") and brief is not None:
                    cfiles = list(
                        getattr(brief, "inspected_files", None)
                        or getattr(brief, "candidate_files", None)
                        or []
                    )[:6]
                    repro_ok = bool(getattr(brief, "repro_fails_on_head", False))
                    consumer = str(getattr(brief, "consumer_locus", "") or "")
                    producer = str(getattr(brief, "producer_upstream_locus", "") or "")
                    siblings = str(getattr(brief, "conventions_and_siblings", "") or "")
                    summary = str(
                        getattr(brief, "raw_summary", "")
                        or getattr(brief, "summary", "")
                        or ""
                    )[:600]
                    org.post_blackboard(
                        "finding",
                        f"Recon candidate_files={cfiles}; consumer={consumer}; producer={producer}; "
                        f"siblings={siblings}; repro_fails_on_head={repro_ok}. {summary}",
                        author="recon",
                        episode=0,
                        channel="recon-map",
                    )
                    org.sync_blackboard_from_disk()
                    org.answer_pending_questions()
                return brief
            engine.run_recon_episode = _recon

        if orig_arch is not None:
            def _arch(*args: Any, **kwargs: Any):
                plan = orig_arch(*args, **kwargs)
                if org.redteam.get("blackboard_on") and isinstance(plan, Mapping):
                    diag = str(plan.get("root_cause_diagnosis") or "")[:300]
                    loci = [
                        f"{b.get('locus')}:{b.get('target_files', [])[:2]}"
                        for b in (plan.get("branches") or [])[:3]
                        if isinstance(b, Mapping)
                    ]
                    if diag or loci:
                        org.post_blackboard(
                            "decision",
                            f"Architect diagnosis: {diag} | branches={loci}",
                            author="architect",
                            episode=0,
                            channel="builders-room",
                        )
                    org.answer_pending_questions()
                return plan
            engine.run_architect_episode = _arch

        if orig_seed_arch is not None:
            def _seed_arch(*args: Any, **kwargs: Any):
                contract, branches = orig_seed_arch(*args, **kwargs)
                if org.redteam.get("blackboard_on"):
                    invs = list(getattr(contract, "invariants", []) or [])[:4]
                    loci = [
                        f"{getattr(b, 'locus', '')}:{list(getattr(b, 'target_files', []) or [])[:2]}"
                        for b in (branches or [])[:3]
                    ]
                    org.post_blackboard(
                        "decision",
                        f"L0 Seed Architect contract invariants={invs} | branches={loci}",
                        author="architect",
                        episode=0,
                        channel="builders-room",
                    )
                    if int(org.redteam.get("pre_huddle_rounds") or 0) > 0:
                        contract, branches = org.run_pre_edit_builder_huddle(engine, contract, list(branches or []))
                        engine.contract = contract
                        engine.branch_queue = list(branches)
                    org.answer_pending_questions()
                return contract, branches
            engine.formulate_seed_contract_and_branches = _seed_arch

        if orig_view is not None:
            def _view(rel_path: str, start_line: int, end_line: int, *args: Any, **kwargs: Any):
                obs = orig_view(rel_path, start_line, end_line, *args, **kwargs)
                if (
                    org.redteam.get("blackboard_on")
                    and org.redteam.get("auto_index_views")
                    and not org._in_falsifier
                    and "ERROR:" not in str(obs)
                ):
                    defs = re.findall(r"^\s*\d+\s*\|\s*((?:def|class)\s+[A-Za-z_][A-Za-z0-9_]*[^:\n]*)", str(obs), flags=re.M)
                    sig_note = f" — symbols: {', '.join(d.strip() for d in defs[:5])}" if defs else ""
                    org.post_blackboard(
                        "finding",
                        f"Indexed `{rel_path}` lines {start_line}-{end_line}{sig_note}",
                        author="recon",
                        channel="recon-map",
                    )
                return obs
            engine.execute_view_file_tool = _view

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
                obs = orig_sr(path, old_str, new_str, *args, **kwargs)
                if (
                    org.redteam.get("blackboard_on")
                    and org.redteam.get("auto_index_views")
                    and not org._in_falsifier
                    and "SUCCESS" in str(obs)
                ):
                    org.post_blackboard(
                        "design_note",
                        f"Applied surgical edit to `{rel}` ({len((new_str or '').splitlines())} replacement lines).",
                        author="executor",
                        channel="builders-room",
                    )
                return obs
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

        # 3. Wrap run_falsifier_episode for panel_size, max_turns, skeptic_default, evidence_tests,
        #    duplicate-patch tracking, candidate-pool ranking, and blackboard failing_case sharing.
        if orig_falsifier is not None:
            def _falsifier(cp: Any, max_turns: int = 8) -> Tuple[bool, str]:
                turns = int(org.redteam.get("max_turns") or max_turns)
                panel_k = max(1, int(org.redteam.get("panel_size") or 1))
                panelists = org.panel_roles(panel_k)
                panel_results: List[Tuple[str, bool, str]] = []
                panel_results_with_ev: List[Tuple[str, bool, str, bool]] = []
                final_ok = True
                final_msg = ""
                snap_cp = copy.deepcopy(cp)

                diff_str = str(getattr(cp, "diff", "") or "")
                diff_sha = hashlib.sha1(diff_str.encode("utf-8", errors="replace")).hexdigest()[:12]
                if diff_str.strip():
                    if diff_sha in org.candidate_shas:
                        org.duplicate_patches += 1
                        if org.redteam.get("blackboard_on"):
                            cfiles = list(getattr(cp, "changed_files", None) or [])
                            org.post_blackboard(
                                "ruled_out",
                                f"DUPLICATE PATCH ({diff_sha[:8]}) on {cfiles}: identical diff was already evaluated. "
                                "Do NOT resubmit the same patch.",
                                author="falsifier",
                                channel="verdicts-and-repros",
                            )
                    org.candidate_shas.append(diff_sha)

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

                    suite_rel = getattr(engine, "falsification_script_rel", ".hae/falsification_suite.py")
                    ran_evidence = org._falsifier_bash_count > 0 or engine.executor.exists(suite_rel)

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
                    panel_results_with_ev.append((rid, bool(ok), str(msg), bool(ran_evidence)))
                    final_ok = bool(ok)
                    final_msg = f"[Panel {idx}/{panel_k} ({role.name if role else 'Generalist'})] {msg}"
                    # Under V18 pool_ranker, let all panelists vote if panel_k <= 2 and ran_evidence is True so pass_rate is calibrated;
                    # under V17 binary gating (pool_ranker=False), short-circuit on first failure.
                    if not ok and not org.redteam.get("pool_ranker"):
                        break
                    if not ok and not ran_evidence:
                        break

                final_ok = all(p_ok for _, p_ok, _ in panel_results) if panel_results else False
                ev_ok = True
                if (final_ok or org.redteam.get("pool_ranker")) and org.redteam.get("evidence_tests"):
                    ev_ok, ev_msg = org._run_expanded_evidence_tests(engine, cp)
                    if not ev_ok:
                        final_ok = False
                        final_msg = ev_msg
                        if panel_results:
                            last_rid, _, _ = panel_results[-1]
                            panel_results[-1] = (last_rid, False, ev_msg)
                            panel_results_with_ev[-1] = (last_rid, False, ev_msg, panel_results_with_ev[-1][3])

                org._last_panel_results = list(panel_results_with_ev)
                org._last_expanded_ok = bool(ev_ok)

                # Share executable failing test case on the blackboard when rejected
                if not final_ok and org.redteam.get("blackboard_on") and org.redteam.get("falsifier_shares_tests"):
                    suite_rel = getattr(engine, "falsification_script_rel", ".hae/falsification_suite.py")
                    suite_snip = ""
                    try:
                        if engine.executor.exists(suite_rel):
                            suite_snip = engine.executor.read_file(suite_rel).strip()[-350:]
                    except Exception:
                        pass
                    fail_body = f"Patch {diff_sha[:8]} failed: {final_msg[:350]}"
                    if suite_snip:
                        fail_body += f" | test ({suite_rel}): {suite_snip}"
                    posted_fc = org.post_blackboard(
                        "failing_case",
                        fail_body,
                        author="falsifier",
                        channel="verdicts-and-repros",
                    )
                    if posted_fc is not None:
                        org.failing_cases_shared += 1
                    # If no concrete assertion/traceback in critique and question budget remains, ask falsifier for minimal input
                    if (
                        "AssertionError" not in final_msg
                        and "Traceback" not in final_msg
                        and int(org.redteam.get("max_questions") or 0) > org.questions_asked
                    ):
                        org.post_blackboard(
                            "question",
                            f"Which minimal Python input or test command breaks patch {diff_sha[:8]}?",
                            author="refiner",
                            to="falsifier",
                            channel="verdicts-and-repros",
                        )
                        org.answer_pending_questions()

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

        # 4. Branch scheduling: continuous_build OR V18 synthesis_roundtable (ensure b2 runs if b1 wasn't unanimously certified).
        if orig_select is not None and (org.redteam.get("continuous_build") or org.redteam.get("synthesis_roundtable")):
            def _select(candidate_modules: List[str]):
                nodes = getattr(engine, "mcts_nodes", {}) or {}
                if org.redteam.get("synthesis_roundtable"):
                    root_children = [n for nid, n in nodes.items() if nid != "root" and getattr(n, "parent_id", "") == "root"]
                    bqueue = getattr(engine, "branch_queue", []) or []
                    # If b1 ran and was not certified global optimum, launch orthogonal b2 from branch_queue so the two builders have distinct prototypes to synthesize!
                    if len(root_children) == 1 and bqueue and not getattr(root_children[0], "is_certified_global_optimum", False):
                        spec = bqueue.pop(0)
                        return "GEN", "root", spec
                if org.redteam.get("continuous_build"):
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

        # 5. Wrap engine.run to finalize Synthesis Roundtable & Candidate-Pool Red-Team Ranker selection.
        if orig_run is not None and (org.redteam.get("pool_ranker") or org.redteam.get("synthesis_roundtable")):
            def _run(*args: Any, **kwargs: Any):
                res = orig_run(*args, **kwargs)
                try:
                    if org.redteam.get("synthesis_roundtable") and not org._synthesis_ran and len(org.distinct_pool_candidates()) >= 2:
                        org.run_synthesis_roundtable(engine)
                    if org.redteam.get("pool_ranker") and org.candidate_pool:
                        best_cand = org.apply_best_pool_candidate(engine)
                        if best_cand is not None and isinstance(res, dict):
                            cp_b = best_cand["checkpoint"]
                            res["synthesized_paths"] = list(cp_b.changed_files)
                            res["patch_chars"] = len(cp_b.diff)
                            res["resolved_by_self_oracle"] = bool(cp_b.repro_ok and cp_b.repo_tests_ok)
                            res["best_checkpoint"] = {
                                "specialist": cp_b.specialist,
                                "turn": cp_b.turn,
                                "rank_key": list(cp_b.rank_key()),
                                "rank_score": best_cand["rank_score"],
                                "changed_files": cp_b.changed_files,
                                "note": cp_b.note,
                            }
                except Exception as exc:
                    org.logger(f"[v18-collab] pool ranker finalize error: {exc}")
                return res
            engine.run = _run

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
            for cand in self.candidate_pool:
                if cand.get("diff_sha") and cand.get("diff"):
                    out["verdict_patches"].setdefault(cand["diff_sha"], cand["diff"])
            out["edit_blocks_refused"] = self.edit_blocks_refused
            out["repro_restorations"] = self.repro_restorations
            out["candidate_patches_total"] = len(self.candidate_shas)
            out["duplicate_patches"] = self.duplicate_patches
            out["duplicate_patch_rate"] = self.duplicate_patch_rate()
            out["candidate_pool"] = [
                {k: v for k, v in c.items() if k not in ("diff", "checkpoint")}
                for c in self.distinct_pool_candidates()
            ]
            out["huddle_notes_count"] = len(self.huddle_notes)
            out["contract_overrides"] = self.contract_overrides
            out["synthesis_runs"] = self.synthesis_runs
            by_channel = {
                ch: sum(1 for e in self.blackboard if e.get("channel") == ch)
                for ch in SUB_BLACKBOARDS
            }
            out["blackboard"] = {
                "enabled": bool(self.redteam.get("blackboard_on")),
                "sub_blackboards_on": bool(self.redteam.get("sub_blackboards_on")),
                "entries_count": len(self.blackboard),
                "by_channel": by_channel,
                "questions_asked": self.questions_asked,
                "questions_answered": self.questions_answered,
                "failing_cases_shared": self.failing_cases_shared,
                "entries": list(self.blackboard),
            }
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
