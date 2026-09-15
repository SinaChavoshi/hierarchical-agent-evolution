"""Preflight checks, exercised without touching the network.

The checks themselves make real requests by design, so these tests drive them
through injected fakes. What is worth testing here is the *decision logic*:
what gets skipped, what blocks a launch, and what advice a failure prints --
because a preflight that fails open, or that reports a cascade of four errors
when one variable is unset, is worse than no preflight at all.
"""

import unittest
import urllib.error
from unittest import mock

from hae.infra import preflight
from hae.infra.config import EvolutionConfig
from hae.infra.preflight import (
    CheckResult,
    PreflightReport,
    check_config,
    repair_iam,
    run_preflight,
)


def _config(**kw):
    defaults = dict(project_id="p", gcs_bucket="b", location="us-east4")
    defaults.update(kw)
    cfg = EvolutionConfig()
    for k, v in defaults.items():
        setattr(cfg, k, v)
    return cfg


class _FakeProc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


class ReportSemanticsTest(unittest.TestCase):

    def test_skipped_check_is_not_a_pass(self):
        """A check we never ran must not count as evidence that it would pass."""
        report = PreflightReport(checks=[
            CheckResult("a", ok=True, detail=""),
            CheckResult("b", ok=False, skipped=True, detail="skipped"),
        ])
        self.assertFalse(report.ok)

    def test_all_pass_is_ok(self):
        report = PreflightReport(checks=[CheckResult("a", ok=True, detail="")])
        self.assertTrue(report.ok)

    def test_render_prints_remedy_only_for_failures(self):
        report = PreflightReport(checks=[
            CheckResult("good", ok=True, detail="fine", remedy="DO NOT SHOW"),
            CheckResult("bad", ok=False, detail="broke", remedy="SHOW THIS"),
        ])
        text = report.render()
        self.assertIn("SHOW THIS", text)
        self.assertNotIn("DO NOT SHOW", text)
        self.assertIn("LAUNCH BLOCKED", text)


class ConfigCheckTest(unittest.TestCase):

    def test_missing_bucket_fails_with_actionable_remedy(self):
        result = check_config(_config(gcs_bucket=""))
        self.assertFalse(result.ok)
        self.assertIn("GCS_BUCKET", result.remedy)

    def test_missing_project_fails(self):
        self.assertFalse(check_config(_config(project_id=None)).ok)

    def test_complete_config_passes(self):
        self.assertTrue(check_config(_config()).ok)


class ShortCircuitTest(unittest.TestCase):
    """One root cause should produce one diagnosis, not four."""

    def test_bad_config_skips_everything_downstream(self):
        report = run_preflight(config=_config(project_id=None))
        self.assertFalse(report.ok)
        statuses = {c.name: c.status for c in report.checks}
        self.assertEqual(statuses["config"], "FAIL")
        for name in ("credentials", "vertex", "gcs"):
            self.assertEqual(statuses[name], "SKIP")
        # Exactly one thing is reported as broken.
        self.assertEqual(len(report.failures), 1)

    def test_missing_credentials_skips_vertex_and_gcs(self):
        with mock.patch.object(preflight, "detect_llm_provider", return_value="vertex"), \
             mock.patch.object(preflight, "get_adc_access_token", return_value=None):
            report = run_preflight(config=_config())
        statuses = {c.name: c.status for c in report.checks}
        self.assertEqual(statuses["credentials"], "FAIL")
        self.assertEqual(statuses["vertex"], "SKIP")
        self.assertEqual(statuses["gcs"], "SKIP")

    def test_non_vertex_provider_skips_vertex_checks(self):
        with mock.patch.object(preflight, "detect_llm_provider", return_value="ollama"):
            report = run_preflight(config=_config())
        self.assertTrue(report.ok)
        self.assertNotIn("credentials", {c.name for c in report.checks})


class VertexCheckTest(unittest.TestCase):

    def _run(self, side_effect):
        with mock.patch.object(preflight.urllib.request, "urlopen",
                               side_effect=side_effect):
            return preflight.check_vertex_model("m", _config(), "tok")

    def test_403_names_latchkey_regrant_in_remedy(self):
        err = urllib.error.HTTPError("u", 403, "Forbidden", {}, None)
        err.read = lambda: b"PERMISSION_DENIED"
        result = self._run(err)
        self.assertFalse(result.ok)
        self.assertIn("add-iam-policy-binding", result.remedy)
        # The remedy must tell you to verify, because the grant has silently
        # not stuck before.
        self.assertIn("get-iam-policy", result.remedy)

    def test_404_suggests_region_or_model_name(self):
        err = urllib.error.HTTPError("u", 404, "Not Found", {}, None)
        err.read = lambda: b"not found"
        result = self._run(err)
        self.assertIn("GOOGLE_CLOUD_LOCATION", result.remedy)

    def test_429_blocks_launch_rather_than_warning(self):
        err = urllib.error.HTTPError("u", 429, "Too Many", {}, None)
        err.read = lambda: b"quota"
        result = self._run(err)
        self.assertFalse(result.ok)
        self.assertIn("Quota", result.remedy)


class IamRepairTest(unittest.TestCase):
    """Latchkey reaps our bindings, so repair must be idempotent and honest."""

    def test_noop_when_roles_already_present(self):
        with mock.patch.object(preflight, "_gcloud") as g:
            g.return_value = _FakeProc(
                stdout="roles/aiplatform.user\nroles/storage.objectAdmin\n")
            result = repair_iam("p", "sa@p.iam.gserviceaccount.com")
        self.assertTrue(result.ok)
        self.assertIn("already bound", result.detail)
        self.assertEqual(g.call_count, 1)  # read only, no writes

    def test_grants_only_the_missing_role(self):
        calls = []

        def fake(args, timeout=120):
            calls.append(args)
            if args[1] == "get-iam-policy":
                return _FakeProc(stdout="roles/storage.objectAdmin\n")
            return _FakeProc(returncode=0)

        with mock.patch.object(preflight, "_gcloud", side_effect=fake):
            result = repair_iam("p", "sa@p.iam.gserviceaccount.com")

        self.assertTrue(result.ok)
        adds = [c for c in calls if c[1] == "add-iam-policy-binding"]
        self.assertEqual(len(adds), 1)
        self.assertIn("--role=roles/aiplatform.user", adds[0])

    def test_reports_recurrence_so_nobody_treats_it_as_fixed(self):
        def fake(args, timeout=120):
            if args[1] == "get-iam-policy":
                return _FakeProc(stdout="")
            return _FakeProc(returncode=0)

        with mock.patch.object(preflight, "_gcloud", side_effect=fake):
            result = repair_iam("p", "sa@p.iam.gserviceaccount.com")
        self.assertIn("Latchkey", result.detail)

    def test_permission_denied_points_at_the_durable_fix(self):
        def fake(args, timeout=120):
            if args[1] == "get-iam-policy":
                return _FakeProc(stdout="")
            return _FakeProc(returncode=1, stderr="PERMISSION_DENIED")

        with mock.patch.object(preflight, "_gcloud", side_effect=fake):
            result = repair_iam("p", "sa@p.iam.gserviceaccount.com")
        self.assertFalse(result.ok)
        self.assertIn("exemption", result.remedy)

    def test_repair_without_service_account_fails_loudly(self):
        with mock.patch.object(preflight, "detect_llm_provider", return_value="vertex"), \
             mock.patch.object(preflight, "get_adc_access_token", return_value="tok"), \
             mock.patch.object(preflight, "_env_gsa", return_value=None), \
             mock.patch.object(preflight, "check_vertex_model",
                               return_value=CheckResult("vertex:m", True, "")), \
             mock.patch.object(preflight, "check_gcs_writable",
                               return_value=CheckResult("gcs", True, "")):
            report = run_preflight(config=_config(), repair=True, models=["m"])
        self.assertFalse(report.ok)
        self.assertIn("AGENT_GSA",
                      next(c for c in report.checks if c.name == "iam-repair").remedy)


class PropagationTest(unittest.TestCase):
    """A 403 right after a grant is usually lag, not denial."""

    def test_retries_403_after_a_repair_then_succeeds(self):
        attempts = {"n": 0}

        def flaky(model, config, token):
            attempts["n"] += 1
            if attempts["n"] < 3:
                return CheckResult(f"vertex:{model}", False, "HTTP 403: denied")
            return CheckResult(f"vertex:{model}", True, "reachable")

        with mock.patch.object(preflight, "check_vertex_model", side_effect=flaky), \
             mock.patch.object(preflight.time, "sleep"):
            result = preflight.await_propagation(_config(), "m", "tok", sleep_s=0)
        self.assertTrue(result.ok)
        self.assertIn("3 attempt", result.detail)

    def test_non_403_failure_does_not_wait(self):
        calls = {"n": 0}

        def always_404(model, config, token):
            calls["n"] += 1
            return CheckResult(f"vertex:{model}", False, "HTTP 404: no model")

        with mock.patch.object(preflight, "check_vertex_model", side_effect=always_404), \
             mock.patch.object(preflight.time, "sleep"):
            result = preflight.await_propagation(_config(), "m", "tok", sleep_s=0)
        self.assertFalse(result.ok)
        self.assertEqual(calls["n"], 1)  # gave up immediately


class CheckIsolationTest(unittest.TestCase):

    def test_a_crashing_check_fails_that_check_not_the_launcher(self):
        def boom(cfg):
            raise ValueError("kaboom")
        boom._check_name = "exploding"
        result = preflight._timed(boom, _config())
        self.assertFalse(result.ok)
        self.assertIn("kaboom", result.detail)


if __name__ == "__main__":
    unittest.main()
