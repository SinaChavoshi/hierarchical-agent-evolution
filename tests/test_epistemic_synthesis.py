"""Gen 16 pilot finding #5: synthesis is an anchored change, and a no-op is said out loud.

`crossover_2` spent 7 of 9 synthesis moves re-emitting the module byte for
byte because the synthesiser was asked for a complete rewrite. The adapter
now (1) asks for a REPAIR_PLAN -- the exact lines to replace -- and applies it
itself when the anchor is unique, (2) falls back to the rewrite with the plan
as its anchor, and (3) re-prompts once, inside the same move, when the module
hash did not move, telling the synthesiser that its previous attempt changed
nothing. Everything below `call_llm` is real.
"""

import json
import shutil
import tempfile
import unittest
from unittest import mock

from hae.epistemic.gatekeeper import EvidenceGatekeeper
from hae.epistemic.ledger import EpistemicState
from hae.epistemic.moves import RepairPlan, apply_repair_plan, parse_repair_plan
from hae.evaluation.verification_loop import VerificationLoop
from hae.genome.schema import AgentGenome
from hae.runtime.company import SUITE_TAG_TO_MODULE, HierarchicalCompanyRunner
from hae.runtime.workspace import AgentWorkspace
from tests.test_epistemic_runner import BUGGY, FIXED, OBJECTIVE, _genome

PLAN_OK = json.dumps({
    "packet": "REPAIR_PLAN", "hypothesis_id": "h1", "function": "add",
    "rationale": "the operator is minus; the oracle expects a sum",
    "old_lines": ["    return a - b"], "new_lines": ["    return a + b"],
})
PLAN_NOOP = json.dumps({
    "packet": "REPAIR_PLAN", "hypothesis_id": "h1", "function": "add", "rationale": "nothing",
    "old_lines": ["    return a - b"], "new_lines": ["    return a - b"],
})
PLAN_BAD_ANCHOR = json.dumps({
    "packet": "REPAIR_PLAN", "hypothesis_id": "h1", "function": "add", "rationale": "x",
    "old_lines": ["    return a * b"], "new_lines": ["    return a + b"],
})


def write_action(content):
    return "Action: write_file\nPath: mypkg/calc.py\n```python\n" + content + "```\n"


class ScriptedLLM:
    """Routes the adapter's three prompts to scripted replies and records them."""

    def __init__(self, plan=None, rewrite=None, retry=None):
        self.plan, self.rewrite, self.retry = plan, rewrite, retry
        self.prompts = []

    def __call__(self, prompt, model_name, temperature, system_instruction,
                 usage_sink=None, response_format=None, max_tokens=None, **_):
        if usage_sink is not None:
            usage_sink.update({"measured": True, "prompt_tokens": 500, "output_tokens": 100})
        if "REPAIR_PLAN packet" in prompt:
            self.prompts.append("plan")
            return self.plan if self.plan is not None else "I cannot plan."
        if "SECOND ATTEMPT" in prompt:
            self.prompts.append("retry")
            assert "Your previous attempt changed nothing" in prompt
            return self.retry if self.retry is not None else write_action(BUGGY)
        if "SYNTHESIZE a repair" in prompt:
            self.prompts.append("rewrite")
            return self.rewrite if self.rewrite is not None else write_action(BUGGY)
        self.prompts.append("other")
        return "{}"


class SynthesisAdapterTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hae_epi_synth_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.runner = HierarchicalCompanyRunner(_genome())
        self.runner.workspace = AgentWorkspace("epi_synth_firm", base_dir=self.tmp)
        self.runner.verification_loop = VerificationLoop(self.runner.workspace)
        self.runner.workspace.write_file("mypkg/__init__.py", "")
        self.runner.workspace.write_file("mypkg/calc.py", BUGGY)
        self.runner._init_required_modules_for_objective(OBJECTIVE)
        self.runner._make_gatekeeper = lambda policy=None: EvidenceGatekeeper(
            self.runner.workspace, timeout_s=20, max_probe_lines=40,
            graded_modules=self.runner._required_modules, module_for_tag=SUITE_TAG_TO_MODULE,
            isolate=False, stage_reference=False)
        self.state = EpistemicState("epi_synth_firm")
        self.q = self.state.add_question("Why does test_add fail in `mypkg/calc.py`?", module="mypkg/calc.py",
                                         source_failure="FAIL: test_add (t.T) -> AssertionError: -1 != 5")
        self.h = self.state.add_hypothesis(self.q.question_id, "add subtracts its operands",
                                           "minus instead of plus", 0.3,
                                           "from mypkg.calc import add\nprint(add(2, 3))\n",
                                           {"expect_exit_code": 0, "expect_stdout_contains": "-1"})
        self.agent = AgentGenome(role="Lead Implementation Engineer", goal="Fix code", backstory="Systems",
                                 temperature=0.2, model_tier="worker", tools_enabled=True)

    def synthesize(self, llm):
        adapter = self.runner._synthesize_patch_adapter(self.agent, OBJECTIVE)
        with mock.patch("hae.runtime.company.call_llm", llm):
            return adapter(self.q, self.h, self.state)

    def module(self):
        # The tool-loop fence parser drops the newline before the closing fence;
        # compare modulo trailing newlines (the fingerprint does the same).
        return self.runner.workspace.read_file("mypkg/calc.py")["content"].rstrip("\n")

    def test_anchored_plan_is_applied_by_the_runner_without_a_rewrite(self):
        llm = ScriptedLLM(plan=PLAN_OK)
        res = self.synthesize(llm)
        self.assertTrue(res["written"])
        self.assertEqual(res["mode"], "plan")
        self.assertFalse(res["retried"])
        self.assertIn("anchored edit in `add`", res["summary"])
        self.assertEqual(llm.prompts, ["plan"])             # no tool-loop rewrite was needed
        self.assertIn("return a + b", self.module())
        self.assertIn("def scale", self.module())             # the rest of the module is untouched
        self.assertNotIn("return a - b", self.module())

    def test_unusable_plan_falls_back_to_the_anchored_rewrite(self):
        llm = ScriptedLLM(plan=PLAN_BAD_ANCHOR, rewrite=write_action(FIXED))
        res = self.synthesize(llm)
        self.assertTrue(res["written"])
        self.assertEqual(res["mode"], "rewrite")
        self.assertEqual(llm.prompts, ["plan", "rewrite"])
        self.assertTrue(any("plan not applicable: anchor not found" in n for n in res["notes"]))
        self.assertEqual(self.module(), FIXED.rstrip("\n"))

    def test_noop_plan_is_refused_before_it_costs_a_write(self):
        llm = ScriptedLLM(plan=PLAN_NOOP, rewrite=write_action(FIXED))
        res = self.synthesize(llm)
        self.assertEqual(res["mode"], "rewrite")
        self.assertTrue(any("plan is a no-op" in n for n in res["notes"]))

    def test_silent_noop_rewrite_is_reprompted_once_and_can_recover(self):
        llm = ScriptedLLM(plan=None, rewrite=write_action(BUGGY), retry=write_action(FIXED))
        res = self.synthesize(llm)
        self.assertTrue(res["written"])
        self.assertTrue(res["retried"])
        self.assertTrue(res["noop_recovered"])
        self.assertIn("no-op (re-prompted, then changed)", res["summary"])
        self.assertEqual(llm.prompts, ["plan", "rewrite", "retry"])
        self.assertEqual(self.module(), FIXED.rstrip("\n"))

    def test_two_silent_noops_are_reported_as_a_failed_synthesis(self):
        llm = ScriptedLLM(plan=None, rewrite=write_action(BUGGY), retry=write_action(BUGGY))
        res = self.synthesize(llm)
        self.assertFalse(res["written"])
        self.assertTrue(res["retried"])
        self.assertFalse(res["noop_recovered"])
        self.assertIn("no-op (re-prompted, gave up)", res["summary"])
        self.assertEqual(llm.prompts, ["plan", "rewrite", "retry"])   # exactly one retry, never more
        self.assertEqual(self.module(), BUGGY.rstrip("\n"))

    def test_search_loop_counts_plan_and_noop_telemetry(self):
        from hae.epistemic.mcts import EpistemicSearchLoop
        from hae.epistemic.value import EpistemicValueFunction
        from hae.genome.schema import EpistemicPolicyGene
        from hae.epistemic.ledger import SUPPORTED
        gk = self.runner._make_gatekeeper()
        # Make the hypothesis SUPPORTED through the gatekeeper so the loop will synthesise it.
        evidence = gk.run_experiment(self.state, self.h)
        gk.apply(self.state, self.h, evidence)
        self.assertEqual(self.h.status, SUPPORTED)
        calls = {"n": 0}

        def synth(q, h, s):
            calls["n"] += 1
            if calls["n"] == 1:
                return {"written": False, "path": "mypkg/calc.py", "mode": "none", "retried": True,
                        "noop_recovered": False, "summary": "no-op (re-prompted, gave up)"}
            self.runner.workspace.write_file("mypkg/calc.py", FIXED)
            return {"written": True, "path": "mypkg/calc.py", "mode": "plan", "summary": "anchored edit"}
        policy = EpistemicPolicyGene(enabled=True, min_hypotheses_before_synthesis=1, search_budget_moves=10)
        loop = EpistemicSearchLoop(self.state, gk, EpistemicValueFunction(), policy,
                                   lambda q, s, k: [], synth, logger=lambda m: None)
        res = loop.run()
        self.assertEqual(res.stats["syntheses"], 2)
        self.assertEqual(res.stats["synthesis_noop_retries"], 1)
        self.assertEqual(res.stats.get("synthesis_noop_recoveries", 0), 0)
        self.assertEqual(res.stats["syntheses_by_plan"], 1)
        self.assertEqual(res.stats["syntheses_verified"], 1)


class RepairPlanTests(unittest.TestCase):
    SRC = "def add(a, b):\n    return a - b\n\n\ndef sub(a, b):\n    return a - b\n"

    def test_exact_anchor(self):
        plan = RepairPlan("add", "r", ["def add(a, b):", "    return a - b"], ["def add(a, b):", "    return a + b"])
        new, note = apply_repair_plan(self.SRC, plan)
        self.assertEqual(new, "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n")
        self.assertIn("exact match", note)

    def test_ambiguous_anchor_is_refused(self):
        new, note = apply_repair_plan(self.SRC, RepairPlan("add", "r", ["    return a - b"], ["    return a + b"]))
        self.assertIsNone(new)
        self.assertIn("ambiguous", note)

    def test_missing_anchor_is_refused(self):
        new, note = apply_repair_plan(self.SRC, RepairPlan("add", "r", ["    return a * b"], ["    return a + b"]))
        self.assertIsNone(new)
        self.assertIn("not found", note)

    def test_trailing_whitespace_and_indentation_are_tolerated(self):
        src = "class C:\n    def add(self, a, b):\n        return a - b   \n"
        plan = RepairPlan("C.add", "r", ["def add(self, a, b):", "    return a - b"],
                          ["def add(self, a, b):", "    return a + b"])
        new, note = apply_repair_plan(src, plan)
        self.assertEqual(new, "class C:\n    def add(self, a, b):\n        return a + b\n")
        self.assertIn("dedent match", note)
        rstrip_plan = RepairPlan("C.add", "r", ["        return a - b"], ["        return a + b"])
        new2, note2 = apply_repair_plan(src, rstrip_plan)
        self.assertIn("rstrip match", note2)
        self.assertIn("return a + b", new2)

    def test_noop_plan_is_refused(self):
        new, note = apply_repair_plan(self.SRC, RepairPlan("add", "r", ["def add(a, b):"], ["def add(a, b):"]))
        self.assertIsNone(new)
        self.assertIn("no-op", note)

    def test_parse_requires_line_arrays(self):
        self.assertIsNone(parse_repair_plan("no json here"))
        self.assertIsNone(parse_repair_plan(json.dumps({"packet": "REPAIR_PLAN", "old_lines": "x", "new_lines": []})))
        self.assertIsNone(parse_repair_plan(json.dumps({"old_lines": ["   "], "new_lines": ["y"]})))
        plan = parse_repair_plan("```json\n" + PLAN_OK + "\n```")
        self.assertEqual(plan.function, "add")
        self.assertEqual(plan.old_lines, ["    return a - b"])
        self.assertEqual(plan.hypothesis_id, "h1")


if __name__ == "__main__":
    unittest.main()
