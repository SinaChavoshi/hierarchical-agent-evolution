"""CLI entrypoint for Hierarchical Agent Evolution."""

import os
import sys
import json
import argparse
from .schema import CompanyGenome
from .company import HierarchicalCompanyRunner
from .evaluator import StrategicFitnessEvaluator
from .engine import EvolutionaryTournamentEngine
from .sandbox_verifier import DeterministicSandboxVerifier
from .llm_factory import detect_llm_provider, resolve_model_for_provider
from .config import DEFAULT_CONFIG

DEFAULT_STRATEGIC_OBJECTIVE = (
    "Formulate an unassailable 5-year commercial and technical strategy for an enterprise "
    "aiming to establish a next-generation hyperscale AI compute cloud (100k+ custom accelerators). "
    "Address physical power delivery and cooling limits, high-bandwidth interconnect fabric, "
    "enterprise developer APIs, capital expenditure financing, unit economics, and competitive "
    "counter-moves by incumbent cloud hyperscalers."
)

def parse_args():
    parser = argparse.ArgumentParser(description="Hierarchical Agent Evolution System")
    parser.add_argument("--mode", choices=["tournament", "single-firm"], default="tournament",
                        help="Execution mode: full evolutionary tournament or single firm evaluation")
    parser.add_argument("--objective", type=str, default=DEFAULT_STRATEGIC_OBJECTIVE,
                        help="The complex open-ended strategic research objective")
    parser.add_argument("--config", "--seed-config", dest="seed_config", type=str,
                        default=os.path.join(os.path.dirname(__file__), "../templates/default_company.json"),
                        help="Path to Generation 0 seed company JSON or evolved genome")
    parser.add_argument("--provider", "--llm-provider", dest="llm_provider",
                        choices=["auto", "vertex", "gemini_api", "openai", "anthropic", "ollama", "vllm"],
                        default=None, help="LLM Provider override (auto, vertex, gemini_api, openai, anthropic, ollama, vllm)")
    parser.add_argument("--runtime", choices=["local", "parallel-local", "k8s"], default="local",
                        help="Runtime engine: sequential local, parallel multi-threaded local, or distributed k8s")
    parser.add_argument("--workers", type=int, default=4,
                        help="Number of concurrent worker threads when running in parallel-local runtime")
    parser.add_argument("--population-size", type=int, default=DEFAULT_CONFIG.population_size,
                        help="Number of competing virtual organizations per generation")
    parser.add_argument("--generations", type=int, default=DEFAULT_CONFIG.num_generations,
                        help="Number of evolutionary generations to execute")
    parser.add_argument("--output-dir", type=str, default=DEFAULT_CONFIG.local_output_dir,
                        help="Directory to store outputs, transcripts, and evaluation logs")
    return parser.parse_args()

def main():
    args = parse_args()
    if args.llm_provider:
        DEFAULT_CONFIG.llm_provider = args.llm_provider
        os.environ["LLM_PROVIDER"] = args.llm_provider

    provider = detect_llm_provider()
    worker_m = resolve_model_for_provider(DEFAULT_CONFIG.worker_model, provider, tier="worker")
    exec_m = resolve_model_for_provider(DEFAULT_CONFIG.executive_model, provider, tier="executive")

    print(f"Hierarchical Agent Evolution - Mode: {args.mode} | Runtime: {args.runtime}")
    print(f"Active LLM Provider: {provider.upper()}")
    print(f"Worker Model: {worker_m} | Executive Model: {exec_m}")
    if provider == "vertex":
        print(f"Target GCP Project: {DEFAULT_CONFIG.project_id} (Location: {DEFAULT_CONFIG.location})")

    with open(args.seed_config, "r") as f:
        seed_data = json.load(f)
    seed_genome = CompanyGenome(**seed_data)
    print(f"Loaded Seed Genome: {seed_genome.company_id} with {seed_genome.total_agent_count} virtual agents.")

    if args.mode == "single-firm":
        print(f"\nRunning Single Firm Execution on Objective:\n{args.objective[:120]}...\n")
        runner = HierarchicalCompanyRunner(seed_genome)
        result = runner.run(args.objective)
        
        # Step 1: Deterministic Sandbox Verification
        verifier = DeterministicSandboxVerifier()
        v_score = verifier.verify_package(seed_genome.company_id, result["final_deliverable"], workspace=runner.workspace)
        print(f"\n[Deterministic Gate Verification] {v_score.details} (Penalty: -{v_score.score_penalty} pts)")

        # Step 2: LLM Strategic Evaluation
        evaluator = StrategicFitnessEvaluator()
        eval_result = evaluator.evaluate(
            company_id=seed_genome.company_id,
            generation=seed_genome.generation,
            objective=args.objective,
            final_deliverable=result["final_deliverable"],
            departmental_briefs=result["departmental_briefs"],
            elapsed_seconds=result["elapsed_seconds"],
            estimated_tokens=result["estimated_tokens"]
        )

        gross_score = eval_result.fitness.overall_score
        net_score = max(0.0, round(gross_score - v_score.score_penalty, 2))
        eval_result.fitness.overall_score = net_score

        print("\n" + "="*80)
        print(f"FIRM EXECUTION COMPLETE: {seed_genome.company_id}")
        print(f"Net Fitness: {net_score}/100 (Gross: {gross_score}, Penalty: -{v_score.score_penalty})")
        print(f"Strategic Depth: {eval_result.fitness.strategic_depth}/100")
        print(f"Technical Feasibility: {eval_result.fitness.technical_feasibility}/100")
        print(f"Cross-Functional Coherence: {eval_result.fitness.cross_functional_coherence}/100")
        print(f"Risk Mitigation: {eval_result.fitness.risk_mitigation}/100")
        print(f"Actionability: {eval_result.fitness.actionability_and_synthesis}/100")
        opex_data = result.get("opex", {})
        if opex_data:
            print(f"OpEx: ${opex_data.get('estimated_cost_usd', 0.0):.4f} (Tokens: {opex_data.get('total_tokens', 0):,})")
        print("="*80)
        print("\n--- MASTER STRATEGIC DELIVERABLE ---\n")
        print(result["final_deliverable"])

    elif args.mode == "tournament":
        parallel_w = args.workers if args.runtime == "parallel-local" else 1
        print(f"\nInitiating Evolutionary Tournament ({args.population_size} firms x {args.generations} generations, Workers: {parallel_w})...")
        engine = EvolutionaryTournamentEngine(
            objective=args.objective,
            seed_genome=seed_genome,
            population_size=args.population_size,
            num_generations=args.generations,
            output_dir=args.output_dir,
            parallel_workers=parallel_w
        )
        tournament_summary = engine.execute_tournament()
        summary_path = tournament_summary.get("summary_file", os.path.join(args.output_dir, "tournament_summary.json"))
        print(f"\nTournament Completed! Summary written to: {summary_path}")

if __name__ == "__main__":
    main()
