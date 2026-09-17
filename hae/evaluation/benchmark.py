"""The self-hosting benchmark: firms reimplement this platform's own modules.

Why this exists
---------------
V1 scored firms on an open-ended prose objective ("design a production-ready
universal agent-org platform..."). That objective had no ground truth, so the
only available grader was an LLM judge, and the judge saturated: by Generation
10 two of its five dimensions had a mean of 99.0 with sigma 1.67. Selection
pressure went where the measurement was, which was prose. Over six generations
published fitness rose at +0.50/generation while the probability that a firm's
code actually ran moved by +0.19 percentage points per generation --
statistically indistinguishable from nothing.

The task here has ground truth, and the ground truth is this repository.

    Given the modules `hae/<module>.py` depends on, and its docstring as the
    specification, implement `hae/<module>.py` so that `tests/test_<module>.py`
    -- which the firm never sees -- collects and passes.

Fitness is the fraction of held-out tests that pass. That number is:

  * ungameable by writing well, because no prose is read;
  * monotonic, because 7/10 is unambiguously better than 5/10;
  * comparable across generations, because the tests do not change;
  * and self-referential in the way the programme's stated goal requires --
    a firm that beats our implementation has produced a mergeable patch.

Anti-gaming
-----------
Three ways a firm could cheat, and what stops each:

  1. *Write the oracle.* A submission that touches anything under `tests/`
     scores zero. The held-out test file is copied in from the pristine
     repository after the submission is materialised, so it cannot be
     overwritten either.
  2. *Import the reference implementation.* The target module is deleted from
     the materialised tree, the suite runs with `PYTHONPATH` pointing only at
     that tree, and a submission mentioning an absolute path outside it is
     rejected before execution.
  3. *Delete the failing tests.* Test selection is by file, chosen by us, and
     the count of collected tests is compared against the reference run. A
     submission that collects fewer tests than the reference scores on the
     reference denominator, so dropping tests cannot raise the fraction.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DEFAULT_TIMEOUT_S = 30


class BenchmarkError(RuntimeError):
    """Raised when a task is malformed or the reference run does not pass."""


@dataclass(frozen=True)
class BenchmarkTask:
    """One held-out reimplementation problem."""

    task_id: str
    # Module the firm must write, relative to the repository root.
    target_module: str
    # Test file that grades it. Never shown to the firm.
    held_out_tests: str
    # Modules the firm may read, because the target legitimately depends on
    # them. Everything else in the repository is withheld.
    visible_context: tuple = ()
    summary: str = ""

    def objective(self, spec: str, context: Dict[str, str]) -> str:
        """The prompt handed to a firm. Contains no hint of the test file."""
        blocks = "\n\n".join(
            f"### File: {path}\n```python\n{body}\n```"
            for path, body in sorted(context.items())
        )
        return (
            f"Implement the module `{self.target_module}`.\n\n"
            f"SPECIFICATION\n{spec}\n\n"
            f"{self.summary}\n\n"
            "You are given the modules it may import, verbatim, below. You may "
            "import from them and from the Python standard library. You may not "
            "add third-party dependencies.\n\n"
            f"{blocks}\n\n"
            "GRADING. A test suite you will not see imports this module and "
            "exercises it. Your score is the fraction of those tests that pass. "
            "Nothing you write outside "
            f"`{self.target_module}` is read, and no prose is scored: write the "
            "module, make it correct, and make it importable on its own.\n\n"
            f"Write your implementation to `{self.target_module}` using the "
            "write_file tool."
        )


@dataclass
class BenchmarkResult:
    """Outcome of grading one submission against one task."""

    task_id: str
    score: float                  # 0-100, the fitness contribution
    tests_passed: int
    tests_total: int
    collected: int
    reference_total: int
    rejected: bool = False
    rejection_reason: str = ""
    failures: List[str] = field(default_factory=list)
    stderr_tail: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "score": self.score,
            "tests_passed": self.tests_passed,
            "tests_total": self.tests_total,
            "collected": self.collected,
            "reference_total": self.reference_total,
            "rejected": self.rejected,
            "rejection_reason": self.rejection_reason,
            "failures": self.failures[:20],
        }


# Tasks are declared, not discovered, so that adding one is a deliberate act
# with a reviewed `visible_context`. An over-generous context leaks the answer.
TASKS: Dict[str, BenchmarkTask] = {
    t.task_id: t for t in (
        BenchmarkTask(
            task_id="artifacts",
            target_module="hae/evaluation/artifacts.py",
            held_out_tests="tests/test_artifacts.py",
            visible_context=(),
            summary=(
                "This module decides which files in an agent's workspace count "
                "as authored artifacts. Getting it wrong inflated V1's "
                "published file counts by 42-59%, because build byproducts and "
                "markdown-mangled paths were counted as agent output."
            ),
        ),
        BenchmarkTask(
            task_id="verification_loop",
            target_module="hae/evaluation/verification_loop.py",
            held_out_tests="tests/test_verification_loop.py",
            visible_context=("hae/evaluation/artifacts.py",
                             "hae/evaluation/harness.py"),
            summary=(
                "This module lets an agent ask the grading harness for a "
                "verdict mid-run, under a fixed budget. It must report "
                "verdicts without coaching: telling the agent how to fix a "
                "gate would make the score measure the harness, not the agent."
            ),
        ),
        BenchmarkTask(
            task_id="morphogenesis",
            target_module="hae/genome/morphogenesis.py",
            held_out_tests="tests/test_morphogenesis.py",
            visible_context=("hae/genome/schema.py", "hae/infra/llm.py"),
            summary=(
                "This module restructures an organisation's topology between "
                "generations: adding, removing and re-scoping department pods."
            ),
        ),
        BenchmarkTask(
            task_id="harness",
            target_module="hae/evaluation/harness.py",
            held_out_tests="tests/test_execution_harness.py",
            visible_context=("hae/evaluation/artifacts.py",),
            summary=(
                "This module is the grader itself: five gates that verify a "
                "Python package by parsing, installing, importing and running "
                "it. A gate it cannot evaluate must report SKIPPED, never "
                "PASSED -- V1's verifier reported passes for code that did not "
                "parse, and six generations were selected on that signal."
            ),
        ),
    )
}


def _first_exception(report: str) -> str:
    """The most informative line of a traceback, for the failure list."""
    for line in reversed(report.splitlines()):
        line = line.strip()
        if line and ("Error" in line.split(":")[0] or line.startswith("Exception")):
            return line[:200]
    return "unknown import failure"


def _extract_failures(report: str) -> List[str]:
    """Extracts each FAIL/ERROR test header with its final exception line.

    Appends the final AssertionError/Exception line (e.g. `AssertionError:
    False is not true : notes.`) without leaking any test source lines, giving
    firms actionable ground-truth feedback for iterative self-repair.
    """
    results: List[str] = []
    for block in report.split("=" * 70):
        lines = [l.strip() for l in block.splitlines() if l.strip()]
        if not lines:
            continue
        header = lines[0]
        if not header.startswith(("FAIL:", "ERROR:")):
            continue
        exc_line = ""
        for line in reversed(lines[1:]):
            if (line.startswith("-" * 20) or line.startswith("Ran ")
                    or line in ("OK", "FAILED") or line.startswith("FAILED (")):
                continue
            exc_line = line[:250]
            break
        if exc_line and not exc_line.startswith(("File ", "Traceback")):
            results.append(f"{header} -> {exc_line}")
        else:
            results.append(header)
    if not results:
        results = [l.strip() for l in report.splitlines()
                   if l.startswith(("FAIL:", "ERROR:"))]
    return results


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def module_docstring(path: str) -> str:
    """The target module's docstring, used as the specification."""
    import ast
    tree = ast.parse(_read(path), filename=path)
    return ast.get_docstring(tree) or ""


def module_specification(path: str) -> str:
    """Full specification for reimplementing `path`: docstring + public API contract.

    Extracts the module docstring plus a stub of all public top-level constants,
    functions, and classes (with signatures, docstrings, and bodies replaced by
    `...`) so firms know the exact names and signatures imported by held-out
    tests without leaking implementation details.
    """
    import ast

    tree = ast.parse(_read(path), filename=path)
    doc = ast.get_docstring(tree) or ""
    stubs: List[ast.stmt] = []

    def _stub_body(node: ast.AST) -> List[ast.stmt]:
        d = ast.get_docstring(node, clean=False)
        body: List[ast.stmt] = []
        if d is not None:
            body.append(ast.Expr(value=ast.Constant(value=d)))
        body.append(ast.Expr(value=ast.Constant(value=Ellipsis)))
        return body

    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = (node.targets if isinstance(node, ast.Assign)
                       else [node.target])
            if any(isinstance(t, ast.Name) and not t.id.startswith("_")
                   for t in targets):
                stubs.append(node)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith("_"):
                stub_fn = type(node)(
                    name=node.name,
                    args=node.args,
                    body=_stub_body(node),
                    decorator_list=node.decorator_list,
                    returns=node.returns,
                )
                ast.fix_missing_locations(stub_fn)
                stubs.append(stub_fn)
        elif isinstance(node, ast.ClassDef):
            if not node.name.startswith("_"):
                class_body: List[ast.stmt] = []
                cdoc = ast.get_docstring(node, clean=False)
                if cdoc is not None:
                    class_body.append(ast.Expr(value=ast.Constant(value=cdoc)))
                for item in node.body:
                    if isinstance(item, (ast.Assign, ast.AnnAssign)):
                        class_body.append(item)
                    elif isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        if (not item.name.startswith("_")
                                or item.name == "__init__"):
                            stub_method = type(item)(
                                name=item.name,
                                args=item.args,
                                body=_stub_body(item),
                                decorator_list=item.decorator_list,
                                returns=item.returns,
                            )
                            ast.fix_missing_locations(stub_method)
                            class_body.append(stub_method)
                if not class_body:
                    class_body.append(ast.Expr(value=ast.Constant(value=Ellipsis)))
                stub_cls = ast.ClassDef(
                    name=node.name,
                    bases=node.bases,
                    keywords=node.keywords,
                    body=class_body,
                    decorator_list=node.decorator_list,
                )
                ast.fix_missing_locations(stub_cls)
                stubs.append(stub_cls)

    stub_module = ast.Module(body=stubs, type_ignores=[])
    contract = ast.unparse(stub_module) if stubs else ""
    if contract:
        return (
            f"{doc}\n\n"
            "PUBLIC API CONTRACT (your implementation must define these exact "
            "constants, functions, and classes with matching signatures):\n"
            f"```python\n{contract}\n```"
        )
    return doc


class SelfHostingBenchmark:
    """Grades submissions by running this repository's own held-out tests."""

    def __init__(self, repo_root: str = REPO_ROOT,
                 timeout_s: int = DEFAULT_TIMEOUT_S,
                 python_executable: Optional[str] = None):
        self.repo_root = repo_root
        self.timeout_s = timeout_s
        self.python = python_executable or sys.executable

    # ------------------------------------------------------------------ #
    # Task construction
    # ------------------------------------------------------------------ #

    def task(self, task_id: str) -> BenchmarkTask:
        if task_id not in TASKS:
            raise BenchmarkError(
                f"Unknown task {task_id!r}. Known: {sorted(TASKS)}")
        task = TASKS[task_id]
        for rel in (task.target_module, task.held_out_tests):
            if not os.path.exists(os.path.join(self.repo_root, rel)):
                raise BenchmarkError(
                    f"Task {task_id!r} references {rel}, which does not exist. "
                    "A benchmark whose own fixtures have drifted grades nothing."
                )
        return task

    def objective_for(self, task_id: str) -> str:
        """The full prompt for a task, with its specification and context."""
        task = self.task(task_id)
        spec = module_specification(os.path.join(self.repo_root, task.target_module))
        context = {rel: _read(os.path.join(self.repo_root, rel))
                   for rel in task.visible_context}
        objective = task.objective(spec, context)
        # A leaked oracle is the one failure mode that invalidates the score
        # silently, so assert rather than trust the task declaration.
        if task.held_out_tests in objective:
            raise BenchmarkError(
                f"Task {task_id!r} leaks its held-out test path into the prompt")
        return objective

    # ------------------------------------------------------------------ #
    # Grading
    # ------------------------------------------------------------------ #

    def _reject(self, task: BenchmarkTask, submission: Dict[str, str]) -> str:
        """Returns a rejection reason, or "" if the submission may be graded."""
        for path in submission:
            normalised = path.replace("\\", "/").lstrip("./")
            if normalised.startswith("tests/") or "/tests/" in normalised:
                return (f"submission writes {path!r}. The grading suite is not "
                        "the firm's to author.")
            if os.path.isabs(path):
                return f"submission writes an absolute path {path!r}."
        body = submission.get(task.target_module, "")
        if self.repo_root in body:
            return ("submission references the repository root path, which "
                    "would let it import the reference implementation.")
        if re.search(r"""sys\.path\s*\.\s*(insert|append)""", body):
            return ("submission mutates sys.path, which would let it import "
                    "the reference implementation.")
        if task.target_module not in submission:
            return f"submission does not contain {task.target_module}."
        return ""

    def _materialise(self, task: BenchmarkTask,
                     submission: Optional[Dict[str, str]]) -> str:
        """Builds a temp repo: this one, with the target module swapped out."""
        workdir = tempfile.mkdtemp(prefix="hae_benchmark_")
        for name in ("hae", "tests", "pyproject.toml"):
            src = os.path.join(self.repo_root, name)
            dst = os.path.join(workdir, name)
            if os.path.isdir(src):
                shutil.copytree(
                    src, dst,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            elif os.path.exists(src):
                shutil.copy2(src, dst)

        if submission is not None:
            # Remove the reference implementation before writing the candidate,
            # so no path through the tree reaches it.
            target = os.path.join(workdir, task.target_module)
            if os.path.exists(target):
                os.remove(target)
            for rel, body in submission.items():
                full = os.path.join(workdir, rel)
                os.makedirs(os.path.dirname(full), exist_ok=True)
                with open(full, "w", encoding="utf-8") as fh:
                    fh.write(body)

            # Restore the pristine held-out suite last, so a submission that
            # tried to weaken it has no effect even if rejection is bypassed.
            shutil.copy2(os.path.join(self.repo_root, task.held_out_tests),
                         os.path.join(workdir, task.held_out_tests))
        return workdir

    @staticmethod
    def _test_module(test_rel: str) -> str:
        return test_rel[: -len(".py")].replace("/", ".").replace("\\", ".")

    def _run_tests(self, workdir: str, test_rel: str) -> Dict[str, Any]:
        """Runs the held-out suite and returns counts.

        Uses `unittest` rather than `pytest` on purpose. The suite is written in
        `unittest`, so this adds no dependency, and the benchmark stays runnable
        on a workstation -- a grader that only works inside the production
        container is a grader nobody checks.

        Exit codes are not trusted; the counts are parsed from the report.
        """
        env = dict(os.environ)
        # Only the materialised tree is importable. This is what stops a
        # submission from reaching the reference implementation.
        env["PYTHONPATH"] = workdir
        env.pop("PYTEST_ADDOPTS", None)
        cmd = [self.python, "-m", "unittest", self._test_module(test_rel), "-v"]
        try:
            proc = subprocess.run(cmd, cwd=workdir, env=env,
                                  timeout=self.timeout_s,
                                  capture_output=True, text=True)
        except subprocess.TimeoutExpired:
            return {"passed": 0, "collected": 0, "failures": ["timed out"],
                    "stderr": f"exceeded {self.timeout_s}s"}

        # unittest writes its report to stderr.
        report = (proc.stderr or "") + (proc.stdout or "")

        ran_match = re.search(r"^Ran (\d+) tests? in", report, re.M)
        collected = int(ran_match.group(1)) if ran_match else 0

        failures = errors = 0
        detail = re.search(r"FAILED \(([^)]*)\)", report)
        if detail:
            for kind, count in re.findall(r"(failures|errors)=(\d+)", detail.group(1)):
                if kind == "failures":
                    failures = int(count)
                else:
                    errors = int(count)

        # When the suite cannot be imported, unittest substitutes a synthetic
        # `_FailedTest` and reports "Ran 1 test". Counting that as a collected
        # test would overstate how far a broken submission got.
        if "unittest.loader._FailedTest" in report:
            return {"passed": 0, "collected": 0,
                    "failures": ["suite failed to import: "
                                 + _first_exception(report)],
                    "stderr": report[-2000:]}

        passed = max(0, collected - failures - errors)
        named = _extract_failures(report)
        if not named and (collected == 0 or proc.returncode != 0):
            named = ["suite failed to import: " + _first_exception(report)]
        return {
            "passed": passed,
            "collected": collected,
            "failures": named,
            "stderr": report[-2000:],
            "returncode": proc.returncode,
        }

    def reference_run(self, task_id: str) -> Dict[str, Any]:
        """Grades our own implementation. Establishes the denominator.

        If this does not come back fully green the benchmark is broken, not the
        firm, and `evaluate` refuses rather than handing out a misleading score.
        """
        task = self.task(task_id)
        workdir = self._materialise(task, submission=None)
        try:
            return self._run_tests(workdir, task.held_out_tests)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    def evaluate(self, task_id: str, submission: Dict[str, str],
                 reference: Optional[Dict[str, Any]] = None) -> BenchmarkResult:
        """Scores one submission. The only number that matters is the fraction."""
        task = self.task(task_id)
        ref = reference if reference is not None else self.reference_run(task_id)
        reference_total = ref.get("collected", 0)
        if reference_total <= 0 or ref.get("passed", 0) != reference_total:
            raise BenchmarkError(
                f"Reference run for {task_id!r} is not green "
                f"({ref.get('passed')}/{reference_total}). The benchmark cannot "
                "grade a firm against a suite it fails itself."
            )

        reason = self._reject(task, submission)
        if reason:
            return BenchmarkResult(
                task_id=task_id, score=0.0, tests_passed=0,
                tests_total=reference_total, collected=0,
                reference_total=reference_total,
                rejected=True, rejection_reason=reason)

        workdir = self._materialise(task, submission)
        try:
            run = self._run_tests(workdir, task.held_out_tests)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

        # Always score against the reference denominator. A submission that
        # causes collection to fail scores on what it failed to satisfy, not on
        # the smaller set it managed to collect.
        passed = min(run["passed"], reference_total)
        score = round(100.0 * passed / reference_total, 2)
        return BenchmarkResult(
            task_id=task_id,
            score=score,
            tests_passed=passed,
            tests_total=reference_total,
            collected=run["collected"],
            reference_total=reference_total,
            failures=run["failures"],
            stderr_tail=run["stderr"][-600:],
        )
