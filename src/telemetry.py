"""Comprehensive Telemetry, Cost Accounting, and Research Lineage Tracker."""

import os
import json
import time
import datetime
from typing import Dict, Any, List, Optional

# Cost per 1k tokens (approximate public list pricing for research accounting)
COST_TABLE = {
    "gemini-2.5-flash": {"input_per_1k": 0.000075, "output_per_1k": 0.00030},
    "gemini-2.5-pro": {"input_per_1k": 0.00125, "output_per_1k": 0.00500},
}

class ResearchLedger:
    """Records full experimental telemetry for research publication and open-source reproducibility."""

    def __init__(self, run_id: str, output_dir: str, gcs_bucket: Optional[str] = None):
        self.run_id = run_id
        self.output_dir = output_dir
        self.gcs_bucket = gcs_bucket
        self.ledger_dir = os.path.join(output_dir, "research_ledger", run_id)
        os.makedirs(self.ledger_dir, exist_ok=True)

        self.ledger_file = os.path.join(self.ledger_dir, "experiment_manifest.json")
        self.lineage_file = os.path.join(self.ledger_dir, "lineage_tree.json")
        self.cost_file = os.path.join(self.ledger_dir, "token_cost_summary.json")
        self.events_log = os.path.join(self.ledger_dir, "execution_events.jsonl")

        self.manifest: Dict[str, Any] = {
            "run_id": self.run_id,
            "start_time": datetime.datetime.utcnow().isoformat() + "Z",
            "end_time": None,
            "generations": [],
            "total_tokens_flash": 0,
            "total_tokens_pro": 0,
            "estimated_total_cost_usd": 0.0,
            "total_runtime_seconds": 0.0,
            "champion_lineage": []
        }

    def log_event(self, event_type: str, data: Dict[str, Any]):
        """Logs an atomic event with timestamp to the JSONL trace."""
        entry = {
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            "event_type": event_type,
            "data": data
        }
        with open(self.events_log, "a") as f:
            f.write(json.dumps(entry) + "\n")

    def record_generation(
        self,
        generation_idx: int,
        leaderboard: List[Dict[str, Any]],
        firm_results: List[Dict[str, Any]],
        breeding_metadata: Optional[Dict[str, Any]] = None
    ):
        """Records a full generation's leaderboard, metrics, and breeding actions."""
        gen_entry = {
            "generation": generation_idx,
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            "firm_count": len(firm_results),
            "leaderboard": leaderboard,
            "breeding_metadata": breeding_metadata or {}
        }
        self.manifest["generations"].append(gen_entry)

        # Update running cost / token accounting
        for f in firm_results:
            tokens = f.get("estimated_tokens", 0)
            # Roughly 70% Flash (specialists), 30% Pro (Managers/CEO/Judge)
            flash_tokens = int(tokens * 0.70)
            pro_tokens = int(tokens * 0.30)
            self.manifest["total_tokens_flash"] += flash_tokens
            self.manifest["total_tokens_pro"] += pro_tokens

        # Calculate estimated cost
        c_flash = (self.manifest["total_tokens_flash"] / 1000.0) * 0.0002
        c_pro = (self.manifest["total_tokens_pro"] / 1000.0) * 0.003
        self.manifest["estimated_total_cost_usd"] = round(c_flash + c_pro, 4)

        # Save manifest
        with open(self.ledger_file, "w") as f:
            json.dump(self.manifest, f, indent=2)

    def finalize(self, champion_summary: Dict[str, Any]):
        """Finalizes the experiment manifest and writes out paper-ready tables."""
        self.manifest["end_time"] = datetime.datetime.utcnow().isoformat() + "Z"
        self.manifest["champion"] = champion_summary

        with open(self.ledger_file, "w") as f:
            json.dump(self.manifest, f, indent=2)

        self._generate_markdown_report()
        self._sync_to_gcs()

    def _generate_markdown_report(self):
        """Generates a human-readable research report formatted for academic paper inclusion."""
        report_path = os.path.join(self.ledger_dir, "RESEARCH_REPORT.md")
        lines = [
            f"# Experiment Telemetry & Research Report: Run `{self.run_id}`",
            f"",
            f"- **Start Time**: {self.manifest['start_time']}",
            f"- **End Time**: {self.manifest['end_time']}",
            f"- **Estimated Cost**: ${self.manifest['estimated_total_cost_usd']} USD",
            f"- **Total Tokens Consumed**: ~{self.manifest['total_tokens_flash'] + self.manifest['total_tokens_pro']:,} tokens",
            f"  - Flash (Workers): ~{self.manifest['total_tokens_flash']:,}",
            f"  - Pro (Executives / Judges): ~{self.manifest['total_tokens_pro']:,}",
            f"",
            f"---",
            f"",
            f"## Generational Progression & Leaderboard",
            f""
        ]

        for g in self.manifest["generations"]:
            lines.append(f"### Generation {g['generation']}")
            lines.append("| Rank | Company ID | Score | Strategic | Technical | Coherence | Risk | Actionability | Elapsed (s) |")
            lines.append("| :---: | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |")
            for rank, entry in enumerate(g["leaderboard"], 1):
                lines.append(
                    f"| #{rank} | `{entry['company_id']}` | **{entry['overall_score']:.2f}** | "
                    f"{entry.get('strategic_depth', 0.0):.1f} | {entry.get('technical_feasibility', 0.0):.1f} | "
                    f"{entry.get('cross_functional_coherence', 0.0):.1f} | {entry.get('risk_mitigation', 0.0):.1f} | "
                    f"{entry.get('actionability', 0.0):.1f} | {entry.get('elapsed_seconds', 0.0):.1f}s |"
                )
            lines.append("")

        with open(report_path, "w") as f:
            f.write("\n".join(lines))

    def _sync_to_gcs(self):
        """Copies experiment files to persistent GCS bucket if available."""
        if not self.gcs_bucket:
            return
        try:
            import subprocess
            cmd = [
                "gcloud", "storage", "cp", "-r",
                self.ledger_dir,
                f"gs://{self.gcs_bucket}/research_ledger/"
            ]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        except Exception:
            pass
