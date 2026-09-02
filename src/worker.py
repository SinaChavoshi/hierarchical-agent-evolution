"""Standalone Parallel Worker Pod Evaluator for GKE Indexed Jobs."""

import os
import sys
import json
import time
import argparse
from typing import Dict, Any

from .schema import CompanyGenome, EvaluationResult
from .company import HierarchicalCompanyRunner
from .evaluator import StrategicFitnessEvaluator
from .sandbox_verifier import DeterministicSandboxVerifier
from .telemetry import ResearchLedger

def evaluate_single_firm(
    firm_index: int,
    generation: int,
    objective: str,
    output_dir: str,
    region: str,
    gcs_bucket: str = "gemle-gke-dev-agent-evolution",
    seed_config_path: str = "templates/default_company.json"
) -> Dict[str, Any]:
    """Executes and scores an individual firm in parallel, writing results to local disk and GCS."""
    os.environ["GOOGLE_CLOUD_LOCATION"] = region
    print(f"=== PARALLEL WORKER: Firm Index {firm_index} (Generation {generation}) on Region {region} ===")

    # Load seed template
    with open(seed_config_path, "r") as f:
        template = json.load(f)

    # Deterministically construct firm genome for this index
    company_id = f"gen_{generation}_firm_{firm_index+1}"
    ceo = template["ceo"]
    # Jitter temperature based on index to create diversity
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

    # Deterministic Gate Check
    verifier = DeterministicSandboxVerifier()
    v_score = verifier.verify_package(company_id, run_output["final_deliverable"])
    print(f" [Deterministic Gate] {v_score.details} (Penalty: -{v_score.score_penalty} pts)")

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
        estimated_tokens=run_output["estimated_tokens"]
    )

    # Apply penalty
    eval_res.fitness.overall_score = max(0.0, round(eval_res.fitness.overall_score - v_score.score_penalty, 2))
    print(f" FINAL SCORE: {eval_res.fitness.overall_score}/100")

    result_payload = {
        "company_id": company_id,
        "generation": generation,
        "region": region,
        "overall_score": eval_res.fitness.overall_score,
        "strategic_depth": eval_res.fitness.strategic_depth,
        "technical_feasibility": eval_res.fitness.technical_feasibility,
        "cross_functional_coherence": eval_res.fitness.cross_functional_coherence,
        "risk_mitigation": eval_res.fitness.risk_mitigation,
        "actionability": eval_res.fitness.actionability_and_synthesis,
        "elapsed_seconds": eval_res.fitness.elapsed_seconds,
        "estimated_tokens": eval_res.fitness.token_count,
        "verification": v_score.__dict__,
        "genome": firm_genome.model_dump(),
        "run_output": run_output,
        "evaluation": eval_res.model_dump()
    }

    # Write locally
    gen_dir = os.path.join(output_dir, f"generation_{generation}")
    os.makedirs(gen_dir, exist_ok=True)
    local_path = os.path.join(gen_dir, f"{company_id}_result.json")
    with open(local_path, "w") as f:
        json.dump(result_payload, f, indent=2)

    # Sync to GCS
    if gcs_bucket:
        try:
            import subprocess
            gcs_dest = f"gs://{gcs_bucket}/parallel_runs/generation_{generation}/{company_id}_result.json"
            subprocess.run(["gcloud", "storage", "cp", local_path, gcs_dest], check=False)
            print(f" Synced scorecard to: {gcs_dest}")
        except Exception as e:
            print(f" Warning: Failed to sync to GCS: {e}")

    return result_payload

def main():
    parser = argparse.ArgumentParser(description="Parallel Firm Evaluation Worker")
    parser.add_argument("--firm-index", type=int, default=int(os.environ.get("JOB_COMPLETION_INDEX", "0")))
    parser.add_argument("--generation", type=int, default=0)
    parser.add_argument("--objective", type=str, required=True)
    parser.add_argument("--region", type=str, default=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-east4"))
    parser.add_argument("--output-dir", type=str, default="/data/outputs")
    parser.add_argument("--gcs-bucket", type=str, default=os.environ.get("GCS_BUCKET", "gemle-gke-dev-agent-evolution"))
    args = parser.parse_args()

    evaluate_single_firm(
        firm_index=args.firm_index,
        generation=args.generation,
        objective=args.objective,
        output_dir=args.output_dir,
        region=args.region,
        gcs_bucket=args.gcs_bucket
    )

if __name__ == "__main__":
    main()
