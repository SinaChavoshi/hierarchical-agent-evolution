"""Command line entry point.

Four modes:

    tournament   Run a population of firms for one or more generations.
    single-firm  Run one firm against one objective. Useful for debugging.
    breed        Produce the next generation's population from a declarative
                 generation spec. Replaces ten one-off breeding scripts.
    benchmark    Grade a firm's workspace against the self-hosting benchmark,
                 or print the objective for a benchmark task.
    preflight    Verify the environment can actually run a tournament before
                 one is launched, and optionally repair IAM first.
"""

import os
import sys
import json
import argparse
from hae.genome.schema import CompanyGenome
from hae.runtime.company import HierarchicalCompanyRunner
from hae.evaluation.judge import StrategicFitnessEvaluator
from hae.orchestration.engine import EvolutionaryTournamentEngine
from hae.evaluation.harness import ExecutionHarness
from hae.infra.llm import detect_llm_provider, resolve_model_for_provider
from hae.infra.config import DEFAULT_CONFIG
from hae.orchestration.breeder import breed_generation
from hae.evaluation.benchmark import TASKS, SelfHostingBenchmark
from hae.infra.preflight import run_preflight
from hae.task import Task, legacy_task
from hae.orchestration.controller import (
    CompletenessGate, GenerationController, StoppingCriteria)
from hae.orchestration.runtimes import GcsHarvest, KubernetesRuntime

# The V1 objective now lives with the Task that describes it, so there is one
# copy rather than one per entry point.
from hae.task.spec import LEGACY_OBJECTIVE as DEFAULT_STRATEGIC_OBJECTIVE

def parse_args():
    parser = argparse.ArgumentParser(description="Hierarchical Agent Evolution System")
    parser.add_argument("--mode",
                        choices=["tournament", "single-firm", "breed",
                                 "benchmark", "preflight", "campaign"],
                        default="tournament",
                        help="tournament | single-firm | breed | benchmark | "
                             "preflight | campaign")
    parser.add_argument("--specs", type=str, nargs="+", default=None,
                        help="(--mode campaign) Generation specs to run in "
                             "order, e.g. configs/generations/gen1.json ...")
    parser.add_argument("--max-total-usd", type=float, default=None,
                        help="(--mode campaign) Ceiling on total spend across "
                             "all generations. Without it an unattended loop "
                             "has no brake.")
    parser.add_argument("--ledger", type=str,
                        default="experiments/v2/ledger.json",
                        help="(--mode campaign) Where per-generation results "
                             "are appended.")
    parser.add_argument("--repair", action="store_true",
                        help="(--mode preflight) Re-grant missing IAM roles "
                             "before probing. Latchkey reaps them on this "
                             "project, so this is expected to be needed often.")
    parser.add_argument("--service-account", type=str, default=None,
                        help="(--mode preflight --repair) Tournament GSA to "
                             "grant roles to. Defaults to $AGENT_GSA.")
    parser.add_argument("--skip-gcs", action="store_true",
                        help="(--mode preflight) Skip the bucket write probe.")
    parser.add_argument("--task-file", type=str, default=None,
                        help="Path to a task declaration (JSON). Supersedes "
                             "--objective: binds the objective, its verifier, "
                             "its spend ceiling and its action space together.")
    parser.add_argument("--budget-usd", type=float, default=None,
                        help="Hard spend ceiling for a single firm. Enforced -- "
                             "calls are refused at the limit, unlike the genome's "
                             "budget_usd which only adjusted the score afterwards.")
    parser.add_argument("--generation-spec", type=str, default=None,
                        help="Path to configs/generations/genNN.json (--mode breed)")
    parser.add_argument("--task", type=str, default=None,
                        choices=sorted(TASKS),
                        help="Self-hosting benchmark task id (--mode benchmark)")
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

def run_campaign(args) -> int:
    """Runs generations back to back until a stopping criterion fires.

    This is the unattended path. It preflights before *every* generation rather
    than once at the start, because Latchkey reaps the IAM bindings on its own
    schedule and a check that passed an hour ago says nothing about now.
    """
    if not args.specs:
        print("--mode campaign requires --specs configs/generations/genNN.json ...")
        return 2

    task = resolve_task(args)
    print(f"Campaign task: {task.describe()}")
    if not task.is_verified:
        print("REFUSING: an unattended campaign against a task with no "
              "ground-truth verifier would optimise a judge-only score, which "
              "saturates. Declare a verifier in the task file.")
        return 2

    bucket = DEFAULT_CONFIG.require_bucket()
    controller = GenerationController(
        task=task,
        spec_paths=args.specs,
        launch=KubernetesRuntime(),
        harvest=GcsHarvest(bucket=bucket),
        stopping=StoppingCriteria(
            max_generations=len(args.specs),
            max_total_usd=args.max_total_usd),
        gate=CompletenessGate(),
        preflight=lambda: run_preflight().ok,
        ledger_path=args.ledger,
    )
    history = controller.run()

    print("\n" + "=" * 68)
    print(f"CAMPAIGN COMPLETE: {len(history)} generation(s)")
    for outcome in history:
        flag = " ABORTED" if outcome.aborted else ""
        print(f"  gen {outcome.generation}: best={outcome.best_score} "
              f"mean={outcome.mean_score} ${outcome.spent_usd:.2f}{flag}")
    print("=" * 68)
    return 1 if any(o.aborted for o in history) else 0


def resolve_task(args) -> Task:
    """Builds the Task for this invocation.

    A declared task file wins. Otherwise we synthesise the V1 task from the
    flags, so the default path is a Task like any other rather than a second
    code path that bypasses verifiers, budgets and capabilities entirely.
    """
    if args.task_file:
        task = Task.load(args.task_file)
        if args.budget_usd is not None:
            print(f"NOTE: --budget-usd ignored; {args.task_file} declares the budget.")
        return task
    return legacy_task(objective=args.objective, budget_usd=args.budget_usd)


def run_preflight_mode(args) -> int:
    """Answers "can this environment run a tournament?" with real requests.

    Exits non-zero on any failure so a launch script can gate on it:

        python -m hae.cli --mode preflight --repair || exit 1
    """
    report = run_preflight(
        repair=args.repair,
        service_account=args.service_account,
        skip_gcs=args.skip_gcs,
    )
    print(report.render())
    return 0 if report.ok else 1


def run_breed(args) -> int:
    """Produces the next generation's population file from its spec."""
    if not args.generation_spec:
        print("--mode breed requires --generation-spec "
              "configs/generations/genNN.json")
        return 2
    path, population = breed_generation(args.generation_spec)
    print(f"Wrote {len(population)} firms to {path}")
    for genome in population:
        lineage = genome.mutation_history[-1] if genome.mutation_history else ""
        print(f"  {genome.company_id:28s} {genome.total_agent_count:3d} agents  {lineage}")
    return 0


def run_benchmark(args) -> int:
    """Prints a benchmark task's objective, or grades a workspace against it."""
    if not args.task:
        print("--mode benchmark requires --task. Available: "
              + ", ".join(sorted(TASKS)))
        return 2
    bench = SelfHostingBenchmark()
    reference = bench.reference_run(args.task)
    print(f"Reference run for {args.task!r}: "
          f"{reference['passed']}/{reference['collected']} held-out tests pass.")
    print("\n--- OBJECTIVE ---\n")
    print(bench.objective_for(args.task))
    return 0


def main():
    args = parse_args()

    if args.mode == "preflight":
        return run_preflight_mode(args)
    if args.mode == "campaign":
        return run_campaign(args)
    if args.mode == "breed":
        return run_breed(args)
    if args.mode == "benchmark":
        return run_benchmark(args)

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

    task = resolve_task(args)
    print(f"Task: {task.describe()}")
    if not task.is_verified:
        print("WARNING: this task has no ground-truth verifier. Its score will "
              "be judge-only, which saturates. Do not compare it to verified "
              "runs.")

    if args.mode == "single-firm":
        print(f"\nRunning Single Firm Execution on Objective:\n{task.objective[:120]}...\n")
        runner = HierarchicalCompanyRunner(seed_genome, budget=task.budget)
        result = runner.run(task.objective)
        
        # Step 1: execution gates -- run the code the firm actually wrote.
        report = ExecutionHarness().verify_workspace(
            runner.workspace, result["final_deliverable"])
        print(f"\n{report.summary()}")

        # Step 2: LLM Strategic Evaluation
        evaluator = StrategicFitnessEvaluator()
        eval_result = evaluator.evaluate(
            company_id=seed_genome.company_id,
            generation=seed_genome.generation,
            objective=task.objective,
            final_deliverable=result["final_deliverable"],
            departmental_briefs=result["departmental_briefs"],
            elapsed_seconds=result["elapsed_seconds"],
            token_usage=result["token_usage"],
            verification=report
        )

        # Gate results already contribute 30% of the gross via
        # `execution_integrity`; subtracting the legacy penalty on top would
        # double-count them.
        gross_score = eval_result.fitness.fitness_score
        net_score = gross_score

        print("\n" + "="*80)
        print(f"FIRM EXECUTION COMPLETE: {seed_genome.company_id}")
        print(f"Net Fitness: {net_score}/100 "
              f"(Execution Integrity: {getattr(eval_result.fitness, 'execution_integrity', 0.0)}/100, "
              f"gate penalty would have been -{report.score_penalty})")
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
            objective=task.objective,
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
    sys.exit(main() or 0)
