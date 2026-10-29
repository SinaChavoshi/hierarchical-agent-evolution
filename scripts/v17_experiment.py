#!/usr/bin/env python3
"""V17: Red Team as a Foundation Piece + Generous Agent Budgets.

Arms (all inherit the V15 champion code patch + V16 refreshed role library + V17 high-budget knobs):
  A17  Dynamic org (`org_mode=dynamic`) + high budgets (`V17_HIGH_BUDGET_KNOBS`, 250 moves) + permissive
       falsifier (`V17_DEFAULT_REDTEAM`: no_edit=False, lock_repro_test=False, skeptic_default=False, ...)
  S    `A17` + strict Red-Team foundation (`V17_STRICT_REDTEAM`: no_edit=True, lock_repro_test=True,
       skeptic_default=True, evidence_tests=True, panel_size=1, continuous_build=True), un-evolved
  R    Full evolution (`evolve --mode evolve`) over Red-Team genes, falsifier & specialist roles, CEO
       policy, harness knobs, and code with red-team fitness (`redteam_weight=0.3`) and per-verdict credit
  Rr   Matched-budget random search (`evolve --mode random`) over knobs + orgs + Red-Team genes
  V12  Flat ReAct agent at matched V17 high budget (`budget_moves=250`, `instance_timeout=7200`)

Subcommands:
  arms    evaluate fixed arms (`A17`, `S`) and/or promoted genomes (`R=...`, `Rr=...`) on a split
  evolve  run `R` (`--mode evolve`) or `Rr` (`--mode random`) on the V17 training split
  v12     evaluate the V12 flat agent on a split (`--runs` replicates)
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from typing import Dict, List

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))

import v15_evolve as v15  # noqa: E402
from hae.evolution import org_evolution as orgevo  # noqa: E402
from hae.evolution.evaluator import GenomeEvaluator  # noqa: E402
from hae.evolution.evolve import EvolutionConfig, EvolutionRun  # noqa: E402
from hae.evolution.genome import HarnessGenome  # noqa: E402
from hae.evolution.mutation import CodeMutator  # noqa: E402
from hae.evolution.role_gates import RoleGates  # noqa: E402
from hae.evolution.workspace import WorktreeManager  # noqa: E402
from hae.swebench.harness_knobs import V17_HIGH_BUDGET_KNOBS  # noqa: E402
from hae.swebench.org_binding import V17_DEFAULT_REDTEAM, V17_PERMISSIVE_REDTEAM, V17_STRICT_REDTEAM  # noqa: E402

SEED_GENOME = os.path.join(REPO_ROOT, "configs", "genomes", "v16_seed_harness.json")
V12_GENOME = os.path.join(REPO_ROOT, "configs", "genomes", "v12_agentic_swebench_firm_0.json")
LIBRARY = os.path.join(REPO_ROOT, "configs", "roles", "v16_role_library.json")
CHAMPION = (os.path.join(REPO_ROOT, "results", "v15", "evolve_2026-10-27"), "evo_g5_03")
DEFAULT_SPLITS = os.path.join(REPO_ROOT, "data", "swebench", "v17_splits.json")
WORKTREE_ROOT = os.path.expanduser("~/hae_v17_worktrees")


def head_ref() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True).strip()


def load_splits(path: str = DEFAULT_SPLITS) -> Dict:
    p = path if os.path.exists(path) else v15.SPLITS
    with open(p, "r", encoding="utf-8") as fh:
        return json.load(fh)


def dataset_map(splits: Dict) -> Dict[str, str]:
    """Map every instance_id across all dataset files in `splits['datasets']` to its JSONL path."""
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
    for split in ("train", "val", "test"):
        ds = os.path.join(REPO_ROOT, splits["datasets"][split])
        for t in splits.get(split, []):
            m.setdefault(t, ds)
    return m


def champion_patch() -> str:
    st_path = os.path.join(CHAMPION[0], "state.json")
    if os.path.exists(st_path):
        st = json.load(open(st_path, encoding="utf-8"))
        return st["genomes"][CHAMPION[1]]["patch"]
    return ""


def arm_genome(arm: str) -> HarnessGenome:
    lib = json.load(open(LIBRARY, encoding="utf-8"))
    knobs = dict(V17_HIGH_BUDGET_KNOBS)
    patch = champion_patch()
    if arm == "A17":
        return HarnessGenome(
            genome_id="A17", knobs=knobs, patch=patch, origin="arm-A17", org_mode="dynamic",
            roles=lib["role_library"], ceo_policy=lib["ceo_policy"], redteam=dict(V17_PERMISSIVE_REDTEAM),
        )
    if arm == "S":
        return HarnessGenome(
            genome_id="S", knobs=knobs, patch=patch, origin="arm-S", org_mode="dynamic",
            roles=lib["role_library"], ceo_policy=lib["ceo_policy"], redteam=dict(V17_STRICT_REDTEAM),
        )
    raise ValueError(f"Unknown V17 arm {arm!r}")


def make_evaluator(args, out_root: str, wt: WorktreeManager, splits: Dict, pod_tag: str,
                   seed_path: str = SEED_GENOME, label_verdicts: bool = False) -> GenomeEvaluator:
    return GenomeEvaluator(
        out_root=out_root, seed_genome_path=seed_path, dataset_by_task=dataset_map(splits), worktrees=wt,
        vllm_url=args.vllm_url, vllm_model=args.vllm_model, context=args.context, workers=args.workers,
        budget_moves=args.budget_moves, max_iterations=args.max_iterations, instance_timeout_s=args.instance_timeout,
        pod_tag=pod_tag, label_verdicts=label_verdicts,
    )


def write_meta(out_root: str, extra: Dict) -> str:
    meta_path = os.path.join(out_root, "run_meta.json")
    if os.path.exists(meta_path):
        return json.load(open(meta_path))["base_ref"]
    ref = head_ref()
    wt = WorktreeManager(REPO_ROOT, ref, WORKTREE_ROOT)
    json.dump({"base_ref": ref, "infra_commit": wt.infra_commit(), **extra}, open(meta_path, "w"), indent=1)
    return ref


def _load_state(path: str) -> Dict:
    return json.load(open(path)) if os.path.exists(path) else {}


def _replicas(genomes: List[HarnessGenome], runs: int) -> List[HarnessGenome]:
    out = []
    for g in genomes:
        for r in range(1, runs + 1):
            d = g.to_dict()
            d.update(genome_id=f"{g.genome_id}__r{r}", results={})
            out.append(HarnessGenome.from_dict(d))
    return out


def cmd_arms(args) -> int:
    splits = load_splits(args.splits)
    out_root = os.path.abspath(args.out)
    os.makedirs(out_root, exist_ok=True)
    ref = write_meta(out_root, {"split": args.split, "arms": args.arms, "extra": args.genomes, "runs": args.runs,
                                "budget_moves": args.budget_moves, "instance_timeout": args.instance_timeout})
    base = [arm_genome(a) for a in args.arms]
    for spec in args.genomes or []:  # label=run_dir:genome_id
        label, src = spec.split("=", 1)
        run_dir, gid = src.rsplit(":", 1)
        st = json.load(open(os.path.join(run_dir, "state.json")))
        d = dict(st["genomes"][gid])
        d.update(genome_id=label, results={})
        base.append(HarnessGenome.from_dict(d))
    genomes = _replicas(base, args.runs)
    state_path = os.path.join(out_root, f"{args.split}_state.json")
    prev = _load_state(state_path)
    for g in genomes:
        if g.genome_id in prev:
            g.results = HarnessGenome.from_dict(prev[g.genome_id]).results
    wt = WorktreeManager(REPO_ROOT, ref, os.path.join(WORKTREE_ROOT, os.path.basename(out_root)))
    label_v = bool(args.label_verdicts or args.split == "train")
    ev = make_evaluator(args, out_root, wt, splits, pod_tag=f"v17{args.split[0]}", label_verdicts=label_v)
    tasks = list(splits[args.split])
    ev.evaluate(genomes, tasks)
    for g in genomes:
        prev[g.genome_id] = g.to_dict()
    json.dump(prev, open(state_path, "w"), indent=1)
    for g in genomes:
        cm = g.red_team_confusion(tasks)
        print(f"{g.genome_id}: {args.split} resolved {sum(r.resolved for r in g.results.values())}/{len(tasks)} "
              f"rt_score={g.red_team_score(tasks):.3f} cm={cm}")
    return 0


def cmd_v12(args) -> int:
    splits = load_splits(args.splits)
    out_root = os.path.abspath(args.out)
    os.makedirs(out_root, exist_ok=True)
    ref = write_meta(out_root, {"split": args.split, "arm": "V12", "runs": args.runs,
                                "budget_moves": args.budget_moves, "instance_timeout": args.instance_timeout})
    genomes = _replicas([HarnessGenome(genome_id="V12", origin="v12-flat")], args.runs)
    state_path = os.path.join(out_root, f"{args.split}_state.json")
    prev = _load_state(state_path)
    for g in genomes:
        if g.genome_id in prev:
            g.results = HarnessGenome.from_dict(prev[g.genome_id]).results
    wt = WorktreeManager(REPO_ROOT, ref, os.path.join(WORKTREE_ROOT, os.path.basename(out_root)))
    ev = make_evaluator(args, out_root, wt, splits, pod_tag=f"v12{args.split[0]}", seed_path=V12_GENOME)
    tasks = list(splits[args.split])
    ev.evaluate(genomes, tasks)
    for g in genomes:
        prev[g.genome_id] = g.to_dict()
    json.dump(prev, open(state_path, "w"), indent=1)
    for g in genomes:
        print(f"{g.genome_id}: {args.split} resolved {sum(r.resolved for r in g.results.values())}/{len(tasks)}")
    return 0


def cmd_evolve(args) -> int:
    splits = load_splits(args.splits)
    out_root = os.path.abspath(args.out)
    os.makedirs(out_root, exist_ok=True)
    ref = write_meta(out_root, {"mode": args.mode, "seed_arm": args.seed_arm, "splits": args.splits,
                                "vllm_model": args.vllm_model, "budget_moves": args.budget_moves,
                                "redteam_weight": args.redteam_weight})
    wt = WorktreeManager(REPO_ROOT, ref, os.path.join(WORKTREE_ROOT, os.path.basename(out_root)))
    ev = make_evaluator(args, out_root, wt, splits, pod_tag="v17" + args.mode[0], label_verdicts=True)
    is_evo = args.mode == "evolve"
    cfg = EvolutionConfig(
        mode=args.mode, population=args.population, generations=args.generations, promote_k=args.promote_k,
        token_lambda=args.token_lambda, partial_weight=args.partial_weight,
        redteam_weight=args.redteam_weight,
        p_code=args.p_code, p_knob=args.p_knob, p_cross=args.p_cross,
        p_org=args.p_org if is_evo else 0.0,
        p_redteam=args.p_redteam if is_evo else 0.0,
        seed=args.seed, stage1_tasks=list(splits["stage1"]), train_tasks=list(splits["train"]),
    )
    seed = arm_genome(args.seed_arm)
    if args.seed_results_from:
        st = json.load(open(os.path.join(args.seed_results_from, "train_state.json")))
        src = HarnessGenome.from_dict(st[args.seed_results_key])
        seed.results = {t: r for t, r in src.results.items() if t in splits["train"]}
    code_mut = org_mut = leak = None
    if is_evo:
        llm = v15.make_llm(args)
        leak = v15.make_leakage(splits)
        code_mut = CodeMutator(wt, leak, llm)
        org_mut = orgevo.OrgMutator(RoleGates(splits, REPO_ROOT), llm)
    run = EvolutionRun(cfg, ev, out_root, code_mutator=code_mut, leakage=leak, seed_genome=seed, org_mutator=org_mut)
    run.run()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--vllm-url", default=os.environ.get("VLLM_BASE_URL", "http://127.0.0.1:8000/v1"))
        p.add_argument("--vllm-model", default="reflection/Beam-1.0-501B-A23B-NVFP4")
        p.add_argument("--context", default=v15.DEFAULT_CONTEXT)
        p.add_argument("--workers", type=int, default=24)
        p.add_argument("--budget-moves", type=int, default=250)
        p.add_argument("--max-iterations", type=int, default=2)
        p.add_argument("--instance-timeout", type=int, default=7200)
        p.add_argument("--splits", default=DEFAULT_SPLITS)
        p.add_argument("--out", required=True)

    p = sub.add_parser("arms"); common(p)
    p.add_argument("--split", choices=["stage1", "train", "val", "test"], required=True)
    p.add_argument("--arms", nargs="*", default=[], choices=["A17", "S"])
    p.add_argument("--genomes", nargs="*", default=[], help="label=run_dir:genome_id (promoted R/Rr genomes)")
    p.add_argument("--runs", type=int, default=1)
    p.add_argument("--label-verdicts", action="store_true", help="Grade intermediate candidate patches in org.verdicts")

    p = sub.add_parser("v12"); common(p)
    p.add_argument("--split", choices=["stage1", "train", "val", "test"], required=True)
    p.add_argument("--runs", type=int, default=1)

    p = sub.add_parser("evolve"); common(p)
    p.add_argument("--mode", choices=["evolve", "random"], required=True)
    p.add_argument("--seed-arm", choices=["A17", "S"], default="A17")
    p.add_argument("--population", type=int, default=8)
    p.add_argument("--generations", type=int, default=6)
    p.add_argument("--promote-k", type=int, default=3)
    p.add_argument("--token-lambda", type=float, default=0.05)
    p.add_argument("--partial-weight", type=float, default=0.25)
    p.add_argument("--redteam-weight", type=float, default=0.30)
    p.add_argument("--p-redteam", type=float, default=0.35)
    p.add_argument("--p-org", type=float, default=0.25)
    p.add_argument("--p-code", type=float, default=0.20)
    p.add_argument("--p-knob", type=float, default=0.10)
    p.add_argument("--p-cross", type=float, default=0.10)
    p.add_argument("--seed", type=int, default=17)
    p.add_argument("--seed-results-from", default="", help="arms run dir whose train_state.json holds seed arm results")
    p.add_argument("--seed-results-key", default="A17__r1")
    args = ap.parse_args()
    return {"arms": cmd_arms, "v12": cmd_v12, "evolve": cmd_evolve}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
