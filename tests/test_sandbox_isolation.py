"""Tests for the network isolation around agent-authored shell commands.

These exist because of a measured failure. In-cluster, on the real Workload
Identity service account, agent code inside the sandbox ran

    curl http://169.254.169.254/computeMetadata/v1/instance/service-accounts/\
default/token

and got back HTTP 200 with a live access token -- even though
`GCE_METADATA_HOST` was pointed at a discard port. An environment variable
only binds callers that read it, and `curl` does not.

The fix is an empty network namespace per command. What follows pins the
behaviour that makes that fix trustworthy: that it is actually applied, that
it is reported honestly, and above all that it never degrades silently.
"""

import os
import unittest
from unittest import mock

from hae.runtime.workspace import AgentWorkspace


class NetnsProbeTest(unittest.TestCase):
    """The probe answers by doing, and answers once."""

    def setUp(self):
        AgentWorkspace._NETNS_SUPPORTED = None

    def tearDown(self):
        AgentWorkspace._NETNS_SUPPORTED = None

    def test_probe_actually_runs_unshare(self):
        """`which unshare` is a different question from whether the kernel
        permits an unprivileged user namespace. Only the second one matters,
        so the probe has to attempt it."""
        with mock.patch("subprocess.run") as run:
            run.return_value = mock.Mock(returncode=0)
            self.assertTrue(AgentWorkspace._netns_available())
            argv = run.call_args[0][0]
        self.assertEqual(argv, ["unshare", "-rn", "true"])

    def test_nonzero_exit_means_unavailable(self):
        with mock.patch("subprocess.run") as run:
            run.return_value = mock.Mock(returncode=1)
            self.assertFalse(AgentWorkspace._netns_available())

    def test_probe_failure_is_not_fatal(self):
        """A host without `unshare` at all must degrade to False, not raise --
        the caller decides what to do about it."""
        with mock.patch("subprocess.run", side_effect=FileNotFoundError):
            self.assertFalse(AgentWorkspace._netns_available())

    def test_result_is_cached_across_calls(self):
        with mock.patch("subprocess.run") as run:
            run.return_value = mock.Mock(returncode=0)
            for _ in range(5):
                AgentWorkspace._netns_available()
        self.assertEqual(run.call_count, 1)


class ArgvShapeTest(unittest.TestCase):
    """What actually gets executed."""

    def setUp(self):
        self.ws = AgentWorkspace("test_argv", base_dir="/tmp/hae_test_iso")

    def _capture(self, isolated):
        with mock.patch.object(AgentWorkspace, "_netns_available",
                               return_value=isolated):
            with mock.patch("subprocess.run") as run:
                run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
                res = self.ws.execute_bash("python3 -m pytest")
                return run.call_args, res

    def test_isolated_command_is_wrapped(self):
        call, res = self._capture(True)
        self.assertEqual(call[0][0],
                         ["unshare", "-rn", "/bin/sh", "-c", "python3 -m pytest"])
        self.assertTrue(res["network_isolated"])

    def test_no_outer_shell(self):
        """`shell=True` would put the whole argv through a second round of
        quoting. Agent commands routinely contain quotes -- `-p 'test_*.py'` --
        so that round is a real source of breakage, not a hypothetical one."""
        call, _ = self._capture(True)
        self.assertNotIn("shell", call[1])

    def test_fallback_still_runs_the_command(self):
        """Where namespaces are unavailable and not required -- a developer
        laptop, a test runner -- the command still runs. It is just labelled
        honestly."""
        call, res = self._capture(False)
        self.assertEqual(call[0][0], ["/bin/sh", "-c", "python3 -m pytest"])
        self.assertFalse(res["network_isolated"])


class FailClosedTest(unittest.TestCase):
    """The property the whole thing rests on."""

    def setUp(self):
        self.ws = AgentWorkspace("test_failclosed", base_dir="/tmp/hae_test_iso")

    def test_refuses_when_required_and_unavailable(self):
        """A sandbox that stops sandboxing without saying so is worse than no
        sandbox, because the surrounding code goes on trusting it."""
        with mock.patch.object(AgentWorkspace, "_netns_available",
                               return_value=False):
            with mock.patch.dict(os.environ,
                                 {"HAE_REQUIRE_NETWORK_ISOLATION": "1"}):
                with mock.patch("subprocess.run") as run:
                    res = self.ws.execute_bash("echo hi")
        self.assertEqual(res["status"], "error")
        self.assertFalse(res["network_isolated"])
        self.assertIn("169.254.169.254", res["stderr"])
        run.assert_not_called()

    def test_requirement_satisfied_proceeds(self):
        with mock.patch.object(AgentWorkspace, "_netns_available",
                               return_value=True):
            with mock.patch.dict(os.environ,
                                 {"HAE_REQUIRE_NETWORK_ISOLATION": "1"}):
                with mock.patch("subprocess.run") as run:
                    run.return_value = mock.Mock(
                        returncode=0, stdout="hi\n", stderr="")
                    res = self.ws.execute_bash("echo hi")
        self.assertEqual(res["status"], "ok")
        self.assertTrue(res["network_isolated"])

    def test_only_exactly_one_enables_the_requirement(self):
        """Guard against a truthy-string bug quietly disarming the control:
        `HAE_REQUIRE_NETWORK_ISOLATION=0` must not be read as enabled."""
        with mock.patch.object(AgentWorkspace, "_netns_available",
                               return_value=False):
            with mock.patch.dict(os.environ,
                                 {"HAE_REQUIRE_NETWORK_ISOLATION": "0"}):
                with mock.patch("subprocess.run") as run:
                    run.return_value = mock.Mock(
                        returncode=0, stdout="", stderr="")
                    res = self.ws.execute_bash("echo hi")
        self.assertEqual(res["status"], "ok")


class ReportingTest(unittest.TestCase):
    """`network_isolated` has to be on every exit path.

    The judge and the scorecards read these dicts. A path that omits the key
    reads as "not isolated" to some consumers and as a KeyError to others;
    either way the record of what the sandbox did becomes unreliable exactly
    when something went wrong.
    """

    def setUp(self):
        self.ws = AgentWorkspace("test_report", base_dir="/tmp/hae_test_iso")

    def _run(self, **kw):
        with mock.patch.object(AgentWorkspace, "_netns_available",
                               return_value=True):
            with mock.patch("subprocess.run", **kw) as run:
                return self.ws.execute_bash("echo hi")

    def test_ok_path(self):
        res = self._run(return_value=mock.Mock(
            returncode=0, stdout="hi", stderr=""))
        self.assertEqual(res["status"], "ok")
        self.assertIn("network_isolated", res)

    def test_failed_path(self):
        res = self._run(return_value=mock.Mock(
            returncode=1, stdout="", stderr="boom"))
        self.assertEqual(res["status"], "failed")
        self.assertIn("network_isolated", res)

    def test_timeout_path(self):
        import subprocess as sp
        res = self._run(side_effect=sp.TimeoutExpired(cmd="x", timeout=1))
        self.assertEqual(res["status"], "timeout")
        self.assertIn("network_isolated", res)

    def test_error_path(self):
        res = self._run(side_effect=OSError("nope"))
        self.assertEqual(res["status"], "error")
        self.assertIn("network_isolated", res)


class LiveIsolationTest(unittest.TestCase):
    """End to end, where the host allows it.

    Skipped rather than failed on hosts without user namespaces, so the suite
    stays runnable on a laptop -- but the in-cluster image runs it for real,
    which is the environment the claim is actually about.
    """

    def setUp(self):
        AgentWorkspace._NETNS_SUPPORTED = None
        if not AgentWorkspace._netns_available():
            self.skipTest("no rootless network namespaces on this host")
        self.ws = AgentWorkspace("test_live", base_dir="/tmp/hae_test_iso")

    def test_command_still_works(self):
        res = self.ws.execute_bash("python3 -c 'print(1+1)'")
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["stdout"].strip(), "2")
        self.assertTrue(res["network_isolated"])

    def test_metadata_server_is_unreachable(self):
        """The finding that started this, inverted into a regression test."""
        res = self.ws.execute_bash(
            "python3 -c \""
            "import urllib.request as u;"
            "print(u.urlopen('http://169.254.169.254/', timeout=4).read())\"",
            timeout=20)
        self.assertNotEqual(res["status"], "ok")
        self.assertNotIn("access_token", res["stdout"])

    def test_loopback_still_binds(self):
        """Isolation that broke local sockets would break test suites that
        stand up a server, which is a legitimate thing for agent code to do."""
        res = self.ws.execute_bash(
            "python3 -c \""
            "import socket;s=socket.socket();s.bind(('127.0.0.1',0));"
            "print('bound',s.getsockname()[1]>0)\"")
        self.assertEqual(res["status"], "ok")
        self.assertIn("bound True", res["stdout"])


if __name__ == "__main__":
    unittest.main()
