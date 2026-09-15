"""Concrete launch and harvest adapters for the generation controller.

Kept apart from `controller.py` so the control logic -- when to stop, when a
generation's results are trustworthy -- can be tested without a cluster, and so
a different runtime can be dropped in without touching it.
"""

import json
import os
import subprocess
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from hae.infra.llm import get_adc_access_token
from hae.task import Task


class LaunchError(RuntimeError):
    """Raised when a generation could not be started or did not finish."""


class KubernetesRuntime:
    """Renders a Job manifest, applies it, and blocks until the Job settles."""

    def __init__(self, namespace: str = "agent-evolution",
                 template: str = "k8s/generation-job.yaml.template",
                 poll_seconds: int = 30,
                 timeout_seconds: int = 7200,
                 repo_root: str = "."):
        self.namespace = namespace
        self.template = template
        self.poll_seconds = poll_seconds
        self.timeout_seconds = timeout_seconds
        self.repo_root = repo_root

    def _kubectl(self, args: List[str], check: bool = True) -> str:
        env = dict(os.environ)
        if "CLOUDSDK_AUTH_ACCESS_TOKEN" not in env:
            try:
                tok = subprocess.run(
                    ["gcloud", "auth", "application-default", "print-access-token"],
                    capture_output=True, text=True, timeout=60).stdout.strip()
                if tok:
                    env["CLOUDSDK_AUTH_ACCESS_TOKEN"] = tok
            except Exception:
                pass
        proc = subprocess.run(["kubectl", "-n", self.namespace] + args,
                              capture_output=True, text=True, env=env)
        if check and proc.returncode != 0:
            raise LaunchError(f"kubectl {' '.join(args)}: {proc.stderr.strip()[:400]}")
        return proc.stdout

    def __call__(self, generation: int, population_file: str, task: Task) -> None:
        job = f"parallel-firms-gen{generation}"
        manifest = os.path.join(
            self.repo_root, f"build/gen{generation}-job.yaml")
        os.makedirs(os.path.dirname(manifest), exist_ok=True)

        rendered = subprocess.run(
            ["python3", "scripts/render_job.py",
             "--generation", str(generation),
             "--population-file", population_file],
            capture_output=True, text=True, cwd=self.repo_root)
        if rendered.returncode != 0:
            raise LaunchError(f"render_job failed: {rendered.stderr.strip()[:400]}")
        with open(manifest, "w", encoding="utf-8") as fh:
            fh.write(rendered.stdout)

        # A stale Job of the same name silently blocks `apply`, and the
        # controller would then poll the *previous* generation to completion
        # and harvest its scorecards as if they were new.
        self._kubectl(["delete", "job", job, "--ignore-not-found"], check=False)
        self._kubectl(["apply", "-f", os.path.abspath(manifest)])
        print(f"[k8s] launched {job}")
        self._wait(job)

    def _wait(self, job: str) -> None:
        deadline = time.time() + self.timeout_seconds
        while time.time() < deadline:
            raw = self._kubectl(
                ["get", "job", job, "-o", "jsonpath={.status}"], check=False)
            status: Dict[str, Any] = json.loads(raw) if raw.strip() else {}
            succeeded = status.get("succeeded", 0)
            failed = status.get("failed", 0)
            active = status.get("active", 0)
            print(f"[k8s] {job}: {succeeded} succeeded, {failed} failed, "
                  f"{active} active")
            if not active:
                # The Job has settled. Whether the outcome is acceptable is the
                # CompletenessGate's decision, not this adapter's -- a runtime
                # that judges its own results is a runtime that can hide them.
                return
            time.sleep(self.poll_seconds)
        raise LaunchError(
            f"{job} did not settle within {self.timeout_seconds}s. Left running "
            f"so it can be inspected; delete it before relaunching.")


class GcsHarvest:
    """Reads a generation's scorecards back out of the results bucket."""

    def __init__(self, bucket: str, prefix: str = "parallel_runs"):
        self.bucket = bucket
        self.prefix = prefix

    def __call__(self, generation: int) -> List[Dict[str, Any]]:
        token = get_adc_access_token()
        if not token:
            raise LaunchError("no access token; cannot harvest")
        listing = urllib.parse.quote(
            f"{self.prefix}/generation_{generation}/")
        url = (f"https://storage.googleapis.com/storage/v1/b/{self.bucket}"
               f"/o?prefix={listing}")
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            items = json.load(resp).get("items", [])

        cards: List[Dict[str, Any]] = []
        for item in items:
            if not item["name"].endswith("_result.json"):
                continue
            obj = urllib.parse.quote(item["name"], safe="")
            media = (f"https://storage.googleapis.com/storage/v1/b/{self.bucket}"
                     f"/o/{obj}?alt=media")
            r = urllib.request.Request(
                media, headers={"Authorization": f"Bearer {token}"})
            with urllib.request.urlopen(r, timeout=120) as resp:
                cards.append(json.load(resp))
        print(f"[harvest] generation {generation}: {len(cards)} scorecards")
        return cards
