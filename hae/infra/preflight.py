"""Launch-time environment checks.

A tournament is expensive and slow. Every failure this module detects has
already cost us at least one run:

  * Generation 11's first launch put seven pods into `Error` with
    `403 PERMISSION_DENIED on aiplatform.endpoints.predict`. The service
    account's `roles/aiplatform.user` binding had been granted earlier and was
    silently gone by launch time. Nothing noticed until the pods did.

    The cause is **Latchkey**, which reaps non-conforming IAM bindings on this
    project on its own schedule. This is not a one-off to be fixed once: any
    binding we add will be removed again. Preflight therefore both detects the
    condition and, with `repair=True`, re-grants before launch. See
    `repair_iam()` for why that is a stopgap rather than a solution.
  * Generation 10 lost firms to an access token that expired mid-run.
  * V1 defaulted the project to `YOUR_GCP_PROJECT_ID`, so a missing
    `GOOGLE_CLOUD_PROJECT` surfaced as a 403 naming a project that never
    existed, an hour in.

The common shape: a credential or permission problem that is trivially
detectable in two seconds before launch, discovered instead after an hour of
billed work. Preflight makes the detection happen first.

Every check answers a question with an actual request, not an inspection of
configuration. `roles/aiplatform.user` being present in an IAM policy is not
the same claim as "this identity can call this model in this region", and on
at least one occasion the two disagreed.

Checks are ordered cheapest-first and later checks are skipped once an earlier
one fails, so a missing project reports as a missing project rather than as
four cascading authentication errors.
"""

import json
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from hae.infra.config import DEFAULT_CONFIG, ConfigurationError, EvolutionConfig
from hae.infra.llm import detect_llm_provider, get_adc_access_token

_VERTEX_HOST = "{location}-aiplatform.googleapis.com"
_GCS_UPLOAD = "https://storage.googleapis.com/upload/storage/v1/b/{bucket}/o"
_GCS_OBJECT = "https://storage.googleapis.com/storage/v1/b/{bucket}/o/{obj}"


@dataclass
class CheckResult:
    """One question, asked and answered."""

    name: str
    ok: bool
    detail: str
    # What to actually type to fix it. Empty when the check passed.
    remedy: str = ""
    skipped: bool = False
    duration_s: float = 0.0

    @property
    def status(self) -> str:
        if self.skipped:
            return "SKIP"
        return "PASS" if self.ok else "FAIL"


@dataclass
class PreflightReport:
    """The full set of answers."""

    checks: List[CheckResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True only if nothing failed. A skipped check is not a pass."""
        return all(c.ok for c in self.checks if not c.skipped) and not self.skipped_any

    @property
    def skipped_any(self) -> bool:
        return any(c.skipped for c in self.checks)

    @property
    def failures(self) -> List[CheckResult]:
        return [c for c in self.checks if not c.ok and not c.skipped]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "checks": [
                {
                    "name": c.name,
                    "status": c.status,
                    "detail": c.detail,
                    "remedy": c.remedy,
                    "duration_s": round(c.duration_s, 3),
                }
                for c in self.checks
            ],
        }

    def render(self) -> str:
        """A report a human can act on without opening the source."""
        lines = ["", "=" * 68, "PREFLIGHT", "=" * 68]
        for c in self.checks:
            lines.append(f"  [{c.status:4}] {c.name:28} {c.detail}")
        lines.append("=" * 68)
        if self.ok:
            lines.append("  All checks passed. Safe to launch.")
        else:
            lines.append("  LAUNCH BLOCKED.")
            for c in self.checks:
                if c.remedy and not c.ok:
                    lines.append("")
                    lines.append(f"  {c.name}:")
                    for rl in c.remedy.strip().splitlines():
                        lines.append(f"    {rl}")
        lines.append("=" * 68)
        lines.append("")
        return "\n".join(lines)


def _timed(fn, *args, **kwargs) -> CheckResult:
    started = time.time()
    try:
        result = fn(*args, **kwargs)
    except Exception as exc:  # A check must never itself crash the launcher.
        result = CheckResult(
            name=getattr(fn, "_check_name", fn.__name__),
            ok=False,
            detail=f"check raised {type(exc).__name__}: {exc}",
            remedy="This is a bug in the preflight check itself; report it.",
        )
    result.duration_s = time.time() - started
    return result


def _env_gsa() -> Optional[str]:
    """The tournament service account, if the environment names one."""
    return os.getenv("AGENT_GSA") or os.getenv("AGENT_SERVICE_ACCOUNT")


def check_config(config: EvolutionConfig) -> CheckResult:
    """Project and bucket resolve to real values."""
    try:
        project = config.require_project()
        bucket = config.require_bucket()
    except ConfigurationError as exc:
        return CheckResult(
            name="config",
            ok=False,
            detail=str(exc).split(".")[0],
            remedy=(
                "export GOOGLE_CLOUD_PROJECT=<project>\n"
                "export GCS_BUCKET=<bucket>\n"
                "Both are required and neither is defaulted."
            ),
        )
    return CheckResult(
        name="config",
        ok=True,
        detail=f"project={project} bucket={bucket} location={config.location}",
    )


def check_credentials() -> CheckResult:
    """An access token is obtainable from some source."""
    token = get_adc_access_token(force_refresh=True)
    if not token:
        return CheckResult(
            name="credentials",
            ok=False,
            detail="no access token from any source",
            remedy=(
                "In-cluster, this means Workload Identity is not wired up:\n"
                "  kubectl get sa agent-evolution-sa -n agent-evolution \\\n"
                "    -o jsonpath='{.metadata.annotations}'\n"
                "should show iam.gke.io/gcp-service-account.\n"
                "Locally:\n"
                "  gcloud auth application-default login"
            ),
        )
    return CheckResult(
        name="credentials",
        ok=True,
        detail=f"token acquired ({len(token)} chars)",
    )


def check_vertex_model(model: str, config: EvolutionConfig, token: str) -> CheckResult:
    """This identity can actually invoke this model in this region.

    Deliberately a real `generateContent` call. An IAM policy listing
    `roles/aiplatform.user` is a statement about a policy document; this is a
    statement about what happens when we call the API. Generation 11 is the
    reason we no longer accept the former as evidence for the latter.
    """
    project = config.project_id
    location = config.location
    url = (
        f"https://{_VERTEX_HOST.format(location=location)}/v1/projects/{project}"
        f"/locations/{location}/publishers/google/models/{model}:generateContent"
    )
    body = json.dumps({
        "contents": [{"role": "user", "parts": [{"text": "ping"}]}],
        "generationConfig": {"maxOutputTokens": 1, "temperature": 0.0},
    }).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    name = f"vertex:{model}"
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            resp.read()
        return CheckResult(name=name, ok=True, detail=f"{location} reachable, 200")
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode("utf-8", "replace")[:300]
        remedy = ""
        if exc.code == 403:
            remedy = (
                "The service account cannot invoke Vertex. This binding has\n"
                "silently reverted on a shared project before -- re-grant and\n"
                "then re-run preflight:\n"
                "  gcloud projects add-iam-policy-binding $GOOGLE_CLOUD_PROJECT \\\n"
                "    --member=serviceAccount:<gsa> --role=roles/aiplatform.user\n"
                "Verify it stuck:\n"
                "  gcloud projects get-iam-policy $GOOGLE_CLOUD_PROJECT \\\n"
                "    --flatten='bindings[].members' \\\n"
                "    --format='value(bindings.role)' \\\n"
                "    --filter='bindings.members:<gsa>'"
            )
        elif exc.code == 404:
            remedy = (
                f"Model '{model}' is not published in '{location}'.\n"
                "Check the model name, or set GOOGLE_CLOUD_LOCATION to a region\n"
                "that serves it."
            )
        elif exc.code == 429:
            remedy = (
                f"Quota exhausted for '{model}' in '{location}'. A tournament\n"
                "launched now will thrash. Request quota or pick another region."
            )
        return CheckResult(
            name=name, ok=False, detail=f"HTTP {exc.code}: {payload}", remedy=remedy
        )
    except Exception as exc:
        return CheckResult(
            name=name,
            ok=False,
            detail=f"{type(exc).__name__}: {exc}",
            remedy=f"Could not reach {location}-aiplatform.googleapis.com.",
        )


def check_gcs_writable(config: EvolutionConfig, token: str) -> CheckResult:
    """Scorecards will have somewhere to land.

    Writes, then deletes, a probe object. Read access is not enough: a
    tournament that cannot write discovers it only after the work is done and
    the results are irrecoverable.
    """
    bucket = config.gcs_bucket
    obj = f"preflight/probe-{uuid.uuid4().hex}.txt"
    url = _GCS_UPLOAD.format(bucket=bucket) + f"?uploadType=media&name={urllib.parse.quote(obj)}"
    req = urllib.request.Request(
        url,
        data=b"preflight",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "text/plain"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp.read()
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode("utf-8", "replace")[:300]
        return CheckResult(
            name="gcs",
            ok=False,
            detail=f"write failed, HTTP {exc.code}: {payload}",
            remedy=(
                "  gcloud projects add-iam-policy-binding $GOOGLE_CLOUD_PROJECT \\\n"
                "    --member=serviceAccount:<gsa> --role=roles/storage.objectAdmin\n"
                f"and confirm gs://{bucket} exists."
            ),
        )
    except Exception as exc:
        return CheckResult(
            name="gcs", ok=False, detail=f"{type(exc).__name__}: {exc}",
            remedy=f"Could not reach storage.googleapis.com for gs://{bucket}.",
        )

    # Clean up. A failure here is not fatal -- we proved the thing we cared
    # about -- but it should be visible, because it means litter accumulates.
    cleaned = True
    try:
        del_req = urllib.request.Request(
            _GCS_OBJECT.format(bucket=bucket, obj=urllib.parse.quote(obj, safe="")),
            headers={"Authorization": f"Bearer {token}"},
            method="DELETE",
        )
        urllib.request.urlopen(del_req, timeout=30).read()
    except Exception:
        cleaned = False

    detail = f"gs://{bucket} writable"
    if not cleaned:
        detail += f" (probe object {obj} left behind; delete not permitted)"
    return CheckResult(name="gcs", ok=True, detail=detail)


# Roles a tournament actually needs. Least privilege on purpose: these are
# re-granted automatically, so the set must stay small enough to be obviously
# safe to re-grant without a human reading the diff.
REQUIRED_ROLES = ("roles/aiplatform.user", "roles/storage.objectAdmin")


def _gcloud(args: List[str], timeout: int = 120) -> "subprocess.CompletedProcess":
    """Runs gcloud with the ambient operator credentials.

    Context Aware Access blocks plain `gcloud` in some environments while
    allowing it with an explicit ADC token, so we pass one through when we can
    get one. A failure to obtain a token is not fatal here; gcloud may well
    have its own working credentials.
    """
    env = dict(os.environ)
    try:
        token = subprocess.run(
            ["gcloud", "auth", "application-default", "print-access-token"],
            capture_output=True, text=True, timeout=60,
        ).stdout.strip()
        if token:
            env["CLOUDSDK_AUTH_ACCESS_TOKEN"] = token
    except Exception:
        pass
    return subprocess.run(["gcloud"] + args, capture_output=True, text=True,
                          timeout=timeout, env=env)


def granted_roles(project: str, service_account: str) -> List[str]:
    """Roles currently bound to `service_account` on `project`.

    This reads the policy document. It is used to decide *what to grant*, never
    to decide whether the identity works -- `check_vertex_model` answers that,
    and the two have disagreed.
    """
    proc = _gcloud([
        "projects", "get-iam-policy", project,
        "--flatten=bindings[].members",
        "--format=value(bindings.role)",
        f"--filter=bindings.members:serviceAccount:{service_account}",
    ])
    if proc.returncode != 0:
        raise RuntimeError(f"get-iam-policy failed: {proc.stderr.strip()[:300]}")
    return [r.strip() for r in proc.stdout.splitlines() if r.strip()]


def repair_iam(project: str, service_account: str,
               roles: Optional[List[str]] = None) -> CheckResult:
    """Re-grants any missing required role. Idempotent.

    > This is a stopgap, not a fix.

    Latchkey will reap these bindings again. Re-granting at launch narrows the
    window from "the whole time" to "between this call and the next reap",
    which is enough to stop losing tournaments at minute zero but does nothing
    for a reap that lands mid-run. The durable fix is a Latchkey exemption for
    the tournament service account, and this function exists so that the cost
    of not having one is a two-second delay instead of a dead generation.

    Requires the *operator's* credentials to hold
    `roles/resourcemanager.projectIamAdmin`. Deliberately not something a
    worker pod can do: a pod that can grant itself IAM is a privilege
    escalation with extra steps, and Latchkey would strip that permission too.
    """
    roles = list(roles or REQUIRED_ROLES)
    try:
        have = set(granted_roles(project, service_account))
    except RuntimeError as exc:
        return CheckResult(
            name="iam-repair", ok=False, detail=str(exc),
            remedy=("Could not read the IAM policy. Check that your own\n"
                    "credentials can see the project:\n"
                    "  gcloud auth application-default login"),
        )

    missing = [r for r in roles if r not in have]
    if not missing:
        return CheckResult(name="iam-repair", ok=True,
                           detail=f"all {len(roles)} required roles already bound")

    granted, failed = [], []
    for role in missing:
        proc = _gcloud([
            "projects", "add-iam-policy-binding", project,
            f"--member=serviceAccount:{service_account}",
            f"--role={role}", "--condition=None", "--quiet",
        ])
        (granted if proc.returncode == 0 else failed).append(
            role if proc.returncode == 0 else f"{role} ({proc.stderr.strip()[:120]})")

    if failed:
        return CheckResult(
            name="iam-repair", ok=False,
            detail=f"granted {granted}, failed {failed}",
            remedy=("Your credentials likely lack\n"
                    "roles/resourcemanager.projectIamAdmin on this project.\n"
                    "Ask an owner to grant the roles, or to add a Latchkey\n"
                    "exemption for the tournament service account -- the\n"
                    "exemption is the real fix; this repair is a stopgap."),
        )
    return CheckResult(
        name="iam-repair", ok=True,
        detail=f"re-granted {', '.join(granted)} (reaped by Latchkey; expected to recur)")


def await_propagation(config: EvolutionConfig, model: str, token: str,
                      timeout_s: float = 120.0,
                      sleep_s: float = 10.0) -> CheckResult:
    """Polls the live Vertex check until a fresh grant takes effect.

    IAM changes are not synchronous. Granting and immediately launching
    reproduces the original failure with extra confidence, so we block on the
    thing we actually care about -- a successful API call -- rather than on the
    policy read, which goes green first.
    """
    deadline = time.time() + timeout_s
    attempt = 0
    result = None
    while time.time() < deadline:
        attempt += 1
        result = check_vertex_model(model, config, token)
        if result.ok:
            result.detail += f" (after {attempt} attempt(s))"
            return result
        if "403" not in result.detail:
            return result  # Not a propagation problem; stop waiting.
        time.sleep(sleep_s)
    if result is not None:
        result.detail = f"still failing after {timeout_s:.0f}s: {result.detail}"
        return result
    return CheckResult(name=f"vertex:{model}", ok=False,
                       detail="propagation wait produced no result")


def run_preflight(
    config: Optional[EvolutionConfig] = None,
    models: Optional[List[str]] = None,
    skip_gcs: bool = False,
    repair: bool = False,
    service_account: Optional[str] = None,
) -> PreflightReport:
    """Run every check, cheapest first, short-circuiting dependents.

    Returns a report; never raises for an environment problem. The caller
    decides whether a failure is fatal, because `--mode preflight` wants to
    print all of it while a launcher wants to stop at the first sign of
    trouble.
    """
    config = config or DEFAULT_CONFIG
    report = PreflightReport()

    cfg_result = _timed(check_config, config)
    report.checks.append(cfg_result)
    if not cfg_result.ok:
        # Everything downstream needs a project. Reporting four failures when
        # one variable is unset buries the actual cause.
        for name in ["credentials", "vertex", "gcs"]:
            report.checks.append(
                CheckResult(name=name, ok=False, skipped=True,
                            detail="skipped: configuration incomplete"))
        return report

    provider = detect_llm_provider()
    if provider != "vertex":
        report.checks.append(CheckResult(
            name="provider", ok=True,
            detail=f"provider={provider}; Vertex checks not applicable"))
        return report

    cred_result = _timed(check_credentials)
    report.checks.append(cred_result)
    if not cred_result.ok:
        for name in ["vertex", "gcs"]:
            report.checks.append(
                CheckResult(name=name, ok=False, skipped=True,
                            detail="skipped: no credentials"))
        return report

    token = get_adc_access_token()

    # Repair before probing, not after failing: Latchkey reaps these bindings
    # on its own schedule, so "it worked last time" carries no information.
    if repair:
        gsa = service_account or _env_gsa()
        if not gsa:
            report.checks.append(CheckResult(
                name="iam-repair", ok=False,
                detail="repair requested but no service account given",
                remedy=("Pass --service-account, or set AGENT_GSA:\n"
                        "  export AGENT_GSA=<name>@<project>.iam.gserviceaccount.com")))
        else:
            report.checks.append(
                _timed(repair_iam, config.require_project(), gsa))

    # Both tiers. They are distinct models with distinct quota and distinct
    # regional availability, and a tournament uses both.
    if models is None:
        models = sorted({config.worker_model, config.executive_model,
                         config.judge_model})
    for model in models:
        result = _timed(check_vertex_model, model, config, token)
        # A 403 straight after a repair is probably just propagation lag, not a
        # real denial. Distinguish the two by waiting, rather than by guessing.
        repaired = any(c.name == "iam-repair" and c.ok and "re-granted" in c.detail
                       for c in report.checks)
        if not result.ok and repaired and "403" in result.detail:
            result = _timed(await_propagation, config, model, token)
        report.checks.append(result)

    if skip_gcs:
        report.checks.append(CheckResult(
            name="gcs", ok=True, detail="skipped by request (--skip-gcs)"))
    else:
        report.checks.append(_timed(check_gcs_writable, config, token))

    return report
