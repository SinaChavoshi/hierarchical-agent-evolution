"""SweBenchCompanyRunner end to end on a temp git repository, with System 1 faked.

The fake proposer returns a reproduction probe that exits 1 while the bug is
present; the fake synthesiser answers with a REPAIR_PLAN that fixes it; the
gatekeeper's self-oracle re-run must then mark the root question RESOLVED and
the exported prediction must apply cleanly to a fresh checkout.
"""

import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, ".")

from hae.epistemic.gatekeeper import SELF_ORACLE_COMMAND  # noqa: E402
from hae.epistemic.ledger import Q_OPEN, Q_RESOLVED, SUPPORTED  # noqa: E402
from hae.genome.schema import AgentGenome, CompanyGenome, DepartmentGenome  # noqa: E402
from hae.swebench import runner as R  # noqa: E402
from hae.swebench.dataset import SweTask, write_jsonl  # noqa: E402
from hae.swebench.executor import LocalExecutor  # noqa: E402

BUGGY = "def f(x):\n    return x + 2\n"
FIXED = "def f(x):\n    return x + 1\n"
STATEMENT = """f(1) returns 3 instead of 2

**Describe the bug**
Calling `pkg.mod.f(1)` returns 3; the documentation says it increments by one.

```
Traceback (most recent call last):
  File "/home/me/proj/pkg/mod.py", line 2, in f
    return x + 2
AssertionError: expected 2, got 3
```
"""
STATEMENT_NO_PATH = "Increment is off by one\n\nThe increment helper adds two instead of one. f(1) gives 3.\n"
REPRO = "import sys\nfrom pkg.mod import f\nprint('F', f(1))\nsys.exit(1 if f(1) == 3 else 0)\n"


def _git(root, *args):
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True).stdout


def _write(root, rel, body):
    full = os.path.join(root, rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as fh:
        fh.write(body)


def make_repo(root):
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    _write(root, "pkg/__init__.py", "")
    _write(root, "pkg/mod.py", BUGGY)
    _write(root, "pkg/other.py", "X = 1\n")
    _write(root, "tests/__init__.py", "")
    _write(root, "tests/test_mod.py", "from pkg.mod import f\n\ndef test_type():\n    assert isinstance(f(1), int)\n")
    _write(root, "README.md", "demo\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")


def genome():
    engineer = AgentGenome(role="Lead Implementation Engineer", goal="Fix code", backstory="Systems",
                           temperature=0.2, model_tier="worker", tools_enabled=True)
    tester = AgentGenome(role="QA Analyst", goal="Verify", backstory="QA", temperature=0.2, model_tier="worker")
    return CompanyGenome(
        company_id="swe_firm", generation=1, parent_ids=[], mutation_history=[],
        ceo=AgentGenome(role="CEO", goal="Lead", backstory="Exec", temperature=0.4, model_tier="executive"),
        departments=[
            DepartmentGenome(dept_id="dept_systems_eng", name="Systems Engineering",
                             mandate="Build and repair modules", manager=engineer, agents=[engineer]),
            DepartmentGenome(dept_id="dept_qa_redteam", name="QA & Verification",
                             mandate="Verify behaviour", manager=tester, agents=[tester]),
        ],
        epistemic_policy={"enabled": True, "search_budget_moves": 12, "branching_k": 2,
                          "low_prior_quota": 0.0, "min_hypotheses_before_synthesis": 1,
                          "experiment_timeout_s": 20},
    )


def task(statement=STATEMENT):
    return SweTask(instance_id="demo__pkg-1", repo="demo/pkg", base_commit="deadbeef", version="1.0",
                   problem_statement=statement)


class FakeSystem1:
    """Replaces `_execute_agent` / `_execute_agent_with_tools` on the runner."""

    def __init__(self, locate_path="pkg/mod.py"):
        self.calls = []
        self.locate_path = locate_path

    def execute(self, agent, prompt, context="", reserved=False, response_format=None, max_tokens=None):
        schema = (response_format or {}).get("json_schema", {}).get("name")
        self.calls.append({"schema": schema, "prompt": prompt[:80], "context_len": len(context)})
        if schema == "HypothesisSetPacket":
            qid = re.search(r'Set `question_id` to "([^"]+)"', prompt).group(1)
            return json.dumps({"packet": "HYPOTHESIS_SET", "question_id": qid, "hypotheses": [
                {"claim": "f multiplies", "mechanism": "uses *", "prior": 0.7,
                 "probe_code": "from pkg.mod import f\nprint('PRODUCT', f(3))\n",
                 "prediction": {"expect_exit_code": 0, "expect_stdout_contains": "PRODUCT 9"}},
                {"claim": "f adds 2 instead of 1", "mechanism": "the literal in the return is 2", "prior": 0.3,
                 "probe_code": REPRO,
                 "prediction": {"expect_exit_code": 1, "expect_stdout_contains": "F 3"}},
            ]})
        if schema == "RepairPlanPacket":
            hid = re.search(r'Set `hypothesis_id` to "([^"]+)"', prompt).group(1)
            return json.dumps({"packet": "REPAIR_PLAN", "hypothesis_id": hid, "function": "f",
                               "rationale": "increment by one", "old_lines": ["    return x + 2"],
                               "new_lines": ["    return x + 1"]})
        if schema == "LocatePacket":
            return json.dumps({"packet": "LOCATE", "path": self.locate_path, "rationale": "the helper lives there"})
        return "no"

    def execute_with_tools(self, agent, prompt, context="", max_turns=3, target_path=""):
        self.calls.append({"schema": "tools", "prompt": prompt[:80]})
        return "Action: finish\nSummary: nothing to do"


class RunnerFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "repo")
        os.makedirs(self.root)
        make_repo(self.root)
        self.ex = LocalExecutor(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def make_runner(self, t=None, fake=None):
        fake = fake or FakeSystem1()
        r = R.SweBenchCompanyRunner(genome(), t or task(), self.ex)
        r._execute_agent = fake.execute
        r._execute_agent_with_tools = fake.execute_with_tools
        return r, fake


class EndToEndTests(RunnerFixture):
    def test_bug_is_reproduced_patched_and_confirmed_gone(self):
        runner, fake = self.make_runner()
        out = runner.run_swebench(budget_moves=12, max_iterations=3)
        state = runner.epistemic_state
        root = state.questions[runner.seed_info["root_question_id"]]
        self.assertEqual(root.module, "pkg/mod.py")
        self.assertEqual(root.status, Q_RESOLVED)
        swe = out["swebench"]
        self.assertTrue(swe["resolved_by_self_oracle"], swe)
        self.assertEqual(swe["stop"], "resolved")
        self.assertEqual(swe["changed_files"], ["pkg/mod.py"])
        self.assertTrue(any(ev.command == SELF_ORACLE_COMMAND for ev in state.evidence_log))
        # No LOCATE call: the statement named the path.
        self.assertFalse(any(c["schema"] == "LocatePacket" for c in fake.calls))
        # The fake answers the sub-question with the same claims; those copies
        # run after the patch and are rightly FALSIFIED. Look at the root's.
        by_claim = {h.claim: h for h in state.hypotheses_for(root.question_id)}
        self.assertEqual(by_claim["f adds 2 instead of 1"].status, SUPPORTED)
        self.assertTrue(by_claim["f adds 2 instead of 1"].patch_applied)
        self.assertEqual(by_claim["f multiplies"].status, "FALSIFIED")
        # The repository was edited in place and the prediction is its diff.
        with open(os.path.join(self.root, "pkg", "mod.py")) as fh:
            self.assertEqual(fh.read(), FIXED)
        pred = out["prediction"]
        self.assertEqual(pred["instance_id"], "demo__pkg-1")
        self.assertIn("+    return x + 1", pred["model_patch"])
        self.assertNotIn(".hae/", pred["model_patch"])
        self.assertTrue(os.path.isdir(os.path.join(self.root, ".hae")))  # probes ran in place
        with tempfile.TemporaryDirectory() as fresh:
            subprocess.run(["git", "clone", "-q", self.root, fresh], check=True, capture_output=True)
            subprocess.run(["git", "checkout", "-q", "HEAD"], cwd=fresh, check=True)
            patch = os.path.join(fresh, "pred.diff")
            with open(patch, "w") as fh:
                fh.write(pred["model_patch"])
            chk = subprocess.run(["git", "apply", "--check", patch], cwd=fresh, capture_output=True, text=True)
            self.assertEqual(chk.returncode, 0, chk.stderr)
        # Standard record shape plus the SWE-bench extras.
        for key in ("epistemic_ledger", "epistemic_search", "epistemic_searches", "token_usage", "opex",
                    "workspace_files", "verification_loop"):
            self.assertIn(key, out)
        self.assertEqual(out["verification_loop"]["mode"], "swebench")
        self.assertEqual(sorted(out["workspace_files"]), ["pkg/mod.py"])
        self.assertIn("SWE-bench demo__pkg-1", out["final_deliverable"])

    def test_locate_step_when_statement_names_no_path(self):
        runner, fake = self.make_runner(t=task(STATEMENT_NO_PATH))
        out = runner.run_swebench(budget_moves=12, max_iterations=2)
        self.assertTrue(any(c["schema"] == "LocatePacket" for c in fake.calls))
        root = runner.epistemic_state.questions[runner.seed_info["root_question_id"]]
        self.assertEqual(root.module, "pkg/mod.py")
        self.assertEqual(runner.locate_log[-1]["resolved"], "pkg/mod.py")
        self.assertTrue(out["swebench"]["resolved_by_self_oracle"])

    def test_unresolvable_locate_leaves_root_without_module(self):
        runner, fake = self.make_runner(t=task(STATEMENT_NO_PATH), fake=FakeSystem1(locate_path="nowhere/nothing.py"))
        out = runner.run_swebench(budget_moves=4, max_iterations=1)
        root = runner.epistemic_state.questions[runner.seed_info["root_question_id"]]
        self.assertEqual(root.module, "")
        self.assertFalse(out["swebench"]["resolved_by_self_oracle"])
        self.assertEqual(out["prediction"]["model_patch"], "")

    def test_budget_is_total_across_iterations(self):
        runner, _ = self.make_runner()
        out = runner.run_swebench(budget_moves=2, max_iterations=5)
        self.assertLessEqual(out["swebench"]["moves_used"], 2)
        self.assertIn(out["swebench"]["stop"], ("budget", "no_progress", "resolved"))

    def test_prompt_context_carries_task_statement_map_source_and_rules(self):
        runner, _ = self.make_runner()
        runner.seed_info = {"paths": ["pkg/mod.py"]}
        ctx = runner._get_focused_module_context("pkg/mod.py", runner.objective_text())
        for needle in ("TASK: fix the behaviour", "PROBLEM STATEMENT", "REPOSITORY MAP", "- pkg/mod.py",
                       "CURRENT SOURCE OF `pkg/mod.py`", "return x + 2", "PROBE RULES"):
            self.assertIn(needle, ctx)
        self.assertIn("does not exist", runner._get_focused_module_context("pkg/nope.py", "obj"))

    def test_gatekeeper_is_built_in_executor_mode(self):
        runner, _ = self.make_runner()
        runner.test_command = "exit 0"
        gk = runner._make_gatekeeper()
        self.assertIs(gk.executor, self.ex)
        self.assertEqual(gk.repo_root, self.root)
        self.assertEqual(gk.python, sys.executable)
        self.assertEqual(gk.test_command, "exit 0")
        self.assertGreaterEqual(gk.timeout_s, R.PROBE_TIMEOUT_MIN_S)

    def test_environment_preflight_reports_import_health(self):
        runner, _ = self.make_runner()
        buf = io.StringIO()
        with redirect_stdout(buf):
            out = runner.run_swebench(budget_moves=2, max_iterations=1)
        pre = out["swebench"]["preflight"]
        self.assertTrue(pre["ok"], pre)
        self.assertEqual(pre["package"], "pkg")        # root module's top-level package, tried first
        # The module gate's `repo_tests` mode is calibrated here, before the first LLM call.
        repo_tests = pre["repo_tests"]
        has_pytest = subprocess.run([sys.executable, "-c", "import pytest"], capture_output=True).returncode == 0
        if has_pytest:
            self.assertEqual(repo_tests["mode"], "whole_suite", repo_tests)
            self.assertEqual((repo_tests["target"], repo_tests["baseline_failures"]), ("tests", 0))
            self.assertIn("repo_tests=whole_suite (tests/ in", buf.getvalue())
        else:
            self.assertEqual(repo_tests["mode"], "proximity", repo_tests)
            self.assertIn("pytest is not installed", repo_tests["reason"])
            self.assertIn("repo_tests=proximity", buf.getvalue())
        self.assertEqual(repo_tests["budget_s"], R.WHOLE_SUITE_MAX_S)
        # Budget 0 keeps the proximity selection regardless.
        runner0 = R.SweBenchCompanyRunner(genome(), task(), self.ex, whole_suite_max_s=0)
        runner0._execute_agent = FakeSystem1().execute
        out0 = runner0.run_swebench(budget_moves=2, max_iterations=1)
        self.assertEqual(out0["swebench"]["preflight"]["repo_tests"]["mode"], "proximity")
        # A package that exists but is broken is the finding; the search still runs and records it.
        _write(self.root, "pkg/__init__.py", "import numpy_does_not_exist_here  # noqa\n")
        runner2, _ = self.make_runner()
        out2 = runner2.run_swebench(budget_moves=2, max_iterations=1)
        pre2 = out2["swebench"]["preflight"]
        self.assertFalse(pre2["ok"], pre2)
        self.assertEqual(pre2["tried"][0], "pkg")
        self.assertIn("numpy_does_not_exist_here", pre2["error"])
        self.assertEqual(pre2["repo_tests"]["mode"], "proximity")
        self.assertIn("does not import", pre2["repo_tests"]["reason"])

    def test_whole_module_rewrite_is_capped_on_large_modules(self):
        class MisanchoredFake(FakeSystem1):
            """Plan whose anchor does not exist; no function rewrite; rung 3 would be next."""

            def execute(self, agent, prompt, context="", reserved=False, response_format=None, max_tokens=None):
                schema = (response_format or {}).get("json_schema", {}).get("name")
                if schema == "RepairPlanPacket":
                    hid = re.search(r'Set `hypothesis_id` to "([^"]+)"', prompt).group(1)
                    self.calls.append({"schema": schema})
                    return json.dumps({"packet": "REPAIR_PLAN", "hypothesis_id": hid, "function": "f",
                                       "rationale": "x", "old_lines": ["    return x + 99"], "new_lines": ["    return x + 1"]})
                return super().execute(agent, prompt, context, reserved, response_format, max_tokens)

        self.assertIsNone(R.HierarchicalCompanyRunner.module_rewrite_max_chars)
        self.assertEqual(R.SweBenchCompanyRunner.module_rewrite_max_chars, R.MODULE_REWRITE_MAX_CHARS)
        fake = MisanchoredFake()
        runner, _ = self.make_runner(fake=fake)
        runner.module_rewrite_max_chars = 10          # pkg/mod.py is 30 chars: above the cap
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            out = runner.run_swebench(budget_moves=12, max_iterations=1)
        self.assertIn("whole-module rewrite skipped", buf.getvalue())
        self.assertFalse(any(c.get("schema") == "tools" for c in fake.calls), "rung 3 must not run above the cap")
        self.assertEqual(out["prediction"]["model_patch"], "")
        # Below the cap the ladder still reaches the whole-module rewrite (the fake's tools call).
        fake2 = MisanchoredFake()
        runner2, _ = self.make_runner(fake=fake2)
        runner2.module_rewrite_max_chars = None
        with contextlib.redirect_stdout(io.StringIO()):
            runner2.run_swebench(budget_moves=12, max_iterations=1)
        self.assertTrue(any(c.get("schema") == "tools" for c in fake2.calls))

    def test_synthesis_target_resolves_collaborator_module_from_hypothesis(self):
        from hae.epistemic.ledger import EpistemicState
        runner, _ = self.make_runner()
        state = EpistemicState("firm")
        runner.epistemic_state = state
        q = state.add_question(text="Reproduce the failure via pkg/other.py", module="pkg/other.py", uncertainty=1.0)
        h = state.add_hypothesis(
            question_id=q.question_id,
            claim="Calling f(1) returns 3 because pkg/mod.py adds 2 instead of 1.",
            mechanism="In `pkg/mod.py`, `f` returns `x + 2`.",
            prior=0.8,
            probe_code=REPRO,
            prediction={"expect_exit_code": 1, "expect_stdout_contains": "F 3"},
            proposed_by="Lead Implementation Engineer",
        )
        self.assertEqual(runner._synthesis_target(q, h), "pkg/mod.py")
        synthesize = runner._synthesize_patch_adapter(runner.genome.departments[0].manager, runner.objective_text())
        res = synthesize(q, h, state)
        self.assertEqual(res["path"], "pkg/mod.py")
        self.assertIn("target resolved from hypothesis to pkg/mod.py (question module was pkg/other.py)", res["notes"])
        with open(os.path.join(self.root, "pkg", "mod.py")) as fh:
            self.assertEqual(fh.read(), FIXED)
        with open(os.path.join(self.root, "pkg", "other.py")) as fh:
            self.assertEqual(fh.read(), "X = 1\n")


class DynamicOrganizationTests(RunnerFixture):
    """With `ceo_policy.enabled` the SWE-bench loop is staffed from the role library, not the departments."""

    def org_genome(self):
        from hae.genome.role_seeds import seed_role_library
        from hae.genome.schema import CEOPolicyGene
        g = genome()
        g.company_id = "swe_org_firm"
        g.ceo_policy = CEOPolicyGene(enabled=True, min_initial_roles=2, max_initial_roles=3, max_active_roles=5,
                                     recruit_mode="library", stall_moves=1, recruit_bias=2.0)
        g.role_library = seed_role_library("swebench")
        return g

    def test_loop_is_staffed_from_the_library_and_the_org_is_recorded(self):
        fake = FakeSystem1()
        runner = R.SweBenchCompanyRunner(self.org_genome(), task(), self.ex)
        runner._execute_agent = fake.execute
        runner._execute_agent_with_tools = fake.execute_with_tools
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            out = runner.run_swebench(budget_moves=12, max_iterations=3)
        logs = buf.getvalue()
        self.assertIn("[org] swe_org_firm: turn-0 team=", logs)
        self.assertIn("[org] swe_org_firm iteration 1: team=", logs)
        self.assertTrue(out["swebench"]["resolved_by_self_oracle"], out["swebench"])
        for key in ("org", "org_audit", "org_history"):
            self.assertIn(key, out)
        team = [r["role_id"] for r in out["org"]["active_roles"]]
        self.assertTrue(2 <= len(team) <= 5, team)
        self.assertTrue(all(rid.endswith("_swebench") for rid in team), team)
        self.assertEqual(out["org_audit"]["library_source"], "genome")
        self.assertEqual(out["org_audit"]["library_size"], len(runner.genome.role_library))
        # Moves were routed to (and credited against) library roles, not the department agents.
        moves = out["epistemic_search"]["trajectory"]
        routed = {m.get("role_id") for m in moves if m.get("role_id")}
        self.assertTrue(routed, moves[:3])
        self.assertTrue(routed <= set(team), (routed, team))
        self.assertIn("org", out["epistemic_search"])
        self.assertIn("org", out["swebench"]["iterations"][0])
        first = [r["role_id"] for r in out["org_history"][0]["org"]["active_roles"]]
        self.assertEqual(out["swebench"]["iterations"][0]["org"]["team"], first)
        self.assertIsNotNone(runner.org_state)

    def test_disabled_policy_keeps_static_bindings(self):
        fake = FakeSystem1()
        runner = R.SweBenchCompanyRunner(genome(), task(), self.ex)
        runner._execute_agent = fake.execute
        runner._execute_agent_with_tools = fake.execute_with_tools
        out = runner.run_swebench(budget_moves=12, max_iterations=2)
        self.assertNotIn("org", out)
        self.assertNotIn("org", out["swebench"]["iterations"][0])
        self.assertIsNone(getattr(runner, "org_state", None))

    def test_run_instance_persists_the_org_trajectory(self):
        ds_path = os.path.join(self.tmp.name, "dev.jsonl")
        write_jsonl(ds_path, [{"instance_id": "demo__pkg-1", "repo": "demo/pkg", "base_commit": "deadbeef",
                               "version": "1.0", "problem_statement": STATEMENT, "created_at": "", "patch": "GOLD",
                               "test_patch": "GOLDTEST", "FAIL_TO_PASS": "[\"t\"]", "PASS_TO_PASS": "[]",
                               "environment_setup_commit": "", "hints_text": ""}])
        out_dir = os.path.join(self.tmp.name, "out")
        fake = FakeSystem1()
        orig_init = R.SweBenchCompanyRunner.__init__

        def patched_init(inner, *a, **kw):
            orig_init(inner, *a, **kw)
            inner._execute_agent = fake.execute
            inner._execute_agent_with_tools = fake.execute_with_tools

        R.SweBenchCompanyRunner.__init__ = patched_init
        try:
            with open(os.devnull, "w") as sink:
                import contextlib
                with contextlib.redirect_stdout(sink):
                    R.run_instance("demo__pkg-1", ds_path, self.ex, self.org_genome(), out_dir, budget_moves=12)
        finally:
            R.SweBenchCompanyRunner.__init__ = orig_init
        with open(os.path.join(out_dir, "record.json")) as fh:
            rec = json.load(fh)
        self.assertIn("org", rec)
        self.assertIn("org_history", rec)
        self.assertEqual(len(rec["org_history"]), len(rec["swebench"]["iterations"]))
        ids = lambda org: [r["role_id"] for r in org["active_roles"]]  # noqa: E731
        self.assertEqual(ids(rec["org"]), ids(rec["org_history"][-1]["org"]))
        self.assertTrue(ids(rec["org"]))

class WorkspaceTests(RunnerFixture):
    def test_writes_go_through_executor_without_monotonic_guard(self):
        ws = R.ExecutorWorkspace(self.ex, lambda: ["pkg/mod.py"])
        # AgentWorkspace would refuse a <100-char body over a valid module; here it is a plain write.
        res = ws.write_file("pkg/mod.py", "X = 1\n")
        self.assertEqual(res["status"], "ok")
        self.assertEqual(self.ex.read_file("pkg/mod.py"), "X = 1\n")
        self.assertEqual(ws.read_file("pkg/mod.py")["content"], "X = 1\n")
        self.assertEqual(ws.read_file("missing.py")["status"], "error")
        self.assertEqual(len(ws.writes), 1)

    def test_path_hygiene_and_large_module_refusal(self):
        ws = R.ExecutorWorkspace(self.ex, lambda: [])
        self.assertEqual(ws.write_file("../escape.py", "x")["status"], "error")
        self.assertEqual(ws.write_file("/abs/x.py", "x")["status"], "error")
        big = "# " + "x" * R.MAX_EDITABLE_MODULE_CHARS + "\n"
        self.ex.write_file("pkg/big.py", big)
        self.assertEqual(ws.write_file("pkg/big.py", "small = 1\n")["status"], "error")
        self.assertEqual(self.ex.read_file("pkg/big.py"), big)
        self.assertEqual(len(ws.refused), 3)

    def test_bundle_and_tree_and_bash(self):
        ws = R.ExecutorWorkspace(self.ex, lambda: ["pkg/mod.py", "pkg/other.py"])
        self.assertEqual(ws.export_bundle(), {})
        ws.write_file("pkg/mod.py", FIXED)
        self.ex.write_file(".hae/probe_h1.py", "print(1)\n")
        self.assertEqual(ws.export_bundle(), {"pkg/mod.py": FIXED})
        self.assertIn("- pkg/other.py", ws.get_file_tree())
        res = ws.execute_bash("echo hi && exit 3")
        self.assertEqual(res["exit_code"], 3)
        self.assertEqual(res["stdout"].strip(), "hi")
        self.assertEqual(R._NoVerificationLoop().summary()["mode"], "swebench")


class RunInstanceTests(RunnerFixture):
    def test_run_instance_writes_outputs(self):
        ds_path = os.path.join(self.tmp.name, "dev.jsonl")
        write_jsonl(ds_path, [{"instance_id": "demo__pkg-1", "repo": "demo/pkg", "base_commit": "deadbeef",
                               "version": "1.0", "problem_statement": STATEMENT, "created_at": "", "patch": "GOLD",
                               "test_patch": "GOLDTEST", "FAIL_TO_PASS": "[\"t\"]", "PASS_TO_PASS": "[]",
                               "environment_setup_commit": "", "hints_text": "the fix is x + 1"}])
        out_dir = os.path.join(self.tmp.name, "out")
        fake = FakeSystem1()
        orig_init = R.SweBenchCompanyRunner.__init__

        def patched_init(inner, *a, **kw):
            orig_init(inner, *a, **kw)
            inner._execute_agent = fake.execute
            inner._execute_agent_with_tools = fake.execute_with_tools

        R.SweBenchCompanyRunner.__init__ = patched_init
        try:
            record = R.run_instance("demo__pkg-1", ds_path, self.ex, genome(), out_dir, budget_moves=12)
        finally:
            R.SweBenchCompanyRunner.__init__ = orig_init
        for name in ("prediction.json", "record.json", "firm.log"):
            self.assertTrue(os.path.exists(os.path.join(out_dir, name)), name)
        with open(os.path.join(out_dir, "prediction.json")) as fh:
            pred = json.load(fh)
        self.assertEqual(sorted(pred), ["instance_id", "model_name_or_path", "model_patch"])
        self.assertIn("+    return x + 1", pred["model_patch"])
        with open(os.path.join(out_dir, "record.json")) as fh:
            rec = json.load(fh)
        for key in ("swebench", "epistemic_ledger", "epistemic_searches", "token_usage", "opex", "epistemic_audit"):
            self.assertIn(key, rec)
        self.assertEqual(rec["task"]["hints_text"], "")           # hints excluded by default
        self.assertNotIn("patch", rec["task"])                     # gold never travels with the task
        self.assertTrue(rec["swebench"]["resolved_by_self_oracle"])
        self.assertEqual(record["instance_id"], "demo__pkg-1")
        with open(os.path.join(out_dir, "firm.log")) as fh:
            self.assertIn("[swebench]", fh.read())


class ExecutorFactoryTests(unittest.TestCase):
    def test_make_executor(self):
        self.assertEqual(R.make_executor("docker", container="c").describe(), "docker:c:/testbed")
        self.assertEqual(R.make_executor("kubectl", pod="p", container="t", namespace="n").describe(),
                         "kubectl:n/p/t:/testbed")
        self.assertEqual(R.make_executor("local", root="/tmp").root, "/tmp")
        with self.assertRaises(ValueError):
            R.make_executor("docker")
        with self.assertRaises(ValueError):
            R.make_executor("local")
        with self.assertRaises(ValueError):
            R.make_executor("nope", root="/tmp")


if __name__ == "__main__":
    unittest.main()
