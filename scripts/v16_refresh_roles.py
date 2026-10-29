#!/usr/bin/env python3
"""V16 Phase 1: refresh the role library so every role staffs one layer of the hierarchical harness.

Steps:
  1. Build a redacted failure digest from V15 *training and validation* records only (aggregate
     counts and rates; no task ids, repository names, file paths or test names). The test split is
     never read.
  2. Ask the LLM (Beam via the llm-d router) to rewrite the 10 V15 SWE-bench roles into
     layer-bound specialists keyed to the observed failure modes, with step-by-step playbooks.
  3. Gate every role text (leakage gate over train+val+test gold/test patches, ban list, schema and
     length limits). A failing role is regenerated up to 3 times, then dropped.
  4. Add one generalist per layer whose rendered prompt is exactly the V15 prompt (no added text).
  5. Write `configs/roles/v16_role_library.json` and `experiments/v16/role_refresh_report.md`.

  PYTHONPATH=. python3 scripts/v16_refresh_roles.py --vllm-url http://127.0.0.1:8000/v1
"""

from __future__ import annotations

import argparse
import collections
import glob
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from hae.evolution.role_gates import RoleGates, to_allele  # noqa: E402
from hae.genome.schema import MAX_PLAYBOOK_STEP_CHARS, MAX_PLAYBOOK_STEPS, CEOPolicyGene, RoleAllele  # noqa: E402
from hae.genome.role_mutation import MAX_BACKSTORY_CHARS, MAX_GOAL_CHARS, MAX_NAME_CHARS  # noqa: E402

SPLITS = os.path.join(REPO_ROOT, "data", "swebench", "v15_splits.json")
SEED_GENOME = os.path.join(REPO_ROOT, "configs", "genomes", "v15_seed_harness_firm_0.json")
DIGEST_SOURCES = [
    os.path.join(REPO_ROOT, "results", "v15", "evolve_2026-10-27", "evals"),
    os.path.join(REPO_ROOT, "results", "v15", "validation_2026-10-28", "evals"),
]
OUT_LIBRARY = os.path.join(REPO_ROOT, "configs", "roles", "v16_role_library.json")
OUT_REPORT = os.path.join(REPO_ROOT, "experiments", "v16", "role_refresh_report.md")

# The V15 persona strings, verbatim from hae/swebench/hierarchical_mcts.py. Generalists add no text
# to any prompt, so with a generalist staffed the harness runs exactly the V15 prompt.
GENERALISTS = {
    "recon": ("Layer-L1 Epistemic Reconnaissance & Reproduction Specialist",
              "Perform fast, surgical reconnaissance and return a distilled Reconnaissance Brief to Layer L0."),
    "architect": ("Layer-L0 Seed Principal Architect & AB-MCTS Tree Master",
                  "Formulate the verification contract and 3 orthogonal causal branches from a clean context."),
    "executor": ("Layer-L2 Scoped Branch Executor",
                 "Implement the branch hypothesis in a clean context window using `str_replace`, verify against the "
                 "reproduction script and `pytest`, and emit `submit`."),
    "refiner": ("Layer-L2 Clean-Context Delta-Refinement Specialist",
                "Refine the parent checkpoint's patch in place using `str_replace` to resolve the exact "
                "Validation/Falsifier Critique without breaking passing tests, then emit `submit`."),
    "falsifier": ("Layer-L1B Independent Adversarial Falsifier & API-Contract Auditor",
                  "Rigorously audit the candidate patch against every requirement, deprecation warning, subclass, and "
                  "edge case in the problem statement, and emit `submit` with `VERDICT: PASS` or `VERDICT: FAIL: <reason>`."),
}

LAYER_DESCRIPTIONS = {
    "architect": "L0, one LLM call per task. Reads the problem statement plus a deterministic reconnaissance brief and "
                 "writes a verification contract (invariants, edge cases, warning/message rules, sibling parity) and "
                 "3 causal branches (consumer_local, producer_upstream, codebase_symmetry), each with target files "
                 "and a plan.",
    "executor": "L2 GEN episode, multi-turn tool use (bash, view_file, str_replace, submit) from a clean HEAD. "
                "Implements one branch plan, runs the reproduction script and tests, submits.",
    "refiner": "L2 CONT episode, multi-turn tool use. Starts from a parent checkpoint and its validation/falsifier "
               "critique and edits the patch in place to fix the critique without breaking passing tests.",
    "falsifier": "L1B, multi-turn tool use after a candidate passes the gate. Writes an adversarial falsification "
                 "suite, may harden edge cases, and returns VERDICT: PASS or VERDICT: FAIL: <reason>.",
}

LOCI = ["consumer_local", "producer_upstream", "codebase_symmetry", "counterfactual_move37"]


# --------------------------------------------------------------------------- #
# 1. Redacted failure digest (train + val only)
# --------------------------------------------------------------------------- #

def _gold_files(splits: Dict[str, Any]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for split in ("train", "val"):
        with open(os.path.join(REPO_ROOT, splits["datasets"][split]), encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    row = json.loads(line)
                    out[row["instance_id"]] = len(re.findall(r"^diff --git ", row.get("patch", ""), flags=re.M))
    return out


def build_digest(splits: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    allowed = set(splits["train"]) | set(splits["val"])
    gold_nfiles = _gold_files(splits)
    c: collections.Counter = collections.Counter()
    stops: collections.Counter = collections.Counter()
    best_locus: Dict[str, collections.Counter] = {"resolved": collections.Counter(), "unresolved": collections.Counter()}
    for root in DIGEST_SOURCES:
        for rec_path in glob.glob(os.path.join(root, "*", "*", "record.json")):
            iid = os.path.basename(os.path.dirname(rec_path))
            if iid not in allowed:
                continue  # never read anything outside train/val
            grade_path = os.path.join(os.path.dirname(rec_path), "grade.json")
            try:
                rec = json.load(open(rec_path, encoding="utf-8"))
                grade = json.load(open(grade_path, encoding="utf-8")) if os.path.exists(grade_path) else {}
            except Exception:
                continue
            swe = rec.get("swebench") or {}
            resolved = bool(grade.get("resolved"))
            c["runs"] += 1
            c["resolved"] += resolved
            stops[str(swe.get("stop") or "unknown")] += 1
            if swe.get("resolved_by_self_oracle") and not resolved:
                c["self_oracle_pass_but_hidden_tests_fail"] += 1
            if not resolved and int(grade.get("f2p_passed") or 0) > 0:
                c["partial_f2p"] += 1
            if not int(swe.get("patch_chars") or 0):
                c["empty_patch"] += 1
            agent_nfiles = len(swe.get("changed_files") or [])
            if not resolved and gold_nfiles.get(iid, 0) > max(1, agent_nfiles):
                c["unresolved_reference_fix_touches_more_files"] += 1
            tree = {}
            for s in rec.get("epistemic_searches") or []:
                tree = (s.get("stats") or {}).get("mcts_tree") or tree
            nodes = {k: v for k, v in tree.items() if k != "root"}
            gen = [v for v in nodes.values() if v.get("parent_id") == "root"]
            cont = [v for v in nodes.values() if v.get("parent_id") not in (None, "root")]
            c["gen_nodes"] += len(gen)
            c["cont_nodes"] += len(cont)
            c["gen_repro_not_ok"] += sum(1 for v in gen if not v.get("repro_ok"))
            c["nodes_repo_tests_regressed"] += sum(1 for v in nodes.values() if v.get("repro_ok") and not v.get("repo_tests_ok"))
            c["nodes_falsifier_reject_after_gate"] += sum(
                1 for v in nodes.values() if v.get("repro_ok") and v.get("repo_tests_ok") and v.get("falsifier_ok") is False)
            for v in cont:
                parent = nodes.get(v.get("parent_id"), {})
                if float(v.get("value") or 0) <= float(parent.get("value") or 0):
                    c["cont_no_improvement"] += 1
            c["certified_nodes_in_unresolved_runs"] += 0 if resolved else sum(1 for v in nodes.values() if v.get("certified"))
            if nodes:
                best = max(nodes.values(), key=lambda v: float(v.get("value") or 0))
                locus = str(best.get("locus") or "").replace("cont_", "")
                best_locus["resolved" if resolved else "unresolved"][locus if locus in LOCI else "other"] += 1
            log = rec_path.replace(f"{os.sep}{iid}{os.sep}record.json", f"{os.sep}{iid}.stdout.log")
            try:
                with open(log, encoding="utf-8", errors="replace") as fh:
                    txt = fh.read()
                if "soft wall-clock limit reached" in txt:
                    c["runs_hit_wall_clock"] += 1
            except OSError:
                pass
    n = max(1, c["runs"])
    pct = lambda k: f"{100.0 * c[k] / n:.0f}%"  # noqa: E731
    lines = [
        f"Runs analysed: {c['runs']} (V15 training + validation; all candidate harnesses). Resolved: {pct('resolved')}.",
        "Stop reasons: " + ", ".join(f"{k} {100.0 * v / n:.0f}%" for k, v in stops.most_common()),
        f"Self-check passed (reproduction + repo tests) but hidden tests failed: {pct('self_oracle_pass_but_hidden_tests_fail')} of runs.",
        f"Unresolved with some hidden fail-to-pass tests passing (partial fix): {pct('partial_f2p')} of runs.",
        f"Unresolved where the reference fix touched more files than the agent's patch: {pct('unresolved_reference_fix_touches_more_files')} of runs.",
        f"Empty final patch: {pct('empty_patch')} of runs. Runs that hit the wall-clock limit: {pct('runs_hit_wall_clock')}.",
        f"GEN episodes whose patch did not make the reproduction pass: {c['gen_repro_not_ok']}/{c['gen_nodes']}.",
        f"Episodes where reproduction passed but existing repo tests regressed: {c['nodes_repo_tests_regressed']}/{c['gen_nodes'] + c['cont_nodes']}.",
        f"Falsifier rejected a candidate that had passed reproduction + tests: {c['nodes_falsifier_reject_after_gate']} episodes.",
        f"CONT refinements that did not improve on their parent: {c['cont_no_improvement']}/{c['cont_nodes']}.",
        f"Nodes certified as solved in runs that the hidden tests marked unresolved: {c['certified_nodes_in_unresolved_runs']}.",
        "Branch locus of the best node: resolved " + json.dumps(dict(best_locus["resolved"]))
        + ", unresolved " + json.dumps(dict(best_locus["unresolved"])) + ".",
    ]
    return "\n".join(f"- {ln}" for ln in lines), {"counts": dict(c), "stops": dict(stops),
                                                  "best_locus": {k: dict(v) for k, v in best_locus.items()}}


# --------------------------------------------------------------------------- #
# 2-3. LLM rewrite + gates
# --------------------------------------------------------------------------- #

ROLE_SCHEMA_TEXT = (
    '{"roles": [{"layer": "architect|executor|refiner|falsifier", "name": "...", "goal": "...", '
    '"backstory": "...", "playbook": ["imperative step", ...], "failure_modes": ["snake_case_tag", ...], '
    '"domain_tags": ["lowercase_word", ...], "parent_role_id": "<one of the current role ids or empty>", '
    '"temperature": 0.2}]}'
)


def build_prompt(current_roles: List[Dict[str, Any]], digest: str) -> Tuple[str, str]:
    system = (
        "You design the staff of an automated software-repair organization. You write role definitions that are "
        "injected into the system prompt of an LLM agent for one layer of a fixed harness. Roles must be "
        "domain-general: never name a repository, project, library, file path, test name, issue number or a specific "
        "function. Respond with one JSON object only."
    )
    cur = "\n".join(f"- {r['role_id']}: {r['name']} — {r['goal']}" for r in current_roles)
    layers = "\n".join(f"- {k}: {v}" for k, v in LAYER_DESCRIPTIONS.items())
    user = (
        "## Harness layers you can staff\n" + layers + "\n\n"
        "Each layer already has a generalist (the current default prompt). You add specialists that a CEO policy can "
        "hire instead of the generalist when the task or the situation calls for it.\n\n"
        "## Current role library (written for a different, flat harness)\n" + cur + "\n\n"
        "## What went wrong in past runs (aggregate statistics, no task details)\n" + digest + "\n\n"
        "## Your task\n"
        "Write 8 to 11 specialists in total: 2 or 3 per layer for architect, executor, refiner and falsifier. Define "
        "each specialist by the failure mode it prevents (for example: fix misses a second file that must change "
        "together, regression in sibling behaviour, wrong root-cause location, submitting before the reproduction "
        "really fails then passes, missed edge case, missing or wrong warning/exception class, over-fitting to the "
        "self-written reproduction), not by technology area. Where a current role's expertise fits, rewrite it and "
        "set `parent_role_id`; otherwise leave it empty.\n"
        f"Constraints per role: name <= {MAX_NAME_CHARS} chars; goal <= {MAX_GOAL_CHARS} chars; backstory <= "
        f"{MAX_BACKSTORY_CHARS} chars; playbook 3-{MAX_PLAYBOOK_STEPS} imperative steps, each <= "
        f"{MAX_PLAYBOOK_STEP_CHARS} chars, concrete and checkable (what to run or inspect, what to compare); "
        "failure_modes 1-4 snake_case tags; domain_tags 5-12 single lowercase words that typically appear in a "
        "GitHub issue describing the kind of problem this specialist handles (e.g. deprecated, warning, subclass, "
        "keyword, argument, parse, format, unicode, timezone, precision, repr, config, regression, inherit) — these "
        "are matched against the issue text to decide when to hire the role. Executor specialists may also include "
        f"one branch locus tag from {LOCI} to be preferred for that branch type. temperature between 0.0 and 0.6.\n"
        "Playbooks must fit the layer's tools: the architect only writes JSON (no tools); executors, refiners and "
        "falsifiers use bash, view_file, str_replace and submit.\n\n"
        "Output schema:\n" + ROLE_SCHEMA_TEXT
    )
    return system, user


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)
    m = re.search(r"\{.*\}", text, flags=re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def generalist_alleles() -> List[RoleAllele]:
    return [RoleAllele(name=name, goal=goal, layer=layer, is_generalist=True, kind="both", temperature=0.2,
                       origin="v16-generalist:v15-prompt", domain_tags=[])
            for layer, (name, goal) in GENERALISTS.items()]


def v16_ceo_policy() -> CEOPolicyGene:
    """CEO policy for the harness. Recruitment fires after 2 stalled episodes with a 5-role team:
    sigmoid(0 + 1.0 * 2 - 0.4 * 5) = 0.5."""
    return CEOPolicyGene(enabled=True, min_initial_roles=4, max_initial_roles=5, max_active_roles=8,
                         headcount_lambda=0.05, exploration_c=0.25, optimistic_prior=0.3, stall_delta_u=0.05,
                         stall_moves=2, recruit_w_stall=1.0, recruit_w_unmatched=0.0, recruit_w_headcount=0.4,
                         recruit_bias=0.0, recruit_cooldown_moves=1, recruit_mode="both", temperature=0.0)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--vllm-url", default="http://127.0.0.1:8000/v1")
    ap.add_argument("--vllm-model", default="reflection/Beam-1.0-501B-A23B-NVFP4")
    ap.add_argument("--dry-run", action="store_true", help="Print the digest and prompt; no LLM call")
    args = ap.parse_args()

    splits = json.load(open(SPLITS, encoding="utf-8"))
    seed = json.load(open(SEED_GENOME, encoding="utf-8"))
    current = seed["role_library"]
    digest, digest_raw = build_digest(splits)
    system, user = build_prompt(current, digest)
    print(digest)
    if args.dry_run:
        print("\n" + system + "\n\n" + user)
        return 0

    os.environ.setdefault("LLM_PROVIDER", "vllm")
    os.environ["VLLM_BASE_URL"] = args.vllm_url
    os.environ["VLLM_MODEL"] = args.vllm_model
    from hae.infra.llm import call_llm

    def llm(sys_p: str, user_p: str) -> str:
        return call_llm(user_p, model_name=args.vllm_model, system_instruction=sys_p, provider="vllm",
                        temperature=0.4, max_tokens=12000)

    gates = RoleGates(splits, REPO_ROOT)
    data = None
    for _ in range(3):
        data = _extract_json(llm(system, user))
        if data and isinstance(data.get("roles"), list) and data["roles"]:
            break
    if not data:
        print("LLM did not return a role list", file=sys.stderr)
        return 1

    accepted: List[Tuple[Dict[str, Any], List[str], int]] = []
    rejected: List[Tuple[Dict[str, Any], List[str]]] = []
    for role in data["roles"]:
        errs = gates.check(role)
        tries = 1
        while errs and tries < 3:
            fix_user = (
                "Rewrite this role so it passes these checks: " + "; ".join(errs) + ". Keep its layer and intent. "
                "Never mention repositories, projects, libraries, file names or specific identifiers.\n\n"
                + json.dumps(role) + "\n\nReturn {\"roles\": [<the one fixed role>]}."
            )
            fixed = _extract_json(llm(system, fix_user)) or {}
            role = (fixed.get("roles") or [role])[0]
            errs = gates.check(role)
            tries += 1
        (accepted if not errs else rejected).append((role, errs, tries) if not errs else (role, errs))

    names = set()
    specialists: List[RoleAllele] = []
    for role, _, _ in accepted:
        a = to_allele(role, generation=0)
        if a.name.lower() in names or a.name in {g[0] for g in GENERALISTS.values()}:
            continue
        names.add(a.name.lower())
        specialists.append(a)
    library = generalist_alleles() + specialists

    lib_doc = {
        "version": "v16-role-refresh",
        "note": "Generalists reproduce the V15 prompts exactly (they add no text). Specialists append an "
                "'Organization assignment' block to their layer's system prompt (hae/swebench/org_binding.py).",
        "role_library": [r.to_dict() for r in library],
        "ceo_policy": v16_ceo_policy().to_dict(),
        "digest": digest_raw,
    }
    os.makedirs(os.path.dirname(OUT_LIBRARY), exist_ok=True)
    with open(OUT_LIBRARY, "w", encoding="utf-8") as fh:
        json.dump(lib_doc, fh, indent=1, sort_keys=True)
        fh.write("\n")

    by_id = {r["role_id"]: r for r in current}
    rep = ["# V16 role refresh report", "",
           f"Generated by `scripts/v16_refresh_roles.py` with `{args.vllm_model}`. Library: "
           f"`configs/roles/v16_role_library.json` ({len(library)} roles: {len(GENERALISTS)} generalists, "
           f"{len(specialists)} specialists).", "",
           "## Failure digest given to the LLM (V15 train + validation only)", "", digest, "",
           "## Gates", "",
           "Every role text (name, goal, backstory, playbook steps) passed: the V15 leakage gate built over the gold "
           "and test patches of train + val + test, a ban list of all instance ids and repository names, a file-path "
           "check, schema validation and length limits. Failing roles were regenerated up to 3 times.", "",
           f"Rejected after 3 tries: {len(rejected)}" + ("" if not rejected else " — " + "; ".join(
               f"{r.get('name')} ({', '.join(e)})" for r, e in rejected)), "",
           "## Generalists (exact V15 prompts)", ""]
    rep += [f"- **{layer}**: {name}" + (" — inert: the V15 recon layer is deterministic and makes no LLM call."
                                          if layer == "recon" else "")
            for layer, (name, _) in GENERALISTS.items()]
    rep += ["", "## Specialists", ""]
    for a in specialists:
        parent = a.origin.split(":", 1)[1]
        before = by_id.get(parent)
        rep += [f"### {a.name} ({a.layer})", "",
                f"- Before: {before['name']} — {before['goal']}" if before else "- Before: (new role)",
                f"- Goal: {a.goal}", f"- Failure modes: {', '.join(a.failure_modes)}",
                f"- Hiring tags: {', '.join(a.domain_tags)}", "- Playbook:"]
        rep += [f"  {i}. {s}" for i, s in enumerate(a.playbook, 1)]
        rep += [""]
    dropped = [r["name"] for r in current if not any(a.origin.endswith(":" + r["role_id"]) for a in specialists)]
    rep += ["## V15 roles not carried over", "", ", ".join(dropped) if dropped else "(none)", ""]
    os.makedirs(os.path.dirname(OUT_REPORT), exist_ok=True)
    with open(OUT_REPORT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(rep))
    print(f"wrote {OUT_LIBRARY} ({len(library)} roles) and {OUT_REPORT}; rejected {len(rejected)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
