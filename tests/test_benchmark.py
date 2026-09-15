"""Tests for the self-hosting benchmark.

These are tests of a grader, so they are mostly adversarial: the interesting
question is not whether a correct submission scores well but whether an
incorrect or dishonest one can score well anyway.
"""

import os
import unittest

from hae.evaluation.benchmark import (
    BenchmarkError,
    SelfHostingBenchmark,
    TASKS,
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
            self.assertTrue(os.path.exists(
                os.path.join(REPO_ROOT, task.target_module)))
            self.assertTrue(os.path.exists(
                os.path.join(REPO_ROOT, task.held_out_tests)))

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


if __name__ == "__main__":
    unittest.main()
