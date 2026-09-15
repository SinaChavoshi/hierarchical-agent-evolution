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
from hae.evaluation.harness import ExecutionHarness
from hae.infra.telemetry import ResearchLedger
from hae.infra.preflight import run_preflight
from hae.infra.config import EvolutionConfig

def evaluate_single_firm(
    firm_index: int,
    generation: int,
    objective: str,
    output_dir: str,
    region: str,
    gcs_bucket: str = os.environ.get("GCS_BUCKET", "YOUR_GCS_BUCKET"),
    seed_config_path: str = "templates/default_company.json",
    population_file: Optional[str] = None
) -> Dict[str, Any]:
    """Executes and scores an individual firm in parallel, writing results to local disk and GCS."""
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
            pop = json.load(f)
        if firm_index < len(pop):
            firm_genome = CompanyGenome(**pop[firm_index])
            company_id = firm_genome.company_id
            print(f"---> Loaded pre-bred genome from {resolved_pop_file}: {company_id} ({firm_genome.total_agent_count} agents)")
        else:
            raise IndexError(f"firm_index {firm_index} out of range for population size {len(pop)}")
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
    runner = HierarchicalCompanyRunner(firm_genome)
    run_output = runner.run(objective)

    # Execution gates. Runs the code the firm actually wrote.
    report = ExecutionHarness().verify_workspace(
        runner.workspace, run_output["final_deliverable"])
    print(f" {report.summary()}")

    # LLM Judge Evaluation
    print(f"---> LLM Judge scoring for {company_id}...")
    evaluator = StrategicFitnessEvaluator()
    eval_res = evaluator.evaluate(
        company_id=company_id,
        generation=generation,
        objective=objective,
        final_deliverable=run_output["final_deliverable"],
        departmental_briefs=run_output["departmental_briefs"],
        elapsed_seconds=run_output["elapsed_seconds"],
        token_usage=run_output["token_usage"],
        verification=report
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
    print(f" FINAL NET FITNESS: {eval_res.fitness.fitness_score}/100 (Gross: {gross_score})")

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
        "verification": report.to_dict(),
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

    evaluate_single_firm(
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
