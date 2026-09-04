"""Evolutionary Tournament Engine supporting 50-firm 3-way breeding, verification, and telemetry."""

import os
import json
import time
from typing import List, Dict, Any, Tuple
from .schema import CompanyGenome, EvaluationResult
from .company import HierarchicalCompanyRunner
from .evaluator import StrategicFitnessEvaluator
from .mutator import OrganizationalMutator
from .breeding import ThreeWayBreedingEngine
from .sandbox_verifier import DeterministicSandboxVerifier
from .telemetry import ResearchLedger

class EvolutionaryTournamentEngine:
    """Manages the full lifecycle of hierarchical multi-agent evolution tournaments."""

    def __init__(
        self,
        seed_genome: CompanyGenome,
        objective: str,
        output_dir: str,
        population_size: int = 50,
        top_k_survivors: int = 5,
        num_generations: int = 2,
        gcs_bucket: str = None
    ):
        self.seed_genome = seed_genome
        self.objective = objective
        self.output_dir = output_dir
        self.population_size = population_size
        self.top_k_survivors = top_k_survivors
        self.num_generations = num_generations
        self.gcs_bucket = gcs_bucket

        self.evaluator = StrategicFitnessEvaluator()
        self.mutator = OrganizationalMutator()
        self.breeding_engine = ThreeWayBreedingEngine(top_k=top_k_survivors, total_population=population_size)
        self.verifier = DeterministicSandboxVerifier()
        
        run_id = f"run_{int(time.time())}"
        self.ledger = ResearchLedger(run_id=run_id, output_dir=output_dir, gcs_bucket=gcs_bucket)

    def initialize_population(self) -> List[CompanyGenome]:
        """Generates the initial Generation 0 population."""
        population: List[CompanyGenome] = []
        # Clone 0: Baseline Seed
        clone_seed = self.seed_genome.model_copy(deep=True)
        clone_seed.company_id = "gen_0_firm_1"
        clone_seed.generation = 0
        population.append(clone_seed)

        # Generate diverse initial variations
        for i in range(1, self.population_size):
            variant = self.seed_genome.model_copy(deep=True)
            variant.company_id = f"gen_0_firm_{i+1}"
            variant.generation = 0
            # Jitter temperature
            variant.ceo.temperature = max(0.2, min(1.0, 0.4 + (i * 0.05)))
            variant.mutation_history.append(f"Gen 0 Diversity Variant {i+1}")
            population.append(variant)

        return population

    def run_generation(self, population: List[CompanyGenome], generation_idx: int) -> Tuple[List[Tuple[CompanyGenome, EvaluationResult]], List[CompanyGenome]]:
        """Executes, verifies, and scores all firms in a generation, then breeds the next."""
        print(f"\n{'='*70}")
        print(f"STARTING GENERATION {generation_idx} ({len(population)} Competing Firms)")
        print(f"{'='*70}")

        gen_dir = os.path.join(self.output_dir, f"generation_{generation_idx}")
        os.makedirs(gen_dir, exist_ok=True)

        gen_results: List[Tuple[CompanyGenome, EvaluationResult]] = []
        raw_firm_outputs: List[Dict[str, Any]] = []

        for firm in population:
            print(f"\n---> Executing Firm: {firm.company_id} ({firm.total_agent_count} agents)...")
            runner = HierarchicalCompanyRunner(firm)
            run_output = runner.run(self.objective)

            # Step 1: Deterministic Sandbox Verification
            v_score = self.verifier.verify_package(firm.company_id, run_output["final_deliverable"], workspace=runner.workspace)
            print(f" [Deterministic Gate] {v_score.details} (Penalty: -{v_score.score_penalty} pts)")

            # Step 2: LLM Strategic Evaluation
            print(f"---> Evaluating deliverables for {firm.company_id} via LLM Judge...")
            eval_result = self.evaluator.evaluate(
                company_id=firm.company_id,
                generation=generation_idx,
                objective=self.objective,
                final_deliverable=run_output["final_deliverable"],
                departmental_briefs=run_output["departmental_briefs"],
                elapsed_seconds=run_output["elapsed_seconds"],
                estimated_tokens=run_output["estimated_tokens"]
            )

            # Apply verification penalty
            eval_result.fitness.overall_score = max(0.0, round(eval_result.fitness.overall_score - v_score.score_penalty, 2))

            print(f" Score: {eval_result.fitness.overall_score}/100 "
                  f"(Strat: {eval_result.fitness.strategic_depth}, Tech: {eval_result.fitness.technical_feasibility}, "
                  f"Risk: {eval_result.fitness.risk_mitigation})")

            gen_results.append((firm, eval_result))
            raw_firm_outputs.append({
                "company_id": firm.company_id,
                "overall_score": eval_result.fitness.overall_score,
                "strategic_depth": eval_result.fitness.strategic_depth,
                "technical_feasibility": eval_result.fitness.technical_feasibility,
                "cross_functional_coherence": eval_result.fitness.cross_functional_coherence,
                "risk_mitigation": eval_result.fitness.risk_mitigation,
                "actionability": eval_result.fitness.actionability_and_synthesis,
                "elapsed_seconds": eval_result.fitness.elapsed_seconds,
                "estimated_tokens": eval_result.fitness.token_count,
                "verification": v_score.__dict__
            })

            # Save individual firm result to disk
            firm_output_file = os.path.join(gen_dir, f"{firm.company_id}_result.json")
            with open(firm_output_file, "w") as f:
                json.dump({
                    "genome": firm.model_dump(),
                    "evaluation": eval_result.model_dump(),
                    "run_output": run_output,
                    "verification": v_score.__dict__
                }, f, indent=2)

        # Sort leaderboard by overall score descending
        gen_results.sort(key=lambda x: x[1].fitness.overall_score, reverse=True)
        raw_firm_outputs.sort(key=lambda x: x["overall_score"], reverse=True)

        print(f"\n--- GENERATION {generation_idx} LEADERBOARD ---")
        for rank, (f_genome, e_res) in enumerate(gen_results, 1):
            print(f"#{rank} {f_genome.company_id} | Score: {e_res.fitness.overall_score:.2f} | Agents: {f_genome.total_agent_count}")

        # Record to Research Telemetry Ledger
        self.ledger.record_generation(
            generation_idx=generation_idx,
            leaderboard=raw_firm_outputs,
            firm_results=raw_firm_outputs
        )

        # Check if final generation
        if generation_idx >= self.num_generations - 1:
            return gen_results, []

        # 3-Way Breeding Pool for Next Generation
        print(f"\n---> Breeding Next Generation via 3-Way Search (Top {self.top_k_survivors} Survivors -> {self.population_size} Offspring)...")
        next_population = self.breeding_engine.produce_next_generation(
            ranked_population=gen_results,
            target_generation=generation_idx + 1
        )

        return gen_results, next_population

    def run_tournament(self) -> Dict[str, Any]:
        """Runs the complete multi-generational evolutionary tournament."""
        current_pop = self.initialize_population()
        all_generation_champions: List[Dict[str, Any]] = []

        for g in range(self.num_generations):
            ranked_results, next_pop = self.run_generation(current_pop, g)
            best_firm, best_eval = ranked_results[0]
            all_generation_champions.append({
                "generation": g,
                "champion_id": best_firm.company_id,
                "score": best_eval.fitness.overall_score,
                "genome": best_firm.model_dump()
            })
            if not next_pop:
                break
            current_pop = next_pop

        overall_champion = all_generation_champions[-1]
        print(f"\n{'='*70}")
        print(f"TOURNAMENT COMPLETE! CHAMPION FIRM: {overall_champion['champion_id']}")
        print(f"Fitness: {overall_champion['score']}/100")
        print(f"{'='*70}")

        summary_path = os.path.join(self.output_dir, "tournament_summary.json")
        summary = {
            "objective": self.objective,
            "generations_completed": self.num_generations,
            "overall_champion": overall_champion,
            "champions_history": all_generation_champions,
            "summary_file": summary_path
        }

        with open(summary_path, "w") as f:
            json.dump(summary, f, indent=2)

        self.ledger.finalize(overall_champion)
        return summary

    def execute_tournament(self) -> Dict[str, Any]:
        return self.run_tournament()
