"""Tests for the self-hosting benchmark.

These are tests of a grader, so they are mostly adversarial: the interesting
question is not whether a correct submission scores well but whether an
incorrect or dishonest one can score well anyway.
"""

import os
import shutil
import tempfile
import unittest

from hae.evaluation.benchmark import (
    BenchmarkError,
    BenchmarkResult,
    SelfHostingBenchmark,
    TASKS,
    _extract_failure_locations,
    _extract_failures,
    _frame_location,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TASK = "artifacts"
TARGET = "hae/evaluation/artifacts.py"


class TestTaskDeclarations(unittest.TestCase):

    def setUp(self):
        self.bench = SelfHostingBenchmark()

    def test_every_declared_task_resolves(self):
        for task_id in TASKS:
            task = self.bench.task(task_id)
            # Multi-module tasks (full_stack_hae) declare comma-separated paths.
            for rel in list(task.target_module.split(",")) + list(task.held_out_tests.split(",")):
                rel = rel.strip()
                self.assertTrue(os.path.exists(os.path.join(REPO_ROOT, rel)),
                                f"{task_id}: {rel} does not exist")

    def test_unknown_task_is_an_error(self):
        with self.assertRaises(BenchmarkError):
            self.bench.task("no-such-task")

    def test_no_task_leaks_its_own_test_file(self):
        """The whole design rests on the firm not seeing the oracle."""
        for task_id, task in TASKS.items():
            objective = self.bench.objective_for(task_id)
            self.assertNotIn(task.held_out_tests, objective)
            self.assertNotIn("test_", os.path.basename(task.target_module))
            for visible in task.visible_context:
                self.assertFalse(
                    visible.startswith("tests/"),
                    f"{task_id} exposes a test file as context")

    def test_objective_names_the_target_and_asks_for_a_file(self):
        objective = self.bench.objective_for(TASK)
        self.assertIn(TARGET, objective)
        self.assertIn("write_file", objective)


class TestGrading(unittest.TestCase):
    """End-to-end grading. Shares one reference run; each test spawns pytest."""

    @classmethod
    def setUpClass(cls):
        cls.bench = SelfHostingBenchmark()
        cls.reference = cls.bench.reference_run(TASK)
        with open(os.path.join(REPO_ROOT, TARGET), encoding="utf-8") as fh:
            cls.real = fh.read()

    def grade(self, submission):
        return self.bench.evaluate(TASK, submission, reference=self.reference)

    def test_reference_implementation_is_green(self):
        """If this fails the benchmark is broken, not the firm."""
        self.assertGreater(self.reference["collected"], 0)
        self.assertEqual(self.reference["passed"], self.reference["collected"])

    def test_our_own_implementation_scores_full_marks(self):
        result = self.grade({TARGET: self.real})
        self.assertEqual(result.score, 100.0)
        self.assertFalse(result.rejected)

    def test_a_stub_scores_zero(self):
        result = self.grade({TARGET: '"""Nothing here."""\n'})
        self.assertEqual(result.score, 0.0)

    def test_partially_correct_work_earns_partial_credit(self):
        """A grader with no gradient cannot drive selection."""
        broken = (self.real.replace("def sanitize_path(",
                                    "def _unused_sanitize_path(", 1)
                  + "\n\ndef sanitize_path(path):\n    return path\n")
        result = self.grade({TARGET: broken})
        self.assertGreater(result.score, 0.0)
        self.assertLess(result.score, 100.0)

    # -- adversarial ------------------------------------------------------ #

    def test_rewriting_the_grading_suite_is_rejected(self):
        result = self.grade({
            TARGET: self.real,
            "tests/test_artifacts.py": "def test_ok():\n    assert True\n",
        })
        self.assertTrue(result.rejected)
        self.assertEqual(result.score, 0.0)
        self.assertIn("not the firm's to author", result.rejection_reason)

    def test_nested_test_directory_is_also_rejected(self):
        result = self.grade({TARGET: self.real,
                             "hae/tests/test_sneaky.py": "assert True\n"})
        self.assertTrue(result.rejected)

    def test_absolute_paths_are_rejected(self):
        result = self.grade({TARGET: self.real, "/etc/passwd": "x"})
        self.assertTrue(result.rejected)

    def test_sys_path_manipulation_is_rejected(self):
        result = self.grade({
            TARGET: "import sys\nsys.path.insert(0, '/app')\n"
                    "from hae.evaluation.artifacts import *\n"})
        self.assertTrue(result.rejected)
        self.assertIn("sys.path", result.rejection_reason)

    def test_sandbox_workdir_sys_path_is_allowed(self):
        """Target modules (e.g. harness.py) may insert dynamic sandbox workdir
        into sys.path without triggering anti-cheat rejection."""
        code = self.real + "\n# subprocess probe: sys.path.insert(0, r'{workdir}')\n"
        result = self.grade({TARGET: code})
        self.assertFalse(result.rejected)
        self.assertEqual(result.score, 100.0)

    def test_referencing_the_repository_root_is_rejected(self):
        result = self.grade({TARGET: f"PATH = {REPO_ROOT!r}\n"})
        self.assertTrue(result.rejected)

    def test_submitting_no_module_at_all_is_rejected(self):
        result = self.grade({"README.md": "We considered the problem deeply."})
        self.assertTrue(result.rejected)
        self.assertEqual(result.score, 0.0)

    def test_score_uses_the_reference_denominator(self):
        """Collecting fewer tests must not raise the fraction."""
        result = self.grade({TARGET: '"""Nothing here."""\n'})
        self.assertEqual(result.tests_total, self.reference["collected"])
        self.assertEqual(result.collected, 0)


class TestGradingRefusesOnABrokenReference(unittest.TestCase):

    def test_evaluate_refuses_when_the_reference_is_not_green(self):
        bench = SelfHostingBenchmark()
        with self.assertRaises(BenchmarkError):
            bench.evaluate(TASK, {TARGET: "x = 1\n"},
                           reference={"passed": 3, "collected": 7,
                                      "failures": [], "stderr": ""})


class TestObjectiveContractSpecification(unittest.TestCase):
    """Regression test for Issue #9: objective must specify the exact target
    module path and public API signatures imported by held-out tests."""

    def test_objective_includes_target_path_and_api_contract_without_oracle_leak(self):
        bench = SelfHostingBenchmark()
        obj = bench.objective_for("artifacts")
        self.assertIn("hae/evaluation/artifacts.py", obj)
        self.assertIn("PUBLIC API CONTRACT", obj)
        for name in ("EXCLUDED_DIR_SEGMENTS", "EXCLUDED_DIR_SUFFIXES",
                     "sanitize_path", "filter_bundle", "partition_bundle",
                     "count_source_files", "is_generated_path", "is_malformed_path"):
            self.assertIn(name, obj, f"API contract missing {name}")
        self.assertNotIn("tests/test_artifacts.py", obj)


class TestFailureLocations(unittest.TestCase):
    """V6 reads where each failure's traceback ended inside the firm's own code.

    The failure strings themselves are the V5 contract and must not move.
    """

    def setUp(self):
        self.workdir = tempfile.mkdtemp(prefix="hae_benchmark_")
        self.addCleanup(shutil.rmtree, self.workdir, True)

    def _report(self, workdir=None):
        wd = workdir or self.workdir
        return (
            "test_x (tests.test_execution_harness.T.test_x) ... ERROR\n"
            "test_y (tests.test_execution_harness.T.test_y) ... FAIL\n"
            "test_z (tests.test_execution_harness.T.test_z) ... FAIL\n"
            "\n" + "=" * 70 + "\n"
            "ERROR: test_x (tests.test_execution_harness.T.test_x)\n"
            + "-" * 70 + "\n"
            "Traceback (most recent call last):\n"
            f'  File "{wd}/tests/test_execution_harness.py", line 50, in test_x\n'
            "    result = harness.run()\n"
            f'  File "{wd}/hae/evaluation/harness.py", line 212, in _run_pytest\n'
            "    return mnf_err\n"
            '  File "<string>", line 1, in <module>\n'
            '  File "/usr/lib/python3.13/subprocess.py", line 500, in run\n'
            "    raise CalledProcessError\n"
            "NameError: name 'mnf_err' is not defined\n"
            "\n" + "=" * 70 + "\n"
            "FAIL: test_y (tests.test_execution_harness.T.test_y)\n"
            + "-" * 70 + "\n"
            "Traceback (most recent call last):\n"
            f'  File "{wd}/tests/test_execution_harness.py", line 60, in test_y\n'
            "    self.assertEqual(0, 4)\n"
            "AssertionError: 0 != 4\n"
            "\n" + "=" * 70 + "\n"
            "FAIL: test_z (tests.test_execution_harness.T.test_z)\n"
            + "-" * 70 + "\n"
            "Traceback (most recent call last):\n"
            f'  File "{wd}/tests/test_execution_harness.py", line 70, in test_z\n'
            "    self.assertTrue(harness.ok())\n"
            f'  File "{wd}/hae/evaluation/harness.py", line 40, in ok\n'
            "    return self._check()\n"
            f'  File "{wd}/hae/evaluation/harness.py", line 88, in _check\n'
            "    assert False\n"
            "AssertionError\n"
            "\n" + "-" * 70 + "\n"
            "Ran 3 tests in 0.010s\n\nFAILED (failures=2, errors=1)\n"
        )

    def test_locations_are_keyed_by_the_exact_failure_strings(self):
        report = self._report()
        failures = _extract_failures(report)
        self.assertEqual(failures, [
            "ERROR: test_x (tests.test_execution_harness.T.test_x) -> NameError: name 'mnf_err' is not defined",
            "FAIL: test_y (tests.test_execution_harness.T.test_y) -> AssertionError: 0 != 4",
            "FAIL: test_z (tests.test_execution_harness.T.test_z) -> AssertionError",
        ])
        locations = _extract_failure_locations(report, self.workdir)
        # Deepest in-sandbox frame outside tests/; stdlib and <string> frames do not count.
        self.assertEqual(locations, {
            failures[0]: "hae/evaluation/harness.py:212 in _run_pytest",
            failures[2]: "hae/evaluation/harness.py:88 in _check",
        })
        self.assertNotIn(failures[1], locations)          # only a tests/ frame: nothing to point at
        for location in locations.values():
            self.assertNotIn(self.workdir, location)       # never leaks the sandbox path

    def test_frames_outside_the_sandbox_are_ignored(self):
        other = tempfile.mkdtemp(prefix="hae_other_")
        self.addCleanup(shutil.rmtree, other, True)
        self.assertEqual(_extract_failure_locations(self._report(workdir=other), self.workdir), {})
        self.assertEqual(_frame_location(['  File "lib/mod.py", line 3, in f'], self.workdir),
                         "lib/mod.py:3 in f")                                   # relative paths are accepted
        self.assertEqual(_frame_location(['  File "../escape.py", line 3, in f'], self.workdir), "")
        self.assertEqual(_frame_location(['  File "tests/helpers.py", line 3, in f'], self.workdir), "")

    def test_result_dict_is_unchanged_when_there_are_no_locations(self):
        result = BenchmarkResult(task_id="harness", score=0.0, tests_passed=0, tests_total=3,
                                 collected=3, reference_total=3, failures=["FAIL: a", "FAIL: b"])
        self.assertEqual(sorted(result.to_dict()), [
            "collected", "failures", "reference_total", "rejected", "rejection_reason",
            "score", "task_id", "tests_passed", "tests_total",
        ])
        result.failure_locations = {"FAIL: b": "hae/x.py:1 in f", "FAIL: gone": "hae/y.py:2 in g", "FAIL: a": ""}
        self.assertEqual(result.to_dict()["failure_locations"], {"FAIL: b": "hae/x.py:1 in f"})

    def test_multi_suite_runs_prefix_locations_like_failures(self):
        bench = SelfHostingBenchmark()
        calls = []

        def fake_single(workdir, test_rel):
            calls.append(test_rel)
            tag = os.path.basename(test_rel)
            return {"passed": 1, "collected": 2, "failures": [f"FAIL: t ({tag})"],
                    "failure_locations": {f"FAIL: t ({tag})": f"hae/{tag}.py:1 in f"},
                    "stderr": "", "returncode": 1}

        bench._run_single_suite = fake_single
        run = bench._run_tests("/nonexistent", "tests/test_a.py,tests/test_b.py")
        self.assertEqual(calls, ["tests/test_a.py", "tests/test_b.py"])
        self.assertEqual(run["failures"], ["[a] FAIL: t (test_a.py)", "[b] FAIL: t (test_b.py)"])
        self.assertEqual(run["failure_locations"], {
            "[a] FAIL: t (test_a.py)": "hae/test_a.py.py:1 in f",
            "[b] FAIL: t (test_b.py)": "hae/test_b.py.py:1 in f",
        })

    def test_evaluate_reports_where_a_real_failure_was_raised(self):
        bench = SelfHostingBenchmark()
        reference = bench.reference_run(TASK)
        with open(os.path.join(REPO_ROOT, TARGET), encoding="utf-8") as fh:
            real = fh.read()
        # sanitize_path raising inside the module: the location must be in artifacts.py.
        broken = real + "\n\ndef sanitize_path(path):\n    raise RuntimeError('boom')\n"
        result = bench.evaluate(TASK, {TARGET: broken}, reference=reference)
        self.assertLess(result.score, 100.0)
        self.assertTrue(result.failure_locations, result.failures[:3])
        self.assertTrue(set(result.failure_locations) <= set(result.failures))
        self.assertTrue(all(loc.startswith("hae/evaluation/artifacts.py:") and loc.endswith(" in sanitize_path")
                            for loc in result.failure_locations.values()), result.failure_locations)
        self.assertIn("failure_locations", result.to_dict())


if __name__ == "__main__":
    unittest.main()
