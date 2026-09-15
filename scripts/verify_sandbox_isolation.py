"""In-cluster verification of the network-namespace sandbox.

The unit tests in `tests/test_sandbox_isolation.py` prove the mechanism is
wired correctly. They cannot prove the hole is shut, because the hole was a
live Workload Identity token on a GKE node and a laptop has neither.

So this runs in the pod, on the real service account. Its first act is a
**control**: reach the metadata server *outside* the sandbox and confirm the
token is still there. If the control fails the whole run is inconclusive --
we would be congratulating ourselves for a hole the node had already closed
for unrelated reasons. That is precisely the error the V1 scores were
retracted for.

Run:

    kubectl create configmap sandbox-probe -n agent-evolution \
        --from-file=probe.py=scripts/verify_sandbox_isolation.py
    # ...mount at /probe in a pod using the tournament service account, then:
    #   python /probe/probe.py

Prints RESULT_JSON followed by VERDICT PASS or VERDICT FAIL: <reasons>.
"""
import json, os, subprocess, sys

sys.path.insert(0, "/app")
from hae.runtime.workspace import AgentWorkspace

TOKEN_URL = ("http://169.254.169.254/computeMetadata/v1/instance/"
             "service-accounts/default/token")
NAME_URL = ("http://metadata.google.internal/computeMetadata/v1/instance/"
            "service-accounts/default/token")

out = {}

# --- CONTROL -----------------------------------------------------------
# Unsandboxed, the token must still be reachable. If this fails the rest of
# the probe proves nothing: we would be "closing" a hole the node had already
# closed for unrelated reasons.
try:
    r = subprocess.run(
        ["curl", "-s", "-m", "5", "-H", "Metadata-Flavor: Google", TOKEN_URL],
        capture_output=True, text=True, timeout=20)
    out["control_unsandboxed_token_present"] = "access_token" in r.stdout
except Exception as e:
    out["control_unsandboxed_token_present"] = f"ERROR {e}"

ws = AgentWorkspace("netns_probe", base_dir="/tmp/probe_ws")
out["netns_available"] = AgentWorkspace._netns_available()

# --- THE LEAK, THREE WAYS ----------------------------------------------
def leaked(res):
    return "access_token" in (res.get("stdout") or "")

r1 = ws.execute_bash(
    f'curl -s -m 5 -H "Metadata-Flavor: Google" {TOKEN_URL}', timeout=25)
out["curl_ip"] = {"status": r1["status"], "leaked": leaked(r1),
                  "isolated": r1.get("network_isolated")}

r2 = ws.execute_bash(
    f'curl -s -m 5 -H "Metadata-Flavor: Google" {NAME_URL}', timeout=25)
out["curl_dns_name"] = {"status": r2["status"], "leaked": leaked(r2),
                        "isolated": r2.get("network_isolated")}

r3 = ws.execute_bash(
    "python3 -c \"import urllib.request as u;"
    "req=u.Request('%s',headers={'Metadata-Flavor':'Google'});"
    "print(u.urlopen(req,timeout=5).read().decode())\"" % TOKEN_URL,
    timeout=25)
out["urllib_ip"] = {"status": r3["status"], "leaked": leaked(r3),
                    "isolated": r3.get("network_isolated")}

# --- THE ISOLATION MUST NOT COST ANYTHING WE USE ------------------------
ws.write_file("t/test_x.py", "def test_x():\n    assert 1 + 1 == 2\n")
r4 = ws.execute_bash("python3 -m pytest t/ -q 2>&1 | tail -3", timeout=60)
out["pytest_in_sandbox"] = {"status": r4["status"],
                            "tail": (r4.get("stdout") or "").strip()[-120:],
                            "isolated": r4.get("network_isolated")}

r5 = ws.execute_bash(
    "python3 -c \"import socket;s=socket.socket();"
    "s.bind(('127.0.0.1',0));print('bound',s.getsockname()[1]>0)\"")
out["loopback"] = {"status": r5["status"],
                   "stdout": (r5.get("stdout") or "").strip()}

# --- FAIL CLOSED --------------------------------------------------------
# With the flag set and namespaces unavailable, nothing may execute.
os.environ["HAE_REQUIRE_NETWORK_ISOLATION"] = "1"
saved = AgentWorkspace._NETNS_SUPPORTED
AgentWorkspace._NETNS_SUPPORTED = False
r7 = ws.execute_bash("echo SHOULD_NOT_RUN")
out["fail_closed"] = {"status": r7["status"],
                      "ran": "SHOULD_NOT_RUN" in (r7.get("stdout") or ""),
                      "isolated": r7.get("network_isolated")}
AgentWorkspace._NETNS_SUPPORTED = saved

# With the flag set and namespaces available, work proceeds.
r8 = ws.execute_bash("echo PROCEEDS")
out["flag_set_and_available"] = {"status": r8["status"],
                                 "stdout": (r8.get("stdout") or "").strip(),
                                 "isolated": r8.get("network_isolated")}
del os.environ["HAE_REQUIRE_NETWORK_ISOLATION"]

print("RESULT_JSON " + json.dumps(out, indent=2))

# --- VERDICT ------------------------------------------------------------
fails = []
if out["control_unsandboxed_token_present"] is not True:
    fails.append("control did not reproduce the leak; probe is inconclusive")
if not out["netns_available"]:
    fails.append("no network namespaces in this container")
for k in ("curl_ip", "curl_dns_name", "urllib_ip"):
    if out[k]["leaked"]:
        fails.append(f"{k} STILL LEAKS A TOKEN")
    if out[k]["isolated"] is not True:
        fails.append(f"{k} was not isolated")
if out["pytest_in_sandbox"]["status"] != "ok":
    fails.append("pytest broke under isolation")
if "bound True" not in out["loopback"]["stdout"]:
    fails.append("loopback broke under isolation")
if out["fail_closed"]["ran"] or out["fail_closed"]["status"] != "error":
    fails.append("did not fail closed")
if out["flag_set_and_available"]["status"] != "ok":
    fails.append("flag blocks legitimate work when namespaces are available")

print("VERDICT " + ("PASS" if not fails else "FAIL: " + "; ".join(fails)))
