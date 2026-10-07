"""Where SWE-bench commands run: the one boundary between the firm and a task repository.

The epistemic machinery (gatekeeper, synthesis, probes) was written for a
firm editing a copy of *this* repository on the local disk. A SWE-bench
instance lives somewhere else -- a prebuilt Docker image with the task
repository checked out at `/testbed` and its interpreter at
`/opt/miniconda3/envs/testbed/bin/python` -- and the host that runs the firm
may not even have Docker (the k8s evolution workers talk to a sidecar through
`kubectl exec`). Everything that touches the task repository therefore goes
through a `CommandExecutor`: run a command in a directory with a timeout,
read or write a file, test existence, list a directory, produce the git diff.
Three implementations, one contract:

  * `LocalExecutor`   -- the repository is a directory on this machine
                         (tests; a VM where the repo was cloned by hand).
  * `DockerExecutor`  -- `docker exec -w <cwd> <container> ...`; file writes
                         are streamed over stdin so no `docker cp` and no
                         shell-quoting of file bodies is needed.
  * `KubectlExecutor` -- the same shape over `kubectl exec -n ns pod -c c -- ...`.

Timeouts are enforced twice where possible: the host-side `subprocess` call
is bounded (so a wedged `docker exec` cannot hang the firm) **and** the
command is wrapped in coreutils `timeout` inside the container when it exists
(so the process actually dies there instead of running on after the host
gave up). The remote executors accept an injected `runner` so unit tests can
check the exact argv without Docker or a cluster.

Nothing here knows about the ledger, prompts, or grading; `hae.epistemic.
gatekeeper` imports this module, never the other way round.
"""

from __future__ import annotations

import os
import posixpath
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Sequence

# Seconds the host waits beyond the command's own timeout before giving up on
# the transport (docker/kubectl start-up, output flushing).
HOST_GRACE_S = 15.0
# How long `timeout` waits after SIGTERM before SIGKILL inside the container.
KILL_AFTER_S = 5
# Exit codes coreutils `timeout` uses for an expired command (TERM honoured /
# KILL needed).
TIMEOUT_EXIT_CODES = (124, 137)
# Scratch directory the gatekeeper writes probes to; never part of a prediction.
SCRATCH_DIR = ".hae"

_SCRUB_MARKERS = ("TOKEN", "KEY", "CREDENTIALS", "SECRET", "PASSWORD")


@dataclass
class ExecResult:
    """What came back from one command."""

    rc: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    elapsed_s: float = 0.0

    @property
    def ok(self) -> bool:
        return self.rc == 0 and not self.timed_out


HostRunner = Callable[[List[str], str, float], ExecResult]


def _scrubbed_environ() -> Dict[str, str]:
    return {k: v for k, v in os.environ.items() if not any(m in k.upper() for m in _SCRUB_MARKERS)}


def host_run(argv: Sequence[str], stdin: str = "", timeout_s: float = 60.0,
             cwd: Optional[str] = None, env: Optional[Mapping[str, str]] = None) -> ExecResult:
    """The one `subprocess.run` everything funnels through. Never raises for a failed command."""
    started = time.monotonic()
    try:
        res = subprocess.run(list(argv), cwd=cwd, input=stdin or "", capture_output=True,
                             text=True, encoding="utf-8", errors="replace",
                             timeout=max(0.1, float(timeout_s)), env=dict(env) if env is not None else None)
        return ExecResult(rc=int(res.returncode), stdout=res.stdout or "", stderr=res.stderr or "",
                          elapsed_s=time.monotonic() - started)
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        err = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        return ExecResult(rc=-1, stdout=out, stderr=(err + f"\n[host] timed out after {timeout_s:.0f}s").strip(),
                          timed_out=True, elapsed_s=time.monotonic() - started)
    except (OSError, ValueError) as exc:  # missing binary, bad argv
        return ExecResult(rc=-1, stdout="", stderr=f"{type(exc).__name__}: {exc}",
                          elapsed_s=time.monotonic() - started)


class CommandExecutor:
    """The contract. Subclasses implement `run`, `read_file`, `write_file`, `exists`, `list_dir`.

    Paths given to the file operations are relative to `root` unless absolute.
    `diff` is implemented once on top of `run` because it is plain git.
    """

    root: str
    python: str

    def run(self, argv: Sequence[str], cwd: Optional[str] = None, timeout_s: float = 60.0,
            env: Optional[Mapping[str, str]] = None, stdin: str = "") -> ExecResult:
        raise NotImplementedError

    def read_file(self, path: str) -> str:
        raise NotImplementedError

    def write_file(self, path: str, content: str) -> None:
        raise NotImplementedError

    def exists(self, path: str) -> bool:
        raise NotImplementedError

    def list_dir(self, path: str = ".") -> List[str]:
        raise NotImplementedError

    def describe(self) -> str:
        return f"{type(self).__name__}({self.root})"

    # -- shared helpers ------------------------------------------------- #

    def abspath(self, path: Optional[str]) -> str:
        """`path` resolved against the repository root (POSIX, the repo side is always Linux)."""
        if not path or path == ".":
            return self.root
        if posixpath.isabs(path):
            return posixpath.normpath(path)
        return posixpath.normpath(posixpath.join(self.root, path))

    def git(self, args: Sequence[str], timeout_s: float = 120.0) -> ExecResult:
        return self.run(["git", *args], cwd=self.root, timeout_s=timeout_s)

    def untracked_files(self, exclude: Iterable[str] = ()) -> List[str]:
        """Untracked, non-ignored files outside the scratch dir, `exclude` prefixes and bytecode caches."""
        res = self.git(["ls-files", "--others", "--exclude-standard"])
        if res.rc != 0:
            return []
        skip = tuple(_norm_prefix(p) for p in (SCRATCH_DIR, *exclude))
        out: List[str] = []
        for line in res.stdout.splitlines():
            rel = line.strip()
            if not rel or any(rel == s.rstrip("/") or rel.startswith(s) for s in skip):
                continue
            if "__pycache__" in rel.split("/") or rel.endswith((".pyc", ".pyo")):
                continue
            out.append(rel)
        return out

    def diff(self, exclude: Iterable[str] = ()) -> str:
        """`git diff` of the repository root, new files included, scratch and `exclude` left out.

        Untracked (non-ignored) files are registered with `git add -N`
        (intent-to-add) so they show up as creations in a plain `git diff`;
        nothing is staged, so the working tree and the prediction stay one
        and the same thing.
        """
        new = self.untracked_files(exclude)
        if new:
            self.git(["add", "-N", "--", *new])
        spec = [".", f":(exclude){SCRATCH_DIR}"] + [f":(exclude){_norm_prefix(p).rstrip('/')}" for p in exclude]
        res = self.git(["diff", "--no-color", "--no-ext-diff", "--", *spec])
        return res.stdout if res.rc == 0 else ""

    def changed_files(self, exclude: Iterable[str] = ()) -> List[str]:
        res = self.git(["diff", "--name-only", "--", "."])
        files = [l.strip() for l in res.stdout.splitlines() if l.strip()] if res.rc == 0 else []
        skip = tuple(_norm_prefix(p) for p in (SCRATCH_DIR, *exclude))
        files = [f for f in files if not any(f.startswith(s) for s in skip)]
        return sorted(set(files) | set(self.untracked_files(exclude)))


def _norm_prefix(p: str) -> str:
    p = p.replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    p = p.lstrip("/")
    return p if p.endswith("/") else p + "/"


# --------------------------------------------------------------------------- #
# Local
# --------------------------------------------------------------------------- #

class LocalExecutor(CommandExecutor):
    """The repository is a directory on this machine."""

    def __init__(self, root: str, python: Optional[str] = None,
                 base_env: Optional[Mapping[str, str]] = None) -> None:
        self.root = os.path.abspath(root)
        self.python = python or sys.executable
        self._base_env = dict(base_env) if base_env is not None else None

    def _env(self, env: Optional[Mapping[str, str]]) -> Dict[str, str]:
        merged = dict(self._base_env) if self._base_env is not None else _scrubbed_environ()
        merged.setdefault("PYTHONDONTWRITEBYTECODE", "1")
        merged.pop("PYTEST_ADDOPTS", None)
        if env:
            merged.update({str(k): str(v) for k, v in env.items()})
        return merged

    def run(self, argv: Sequence[str], cwd: Optional[str] = None, timeout_s: float = 60.0,
            env: Optional[Mapping[str, str]] = None, stdin: str = "") -> ExecResult:
        return host_run(argv, stdin=stdin, timeout_s=timeout_s, cwd=self.abspath(cwd), env=self._env(env))

    def read_file(self, path: str) -> str:
        with open(self.abspath(path), "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()

    def write_file(self, path: str, content: str) -> None:
        full = self.abspath(path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(content)

    def exists(self, path: str) -> bool:
        return os.path.exists(self.abspath(path))

    def list_dir(self, path: str = ".") -> List[str]:
        try:
            return sorted(os.listdir(self.abspath(path)))
        except OSError:
            return []


# --------------------------------------------------------------------------- #
# Remote (docker / kubectl)
# --------------------------------------------------------------------------- #

class _RemoteExecutor(CommandExecutor):
    """Shared plumbing: argv assembly, in-container `timeout`, stdin streaming for writes.

    `runner(host_argv, stdin, timeout_s) -> ExecResult` is the host-side
    transport; tests inject a fake, production uses `host_run`.
    `use_timeout` pins whether coreutils `timeout` exists in the container;
    None means "probe once, lazily".
    """

    def __init__(self, root: str, python: str, runner: Optional[HostRunner] = None,
                 use_timeout: Optional[bool] = None) -> None:
        self.root = root
        self.python = python
        self._runner: HostRunner = runner or (lambda argv, stdin, t: host_run(argv, stdin=stdin, timeout_s=t))
        self._use_timeout = use_timeout

    # subclasses: the transport prefix for a command in `cwd`, with/without stdin
    def _prefix(self, cwd: str, env: Mapping[str, str], interactive: bool) -> List[str]:
        raise NotImplementedError

    def _has_timeout(self) -> bool:
        if self._use_timeout is None:
            res = self._runner(self._prefix(self.root, {}, False) + ["sh", "-c", "command -v timeout"],
                               "", HOST_GRACE_S * 2)
            self._use_timeout = res.rc == 0 and bool(res.stdout.strip())
        return bool(self._use_timeout)

    def _wrap_timeout(self, argv: Sequence[str], timeout_s: float) -> List[str]:
        if self._has_timeout():
            return ["timeout", "-k", str(KILL_AFTER_S), str(int(max(1, round(timeout_s)))), *argv]
        return list(argv)

    def run(self, argv: Sequence[str], cwd: Optional[str] = None, timeout_s: float = 60.0,
            env: Optional[Mapping[str, str]] = None, stdin: str = "",
            interactive: Optional[bool] = None) -> ExecResult:
        wrapped = self._wrap_timeout(argv, timeout_s)
        attach_stdin = bool(stdin) if interactive is None else bool(interactive)
        host_argv = self._prefix(self.abspath(cwd), dict(env or {}), attach_stdin) + wrapped
        res = self._runner(host_argv, stdin, float(timeout_s) + HOST_GRACE_S + (KILL_AFTER_S if self._use_timeout else 0))
        if wrapped[:1] == ["timeout"] and list(argv[:1]) != ["timeout"] \
                and res.rc in TIMEOUT_EXIT_CODES and not res.timed_out:
            res.timed_out = True
            res.stderr = (res.stderr + f"\n[container] timed out after {timeout_s:.0f}s").strip()
        return res

    def read_file(self, path: str) -> str:
        res = self.run(["cat", "--", self.abspath(path)], timeout_s=60)
        if res.rc != 0:
            raise FileNotFoundError(f"{self.describe()}: cannot read {path}: {res.stderr.strip()[:200]}")
        return res.stdout

    def write_file(self, path: str, content: str) -> None:
        full = self.abspath(path)
        script = 'mkdir -p "$(dirname -- "$1")" && cat > "$1"'
        res = self.run(["sh", "-c", script, "sh", full], timeout_s=60, stdin=content, interactive=True)
        if res.rc != 0:
            raise OSError(f"{self.describe()}: cannot write {path}: {res.stderr.strip()[:200]}")

    def exists(self, path: str) -> bool:
        return self.run(["test", "-e", self.abspath(path)], timeout_s=30).rc == 0

    def list_dir(self, path: str = ".") -> List[str]:
        res = self.run(["ls", "-1A", "--", self.abspath(path)], timeout_s=30)
        return sorted(l for l in res.stdout.splitlines() if l.strip()) if res.rc == 0 else []


class DockerExecutor(_RemoteExecutor):
    """`docker exec -w <cwd> [-e K=V] [-i] <container> <argv>` against a running container.

    Start one with `docker run -d --name <c> <image> sleep infinity`
    (`scripts/swebench_docker_batch.py` does exactly that per instance).
    """

    def __init__(self, container: str, root: str = "/testbed",
                 python: str = "/opt/miniconda3/envs/testbed/bin/python", docker: str = "docker",
                 runner: Optional[HostRunner] = None, use_timeout: Optional[bool] = None) -> None:
        super().__init__(root, python, runner=runner, use_timeout=use_timeout)
        self.container = container
        self.docker = docker

    def _prefix(self, cwd: str, env: Mapping[str, str], interactive: bool) -> List[str]:
        argv = [self.docker, "exec"]
        if interactive:
            argv.append("-i")
        argv += ["-w", cwd]
        for k in sorted(env):
            argv += ["-e", f"{k}={env[k]}"]
        argv.append(self.container)
        return argv

    def describe(self) -> str:
        return f"docker:{self.container}:{self.root}"


class KubectlExecutor(_RemoteExecutor):
    """`kubectl exec [-i] -n <ns> <pod> -c <container> -- <argv>`.

    kubectl has no working-directory or environment flags, so the command is
    wrapped as `env K=V sh -c 'cd "$1" && shift && exec "$@"' sh <cwd> argv...`.
    """

    def __init__(self, pod: str, container: str = "", namespace: str = "default", root: str = "/testbed",
                 python: str = "/opt/miniconda3/envs/testbed/bin/python", kubectl: str = "kubectl",
                 runner: Optional[HostRunner] = None, use_timeout: Optional[bool] = None) -> None:
        super().__init__(root, python, runner=runner, use_timeout=use_timeout)
        self.pod = pod
        self.container = container
        self.namespace = namespace
        self.kubectl = kubectl

    def _prefix(self, cwd: str, env: Mapping[str, str], interactive: bool) -> List[str]:
        argv = [self.kubectl, "exec"]
        if interactive:
            argv.append("-i")
        argv += ["-n", self.namespace, self.pod]
        if self.container:
            argv += ["-c", self.container]
        argv.append("--")
        if env:
            argv += ["env"] + [f"{k}={env[k]}" for k in sorted(env)]
        argv += ["sh", "-c", 'cd "$1" && shift && exec "$@"', "sh", cwd]
        return argv

    def describe(self) -> str:
        return f"kubectl:{self.namespace}/{self.pod}" + (f"/{self.container}" if self.container else "") + f":{self.root}"


def shell_join(argv: Sequence[str]) -> str:
    """For logs and docs: the command as a human would type it."""
    return " ".join(shlex.quote(a) for a in argv)
