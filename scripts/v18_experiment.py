#!/usr/bin/env python3
"""V18 Experiment Runner: Verifier Calibration + Compute-Matched Showdown on Fresh N=80 SWE-bench Verified.

Arms evaluated on the fresh, offline-screened `eval_80` (`data/swebench/v18_splits.json["test"]`):
  1. `Flat_1x_V18`: Single flat ReAct rollout (`v12_agentic` with V18 compound-turn + `</rfl:parameter>`
     substrate fixes, `budget_moves=120`, ~150k tokens/run). Evaluated with 3 independent seeds (`__r1..__r3`).
  2. `Flat_3x_Select`: Compute-matched 3x parallel `Flat_1x_V18` rollouts (~450k total tokens) +
     blind Candidate-Pool Ranker / Selector over the 3 candidate patches per task.
  3. `V18_Collab`: Hierarchical Collaborative Harness (`V18_COLLAB_REDTEAM`: 4 scoped sub-blackboards,
     `entry_char_limit=1800`, `auto_index_views=True`, `pre_huddle_rounds=1` with contract-override
     authority, `synthesis_roundtable=True`, `pool_ranker=True`, ~420k tokens/run).
  4. `V18_Evolved`: Hierarchical Collaborative Harness with the V17 evolved specialist/falsifier role
     library (`evo_g3_00`) + offline-calibrated Red-Team Ranker weights trained on 2,525 container-graded
     candidate patches (`results/v18/ranker_calibration.json`).

Subcommands:
  calibrate-ranker  Offline evaluation & calibration of the Red-Team Verifier/Ranker on all 2,525
                    container-graded candidate patches from V17 (`TP`, `FP`, `TN`, `FN`, Precision,
                    Recall, Specificity, F1, AUROC, and Top-1 Multi-Candidate Selection Accuracy).
  arms              Run `V18_Collab` and/or `V18_Evolved` on `--split` (`test`, `eval_24_stratified`, `val`).
  flat              Run `Flat_1x_V18` (`--runs 3`) on `--split` and synthesize `Flat_3x_Select`.
  select-flat3x     Recompute `Flat_3x_Select` from completed `Flat_1x_V18__r1..__rN` rollouts.
"""

from __future__ import annotations

import argparse
import collections
import glob
import hashlib
import json
import os
import subprocess
import sys
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))

import v15_evolve as v15  # noqa: E402
from hae.evolution.evaluator import GenomeEvaluator  # noqa: E402
from hae.evolution.genome import HarnessGenome, TaskResult  # noqa: E402
from hae.evolution.workspace import WorktreeManager  # noqa: E402
from hae.swebench.harness_knobs import V17_HIGH_BUDGET_KNOBS  # noqa: E402
from hae.swebench.org_binding import (  # noqa: E402
    V18_COLLAB_REDTEAM,
    score_candidate_patch,
)

SEED_GENOME = os.path.join(REPO_ROOT, "configs", "genomes", "v16_seed_harness.json")
V12_GENOME = os.path.join(REPO_ROOT, "configs", "genomes", "v12_agentic_swebench_firm_0.json")
LIBRARY = os.path.join(REPO_ROOT, "configs", "roles", "v16_role_library.json")
CHAMPION = (os.path.join(REPO_ROOT, "results", "v15", "evolve_2026-10-27"), "evo_g5_03")
V17_EVOLVED = (os.path.join(REPO_ROOT, "results", "v17", "evolve_2026-10-30"), "evo_g3_00")
DEFAULT_SPLITS = os.path.join(REPO_ROOT, "data", "swebench", "v18_splits.json")
DEFAULT_CALIBRATION = os.path.join(REPO_ROOT, "results", "v18", "ranker_calibration.json")
WORKTREE_ROOT = os.path.expanduser("~/hae_v18_worktrees")

# Compute-matched V18 hierarchical budget knobs (~400k-450k tokens, matching 3x Flat_1x_V18)
V18_MATCHED_BUDGET_KNOBS: Dict[str, Any] = {
    **V17_HIGH_BUDGET_KNOBS,
    "min_budget_moves": 180,
    "max_episodes": 6,
    "gen1_turns_min": 14,
    "gen1_turns_max": 32,
    "genk_turns_min": 12,
    "genk_turns_max": 26,
    "cont_turns_min": 10,
    "cont_turns_max": 20,
    "falsifier_max_turns": 10,
}


def _load_json(path: str) -> Any:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _dump_json(path: str, data: Any) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)


def head_ref() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True).strip()


def load_splits(path: str = DEFAULT_SPLITS) -> Dict[str, Any]:
    return _load_json(path if os.path.exists(path) else v15.SPLITS)


def dataset_map(splits: Dict[str, Any]) -> Dict[str, str]:
    m: Dict[str, str] = {}
    for rel in sorted(set(splits["datasets"].values())):
        ds = os.path.join(REPO_ROOT, rel)
        if not os.path.exists(ds):
            continue
        with open(ds, encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    row = json.loads(line)
                    iid = row.get("instance_id")
                    if iid:
                        m[str(iid)] = ds
    for split in ("train", "val", "test", "eval_80", "eval_24_stratified"):
        if split in splits and split in splits.get("datasets", {}):
            ds = os.path.join(REPO_ROOT, splits["datasets"][split])
            for t in splits.get(split, []):
                m.setdefault(t, ds)
    return m


def champion_patch() -> str:
    st_path = os.path.join(CHAMPION[0], "state.json")
    if os.path.exists(st_path):
        st = _load_json(st_path)
        return st["genomes"][CHAMPION[1]]["patch"]
    return ""


def compute_auroc(scores_and_labels: Sequence[Tuple[float, bool]]) -> float:
    """Exact Wilcoxon-Mann-Whitney AUROC with tie handling."""
    pos = [s for s, y in scores_and_labels if y]
    neg = [s for s, y in scores_and_labels if not y]
    if not pos or not neg:
        return 0.5
    wins = 0.0
    for sp in pos:
        for sn in neg:
            if sp > sn:
                wins += 1.0
            elif sp == sn:
                wins += 0.5
    return round(wins / float(len(pos) * len(neg)), 4)


def _extract_patch_features(eval_dir: str, lv: Mapping[str, Any]) -> Dict[str, Any]:
    """Extract candidate patch features from `verdicts_grade.json` + `verdict_<sha>.pred.json` (never peeking at ground truth)."""
    sha = str(lv.get("diff_sha") or "")
    diff_chars = int(lv.get("diff_chars") or 0)
    pred_path = os.path.join(eval_dir, f"verdict_{sha}.pred.json")
    diff_lines = max(1, diff_chars // 45)
    if os.path.exists(pred_path):
        try:
            pred = _load_json(pred_path)
            patch = str(pred.get("model_patch") or "")
            if patch:
                diff_lines = max(
                    1,
                    sum(
                        1 for ln in patch.splitlines()
                        if ln[:1] in "+-" and not ln.startswith(("+++", "---"))
                    ),
                )
        except Exception:
            pass
    panel = list(lv.get("panel") or [])
    if panel:
        pass_rate = sum(1.0 for p in panel if p.get("passed")) / float(len(panel))
        ran_ev = sum(
            1.0
            for p in panel
            if "did not execute any bash verification test" not in str(p.get("critique") or "")
        ) / float(len(panel))
        has_expanded_fail = any(
            "Expanded evidence_tests pytest" in str(p.get("critique") or "")
            and not p.get("passed")
            for p in panel
        )
    else:
        pass_rate = 1.0 if lv.get("falsifier_ok") else 0.0
        ran_ev = 1.0
        has_expanded_fail = False

    return {
        "node_id": str(lv.get("node") or "b1"),
        "episode": int(lv.get("episode") or 1),
        "diff_sha": sha,
        "diff_chars": diff_chars,
        "diff_lines": diff_lines,
        # Every candidate reaching the Red-Team falsifier in V17 already passed compile, repro_ok, and repo_tests_ok!
        "repro_ok": True,
        "repo_tests_ok": True,
        "compile_ok": True,
        "static_ok": True,
        "expanded_tests_ok": not has_expanded_fail,
        "new_test_failures": 1 if has_expanded_fail else 0,
        "panel_pass_rate": round(pass_rate, 4),
        "ran_evidence": round(ran_ev, 4),
        "is_synthesis": str(lv.get("node") or "") == "b_synth",
        "falsifier_ok": bool(lv.get("falsifier_ok")),
        "ground_truth_resolved": bool(lv.get("ground_truth_resolved")),
    }


def calibrate_ranker(out_path: str = DEFAULT_CALIBRATION) -> Dict[str, Any]:
    """Evaluate & calibrate the Red-Team Verifier/Ranker on all container-graded candidate patches from V17."""
    sources = {
        "train_arms": sorted(glob.glob(os.path.join(REPO_ROOT, "results/v17/train_arms_2026-10-30/evals/*/*/verdicts_grade.json"))),
        "evolve": sorted(glob.glob(os.path.join(REPO_ROOT, "results/v17/evolve_2026-10-30/evals/*/*/verdicts_grade.json"))),
        "random": sorted(glob.glob(os.path.join(REPO_ROOT, "results/v17/random_2026-10-30/evals/*/*/verdicts_grade.json"))),
    }

    task_runs: List[Dict[str, Any]] = []
    all_patches: List[Dict[str, Any]] = []
    by_source_counts: Dict[str, Dict[str, int]] = {}

    for src_name, paths in sources.items():
        tp = fp = tn = fn = 0
        n_runs = 0
        for p in paths:
            try:
                vg = _load_json(p)
            except Exception:
                continue
            lvs = list(vg.get("labelled_verdicts") or [])
            if not lvs:
                continue
            n_runs += 1
            eval_dir = os.path.dirname(p)
            cands = [_extract_patch_features(eval_dir, lv) for lv in lvs]
            for c in cands:
                c["source"] = src_name
                all_patches.append(c)
                f_ok = c["falsifier_ok"]
                gt = c["ground_truth_resolved"]
                if f_ok and gt:
                    tp += 1
                elif f_ok and not gt:
                    fp += 1
                elif (not f_ok) and (not gt):
                    tn += 1
                else:
                    fn += 1
            task_runs.append({
                "source": src_name,
                "eval_dir": eval_dir,
                "instance_id": vg.get("instance_id", ""),
                "candidates": cands,
            })
        by_source_counts[src_name] = {
            "task_runs": n_runs,
            "patches": tp + fp + tn + fn,
            "TP": tp,
            "FP": fp,
            "TN": tn,
            "FN": fn,
        }

    total_tp = sum(d["TP"] for d in by_source_counts.values())
    total_fp = sum(d["FP"] for d in by_source_counts.values())
    total_tn = sum(d["TN"] for d in by_source_counts.values())
    total_fn = sum(d["FN"] for d in by_source_counts.values())
    total_n = total_tp + total_fp + total_tn + total_fn

    precision = round(total_tp / max(1, total_tp + total_fp), 4)
    recall = round(total_tp / max(1, total_tp + total_fn), 4)
    specificity = round(total_tn / max(1, total_tn + total_fp), 4)
    f1 = round(2 * precision * recall / max(1e-9, precision + recall), 4)
    false_pass_rate = round(total_fp / max(1, total_tp + total_fp), 4)
    false_rejection_rate = round(total_fn / max(1, total_tp + total_fn), 4)

    # Multi-candidate task-runs where >= 2 distinct patches exist and at least 1 is ground-truth resolved
    multi_resolvable = [
        tr for tr in task_runs
        if len({c["diff_sha"] for c in tr["candidates"]}) >= 2
        and any(c["ground_truth_resolved"] for c in tr["candidates"])
        and not all(c["ground_truth_resolved"] for c in tr["candidates"])
    ]
    all_resolvable_runs = [
        tr for tr in task_runs
        if any(c["ground_truth_resolved"] for c in tr["candidates"])
    ]

    def _eval_weights(w_fals: float, w_simp: float, w_syn: float) -> Tuple[float, float, float]:
        cfg = {
            "ranker_falsifier_weight": w_fals,
            "ranker_simplicity_weight": w_simp,
            "ranker_synthesis_bonus": w_syn,
        }
        scored_pairs = [
            (score_candidate_patch(c, cfg), c["ground_truth_resolved"])
            for c in all_patches
        ]
        auroc = compute_auroc(scored_pairs)

        # Top-1 selection on discordant multi-candidate task-runs
        disc_wins = 0
        for tr in multi_resolvable:
            best_c = max(
                tr["candidates"],
                key=lambda c: (score_candidate_patch(c, cfg), c["episode"], -c["diff_lines"]),
            )
            if best_c["ground_truth_resolved"]:
                disc_wins += 1
        disc_acc = round(disc_wins / max(1, len(multi_resolvable)), 4)

        # Effective task resolution rate across all resolvable runs when using ranker vs binary gate
        all_wins = 0
        for tr in all_resolvable_runs:
            best_c = max(
                tr["candidates"],
                key=lambda c: (score_candidate_patch(c, cfg), c["episode"], -c["diff_lines"]),
            )
            if best_c["ground_truth_resolved"]:
                all_wins += 1
        all_acc = round(all_wins / max(1, len(all_resolvable_runs)), 4)
        return disc_acc, auroc, all_acc

    # Baseline V17 binary gate selection on all_resolvable_runs and multi_resolvable:
    # Binary gate only accepts candidates with falsifier_ok=True (or falls back to last episode if none pass)
    bin_disc_wins = 0
    for tr in multi_resolvable:
        passed = [c for c in tr["candidates"] if c["falsifier_ok"]]
        chosen = passed[-1] if passed else tr["candidates"][-1]
        if chosen["ground_truth_resolved"]:
            bin_disc_wins += 1
    bin_disc_acc = round(bin_disc_wins / max(1, len(multi_resolvable)), 4)

    bin_gate_kept_wins = 0
    for tr in all_resolvable_runs:
        passed = [c for c in tr["candidates"] if c["falsifier_ok"] and c["ground_truth_resolved"]]
        if passed:
            bin_gate_kept_wins += 1
    bin_gate_recall_on_runs = round(bin_gate_kept_wins / max(1, len(all_resolvable_runs)), 4)

    bin_auroc = compute_auroc([(1.0 if c["falsifier_ok"] else 0.0, c["ground_truth_resolved"]) for c in all_patches])
    default_disc_acc, default_auroc, default_all_acc = _eval_weights(0.45, 0.15, 0.08)

    # Grid search over (w_fals, w_simp, w_syn)
    best_key = (-1.0, -1.0, -1.0)
    best_weights = {"ranker_falsifier_weight": 0.45, "ranker_simplicity_weight": 0.15, "ranker_synthesis_bonus": 0.08}
    for wf in (0.35, 0.45, 0.55, 0.65):
        for ws in (0.05, 0.10, 0.15, 0.25):
            for wy in (0.08, 0.12, 0.05):
                d_acc, roc, a_acc = _eval_weights(wf, ws, wy)
                key = (a_acc, d_acc, roc)
                if key > best_key:
                    best_key = key
                    best_weights = {
                        "ranker_falsifier_weight": wf,
                        "ranker_simplicity_weight": ws,
                        "ranker_synthesis_bonus": wy,
                    }

    cal_disc_acc, cal_auroc, cal_all_acc = _eval_weights(
        best_weights["ranker_falsifier_weight"],
        best_weights["ranker_simplicity_weight"],
        best_weights["ranker_synthesis_bonus"],
    )

    report = {
        "total_task_runs": len(task_runs),
        "total_labelled_patches": total_n,
        "by_source": by_source_counts,
        "confusion_matrix": {
            "TP": total_tp,
            "FP": total_fp,
            "TN": total_tn,
            "FN": total_fn,
            "precision": precision,
            "recall": recall,
            "specificity": specificity,
            "f1": f1,
            "false_pass_rate": false_pass_rate,
            "false_rejection_rate": false_rejection_rate,
        },
        "binary_gate_vs_ranker": {
            "discordant_multi_candidate_runs": len(multi_resolvable),
            "all_resolvable_runs": len(all_resolvable_runs),
            "v17_binary_gate_auroc": bin_auroc,
            "v17_binary_gate_run_recall": bin_gate_recall_on_runs,
            "v17_binary_gate_discordant_top1_acc": bin_disc_acc,
            "v18_default_ranker_auroc": default_auroc,
            "v18_default_ranker_all_resolvable_top1_acc": default_all_acc,
            "v18_default_ranker_discordant_top1_acc": default_disc_acc,
            "v18_calibrated_ranker_auroc": cal_auroc,
            "v18_calibrated_ranker_all_resolvable_top1_acc": cal_all_acc,
            "v18_calibrated_ranker_discordant_top1_acc": cal_disc_acc,
        },
        "calibrated_weights": best_weights,
    }
    _dump_json(out_path, report)
    return report


def arm_genome(arm: str, calibration_path: str = DEFAULT_CALIBRATION) -> HarnessGenome:
    lib = _load_json(LIBRARY)
    knobs = dict(V18_MATCHED_BUDGET_KNOBS)
    patch = champion_patch()
    roles = lib["role_library"]
    ceo_policy = lib["ceo_policy"]
    rt = dict(V18_COLLAB_REDTEAM)

    if arm == "V18_Collab":
        pass
    elif arm == "V18_Evolved":
        # Load evolved roles & CEO policy from V17 evolution champion (`evo_g3_00`) + calibrated ranker weights
        v17_st_path = os.path.join(V17_EVOLVED[0], "state.json")
        if os.path.exists(v17_st_path):
            v17_st = _load_json(v17_st_path)
            g_evo = v17_st.get("genomes", {}).get(V17_EVOLVED[1], {})
            if g_evo.get("roles"):
                roles = g_evo["roles"]
            if g_evo.get("ceo_policy"):
                ceo_policy = g_evo["ceo_policy"]
        if os.path.exists(calibration_path):
            cal = _load_json(calibration_path)
            rt.update(cal.get("calibrated_weights") or {})
    else:
        raise ValueError(f"Unknown V18 hierarchical arm {arm!r} (expected 'V18_Collab' or 'V18_Evolved')")

    return HarnessGenome(
        genome_id=arm,
        knobs=knobs,
        patch=patch,
        origin=f"arm-{arm}",
        org_mode="dynamic",
        roles=roles,
        ceo_policy=ceo_policy,
        redteam=rt,
    )


def make_evaluator(
    args: argparse.Namespace,
    out_root: str,
    wt: WorktreeManager,
    splits: Dict[str, Any],
    pod_tag: str,
    seed_path: str = SEED_GENOME,
    label_verdicts: bool = False,
) -> GenomeEvaluator:
    return GenomeEvaluator(
        out_root=out_root,
        seed_genome_path=seed_path,
        dataset_by_task=dataset_map(splits),
        worktrees=wt,
        vllm_url=args.vllm_url,
        vllm_model=args.vllm_model,
        context=args.context,
        workers=args.workers,
        budget_moves=args.budget_moves,
        max_iterations=args.max_iterations,
        instance_timeout_s=args.instance_timeout,
        pod_tag=pod_tag,
        label_verdicts=label_verdicts,
    )


def write_meta(out_root: str, extra: Dict[str, Any]) -> str:
    meta_path = os.path.join(out_root, "run_meta.json")
    if os.path.exists(meta_path):
        return _load_json(meta_path)["base_ref"]
    ref = head_ref()
    wt = WorktreeManager(REPO_ROOT, ref, WORKTREE_ROOT)
    _dump_json(meta_path, {"base_ref": ref, "infra_commit": wt.infra_commit(), **extra})
    return ref


def _load_state(path: str) -> Dict[str, Any]:
    return _load_json(path) if os.path.exists(path) else {}


def _replicas(genomes: List[HarnessGenome], runs: int) -> List[HarnessGenome]:
    if runs <= 1:
        return genomes
    out: List[HarnessGenome] = []
    for g in genomes:
        for r in range(1, runs + 1):
            d = g.to_dict()
            d.update(genome_id=f"{g.genome_id}__r{r}", results={})
            out.append(HarnessGenome.from_dict(d))
    return out


def score_flat_rollout_blind(eval_dir: str, cal_weights: Optional[Mapping[str, Any]] = None) -> Tuple[float, str, Dict[str, Any]]:
    """Score a single Flat ReAct rollout strictly using blind self-verification signals (NO `grade.json` access)."""
    rec_path = os.path.join(eval_dir, "record.json")
    pred_path = os.path.join(eval_dir, "prediction.json")
    patch = ""
    if os.path.exists(pred_path):
        try:
            patch = str(_load_json(pred_path).get("model_patch") or "").strip()
        except Exception:
            patch = ""
    if not patch or not os.path.exists(rec_path):
        return (-1.0, "", {"empty": True})

    rec = _load_json(rec_path)
    swe = rec.get("swebench") or {}
    best_cp = swe.get("best_checkpoint") or {}
    rk = list(best_cp.get("rank_key") or [0, 0, 0, 0, 0, 0, -99])
    # V12 rank_key: [gatekeeper_ok, repro&repo&audit, repro&repo, repro_ok, repo_tests_ok, static_ok, -new_test_failures]
    gatekeeper_ok = bool(rk[0]) if len(rk) > 0 else bool(swe.get("resolved_by_self_oracle"))
    audit_ok = bool(rk[1]) if len(rk) > 1 else gatekeeper_ok
    repro_ok = bool(rk[3]) if len(rk) > 3 else bool(swe.get("resolved_by_self_oracle"))
    repo_tests_ok = bool(rk[4]) if len(rk) > 4 else bool(swe.get("resolved_by_self_oracle"))
    static_ok = bool(rk[5]) if len(rk) > 5 else True
    new_fails = max(0, -int(rk[6])) if len(rk) > 6 else 0

    diff_lines = max(
        1,
        sum(
            1 for ln in patch.splitlines()
            if ln[:1] in "+-" and not ln.startswith(("+++", "---"))
        ),
    )
    diff_sha = hashlib.sha1(patch.encode("utf-8", errors="replace")).hexdigest()[:12]
    entry = {
        "repro_ok": repro_ok,
        "repo_tests_ok": repo_tests_ok,
        "compile_ok": True,
        "static_ok": static_ok,
        "expanded_tests_ok": gatekeeper_ok,
        "new_test_failures": new_fails,
        "panel_pass_rate": 1.0 if audit_ok else (0.5 if gatekeeper_ok else 0.0),
        "ran_evidence": 1.0 if (repro_ok or repo_tests_ok) else 0.0,
        "diff_lines": diff_lines,
        "is_synthesis": False,
    }
    score = score_candidate_patch(entry, cal_weights or V18_COLLAB_REDTEAM)
    return (score, diff_sha, entry)


def synthesize_flat_3x_select(out_root: str, tasks: Sequence[str], base_id: str = "Flat_1x_V18", runs: int = 3) -> HarnessGenome:
    """Construct `Flat_3x_Select` by blindly ranking the `runs` independent rollouts of `base_id` per task."""
    cal_weights = V18_COLLAB_REDTEAM
    if os.path.exists(DEFAULT_CALIBRATION):
        cal_weights = {**V18_COLLAB_REDTEAM, **(_load_json(DEFAULT_CALIBRATION).get("calibrated_weights") or {})}

    state_path = os.path.join(out_root, "state.json")
    saved = _load_state(state_path)
    rep_ids = [f"{base_id}__r{r}" for r in range(1, runs + 1)]

    sel_genome = HarnessGenome(
        genome_id="Flat_3x_Select",
        knobs={},
        patch="",
        origin=f"selector({','.join(rep_ids)})",
    )

    for tid in tasks:
        candidates: List[Tuple[float, int, str, int, Dict[str, Any]]] = []
        sha_counts: Dict[str, int] = collections.Counter()
        raw_items: List[Tuple[int, str, float, str, Dict[str, Any]]] = []

        for r_idx, rid in enumerate(rep_ids, 1):
            eval_dir = os.path.join(out_root, "evals", rid, tid)
            score, sha, meta = score_flat_rollout_blind(eval_dir, cal_weights)
            res_dict = ((saved.get(rid) or {}).get("results") or {}).get(tid)
            if res_dict is None:
                continue
            if sha:
                sha_counts[sha] += 1
            raw_items.append((r_idx, rid, score, sha, res_dict))

        if not raw_items:
            continue

        # Add majority-vote / consensus bonus (+0.25 if >=2 rollouts produced the exact same patch SHA)
        for r_idx, rid, score, sha, res_dict in raw_items:
            consensus_bonus = 0.25 if (sha and sha_counts[sha] >= 2) else 0.0
            candidates.append((round(score + consensus_bonus, 5), -r_idx, rid, sha_counts.get(sha, 0), res_dict))

        candidates.sort(key=lambda t: (t[0], t[3], t[1]), reverse=True)
        _, _, winning_rid, _, winning_res = candidates[0]

        # Sum tokens and elapsed time across all 3 rollouts so Flat_3x_Select reflects true 3x compute cost!
        total_tokens = sum(int(item[4].get("tokens", 0) or 0) for item in raw_items)
        total_elapsed = round(sum(float(item[4].get("elapsed_s", 0.0) or 0.0) for item in raw_items), 2)
        total_moves = sum(int(item[4].get("moves", 0) or 0) for item in raw_items)

        merged_res = dict(winning_res)
        merged_res["tokens"] = total_tokens
        merged_res["elapsed_s"] = total_elapsed
        merged_res["moves"] = total_moves
        sel_genome.results[tid] = TaskResult(**{
            k: v for k, v in merged_res.items() if k in TaskResult.__dataclass_fields__
        })

    saved["Flat_3x_Select"] = sel_genome.to_dict()
    _dump_json(state_path, saved)
    return sel_genome


def cmd_calibrate(args: argparse.Namespace) -> int:
    rep = calibrate_ranker(args.out)
    cm = rep["confusion_matrix"]
    bg = rep["binary_gate_vs_ranker"]
    print(
        f"=== V18 Offline Verifier & Ranker Calibration ({rep['total_labelled_patches']} Container-Graded Candidate Patches across {rep['total_task_runs']} Task-Runs) ===\n"
        f"Confusion Matrix (V17 Binary Red-Team Verdict vs. Container Ground Truth):\n"
        f"  TP={cm['TP']}  FP={cm['FP']}  TN={cm['TN']}  FN={cm['FN']}\n"
        f"  Precision={cm['precision']:.3f}  Recall={cm['recall']:.3f}  Specificity={cm['specificity']:.3f}  F1={cm['f1']:.3f}\n"
        f"  False-Pass Rate (FP/(TP+FP))={cm['false_pass_rate']:.3f}  False-Rejection Rate (FN/(TP+FN))={cm['false_rejection_rate']:.3f}\n"
        f"Verifier Ranking & Selection Performance:\n"
        f"  V17 Binary Gate:        AUROC={bg['v17_binary_gate_auroc']:.3f} | Run-Level Recall={bg['v17_binary_gate_run_recall']:.3f} | Discordant Top-1 Acc={bg['v17_binary_gate_discordant_top1_acc']:.3f}\n"
        f"  V18 Default Ranker:     AUROC={bg['v18_default_ranker_auroc']:.3f} | All-Resolvable Top-1 Acc={bg['v18_default_ranker_all_resolvable_top1_acc']:.3f} | Discordant Top-1 Acc={bg['v18_default_ranker_discordant_top1_acc']:.3f}\n"
        f"  V18 Calibrated Ranker:  AUROC={bg['v18_calibrated_ranker_auroc']:.3f} | All-Resolvable Top-1 Acc={bg['v18_calibrated_ranker_all_resolvable_top1_acc']:.3f} | Discordant Top-1 Acc={bg['v18_calibrated_ranker_discordant_top1_acc']:.3f}\n"
        f"Calibrated Weights saved to {args.out}: {rep['calibrated_weights']}"
    )
    return 0


def cmd_arms(args: argparse.Namespace) -> int:
    splits = load_splits(args.splits)
    out_root = os.path.abspath(args.out)
    os.makedirs(out_root, exist_ok=True)
    ref = write_meta(
        out_root,
        {
            "split": args.split,
            "arms": args.arms,
            "runs": args.runs,
            "budget_moves": args.budget_moves,
            "instance_timeout": args.instance_timeout,
        },
    )
    base = [arm_genome(a, args.calibration) for a in args.arms]
    genomes = _replicas(base, args.runs)
    state_path = os.path.join(out_root, "state.json")
    saved = _load_state(state_path)
    for g in genomes:
        if g.genome_id in saved:
            g.results = {tid: TaskResult(**r) for tid, r in saved[g.genome_id].get("results", {}).items()}
    wt = WorktreeManager(REPO_ROOT, ref, WORKTREE_ROOT)
    ev = make_evaluator(
        args,
        out_root,
        wt,
        splits,
        pod_tag=f"v18{args.split[:2]}",
        label_verdicts=args.label_verdicts,
    )

    def _ckpt() -> None:
        curr = _load_state(state_path)
        for g in genomes:
            curr[g.genome_id] = g.to_dict()
        _dump_json(state_path, curr)

    ev.evaluate(genomes, list(splits[args.split]))
    _ckpt()
    return 0


def cmd_flat(args: argparse.Namespace) -> int:
    splits = load_splits(args.splits)
    out_root = os.path.abspath(args.out)
    os.makedirs(out_root, exist_ok=True)
    ref = write_meta(
        out_root,
        {
            "split": args.split,
            "arm": "Flat_1x_V18",
            "runs": args.runs,
            "budget_moves": args.budget_moves,
            "instance_timeout": args.instance_timeout,
        },
    )
    base_g = HarnessGenome(genome_id="Flat_1x_V18", knobs={}, patch="", origin="v12-flat-v18-substrate")
    genomes = _replicas([base_g], args.runs)
    state_path = os.path.join(out_root, "state.json")
    saved = _load_state(state_path)
    for g in genomes:
        if g.genome_id in saved:
            g.results = {tid: TaskResult(**r) for tid, r in saved[g.genome_id].get("results", {}).items()}
    wt = WorktreeManager(REPO_ROOT, ref, WORKTREE_ROOT)
    ev = make_evaluator(
        args,
        out_root,
        wt,
        splits,
        pod_tag="v18fl",
        seed_path=V12_GENOME,
        label_verdicts=False,
    )

    def _ckpt() -> None:
        curr = _load_state(state_path)
        for g in genomes:
            curr[g.genome_id] = g.to_dict()
        _dump_json(state_path, curr)

    tasks = list(splits[args.split])
    ev.evaluate(genomes, tasks)
    _ckpt()
    if args.runs >= 2:
        synthesize_flat_3x_select(out_root, tasks, base_id="Flat_1x_V18", runs=args.runs)
    return 0


def cmd_select_flat3x(args: argparse.Namespace) -> int:
    splits = load_splits(args.splits)
    tasks = list(splits[args.split])
    sel = synthesize_flat_3x_select(os.path.abspath(args.out), tasks, base_id=args.base_id, runs=args.runs)
    resolved = sum(1 for r in sel.results.values() if r.resolved)
    mean_tok = (
        sum(r.tokens for r in sel.results.values()) / max(1, len(sel.results))
        if sel.results
        else 0.0
    )
    print(f"Synthesized Flat_3x_Select across {len(sel.results)} tasks: resolved={resolved}/{len(sel.results)}, mean_tokens={mean_tok:.0f}")
    return 0


def _common(p: argparse.ArgumentParser, moves: int = 180, timeout: int = 5400) -> None:
    p.add_argument("--splits", default=DEFAULT_SPLITS)
    p.add_argument("--vllm-url", default=os.environ.get("VLLM_BASE_URL", "http://127.0.0.1:8000/v1"))
    p.add_argument("--vllm-model", default="reflection/Beam-1.0-501B-A23B-NVFP4")
    p.add_argument("--context", default=v15.DEFAULT_CONTEXT)
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--budget-moves", type=int, default=moves)
    p.add_argument("--max-iterations", type=int, default=2)
    p.add_argument("--instance-timeout", type=int, default=timeout)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_cal = sub.add_parser("calibrate-ranker")
    p_cal.add_argument("--out", default=DEFAULT_CALIBRATION)
    p_cal.set_defaults(fn=cmd_calibrate)

    p_arms = sub.add_parser("arms")
    _common(p_arms, moves=180, timeout=5400)
    p_arms.add_argument("--split", choices=["test", "eval_80", "eval_24_stratified", "val", "train"], default="eval_24_stratified")
    p_arms.add_argument("--arms", nargs="*", default=["V18_Collab", "V18_Evolved"])
    p_arms.add_argument("--runs", type=int, default=1)
    p_arms.add_argument("--calibration", default=DEFAULT_CALIBRATION)
    p_arms.add_argument("--label-verdicts", action="store_true")
    p_arms.add_argument("--out", required=True)
    p_arms.set_defaults(fn=cmd_arms)

    p_flat = sub.add_parser("flat")
    _common(p_flat, moves=120, timeout=3600)
    p_flat.add_argument("--split", choices=["test", "eval_80", "eval_24_stratified", "val", "train"], default="eval_24_stratified")
    p_flat.add_argument("--runs", type=int, default=3)
    p_flat.add_argument("--out", required=True)
    p_flat.set_defaults(fn=cmd_flat)

    p_sel = sub.add_parser("select-flat3x")
    p_sel.add_argument("--splits", default=DEFAULT_SPLITS)
    p_sel.add_argument("--split", choices=["test", "eval_80", "eval_24_stratified", "val", "train"], default="eval_24_stratified")
    p_sel.add_argument("--base-id", default="Flat_1x_V18")
    p_sel.add_argument("--runs", type=int, default=3)
    p_sel.add_argument("--out", required=True)
    p_sel.set_defaults(fn=cmd_select_flat3x)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
