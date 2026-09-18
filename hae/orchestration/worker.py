"""Standalone Parallel Worker Pod Evaluator for GKE Indexed Jobs."""

import os
import sys
import json
import time
import argparse
from typing import Dict, Any, Optional, List

from hae.genome.schema import CompanyGenome, EvaluationResult
from hae.runtime.company import HierarchicalCompanyRunner
from hae.evaluation.judge import StrategicFitnessEvaluator
from hae.infra.telemetry import ResearchLedger
from hae.infra.preflight import run_preflight
from hae.infra.config import EvolutionConfig
from hae.task import Submission, Task, legacy_task

def evaluate_single_firm(
    firm_index: int,
    generation: int,
    objective: str,
    output_dir: str,
    region: str,
    gcs_bucket: str = os.environ.get("GCS_BUCKET", "YOUR_GCS_BUCKET"),
    seed_config_path: str = "templates/default_company.json",
    population_file: Optional[str] = None,
    task: Optional[Task] = None,
    seed_files: Optional[Dict[str, str]] = None
) -> Dict[str, Any]:
    """Executes and scores an individual firm in parallel, writing results to local disk and GCS.

    `task` carries the objective, its verifier, its spend ceiling and its
    action space. When absent the V1 task is synthesised from `objective`, so
    an un-migrated caller behaves exactly as before.

    `seed_files` is the previous generation's artifact, when the task asks for
    carryover. The firm continues from it rather than starting empty.
    """
    task = task or legacy_task(objective=objective)
    os.environ["GOOGLE_CLOUD_LOCATION"] = region
    print(f"=== PARALLEL WORKER: Firm Index {firm_index} (Generation {generation}) on Region {region} ===")

    resolved_pop_file = population_file
    if resolved_pop_file and not os.path.exists(resolved_pop_file):
        for candidate in [
            os.path.join("/app", resolved_pop_file.lstrip("/")),
            os.path.join(".", resolved_pop_file.lstrip("/")),
            os.path.join(os.path.dirname(__file__), "..", resolved_pop_file.lstrip("/"))
        ]:
            if os.path.exists(candidate):
                resolved_pop_file = candidate
                break

    if resolved_pop_file and os.path.exists(resolved_pop_file):
        with open(resolved_pop_file, "r") as f:
            raw = json.load(f)

        # Two shapes reach this code. `Breeder.write` emits an envelope --
        # {"generation": N, "name": ..., "population": [...]} -- while V1
        # archives are a bare list of genomes.
        #
        # This used to index `raw` directly, guarded by `firm_index <
        # len(raw)`. On the envelope that check compares against the number of
        # *keys*, which is 5, so index 0-4 sailed through and then failed with
        # `KeyError: 0`. The guard was real but measured the wrong collection,
        # which is worse than no guard: it reported the population as large
        # enough when it had not found the population at all.
        if isinstance(raw, dict):
            pop = raw.get("population")
            if pop is None:
                raise ValueError(
                    f"{resolved_pop_file} is an object with no `population` "
                    f"key (found {sorted(raw)[:6]}). Refusing to guess which "
                    f"field holds the genomes.")
        elif isinstance(raw, list):
            pop = raw
        else:
            raise ValueError(
                f"{resolved_pop_file} holds {type(raw).__name__}; expected a "
                f"list of genomes or an object with a `population` key.")

        if not isinstance(pop, list):
            raise ValueError(
                f"{resolved_pop_file}: `population` is "
                f"{type(pop).__name__}, expected a list.")
        if firm_index >= len(pop):
            raise IndexError(
                f"firm_index {firm_index} out of range for population size "
                f"{len(pop)} in {resolved_pop_file}")

        firm_genome = CompanyGenome.from_dict(pop[firm_index])
        company_id = firm_genome.company_id
        print(f"---> Loaded pre-bred genome from {resolved_pop_file}: "
              f"{company_id} ({firm_genome.total_agent_count} agents)")
    else:
        # Load seed template
        with open(seed_config_path, "r") as f:
            template = json.load(f)

        company_id = f"gen_{generation}_firm_{firm_index+1}"
        ceo = template["ceo"]
        ceo["temperature"] = max(0.2, min(1.0, 0.35 + (firm_index * 0.04)))
        
        firm_genome = CompanyGenome(
            company_id=company_id,
            generation=generation,
            parent_ids=["seed_root"],
            mutation_history=[f"Parallel Generation {generation} Variant {firm_index+1}"],
            ceo=ceo,
            departments=template["departments"]
        )

    print(f"---> Running {firm_genome.company_id} ({firm_genome.total_agent_count} agents)...")
    print(f"     {task.describe()}")
    verifier = getattr(task, "verifier", None)
    target_mod = getattr(verifier, "target_module", None)
    if not target_mod and hasattr(verifier, "benchmark") and hasattr(verifier, "task_id"):
        try:
            target_mod = verifier.benchmark.task(verifier.task_id).target_module
        except Exception:
            target_mod = None
    seed_files = dict(seed_files or {})
    if getattr(firm_genome, "code_overlays", None):
        for ov_path, ov_code in firm_genome.code_overlays.items():
            clean_ov = ov_path.lstrip("./")
            if not getattr(task, "carry_artifacts", False) and target_mod and clean_ov == target_mod.lstrip("./"):
                print(f"     [carry_artifacts=False] Excluded active target module {clean_ov} from workspace seed_files.")
                continue
            seed_files.setdefault(clean_ov, ov_code)
        print(f"     [LEVEL-3 RSI] Active code overlays on genome: {sorted(firm_genome.code_overlays.keys())}")
    if seed_files:
        print(f"     Inheriting {len(seed_files)} file(s) in workspace: {sorted(seed_files.keys())}")

    runner = HierarchicalCompanyRunner(
        firm_genome, budget=task.budget, seed_files=seed_files)

    max_iters = max(1, int(getattr(task, "max_iterations", 1)))
    total_elapsed = 0.0
    iterations_history: List[Dict[str, Any]] = []
    best_outcome = None
    best_workspace_files: Dict[str, str] = {}
    best_deliverable = ""
    best_briefs: Dict[str, str] = {}
    run_output: Dict[str, Any] = {}
    outcome = None

    for it in range(1, max_iters + 1):
        if it == 1:
            current_objective = task.objective
        else:
            for path, content in best_workspace_files.items():
                runner.workspace.write_file(path, content)
            runner.verification_loop.used = 0
            failures_list = (best_outcome.evidence.get("failures", [])
                             if best_outcome and best_outcome.evidence else [])
            failures_text = (
                "\n".join(f"  - {f}" for f in failures_list)
                if failures_list else f"  - {best_outcome.detail if best_outcome else 'verification failed'}"
            )
            current_objective = (
                f"{task.objective}\n\n"
                f"======================================================================\n"
                f"ITERATION {it}/{max_iters} — GROUND-TRUTH VERIFIER FEEDBACK\n"
                f"======================================================================\n"
                f"Your workspace already contains the files authored in previous iterations.\n"
                f"Best ground-truth score achieved so far: {best_outcome.score if best_outcome else 0.0}/100.0 "
                f"({best_outcome.detail if best_outcome else ''})\n"
                f"Failing ground-truth checks:\n"
                f"{failures_text}\n\n"
                f"INSTRUCTIONS FOR ITERATION {it}:\n"
                f"1. Read and inspect the existing implementation in your workspace using `Action: read_file`.\n"
                f"2. Fix the specific failing edge cases or assertion errors identified above without breaking passing tests.\n"
                f"3. Write the updated implementation back to the workspace file using `Action: write_file` and run `Action: verify` to confirm."
            )
            print(f"---> [ITERATION {it}/{max_iters}] Re-running {firm_genome.company_id} with ground-truth repair feedback...")

        run_output = runner.run(current_objective)
        total_elapsed += run_output.get("elapsed_seconds", 0.0)

        if run_output.get("budget_exhausted"):
            print(f" [BUDGET] {firm_genome.company_id} reached its ceiling; the "
                  f"deliverable is truncated. {run_output.get('budget')}")

        outcome = task.verifier.verify(Submission(
            files=run_output.get("workspace_files", {}),
            deliverable_text=run_output["final_deliverable"],
            workspace=runner.workspace,
        ))
        score_val = float(outcome.score) if outcome.score is not None else 0.0
        passed_flag = bool(outcome.evaluable and score_val >= 100.0)
        print(f" [Iteration {it}/{max_iters}] [{outcome.verifier}] {outcome.detail} (score={score_val})")

        iterations_history.append({
            "iteration": it,
            "score": score_val,
            "passed": passed_flag,
            "detail": outcome.detail,
            "failures": outcome.evidence.get("failures", []) if outcome.evidence else [],
            "elapsed_seconds": run_output.get("elapsed_seconds", 0.0),
            "cumulative_cost_usd": run_output.get("opex", {}).get("estimated_cost_usd", 0.0),
        })

        best_score_val = (float(best_outcome.score)
                          if best_outcome and best_outcome.score is not None else -1.0)
        curr_files = run_output.get("workspace_files", {})
        curr_bytes = sum(len(v) for v in curr_files.values())
        best_bytes = sum(len(v) for v in best_workspace_files.values())
        if best_outcome is None or score_val > best_score_val or (
            score_val == best_score_val and curr_bytes >= best_bytes
        ):
            best_outcome = outcome
            best_workspace_files = dict(curr_files)
            best_deliverable = run_output["final_deliverable"]
            best_briefs = dict(run_output.get("departmental_briefs", {}))
        else:
            print(f" [REGRESSION] Iteration {it} scored {score_val} ({curr_bytes}B) < best {best_score_val} ({best_bytes}B); restoring best workspace snapshot.")
            for path, content in best_workspace_files.items():
                runner.workspace.write_file(path, content)

        if passed_flag:
            print(f" [CONVERGED] {firm_genome.company_id} achieved 100.0% ground-truth verification on iteration {it}/{max_iters}!")
            break

        if run_output.get("budget_exhausted") or (task.budget and task.budget.working_exhausted):
            print(f" [STOP] Stopping iterative repair: budget exhausted after iteration {it}/{max_iters}.")
            break

    outcome = best_outcome
    run_output["workspace_files"] = best_workspace_files
    run_output["final_deliverable"] = best_deliverable
    run_output["departmental_briefs"] = best_briefs
    run_output["elapsed_seconds"] = round(total_elapsed, 2)
    run_output["iterations_used"] = len(iterations_history)
    run_output["iterations_history"] = iterations_history

    # Level 3 RSI: Promote 100%-verified hae/ modules into firm_genome.code_overlays
    if outcome and outcome.score is not None and float(outcome.score) >= 100.0:
        for path, content in best_workspace_files.items():
            clean_path = path.lstrip("./")
            if (clean_path.startswith("hae/") and clean_path.endswith(".py")
                    and "test" not in os.path.basename(clean_path)):
                firm_genome.code_overlays[clean_path] = content
                print(f" [LEVEL-3 RSI] Promoted 100%-verified {clean_path} ({len(content)} bytes) into {company_id}.code_overlays")

    # LLM Judge Evaluation
    print(f"---> LLM Judge scoring for {company_id}...")
    evaluator = StrategicFitnessEvaluator()
    eval_res = evaluator.evaluate(
        company_id=company_id,
        generation=generation,
        objective=task.objective,
        final_deliverable=run_output["final_deliverable"],
        departmental_briefs=run_output["departmental_briefs"],
        elapsed_seconds=run_output["elapsed_seconds"],
        token_usage=run_output["token_usage"],
        verification=outcome
    )

    gross_score = eval_res.fitness.fitness_score
    opex_data = run_output.get("opex", {})
    cost_penalty = opex_data.get("cost_penalty", 0.0)
    efficiency_bonus = opex_data.get("efficiency_bonus", 0.0)

    # Net fitness. Gate failures are NOT subtracted here: they already enter
    # the gross score as `execution_integrity`, worth 30%. Subtracting
    # `report.score_penalty` as well would count the same failures twice.
    net_score = round(max(0.0, min(100.0, gross_score - cost_penalty + efficiency_bonus)), 2)
    eval_res.fitness.fitness_score = net_score

    if getattr(eval_res.fitness, "evaluation_failed", False):
        print(f" [WARNING] {company_id} has a FAILED evaluation and scores 0.0. "
              f"It must be excluded from the breeding pool.")

    cost_usd = opex_data.get("estimated_cost_usd", 0.0)
    budget_usd = opex_data.get("budget_usd", 0.50)
    print(f" [OpEx Balance Sheet] Cost: ${cost_usd:.4f} USD (Budget: ${budget_usd:.2f}) | Penalty: -{cost_penalty} pts | Bonus: +{efficiency_bonus} pts")
    print(f" FINAL NET FITNESS: {eval_res.fitness.fitness_score}/100 (Gross: {gross_score}) | Iterations: {len(iterations_history)}/{max_iters}")

    result_payload = {
        "company_id": company_id,
        "generation": generation,
        "region": region,
        "fitness_score": eval_res.fitness.fitness_score,
        "gross_score": gross_score,
        "strategic_depth": eval_res.fitness.strategic_depth,
        "technical_feasibility": eval_res.fitness.technical_feasibility,
        "cross_functional_coherence": eval_res.fitness.cross_functional_coherence,
        "risk_mitigation": eval_res.fitness.risk_mitigation,
        "actionability": eval_res.fitness.actionability_and_synthesis,
        "execution_integrity": getattr(eval_res.fitness, "execution_integrity", 0.0),
        "execution_evaluable": getattr(eval_res.fitness, "execution_evaluable", False),
        "evaluation_failed": getattr(eval_res.fitness, "evaluation_failed", False),
        "elapsed_seconds": eval_res.fitness.elapsed_seconds,
        "token_usage": eval_res.fitness.token_usage,
        "iterations_used": len(iterations_history),
        "iterations_history": iterations_history,
        "verification": outcome.evidence if outcome.gate_status else outcome.to_dict(),
        "verifier_outcome": outcome.to_dict(),
        "task": task.to_dict(),
        "opex": opex_data,
        "genome": firm_genome.to_dict(),
        "run_output": run_output,
        "evaluation": eval_res.to_dict()
    }

    # Write locally
    gen_dir = os.path.join(output_dir, f"generation_{generation}")
    os.makedirs(gen_dir, exist_ok=True)
    local_path = os.path.join(gen_dir, f"{company_id}_result.json")
    with open(local_path, "w") as f:
        json.dump(result_payload, f, indent=2, default=str)

    # Sync to GCS
    if gcs_bucket:
        try:
            from google.cloud import storage
            client = storage.Client()
            bucket = client.bucket(gcs_bucket)
            blob = bucket.blob(f"parallel_runs/generation_{generation}/{company_id}_result.json")
            blob.upload_from_filename(local_path)
            print(f" Synced scorecard to: gs://{gcs_bucket}/parallel_runs/generation_{generation}/{company_id}_result.json")
        except Exception as e1:
            try:
                import urllib.request
                from hae.infra.llm import get_adc_access_token
                token = get_adc_access_token()
                if token:
                    object_name = f"parallel_runs/generation_{generation}/{company_id}_result.json"
                    url = f"https://storage.googleapis.com/upload/storage/v1/b/{gcs_bucket}/o?uploadType=media&name={object_name}"
                    with open(local_path, "rb") as f_data:
                        data_bytes = f_data.read()
                    req = urllib.request.Request(
                        url,
                        data=data_bytes,
                        headers={
                            "Authorization": f"Bearer {token}",
                            "Content-Type": "application/json"
                        },
                        method="POST"
                    )
                    with urllib.request.urlopen(req, timeout=30) as resp:
                        print(f" Synced scorecard via REST to: gs://{gcs_bucket}/{object_name}")
            except Exception as e2:
                print(f" [WARNING] GCS sync fallback failed: {e2}")

    return result_payload

def main():
    parser = argparse.ArgumentParser(description="Parallel Worker for Hierarchical Agent Evolution")
    parser.add_argument("--generation", type=int, default=1, help="Evolutionary generation index")
    parser.add_argument("--firm-index", type=int, default=None, help="Index of firm within population (defaults to JOB_COMPLETION_INDEX)")
    parser.add_argument("--objective", type=str, default="5-Year Hyperscale AI Compute Strategy", help="Strategic objective")
    parser.add_argument("--output-dir", type=str, default="/data/outputs", help="Directory for local outputs")
    parser.add_argument("--region", type=str, default="us-east4", help="GCP Region for Vertex AI calls")
    parser.add_argument("--gcs-bucket", type=str, default=os.environ.get("GCS_BUCKET", "YOUR_GCS_BUCKET"), help="GCS Bucket for persistent results")
    parser.add_argument("--seed-config", type=str, default="templates/default_company.json", help="Path to seed company genome template")
    parser.add_argument("--population-file", type=str, default=None, help="Path to pre-bred JSON population array")
    parser.add_argument("--task-file", type=str, default=None,
                        help="Task declaration (JSON): objective, verifier, "
                             "budget and capabilities in one place. Falls back "
                             "to --objective with the V1 defaults.")
    parser.add_argument("--seed-files", type=str, default=None,
                        help="JSON map of path->content to pre-populate the "
                             "workspace with, so this firm continues from the "
                             "previous generation's artifact instead of an "
                             "empty directory.")
    parser.add_argument("--skip-preflight", action="store_true",
                        help="Skip the startup environment probe. Only for "
                             "offline tests; a real run should never set it.")

    args = parser.parse_args()

    # If firm-index not supplied via CLI, check Kubernetes Indexed Job environment variable
    firm_idx = args.firm_index
    if firm_idx is None:
        idx_env = os.environ.get("JOB_COMPLETION_INDEX")
        if idx_env is not None:
            firm_idx = int(idx_env)
        else:
            firm_idx = 0

    # Detection-only preflight. Repair is deliberately NOT available here: a
    # worker that can grant itself IAM is a privilege escalation, and Latchkey
    # would reap that permission too. The operator repairs before launch with
    #     python -m hae.cli --mode preflight --repair
    #
    # What this buys us is failing in two seconds with a legible message
    # instead of thirty pods each burning a retry budget against a 403. In
    # Generation 11 that distinction was seven pods and about an hour.
    if not args.skip_preflight:
        os.environ.setdefault("GOOGLE_CLOUD_LOCATION", args.region)
        os.environ.setdefault("GCS_BUCKET", args.gcs_bucket)
        # A fresh config: DEFAULT_CONFIG resolved the environment at import
        # time, before the two lines above ran.
        report = run_preflight(config=EvolutionConfig())
        if not report.ok:
            print(report.render(), flush=True)
            print(f"[Firm {firm_idx}] Aborting before any billed work.",
                  flush=True)
            raise SystemExit(2)
        print(f"[Firm {firm_idx}] Preflight OK "
              f"({len(report.checks)} checks).", flush=True)

    task = Task.load(args.task_file) if args.task_file else legacy_task(
        objective=args.objective)

    seed_files = None
    if args.seed_files:
        with open(args.seed_files, "r", encoding="utf-8") as fh:
            seed_files = json.load(fh)
        if not task.carry_artifacts:
            # Refuse rather than quietly honour it: a run that inherits work
            # its task says it should not have inherited produces a fitness
            # number that cannot be compared to anything.
            raise SystemExit(
                f"--seed-files given but task {task.task_id!r} sets "
                f"carry_artifacts=false. Inherited work would make this "
                f"firm's score incomparable to the rest of its generation.")

    evaluate_single_firm(
        task=task,
        seed_files=seed_files,
        firm_index=firm_idx,
        generation=args.generation,
        objective=args.objective,
        output_dir=args.output_dir,
        region=args.region,
        gcs_bucket=args.gcs_bucket,
        seed_config_path=args.seed_config,
        population_file=args.population_file
    )

if __name__ == "__main__":
    main()
