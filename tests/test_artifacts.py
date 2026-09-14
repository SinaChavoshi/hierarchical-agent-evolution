"""Tests for canonical artifact accounting.

Guards the invariant that only genuinely agent-authored files are counted as
delivered artifacts. Regressions here silently inflate every published metric.
"""

import os
import shutil
import tempfile
import unittest

from src.artifacts import (
    count_source_files,
    filter_bundle,
    is_generated_path,
    is_malformed_path,
    partition_bundle,
    sanitize_path,
)
from src.sandbox_env import AgentWorkspace


class TestArtifactClassification(unittest.TestCase):
    def test_generated_paths_are_excluded(self):
        for path in [
            ".pytest_cache/CACHEDIR.TAG",
            ".pytest_cache/v/cache/nodeids",
            "tests/__pycache__/test_a.cpython-311.pyc",
            "venv/lib/python3.11/site-packages/requests/api.py",
            ".venv/bin/activate",
            ".git/HEAD",
            "src/agent_org.egg-info/PKG-INFO",
            "node_modules/left-pad/index.js",
        ]:
            self.assertTrue(is_generated_path(path), path)

    def test_authored_paths_are_kept(self):
        for path in [
            "pyproject.toml",
            "src/agent_org/core.py",
            "tests/test_routing.py",
            "README.md",
        ]:
            self.assertFalse(is_generated_path(path), path)
            self.assertFalse(is_malformed_path(path), path)

    def test_markdown_contaminated_paths_are_malformed(self):
        for path in ["src/routing.py**", "`README.md`", "  tests/a.py", "notes."]:
            self.assertTrue(is_malformed_path(path), path)

    def test_sanitize_recovers_intended_path(self):
        self.assertEqual(sanitize_path("src/routing.py**"), "src/routing.py")
        self.assertEqual(sanitize_path("**src/core.py**"), "src/core.py")
        self.assertEqual(sanitize_path(" `a/b.py` "), "a/b.py")
        self.assertEqual(sanitize_path("clean/path.py"), "clean/path.py")

    def test_partition_and_counts(self):
        bundle = {
            "pyproject.toml": "[project]",
            "src/core.py": "x = 1",
            "src/core.py**": "x = 1",
            ".pytest_cache/CACHEDIR.TAG": "sig",
            "tests/test_core.py": "def test_x(): pass",
        }
        authored, generated, malformed = partition_bundle(bundle)
        self.assertEqual(set(authored), {"pyproject.toml", "src/core.py", "tests/test_core.py"})
        self.assertEqual(set(generated), {".pytest_cache/CACHEDIR.TAG"})
        self.assertEqual(set(malformed), {"src/core.py**"})
        self.assertEqual(len(filter_bundle(bundle)), 3)
        self.assertEqual(count_source_files(bundle), 2)


class TestWorkspaceArtifactAccounting(unittest.TestCase):
    """End-to-end: a real workspace must not report generated files as authored."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hae_artifact_test_")
        self.ws = AgentWorkspace(company_id="artifact_test", base_dir=self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_markdown_path_is_sanitized_on_write(self):
        res = self.ws.write_file("src/routing.py**", "def route(): return 1\n")
        self.assertEqual(res["status"], "ok")
        paths = {f["path"] for f in self.ws.list_files()}
        self.assertIn(os.path.join("src", "routing.py"), paths)
        self.assertNotIn(os.path.join("src", "routing.py**"), paths)

    def test_generated_dirs_excluded_from_bundle(self):
        self.ws.write_file("src/core.py", "VALUE = 1\n")
        self.ws.write_file("pyproject.toml", "[project]\nname='x'\n")
        # Simulate pytest and interpreter byproducts.
        self.ws.write_file(".pytest_cache/CACHEDIR.TAG", "Signature: 8a477f5\n")
        self.ws.write_file("src/__pycache__/core.cpython-311.pyc", "\x00\x00")
        self.ws.write_file("src/x.egg-info/PKG-INFO", "Name: x\n")

        bundle = self.ws.export_bundle()
        self.assertEqual(set(bundle), {"src/core.py", "pyproject.toml"})
        self.assertEqual(len(filter_bundle(bundle)), 2)


if __name__ == "__main__":
    unittest.main()
