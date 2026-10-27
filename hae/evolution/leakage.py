"""Leakage gate for evolved harness patches.

V14 showed that task-specific fixes can hide inside "generic" harness code. Every
code mutation must pass this gate before it is evaluated. A patch is rejected if a
line it adds introduces (relative to the seed harness):

  * an instance id or repository name from the benchmark datasets, or
  * a rare identifier (>= 6 chars) or string literal (>= 12 chars) that appears in the
    gold patch or hidden test patch of any task in the configured task pool.

"Rare" means the token occurs in at most `max_doc_freq` tasks' patches; common
identifiers such as `isinstance` or `DeprecationWarning` are therefore allowed.
"""

from __future__ import annotations

import builtins
import json
import keyword
import re
from collections import Counter
from typing import Dict, Iterable, List, Optional, Set

_IDENT_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]{5,}\b")
_STRLIT_RE = re.compile(r"""(['"])([^'"\n]{12,200})\1""")
_ALWAYS_ALLOWED = set(dir(builtins)) | set(keyword.kwlist) | {
    "self", "return", "import", "print", "string", "number", "values", "default", "result",
    "results", "message", "messages", "params", "kwargs", "options", "warnings", "typing",
}


def _added_lines(patch: str) -> List[str]:
    return [ln[1:] for ln in patch.splitlines() if ln.startswith("+") and not ln.startswith("+++")]


def _tokens(text: str) -> Set[str]:
    toks = {m.group(0) for m in _IDENT_RE.finditer(text)}
    toks |= {m.group(2).strip() for m in _STRLIT_RE.finditer(text)}
    return {t for t in toks if t and t not in _ALWAYS_ALLOWED}


class LeakageGate:
    def __init__(self, forbidden: Set[str], forbidden_names: Set[str]) -> None:
        self.forbidden = forbidden
        self.forbidden_names = forbidden_names

    @classmethod
    def build(cls, dataset_paths: Iterable[str], task_ids: Iterable[str], baseline_source: str,
              max_doc_freq: int = 3) -> "LeakageGate":
        wanted = set(task_ids)
        names: Set[str] = set()
        doc_freq: Counter = Counter()
        per_task_tokens: Dict[str, Set[str]] = {}
        for path in dataset_paths:
            with open(path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    row = json.loads(line)
                    iid = row.get("instance_id", "")
                    repo = row.get("repo", "")
                    names.add(iid)
                    if repo:
                        names.add(repo)
                        names.add(repo.split("/")[-1])
                    toks = _tokens("\n".join(_added_lines(row.get("patch", "") or "")
                                             + _added_lines(row.get("test_patch", "") or "")))
                    doc_freq.update(toks)
                    if iid in wanted:
                        per_task_tokens[iid] = toks
        base_toks = _tokens(baseline_source) | set(_IDENT_RE.findall(baseline_source))
        forbidden: Set[str] = set()
        for toks in per_task_tokens.values():
            forbidden |= {t for t in toks if doc_freq[t] <= max_doc_freq}
        forbidden -= base_toks
        # Instance ids and repo names: forbidden only if they are new relative to the seed harness.
        forbidden_names = {n for n in names if n and n not in baseline_source and len(n) >= 4}
        return cls(forbidden, forbidden_names)

    def violations(self, patch: str) -> List[str]:
        added = "\n".join(_added_lines(patch))
        hits = sorted(_tokens(added) & self.forbidden)
        lowered = added.lower()
        hits += sorted(n for n in self.forbidden_names if n.lower() in lowered)
        return hits[:20]

    def check(self, patch: str) -> Optional[str]:
        v = self.violations(patch)
        return None if not v else f"leakage gate: patch introduces task-specific tokens {v}"
