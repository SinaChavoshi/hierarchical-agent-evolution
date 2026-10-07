"""The worker dispatches repair iterations by the `epistemic_policy` gene.

Drives the real `evaluate_single_firm` loop with a fake runner so the test
can see which entry point each iteration used, what reached the evaluator,
and what landed on disk. The contract: iteration 1 is `run()` in both modes;
with the gene enabled, iteration >= 2 is `run_epistemic_search()` fed the
oracle's failures; the ledger is reconciled against the final verdict; the
non-LLM audit reaches the evaluator and the scorecard; and a sidecar
`<firm>_epistemic_tree.json` is written. With the gene disabled, none of that
happens and the V5 path is byte-for-byte what it was.
"""

import json
import os
import shutil
import tempfile
import unittest

import hae.orchestration.worker as w
from hae.orchestration.worker import evaluate_single_firm
from hae.task import Task, VerificationOutcome

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _genome(company_id, enabled):
    with open(os.path.join(REPO, "templates", "default_company.json")) as fh:
        g = json.load(fh)
    g["company_id"] = company_id
    g["epistemic_policy"] = {"enabled": enabled, "search_budget_moves": 6}
    return g


class _FakeWorkspace:
    def __init__(self):
        self.files = {}

    def write_file(self, p, c):
        self.files[p] = c


class _FakeVLoop:
    used = 0


class _FakeLedger:
    def __init__(self):
        self.reconciled = []

    def to_dict(self):
        return {"company_id": "fake", "reconciled": list(self.reconciled)}


class _FakeRunner:
    """Records which entry point each iteration used."""

    log = []
    locations_seen = []

    def __init__(self, genome, **kw):
        self.genome = genome
        self.workspace = _FakeWorkspace()
        self.verification_loop = _FakeVLoop()
        self.calls = 0
        self.epistemic_state = None
        self.epistemic_searches = []

    def _record(self):
        self.calls += 1
        files = {"hae/evaluation/artifacts.py": f"# iter {self.calls}\n" + "x = 1\n" * 40}
        self.workspace.write_file("hae/evaluation/artifacts.py", files["hae/evaluation/artifacts.py"])
        return {
            "final_deliverable": f"deliverable {self.calls}",
            "departmental_briefs": {"eng": f"brief {self.calls}"},
            "elapsed_seconds": 1.0,
            "token_usage": 1000 * self.calls,
            "workspace_files": files,
            "budget_exhausted": False,
            "opex": {"estimated_cost_usd": 0.05 * self.calls, "budget_usd": 5.0},
        }

    def run(self, objective):
        _FakeRunner.log.append(("run", None))
        return self._record()

    def run_epistemic_search(self, objective, failures, iteration=2, max_iterations=1,
                             locations=None):
        _FakeRunner.log.append(("search", list(failures)))
        _FakeRunner.locations_seen.append(dict(locations or {}))
        assert "GROUND-TRUTH VERIFIER FEEDBACK" in objective
        if self.epistemic_state is None:
            self.epistemic_state = _FakeLedger()
        self.epistemic_searches.append({"iteration": iteration, "moves_used": 3})
        out = self._record()
        out["epistemic_ledger"] = self.epistemic_state.to_dict()
        return out

    def reconcile_epistemic_state(self, failures, iteration, locations=None):
        if self.epistemic_state is None:
            self.epistemic_state = _FakeLedger()
        self.epistemic_state.reconciled.append((iteration, list(failures)))
        _FakeRunner.locations_seen.append(dict(locations or {}))
        return {"certified": [], "reopened": [], "refreshed": [], "seeded": []}

    def epistemic_audit(self, token_usage=0):
        return {"questions": 1, "questions_certified": 1, "hypotheses_tested": 2,
                "evidence_backed_fraction": 1.0, "status_inconsistencies": 0,
                "calibration_brier": 0.1, "calibration_tested": 2, "proposed_total": 2,
                "tabu_rejections": 0, "moves_used": 3, "budget_moves": 6,
                "resolved_fraction": 1.0, "ledger_hash": "abc123", "token_usage": token_usage}


class _FakeVerifier:
    name = "benchmark:artifacts"

    def __init__(self, scores):
        self.scores = list(scores)
        self.n = 0

    def describe(self):
        return "fake-verifier"

    def to_dict(self):
        return {"type": "fake"}

    def verify(self, submission):
        score = self.scores[min(self.n, len(self.scores) - 1)]
        self.n += 1
        failures = [] if score >= 100.0 else [f"[artifact_hygiene] FAIL: test_{self.n} -> AssertionError"]
        evidence = {"failures": failures}
        if failures:
            # What BenchmarkResult.to_dict() emits when a traceback ended in the firm's code.
            evidence["failure_locations"] = {failures[0]: "hae/evaluation/artifacts.py:12 in sanitize_path"}
        return VerificationOutcome(evaluable=True, score=score, verifier=self.name,
                                   detail=f"{score}", evidence=evidence)


class _FakeJudge:
    seen = []

    def evaluate(self, **kw):
        _FakeJudge.seen.append(kw)

        class Res:
            class Fitness:
                fitness_score = 90.0
                strategic_depth = 90.0
                technical_feasibility = 90.0
                cross_functional_coherence = 90.0
                risk_mitigation = 90.0
                actionability_and_synthesis = 90.0
                execution_integrity = kw["verification"].score
                execution_evaluable = True
                evaluation_failed = False
                elapsed_seconds = kw["elapsed_seconds"]
                token_usage = kw["token_usage"]
                epistemic_integrity = 77.0 if kw.get("epistemic_audit") else None
                epistemic_efficiency = 88.0 if kw.get("epistemic_audit") else None
                epistemic_evaluable = bool(kw.get("epistemic_audit"))
            fitness = Fitness()

            def to_dict(self):
                return {"score": 90.0}
        return Res()


class WorkerDispatchTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hae_epi_worker_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        _FakeRunner.log = []
        _FakeRunner.locations_seen = []
        _FakeJudge.seen = []
        self._real = (w.HierarchicalCompanyRunner, w.StrategicFitnessEvaluator)
        w.HierarchicalCompanyRunner = _FakeRunner
        w.StrategicFitnessEvaluator = _FakeJudge
        self.addCleanup(self._restore)

    def _restore(self):
        w.HierarchicalCompanyRunner, w.StrategicFitnessEvaluator = self._real

    def _run(self, company_id, enabled, scores, max_iterations=3):
        pop = os.path.join(self.tmp, f"{company_id}.json")
        with open(pop, "w") as fh:
            json.dump({"population": [_genome(company_id, enabled)]}, fh)
        task = Task(task_id="t", objective="Implement artifacts",
                    verifier=_FakeVerifier(scores), max_iterations=max_iterations)
        return evaluate_single_firm(
            firm_index=0, generation=16, objective=task.objective, output_dir=self.tmp,
            region="us-east4", gcs_bucket="", population_file=pop, task=task)

    def test_enabled_gene_routes_repair_iterations_to_the_search(self):
        res = self._run("v6_firm", True, scores=[60.0, 80.0, 100.0])
        kinds = [k for k, _ in _FakeRunner.log]
        self.assertEqual(kinds, ["run", "search", "search"])
        # The search is fed the oracle's failures from the best iteration so far.
        self.assertTrue(all(f for _, f in _FakeRunner.log[1:]))
        self.assertIn("[artifact_hygiene]", _FakeRunner.log[1][1][0])
        # ...together with where each failure's traceback ended, keyed by that failure.
        first_failure = _FakeRunner.log[1][1][0]
        self.assertEqual(_FakeRunner.locations_seen[0],
                         {first_failure: "hae/evaluation/artifacts.py:12 in sanitize_path"})
        # The audit reached the evaluator and the scorecard.
        self.assertIsNotNone(_FakeJudge.seen[-1]["epistemic_audit"])
        self.assertEqual(res["epistemic_audit"]["questions_certified"], 1)
        self.assertEqual(res["epistemic_integrity"], 77.0)
        self.assertEqual(res["epistemic_efficiency"], 88.0)
        self.assertTrue(res["epistemic_evaluable"])
        self.assertIn("epistemic_ledger", res["run_output"])
        self.assertEqual(len(res["run_output"]["epistemic_searches"]), 2)
        # The ledger was reconciled against the final oracle verdict (no failures).
        reconciled = res["run_output"]["epistemic_ledger"]["reconciled"]
        self.assertEqual(reconciled[-1][1], [])
        # And the sidecar is on disk next to the scorecard.
        tree = os.path.join(self.tmp, "generation_16", "v6_firm_epistemic_tree.json")
        self.assertTrue(os.path.exists(tree))
        with open(tree) as fh:
            payload = json.load(fh)
        self.assertEqual(payload["audit"]["moves_used"], 3)
        self.assertTrue(payload["policy"]["enabled"])
        self.assertEqual(len(payload["searches"]), 2)

    def test_disabled_gene_is_the_v5_path(self):
        res = self._run("v5_firm", False, scores=[60.0, 100.0])
        kinds = [k for k, _ in _FakeRunner.log]
        self.assertEqual(kinds, ["run", "run"])
        self.assertIsNone(_FakeJudge.seen[-1]["epistemic_audit"])
        self.assertIsNone(res["epistemic_audit"])
        self.assertIsNone(res["epistemic_integrity"])
        self.assertFalse(res["epistemic_evaluable"])
        self.assertNotIn("epistemic_ledger", res["run_output"])
        self.assertFalse(os.path.exists(
            os.path.join(self.tmp, "generation_16", "v5_firm_epistemic_tree.json")))

    def test_converging_on_iteration_one_never_opens_a_ledger(self):
        res = self._run("lucky_firm", True, scores=[100.0])
        self.assertEqual([k for k, _ in _FakeRunner.log], ["run"])
        self.assertIsNone(res["epistemic_audit"])
        self.assertFalse(res["epistemic_evaluable"])

    def test_single_iteration_task_ignores_the_gene(self):
        res = self._run("one_shot", True, scores=[50.0], max_iterations=1)
        self.assertEqual([k for k, _ in _FakeRunner.log], ["run"])
        self.assertIsNone(res["epistemic_audit"])


if __name__ == "__main__":
    unittest.main()
