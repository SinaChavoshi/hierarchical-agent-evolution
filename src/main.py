"""CLI entrypoint for Hierarchical Agent Evolution."""

import os
import sys
import json
import argparse
from .schema import CompanyGenome
from .company import HierarchicalCompanyRunner
from .evaluator import StrategicFitnessEvaluator
from .engine import EvolutionaryTournamentEngine
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
    parser.add_argument("--seed-config", type=str,
                        default=os.path.join(os.path.dirname(__file__), "../templates/default_company.json"),
                        help="Path to Generation 0 seed company JSON")
    parser.add_argument("--population-size", type=int, default=DEFAULT_CONFIG.population_size,
                        help="Number of competing virtual organizations per generation")
    parser.add_argument("--generations", type=int, default=DEFAULT_CONFIG.num_generations,
                        help="Number of evolutionary generations to execute")
    parser.add_argument("--output-dir", type=str, default=DEFAULT_CONFIG.local_output_dir,
                        help="Directory to store outputs, transcripts, and evaluation logs")
    return parser.parse_args()

def main():
    args = parse_args()
    print(f"Hierarchical Agent Evolution - Mode: {args.mode}")
    print(f"Target GCP Project: {DEFAULT_CONFIG.project_id} (Location: {DEFAULT_CONFIG.location})")
    print(f"Worker Model: {DEFAULT_CONFIG.worker_model} | Executive Model: {DEFAULT_CONFIG.executive_model}")

    with open(args.seed_config, "r") as f:
        seed_data = json.load(f)
    seed_genome = CompanyGenome(**seed_data)
    print(f"Loaded Seed Genome: {seed_genome.company_id} with {seed_genome.total_agent_count} virtual agents.")

    if args.mode == "single-firm":
        print(f"\nRunning Single Firm Execution on Objective:\n{args.objective[:120]}...\n")
        runner = HierarchicalCompanyRunner(seed_genome)
        result = runner.run(args.objective)
        
        evaluator = StrategicFitnessEvaluator()
        eval_result = evaluator.evaluate(
            company_id=seed_genome.company_id,
            generation=0,
            objective=args.objective,
            final_deliverable=result["final_deliverable"],
            departmental_briefs=result["departmental_briefs"],
            elapsed_seconds=result["elapsed_seconds"],
            estimated_tokens=result["estimated_tokens"]
        )

        print("\n" + "="*80)
        print(f"FIRM EXECUTION COMPLETE: {seed_genome.company_id}")
        print(f"Overall Fitness: {eval_result.fitness.overall_score}/100")
        print(f"Strategic Depth: {eval_result.fitness.strategic_depth}/100")
        print(f"Technical Feasibility: {eval_result.fitness.technical_feasibility}/100")
        print(f"Cross-Functional Coherence: {eval_result.fitness.cross_functional_coherence}/100")
        print(f"Risk Mitigation: {eval_result.fitness.risk_mitigation}/100")
        print(f"Actionability: {eval_result.fitness.actionability_and_synthesis}/100")
        print("="*80)
        print("\n--- MASTER STRATEGIC DELIVERABLE ---\n")
        print(result["final_deliverable"])

    elif args.mode == "tournament":
        print(f"\nInitiating Evolutionary Tournament ({args.population_size} firms x {args.generations} generations)...")
        engine = EvolutionaryTournamentEngine(
            objective=args.objective,
            seed_genome=seed_genome,
            population_size=args.population_size,
            num_generations=args.generations,
            output_dir=args.output_dir
        )
        tournament_summary = engine.execute_tournament()
        print(f"\nTournament Completed! Summary written to: {tournament_summary['summary_file']}")

if __name__ == "__main__":
    main()
