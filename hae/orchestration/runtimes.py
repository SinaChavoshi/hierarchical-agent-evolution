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
    """Publishes the population, renders a Job, applies it, and blocks.

    The Job mounts the population as a ConfigMap and the code from an
    immutable image tag. Both are this adapter's responsibility: a Job applied
    without its ConfigMap does not fail, it sits in `ContainerCreating` until
    someone notices, which for an unattended campaign means until morning.
    """

    def __init__(self, image_tag: str,
                 namespace: str = "agent-evolution",
                 template: str = "k8s/generation-job.yaml.template",
                 poll_seconds: int = 30,
                 timeout_seconds: int = 7200,
                 repo_root: str = "."):
        if not image_tag or image_tag == "latest":
            # A campaign spanning hours must not race the next build. `latest`
            # would let generation 3 run different code from generation 1 and
            # report the difference as evolution.
            raise LaunchError(
                "KubernetesRuntime requires an immutable image_tag, never "
                f"'latest' (got {image_tag!r}).")
        self.image_tag = image_tag
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

    def _publish_population(self, generation: int, population_file: str) -> str:
        """Puts the bred population in a ConfigMap for the Job to mount.

        `create --dry-run=client -o yaml | apply` is the idiomatic upsert and
        is wrong here: `apply` stores the previous state in the
        `last-applied-configuration` annotation, and a ten-genome population
        exceeds the 262144-byte annotation limit. Delete-then-create sidesteps
        it. The window between the two is harmless because the Job that mounts
        this has not been applied yet.

        The key must match the path the worker is given on its command line --
        `/configs/generation_<N>_population.json` -- or the container starts
        and immediately fails to find its own population.
        """
        name = f"hae-gen{generation}-population"
        key = f"generation_{generation}_population.json"
        self._kubectl(["delete", "configmap", name, "--ignore-not-found"],
                      check=False)
        self._kubectl(["create", "configmap", name,
                       f"--from-file={key}={os.path.abspath(population_file)}"])
        print(f"[k8s] published {name} ({key})")
        return name

    def __call__(self, generation: int, population_file: str, task: Task) -> None:
        job = f"parallel-firms-gen{generation}"
        manifest = os.path.join(
            self.repo_root, f"build/gen{generation}-job.yaml")
        os.makedirs(os.path.dirname(manifest), exist_ok=True)

        configmap = self._publish_population(generation, population_file)

        rendered = subprocess.run(
            ["python3", "scripts/render_job.py",
             "--generation", str(generation),
             "--image-tag", self.image_tag,
             "--configmap", configmap,
             "--job-name", job,
             "--namespace", self.namespace],
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
        print(f"[k8s] launched {job} @ {self.image_tag}")
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

    def __init__(self, bucket: str, prefix: str = "parallel_runs",
                 local_dir: str = "experiments/v2"):
        self.bucket = bucket
        self.prefix = prefix
        self.local_dir = local_dir

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

        out_dir = os.path.join(self.local_dir, f"generation_{generation}_results")
        os.makedirs(out_dir, exist_ok=True)

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
                card = json.load(resp)
                cards.append(card)
                fname = os.path.basename(item["name"])
                with open(os.path.join(out_dir, fname), "w", encoding="utf-8") as fh:
                    json.dump(card, fh, indent=2)
        print(f"[harvest] generation {generation}: {len(cards)} scorecards -> {out_dir}")
        return cards
