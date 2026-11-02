#!/usr/bin/env python3
"""V16: roles + CEO policy on the hierarchical harness. Arms, evolution, and the V12 control.

Arms (all start from the V15 champion `evo_g5_03`: its code patch and knobs):
  A  champion, org_mode=off (exactly V15)
  B  champion + refreshed role library (configs/roles/v16_role_library.json), org_mode=static
  C  champion + refreshed role library, org_mode=dynamic
  D  full evolution (code, knobs, roles, CEO policy) seeded from C       -> `evolve --mode evolve`
  E  random search over knobs + organizations seeded from C (matched budget) -> `evolve --mode random`
  V12 flat ReAct agent (configs/genomes/v12_agentic_swebench_firm_0.json), same evaluator -> `v12`

Subcommands:
  arms    evaluate fixed arms (A/B/C and/or promoted genomes) on a split, `--runs` replicates each
  evolve  run D (`--mode evolve`) or E (`--mode random`) on the training split
  v12     evaluate the V12 flat agent on a split (`--runs` replicates)

Examples:
  PYTHONPATH=. python3 scripts/v16_experiment.py arms --split val --arms A B C --runs 2 --out results/v16/val
  PYTHONPATH=. python3 scripts/v16_experiment.py evolve --mode evolve --out results/v16/evolve
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from typing import Dict, List, Tuple

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

SEED_GENOME = os.path.join(REPO_ROOT, "configs", "genomes", "v16_seed_harness.json")
V12_GENOME = os.path.join(REPO_ROOT, "configs", "genomes", "v12_agentic_swebench_firm_0.json")
LIBRARY = os.path.join(REPO_ROOT, "configs", "roles", "v16_role_library.json")
CHAMPION = (os.path.join(REPO_ROOT, "results", "v15", "evolve_2026-10-27"), "evo_g5_03")
WORKTREE_ROOT = os.path.expanduser("~/hae_v16_worktrees")


def head_ref() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True).strip()


def champion() -> HarnessGenome:
    st = json.load(open(os.path.join(CHAMPION[0], "state.json"), encoding="utf-8"))
    g = st["genomes"][CHAMPION[1]]
    return HarnessGenome(genome_id="champion", knobs=g["knobs"], patch=g["patch"], origin="v15-champion")


def arm_genome(arm: str) -> HarnessGenome:
    c = champion()
    if arm == "A":
        return HarnessGenome(genome_id="A", knobs=c.knobs, patch=c.patch, origin="arm-A")
    lib = json.load(open(LIBRARY, encoding="utf-8"))
    mode = {"B": "static", "C": "dynamic"}[arm]
    return HarnessGenome(genome_id=arm, knobs=c.knobs, patch=c.patch, origin=f"arm-{arm}", org_mode=mode,
                         roles=lib["role_library"], ceo_policy=lib["ceo_policy"])


def make_evaluator(args, out_root: str, wt: WorktreeManager, splits: Dict, pod_tag: str,
                   seed_path: str = SEED_GENOME) -> GenomeEvaluator:
    return GenomeEvaluator(
        out_root=out_root, seed_genome_path=seed_path, dataset_by_task=v15.dataset_map(splits), worktrees=wt,
        vllm_url=args.vllm_url, vllm_model=args.vllm_model, context=args.context, workers=args.workers,
        budget_moves=args.budget_moves, max_iterations=args.max_iterations, instance_timeout_s=args.instance_timeout,
        pod_tag=pod_tag,
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
    splits = v15.load_splits()
    out_root = os.path.abspath(args.out)
    os.makedirs(out_root, exist_ok=True)
    ref = write_meta(out_root, {"split": args.split, "arms": args.arms, "extra": args.genomes, "runs": args.runs})
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
    ev = make_evaluator(args, out_root, wt, splits, pod_tag=f"v16{args.split[0]}")
    ev.evaluate(genomes, list(splits[args.split]))
    for g in genomes:
        prev[g.genome_id] = g.to_dict()
    json.dump(prev, open(state_path, "w"), indent=1)
    for g in genomes:
        print(f"{g.genome_id}: {args.split} resolved {sum(r.resolved for r in g.results.values())}/{len(splits[args.split])}")
    return 0


def cmd_v12(args) -> int:
    splits = v15.load_splits()
    out_root = os.path.abspath(args.out)
    os.makedirs(out_root, exist_ok=True)
    ref = write_meta(out_root, {"split": args.split, "arm": "V12", "runs": args.runs, "budget_moves": args.budget_moves})
    genomes = _replicas([HarnessGenome(genome_id="V12", origin="v12-flat")], args.runs)
    state_path = os.path.join(out_root, f"{args.split}_state.json")
    prev = _load_state(state_path)
    for g in genomes:
        if g.genome_id in prev:
            g.results = HarnessGenome.from_dict(prev[g.genome_id]).results
    wt = WorktreeManager(REPO_ROOT, ref, os.path.join(WORKTREE_ROOT, os.path.basename(out_root)))
    ev = make_evaluator(args, out_root, wt, splits, pod_tag=f"v12{args.split[0]}", seed_path=V12_GENOME)
    ev.evaluate(genomes, list(splits[args.split]))
    for g in genomes:
        prev[g.genome_id] = g.to_dict()
    json.dump(prev, open(state_path, "w"), indent=1)
    for g in genomes:
        print(f"{g.genome_id}: {args.split} resolved {sum(r.resolved for r in g.results.values())}/{len(splits[args.split])}")
    return 0


def cmd_evolve(args) -> int:
    splits = v15.load_splits()
    out_root = os.path.abspath(args.out)
    os.makedirs(out_root, exist_ok=True)
    ref = write_meta(out_root, {"mode": args.mode, "seed_arm": "C", "splits": v15.SPLITS, "vllm_model": args.vllm_model})
    wt = WorktreeManager(REPO_ROOT, ref, os.path.join(WORKTREE_ROOT, os.path.basename(out_root)))
    ev = make_evaluator(args, out_root, wt, splits, pod_tag="v16" + args.mode[0])
    cfg = EvolutionConfig(mode=args.mode, population=args.population, generations=args.generations,
                          promote_k=args.promote_k, token_lambda=args.token_lambda, partial_weight=args.partial_weight,
                          p_code=0.5, p_knob=0.3, p_cross=0.2, p_org=args.p_org if args.mode == "evolve" else 0.0,
                          seed=args.seed, stage1_tasks=list(splits["stage1"]), train_tasks=list(splits["train"]))
    seed = arm_genome("C")
    if args.seed_results_from:
        st = json.load(open(os.path.join(args.seed_results_from, "train_state.json")))
        src = HarnessGenome.from_dict(st[args.seed_results_key])
        seed.results = {t: r for t, r in src.results.items() if t in splits["train"]}
    code_mut = org_mut = leak = None
    if args.mode == "evolve":
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
        p.add_argument("--budget-moves", type=int, default=30)
        p.add_argument("--max-iterations", type=int, default=2)
        p.add_argument("--instance-timeout", type=int, default=2400)
        p.add_argument("--out", required=True)

    p = sub.add_parser("arms"); common(p)
    p.add_argument("--split", choices=["train", "val", "test"], required=True)
    p.add_argument("--arms", nargs="*", default=[], choices=["A", "B", "C"])
    p.add_argument("--genomes", nargs="*", default=[], help="label=run_dir:genome_id (promoted D/E genomes)")
    p.add_argument("--runs", type=int, default=1)
    p = sub.add_parser("v12"); common(p)
    p.add_argument("--split", choices=["train", "val", "test"], required=True)
    p.add_argument("--runs", type=int, default=1)
    p = sub.add_parser("evolve"); common(p)
    p.add_argument("--mode", choices=["evolve", "random"], required=True)
    p.add_argument("--population", type=int, default=8)
    p.add_argument("--generations", type=int, default=6)
    p.add_argument("--promote-k", type=int, default=3)
    p.add_argument("--token-lambda", type=float, default=0.05)
    p.add_argument("--partial-weight", type=float, default=0.25)
    p.add_argument("--p-org", type=float, default=0.4)
    p.add_argument("--seed", type=int, default=16)
    p.add_argument("--seed-results-from", default="", help="arms run dir whose train_state.json holds arm C results")
    p.add_argument("--seed-results-key", default="C__r1")
    args = ap.parse_args()
    return {"arms": cmd_arms, "v12": cmd_v12, "evolve": cmd_evolve}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
