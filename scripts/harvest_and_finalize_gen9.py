"""
Harvests all Generation 9 scorecards, ranks the population,
extracts the champion and top 5 survivors.
"""
import os
import glob
import json
import re

def finalize_gen9():
    sc_files = sorted(glob.glob("experiments/exp-011-parallel-gen9/scorecards/*.json"))
    data = []
    for f in sc_files:
        with open(f) as fp:
            data.append(json.load(fp))

    data.sort(key=lambda x: x.get("overall_score", 0.0), reverse=True)

    print(f"Loaded {len(data)} Generation 9 scorecards.")
    if data:
        champ = data[0]
        champ_genome = champ.get("genome", {})
        champ_path = "experiments/exp-011-parallel-gen9/winning_champion_genome.json"
        with open(champ_path, "w") as f:
            json.dump(champ_genome, f, indent=2)
        print(f"Saved Gen 9 Champion Genome to {champ_path} ({champ['company_id']}, Net: {champ.get('overall_score')})")

        top_5_genomes = [d.get("genome", {}) for d in data[:5]]
        top_5_path = "experiments/exp-011-parallel-gen9/top_5_survivor_genomes.json"
        with open(top_5_path, "w") as f:
            json.dump(top_5_genomes, f, indent=2)
        print(f"Saved Gen 9 Top 5 Survivor Genomes to {top_5_path}")

if __name__ == "__main__":
    finalize_gen9()
