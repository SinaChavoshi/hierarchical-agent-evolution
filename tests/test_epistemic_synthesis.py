"""Gen 16 pilot finding #5 / cohort A4: synthesis is a ladder of targeted edits, and a no-op is said out loud.

`crossover_2` spent 7 of 9 synthesis moves re-emitting the module byte for
byte because the synthesiser was asked for a complete rewrite. The adapter
now climbs a ladder, cheapest rung first: (1) a REPAIR_PLAN -- the exact
lines to replace -- applied by the runner when the anchor is unique; (2) a
FUNCTION_REWRITE -- one definition, replaced whole -- spliced in by the
runner; (3) the whole-module rewrite, run at most ONCE and skipped entirely
when rungs 1 and 2 were both no-ops (Gen 16 cohort run 2: the 28 moves that
wrote nothing cost 17 min each, all of them a no-op plan or a missed anchor
followed by an identical rewrite and an identical retry). Everything below
`call_llm` is real.
"""

import json
import os
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
ADD_FIXED_LINES = [
    "def add(a, b):",
    '    """Return the sum of a and b. Repaired: the operator was `-`."""',
    "    return a + b",
]
ADD_CURRENT_LINES = [
    "def add(a, b):",
    '    """Return the sum of a and b. (The implementation below is wrong on purpose:',
    '    it subtracts, which is the mechanism the true hypothesis names.)"""',
    "    return a - b",
]
FUNCTION_OK = json.dumps({
    "packet": "FUNCTION_REWRITE", "hypothesis_id": "h1", "function": "add",
    "rationale": "plus instead of minus", "new_source": ADD_FIXED_LINES, "add_imports": [],
})
FUNCTION_NOOP = json.dumps({
    "packet": "FUNCTION_REWRITE", "hypothesis_id": "h1", "function": "add",
    "rationale": "nothing to change", "new_source": ADD_CURRENT_LINES, "add_imports": [],
})
FUNCTION_UNKNOWN = json.dumps({
    "packet": "FUNCTION_REWRITE", "hypothesis_id": "h1", "function": "plus",
    "rationale": "x", "new_source": ["def plus(a, b):", "    return a + b"], "add_imports": [],
})
FUNCTION_BROKEN = json.dumps({
    "packet": "FUNCTION_REWRITE", "hypothesis_id": "h1", "function": "add",
    "rationale": "x", "new_source": ["def add(a, b)", "    return a + b"], "add_imports": [],
})


def write_action(content):
    return "Action: write_file\nPath: mypkg/calc.py\n```python\n" + content + "```\n"


class ScriptedLLM:
    """Routes the adapter's prompts (plan, function rewrite, rewrite, author retry) to scripted replies."""

    def __init__(self, plan=None, function=None, rewrite=None, retry=None):
        self.plan, self.function, self.rewrite, self.retry = plan, function, rewrite, retry
        self.prompts = []
        self.prompt_text = {}
        self.last_prompt = ""
        self.retry_prompt = ""

    def __call__(self, prompt, model_name, temperature, system_instruction,
                 usage_sink=None, response_format=None, max_tokens=None, **_):
        if usage_sink is not None:
            usage_sink.update({"measured": True, "prompt_tokens": 500, "output_tokens": 100})
        self.last_prompt = prompt
        if "REPAIR_PLAN packet" in prompt:
            self.prompts.append("plan")
            self.prompt_text["plan"] = prompt
            return self.plan if self.plan is not None else "I cannot plan."
        if "FUNCTION_REWRITE packet" in prompt:
            self.prompts.append("function")
            self.prompt_text["function"] = prompt
            self.schema = response_format
            self.function_max_tokens = max_tokens
            return self.function if self.function is not None else "I cannot rewrite one function."
        if "SECOND ATTEMPT" in prompt:
            self.prompts.append("retry")
            self.retry_prompt = prompt
            assert "Your previous attempt" in prompt
            return self.retry if self.retry is not None else write_action(BUGGY)
        if "SYNTHESIZE" in prompt:
            self.prompts.append("rewrite")
            self.prompt_text["rewrite"] = prompt
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
        self.assertEqual(llm.prompts, ["plan"])             # no function rewrite, no tool-loop rewrite
        self.assertIn("return a + b", self.module())
        self.assertIn("def scale", self.module())             # the rest of the module is untouched
        self.assertNotIn("return a - b", self.module())
        self.assertIsInstance(res["elapsed_s"], float)
        self.assertEqual(res["previous_source"], BUGGY)

    def test_unusable_plan_and_unusable_function_fall_back_to_one_anchored_rewrite(self):
        llm = ScriptedLLM(plan=PLAN_BAD_ANCHOR, function=None, rewrite=write_action(FIXED))
        res = self.synthesize(llm)
        self.assertTrue(res["written"])
        self.assertEqual(res["mode"], "rewrite")
        self.assertEqual(llm.prompts, ["plan", "function", "rewrite"])   # the rungs, in order, once each
        self.assertTrue(any(n.startswith("plan not applicable: anchor not found; closest is lines") for n in res["notes"]))
        self.assertIn("no usable function rewrite", res["notes"])
        self.assertEqual(self.module(), FIXED.rstrip("\n"))

    def test_noop_plan_is_refused_before_it_costs_a_write(self):
        llm = ScriptedLLM(plan=PLAN_NOOP, function=None, rewrite=write_action(FIXED))
        res = self.synthesize(llm)
        self.assertEqual(res["mode"], "rewrite")          # the function rung was unusable, not a no-op
        self.assertTrue(any("plan is a no-op" in n for n in res["notes"]))

    def test_double_noop_skips_the_whole_module_rewrite(self):
        # Gen 16 cohort run 2: a no-op plan followed by an identical rewrite
        # (and an identical retry) cost 17 min a move. Two cheap no-ops now
        # end the move before the expensive one starts.
        llm = ScriptedLLM(plan=PLAN_NOOP, function=FUNCTION_NOOP, rewrite=write_action(FIXED))
        res = self.synthesize(llm)
        self.assertFalse(res["written"])
        self.assertEqual(res["mode"], "none")
        self.assertTrue(res["rewrite_skipped"])
        self.assertFalse(res["retried"])
        self.assertEqual(res["summary"],
                         "model sees no change to make (plan and function rewrite both no-ops); whole-module rewrite skipped")
        self.assertEqual(llm.prompts, ["plan", "function"])          # no tool-loop call at all
        self.assertTrue(any("plan not applicable: plan is a no-op" in n for n in res["notes"]))
        self.assertTrue(any("function rewrite not applicable: new source identical" in n for n in res["notes"]))
        self.assertIsInstance(res["elapsed_s"], float)
        self.assertEqual(res["previous_source"], BUGGY)
        self.assertEqual(self.module(), BUGGY.rstrip("\n"))

    def test_whitespace_only_plan_counts_as_a_noop_for_the_skip_rule(self):
        # The workspace accepts the write but the whitespace-normalised
        # fingerprint does not move: that is a no-op in substance.
        plan_ws = json.dumps({
            "packet": "REPAIR_PLAN", "hypothesis_id": "h1", "function": "add", "rationale": "x",
            "old_lines": ["    return a - b"], "new_lines": ["    return a - b   "],
        })
        llm = ScriptedLLM(plan=plan_ws, function=FUNCTION_NOOP, rewrite=write_action(FIXED))
        res = self.synthesize(llm)
        self.assertFalse(res["written"])
        self.assertTrue(res["rewrite_skipped"])
        self.assertEqual(llm.prompts, ["plan", "function"])
        self.assertIn("plan write changed nothing (whitespace-only edit)", res["notes"])
        self.assertIn("could not be applied mechanically: plan write changed nothing", llm.prompt_text["function"])

    def test_missed_anchor_then_function_rewrite_writes_the_function(self):
        llm = ScriptedLLM(plan=PLAN_BAD_ANCHOR, function=FUNCTION_OK)
        res = self.synthesize(llm)
        self.assertTrue(res["written"])
        self.assertEqual(res["mode"], "function")
        self.assertEqual(llm.prompts, ["plan", "function"])          # no tool-loop rewrite was needed
        self.assertIn("function rewrite of `add`", res["summary"])
        self.assertIn("rewrote `add`", res["summary"])
        self.assertIn("return a + b", self.module())
        self.assertNotIn("return a - b", self.module())
        self.assertIn("def scale", self.module())
        # The function prompt told the model why the plan failed and where it
        # was aiming, quoted the candidate definition, and the grammar was
        # built from this module's definitions.
        fn_prompt = llm.prompt_text["function"]
        self.assertIn("could not be applied mechanically: anchor not found; closest is lines", fn_prompt)
        self.assertIn("CURRENT SOURCE of the candidate definition(s)", fn_prompt)
        self.assertIn("`add` (lines", fn_prompt)
        self.assertIn("The module defines: add, scale", fn_prompt)
        schema = llm.schema["json_schema"]["schema"]["properties"]["function"]
        self.assertEqual(schema["enum"], ["add", "scale"])
        self.assertEqual(llm.function_max_tokens, 4000)

    def test_function_not_found_runs_exactly_one_rewrite_anchored_by_the_packet(self):
        llm = ScriptedLLM(plan=None, function=FUNCTION_UNKNOWN, rewrite=write_action(FIXED))
        res = self.synthesize(llm)
        self.assertTrue(res["written"])
        self.assertEqual(res["mode"], "rewrite")
        self.assertEqual(llm.prompts, ["plan", "function", "rewrite"])
        self.assertTrue(any("function not found: plus; module defines: add, scale" in n for n in res["notes"]))
        self.assertIn("Your own rewrite of `plus`", llm.prompt_text["rewrite"])
        self.assertIn("def plus(a, b):", llm.prompt_text["rewrite"])

    def test_non_parsing_function_rewrite_is_refused_and_one_rewrite_follows(self):
        llm = ScriptedLLM(plan=None, function=FUNCTION_BROKEN, rewrite=write_action(FIXED))
        res = self.synthesize(llm)
        self.assertEqual(res["mode"], "rewrite")
        self.assertEqual(llm.prompts, ["plan", "function", "rewrite"])
        self.assertTrue(any("replacement does not parse" in n for n in res["notes"]))

    def test_noop_rewrite_gets_no_second_attempt(self):
        # The retry recovered 5 of 33 no-ops in run 2 (8 of 35 in run 1) at
        # 6-7 min a generation; it is gone for existing modules.
        llm = ScriptedLLM(plan=None, function=None, rewrite=write_action(BUGGY), retry=write_action(FIXED))
        res = self.synthesize(llm)
        self.assertFalse(res["written"])
        self.assertEqual(res["mode"], "none")
        self.assertFalse(res["retried"])
        self.assertFalse(res["noop_recovered"])
        self.assertFalse(res.get("rewrite_skipped", False))
        self.assertIn("rewrite re-emitted identical content; no second attempt", res["summary"])
        self.assertIn("rewrite re-emitted identical content; no second attempt", res["notes"])
        self.assertEqual(llm.prompts, ["plan", "function", "rewrite"])   # exactly one rewrite, never a retry
        self.assertEqual(self.module(), BUGGY.rstrip("\n"))
        self.assertEqual(res["previous_source"], BUGGY)

    def test_a_missing_module_is_authored_not_repaired(self):
        # Gen 16 cohort finding #7: a first pass that never wrote the module.
        # There is no source to plan against, so the move must create the
        # file, say so, and never pretend it repaired something.
        os.remove(os.path.join(self.runner.workspace.path, "mypkg", "calc.py"))
        llm = ScriptedLLM(plan=PLAN_OK, rewrite=write_action(FIXED))
        res = self.synthesize(llm)
        self.assertTrue(res["written"])
        self.assertEqual(res["mode"], "author")
        self.assertEqual(llm.prompts, ["rewrite"])                   # no REPAIR_PLAN step without a source
        self.assertTrue(any("target module absent" in n for n in res["notes"]))
        self.assertIn("return a + b", self.module())
        # The authoring prompt said what it was doing.
        self.assertIn("does NOT exist in the workspace", llm.last_prompt)

    def test_authoring_that_writes_nothing_is_reported_honestly(self):
        os.remove(os.path.join(self.runner.workspace.path, "mypkg", "calc.py"))
        llm = ScriptedLLM(plan=None, rewrite="I will think about it.", retry="Still thinking.")
        res = self.synthesize(llm)
        self.assertFalse(res["written"])
        self.assertTrue(res["retried"])
        self.assertIn("did not create the file", res["summary"])
        self.assertEqual(llm.prompts, ["rewrite", "retry"])
        self.assertIn("did not create", llm.retry_prompt)

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
