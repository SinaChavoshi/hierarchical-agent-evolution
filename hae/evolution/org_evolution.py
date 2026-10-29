"""V16/V17: evolution operators for a genome's organization and red team.

  role_rewrite     the LLM rewrites one specialist using the role's measured credit and the parent's
                   org evidence (never generalists);
  redteam_rewrite  (V17) the LLM rewrites a red-team (`falsifier`) specialist from its verdict
                   confusion matrix (TP/FP/TN/FN counts and the red team's own critique strings,
                   never hidden-test content);
  role_add         the LLM writes one new specialist for a layer;
  role_remove      drop one specialist (generalists are never removed);
  ceo_mutate       jitter 1-3 CEO-policy genes within bounds; flip static <-> dynamic with p=0.2;
  org_crossover    union of two role libraries (capped) + uniform CEO-policy crossover.

Every LLM-written role passes `RoleGates` (leakage, ban list, schema, lengths) or the operator
returns None. `random_org` is the matched random-search control (no LLM, no evidence).
Role statistics (`uses`, `mean_delta_u`, `tasks_resolved`) are folded in from training records by
`apply_role_stats` before breeding, so the CEO's `role_score` uses real data.
"""

from __future__ import annotations

import json
import random
import re
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from hae.evolution.role_gates import LLM_LAYERS, RoleGates, to_allele
from hae.genome.schema import CEO_POLICY_BOUNDS, DELTA_U_SCALE, MAX_ROLE_LIBRARY, CEOPolicyGene

# V16 fallback when labelled verdicts are absent:
FALSIFIER_CREDIT = 0.1
# V17 per-verdict credit scaled by DELTA_U_SCALE so +1 maps to quality=1.0:
# correct (+1), false pass (-2), false reject (-1).
VERDICT_CREDIT_TP = 1.0 * DELTA_U_SCALE
VERDICT_CREDIT_TN = 1.0 * DELTA_U_SCALE
VERDICT_CREDIT_FP = -2.0 * DELTA_U_SCALE
VERDICT_CREDIT_FN = -1.0 * DELTA_U_SCALE

CEO_EVOLVABLE = ("exploration_c", "optimistic_prior", "stall_delta_u", "stall_moves", "recruit_w_stall",
                 "recruit_w_headcount", "recruit_bias", "recruit_cooldown_moves")


# --------------------------------------------------------------------------- #
# Statistics from training records
# --------------------------------------------------------------------------- #

def aggregate_role_stats(org_records: Sequence[Mapping[str, Any]]) -> Dict[str, Dict[str, float]]:
    """Per role: episodes, sum of ΔV, tasks staffed, tasks resolved, plus V17 verdict confusion counts."""
    out: Dict[str, Dict[str, float]] = {}

    def st(rid: str) -> Dict[str, float]:
        return out.setdefault(rid, {"episodes": 0, "sum_delta_v": 0.0, "tasks": 0, "resolved": 0,
                                    "tp": 0, "fp": 0, "tn": 0, "fn": 0})

    for rec in org_records:
        org = rec.get("org") or {}
        vg = rec.get("verdicts_grade") or {}
        resolved = bool(rec.get("resolved"))
        staffed = set()
        for rid, c in (org.get("credits") or {}).items():
            s = st(rid)
            s["episodes"] += int(c.get("episodes") or 0)
            s["sum_delta_v"] += float(c.get("sum_delta_v") or 0.0)
            staffed.add(rid)
        for layer, member in (org.get("team") or {}).items():
            staffed.add(member.get("role_id", ""))

        labelled = vg.get("labelled_verdicts") or []
        if labelled:
            for vd in labelled:
                gt = bool(vd.get("ground_truth_resolved"))
                for p in (vd.get("panel") or []):
                    fid = str(p.get("role_id") or "")
                    if not fid:
                        continue
                    passed = bool(p.get("passed"))
                    s = st(fid)
                    s["episodes"] += 1
                    staffed.add(fid)
                    if passed and gt:
                        s["tp"] += 1
                        s["sum_delta_v"] += VERDICT_CREDIT_TP
                    elif passed and not gt:
                        s["fp"] += 1
                        s["sum_delta_v"] += VERDICT_CREDIT_FP
                    elif not passed and not gt:
                        s["tn"] += 1
                        s["sum_delta_v"] += VERDICT_CREDIT_TN
                    else:
                        s["fn"] += 1
                        s["sum_delta_v"] += VERDICT_CREDIT_FN
        else:
            eps = org.get("episodes") or []
            last_ok = [e for e in eps if e.get("falsifier_ok") is not None]
            if last_ok:
                fid = last_ok[-1].get("falsifier_role_id") or ""
                if fid and last_ok[-1].get("falsifier_ok"):
                    s = st(fid)
                    s["episodes"] += 1
                    s["sum_delta_v"] += FALSIFIER_CREDIT if resolved else -FALSIFIER_CREDIT

        for rid in staffed:
            if rid:
                s = st(rid)
                s["tasks"] += 1
                s["resolved"] += int(resolved)
    return out


def apply_role_stats(roles: Sequence[Mapping[str, Any]], stats: Mapping[str, Mapping[str, float]]) -> List[Dict[str, Any]]:
    """Fold new evidence into lifetime statistics (running means weighted by `uses`)."""
    out = []
    for r in roles:
        r = dict(r)
        s = stats.get(r.get("role_id", ""))
        if s and s.get("episodes"):
            n_old = int(r.get("uses") or 0)
            n_new = int(s["episodes"])
            mean_new = float(s["sum_delta_v"]) / n_new
            r["mean_delta_u"] = round((float(r.get("mean_delta_u") or 0.0) * n_old + mean_new * n_new) / (n_old + n_new), 4)
            r["uses"] = n_old + n_new
        if s:
            r["tasks_resolved"] = int(r.get("tasks_resolved") or 0) + int(s.get("resolved") or 0)
        out.append(r)
    return out


def org_digest(roles: Sequence[Mapping[str, Any]], stats: Mapping[str, Mapping[str, float]],
               org_records: Sequence[Mapping[str, Any]]) -> str:
    """Task-free summary of how the organization performed (for the LLM operators)."""
    lines = []
    n = len(org_records)
    solved = sum(1 for r in org_records if r.get("resolved"))
    lines.append(f"Parent organization: {solved}/{n} training issues resolved.")
    swaps = sum(len((r.get("org") or {}).get("swaps") or []) for r in org_records)
    lines.append(f"Mid-run role swaps (recruitment on stall): {swaps} across {n} runs.")
    by_id = {r.get("role_id"): r for r in roles}
    for rid, s in sorted(stats.items(), key=lambda kv: -kv[1].get("tasks", 0)):
        r = by_id.get(rid)
        if not r:
            continue
        ep = int(s.get("episodes") or 0)
        mean = (float(s.get("sum_delta_v") or 0) / ep) if ep else 0.0
        extra_rt = ""
        if int(s.get("tp", 0) + s.get("fp", 0) + s.get("tn", 0) + s.get("fn", 0)) > 0:
            extra_rt = (f" [verdicts: TP={int(s.get('tp', 0))}, FP={int(s.get('fp', 0))}, "
                        f"TN={int(s.get('tn', 0))}, FN={int(s.get('fn', 0))}]")
        lines.append(f"- {r.get('name')} ({r.get('layer')}{', generalist' if r.get('is_generalist') else ''}): "
                     f"staffed on {int(s.get('tasks', 0))} runs ({int(s.get('resolved', 0))} resolved), "
                     f"{ep} credited episodes, mean ΔV {mean:+.3f}{extra_rt}")
    unused = [r.get("name") for r in roles if r.get("role_id") not in stats and r.get("layer") in LLM_LAYERS]
    if unused:
        lines.append("Never staffed: " + ", ".join(unused))
    return "\n".join(lines)


def _sanitize_critique(text: str) -> str:
    """Strip file paths, module names, and issue identifiers so only general failure patterns remain."""
    s = re.sub(r"[A-Za-z0-9_./-]+\.py(?:::[A-Za-z0-9_:\[\]-]+)?", "<file>", text or "")
    s = re.sub(r"\b[a-z0-9_]+__[a-z0-9_-]+\b", "<task>", s, flags=re.I)
    return s[:220].strip()


def redteam_digest(roles: Sequence[Mapping[str, Any]], stats: Mapping[str, Mapping[str, float]],
                   org_records: Sequence[Mapping[str, Any]]) -> str:
    """Confusion matrix and sanitized false-PASS / false-REJECT notes of the red team (never hidden-test content)."""
    tp = fp = tn = fn = 0
    fp_reasons: List[str] = []
    fn_reasons: List[str] = []
    for rec in org_records:
        vg = rec.get("verdicts_grade") or {}
        tp += int(vg.get("rt_tp") or 0)
        fp += int(vg.get("rt_fp") or 0)
        tn += int(vg.get("rt_tn") or 0)
        fn += int(vg.get("rt_fn") or 0)
        for vd in (vg.get("labelled_verdicts") or []):
            gt = bool(vd.get("ground_truth_resolved"))
            for p in (vd.get("panel") or []):
                crit = _sanitize_critique(str(p.get("critique") or ""))
                if not crit:
                    continue
                if p.get("passed") and not gt and len(fp_reasons) < 4:
                    fp_reasons.append(f"False PASS rationale: {crit}")
                elif (not p.get("passed")) and gt and len(fn_reasons) < 3:
                    fn_reasons.append(f"False REJECT rationale: {crit}")
    lines = [
        f"Red-team confusion matrix across training runs: True PASS (TP)={tp}, False PASS (FP)={fp}, "
        f"True REJECT (TN)={tn}, False REJECT (FN)={fn}.",
        org_digest(roles, stats, org_records),
    ]
    if fp_reasons:
        lines.append("Sample False-PASS rationales (where the red team certified an incomplete/broken patch):")
        lines += [f"  * {x}" for x in fp_reasons]
    if fn_reasons:
        lines.append("Sample False-REJECT rationales (where the red team rejected a valid patch):")
        lines += [f"  * {x}" for x in fn_reasons]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Operators
# --------------------------------------------------------------------------- #

def _specialists(roles: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    return [dict(r) for r in roles if not r.get("is_generalist") and r.get("layer") in LLM_LAYERS]


def _extract_role(text: str) -> Optional[Dict[str, Any]]:
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)
    m = re.search(r"\{.*\}", text, flags=re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if isinstance(data.get("roles"), list) and data["roles"]:
        data = data["roles"][0]
    return data if isinstance(data, dict) else None


ROLE_JSON = ('{"layer": "...", "name": "...", "goal": "...", "backstory": "...", "playbook": ["..."], '
             '"failure_modes": ["..."], "domain_tags": ["..."], "temperature": 0.2}')

SYSTEM = ("You improve the staff of an automated software-repair organization. A role is injected into the system "
          "prompt of an LLM agent for one harness layer. Roles must be domain-general: never name a repository, "
          "project, library, file path, test name, issue number or specific function. Reply with one JSON object.")

LAYERS_TEXT = (
    "architect: one JSON call that writes the verification contract and 3 causal branches (no tools).\n"
    "executor: multi-turn GEN episode from clean HEAD (bash, view_file, str_replace, submit).\n"
    "refiner: multi-turn CONT episode that fixes a parent patch from its critique.\n"
    "falsifier: multi-turn read/test-only adversarial audit, returns VERDICT: PASS or VERDICT: FAIL: <reason>."
)


class OrgMutator:
    def __init__(self, gates: RoleGates, llm: Callable[[str, str], str], logger: Callable[[str], None] = print,
                 max_attempts: int = 3) -> None:
        self.gates = gates
        self.llm = llm
        self.logger = logger
        self.max_attempts = max_attempts

    def _ask_role(self, prompt: str, layer: str, generation: int, origin: str) -> Optional[Dict[str, Any]]:
        feedback = ""
        for _ in range(self.max_attempts):
            try:
                reply = self.llm(SYSTEM, prompt + (f"\n\nYour previous answer was rejected: {feedback}" if feedback else ""))
            except Exception as exc:  # pragma: no cover - network
                feedback = f"LLM error {exc}"
                continue
            role = _extract_role(reply)
            if not role:
                feedback = "no JSON object found"
                continue
            role["layer"] = layer
            errs = self.gates.check(role)
            if errs:
                feedback = "; ".join(errs)
                continue
            allele = to_allele(role, generation=generation)
            allele.origin = origin
            return allele.to_dict()
        return None

    def role_rewrite(self, roles: List[Dict[str, Any]], digest: str, rng: random.Random,
                     generation: int) -> Tuple[Optional[List[Dict[str, Any]]], str]:
        specs = _specialists(roles)
        if not specs:
            return None, "no specialist to rewrite"
        used = [r for r in specs if int(r.get("uses") or 0) > 0]
        target = min(used, key=lambda r: float(r.get("mean_delta_u") or 0)) if used and rng.random() < 0.7 else rng.choice(specs)
        prompt = (f"## Harness layers\n{LAYERS_TEXT}\n\n## How the organization performed\n{digest}\n\n"
                  f"## Role to rewrite (layer: {target['layer']})\n"
                  + json.dumps({k: target.get(k) for k in ("name", "goal", "backstory", "playbook", "failure_modes",
                                                            "domain_tags", "temperature")}, indent=1)
                  + "\n\nRewrite this role so it adds more value in its layer: sharpen the playbook into concrete, "
                    "checkable steps for the failure modes the evidence shows. Keep the layer. Playbook 3-8 steps "
                    "<= 200 chars each; domain_tags 5-12 lowercase words found in issue text. Output: " + ROLE_JSON)
        new = self._ask_role(prompt, target["layer"], generation, f"v16-rewrite:{target['role_id']}")
        if new is None:
            return None, f"rewrite of {target['name']} failed gates"
        new["role_id"] = target["role_id"]
        new["uses"], new["mean_delta_u"], new["tasks_resolved"] = 0, 0.0, 0
        out = [new if r.get("role_id") == target["role_id"] else dict(r) for r in roles]
        return out, f"role_rewrite {target['name']} -> {new['name']}"

    def redteam_rewrite(self, roles: List[Dict[str, Any]], rt_digest: str, rng: random.Random,
                        generation: int) -> Tuple[Optional[List[Dict[str, Any]]], str]:
        """V17: rewrite a red-team (`falsifier`) specialist from its confusion matrix to eliminate False PASSes."""
        fals_specs = [r for r in _specialists(roles) if r.get("layer") == "falsifier"]
        if not fals_specs:
            return self.role_add(roles, rt_digest, rng, generation, force_layer="falsifier")
        used = [r for r in fals_specs if int(r.get("uses") or 0) > 0]
        target = min(used, key=lambda r: float(r.get("mean_delta_u") or 0)) if used else rng.choice(fals_specs)
        prompt = (f"## Red-Team Falsifier Calibration Task\n"
                  f"The falsifier layer is a strictly read/test-only evaluator that audits candidate patches and "
                  f"emits VERDICT: PASS or VERDICT: FAIL: <reason>. A False PASS counts double negative.\n\n"
                  f"## Red-Team Confusion Matrix & Evidence\n{rt_digest}\n\n"
                  f"## Falsifier Role to Rewrite\n"
                  + json.dumps({k: target.get(k) for k in ("name", "goal", "backstory", "playbook", "failure_modes",
                                                            "domain_tags", "temperature")}, indent=1)
                  + "\n\nRewrite this falsifier role to cut False PASSes while preserving True PASSes: require "
                    "concrete executable checks (boundary values, sibling method/class parity, return types, "
                    "exception/warning classes, and running existing module tests) before approving. "
                    "Playbook 4-8 steps <= 200 chars each; domain_tags 5-12 general lowercase words. Output: " + ROLE_JSON)
        new = self._ask_role(prompt, "falsifier", generation, f"v17-rt-rewrite:{target['role_id']}")
        if new is None:
            return None, f"redteam_rewrite of {target['name']} failed gates"
        new["role_id"] = target["role_id"]
        new["uses"], new["mean_delta_u"], new["tasks_resolved"] = 0, 0.0, 0
        out = [new if r.get("role_id") == target["role_id"] else dict(r) for r in roles]
        return out, f"redteam_rewrite {target['name']} -> {new['name']}"

    def role_add(self, roles: List[Dict[str, Any]], digest: str, rng: random.Random,
                 generation: int, force_layer: Optional[str] = None) -> Tuple[Optional[List[Dict[str, Any]]], str]:
        if len(roles) >= MAX_ROLE_LIBRARY:
            return None, "library full"
        counts = {layer: sum(1 for r in _specialists(roles) if r["layer"] == layer) for layer in LLM_LAYERS}
        layer = force_layer or rng.choice(
            [l for l in LLM_LAYERS if counts[l] == min(counts.values())] if rng.random() < 0.5 else list(LLM_LAYERS)
        )
        existing = "\n".join(f"- {r['name']} ({r['layer']}): {r.get('goal', '')}" for r in _specialists(roles))
        prompt = (f"## Harness layers\n{LAYERS_TEXT}\n\n## How the organization performed\n{digest}\n\n"
                  f"## Existing specialists\n{existing}\n\nWrite ONE new {layer} specialist that targets a failure "
                  "mode the existing specialists do not cover. Playbook 3-8 concrete steps <= 200 chars each; "
                  "domain_tags 5-12 lowercase words found in issue text. Output: " + ROLE_JSON)
        new = self._ask_role(prompt, layer, generation, "v16-add:new")
        if new is None:
            return None, f"role_add ({layer}) failed gates"
        if any(r.get("role_id") == new["role_id"] for r in roles):
            new["role_id"] = new["role_id"] + f"_g{generation}"
        return [dict(r) for r in roles] + [new], f"role_add {layer}: {new['name']}"


def role_remove(roles: List[Dict[str, Any]], rng: random.Random) -> Tuple[Optional[List[Dict[str, Any]]], str]:
    specs = _specialists(roles)
    if not specs:
        return None, "no specialist to remove"
    used = [r for r in specs if int(r.get("uses") or 0) > 0]
    target = min(used, key=lambda r: float(r.get("mean_delta_u") or 0)) if used and rng.random() < 0.7 else rng.choice(specs)
    return [dict(r) for r in roles if r.get("role_id") != target["role_id"]], f"role_remove {target['name']}"


def ceo_mutate(policy: Mapping[str, Any], org_mode: str, rng: random.Random) -> Tuple[Dict[str, Any], str, str]:
    data = CEOPolicyGene.from_dict(dict(policy or {})).to_dict()
    notes = []
    for name in rng.sample(CEO_EVOLVABLE, rng.randint(1, 3)):
        lo, hi, is_int = CEO_POLICY_BOUNDS[name]
        step = rng.gauss(0.0, 0.15 * float(hi - lo))
        if is_int and abs(step) < 1.0:
            step = 1.0 if step >= 0 else -1.0
        val = min(hi, max(lo, float(data[name]) + step))
        data[name] = int(round(val)) if is_int else round(val, 4)
        notes.append(f"{name}={data[name]}")
    mode = org_mode
    if org_mode in ("static", "dynamic") and rng.random() < 0.2:
        mode = "static" if org_mode == "dynamic" else "dynamic"
        notes.append(f"org_mode={mode}")
    data["enabled"] = True
    return CEOPolicyGene.from_dict(data).to_dict(), mode, "ceo_mutate " + ", ".join(notes)


def org_crossover(a_roles: Sequence[Mapping[str, Any]], b_roles: Sequence[Mapping[str, Any]],
                  a_pol: Mapping[str, Any], b_pol: Mapping[str, Any], rng: random.Random
                  ) -> Tuple[List[Dict[str, Any]], Dict[str, Any], str]:
    merged: Dict[str, Dict[str, Any]] = {}
    for r in list(a_roles) + list(b_roles):
        rid = r.get("role_id")
        if rid not in merged or int(r.get("uses") or 0) > int(merged[rid].get("uses") or 0):
            merged[rid] = dict(r)
    roles = list(merged.values())
    if len(roles) > MAX_ROLE_LIBRARY:
        gens = [r for r in roles if r.get("is_generalist")]
        specs = sorted((r for r in roles if not r.get("is_generalist")),
                       key=lambda r: (-float(r.get("mean_delta_u") or 0), r.get("role_id")))
        roles = gens + specs[: MAX_ROLE_LIBRARY - len(gens)]
    pa = CEOPolicyGene.from_dict(dict(a_pol or {})).to_dict()
    pb = CEOPolicyGene.from_dict(dict(b_pol or {})).to_dict()
    child = {k: (pa[k] if rng.random() < 0.5 else pb[k]) for k in pa}
    child["enabled"] = True
    return roles, CEOPolicyGene.from_dict(child).to_dict(), f"org_crossover ({len(roles)} roles)"


def random_org(base_roles: Sequence[Mapping[str, Any]], base_policy: Mapping[str, Any],
               rng: random.Random) -> Tuple[List[Dict[str, Any]], Dict[str, Any], str]:
    """Random-search control: keep each specialist with p=0.5 (generalists always), uniform CEO genes, random mode."""
    roles = [dict(r) for r in base_roles if r.get("is_generalist") or rng.random() < 0.5]
    data = CEOPolicyGene.from_dict(dict(base_policy or {})).to_dict()
    for name in CEO_EVOLVABLE:
        lo, hi, is_int = CEO_POLICY_BOUNDS[name]
        v = rng.uniform(lo, hi)
        data[name] = int(round(v)) if is_int else round(v, 4)
    data["enabled"] = True
    return roles, CEOPolicyGene.from_dict(data).to_dict(), rng.choice(["static", "dynamic"])


def generalists_present(roles: Sequence[Mapping[str, Any]]) -> bool:
    layers = {r.get("layer") for r in roles if r.get("is_generalist")}
    return set(LLM_LAYERS) <= layers


__all__ = ["aggregate_role_stats", "apply_role_stats", "org_digest", "redteam_digest", "OrgMutator",
           "role_remove", "ceo_mutate", "org_crossover", "random_org", "generalists_present"]
