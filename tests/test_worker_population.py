"""How the worker reads a population file.

The breeder writes an envelope; the worker indexed the envelope as if it were
a list. Every unit test passed and the campaign died in-cluster with
`KeyError: 0` -- the sixth interface disagreement between two real components
found in a single session, and the third that no mock could have caught.

The guard that was supposed to prevent it made the bug worse. `firm_index <
len(raw)` on a five-key dict compares against five, so indices 0-4 passed a
bounds check that had not located the population at all.
"""

import json
import os
import tempfile
import unittest

from hae.orchestration.worker import evaluate_single_firm

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _genome(company_id="firm_a"):
    with open(os.path.join(REPO, "templates", "default_company.json")) as fh:
        g = json.load(fh)
    g["company_id"] = company_id
    return g


class PopulationShapeTest(unittest.TestCase):
    """Loading stops before any LLM call, so these run offline.

    Each case drives the real `evaluate_single_firm` and asserts on how far it
    gets: a shape that should load must get past loading, and a shape that
    should be refused must raise before anything is spent.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _write(self, payload):
        p = os.path.join(self.tmp, "pop.json")
        with open(p, "w") as fh:
            json.dump(payload, fh)
        return p

    def _load_only(self, path, index=0):
        """Runs the loader and reports the genome it selected.

        `evaluate_single_firm` goes on to call an LLM, which we neither want
        nor can do here, so this stops at the first thing that needs one and
        reports what was loaded up to that point.
        """
        import hae.orchestration.worker as w
        seen = {}
        real = w.HierarchicalCompanyRunner

        class Stop(Exception):
            pass

        class Spy:
            def __init__(self, genome, **kw):
                seen["company_id"] = genome.company_id
                raise Stop()

        w.HierarchicalCompanyRunner = Spy
        try:
            evaluate_single_firm(
                generation=999, firm_index=index, region="us-east4",
                population_file=path, output_dir=self.tmp,
                objective="x")
        except Stop:
            pass
        finally:
            w.HierarchicalCompanyRunner = real
        return seen.get("company_id")

    def test_breeder_envelope_loads(self):
        """The shape `Breeder.write` actually produces. This is the
        regression."""
        path = self._write({"generation": 1, "name": "n", "objective": "",
                            "benchmark_task": "",
                            "population": [_genome("envelope_firm")]})
        self.assertEqual(self._load_only(path), "envelope_firm")

    def test_bare_list_still_loads(self):
        """V1 archives are a bare list. Reading the historical record must not
        require rewriting it."""
        path = self._write([_genome("list_firm")])
        self.assertEqual(self._load_only(path), "list_firm")

    def test_index_selects_within_the_envelope(self):
        path = self._write({"population": [_genome("a"), _genome("b"),
                                           _genome("c")]})
        self.assertEqual(self._load_only(path, index=2), "c")

    def test_out_of_range_is_measured_against_the_population(self):
        """The original defect: a five-key envelope made index 3 look valid.
        The bound must come from the genome list, not the container."""
        path = self._write({"generation": 1, "name": "n", "objective": "",
                            "benchmark_task": "",
                            "population": [_genome("only")]})
        with self.assertRaises(IndexError):
            self._load_only(path, index=3)

    def test_object_without_population_is_refused(self):
        path = self._write({"generation": 1, "firms": [_genome()]})
        with self.assertRaises(ValueError):
            self._load_only(path)

    def test_population_of_wrong_type_is_refused(self):
        path = self._write({"population": {"0": _genome()}})
        with self.assertRaises(ValueError):
            self._load_only(path)

    def test_scalar_file_is_refused(self):
        path = self._write("not a population")
        with self.assertRaises(ValueError):
            self._load_only(path)


class IterativeRepairTest(unittest.TestCase):
    """Tests ground-truth failure extraction and multi-iteration repair loop."""

    def test_extract_failures_includes_assertion_without_source(self):
        from hae.evaluation.benchmark import _extract_failures
        sample = (
            "======================================================================\n"
            "FAIL: test_markdown_contaminated_paths_are_malformed (test_artifacts.TestArtifacts)\n"
            "----------------------------------------------------------------------\n"
            "Traceback (most recent call last):\n"
            "  File \"/tmp/work/tests/test_artifacts.py\", line 62, in test_markdown_contaminated_paths_are_malformed\n"
            "    self.assertTrue(is_malformed_path(p), p)\n"
            "AssertionError: False is not true : notes.\n"
            "\n----------------------------------------------------------------------\n"
            "Ran 7 tests in 0.002s\n\nFAILED (failures=1)\n"
        )
        failures = _extract_failures(sample)
        self.assertEqual(len(failures), 1)
        self.assertIn("FAIL: test_markdown_contaminated_paths_are_malformed", failures[0])
        self.assertIn("AssertionError: False is not true : notes.", failures[0])
        self.assertNotIn("File ", failures[0])
        self.assertNotIn("self.assertTrue", failures[0])

    def test_multi_iteration_early_stops_on_convergence_and_rolls_back_regression(self):
        import hae.orchestration.worker as w
        from hae.task import Task, VerificationOutcome

        tmp = tempfile.mkdtemp()
        pop_path = os.path.join(tmp, "pop.json")
        with open(pop_path, "w") as fh:
            json.dump({"population": [_genome("repair_firm")]}, fh)

        objectives_seen = []

        class FakeWorkspace:
            def __init__(self):
                self.files = {}
            def write_file(self, p, c):
                self.files[p] = c

        class FakeVLoop:
            def __init__(self):
                self.used = 0

        class FakeRunner:
            def __init__(self, genome, **kw):
                self.genome = genome
                self.workspace = FakeWorkspace()
                self.verification_loop = FakeVLoop()
                self.calls = 0

            def run(self, objective):
                self.calls += 1
                objectives_seen.append(objective)
                # Simulate writing different workspace snapshots per iteration
                files = {"hae/evaluation/artifacts.py": f"# iter {self.calls}"}
                self.workspace.write_file("hae/evaluation/artifacts.py", files["hae/evaluation/artifacts.py"])
                return {
                    "final_deliverable": f"deliverable {self.calls}",
                    "departmental_briefs": {"eng": f"brief {self.calls}"},
                    "elapsed_seconds": 1.5,
                    "token_usage": 1000 * self.calls,
                    "workspace_files": files,
                    "budget_exhausted": False,
                    "opex": {"estimated_cost_usd": 0.10 * self.calls, "budget_usd": 5.0},
                }

        class FakeVerifier:
            name = "benchmark:artifacts"
            def __init__(self):
                self.verifications = 0
            def describe(self):
                return "fake-verifier"
            def to_dict(self):
                return {"type": "fake"}
            def verify(self, submission):
                self.verifications += 1
                if self.verifications == 1:
                    # Iter 1: 85.71%
                    return VerificationOutcome(
                        evaluable=True, score=85.71, verifier=self.name,
                        detail="6/7 held-out tests passed",
                        evidence={"failures": ["FAIL: test_x -> AssertionError: notes."]}
                    )
                elif self.verifications == 2:
                    # Iter 2: Regression to 71.43%
                    return VerificationOutcome(
                        evaluable=True, score=71.43, verifier=self.name,
                        detail="5/7 held-out tests passed",
                        evidence={"failures": ["FAIL: test_x", "FAIL: test_y"]}
                    )
                else:
                    # Iter 3: Converged 100.0%
                    return VerificationOutcome(
                        evaluable=True, score=100.0, verifier=self.name,
                        detail="7/7 held-out tests passed",
                        evidence={"failures": []}
                    )

        class FakeJudge:
            def evaluate(self, **kw):
                class Res:
                    class Fitness:
                        fitness_score = 95.0
                        strategic_depth = 90.0
                        technical_feasibility = 95.0
                        cross_functional_coherence = 90.0
                        risk_mitigation = 90.0
                        actionability_and_synthesis = 90.0
                        execution_integrity = kw["verification"].score
                        execution_evaluable = True
                        evaluation_failed = False
                        elapsed_seconds = kw["elapsed_seconds"]
                        token_usage = kw["token_usage"]
                    fitness = Fitness()
                    def to_dict(self):
                        return {"score": 95.0}
                return Res()

        real_runner = w.HierarchicalCompanyRunner
        real_judge = w.StrategicFitnessEvaluator
        w.HierarchicalCompanyRunner = FakeRunner
        w.StrategicFitnessEvaluator = FakeJudge
        try:
            task = Task(
                task_id="test-iter",
                objective="Implement artifacts",
                verifier=FakeVerifier(),
                max_iterations=10,
            )
            res = evaluate_single_firm(
                firm_index=0, generation=4, objective=task.objective,
                output_dir=tmp, region="us-east4", gcs_bucket="",
                population_file=pop_path, task=task
            )
            self.assertEqual(res["iterations_used"], 3)
            self.assertEqual(len(res["iterations_history"]), 3)
            self.assertEqual(res["iterations_history"][0]["score"], 85.71)
            self.assertEqual(res["iterations_history"][1]["score"], 71.43)
            self.assertEqual(res["iterations_history"][2]["score"], 100.0)
            self.assertEqual(res["verifier_outcome"]["score"], 100.0)
            self.assertEqual(res["elapsed_seconds"], 4.5)
            # Iteration 2 objective should contain ground-truth failure from Iteration 1
            self.assertIn("AssertionError: notes.", objectives_seen[1])
            # Iteration 3 objective should still reference best score (85.71), not regressed 71.43
            self.assertIn("85.71/100.0", objectives_seen[2])
        finally:
            w.HierarchicalCompanyRunner = real_runner
            w.StrategicFitnessEvaluator = real_judge


if __name__ == "__main__":
    unittest.main()
