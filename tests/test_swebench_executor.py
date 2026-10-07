"""Tests for the SWE-bench executor boundary (local on a temp git repo; docker/kubectl by argv)."""

import os
import subprocess
import sys
import tempfile
import unittest
from typing import List

sys.path.insert(0, ".")

from hae.swebench.executor import (  # noqa: E402
    DockerExecutor, ExecResult, KubectlExecutor, LocalExecutor, SCRATCH_DIR, shell_join,
)


def _git(root: str, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True).stdout


def make_repo(root: str) -> None:
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    os.makedirs(os.path.join(root, "pkg"))
    with open(os.path.join(root, "pkg", "__init__.py"), "w") as fh:
        fh.write("")
    with open(os.path.join(root, "pkg", "mod.py"), "w") as fh:
        fh.write("def f(x):\n    return x + 1\n")
    with open(os.path.join(root, ".gitignore"), "w") as fh:
        fh.write("*.pyc\n__pycache__/\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")


class TestLocalExecutor(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        make_repo(self.root)
        self.ex = LocalExecutor(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_run_in_root_and_subdir_with_env(self):
        res = self.ex.run([sys.executable, "-c", "import os; print(os.getcwd()); print(os.environ.get('HAE_X'))"],
                          env={"HAE_X": "1"}, timeout_s=30)
        self.assertEqual(res.rc, 0)
        self.assertEqual(os.path.realpath(res.stdout.splitlines()[0]), os.path.realpath(self.root))
        self.assertEqual(res.stdout.splitlines()[-1], "1")
        sub = self.ex.run([sys.executable, "-c", "import os; print(os.path.basename(os.getcwd()))"], cwd="pkg")
        self.assertEqual(sub.stdout.strip(), "pkg")

    def test_python_defaults_to_interpreter(self):
        self.assertEqual(self.ex.python, sys.executable)

    def test_timeout_is_reported_not_raised(self):
        res = self.ex.run([sys.executable, "-c", "import time; time.sleep(5)"], timeout_s=0.5)
        self.assertTrue(res.timed_out)
        self.assertEqual(res.rc, -1)
        self.assertIn("timed out", res.stderr)

    def test_missing_binary_is_reported_not_raised(self):
        res = self.ex.run(["/nonexistent/binary/xyz"], timeout_s=5)
        self.assertEqual(res.rc, -1)
        self.assertFalse(res.timed_out)
        self.assertIn("Error", res.stderr)

    def test_stdin_is_delivered(self):
        res = self.ex.run([sys.executable, "-c", "import sys; print(sys.stdin.read().upper())"], stdin="abc")
        self.assertEqual(res.stdout.strip(), "ABC")

    def test_file_operations(self):
        self.assertTrue(self.ex.exists("pkg/mod.py"))
        self.assertFalse(self.ex.exists("pkg/nope.py"))
        self.ex.write_file("pkg/new/deep.py", "x = 1\n")
        self.assertEqual(self.ex.read_file("pkg/new/deep.py"), "x = 1\n")
        self.assertIn("deep.py", self.ex.list_dir("pkg/new"))
        self.assertEqual(self.ex.list_dir("does/not/exist"), [])
        self.assertEqual(self.ex.abspath("/abs/x"), "/abs/x")
        self.assertEqual(self.ex.abspath("a/../b"), os.path.join(self.root, "b"))

    def test_diff_includes_edits_and_new_files_but_not_scratch(self):
        self.ex.write_file("pkg/mod.py", "def f(x):\n    return x + 2\n")
        self.ex.write_file("pkg/extra.py", "y = 2\n")
        self.ex.write_file(f"{SCRATCH_DIR}/probe_h1.py", "print('probe')\n")
        self.ex.write_file("notes/scratch.txt", "ignore me\n")
        diff = self.ex.diff(exclude=("notes/",))
        self.assertIn("pkg/mod.py", diff)
        self.assertIn("+    return x + 2", diff)
        self.assertIn("pkg/extra.py", diff)
        self.assertIn("new file mode", diff)
        self.assertNotIn("probe_h1", diff)
        self.assertNotIn("scratch.txt", diff)
        self.assertEqual(self.ex.changed_files(exclude=("notes/",)), ["pkg/extra.py", "pkg/mod.py"])
        # The diff applies cleanly to a fresh checkout of the same commit.
        with tempfile.TemporaryDirectory() as fresh:
            subprocess.run(["git", "clone", "-q", self.root, fresh], check=True, capture_output=True)
            patch = os.path.join(fresh, "pred.diff")
            with open(patch, "w") as fh:
                fh.write(diff)
            chk = subprocess.run(["git", "apply", "--check", patch], cwd=fresh, capture_output=True, text=True)
            self.assertEqual(chk.returncode, 0, chk.stderr)

    def test_diff_empty_when_clean(self):
        self.assertEqual(self.ex.diff(), "")
        self.assertEqual(self.ex.changed_files(), [])


class FakeRunner:
    """Records host argv and answers from a queue."""

    def __init__(self, answers=None):
        self.calls: List[dict] = []
        self.answers = list(answers or [])

    def __call__(self, argv, stdin, timeout_s):
        self.calls.append({"argv": list(argv), "stdin": stdin, "timeout_s": timeout_s})
        return self.answers.pop(0) if self.answers else ExecResult(rc=0, stdout="")


class TestDockerExecutor(unittest.TestCase):
    def test_run_argv_with_cwd_env_and_timeout_wrapper(self):
        fr = FakeRunner([ExecResult(rc=0, stdout="hello")])
        ex = DockerExecutor("hae_x", runner=fr, use_timeout=True)
        res = ex.run(["python", "-c", "print(1)"], cwd="sub", timeout_s=20, env={"PYTHONPATH": "/testbed", "A": "b"})
        self.assertEqual(res.stdout, "hello")
        self.assertEqual(fr.calls[0]["argv"], [
            "docker", "exec", "-w", "/testbed/sub", "-e", "A=b", "-e", "PYTHONPATH=/testbed", "hae_x",
            "timeout", "-k", "5", "20", "python", "-c", "print(1)"])
        self.assertGreater(fr.calls[0]["timeout_s"], 20)
        self.assertEqual(ex.python, "/opt/miniconda3/envs/testbed/bin/python")
        self.assertEqual(ex.root, "/testbed")

    def test_no_timeout_wrapper_when_absent(self):
        fr = FakeRunner()
        ex = DockerExecutor("c", runner=fr, use_timeout=False)
        ex.run(["ls"])
        self.assertEqual(fr.calls[0]["argv"], ["docker", "exec", "-w", "/testbed", "c", "ls"])

    def test_timeout_is_probed_once(self):
        fr = FakeRunner([ExecResult(rc=0, stdout="/usr/bin/timeout\n")])
        ex = DockerExecutor("c", runner=fr)
        ex.run(["ls"])
        ex.run(["ls"])
        self.assertEqual(len(fr.calls), 3)
        self.assertEqual(fr.calls[0]["argv"][-3:], ["sh", "-c", "command -v timeout"])
        self.assertEqual(fr.calls[1]["argv"][-5:], ["timeout", "-k", "5", "60", "ls"])
        self.assertEqual(fr.calls[2]["argv"][-5:], ["timeout", "-k", "5", "60", "ls"])

    def test_rc_124_from_timeout_marks_timed_out(self):
        fr = FakeRunner([ExecResult(rc=124, stdout="partial")])
        ex = DockerExecutor("c", runner=fr, use_timeout=True)
        res = ex.run(["sleep", "99"], timeout_s=1)
        self.assertTrue(res.timed_out)
        self.assertIn("timed out", res.stderr)
        fr2 = FakeRunner([ExecResult(rc=124)])
        res2 = DockerExecutor("c", runner=fr2, use_timeout=False).run(["x"])
        self.assertFalse(res2.timed_out)  # no wrapper -> 124 is the command's own code

    def test_write_file_streams_body_over_stdin(self):
        fr = FakeRunner()
        ex = DockerExecutor("c", runner=fr, use_timeout=False)
        ex.write_file("pkg/mod.py", "body\n")
        call = fr.calls[0]
        self.assertEqual(call["stdin"], "body\n")
        self.assertEqual(call["argv"][:4], ["docker", "exec", "-i", "-w"])
        self.assertEqual(call["argv"][-1], "/testbed/pkg/mod.py")
        self.assertIn("cat > \"$1\"", call["argv"][-3])
        ex.write_file("empty.txt", "")
        self.assertIn("-i", fr.calls[1]["argv"])

    def test_read_exists_list(self):
        fr = FakeRunner([ExecResult(rc=0, stdout="src"), ExecResult(rc=1), ExecResult(rc=0, stdout="b\na\n"),
                         ExecResult(rc=1, stderr="No such file")])
        ex = DockerExecutor("c", runner=fr, use_timeout=False)
        self.assertEqual(ex.read_file("/abs/file.py"), "src")
        self.assertEqual(fr.calls[0]["argv"][-3:], ["cat", "--", "/abs/file.py"])
        self.assertFalse(ex.exists("x"))
        self.assertEqual(fr.calls[1]["argv"][-3:], ["test", "-e", "/testbed/x"])
        self.assertEqual(ex.list_dir("d"), ["a", "b"])
        with self.assertRaises(FileNotFoundError):
            ex.read_file("missing")

    def test_diff_uses_git_in_root(self):
        fr = FakeRunner([ExecResult(rc=0, stdout="new.py\n.hae/probe.py\n"), ExecResult(rc=0),
                         ExecResult(rc=0, stdout="diff --git a/x b/x\n")])
        ex = DockerExecutor("c", runner=fr, use_timeout=False)
        out = ex.diff(exclude=("scratch/",))
        self.assertEqual(out, "diff --git a/x b/x\n")
        self.assertEqual(fr.calls[0]["argv"][-4:], ["git", "ls-files", "--others", "--exclude-standard"])
        self.assertEqual(fr.calls[1]["argv"][-5:], ["git", "add", "-N", "--", "new.py"])
        self.assertEqual(fr.calls[2]["argv"][-8:], ["git", "diff", "--no-color", "--no-ext-diff", "--", ".",
                                                    ":(exclude).hae", ":(exclude)scratch"])


class TestKubectlExecutor(unittest.TestCase):
    def test_run_argv(self):
        fr = FakeRunner()
        ex = KubectlExecutor("pod-1", container="task", namespace="hae", runner=fr, use_timeout=True)
        ex.run(["python", "x.py"], cwd="/testbed", timeout_s=7, env={"PYTHONPATH": "/testbed"})
        self.assertEqual(fr.calls[0]["argv"], [
            "kubectl", "exec", "-n", "hae", "pod-1", "-c", "task", "--",
            "env", "PYTHONPATH=/testbed", "sh", "-c", 'cd "$1" && shift && exec "$@"', "sh", "/testbed",
            "timeout", "-k", "5", "7", "python", "x.py"])

    def test_write_file_uses_interactive(self):
        fr = FakeRunner()
        ex = KubectlExecutor("pod-1", runner=fr, use_timeout=False)
        ex.write_file("a.py", "z")
        self.assertEqual(fr.calls[0]["argv"][:3], ["kubectl", "exec", "-i"])
        self.assertEqual(fr.calls[0]["stdin"], "z")
        self.assertEqual(fr.calls[0]["argv"][-1], "/testbed/a.py")
        self.assertIn("kubectl:default/pod-1", ex.describe())


class TestHelpers(unittest.TestCase):
    def test_shell_join_quotes(self):
        self.assertEqual(shell_join(["sh", "-c", "echo hi there"]), "sh -c 'echo hi there'")


if __name__ == "__main__":
    unittest.main()
