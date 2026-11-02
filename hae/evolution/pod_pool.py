"""Pre-warmed, single-use SWE-bench sandbox pods.

Pod start-up (schedule + image + Ready) is taken off the critical path: while a job runs,
pods for the next jobs in the queue are created in the background. Each pod is still used
for exactly one job and deleted afterwards. Pods are not reused across jobs, because an
agent can change state outside `/testbed` (pip installs, files in /tmp) that a git reset
does not undo, and that would leak between genomes.
"""

from __future__ import annotations

import atexit
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Callable, Dict, List, Optional, Sequence, Tuple

# create(pod_name, instance_id) -> error or None; delete(pod_name) -> None; name(instance_id) -> pod_name
CreateFn = Callable[[str, str], Optional[str]]
DeleteFn = Callable[[str], None]
NameFn = Callable[[str], str]


class PodPool:
    def __init__(self, *, create: CreateFn, delete: DeleteFn, name: NameFn, lookahead: int = 4) -> None:
        self._create = create
        self._delete = delete
        self._name = name
        self.lookahead = max(0, int(lookahead))
        self._lock = threading.Lock()
        self._queue: List[str] = []          # upcoming instance ids, in job order
        self._next = 0                        # index into _queue of the next job to prewarm
        self._started = 0                     # number of acquire() calls so far
        self._warm: Dict[str, List[Tuple[str, Future]]] = {}  # instance_id -> [(pod, future)]
        self._outstanding = 0
        self._exec = ThreadPoolExecutor(max_workers=max(1, self.lookahead), thread_name_prefix="podpool")
        self._closed = False
        self.stats = {"warm_hits": 0, "cold_starts": 0, "prewarm_errors": 0}
        atexit.register(self.close)

    # ------------------------------------------------------------------ #
    def set_queue(self, instance_ids: Sequence[str]) -> None:
        """Declare the order in which jobs will call `acquire` (one call per job)."""
        with self._lock:
            self._queue = list(instance_ids)
            self._next = 0
            self._started = 0
        self._top_up()

    def _top_up(self) -> None:
        stale: List[Tuple[str, Future]] = []
        with self._lock:
            if self._closed:
                return
            # Never prewarm for jobs that have already started.
            self._next = max(self._next, self._started)
            remaining = set(self._queue[self._started:])
            for iid in [i for i, lst in self._warm.items() if lst and i not in remaining]:
                stale += self._warm.pop(iid)
            self._outstanding -= len(stale)
            while self._outstanding < self.lookahead and self._next < len(self._queue):
                iid = self._queue[self._next]
                self._next += 1
                self._outstanding += 1
                pod = self._name(iid)
                self._warm.setdefault(iid, []).append((pod, self._exec.submit(self._create, pod, iid)))
        for pod, fut in stale:
            threading.Thread(target=self._discard, args=(pod, fut), daemon=True).start()

    def _discard(self, pod: str, fut: Future) -> None:
        try:
            fut.result(timeout=1300)
        except Exception:
            pass
        self._delete(pod)

    def acquire(self, instance_id: str) -> Tuple[str, Optional[str]]:
        """Return `(pod, error)`: a pre-warmed pod for `instance_id` if one exists, else a new one."""
        entry = None
        with self._lock:
            self._started += 1
            lst = self._warm.get(instance_id)
            if lst:
                entry = lst.pop(0)
                self._outstanding -= 1
        self._top_up()
        if entry is not None:
            pod, fut = entry
            err = fut.result()
            if err is None:
                with self._lock:
                    self.stats["warm_hits"] += 1
                return pod, None
            with self._lock:
                self.stats["prewarm_errors"] += 1
            self._delete(pod)
        with self._lock:
            self.stats["cold_starts"] += 1
        pod = self._name(instance_id)
        return pod, self._create(pod, instance_id)

    def release(self, pod: str) -> None:
        self._delete(pod)

    def drain(self) -> None:
        """Delete every prewarmed pod that was not used (end of an `evaluate` call)."""
        with self._lock:
            leftovers = [e for lst in self._warm.values() for e in lst]
            self._warm.clear()
            self._outstanding = 0
            self._queue, self._next, self._started = [], 0, 0
        for pod, fut in leftovers:
            threading.Thread(target=self._discard, args=(pod, fut), daemon=True).start()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            leftovers = [e for lst in self._warm.values() for e in lst]
            self._warm.clear()
        for pod, fut in leftovers:
            try:
                fut.result(timeout=1300)
            except Exception:
                pass
            self._delete(pod)
        self._exec.shutdown(wait=False)


def interleave_jobs(genomes: Sequence[str], tasks_by_genome: Dict[str, Sequence[str]],
                    task_order: Sequence[str]) -> List[Tuple[str, str]]:
    """Order (genome, task) jobs round-robin so every genome sees the same load over time.

    Row i assigns genome j the task at position (i + j) mod n of `task_order`, skipping tasks the
    genome does not need. With a full grid every genome runs every task exactly once, and no
    genome's jobs are bunched at the start or end of the run.
    """
    n = len(task_order)
    todo = {g: set(tasks_by_genome.get(g, ())) for g in genomes}
    out: List[Tuple[str, str]] = []
    for i in range(n):
        for j, g in enumerate(genomes):
            t = task_order[(i + j) % n]
            if t in todo[g]:
                out.append((g, t))
                todo[g].discard(t)
    for g in genomes:  # tasks not in task_order
        out += [(g, t) for t in tasks_by_genome.get(g, ()) if t in todo[g]]
    return out
