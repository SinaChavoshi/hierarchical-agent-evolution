"""Active Tool Sandboxing Environment for Virtual Enterprises.

Provides isolated workspace directories with safe primitives (write_file, read_file,
list_files, execute_bash) enabling agents to author, test, and self-heal code
before delivering to management.
"""

import os
import shutil
import subprocess
from typing import Dict, List, Any, Optional

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
        """Resolves a path relative to workspace_dir and guards against path traversal."""
        clean_path = os.path.normpath(relative_path.strip().lstrip("/"))
        resolved = os.path.abspath(os.path.join(self.workspace_dir, clean_path))
        if not resolved.startswith(self.workspace_dir):
            raise ValueError(f"Path traversal detected: {relative_path} resolves outside workspace.")
        return resolved

    def write_file(self, relative_path: str, content: str) -> Dict[str, Any]:
        """Writes content to a file in the workspace, creating parent directories."""
        try:
            target_path = self._resolve_path(relative_path)
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

    def execute_bash(self, command: str, timeout: int = 30) -> Dict[str, Any]:
        """Executes a shell command inside the workspace directory."""
        try:
            res = subprocess.run(
                command,
                shell=True,
                cwd=self.workspace_dir,
                capture_output=True,
                text=True,
                timeout=timeout
            )
            return {
                "status": "ok" if res.returncode == 0 else "failed",
                "exit_code": res.returncode,
                "stdout": res.stdout,
                "stderr": res.stderr
            }
        except subprocess.TimeoutExpired as e:
            return {
                "status": "timeout",
                "exit_code": -1,
                "stdout": e.stdout or "",
                "stderr": f"Command timed out after {timeout} seconds."
            }
        except Exception as e:
            return {
                "status": "error",
                "exit_code": -1,
                "stdout": "",
                "stderr": str(e)
            }

    def mount_assets(self, assets: List[Dict[str, Any]]) -> List[str]:
        """Mounts pre-licensed corporate assets directly into the workspace."""
        mounted = []
        for a in assets:
            name = a.get("name")
            content = a.get("content")
            if name and content:
                res = self.write_file(name, content)
                if res.get("status") == "ok":
                    mounted.append(name)
        return mounted

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
