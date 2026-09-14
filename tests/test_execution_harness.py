"""Tests for the execution-grounded verification harness.

These assert the behaviours that distinguish the harness from the heuristic
verifier it replaces: prose cannot pass a gate, broken code cannot pass a gate,
and a gate that could not be evaluated is never scored as a pass.
"""

import unittest

from hae.evaluation.harness import ExecutionHarness, FAILED, PASSED, SKIPPED

BASE = {
    "pyproject.toml": '[project]\nname = "demo"\nversion = "0.1.0"\n',
    "src/demo/__init__.py": "",
    "src/demo/core.py": "def add(a, b):\n    return a + b\n",
    "tests/test_core.py": "from demo.core import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n",
}


def variant(**overrides):
    b = dict(BASE)
    b.update(overrides)
    return b


class TestExecutionHarness(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.h = ExecutionHarness(timeout_s=60)

    def status(self, bundle, gate):
        return self.h.verify_bundle(bundle).gate(gate).status

    # -- syntax ---------------------------------------------------------- #

    def test_syntax_gate_catches_broken_code(self):
        bad = variant(**{"src/demo/core.py": "def add(a, b)\n    return a + b\n"})
        self.assertEqual(self.status(bad, "syntax"), FAILED)

    def test_syntax_gate_passes_valid_code(self):
        self.assertEqual(self.status(BASE, "syntax"), PASSED)

    # -- telemetry: the headline regression ------------------------------ #

    def test_prose_mentioning_opentelemetry_does_not_pass(self):
        """The old gate substring-searched text that included the CEO's essay."""
        prose = variant(**{
            "src/demo/core.py":
                "# Our platform uses OpenTelemetry for distributed tracing.\n"
                "# See the opentelemetry docs for details.\n"
                "def add(a, b):\n    return a + b\n"
        })
        self.assertEqual(self.status(prose, "telemetry"), FAILED)

    def test_real_instrumentation_passes(self):
        instrumented = variant(**{
            "src/demo/core.py":
                "from opentelemetry import trace\n"
                "tracer = trace.get_tracer(__name__)\n\n"
                "def add(a, b):\n"
                "    with tracer.start_as_current_span('add'):\n"
                "        return a + b\n"
        })
        self.assertEqual(self.status(instrumented, "telemetry"), PASSED)

    def test_import_without_instrumentation_does_not_pass(self):
        """Importing the package but never creating a span is not observability."""
        shallow = variant(**{
            "src/demo/core.py":
                "import opentelemetry\n\ndef add(a, b):\n    return a + b\n"
        })
        self.assertEqual(self.status(shallow, "telemetry"), FAILED)

    # -- tests gate ------------------------------------------------------ #

    def test_failing_assertion_is_detected(self):
        failing = variant(**{
            "tests/test_core.py":
                "from demo.core import add\n\n\ndef test_add():\n    assert add(1, 2) == 999\n"
        })
        self.assertEqual(self.status(failing, "tests"), FAILED)

    def test_pytest_style_functions_are_collected(self):
        """unittest discover silently reports 'Ran 0 tests' for this style."""
        self.assertEqual(self.status(BASE, "tests"), PASSED)

    def test_unittest_style_classes_are_collected(self):
        ut = variant(**{
            "tests/test_core.py":
                "import unittest\nfrom demo.core import add\n\n\n"
                "class T(unittest.TestCase):\n"
                "    def test_add(self):\n        self.assertEqual(add(1, 2), 3)\n"
        })
        self.assertEqual(self.status(ut, "tests"), PASSED)

    def test_missing_tests_fail_rather_than_skip(self):
        no_tests = {k: v for k, v in BASE.items() if "test" not in k}
        self.assertEqual(self.status(no_tests, "tests"), FAILED)

    # -- build ----------------------------------------------------------- #

    def test_missing_manifest_fails_build(self):
        no_manifest = {k: v for k, v in BASE.items() if k != "pyproject.toml"}
        self.assertEqual(self.status(no_manifest, "build"), FAILED)

    def test_malformed_manifest_fails_build(self):
        bad = variant(**{"pyproject.toml": "this is not = valid [toml\n"})
        self.assertEqual(self.status(bad, "build"), FAILED)

    def test_filename_alone_does_not_pass_build(self):
        """The old gate passed on the mere presence of these filenames."""
        shell = {
            "pyproject.toml": "",          # present but declares nothing
            "src/demo/core.py": "x = 1\n",
            "src/demo/runtime.py": "y = 2\n",
        }
        self.assertEqual(self.status(shell, "build"), FAILED)

    # -- unevaluable gates ----------------------------------------------- #

    def test_uninstalled_dependency_skips_rather_than_fails(self):
        """An absent third-party package is not a defect in the authored code."""
        dep = variant(**{
            "src/demo/core.py":
                "from opentelemetry import trace\n"
                "tracer = trace.get_tracer(__name__)\n\n"
                "def add(a, b):\n"
                "    with tracer.start_as_current_span('add'):\n"
                "        return a + b\n"
        })
        report = self.h.verify_bundle(dep)
        self.assertEqual(report.gate("smoke").status, SKIPPED)
        # A skipped gate must not contribute penalty.
        self.assertNotIn(report.gate("smoke"), report.passed_gates)
        self.assertFalse(report.gate("smoke").evaluated)

    def test_penalty_counts_only_real_failures(self):
        report = self.h.verify_bundle(BASE)
        failures = sum(1 for g in report.gates if g.status == FAILED)
        self.assertAlmostEqual(report.score_penalty, failures * 6.25)

    def test_empty_workspace_fails_everything_evaluable(self):
        report = self.h.verify_bundle({})
        self.assertEqual(report.authored_files, 0)
        self.assertEqual(report.gate("syntax").status, FAILED)
        self.assertEqual(report.gate("build").status, FAILED)

    def test_generated_files_are_not_counted(self):
        polluted = variant(**{
            ".pytest_cache/CACHEDIR.TAG": "Signature: 8a477f5\n",
            "src/__pycache__/core.cpython-311.pyc": "\x00",
        })
        report = self.h.verify_bundle(polluted)
        self.assertEqual(report.authored_files, len(BASE))


if __name__ == "__main__":
    unittest.main()
