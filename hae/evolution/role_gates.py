"""V16: gates every LLM-written role text must pass (role refresh and role mutations).

A role (name, goal, backstory, playbook steps) is rejected if it contains task-specific content
(the V15 leakage gate over train + val + test gold/test patches, instance ids, repository names,
file paths) or violates the schema and length limits.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Sequence

from hae.evolution.genome import EVOLVABLE_FILES
from hae.evolution.leakage import LeakageGate
from hae.genome.role_mutation import MAX_BACKSTORY_CHARS, MAX_GOAL_CHARS, MAX_NAME_CHARS
from hae.genome.schema import MAX_PLAYBOOK_STEPS, GenomeValidationError, RoleAllele

LLM_LAYERS = ("architect", "executor", "refiner", "falsifier")


_PATH_LIKE = re.compile(r"\b[\w.-]+/[\w./-]+\.(?:py|cfg|toml|ini|txt|rst)\b|\b[\w-]+\.py\b")


class RoleGates:
    def __init__(self, splits: Dict[str, Any], repo_root: str) -> None:
        baseline = ""
        for rel in EVOLVABLE_FILES:
            with open(os.path.join(repo_root, rel), encoding="utf-8") as fh:
                baseline += fh.read() + "\n"
        ds = sorted({os.path.join(repo_root, p) for p in splits["datasets"].values()})
        pool = splits["train"] + splits["val"] + splits["test"]
        self.leakage = LeakageGate.build(ds, pool, baseline)
        self.banned: List[str] = []
        for p in ds:
            with open(p, encoding="utf-8") as fh:
                for line in fh:
                    if line.strip():
                        row = json.loads(line)
                        self.banned += [row["instance_id"].lower(), row.get("repo", "").lower(),
                                        row.get("repo", "").split("/")[-1].lower()]
        self.banned = sorted({b for b in self.banned if len(b) >= 4})

    def check(self, role: Dict[str, Any]) -> List[str]:
        errs: List[str] = []
        texts = [str(role.get("name", "")), str(role.get("goal", "")), str(role.get("backstory", ""))]
        texts += [str(s) for s in role.get("playbook") or []]
        blob = "\n".join(texts)
        leak = self.leakage.violations("\n".join("+" + t for t in blob.splitlines()))
        if leak:
            errs.append(f"leakage: {leak[:5]}")
        low = blob.lower()
        hit = [b for b in self.banned if re.search(r"(?<![a-z0-9])" + re.escape(b) + r"(?![a-z0-9])", low)]
        if hit:
            errs.append(f"banned names: {hit[:5]}")
        if _PATH_LIKE.search(blob.replace("reproduce_issue.py", "").replace("falsification_suite.py", "")):
            errs.append(f"file path: {_PATH_LIKE.search(blob).group(0)}")
        if role.get("layer") not in LLM_LAYERS:
            errs.append(f"bad layer {role.get('layer')!r}")
        if not (3 <= len(role.get("playbook") or []) <= MAX_PLAYBOOK_STEPS):
            errs.append("playbook must have 3-8 steps")
        if len(str(role.get("name", ""))) > MAX_NAME_CHARS or len(str(role.get("goal", ""))) > MAX_GOAL_CHARS \
                or len(str(role.get("backstory", ""))) > MAX_BACKSTORY_CHARS:
            errs.append("length limit")
        if not errs:
            try:
                to_allele(role, generation=0)
            except (GenomeValidationError, TypeError, ValueError) as exc:
                errs.append(f"schema: {exc}")
        return errs


def to_allele(role: Dict[str, Any], generation: int) -> RoleAllele:
    parent = str(role.get("parent_role_id") or "").strip()
    temp = min(0.6, max(0.0, float(role.get("temperature", 0.2) or 0.2)))
    tags = [re.sub(r"[^a-z0-9_]", "", str(t).lower()) for t in role.get("domain_tags") or []]
    return RoleAllele(
        name=str(role["name"]).strip(), goal=str(role.get("goal", "")).strip(),
        backstory=str(role.get("backstory", "")).strip(), domain_tags=[t for t in tags if t][:12],
        kind="both", temperature=temp, tools_enabled=role.get("layer") != "architect",
        layer=str(role["layer"]), playbook=[str(s).strip() for s in role.get("playbook") or []],
        failure_modes=[re.sub(r"[^a-z0-9_]", "_", str(m).lower()).strip("_") for m in role.get("failure_modes") or []][:4],
        is_generalist=False, origin=f"v16-refresh:{parent}" if parent else "v16-refresh:new",
        created_generation=generation,
    )
