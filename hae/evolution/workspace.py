"""Materialize a `HarnessGenome` as an isolated git worktree and run its safety gate."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import List, Optional, Tuple

from hae.evolution.genome import EVOLVABLE_FILES, HarnessGenome

# Unit-test modules every candidate must pass before it is allowed onto the cluster.
GATE_TEST_MODULES: List[str] = [
    "tests.test_swebench_hierarchical_mcts",
    "tests.test_swebench_agentic",
    "tests.test_swebench_runner",
]


def _run(cmd: List[str], cwd: str, timeout: float = 300.0, env: Optional[dict] = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                          errors="replace", timeout=timeout, env=env)


@dataclass
class GateResult:
    ok: bool
    stage: str
    message: str


class WorktreeManager:
    """Creates one detached git worktree per genome under `root_dir`."""

    def __init__(self, repo_root: str, base_ref: str, root_dir: str) -> None:
        self.repo_root = os.path.abspath(repo_root)
        self.base_ref = base_ref
        self.root_dir = os.path.abspath(root_dir)
        os.makedirs(self.root_dir, exist_ok=True)

    def path_for(self, genome_id: str) -> str:
        return os.path.join(self.root_dir, genome_id)

    def materialize(self, genome: HarnessGenome) -> Tuple[str, Optional[str]]:
        """Create (or reuse) the worktree for `genome`. Returns `(path, error)`."""
        path = self.path_for(genome.genome_id)
        if os.path.isdir(os.path.join(path, "hae")):
            return path, None
        if os.path.exists(path):
            shutil.rmtree(path, ignore_errors=True)
        _run(["git", "worktree", "prune"], cwd=self.repo_root, timeout=60)
        res = _run(["git", "worktree", "add", "--detach", path, self.base_ref], cwd=self.repo_root, timeout=180)
        if res.returncode != 0:
            return path, f"git worktree add failed: {res.stdout[-600:]}"
        if genome.patch.strip():
            ok, msg = apply_patch(path, genome.patch)
            if not ok:
                self.remove(genome.genome_id)
                return path, f"patch does not apply: {msg}"
        return path, None

    def remove(self, genome_id: str) -> None:
        path = self.path_for(genome_id)
        _run(["git", "worktree", "remove", "--force", path], cwd=self.repo_root, timeout=120)
        shutil.rmtree(path, ignore_errors=True)


def apply_patch(worktree: str, patch: str) -> Tuple[bool, str]:
    patch_file = os.path.join(worktree, ".hae_v15_candidate.diff")
    with open(patch_file, "w", encoding="utf-8") as fh:
        fh.write(patch if patch.endswith("\n") else patch + "\n")
    try:
        res = _run(["git", "apply", "--whitespace=nowarn", patch_file], cwd=worktree, timeout=60)
        if res.returncode != 0:
            res = _run(["git", "apply", "--3way", "--whitespace=nowarn", patch_file], cwd=worktree, timeout=60)
        return res.returncode == 0, res.stdout[-800:]
    finally:
        try:
            os.remove(patch_file)
        except OSError:
            pass


def current_patch(worktree: str) -> str:
    """Unified diff of the evolvable files in `worktree` relative to its base commit."""
    res = _run(["git", "diff", "--no-color", "HEAD", "--", *EVOLVABLE_FILES], cwd=worktree, timeout=60)
    return res.stdout if res.returncode == 0 else ""


def touched_files(patch: str) -> List[str]:
    files: List[str] = []
    for ln in patch.splitlines():
        if ln.startswith("+++ b/"):
            files.append(ln[6:].strip())
    return files


def run_gate(worktree: str, timeout: float = 600.0) -> GateResult:
    """Safety gate: the patch touches only evolvable files, compiles, and passes the gate unit tests."""
    patch = current_patch(worktree)
    stray = _run(["git", "status", "--porcelain"], cwd=worktree, timeout=60).stdout.splitlines()
    stray_files = [ln[3:].strip() for ln in stray if ln[3:].strip() and ln[3:].strip() not in EVOLVABLE_FILES]
    if stray_files:
        return GateResult(False, "scope", f"edits outside evolvable files: {stray_files[:5]}")
    for rel in touched_files(patch):
        res = _run([sys.executable, "-m", "py_compile", rel], cwd=worktree, timeout=60)
        if res.returncode != 0:
            return GateResult(False, "compile", res.stdout[-1200:])
    env = dict(os.environ)
    env["PYTHONPATH"] = worktree
    res = _run([sys.executable, "-m", "unittest", "-q", *GATE_TEST_MODULES], cwd=worktree, timeout=timeout, env=env)
    if res.returncode != 0:
        return GateResult(False, "unit_tests", res.stdout[-2000:])
    return GateResult(True, "ok", "compile + gate unit tests passed")
