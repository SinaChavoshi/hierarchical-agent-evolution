"""V16 Phase 0: infrastructure fixes (pod prewarming, job interleaving, infra sync, retries)."""

import os
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
from unittest import mock

from hae.evolution.pod_pool import PodPool, interleave_jobs
from hae.evolution.workspace import INFRA_SYNC_FILES, WorktreeManager
from hae.infra import llm
from hae.swebench.executor import ExecResult, KubectlExecutor


class _FakeCluster:
    def __init__(self, fail_names=()):
        self.created, self.deleted = [], []
        self.fail_names = set(fail_names)
        self._n = 0
        self._lock = threading.Lock()

    def name(self, iid):
        with self._lock:
            self._n += 1
            return f"pod{self._n}-{iid}"

    def create(self, pod, iid):
        with self._lock:
            self.created.append(pod)
        return "boom" if iid in self.fail_names else None

    def delete(self, pod):
        with self._lock:
            self.deleted.append(pod)


class PodPoolTest(unittest.TestCase):
    def test_prewarmed_pods_are_single_use_and_all_deleted(self):
        c = _FakeCluster()
        pool = PodPool(create=c.create, delete=c.delete, name=c.name, lookahead=2)
        queue = ["a", "b", "c", "d", "e"]
        pool.set_queue(queue)
        used = []
        for iid in queue:
            pod, err = pool.acquire(iid)
            self.assertIsNone(err)
            self.assertTrue(pod.endswith(iid))
            used.append(pod)
            pool.release(pod)
        pool.close()
        self.assertEqual(len(set(used)), len(used))  # never reused
        self.assertEqual(sorted(c.created), sorted(c.deleted))  # nothing leaks
        self.assertGreaterEqual(pool.stats["warm_hits"], 4)

    def test_failed_prewarm_falls_back_to_cold_start(self):
        c = _FakeCluster(fail_names={"x"})
        pool = PodPool(create=c.create, delete=c.delete, name=c.name, lookahead=1)
        pool.set_queue(["x"])
        time.sleep(0.05)
        pod, err = pool.acquire("x")
        self.assertEqual(err, "boom")  # cold retry also fails in this fake
        self.assertEqual(pool.stats["prewarm_errors"], 1)
        pool.release(pod)
        pool.close()
        self.assertEqual(sorted(c.created), sorted(c.deleted))

    def test_stale_prewarms_are_discarded(self):
        c = _FakeCluster()
        pool = PodPool(create=c.create, delete=c.delete, name=c.name, lookahead=2)
        pool.set_queue(["a", "b"])
        # Jobs run out of order: "b" first, then "a" -> nothing stale. Then an unexpected job.
        for iid in ("b", "a", "z"):
            pod, _ = pool.acquire(iid)
            pool.release(pod)
        pool.close()
        time.sleep(0.05)
        self.assertEqual(sorted(c.created), sorted(c.deleted))

    def test_zero_lookahead_is_plain_create_delete(self):
        c = _FakeCluster()
        pool = PodPool(create=c.create, delete=c.delete, name=c.name, lookahead=0)
        pool.set_queue(["a"])
        pod, err = pool.acquire("a")
        pool.release(pod)
        pool.close()
        self.assertEqual(c.created, [pod])
        self.assertEqual(pool.stats["cold_starts"], 1)


class InterleaveTest(unittest.TestCase):
    def test_full_grid_round_robin(self):
        tasks = ["t0", "t1", "t2"]
        out = interleave_jobs(["g0", "g1"], {"g0": tasks, "g1": tasks}, tasks)
        self.assertEqual(out[:2], [("g0", "t0"), ("g1", "t1")])
        self.assertEqual(sorted(out), sorted((g, t) for g in ("g0", "g1") for t in tasks))
        # Each genome's jobs are spread evenly over the run.
        pos = {g: [i for i, (gg, _) in enumerate(out) if gg == g] for g in ("g0", "g1")}
        self.assertEqual(pos["g0"], [0, 2, 4])
        self.assertEqual(pos["g1"], [1, 3, 5])

    def test_partial_todo_and_unknown_tasks(self):
        out = interleave_jobs(["g0", "g1"], {"g0": ["t1"], "g1": ["t0", "extra"]}, ["t0", "t1"])
        self.assertEqual(sorted(out), [("g0", "t1"), ("g1", "extra"), ("g1", "t0")])
        self.assertEqual(len(out), 3)


class InfraSyncTest(unittest.TestCase):
    def test_materialize_copies_infra_files_into_cached_worktree(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = os.path.join(tmp, "repo")
            os.makedirs(os.path.join(repo, "hae", "infra"))
            os.makedirs(os.path.join(repo, "hae", "swebench"))
            for rel in INFRA_SYNC_FILES[:2]:
                with open(os.path.join(repo, rel), "w") as fh:
                    fh.write("v1\n")
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
            subprocess.run(["git", "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "x"],
                           cwd=repo, check=True)
            wt = WorktreeManager(repo, "HEAD", os.path.join(tmp, "wts"))
            from hae.evolution.genome import HarnessGenome
            g = HarnessGenome(genome_id="g")
            path, err = wt.materialize(g)
            self.assertIsNone(err)
            with open(os.path.join(repo, INFRA_SYNC_FILES[0]), "w") as fh:
                fh.write("v2\n")
            path2, err2 = wt.materialize(g)  # cached worktree
            self.assertEqual(path, path2)
            self.assertIsNone(err2)
            with open(os.path.join(path, INFRA_SYNC_FILES[0])) as fh:
                self.assertEqual(fh.read(), "v2\n")
            self.assertTrue(wt.infra_commit().endswith("+dirty"))


class KubectlRetryTest(unittest.TestCase):
    def _exe(self):
        exe = KubectlExecutor.__new__(KubectlExecutor)
        exe.transport_retry_s = 5.0
        return exe

    def test_retries_transport_failure_then_succeeds(self):
        exe = self._exe()
        seq = [ExecResult(rc=1, stdout="", stderr="Unable to connect to the server: EOF"),
               ExecResult(rc=0, stdout="ok", stderr="")]
        with mock.patch("hae.swebench.executor._RemoteExecutor.run", side_effect=seq) as m, \
                mock.patch("hae.swebench.executor.time.sleep"):
            res = exe.run(["true"])
        self.assertEqual(res.rc, 0)
        self.assertEqual(m.call_count, 2)

    def test_does_not_retry_command_failures(self):
        exe = self._exe()
        with mock.patch("hae.swebench.executor._RemoteExecutor.run",
                        return_value=ExecResult(rc=1, stdout="", stderr="AssertionError")) as m:
            res = exe.run(["false"])
        self.assertEqual(res.rc, 1)
        self.assertEqual(m.call_count, 1)


class LlmConnectionRetryTest(unittest.TestCase):
    def test_connection_errors_classified(self):
        import socket
        self.assertTrue(llm._is_connection_error(urllib.error.URLError(ConnectionRefusedError())))
        self.assertTrue(llm._is_connection_error(ConnectionResetError()))
        self.assertFalse(llm._is_connection_error(urllib.error.URLError(socket.timeout())))
        self.assertFalse(llm._is_connection_error(ValueError("bad json")))


if __name__ == "__main__":
    unittest.main()
