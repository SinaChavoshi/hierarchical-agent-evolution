#!/usr/bin/env python3
"""Automated Background Orchestrator for V3 Campaign: Level 3 Closed-Loop RSI (Generations 7-9).

Executes Generations 7, 8, and 9 sequentially on GKE cluster `chavoshi-evolution-east4`:
  - Generation 7: 10 firms (seeded from Gen 6 champions, carrying their 100%-verified
    `hae/evaluation/artifacts.py` overlays) compete to evolve `hae/genome/morphogenesis.py`
    against the held-out `tests/test_morphogenesis.py` benchmark (max_iterations=10).
  - Generation 8: `Breeder` dynamically loads and executes Generation 7's winning firms'
    evolved `MorphogenesisEngine` & `StructuralCrossoverEngine` overlays to breed the
    Generation 8 population, while preserving concurrent per-firm code overlays.
  - Generation 9: `Breeder` dynamically executes Generation 8's evolved breeding engines
    to breed Generation 9, completing two full closed-loop code self-hosting cycles.
"""

import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from typing import Dict, List

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from hae.infra.llm import get_adc_access_token
from hae.infra.preflight import repair_iam
from hae.orchestration.breeder import breed_generation

PROJECT_ID = "gemle-gke-dev"
BUCKET_NAME = "gemle-gke-dev-agent-evolution"
SERVICE_ACCOUNT = "agent-evolution-sa@gemle-gke-dev.iam.gserviceaccount.com"
IMAGE_TAG = "v3-level3-rsi"
NAMESPACE = "agent-evolution"


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    status_path = os.path.join(REPO_ROOT, "experiments", "v3_rsi", "campaign.log")
    os.makedirs(os.path.dirname(status_path), exist_ok=True)
    with open(status_path, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def run_cmd(cmd: List[str], check: bool = True) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    token = get_adc_access_token()
    if token:
        env["CLOUDSDK_AUTH_ACCESS_TOKEN"] = token
    return subprocess.run(cmd, cwd=REPO_ROOT, env=env, capture_output=True, text=True, check=check)


def ensure_iam() -> None:
    try:
        res = repair_iam(PROJECT_ID, SERVICE_ACCOUNT)
        log(f"[IAM Watchdog] {res.status}: {res.detail}")
    except Exception as exc:
        log(f"[IAM Watchdog] Warning: {exc}")


def list_gcs_scorecards(generation: int) -> List[str]:
    """Lists scorecard object names in GCS for `generation` via REST API + ADC token."""
    token = get_adc_access_token()
    prefix = f"parallel_runs/generation_{generation}/"
    url = f"https://storage.googleapis.com/storage/v1/b/{BUCKET_NAME}/o?prefix={prefix}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    items = data.get("items", [])
    return [item["name"] for item in items if item["name"].endswith("_result.json")]


def download_gcs_object(obj_name: str, dest_path: str) -> None:
    token = get_adc_access_token()
    encoded = urllib.parse.quote(obj_name, safe="")
    url = f"https://storage.googleapis.com/storage/v1/b/{BUCKET_NAME}/o/{encoded}?alt=media"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    with urllib.request.urlopen(req, timeout=60) as resp:
        content = resp.read()
    with open(dest_path, "wb") as fh:
        fh.write(content)


def harvest_generation(generation: int) -> Dict[str, dict]:
    out_dir = os.path.join(REPO_ROOT, "experiments", "v3_rsi", f"generation_{generation}_results")
    os.makedirs(out_dir, exist_ok=True)
    objects = list_gcs_scorecards(generation)
    results: Dict[str, dict] = {}
    for obj in sorted(objects):
        fname = os.path.basename(obj)
        dest = os.path.join(out_dir, fname)
        download_gcs_object(obj, dest)
        with open(dest, "r", encoding="utf-8") as fh:
            card = json.load(fh)
        cid = card.get("company_id", fname.replace("_result.json", ""))
        results[cid] = card
    log(f"Harvested {len(results)} scorecards to {out_dir}")
    return results


def update_ledger_and_status(all_gen_results: Dict[int, Dict[str, dict]]) -> None:
    ledger_path = os.path.join(REPO_ROOT, "experiments", "v3_rsi", "ledger_level3_rsi.json")
    os.makedirs(os.path.dirname(ledger_path), exist_ok=True)
    summary = {}
    for gen, cards in sorted(all_gen_results.items()):
        if not cards:
            continue
        net_scores = [float(c.get("fitness_score", 0.0)) for c in cards.values()]
        exec_scores = [float(c.get("execution_integrity", 0.0)) for c in cards.values()]
        iters = [int(c.get("iterations_used", 1)) for c in cards.values()]
        spends = [float((c.get("opex") or {}).get("estimated_cost_usd", 0.0)) for c in cards.values()]
        passed_100 = sum(1 for e in exec_scores if e >= 100.0)
        best_cid = max(cards.keys(), key=lambda k: float(cards[k].get("fitness_score", 0.0)))
        summary[f"generation_{gen}"] = {
            "generation": gen,
            "firms_completed": len(cards),
            "best_firm": best_cid,
            "best_net_fitness": round(max(net_scores), 2),
            "mean_net_fitness": round(sum(net_scores) / len(net_scores), 2),
            "perfect_exec_count": f"{passed_100}/{len(cards)}",
            "mean_iterations": round(sum(iters) / len(iters), 2),
            "total_spend_usd": round(sum(spends), 4),
        }
    with open(ledger_path, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)

    md_path = os.path.join(REPO_ROOT, "experiments", "v3_rsi", "CAMPAIGN_STATUS.md")
    lines = [
        "# V3 Campaign Status — Level 3 Closed-Loop Recursive Self-Improvement (`Generations 7–9`)",
        "",
        "| Generation | Target Module | Breeding Engine | Firms | Best Net Fitness | Best Firm | Mean Net Fitness | 100% Exec (`7/7`) | Mean Iters | Spend (`$`) |",
        "| :---: | :--- | :--- | :---: | :---: | :--- | :---: | :---: | :---: | :---: |",
    ]
    for gen, s in sorted(summary.items()):
        gnum = s["generation"]
        engine_label = "Kernel Base (`Gen 6` Champions)" if gnum == 7 else f"Evolved Overlays from `Gen {gnum-1}`"
        lines.append(
            f"| **Gen {gnum}** | `hae/genome/morphogenesis.py` | {engine_label} | `{s['firms_completed']}/10` | "
            f"**`{s['best_net_fitness']}`** | `{s['best_firm']}` | **`{s['mean_net_fitness']}`** | "
            f"`{s['perfect_exec_count']}` | `{s['mean_iterations']}` | `${s['total_spend_usd']}` |"
        )
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def git_commit_and_push(msg: str) -> None:
    try:
        run_cmd(["git", "add", "configs/", "build/", "experiments/v3_rsi/", "hae/", "tests/"], check=False)
        run_cmd(["git", "commit", "-m", msg], check=False)
        run_cmd(["git", "push", "origin", "main"], check=False)
        log(f"Committed and pushed to GitHub: {msg}")
    except Exception as exc:
        log(f"Git push warning: {exc}")


def run_generation(generation: int, all_gen_results: Dict[int, Dict[str, dict]]) -> None:
    log(f"==================================================================")
    log(f"STARTING GENERATION {generation} (LEVEL 3 CLOSED-LOOP RSI)")
    log(f"==================================================================")

    ensure_iam()

    spec_path = f"configs/generations/gen{generation}.json"
    pop_path, pop = breed_generation(spec_path, repo_root=REPO_ROOT)
    log(f"Bred Generation {generation} population ({len(pop)} firms) -> {pop_path}")
    for g in pop:
        log(f"  * {g.company_id}: active overlays = {sorted(g.code_overlays.keys())}")

    job_yaml_path = os.path.join(REPO_ROOT, "build", f"gen{generation}-job.yaml")
    render_res = run_cmd([
        sys.executable, "scripts/render_job.py",
        "--generation", str(generation),
        "--job-name", f"parallel-firms-gen{generation}",
        "--image-tag", IMAGE_TAG,
        "--project", PROJECT_ID,
        "--bucket", BUCKET_NAME,
    ])
    with open(job_yaml_path, "w", encoding="utf-8") as fh:
        fh.write(render_res.stdout)
    log(f"Rendered Kubernetes Job manifest -> {job_yaml_path}")

    cm_name = f"hae-gen{generation}-population"
    run_cmd([
        "kubectl", "create", "configmap", cm_name,
        "-n", NAMESPACE,
        f"--from-file=generation_{generation}_population.json={pop_path}",
        "--dry-run=client", "-o", "yaml",
    ])
    # Apply configmap via shell pipeline
    subprocess.run(
        f"kubectl create configmap {cm_name} -n {NAMESPACE} "
        f"--from-file=generation_{generation}_population.json={pop_path} "
        f"--dry-run=client -o yaml | kubectl apply -f -",
        shell=True, cwd=REPO_ROOT, check=True,
    )
    log(f"Applied ConfigMap {cm_name} in namespace {NAMESPACE}")

    job_name = f"parallel-firms-gen{generation}"
    subprocess.run(f"kubectl delete job {job_name} -n {NAMESPACE} --ignore-not-found", shell=True, cwd=REPO_ROOT)
    subprocess.run(f"kubectl apply -f {job_yaml_path}", shell=True, cwd=REPO_ROOT, check=True)
    log(f"Launched Kubernetes Indexed Job {job_name} (10 parallel firms)")

    start_ts = time.time()
    last_iam_check = time.time()
    while True:
        time.sleep(30)
        if time.time() - last_iam_check >= 300:
            ensure_iam()
            last_iam_check = time.time()

        try:
            scorecards = list_gcs_scorecards(generation)
        except Exception as exc:
            log(f"GCS poll warning: {exc}")
            scorecards = []

        elapsed_m = (time.time() - start_ts) / 60.0
        log(f"[Gen {generation} Poll] {len(scorecards)}/10 scorecards uploaded in GCS ({elapsed_m:.1f} min elapsed)")
        if len(scorecards) >= 10:
            log(f"All 10/10 scorecards completed for Generation {generation}!")
            break

        # Safety timeout: 90 minutes per generation
        if elapsed_m > 90.0:
            log(f"Timeout reached (90 min) for Generation {generation} with {len(scorecards)}/10 scorecards.")
            break

    cards = harvest_generation(generation)
    all_gen_results[generation] = cards
    update_ledger_and_status(all_gen_results)

    subprocess.run(f"kubectl delete job {job_name} -n {NAMESPACE} --ignore-not-found", shell=True, cwd=REPO_ROOT)
    log(f"Cleaned up Kubernetes Job {job_name}")

    git_commit_and_push(f"feat(v3-rsi): harvest Generation {generation} Level 3 RSI results and overlays")


def main() -> None:
    log("Launching V3 Level 3 Closed-Loop RSI Campaign (Generations 7, 8, 9)...")
    all_gen_results: Dict[int, Dict[str, dict]] = {}
    for gen in (7, 8, 9):
        existing_dir = os.path.join(REPO_ROOT, "experiments", "v3_rsi", f"generation_{gen}_results")
        if os.path.isdir(existing_dir) and len([f for f in os.listdir(existing_dir) if f.endswith("_result.json")]) >= 10:
            log(f"Generation {gen} already harvested on disk ({existing_dir}); loading existing scorecards.")
            cards = {}
            for fn in os.listdir(existing_dir):
                if fn.endswith("_result.json"):
                    with open(os.path.join(existing_dir, fn), "r") as fh:
                        c = json.load(fh)
                        cards[c.get("company_id", fn)] = c
            all_gen_results[gen] = cards
            update_ledger_and_status(all_gen_results)
            continue
        run_generation(gen, all_gen_results)

    log("==================================================================")
    log("V3 CAMPAIGN (GENERATIONS 7-9, LEVEL 3 CLOSED-LOOP RSI) COMPLETE!")
    log("==================================================================")


if __name__ == "__main__":
    main()
