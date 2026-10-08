"""Gatekeeper executor mode: probes and module checks run in place in a task repository.

The `executor=None` path is covered, unchanged, by tests/test_epistemic_gatekeeper.py.
"""

import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, ".")

from hae.epistemic.gatekeeper import (  # noqa: E402
    RESOLVED_FLOOR, SELF_ORACLE_COMMAND, EvidenceGatekeeper,
)
from hae.epistemic.ledger import FALSIFIED, Q_OPEN, Q_RESOLVED, SUPPORTED, EpistemicState  # noqa: E402
from hae.swebench.executor import LocalExecutor  # noqa: E402

BUGGY = "def f(x):\n    return x + 2\n"
FIXED = "def f(x):\n    return x + 1\n"
# Exits 1 while the bug is present (the *buggy* behaviour is what the probe predicts).
REPRO = "import sys\nfrom pkg.mod import f\nprint('F', f(1))\nsys.exit(1 if f(1) == 3 else 0)\n"


def _git(root, *args):
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True).stdout


def _write(root, rel, body):
    full = os.path.join(root, rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as fh:
        fh.write(body)


class ExecutorGatekeeperFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        _git(self.root, "init", "-q")
        _git(self.root, "config", "user.email", "t@example.com")
        _git(self.root, "config", "user.name", "t")
        _write(self.root, "pkg/__init__.py", "")
        _write(self.root, "pkg/mod.py", BUGGY)
        _write(self.root, "tests/__init__.py", "")
        _write(self.root, "tests/test_mod.py", "from pkg.mod import f\n\ndef test_type():\n    assert isinstance(f(1), int)\n")
        _write(self.root, "conftest.py", "")
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-q", "-m", "init")
        self.ex = LocalExecutor(self.root)
        self.state = EpistemicState("swe_firm")
        self.root_q = self.state.add_question("Reproduce the reported behaviour: f(1) returns 3", module="pkg/mod.py",
                                              uncertainty=1.0, source_failure_key="swebench:x__y-1")
        self.gk = EvidenceGatekeeper(workspace=None, timeout_s=30, max_probe_lines=20,
                                     executor=self.ex, isolate=False, stage_reference=False)

    def tearDown(self):
        self.tmp.cleanup()

    def _hyp(self, prediction, probe=REPRO, q=None):
        return self.state.add_hypothesis((q or self.root_q).question_id, "f adds 2 instead of 1",
                                         prior=0.5, probe_code=probe, prediction=prediction)

    def _has_pytest(self):
        return subprocess.run([sys.executable, "-c", "import pytest"], capture_output=True).returncode == 0


class ConstructionTests(ExecutorGatekeeperFixture):
    def test_defaults_come_from_executor(self):
        self.assertEqual(self.gk.repo_root, self.root)
        self.assertEqual(self.gk.python, sys.executable)
        self.assertIs(self.gk.executor, self.ex)

    def test_none_executor_keeps_staged_path(self):
        gk = EvidenceGatekeeper(workspace=None, repo_root=self.root, python_executable=sys.executable,
                                isolate=False, stage_reference=False)
        self.assertIsNone(gk.executor)
        self.assertIsNone(gk.test_command)


class ProbeTests(ExecutorGatekeeperFixture):
    def test_probe_runs_in_repo_root_and_is_written_under_scratch(self):
        h = self._hyp({"expect_exit_code": 1, "expect_stdout_contains": "F 3"})
        ev = self.gk.run_experiment(self.state, h)
        self.assertEqual(ev.kind, "probe")
        self.assertTrue(ev.matched_prediction, ev.detail)
        self.assertEqual(ev.command, f"python .hae/probe_{h.hypothesis_id}.py")
        self.assertTrue(os.path.exists(os.path.join(self.root, ".hae", f"probe_{h.hypothesis_id}.py")))
        self.assertEqual(self.gk.probes_run, 1)
        self.gk.apply(self.state, h, ev)
        self.assertEqual(h.status, SUPPORTED)

    def test_probe_sees_edited_source_in_place(self):
        h = self._hyp({"expect_exit_code": 1})
        _write(self.root, "pkg/mod.py", FIXED)
        ev = self.gk.run_experiment(self.state, h)
        self.assertFalse(ev.matched_prediction)
        self.assertEqual(ev.exit_code, 0)

    def test_rejected_probe_never_touches_executor(self):
        h = self._hyp({"expect_exit_code": 1}, probe="import sys\nsys.path.insert(0, '/tmp')\n")
        ev = self.gk.run_experiment(self.state, h)
        self.assertEqual(ev.kind, "probe_rejected")
        self.assertFalse(os.path.exists(os.path.join(self.root, ".hae")))

    def test_probe_referencing_repo_root_is_rejected(self):
        h = self._hyp({"expect_exit_code": 1}, probe=f"open('{self.root}/pkg/mod.py').read()\n")
        self.assertEqual(self.gk.run_experiment(self.state, h).kind, "probe_rejected")

    def test_timeout_is_inconclusive(self):
        self.gk.timeout_s = 1
        h = self._hyp({"expect_exit_code": 1}, probe="import time\ntime.sleep(5)\n")
        ev = self.gk.run_experiment(self.state, h)
        self.assertIn("inconclusive", ev.detail)
        self.assertEqual(ev.exit_code, -1)


class ModuleCheckTests(ExecutorGatekeeperFixture):
    def test_passes_and_runs_proximity_tests(self):
        ev = self.gk.verify_module(self.state, "pkg/mod.py", question_id=self.root_q.question_id)
        self.assertTrue(ev.matched_prediction, ev.detail + "\n" + ev.stderr)
        self.assertIn("py_compile, import, static_check, repo_tests", ev.detail)
        self.assertIn("[import] IMPORT_OK pkg.mod", ev.stdout)
        if self._has_pytest():
            self.assertIn("proximity tests passed: tests/test_mod.py", ev.stdout)
        else:
            self.assertIn("pytest is not installed", ev.stdout)
        self.assertIn("in place via", ev.command)

    def test_absolute_path_is_relativised(self):
        ev = self.gk.verify_module(self.state, os.path.join(self.root, "pkg", "mod.py"))
        self.assertTrue(ev.matched_prediction, ev.detail)
        self.assertIn("verify_module pkg/mod.py", ev.command)

    def test_missing_module_fails(self):
        ev = self.gk.verify_module(self.state, "pkg/nope.py")
        self.assertFalse(ev.matched_prediction)
        self.assertIn("missing", ev.detail)

    def test_syntax_error_fails_at_py_compile(self):
        _write(self.root, "pkg/mod.py", "def f(x:\n")
        ev = self.gk.verify_module(self.state, "pkg/mod.py")
        self.assertFalse(ev.matched_prediction)
        self.assertIn("py_compile", ev.detail)

    def test_import_time_error_fails_at_import(self):
        _write(self.root, "pkg/mod.py", "raise RuntimeError('boom at import')\n")
        ev = self.gk.verify_module(self.state, "pkg/mod.py")
        self.assertFalse(ev.matched_prediction)
        self.assertIn("failed at import", ev.detail)
        self.assertIn("boom at import", ev.detail)

    def test_src_layout_tries_stripped_dotted_name_first(self):
        _write(self.root, "src/pkg2/__init__.py", "")
        _write(self.root, "src/pkg2/m.py", "VALUE = 1\n")
        ev = self.gk.verify_module(self.state, "src/pkg2/m.py")
        self.assertTrue(ev.matched_prediction, ev.detail)
        self.assertIn("IMPORT_OK", ev.stdout)

    def test_new_undefined_name_fails_static_check(self):
        _write(self.root, "pkg/mod.py", "def f(x):\n    return x + missing_name\n")
        ev = self.gk.verify_module(self.state, "pkg/mod.py")
        self.assertFalse(ev.matched_prediction)
        self.assertIn("static_check", ev.detail)
        self.assertIn("missing_name", ev.detail)

    def test_preexisting_findings_at_head_do_not_block(self):
        _write(self.root, "pkg/legacy.py", "def g():\n    return legacy_undefined\n")
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-q", "-m", "legacy")
        _write(self.root, "pkg/legacy.py", "def g():\n    return legacy_undefined\n\n\ndef h():\n    return 1\n")
        ev = self.gk.verify_module(self.state, "pkg/legacy.py")
        self.assertTrue(ev.matched_prediction, ev.detail)
        self.assertIn("already present at HEAD", ev.stdout)
        _write(self.root, "pkg/legacy.py", "def g():\n    return legacy_undefined\n\n\ndef h():\n    return brand_new\n")
        ev2 = self.gk.verify_module(self.state, "pkg/legacy.py")
        self.assertFalse(ev2.matched_prediction)
        self.assertIn("brand_new", ev2.detail)
        self.assertNotIn("legacy_undefined", ev2.detail)

    def test_failing_proximity_test_fails_the_check(self):
        if not self._has_pytest():
            self.skipTest("pytest not installed")
        _write(self.root, "tests/test_mod_regress.py", "from pkg.mod import f\n\ndef test_value():\n    assert f(1) == 2\n")
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-q", "-m", "regress")
        ev = self.gk.verify_module(self.state, "pkg/mod.py")
        self.assertFalse(ev.matched_prediction)
        self.assertIn("failed at repo_tests", ev.detail)
        self.assertIn("tests/test_mod.py", ev.detail)
        _write(self.root, "pkg/mod.py", FIXED)
        ev2 = self.gk.verify_module(self.state, "pkg/mod.py")
        self.assertTrue(ev2.matched_prediction, ev2.detail)

    def test_startup_failure_caused_by_the_module_fails_the_check(self):
        # The V8 rerun on marshmallow-1810 rewrote `Schema.__init__` so that
        # every schema raised; the repository's conftest builds schemas at
        # import time, pytest exited 4 before collecting anything, and the
        # gate recorded "inconclusive" and let the write through.
        if not self._has_pytest():
            self.skipTest("pytest not installed")
        _write(self.root, "conftest.py", "from pkg.mod import f\n\nVALUE = f(1)\n")
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-q", "-m", "conftest uses the module")
        ev_ok = self.gk.verify_module(self.state, "pkg/mod.py")
        self.assertTrue(ev_ok.matched_prediction, ev_ok.detail)
        _write(self.root, "pkg/mod.py", "def f(x):\n    return x.missing_attribute\n")
        ev = self.gk.verify_module(self.state, "pkg/mod.py")
        self.assertFalse(ev.matched_prediction)
        self.assertIn("failed at repo_tests", ev.detail)
        self.assertIn("could not start (pytest exited 4)", ev.detail)
        self.assertIn("traceback runs through pkg/mod.py", ev.detail)
        self.assertIn("AttributeError", ev.detail)

    def test_startup_failure_elsewhere_stays_inconclusive(self):
        if not self._has_pytest():
            self.skipTest("pytest not installed")
        _write(self.root, "conftest.py", "import module_that_does_not_exist_anywhere\n")
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-q", "-m", "broken conftest")
        ev = self.gk.verify_module(self.state, "pkg/mod.py")
        self.assertTrue(ev.matched_prediction, ev.detail)
        self.assertIn("pytest exited 4 (internal/usage error)", ev.stdout)
        self.assertIn("inconclusive, step skipped", ev.stdout)

    def test_test_command_overrides_proximity(self):
        gk_ok = EvidenceGatekeeper(workspace=None, executor=self.ex, isolate=False, test_command="exit 0")
        ev = gk_ok.verify_module(self.state, "pkg/mod.py")
        self.assertTrue(ev.matched_prediction, ev.detail)
        self.assertIn("test_command tests passed", ev.stdout)
        gk_bad = EvidenceGatekeeper(workspace=None, executor=self.ex, isolate=False, test_command=["sh", "-c", "echo 1 failed; exit 1"])
        ev2 = gk_bad.verify_module(self.state, "pkg/mod.py")
        self.assertFalse(ev2.matched_prediction)
        self.assertIn("failed at repo_tests", ev2.detail)

    def test_no_nearby_test_is_a_note_not_a_failure(self):
        _write(self.root, "pkg/lonely.py", "X = 1\n")
        ev = self.gk.verify_module(self.state, "pkg/lonely.py")
        self.assertTrue(ev.matched_prediction, ev.detail)
        self.assertIn("no test file near pkg/lonely.py", ev.stdout)


class ProximityTests(ExecutorGatekeeperFixture):
    def test_selection_by_filename_only(self):
        for rel in ("tests/test_mod_extra.py", "tests/test_other.py", "tests/test_modern.py",
                    "pkg/tests/test_mod_inner.py", "tests/sub/tests.py", "tests/mod/tests.py",
                    "pkg/test_mod.py", "tests/mod_test.py", "tests/not_a_test_mod.py"):
            _write(self.root, rel, "")
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-q", "-m", "more tests")
        self.assertEqual(self.gk._proximity_tests("pkg/mod.py", limit=10), [
            "tests/mod_test.py", "tests/test_mod.py", "tests/mod/tests.py",
            "tests/test_mod_extra.py", "pkg/tests/test_mod_inner.py"])
        self.assertEqual(self.gk._proximity_tests("pkg/mod.py"), [
            "tests/mod_test.py", "tests/test_mod.py", "tests/mod/tests.py"])
        self.assertEqual(self.gk._proximity_tests("pkg/__init__.py", limit=10), [])
        self.assertEqual(self.gk._proximity_tests("other/thing.py"), [])

    def test_init_uses_package_directory(self):
        _write(self.root, "tests/test_pkg.py", "")
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-q", "-m", "pkg test")
        self.assertEqual(self.gk._proximity_tests("pkg/__init__.py"), ["tests/test_pkg.py"])

    def test_argv_shape(self):
        argv, files, mode = self.gk._repo_tests_argv("pkg/mod.py")
        self.assertEqual(mode, "proximity")
        self.assertEqual(files, ["tests/test_mod.py"])
        self.assertEqual(argv, [sys.executable, "-m", "pytest", "-x", "-q", "--no-header", "-p", "no:cacheprovider",
                                "tests/test_mod.py"])


class ReproductionRecheckTests(ExecutorGatekeeperFixture):
    def _supported_root_probe(self):
        h = self._hyp({"expect_exit_code": 1, "expect_stdout_contains": "F 3"})
        ev = self.gk.run_experiment(self.state, h)
        self.gk.apply(self.state, h, ev)
        self.assertEqual(h.status, SUPPORTED)
        return h

    def test_bug_gone_resolves_root(self):
        h = self._supported_root_probe()
        _write(self.root, "pkg/mod.py", FIXED)
        out = self.gk.reconcile_with_reproduction(self.state, self.root_q.question_id, iteration=1)
        self.assertEqual(out["verdict"], "resolved")
        self.assertEqual(out["bug_gone_hypotheses"], [h.hypothesis_id])
        self.assertEqual(self.root_q.status, Q_RESOLVED)
        self.assertAlmostEqual(self.root_q.uncertainty, RESOLVED_FLOOR)
        summary = self.state.evidence_by_id(out["evidence_id"])
        self.assertEqual(summary.kind, "oracle")
        self.assertEqual(summary.command, SELF_ORACLE_COMMAND)
        self.assertTrue(summary.matched_prediction)
        self.assertEqual(h.status, SUPPORTED)  # re-run records evidence but does not re-judge the hypothesis
        rerun = out["reruns"][0]
        self.assertTrue(rerun["completed"] and not rerun["matched_prediction"] and rerun["exit_code"] == 0)

    def test_still_reproducing_reopens_questions_resolved_this_iteration(self):
        self._supported_root_probe()
        sub = self.state.add_question("Where: pkg/mod.py", module="pkg/mod.py", uncertainty=0.8,
                                      source_failure_key="swebench:x__y-1:pkg/mod.py")
        hs = self._hyp({"expect_exit_code": 1}, q=sub)
        ev = self.gk.run_experiment(self.state, hs)
        self.gk.apply(self.state, hs, ev)
        hs.patch_applied = True
        self.state.set_question_status(sub.question_id, Q_RESOLVED, self.gk.authority)
        out = self.gk.reconcile_with_reproduction(self.state, self.root_q.question_id, iteration=1,
                                                  resolved_this_iteration=[sub.question_id])
        self.assertEqual(out["verdict"], "still_reproduces")
        self.assertEqual(out["reopened"], [sub.question_id])
        self.assertEqual(sub.status, Q_OPEN)
        self.assertAlmostEqual(sub.uncertainty, 0.8 * 0.75)
        self.assertEqual(hs.status, FALSIFIED)
        self.assertEqual(self.root_q.status, Q_OPEN)

    def test_probe_that_now_crashes_is_not_bug_gone(self):
        self._supported_root_probe()
        _write(self.root, "pkg/mod.py", "def f(x):\n    raise ValueError('new crash')\n")
        out = self.gk.reconcile_with_reproduction(self.state, self.root_q.question_id)
        self.assertEqual(out["verdict"], "still_reproduces")
        self.assertFalse(out["reruns"][0]["bug_no_longer_reproduces"])
        self.assertNotEqual(out["reruns"][0]["exit_code"], 0)

    def test_inconclusive_without_supported_probe(self):
        self._hyp({"expect_exit_code": 1})  # untested
        out = self.gk.reconcile_with_reproduction(self.state, self.root_q.question_id)
        self.assertEqual(out["verdict"], "inconclusive")
        self.assertEqual(out["probes_rerun"], 0)
        self.assertEqual(self.root_q.status, Q_OPEN)


class WholeSuiteTests(ExecutorGatekeeperFixture):
    """`calibrate_whole_suite` and the whole-suite `repo_tests` mode (defect 3, part 2).

    The V9 run's cosmetic rewrite broke `tests/test_decorators.py` on
    marshmallow while the gate ran only `tests/test_fields.py` (the
    proximity selection for `fields.py`). When the clean checkout's suite is
    cheap the gate runs all of it and compares against the base commit.
    """

    def setUp(self):
        super().setUp()
        if not self._has_pytest():
            self.skipTest("pytest not installed")

    def commit(self, msg="more"):
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-q", "-m", msg)

    def test_calibration_switches_mode_and_tolerates_baseline_failures(self):
        _write(self.root, "tests/test_other.py", "def test_pre_existing():\n    assert False, 'broken at base'\n")
        self.commit()
        info = self.gk.calibrate_whole_suite(60)
        self.assertEqual(info["mode"], "whole_suite", info)
        self.assertEqual(info["target"], "tests")
        self.assertEqual(info["baseline_failures"], 1)
        self.assertEqual(self.gk.whole_suite["baseline"], ["tests/test_other.py::test_pre_existing"])
        self.assertGreaterEqual(self.gk.whole_suite["timeout_s"], 60.0)
        argv, files, mode = self.gk._repo_tests_argv("pkg/mod.py")
        self.assertEqual(mode, "whole_suite")
        self.assertEqual(files, ["tests"])
        self.assertNotIn("-x", argv)
        self.assertIn("-rfE", argv)
        ev = self.gk.verify_module(self.state, "pkg/mod.py")
        self.assertTrue(ev.matched_prediction, ev.detail + "\n" + ev.stdout)
        self.assertIn("whole suite: no new failure", ev.stdout)
        self.assertIn("1 baseline failure(s)", ev.stdout)

    def test_regression_outside_the_proximity_files_fails_the_check(self):
        # Passes at the base commit (it pins the buggy value) and lives in a file
        # the proximity heuristic for `mod.py` never selects.
        _write(self.root, "tests/test_far_away.py", "from pkg.mod import f\n\ndef test_pins_value():\n    assert f(1) == 3\n")
        self.commit()
        self.assertEqual(self.gk._proximity_tests("pkg/mod.py"), ["tests/test_mod.py"])
        info = self.gk.calibrate_whole_suite(60)
        self.assertEqual(info["mode"], "whole_suite", info)
        self.assertEqual(info["baseline_failures"], 0)
        _write(self.root, "pkg/mod.py", FIXED)
        ev = self.gk.verify_module(self.state, "pkg/mod.py")
        self.assertFalse(ev.matched_prediction)
        self.assertIn("failed at repo_tests", ev.detail)
        self.assertIn("1 new failure(s) not present on the clean checkout", ev.detail)
        self.assertIn("tests/test_far_away.py::test_pins_value", ev.detail)
        # The same write passes the proximity gate: that is the gap being closed.
        plain = EvidenceGatekeeper(workspace=None, timeout_s=30, executor=self.ex, isolate=False, stage_reference=False)
        ev2 = plain.verify_module(self.state, "pkg/mod.py")
        self.assertTrue(ev2.matched_prediction, ev2.detail)

    def test_over_budget_keeps_the_proximity_selection(self):
        _write(self.root, "tests/test_slow.py", "import time\n\ndef test_slow():\n    time.sleep(3)\n")
        self.commit()
        info = self.gk.calibrate_whole_suite(1)
        self.assertEqual(info["mode"], "proximity", info)
        self.assertIn("did not finish within 1s", info["reason"])
        self.assertIsNone(self.gk.whole_suite)
        self.assertEqual(self.gk._repo_tests_argv("pkg/mod.py")[2], "proximity")

    def test_flaky_new_failure_is_rerun_alone_and_forgiven(self):
        # Passes on the calibration run (count 0), fails on the gate run
        # (count 1), passes again when re-run alone (count 2).
        _write(self.root, "tests/test_flaky.py",
               "import os\nP = os.path.join(os.path.dirname(__file__), 'flaky_count.txt')\n\n"
               "def test_flaky():\n    n = int(open(P).read()) if os.path.exists(P) else 0\n"
               "    open(P, 'w').write(str(n + 1))\n    assert n != 1\n")
        self.commit()
        info = self.gk.calibrate_whole_suite(60)
        self.assertEqual(info["mode"], "whole_suite", info)
        self.assertEqual(info["baseline_failures"], 0)
        ev = self.gk.verify_module(self.state, "pkg/mod.py")
        self.assertTrue(ev.matched_prediction, ev.detail + "\n" + ev.stdout)
        self.assertIn("did not reproduce when re-run alone (flaky): tests/test_flaky.py::test_flaky", ev.stdout)

    def test_suite_that_stops_finishing_fails_the_step(self):
        _write(self.root, "tests/test_hang.py",
               "import time\nfrom pkg.mod import f\n\ndef test_hangs_after_the_fix():\n    if f(1) == 2:\n        time.sleep(6)\n")
        self.commit()
        info = self.gk.calibrate_whole_suite(60)
        self.assertEqual(info["mode"], "whole_suite", info)
        self.gk.whole_suite["timeout_s"] = 1.5          # the floor is 60 s in production
        _write(self.root, "pkg/mod.py", FIXED)
        ev = self.gk.verify_module(self.state, "pkg/mod.py")
        self.assertFalse(ev.matched_prediction)
        self.assertIn("failed at repo_tests", ev.detail)
        self.assertIn("did not finish within 2s", ev.detail)
        self.assertIn("may have introduced a hang", ev.detail)

    def test_explicit_test_command_and_zero_budget_disable_calibration(self):
        gk = EvidenceGatekeeper(workspace=None, executor=self.ex, isolate=False, test_command="exit 0")
        info = gk.calibrate_whole_suite(60)
        self.assertEqual((info["mode"], info["reason"]), ("proximity", "explicit test_command"))
        self.assertEqual(gk._repo_tests_argv("pkg/mod.py")[2], "test_command")
        info0 = self.gk.calibrate_whole_suite(0)
        self.assertEqual(info0["mode"], "proximity")
        self.assertIn("disabled", info0["reason"])
        staged = EvidenceGatekeeper(workspace=None, repo_root=self.root, python_executable=sys.executable,
                                    isolate=False, stage_reference=False)
        self.assertEqual(staged.calibrate_whole_suite(60)["mode"], "proximity")

    def test_summary_parser(self):
        text = ("tests/test_a.py ..F.                                                     [ 50%]\n"
                "=========================== short test summary info ============================\n"
                "FAILED tests/test_a.py::test_x - AssertionError: 1 != 2\n"
                "FAILED tests/test_a.py::TestB::test_y[param a-b] - assert False\n"
                "ERROR tests/test_c.py - ImportError: cannot import name 'g'\n"
                "ERROR tests/test_d.py::test_z\n"
                "FAILED tests/test_a.py::test_x - AssertionError: 1 != 2\n"
                "3 failed, 1 passed, 2 errors in 0.12s\n")
        self.assertEqual(EvidenceGatekeeper.failing_test_ids(text), [
            "tests/test_a.py::test_x", "tests/test_a.py::TestB::test_y[param a-b]", "tests/test_c.py",
            "tests/test_d.py::test_z"])
        self.assertEqual(EvidenceGatekeeper.failing_test_ids("1 passed in 0.01s\n"), [])


if __name__ == "__main__":
    unittest.main()
