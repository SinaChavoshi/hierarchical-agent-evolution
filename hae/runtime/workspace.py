"""Active Tool Sandboxing Environment for Virtual Enterprises.

Provides isolated workspace directories with safe primitives (write_file, read_file,
list_files, execute_bash) enabling agents to author, test, and self-heal code
before delivering to management.
"""

import os
import shutil
import subprocess
from typing import Dict, List, Any, Optional

from hae.evaluation.artifacts import EXCLUDED_DIR_SEGMENTS, EXCLUDED_DIR_SUFFIXES, sanitize_path

class AgentWorkspace:
    """Isolated scratchpad environment for a virtual enterprise."""

    def __init__(self, company_id: str, base_dir: str = "/tmp/hae_workspaces"):
        self.company_id = company_id
        self.base_dir = base_dir
        self.workspace_dir = os.path.abspath(os.path.join(base_dir, company_id))
        os.makedirs(self.workspace_dir, exist_ok=True)

    @property
    def path(self) -> str:
        """Alias for workspace_dir."""
        return self.workspace_dir

    def _resolve_path(self, relative_path: str) -> str:
        """Resolves a path relative to workspace_dir and guards against path traversal.

        Agent-supplied paths arrive through a free-text ReAct protocol, so
        markdown decoration (``**bold**``, backticks) regularly leaks into the
        filename. Stripping it here means the artifact lands at its intended
        location rather than being written as an unusable sibling such as
        ``routing.py**``.
        """
        clean_path = os.path.normpath(sanitize_path(relative_path).lstrip("/"))
        resolved = os.path.abspath(os.path.join(self.workspace_dir, clean_path))
        if not resolved.startswith(self.workspace_dir):
            raise ValueError(f"Path traversal detected: {relative_path} resolves outside workspace.")
        return resolved

    def write_file(self, relative_path: str, content: str) -> Dict[str, Any]:
        """Writes content to a file in the workspace, creating parent directories."""
        try:
            target_path = self._resolve_path(relative_path)
            if relative_path.endswith(".py") and os.path.exists(target_path):
                try:
                    import ast
                    with open(target_path, "r", encoding="utf-8", errors="replace") as existing_f:
                        existing_src = existing_f.read()
                    if len(existing_src.strip()) > 200:
                        ast.parse(existing_src)
                        # Existing file is valid, non-trivial Python. Verify candidate is valid Python.
                        if content.lstrip().startswith("Action:") or len(content.strip()) < 100:
                            return {
                                "status": "error",
                                "path": relative_path,
                                "error": (
                                    f"Refused to overwrite valid Python module ({len(existing_src)} bytes) "
                                    f"with truncated/ReAct-header snippet ({len(content)} bytes)."
                                ),
                            }
                        ast.parse(content)
                except SyntaxError as syn_err:
                    return {
                        "status": "error",
                        "path": relative_path,
                        "error": (
                            f"Refused to overwrite valid Python module with invalid syntax "
                            f"({syn_err.msg} at line {syn_err.lineno})."
                        ),
                    }
                except Exception:
                    pass
            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            with open(target_path, "w", encoding="utf-8") as f:
                f.write(content)
            return {
                "status": "ok",
                "path": relative_path,
                "bytes_written": len(content.encode("utf-8"))
            }
        except Exception as e:
            return {
                "status": "error",
                "path": relative_path,
                "error": str(e)
            }

    def read_file(self, relative_path: str, max_bytes: int = 200000) -> Dict[str, Any]:
        """Reads content from a file in the workspace."""
        try:
            target_path = self._resolve_path(relative_path)
            if not os.path.exists(target_path):
                return {"status": "error", "path": relative_path, "error": "File not found"}
            if os.path.isdir(target_path):
                return {"status": "error", "path": relative_path, "error": "Path is a directory, not a file"}
            with open(target_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read(max_bytes)
            return {
                "status": "ok",
                "path": relative_path,
                "content": content
            }
        except Exception as e:
            return {"status": "error", "path": relative_path, "error": str(e)}

    def list_files(self, subpath: str = "") -> List[Dict[str, Any]]:
        """Lists files in the workspace recursively."""
        results = []
        try:
            start_dir = self._resolve_path(subpath) if subpath else self.workspace_dir
            if not os.path.exists(start_dir):
                return results
            for root, dirs, files in os.walk(start_dir):
                dirs[:] = [
                    d for d in dirs
                    if d not in EXCLUDED_DIR_SEGMENTS and not d.endswith(EXCLUDED_DIR_SUFFIXES)
                ]
                for f in sorted(files):
                    full = os.path.join(root, f)
                    rel = os.path.relpath(full, self.workspace_dir)
                    results.append({
                        "path": rel,
                        "size": os.path.getsize(full),
                        "is_file": True
                    })
        except Exception:
            pass
        return sorted(results, key=lambda x: x["path"])

    #: Environment variables that must never reach agent-authored code.
    _CREDENTIAL_ENV = (
        "CLOUDSDK_AUTH_ACCESS_TOKEN", "GOOGLE_APPLICATION_CREDENTIALS",
        "VERTEX_API_TOKEN", "GEMINI_API_KEY", "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
        "GITHUB_TOKEN",
    )

    #: Cached result of probing for rootless network namespaces. Class-level so
    #: the probe runs once per process rather than once per command.
    _NETNS_SUPPORTED: Optional[bool] = None

    @classmethod
    def _netns_available(cls) -> bool:
        """Whether this environment can give a command an empty network stack.

        Probed once, by doing it. Asking whether `unshare` exists on PATH is a
        different question from whether the kernel will permit an unprivileged
        user namespace, and the two disagree on hardened hosts.
        """
        if cls._NETNS_SUPPORTED is None:
            try:
                proc = subprocess.run(
                    ["unshare", "-rn", "true"],
                    capture_output=True, timeout=10)
                cls._NETNS_SUPPORTED = proc.returncode == 0
            except Exception:
                cls._NETNS_SUPPORTED = False
        return cls._NETNS_SUPPORTED

    def _sandbox_env(self) -> Dict[str, str]:
        """The environment agent-authored code runs in, minus credentials.

        This is hygiene, not a boundary, and it is important to be precise
        about which. Measured in-cluster on the real service account:

          * `google.auth.default()` inside the sandbox raises
            `DefaultCredentialsError` -- so `GCE_METADATA_HOST` does bind code
            that consults it, which is most client-library code.
          * `curl http://169.254.169.254/...` inside the same sandbox returned
            **HTTP 200 with a live access token**, as did raw `urllib`.

        An environment variable only constrains callers that read it. The
        actual boundary is the network namespace; see `execute_bash`.
        """
        env = {k: v for k, v in os.environ.items()
               if k not in self._CREDENTIAL_ENV}
        env["GCE_METADATA_HOST"] = "169.254.169.254:9"   # discard port
        env["GCE_METADATA_IP"] = "169.254.169.254:9"
        env["GOOGLE_CLOUD_DISABLE_GRPC"] = "true"
        env["NO_GCE_CHECK"] = "true"
        return env

    def execute_bash(self, command: str, timeout: int = 30) -> Dict[str, Any]:
        """Runs a shell command in the workspace, with no network.

        The command is executed inside its own empty network namespace via
        `unshare -rn`. Agent code here compiles Python and runs tests; it has
        no legitimate need to reach anything. Removing the network entirely is
        both stronger and simpler than blocking the one address we happened to
        think of, and unlike a NetworkPolicy it does not depend on the cluster
        enforcing one.

        Verified in-cluster: the metadata server is unreachable inside the
        namespace, while `pytest` passes and loopback still binds -- so the
        isolation costs the agents nothing they actually use.

        Under `HAE_REQUIRE_NETWORK_ISOLATION=1` (set for tournament pods) this
        refuses to run at all when namespaces are unavailable, rather than
        quietly executing with a live path to the metadata server. A sandbox
        that silently stops sandboxing is worse than one that was never
        claimed, because the surrounding code goes on trusting it.
        """
        isolated = self._netns_available()
        required = os.getenv("HAE_REQUIRE_NETWORK_ISOLATION") == "1"

        if required and not isolated:
            return {
                "status": "error", "exit_code": -1, "stdout": "",
                "stderr": ("Refusing to execute: HAE_REQUIRE_NETWORK_ISOLATION=1 "
                           "but this host cannot create a network namespace. "
                           "Without one, agent-authored code can read a live "
                           "Workload Identity token from 169.254.169.254."),
                "network_isolated": False,
            }

        # No outer shell: the command goes to /bin/sh as a single argument, so
        # there is no second round of quoting to get wrong.
        argv = (["unshare", "-rn", "/bin/sh", "-c", command] if isolated
                else ["/bin/sh", "-c", command])

        try:
            res = subprocess.run(
                argv,
                cwd=self.workspace_dir,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=self._sandbox_env(),
            )
            return {
                "status": "ok" if res.returncode == 0 else "failed",
                "exit_code": res.returncode,
                "stdout": res.stdout,
                "stderr": res.stderr,
                "network_isolated": isolated,
            }
        except subprocess.TimeoutExpired as e:
            return {
                "status": "timeout",
                "exit_code": -1,
                "stdout": e.stdout or "",
                "stderr": f"Command timed out after {timeout} seconds.",
                "network_isolated": isolated,
            }
        except Exception as e:
            return {
                "status": "error",
                "exit_code": -1,
                "stdout": "",
                "stderr": str(e),
                "network_isolated": isolated,
            }

    def get_file_tree(self) -> str:
        """Returns an ASCII tree representation of workspace files."""
        files = self.list_files()
        if not files:
            return "(Empty workspace)"
        lines = []
        for f in files:
            lines.append(f"- {f['path']} ({f['size']} bytes)")
        return "\n".join(lines)

    def export_bundle(self) -> Dict[str, str]:
        """Exports all workspace text files as a dictionary {relative_path: content}."""
        bundle = {}
        for item in self.list_files():
            path = item["path"]
            res = self.read_file(path)
            if res.get("status") == "ok":
                bundle[path] = res.get("content", "")
        return bundle

    def cleanup(self):
        """Deletes the workspace directory."""
        if os.path.exists(self.workspace_dir):
            shutil.rmtree(self.workspace_dir, ignore_errors=True)
