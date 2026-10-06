"""The gatekeeper runs real probes in a real sandbox and writes the only verdicts that count."""

import os
import shutil
import tempfile
import unittest

from hae.epistemic.gatekeeper import (
    failure_signature,
    INCONCLUSIVE, EvidenceGatekeeper, failure_key, failure_summary,
)
from hae.epistemic.ledger import (
    CERTIFIED, FALSIFIED, Q_CERTIFIED, Q_EXHAUSTED, Q_OPEN, Q_RESOLVED, SUPPORTED, UNTESTABLE, UNVERIFIED,
    EpistemicState,
)
from hae.runtime.workspace import AgentWorkspace

BUGGY = "def add(a, b):\n    return a - b\n"
FIXED = "def add(a, b):\n    return a + b\n"
PROBE = "from mypkg.calc import add\nprint('ADD', add(2, 2))\n"


class GatekeeperFixture(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hae_gk_test_")
        self.ws = AgentWorkspace("gk_firm", base_dir=self.tmp)
        self.ws.write_file("mypkg/__init__.py", "")
        self.ws.write_file("mypkg/calc.py", BUGGY)
        self.state = EpistemicState("gk_firm")
        self.q = self.state.add_question("Why does test_add fail?", module="mypkg/calc.py",
                                         source_failure_key="calc:test_add")
        self.gk = EvidenceGatekeeper(self.ws, timeout_s=15, max_probe_lines=20,
                                     isolate=False, stage_reference=False)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _hyp(self, claim, prior, prediction, probe=PROBE):
        return self.state.add_hypothesis(self.q.question_id, claim, prior=prior,
                                         probe_code=probe, prediction=prediction)


class ExperimentTests(GatekeeperFixture):

    def test_matching_prediction_supports(self):
        h = self._hyp("add subtracts", 0.3, {"expect_exit_code": 0, "expect_stdout_contains": "ADD 0"})
        ev = self.gk.run_experiment(self.state, h)
        self.assertEqual(ev.kind, "probe")
        self.assertTrue(ev.matched_prediction, ev.detail)
        delta = self.gk.apply(self.state, h, ev)
        self.assertEqual(h.status, SUPPORTED)
        self.assertGreater(h.posterior, h.prior)
        self.assertGreater(delta, 0.0)
        self.assertEqual(h.evidence_ids, [ev.evidence_id])

    def test_failed_prediction_falsifies_and_makes_tabu(self):
        h = self._hyp("add is correct", 0.8, {"expect_exit_code": 0, "expect_stdout_contains": "ADD 4"})
        ev = self.gk.run_experiment(self.state, h)
        self.assertFalse(ev.matched_prediction)
        delta = self.gk.apply(self.state, h, ev)
        self.assertEqual(h.status, FALSIFIED)
        self.assertEqual(h.posterior, 0.0)
        self.assertGreater(delta, 0.0)
        self.assertTrue(self.state.is_tabu(h.mechanism_signature, self.q.question_id))
        # Sole hypothesis falsified -> the question is exhausted, not resolved.
        self.assertEqual(self.q.status, Q_EXHAUSTED)
        self.assertGreater(self.q.uncertainty, 0.0)

    def test_elimination_alone_never_resolves_a_question(self):
        hs = [self._hyp(f"wrong {i} mechanism {i}", 0.3, {"expect_stdout_contains": f"NOPE{i}"}) for i in range(3)]
        for h in hs:
            self.gk.apply(self.state, h, self.gk.run_experiment(self.state, h))
        self.assertTrue(all(h.status == FALSIFIED for h in hs))
        self.assertGreaterEqual(self.q.uncertainty, 0.5)

    def test_vacuous_prediction_cannot_support(self):
        h = self._hyp("something", 0.5, {})
        ev = self.gk.run_experiment(self.state, h)
        self.assertFalse(ev.matched_prediction)
        self.assertIn("vacuous", ev.detail)
        self.gk.apply(self.state, h, ev)
        self.assertEqual(h.status, FALSIFIED)

    def test_timeout_is_inconclusive_not_falsifying(self):
        gk = EvidenceGatekeeper(self.ws, timeout_s=1, isolate=False, stage_reference=False)
        h = self._hyp("slow", 0.5, {"expect_exit_code": 0}, probe="import time\ntime.sleep(5)\nprint('done')\n")
        ev = gk.run_experiment(self.state, h)
        self.assertTrue(ev.detail.startswith(INCONCLUSIVE), ev.detail)
        delta = gk.apply(self.state, h, ev)
        self.assertEqual(delta, 0.0)
        self.assertEqual(h.status, UNVERIFIED)
        self.assertEqual(len(self.state.evidence_log), 1)

    def test_exit_code_only_prediction_is_falsifiable(self):
        h = self._hyp("assert-style", 0.5, {"expect_exit_code": 0},
                      probe="from mypkg.calc import add\nassert add(2, 2) == 4\n")
        ev = self.gk.run_experiment(self.state, h)
        self.assertFalse(ev.matched_prediction)
        self.assertEqual(ev.exit_code, 1)

    def test_probe_runs_against_the_firms_code_in_a_stage(self):
        h = self._hyp("stage", 0.5, {"expect_stdout_contains": "ADD 0"})
        ev = self.gk.run_experiment(self.state, h)
        self.assertTrue(ev.matched_prediction)
        # Nothing was left behind in the workspace.
        self.assertFalse(any(f["path"].startswith("_probe") for f in self.ws.list_files()))

    def test_duplicate_evidence_id_is_impossible(self):
        h = self._hyp("x", 0.5, {"expect_exit_code": 0})
        ev1 = self.gk.run_experiment(self.state, h)
        ev2 = self.gk.run_experiment(self.state, h)
        self.assertNotEqual(ev1.evidence_id, ev2.evidence_id)


class ProbeHygieneTests(GatekeeperFixture):

    def _rejected(self, code):
        h = self._hyp(f"escape {abs(hash(code))}", 0.5, {"expect_exit_code": 0}, probe=code)
        ev = self.gk.run_experiment(self.state, h)
        self.assertEqual(ev.kind, "probe_rejected", code)
        self.assertEqual(self.gk.apply(self.state, h, ev), 0.0)
        # Parked, not judged: no verdict, no ruled_out entry, probe repairable.
        self.assertEqual(h.status, UNTESTABLE)
        self.assertFalse(h.tested)
        self.assertEqual(h.probe_rejections, 1)
        self.assertNotIn(h.hypothesis_id, {b.hypothesis_id for b in self.state.ruled_out})
        return ev

    def test_reference_tree_paths_are_rejected(self):
        self._rejected("print(open('/app/hae/evaluation/harness.py').read())")
        self._rejected("import os; print(os.listdir('/usr/lib'))")
        self._rejected("print(open('../../hae/evaluation/harness.py').read())")

    def test_import_system_escapes_are_rejected(self):
        self._rejected("import sys; sys.path.insert(0, '/tmp/x'); import hae")
        self._rejected("import inspect, mypkg.calc; print(inspect.getsource(mypkg.calc))")
        self._rejected("import site; print([p for p in __import__('sys').path if 'site-packages' in p])")

    def test_network_and_oracle_are_rejected(self):
        self._rejected("import socket; socket.create_connection(('169.254.169.254', 80))")
        self._rejected("import urllib.request; print(urllib.request.urlopen('http://x').read())")
        self._rejected("import tests.held_out.test_execution_harness")

    def test_line_limit_and_syntax(self):
        self._rejected("\n".join(f"x{i} = {i}" for i in range(25)) + "\nprint('ok')")
        self._rejected("def broken(:\n    pass")
        self.assertEqual(self.gk.probes_rejected, 2)

    def test_empty_probe(self):
        self.assertEqual(self.gk.validate_probe("   \n"), "empty probe")


class ModuleCheckAndSynthesisTests(GatekeeperFixture):

    def test_verify_module_passes_for_importable_module(self):
        ev = self.gk.verify_module(self.state, "mypkg/calc.py", self.q.question_id)
        self.assertEqual(ev.kind, "module_check")
        self.assertTrue(ev.matched_prediction, ev.stderr)
        self.assertIn("IMPORT_OK", ev.stdout)

    def test_verify_module_fails_on_syntax_error(self):
        self.ws.write_file("mypkg/broken.py", "def f(:\n  pass\n")
        ev = self.gk.verify_module(self.state, "mypkg/broken.py")
        self.assertFalse(ev.matched_prediction)
        self.assertIn("py_compile", ev.detail)

    def test_verify_module_runs_firm_tests(self):
        self.ws.write_file("tests/__init__.py", "")
        self.ws.write_file("tests/test_calc.py",
                           "import unittest\nfrom mypkg.calc import add\n"
                           "class T(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(2, 2), 4)\n")
        ev = self.gk.verify_module(self.state, "mypkg/calc.py")
        self.assertFalse(ev.matched_prediction)
        self.assertIn("firm_tests", ev.detail)
        self.ws.write_file("mypkg/calc.py", FIXED)
        ev2 = self.gk.verify_module(self.state, "mypkg/calc.py")
        self.assertTrue(ev2.matched_prediction, ev2.stderr)

    def test_missing_module_fails(self):
        ev = self.gk.verify_module(self.state, "mypkg/nope.py")
        self.assertFalse(ev.matched_prediction)
        self.assertIn("missing", ev.detail)

    def test_apply_synthesis_resolves_question_only_on_pass(self):
        h = self._hyp("add subtracts", 0.3, {"expect_stdout_contains": "ADD 0"})
        self.gk.apply(self.state, h, self.gk.run_experiment(self.state, h))
        bad = self.gk.verify_module(self.state, "mypkg/nope.py", self.q.question_id, h.hypothesis_id)
        self.assertEqual(self.gk.apply_synthesis(self.state, self.q, h, bad), 0.0)
        self.assertEqual(h.synthesis_failures, 1)
        self.assertEqual(self.q.status, Q_OPEN)
        self.ws.write_file("mypkg/calc.py", FIXED)
        good = self.gk.verify_module(self.state, "mypkg/calc.py", self.q.question_id, h.hypothesis_id)
        delta = self.gk.apply_synthesis(self.state, self.q, h, good)
        self.assertGreater(delta, 0.0)
        self.assertTrue(h.patch_applied)
        self.assertEqual(self.q.status, Q_RESOLVED)
        self.assertLessEqual(self.q.uncertainty, 0.02)


class OracleReconciliationTests(GatekeeperFixture):

    FAIL = "[calc] FAIL: test_add (tests.test_calc.T.test_add) -> AssertionError: 0 != 4"
    FAIL2 = "[calc] ERROR: test_sub (tests.test_calc.T.test_sub) -> TypeError: boom"

    def test_failure_key_is_stable_across_messages(self):
        k1, tag, name = failure_key(self.FAIL)
        k2, _, _ = failure_key("[calc] FAIL: test_add (x) -> AssertionError: 1 != 4")
        self.assertEqual(k1, k2)
        self.assertEqual((tag, name), ("calc", "test_add"))
        self.assertEqual(failure_key("FAIL: test_plain")[0], "test_plain")
        self.assertTrue(failure_key("something odd")[0].startswith("raw:"))
        self.assertEqual(failure_summary(self.FAIL), "AssertionError: 0 != 4")

    def test_seeds_questions_from_failures_once(self):
        state = EpistemicState("f")
        gk = EvidenceGatekeeper(self.ws, isolate=False, stage_reference=False,
                                module_for_tag={"calc": "mypkg/calc.py"})
        rec = gk.reconcile_with_oracle(state, [self.FAIL, self.FAIL2], iteration=1)
        self.assertEqual(len(rec["seeded"]), 2)
        q = state.questions[rec["seeded"][0]]
        self.assertEqual(q.module, "mypkg/calc.py")
        self.assertEqual(q.source_failure_key, "calc:test_add")
        self.assertIn("test_add", q.text)
        rec2 = gk.reconcile_with_oracle(state, [self.FAIL, self.FAIL2], iteration=2)
        self.assertEqual(rec2["seeded"], [])
        self.assertEqual(len(state.questions), 2)
        self.assertEqual(state.evidence_log[-1].kind, "oracle")

    SUITE = "[harness] suite failed to import: ModuleNotFoundError: No module named 'mypkg.harness'"

    def test_a_suite_that_cannot_import_is_attributed_to_its_module(self):
        # Gen 16 cohort finding #7: a first pass that never wrote a module makes
        # the oracle say `suite failed to import`; without a tag the question had
        # no module and every synthesis landed on an existing file.
        key, tag, name = failure_key(self.SUITE)
        self.assertEqual((key, tag, name), ("harness:suite_import", "harness", "suite_import"))
        self.assertEqual(failure_key("[harness] suite failed to import: SyntaxError: bad")[0], "harness:suite_import")
        self.assertEqual(failure_signature(self.SUITE), "")      # its own cluster; nothing to merge on
        state = EpistemicState("f")
        gk = EvidenceGatekeeper(self.ws, isolate=False, stage_reference=False,
                                module_for_tag={"calc": "mypkg/calc.py", "harness": "mypkg/harness.py"})
        rec = gk.reconcile_with_oracle(state, [self.SUITE, self.FAIL], iteration=1, default_module="")
        self.assertEqual(len(rec["seeded"]), 2)
        q = state.questions[rec["seeded"][0]]
        self.assertEqual(q.module, "mypkg/harness.py")
        self.assertEqual(q.source_failure_key, "harness:suite_import")
        self.assertIn("cannot import", q.text.replace("can the `harness` suite not import", "cannot import"))
        self.assertIn("mypkg/harness.py", q.text)
        self.assertNotIn("[harness]", q.text)
        # The same line next iteration matches the same question (no re-seeding).
        rec2 = gk.reconcile_with_oracle(state, [self.SUITE, self.FAIL], iteration=2)
        self.assertEqual(rec2["seeded"], [])

    def test_vanished_failure_certifies_question(self):
        h = self._hyp("add subtracts", 0.3, {"expect_stdout_contains": "ADD 0"})
        self.gk.apply(self.state, h, self.gk.run_experiment(self.state, h))
        rec = self.gk.reconcile_with_oracle(self.state, [], iteration=2)
        self.assertEqual(rec["certified"], [self.q.question_id])
        self.assertEqual(self.q.status, Q_CERTIFIED)
        self.assertEqual(h.status, CERTIFIED)
        self.assertEqual(self.state.settled_knowledge[0].certified_by, "oracle")
        self.assertEqual(self.state.total_uncertainty(), 0.0)

    def test_persisting_failure_reopens_resolved_question_and_falsifies_patch(self):
        h = self._hyp("add subtracts", 0.3, {"expect_stdout_contains": "ADD 0"})
        self.gk.apply(self.state, h, self.gk.run_experiment(self.state, h))
        good = self.gk.verify_module(self.state, "mypkg/calc.py", self.q.question_id, h.hypothesis_id)
        self.gk.apply_synthesis(self.state, self.q, h, good)
        self.assertEqual(self.q.status, Q_RESOLVED)
        rec = self.gk.reconcile_with_oracle(self.state, [self.FAIL], iteration=2)
        self.assertEqual(rec["reopened"], [self.q.question_id])
        self.assertEqual(self.q.status, Q_OPEN)
        self.assertGreaterEqual(self.q.uncertainty, 0.7)
        self.assertEqual(h.status, FALSIFIED)
        self.assertTrue(self.state.is_tabu(h.mechanism_signature, self.q.question_id))

    def test_open_question_stays_open_when_failure_persists(self):
        rec = self.gk.reconcile_with_oracle(self.state, [self.FAIL], iteration=2)
        self.assertEqual(rec["reopened"], [])
        self.assertEqual(self.q.status, Q_OPEN)
        self.assertEqual(self.q.source_failure, self.FAIL)


if __name__ == "__main__":
    unittest.main()
