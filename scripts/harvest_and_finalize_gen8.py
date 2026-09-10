"""
Harvests all Generation 8 scorecards from GCS, ranks the population,
extracts the champion and top 5 survivors, recalculates standardized fitness,
and generates full reports.
"""

import os
import glob
import json
import urllib.request
import urllib.parse
import subprocess

def harvest():
    os.makedirs("experiments/exp-010-parallel-gen8/scorecards", exist_ok=True)
    token = subprocess.check_output(['gcloud', 'auth', 'application-default', 'print-access-token']).decode().strip()

    # List items in GCS
    list_req = urllib.request.Request(
        'https://storage.googleapis.com/storage/v1/b/gemle-gke-dev-agent-evolution/o?prefix=parallel_runs/generation_8/',
        headers={'Authorization': f'Bearer {token}'}
    )
    with urllib.request.urlopen(list_req) as resp:
        items = json.loads(resp.read().decode()).get('items', [])

    print(f"Found {len(items)} items in GCS.")
    for item in items:
        name = item['name']
        fname = os.path.basename(name)
        if not fname.endswith(".json"):
            continue
        local_path = os.path.join("experiments/exp-010-parallel-gen8/scorecards", fname)
        media_url = f'https://storage.googleapis.com/download/storage/v1/b/gemle-gke-dev-agent-evolution/o/{urllib.parse.quote(name, safe="")}?alt=media'
        dl_req = urllib.request.Request(media_url, headers={'Authorization': f'Bearer {token}'})
        with urllib.request.urlopen(dl_req) as dl_resp:
            content = dl_resp.read()
        with open(local_path, "wb") as f:
            f.write(content)
        print(f"-> Synced {fname} ({len(content)} bytes)")

    # Load and rank all scorecards
    sc_files = sorted(glob.glob("experiments/exp-010-parallel-gen8/scorecards/*.json"))
    data = []
    for f in sc_files:
        with open(f) as fp:
            d = json.load(fp)
        data.append(d)

    data.sort(key=lambda x: x.get("overall_score", 0.0), reverse=True)

    print("\n" + "="*85)
    print("=== GENERATION 8 TOURNAMENT CURRENT / FINAL STANDINGS ===")
    print("="*85)
    print(f"{'Rank':<5} {'Company ID':<24} {'Net Fitness':<12} {'Gross':<8} {'Penalty':<9} {'B/S/T/Tel':<10} {'Files':<6} {'Tokens':<8}")
    print("-" * 85)
    for i, d in enumerate(data, 1):
        verif = d.get("verification", {})
        b = 'P' if verif.get('build_passed') else 'F'
        s = 'P' if verif.get('smoke_passed') else 'F'
        t = 'P' if verif.get('test_passed') else 'F'
        tel = 'P' if verif.get('telemetry_passed') else 'F'
        pen = verif.get('score_penalty', 0.0)
        tok = d.get('estimated_tokens', 0)
        details = verif.get('details', '')
        import re
        m = re.search(r"(\d+)\s+files", details)
        files_c = int(m.group(1)) if m else verif.get('files_found_count', 0)
        print(f"{i:<5} {d['company_id']:<24} {d.get('overall_score', 0.0):<12.2f} {d.get('gross_score', 0.0):<8.1f} -{pen:<8.2f} {b}/{s}/{t}/{tel:<10} {files_c:<6} {tok:<8}")

    if data:
        # Extract champion & top 5 survivors
        champ = data[0]
        champ_genome = champ.get("genome", {})
        champ_path = "experiments/exp-010-parallel-gen8/winning_champion_genome.json"
        with open(champ_path, "w") as f:
            json.dump(champ_genome, f, indent=2)
        print(f"\nSaved Generation 8 Champion Genome to {champ_path} ({champ['company_id']}, Net: {champ.get('overall_score')})")

        top_5_genomes = [d.get("genome", {}) for d in data[:5]]
        top_5_path = "experiments/exp-010-parallel-gen8/top_5_survivor_genomes.json"
        with open(top_5_path, "w") as f:
            json.dump(top_5_genomes, f, indent=2)
        print(f"Saved Generation 8 Top 5 Survivor Genomes to {top_5_path}")

    return data

if __name__ == "__main__":
    harvest()
