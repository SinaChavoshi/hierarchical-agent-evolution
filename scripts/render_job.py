"""Renders the single Kubernetes Job template for one generation.

Replaces 22 hand-maintained manifests. Every value comes from the generation
spec or an explicit flag, and an unsubstituted placeholder is a hard error --
V1's manifests drifted precisely because each was edited independently.

Usage:
    PYTHONPATH=. python3 scripts/render_job.py --generation 1 \
        --image-tag v2-gen1 --project gemle-gke-dev > /tmp/job.yaml
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hae.orchestration.breeder import GenerationSpec
from hae.evaluation.benchmark import SelfHostingBenchmark

TEMPLATE = "k8s/generation-job.yaml.template"
SPEC_DIR = "configs/generations"
PLACEHOLDER = re.compile(r"\$\{([A-Z0-9_]+)\}")


def build_values(args, spec: GenerationSpec) -> dict:
    # The objective still goes on the command line because the judge prompt
    # needs it, but when a task file is declared it is the task's objective --
    # there is one source, not two that can drift.
    if spec.task_file:
        from hae.task import Task
        task = Task.load(os.path.join(args.repo_root, spec.task_file))
        objective = task.objective
        task_file = spec.task_file
    elif spec.benchmark_task:
        objective = SelfHostingBenchmark().objective_for(spec.benchmark_task)
        task_file = ""
    else:
        objective = spec.objective
        task_file = ""
    return {
        "JOB_NAME": args.job_name or f"hae-v2-gen{spec.generation}-{args.region}",
        "NAMESPACE": args.namespace,
        "GENERATION": str(spec.generation),
        "POPULATION_SIZE": str(spec.population_size),
        "PARALLELISM": str(args.parallelism),
        "BACKOFF_LIMIT": str(args.backoff_limit),
        "SERVICE_ACCOUNT": args.service_account,
        "IMAGE": f"{args.image_repo}:{args.image_tag}",
        "REGION": args.region,
        "OBJECTIVE": objective.replace("\\", "\\\\").replace('"', '\\"'),
        "TASK_FILE": task_file,
        "PROJECT_ID": args.project,
        "GCS_BUCKET": args.bucket,
        "WORKER_MODEL": args.worker_model,
        "EXECUTIVE_MODEL": args.executive_model,
        "JUDGE_MODEL": args.judge_model,
        "CPU": args.cpu,
        "MEMORY": args.memory,
        "POPULATION_CONFIGMAP": (args.configmap
                                 or f"hae-gen{spec.generation}-population"),
    }


def render(template: str, values: dict) -> str:
    out = PLACEHOLDER.sub(lambda m: values.get(m.group(1), m.group(0)), template)
    missing = sorted(set(PLACEHOLDER.findall(out)))
    if missing:
        raise SystemExit(
            f"Refusing to emit a manifest with unsubstituted placeholders: "
            f"{missing}")
    return out


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--generation", type=int, required=True)
    p.add_argument("--image-tag", required=True,
                   help="Immutable tag. Never 'latest'.")
    p.add_argument("--image-repo",
                   default="us-central1-docker.pkg.dev/gemle-gke-dev/"
                           "chavoshi-repo/agent-evolution")
    p.add_argument("--project", default=os.getenv("GOOGLE_CLOUD_PROJECT", ""))
    p.add_argument("--bucket", default=os.getenv("GCS_BUCKET", ""))
    p.add_argument("--region", default="us-east4")
    p.add_argument("--namespace", default="agent-evolution")
    p.add_argument("--service-account", default="agent-evolution-sa")
    p.add_argument("--parallelism", type=int, default=3)
    p.add_argument("--backoff-limit", type=int, default=8)
    p.add_argument("--cpu", default="4")
    p.add_argument("--memory", default="8Gi")
    p.add_argument("--worker-model", default="gemini-2.5-flash")
    p.add_argument("--executive-model", default="gemini-2.5-pro")
    p.add_argument("--judge-model", default="gemini-2.5-pro")
    p.add_argument("--job-name", default=None)
    p.add_argument("--configmap", default=None)
    p.add_argument("--repo-root", dest="repo_root", default=".",
                   help="Root used to resolve a spec's task_file.")
    args = p.parse_args()

    if args.image_tag == "latest":
        raise SystemExit(
            "Refusing to pin a run to ':latest'. Use an immutable tag so the "
            "published results keep corresponding to a specific image.")
    if not args.project:
        raise SystemExit(
            "No project. Pass --project or set GOOGLE_CLOUD_PROJECT.")
    if not args.bucket:
        raise SystemExit("No results bucket. Pass --bucket or set GCS_BUCKET.")

    spec_path = os.path.join(SPEC_DIR, f"gen{args.generation}.json")
    spec = GenerationSpec.load(spec_path)
    with open(TEMPLATE, encoding="utf-8") as fh:
        template = fh.read()
    sys.stdout.write(render(template, build_values(args, spec)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
