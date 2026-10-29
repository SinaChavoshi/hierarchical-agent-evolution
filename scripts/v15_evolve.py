#!/usr/bin/env python3
"""V15: evolve the agent harness (knobs + code) on SWE-bench dev tasks; validate; test on unseen Verified.

Subcommands:
  smoke     Materialize the seed harness, run the safety gate, and (optionally) propose one code mutation.
  evolve    Run the generational loop (`--mode evolve`) or the equal-budget random-search control (`--mode random`).
  validate  Evaluate the top genomes of one or more runs on the validation split.
  test      Evaluate named genomes on the held-out test split (unseen SWE-bench Verified).
  report    Print a summary table of a run.

Example:
  PYTHONPATH=. python3 scripts/v15_evolve.py evolve --mode evolve --out results/v15/evolve_2026-10-27 \\
      --vllm-url http://127.0.0.1:8000/v1 --workers 4
"""

from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
from typing import Dict, List

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from hae.evolution.evaluator import GenomeEvaluator  # noqa: E402
from hae.evolution.evolve import EvolutionConfig, EvolutionRun  # noqa: E402
from hae.evolution.genome import EVOLVABLE_FILES, HarnessGenome  # noqa: E402
from hae.evolution.leakage import LeakageGate  # noqa: E402
from hae.evolution.mutation import CodeMutator, failure_digest  # noqa: E402
from hae.evolution.workspace import WorktreeManager, run_gate  # noqa: E402

DEFAULT_CONTEXT = "gke_gemle-gke-dev_us-central1-b_chavoshi-v6-cohort"
SPLITS = os.path.join(REPO_ROOT, "data", "swebench", "v15_splits.json")
SEED_GENOME = os.path.join(REPO_ROOT, "configs", "genomes", "v15_seed_harness_firm_0.json")
WORKTREE_ROOT = os.path.expanduser("~/hae_v15_worktrees")


def load_splits() -> Dict:
    with open(SPLITS, "r", encoding="utf-8") as fh:
        return json.load(fh)


def dataset_map(splits: Dict) -> Dict[str, str]:
    m: Dict[str, str] = {}
    for split in ("train", "val", "test"):
        ds = os.path.join(REPO_ROOT, splits["datasets"][split])
        for t in splits[split]:
            m[t] = ds
    return m


def base_ref(args) -> str:
    if args.base_ref:
        return args.base_ref
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True).strip()


def make_llm(args):
    os.environ.setdefault("LLM_PROVIDER", "vllm")
    os.environ["VLLM_BASE_URL"] = args.vllm_url
    os.environ["VLLM_MODEL"] = args.vllm_model
    from hae.infra.llm import call_llm

    def _llm(system: str, prompt: str) -> str:
        return call_llm(prompt, model_name=args.vllm_model, system_instruction=system, provider="vllm",
                        temperature=0.8, max_tokens=8192)

    return _llm


def make_leakage(splits: Dict) -> LeakageGate:
    baseline = ""
    for rel in EVOLVABLE_FILES:
        with open(os.path.join(REPO_ROOT, rel), "r", encoding="utf-8") as fh:
            baseline += fh.read() + "\n"
    ds = sorted({os.path.join(REPO_ROOT, p) for p in splits["datasets"].values()})
    pool = splits["train"] + splits["val"] + splits["test"]
    return LeakageGate.build(ds, pool, baseline)


def make_evaluator(args, out_root: str, worktrees: WorktreeManager, splits: Dict, pod_tag: str) -> GenomeEvaluator:
    return GenomeEvaluator(
        out_root=out_root, seed_genome_path=SEED_GENOME, dataset_by_task=dataset_map(splits),
        worktrees=worktrees, vllm_url=args.vllm_url, vllm_model=args.vllm_model, context=args.context,
        workers=args.workers, budget_moves=args.budget_moves, max_iterations=args.max_iterations,
        instance_timeout_s=args.instance_timeout, pod_tag=pod_tag,
    )


def cmd_smoke(args) -> int:
    splits = load_splits()
    wt = WorktreeManager(REPO_ROOT, base_ref(args), os.path.join(WORKTREE_ROOT, "smoke"))
    seed = HarnessGenome(genome_id="smoke_seed")
    path, err = wt.materialize(seed)
    print("materialize:", path, err)
    gate = run_gate(path)
    print("gate:", gate.ok, gate.stage, gate.message[-300:])
    leak = make_leakage(splits)
    print(f"leakage gate: {len(leak.forbidden)} forbidden tokens, {len(leak.forbidden_names)} names")
    if args.propose:
        mut = CodeMutator(wt, leak, make_llm(args))
        res = mut.propose(seed, "smoke_child", "Parent resolved 0/0 training issues (smoke test).", random.Random(0))
        print("code mutation:", None if res is None else (res.targets, res.attempts, res.rationale))
        if res is not None:
            print(res.patch[:3000])
        wt.remove("smoke_child")
    wt.remove("smoke_seed")
    return 0 if gate.ok else 1


def cmd_evolve(args) -> int:
    splits = load_splits()
    out_root = os.path.abspath(args.out)
    os.makedirs(out_root, exist_ok=True)
    ref = base_ref(args)
    meta_path = os.path.join(out_root, "run_meta.json")
    if os.path.exists(meta_path):
        ref = json.load(open(meta_path))["base_ref"]
    else:
        json.dump({"base_ref": ref, "mode": args.mode, "vllm_model": args.vllm_model, "splits": SPLITS,
                   "seed_genome": SEED_GENOME}, open(meta_path, "w"), indent=1)
    wt = WorktreeManager(REPO_ROOT, ref, os.path.join(WORKTREE_ROOT, os.path.basename(out_root)))
    evaluator = make_evaluator(args, out_root, wt, splits, pod_tag=f"v15{args.mode[0]}")
    leak = make_leakage(splits)
    mutator = CodeMutator(wt, leak, make_llm(args)) if args.mode == "evolve" else None
    cfg = EvolutionConfig(mode=args.mode, population=args.population, generations=args.generations,
                          promote_k=args.promote_k, token_lambda=args.token_lambda, seed=args.seed,
                          partial_weight=args.partial_weight,
                          stage1_tasks=list(splits["stage1"]), train_tasks=list(splits["train"]))
    run = EvolutionRun(cfg, evaluator, out_root, code_mutator=mutator, leakage=leak)
    if args.seed_results_from:
        src = os.path.join(os.path.abspath(args.seed_results_from), "state.json")
        if os.path.exists(src) and "seed" in json.load(open(src))["genomes"]:
            seed_src = HarnessGenome.from_dict(json.load(open(src))["genomes"]["seed"])
            run.genomes.setdefault("seed", HarnessGenome(genome_id="seed", origin="seed"))
            run.genomes["seed"].results.update(seed_src.results)
            run.save()
            print(f"[v15] reused {len(seed_src.results)} seed evaluation(s) from {src}")
    run.run()
    return 0


def _genomes_from(run_dir: str) -> Dict[str, HarnessGenome]:
    st = json.load(open(os.path.join(run_dir, "state.json")))
    return {gid: HarnessGenome.from_dict(g) for gid, g in st["genomes"].items()}


def _eval_named(args, split: str) -> int:
    splits = load_splits()
    out_root = os.path.abspath(args.out)
    os.makedirs(out_root, exist_ok=True)
    picked: List[HarnessGenome] = []
    for spec in args.genomes:  # run_dir:genome_id
        run_dir, gid = spec.rsplit(":", 1)
        g = _genomes_from(os.path.abspath(run_dir))[gid]
        ref = json.load(open(os.path.join(os.path.abspath(run_dir), "run_meta.json")))["base_ref"]
        tag = f"{os.path.basename(os.path.abspath(run_dir))}__{gid}"
        picked.append((HarnessGenome(genome_id=tag, knobs=g.knobs, patch=g.patch, origin=g.origin), ref))
    state_path = os.path.join(out_root, f"{split}_state.json")
    prev = json.load(open(state_path)) if os.path.exists(state_path) else {}
    by_ref: Dict[str, List[HarnessGenome]] = {}
    for g, ref in picked:
        if g.genome_id in prev:
            g.results = HarnessGenome.from_dict(prev[g.genome_id]).results
        by_ref.setdefault(ref, []).append(g)
    # Evaluate all genomes that share a base commit in one worker pool (parallel across genomes).
    for ref, genomes in by_ref.items():
        wt = WorktreeManager(REPO_ROOT, ref, os.path.join(WORKTREE_ROOT, f"{split}_eval"))
        ev = make_evaluator(args, out_root, wt, splits, pod_tag=f"v15{split[0]}")
        ev.evaluate(genomes, list(splits[split]))
        for g in genomes:
            prev[g.genome_id] = g.to_dict()
        json.dump(prev, open(state_path, "w"), indent=1)
    for g, _ in picked:
        print(f"{g.genome_id}: {split} resolved {sum(r.resolved for r in g.results.values())}/{len(splits[split])}")
    return 0


def cmd_report(args) -> int:
    splits = load_splits()
    st = json.load(open(os.path.join(os.path.abspath(args.out), "state.json")))
    train = splits["train"]
    rows = []
    for gid, gd in st["genomes"].items():
        g = HarnessGenome.from_dict(gd)
        rows.append((g.generation, gid, g.origin, len(g.results), g.success_rate(train) if g.evaluated_on(train) else None,
                     g.success_rate(splits["stage1"]), int(g.mean_tokens()), g.patch_lines))
    print(f"{'gen':>3} {'genome':<14} {'origin':<13} {'n':>3} {'train':>6} {'stage1':>6} {'tokens':>8} {'patch':>5}")
    for r in sorted(rows):
        tr = f"{r[4]:.3f}" if r[4] is not None else "  -  "
        print(f"{r[0]:>3} {r[1]:<14} {r[2]:<13} {r[3]:>3} {tr:>6} {r[5]:>6.3f} {r[6]:>8} {r[7]:>5}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--vllm-url", default=os.environ.get("VLLM_BASE_URL", "http://127.0.0.1:8000/v1"))
        p.add_argument("--vllm-model", default="reflection/Beam-1.0-501B-A23B-NVFP4")
        p.add_argument("--context", default=DEFAULT_CONTEXT)
        p.add_argument("--workers", type=int, default=4)
        p.add_argument("--budget-moves", type=int, default=30)
        p.add_argument("--max-iterations", type=int, default=2)
        p.add_argument("--instance-timeout", type=int, default=2400)
        p.add_argument("--base-ref", default="")

    p = sub.add_parser("smoke"); common(p); p.add_argument("--propose", action="store_true")
    p = sub.add_parser("evolve"); common(p)
    p.add_argument("--mode", choices=["evolve", "random"], default="evolve")
    p.add_argument("--out", required=True)
    p.add_argument("--population", type=int, default=8)
    p.add_argument("--generations", type=int, default=6)
    p.add_argument("--promote-k", type=int, default=3)
    p.add_argument("--token-lambda", type=float, default=0.05)
    p.add_argument("--partial-weight", type=float, default=0.25)
    p.add_argument("--seed", type=int, default=15)
    p.add_argument("--seed-results-from", default="", help="Reuse the seed genome's evaluations from another run dir")
    for name in ("validate", "test", "eval"):
        p = sub.add_parser(name); common(p)
        p.add_argument("--out", required=True)
        p.add_argument("--genomes", nargs="+", required=True, help="run_dir:genome_id entries")
        if name == "eval":
            p.add_argument("--split", choices=["train", "val", "test"], required=True)
    p = sub.add_parser("report"); p.add_argument("--out", required=True)

    args = ap.parse_args()
    if args.cmd == "smoke":
        return cmd_smoke(args)
    if args.cmd == "evolve":
        return cmd_evolve(args)
    if args.cmd == "validate":
        return _eval_named(args, "val")
    if args.cmd == "test":
        return _eval_named(args, "test")
    if args.cmd == "eval":
        return _eval_named(args, args.split)
    return cmd_report(args)


if __name__ == "__main__":
    raise SystemExit(main())
