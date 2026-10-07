"""A4, the synthesis ladder (Gen 16 cohort run 2): plan -> function rewrite -> module rewrite.

Of 151 synthesis moves in run 2, 44 were written by the anchored plan, 5 by
authoring a missing module and 102 fell through to the whole-module rewrite;
the 28 moves that wrote nothing cost 17 min each (max 43.6) -- a no-op plan
or a missed anchor, then a rewrite that re-emitted the module, then a retry
that did the same. This file tests the two new pieces below the adapter: the
fuzzy anchor strategy of `apply_repair_plan`, and the FUNCTION_REWRITE packet
(`list_definitions`, `apply_function_rewrite`, its schema and parser); and
the search loop's side of the ladder: the new counters, the `[mode]` in the
move note, and the revert of a synthesis whose module check failed. The
adapter's policy itself is tested in `tests/test_epistemic_synthesis.py`.
"""

import ast
import json
import shutil
import tempfile
import unittest

from hae.epistemic.gatekeeper import EvidenceGatekeeper
from hae.epistemic.ledger import SUPPORTED, EpistemicState
from hae.epistemic.mcts import EpistemicSearchLoop
from hae.epistemic.moves import (
    FUZZY_ANCHOR_MIN_RATIO, MAX_FUNCTION_ENUM, V6_FUNCTION_REWRITE_SCHEMA, FunctionRewrite, RepairPlan,
    apply_function_rewrite, apply_repair_plan, function_rewrite_schema, list_definitions,
    parse_function_rewrite,
)
from hae.epistemic.value import EpistemicValueFunction
from hae.genome.schema import EpistemicPolicyGene
from hae.runtime.workspace import AgentWorkspace

MODULE = '''"""A small module with every kind of definition the lister must see."""

import os
from typing import List


def add(a, b):
    """Return the sum of a and b."""
    return a - b


@staticmethod
@functools.lru_cache(maxsize=8)
def decorated(x):
    return x


class Calc:
    """A class with methods, a decorated method, an async method and a nested class."""

    factor = 2

    def scale(self, x):
        return x * self.factor

    @property
    def doubled(self):
        return self.factor * 2

    async def fetch(self, key):
        return key

    class Inner:
        def ping(self):
            return "pong"


async def main():
    return await Calc().fetch("k")
'''


class ListDefinitionsTests(unittest.TestCase):

    def test_lists_functions_classes_methods_async_and_decorators(self):
        defs = list_definitions(MODULE)
        by_name = {name: (start, end) for name, start, end in defs}
        self.assertEqual([d[0] for d in defs],
                         ["add", "decorated", "Calc", "Calc.scale", "Calc.doubled", "Calc.fetch",
                          "Calc.Inner", "Calc.Inner.ping", "main"])
        lines = MODULE.split("\n")
        self.assertEqual(lines[by_name["add"][0] - 1], "def add(a, b):")
        self.assertEqual(lines[by_name["add"][1] - 1], "    return a - b")
        # A decorated definition starts at its first decorator line.
        self.assertEqual(lines[by_name["decorated"][0] - 1], "@staticmethod")
        self.assertEqual(lines[by_name["Calc.doubled"][0] - 1], "    @property")
        self.assertEqual(lines[by_name["Calc.fetch"][0] - 1], "    async def fetch(self, key):")
        self.assertEqual(lines[by_name["main"][0] - 1], "async def main():")
        # The class spans all of its members.
        self.assertLessEqual(by_name["Calc"][0], by_name["Calc.scale"][0])
        self.assertGreaterEqual(by_name["Calc"][1], by_name["Calc.Inner.ping"][1])

    def test_unparseable_module_lists_nothing(self):
        self.assertEqual(list_definitions("def broken(:\n    pass\n"), [])
        self.assertEqual(list_definitions(""), [])


class ApplyFunctionRewriteTests(unittest.TestCase):

    def assertParses(self, source):
        ast.parse(source)

    def test_top_level_def_is_replaced_in_place(self):
        new, note = apply_function_rewrite(MODULE, FunctionRewrite(
            "add", "plus", ["def add(a, b):", '    """Return the sum of a and b."""', "    return a + b"]))
        self.assertIsNotNone(new, note)
        self.assertParses(new)
        self.assertIn("    return a + b\n", new)
        self.assertNotIn("return a - b", new)
        self.assertIn("rewrote `add`", note)
        # Everything else is byte-identical.
        self.assertEqual(new.replace("return a + b", "return a - b"), MODULE)

    def test_method_is_reindented_to_the_class_body(self):
        # The packet quotes the method at column 0; it must land at the class body's indentation.
        new, note = apply_function_rewrite(MODULE, FunctionRewrite(
            "Calc.scale", "r", ["def scale(self, x):", "    if x is None:", "        return 0", "    return x * self.factor * 3"]))
        self.assertIsNotNone(new, note)
        self.assertParses(new)
        self.assertIn("    def scale(self, x):\n        if x is None:\n            return 0\n"
                      "        return x * self.factor * 3\n", new)
        # The rest of the class is intact and still a member of Calc.
        names = [d[0] for d in list_definitions(new)]
        self.assertEqual(names, [d[0] for d in list_definitions(MODULE)])

    def test_method_quoted_with_its_indentation_is_also_accepted(self):
        new, note = apply_function_rewrite(MODULE, FunctionRewrite(
            "Calc.scale", "r", ["    def scale(self, x):", "        return x * self.factor * 4"]))
        self.assertIsNotNone(new, note)
        self.assertIn("    def scale(self, x):\n        return x * self.factor * 4\n", new)
        self.assertParses(new)

    def test_bare_method_name_resolves_when_unique(self):
        new, note = apply_function_rewrite(MODULE, FunctionRewrite(
            "ping", "r", ["def ping(self):", '    return "PONG"']))
        self.assertIsNotNone(new, note)
        self.assertIn("rewrote `Calc.Inner.ping`", note)
        self.assertIn('            return "PONG"\n', new)
        self.assertParses(new)

    def test_decorated_def_is_replaced_including_its_decorators(self):
        new, note = apply_function_rewrite(MODULE, FunctionRewrite(
            "decorated", "r", ["@functools.lru_cache(maxsize=16)", "def decorated(x):", "    return x + 1"]))
        self.assertIsNotNone(new, note)
        self.assertParses(new)
        self.assertNotIn("@staticmethod\n@functools.lru_cache(maxsize=8)\ndef decorated", new)
        self.assertIn("\n\n@functools.lru_cache(maxsize=16)\ndef decorated(x):\n    return x + 1\n\n\nclass Calc:", new)
        # Replacing a decorated method keeps the class body's indentation for the decorator too.
        new2, note2 = apply_function_rewrite(MODULE, FunctionRewrite(
            "Calc.doubled", "r", ["@property", "def doubled(self):", "    return self.factor * 4"]))
        self.assertIsNotNone(new2, note2)
        self.assertIn("    @property\n    def doubled(self):\n        return self.factor * 4\n", new2)
        self.assertParses(new2)

    def test_add_imports_are_inserted_once_after_the_last_import_and_never_duplicated(self):
        rw = FunctionRewrite("add", "r", ["def add(a, b):", "    return math.fsum([a, b])"],
                             add_imports=["import math", "import os", "import math", "from typing import List"])
        new, note = apply_function_rewrite(MODULE, rw)
        self.assertIsNotNone(new, note)
        self.assertParses(new)
        lines = new.split("\n")
        self.assertEqual(lines[2:5], ["import os", "from typing import List", "import math"])
        self.assertEqual(new.count("import math"), 1)
        self.assertEqual(new.count("import os"), 1)
        self.assertIn("added import(s): import math", note)
        # Applying an identical packet to the result changes nothing: no second `import math`.
        again, note2 = apply_function_rewrite(new, rw)
        self.assertIsNone(again)
        self.assertEqual(note2, "new source identical to the current definition")

    def test_add_imports_go_after_the_docstring_when_there_are_no_imports(self):
        src = '"""Doc."""\n\n\ndef f():\n    return 1\n'
        new, note = apply_function_rewrite(src, FunctionRewrite("f", "r", ["def f():", "    return math.pi"],
                                                                add_imports=["import math", "not an import"]))
        self.assertIsNotNone(new, note)
        self.assertTrue(new.startswith('"""Doc."""\nimport math\n'), new)
        self.assertIn("ignored non-import add_imports: not an import", note)
        self.assertParses(new)

    def test_noop_is_refused(self):
        new, note = apply_function_rewrite(MODULE, FunctionRewrite(
            "add", "r", ["def add(a, b):", '    """Return the sum of a and b."""', "    return a - b   "]))
        self.assertIsNone(new)
        self.assertEqual(note, "new source identical to the current definition")

    def test_unknown_function_note_lists_the_definitions(self):
        new, note = apply_function_rewrite(MODULE, FunctionRewrite("plus", "r", ["def plus(a, b):", "    return a + b"]))
        self.assertIsNone(new)
        self.assertEqual(note, "function not found: plus; module defines: add, decorated, Calc, Calc.scale, "
                               "Calc.doubled, Calc.fetch, Calc.Inner, Calc.Inner.ping, main")

    def test_non_parsing_replacement_is_refused(self):
        new, note = apply_function_rewrite(MODULE, FunctionRewrite("add", "r", ["def add(a, b)", "    return a + b"]))
        self.assertIsNone(new)
        self.assertTrue(note.startswith("replacement does not parse:"), note)

    def test_wrong_name_or_kind_or_several_statements_are_refused(self):
        new, note = apply_function_rewrite(MODULE, FunctionRewrite("add", "r", ["def plus(a, b):", "    return a + b"]))
        self.assertIsNone(new)
        self.assertEqual(note, "replacement defines plus, not add")
        new, note = apply_function_rewrite(MODULE, FunctionRewrite("Calc", "r", ["def Calc():", "    pass"]))
        self.assertIsNone(new)
        self.assertEqual(note, "replacement is a def, but Calc is a class")
        new, note = apply_function_rewrite(MODULE, FunctionRewrite(
            "add", "r", ["import math", "def add(a, b):", "    return a + b"]))
        self.assertIsNone(new)
        self.assertEqual(note, "replacement must be a single def/class, got 2 statements")
        new, note = apply_function_rewrite(MODULE, FunctionRewrite("add", "r", ["x = 1"]))
        self.assertIsNone(new)
        self.assertEqual(note, "replacement is a Assign, not a def or class")

    def test_unparseable_module_is_refused_with_a_note(self):
        new, note = apply_function_rewrite("def broken(:\n    pass\n", FunctionRewrite("broken", "r", ["def broken():", "    pass"]))
        self.assertIsNone(new)
        self.assertTrue(note.startswith("module does not parse:"), note)

    def test_whole_class_can_be_replaced(self):
        new, note = apply_function_rewrite(MODULE, FunctionRewrite(
            "Calc", "r", ["class Calc:", "    factor = 3", "", "    def scale(self, x):", "        return x * self.factor"]))
        self.assertIsNotNone(new, note)
        self.assertParses(new)
        self.assertEqual([d[0] for d in list_definitions(new)], ["add", "decorated", "Calc", "Calc.scale", "main"])


class FunctionRewritePacketTests(unittest.TestCase):

    def test_schema_constrains_function_to_the_module_definitions(self):
        schema = function_rewrite_schema(["add", "Calc.scale", "add", " "])
        props = schema["json_schema"]["schema"]["properties"]
        self.assertEqual(schema["json_schema"]["name"], "FunctionRewritePacket")
        self.assertEqual(props["packet"]["enum"], ["FUNCTION_REWRITE"])
        self.assertEqual(props["function"]["enum"], ["add", "Calc.scale"])     # deduplicated, blanks dropped
        self.assertEqual(props["new_source"]["type"], "array")
        self.assertEqual(props["add_imports"]["type"], "array")
        self.assertEqual(schema["json_schema"]["schema"]["required"],
                         ["packet", "hypothesis_id", "function", "rationale", "new_source", "add_imports"])
        # No definitions (or too many for an enum): a free string, as in the module-level default.
        self.assertNotIn("enum", V6_FUNCTION_REWRITE_SCHEMA["json_schema"]["schema"]["properties"]["function"])
        many = function_rewrite_schema([f"f{i}" for i in range(MAX_FUNCTION_ENUM + 1)])
        self.assertNotIn("enum", many["json_schema"]["schema"]["properties"]["function"])
        json.dumps(schema)   # serialisable for the vLLM request

    def test_parse_requires_a_function_and_a_line_array(self):
        self.assertIsNone(parse_function_rewrite("no json"))
        self.assertIsNone(parse_function_rewrite(json.dumps({"function": "add", "new_source": "def add(): pass"})))
        self.assertIsNone(parse_function_rewrite(json.dumps({"function": "", "new_source": ["def add(): pass"]})))
        self.assertIsNone(parse_function_rewrite(json.dumps({"function": "add", "new_source": ["", "  "]})))
        rw = parse_function_rewrite("```json\n" + json.dumps({
            "packet": "FUNCTION_REWRITE", "hypothesis_id": "h9", "function": " Calc.scale ",
            "rationale": "two  words", "new_source": ["def scale(self, x):", None, "    return x"],
            "add_imports": ["import math", "", None]}) + "\n```")
        self.assertEqual(rw.function, "Calc.scale")
        self.assertEqual(rw.rationale, "two words")
        self.assertEqual(rw.new_source, ["def scale(self, x):", "    return x"])
        self.assertEqual(rw.add_imports, ["import math"])
        self.assertEqual(rw.hypothesis_id, "h9")
        self.assertEqual(rw.new_text, "def scale(self, x):\n    return x")
        rw2 = parse_function_rewrite(json.dumps({"function": "add", "new_source": ["def add(): pass"], "add_imports": "x"}))
        self.assertEqual(rw2.add_imports, [])


FUZZY_SRC = '''def compute(items, factor):
    total = 0
    for item in items:
        if item.weight > 0:
            total += item.weight * factor
        else:
            total -= 1
    return total


def report(items):
    lines = []
    for item in items:
        lines.append(f"{item.name}: {item.weight}")
    return "\\n".join(lines)
'''


class FuzzyAnchorTests(unittest.TestCase):

    def test_small_drift_is_accepted_with_a_fuzzy_note_and_reindented(self):
        # The model dropped `.weight` in one of six quoted lines: an exact,
        # rstrip or dedent match fails, the fuzzy window is unique and close.
        old = ["total = 0", "for item in items:", "    if item.weight > 0:",
               "        total += item.w * factor", "    else:", "        total -= 1"]
        new = ["total = 0", "for item in items:", "    if item.weight > 0:",
               "        total += item.weight * factor", "    else:", "        total -= 10"]
        out, note = apply_repair_plan(FUZZY_SRC, RepairPlan("compute", "r", old, new))
        self.assertIsNotNone(out, note)
        self.assertIn("fuzzy match, ratio 0.9", note)
        self.assertIn("            total -= 10\n    return total\n", out)   # re-indented to the window's indentation
        self.assertEqual(out.replace("total -= 10", "total -= 1"), FUZZY_SRC)

    def test_ambiguous_fuzzy_anchor_is_refused(self):
        src = FUZZY_SRC + "\n\n" + FUZZY_SRC.replace("compute", "compute2").replace("report", "report2")
        old = ["total = 0", "for item in items:", "    if item.weight > 0:",
               "        total += item.w * factor", "    else:", "        total -= 1"]
        out, note = apply_repair_plan(src, RepairPlan("compute", "r", old, ["total = 1"]))
        self.assertIsNone(out)
        self.assertTrue(note.startswith("anchor is ambiguous (fuzzy:"), note)

    def test_below_threshold_is_not_found_and_names_the_closest_window(self):
        old = ["for item in items:", "    if item.weight >= 0 and item.name:", "        total += item.weight ** factor"]
        out, note = apply_repair_plan(FUZZY_SRC, RepairPlan("compute", "r", old, ["pass"]))
        self.assertIsNone(out)
        self.assertRegex(note, r"^anchor not found; closest is lines 3-5 \(ratio 0\.\d\d\)$")
        ratio = float(note.rsplit("ratio ", 1)[1].rstrip(")"))
        self.assertLess(ratio, FUZZY_ANCHOR_MIN_RATIO)

    def test_exact_strategies_still_win_before_fuzzy(self):
        out, note = apply_repair_plan(FUZZY_SRC, RepairPlan("compute", "r", ["            total -= 1"], ["            total -= 2"]))
        self.assertIn("exact match", note)
        self.assertIn("total -= 2", out)


BUGGY = "def add(a, b):\n    return a - b\n"
BROKEN = "def add(a, b):\n    return a +\n"      # does not compile: the module check must fail
FIXED = "def add(a, b):\n    return a + b\n"
PROBE = "from mypkg.calc import add\nprint('ADD', add(2, 2))\n"


class SearchLoopLadderTests(unittest.TestCase):
    """The loop's side of A4: counters, the `[mode]` note and the revert hook."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hae_ladder_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.ws = AgentWorkspace("ladder_firm", base_dir=self.tmp)
        self.ws.write_file("mypkg/__init__.py", "")
        self.ws.write_file("mypkg/calc.py", BUGGY)
        self.state = EpistemicState("ladder_firm")
        self.gk = EvidenceGatekeeper(self.ws, timeout_s=15, isolate=False, stage_reference=False,
                                     module_for_tag={"calc": "mypkg/calc.py"})
        self.gk.reconcile_with_oracle(
            self.state, ["[calc] FAIL: test_add (t.T.test_add) -> AssertionError: 0 != 4"], iteration=1)
        self.q = next(iter(self.state.questions.values()))
        self.h = self.state.add_hypothesis(self.q.question_id, "add subtracts", "operator bug", 0.3, PROBE,
                                           {"expect_exit_code": 0, "expect_stdout_contains": "ADD 0"})
        self.gk.apply(self.state, self.h, self.gk.run_experiment(self.state, self.h))
        self.assertEqual(self.h.status, SUPPORTED)
        self.logs = []

    def loop(self, synth, revert=None, **kw):
        policy = EpistemicPolicyGene(enabled=True, search_budget_moves=10, min_hypotheses_before_synthesis=1,
                                     low_prior_quota=0.0)
        return EpistemicSearchLoop(self.state, self.gk, EpistemicValueFunction(), policy,
                                   lambda q, s, k: [], synth, logger=self.logs.append, revert_patch=revert, **kw)

    def broken_synth(self, q, h, s):
        self.ws.write_file("mypkg/calc.py", BROKEN)
        return {"written": True, "path": "mypkg/calc.py", "mode": "function", "summary": "function rewrite",
                "previous_source": BUGGY, "elapsed_s": 1.5}

    def test_failed_module_check_reverts_exactly_once_with_path_and_previous_source(self):
        reverts = []

        def revert(path, src):
            reverts.append((path, src))
            return self.ws.write_file(path, src)

        res = self.loop(self.broken_synth, revert=revert).run(budget_moves=1)
        self.assertEqual(reverts, [("mypkg/calc.py", BUGGY)])
        self.assertEqual(self.ws.read_file("mypkg/calc.py")["content"], BUGGY)
        self.assertEqual(res.stats["syntheses_reverted"], 1)
        self.assertEqual(res.stats["syntheses_by_function"], 1)
        self.assertEqual(res.stats["syntheses_verified"], 0)
        self.assertEqual(res.stats["synthesis_seconds"], 1.5)
        note = res.trajectory[-1].note
        self.assertTrue(note.startswith("mypkg/calc.py [function]: module check failed at py_compile"), note)
        self.assertTrue(note.endswith("; reverted to pre-synthesis module"), note)
        self.assertTrue(any("reverted mypkg/calc.py to its pre-synthesis content" in m for m in self.logs))
        self.assertFalse(self.h.patch_applied)
        self.assertEqual(self.h.synthesis_failures, 1)

    def test_without_a_revert_hook_the_broken_write_stays_and_nothing_is_counted(self):
        res = self.loop(self.broken_synth).run(budget_moves=1)
        self.assertEqual(self.ws.read_file("mypkg/calc.py")["content"], BROKEN)
        self.assertNotIn("syntheses_reverted", res.stats)
        self.assertEqual(res.trajectory[-1].note, "mypkg/calc.py [function]: module check failed at py_compile (exit 1)")

    def test_a_failing_or_refused_revert_is_logged_not_raised(self):
        def boom(path, src):
            raise OSError("disk says no")
        res = self.loop(self.broken_synth, revert=boom).run(budget_moves=1)
        self.assertNotIn("syntheses_reverted", res.stats)
        self.assertNotIn("reverted to pre-synthesis module", res.trajectory[-1].note)
        self.assertTrue(any("revert of mypkg/calc.py failed: OSError: disk says no" in m for m in self.logs))
        # A workspace refusal (e.g. the Monotonic Verification Guard) comes back as a status dict.
        self.setUp()
        res = self.loop(self.broken_synth, revert=lambda p, s: {"status": "error", "error": "guard"}).run(budget_moves=1)
        self.assertNotIn("syntheses_reverted", res.stats)
        self.assertTrue(any("revert of mypkg/calc.py refused: guard" in m for m in self.logs))

    def test_a_passing_synthesis_is_not_reverted_and_carries_its_mode(self):
        reverts = []

        def synth(q, h, s):
            self.ws.write_file("mypkg/calc.py", FIXED)
            return {"written": True, "path": "mypkg/calc.py", "mode": "plan", "summary": "anchored edit",
                    "previous_source": BUGGY, "elapsed_s": 0.25}

        res = self.loop(synth, revert=lambda p, s: reverts.append((p, s))).run(budget_moves=1)
        self.assertEqual(reverts, [])
        self.assertEqual(res.stats["syntheses_verified"], 1)
        self.assertEqual(res.stats["syntheses_by_plan"], 1)
        self.assertTrue(res.trajectory[-1].note.startswith("mypkg/calc.py [plan]: module check passed"))
        self.assertTrue(self.h.patch_applied)

    def test_skipped_rewrites_and_seconds_are_counted_and_old_adapters_still_work(self):
        calls = {"n": 0}

        def synth(q, h, s):
            calls["n"] += 1
            if calls["n"] == 1:
                return {"written": False, "path": "mypkg/calc.py", "mode": "none", "rewrite_skipped": True,
                        "summary": "model sees no change to make (plan and function rewrite both no-ops); "
                                   "whole-module rewrite skipped", "elapsed_s": 40.0}
            # A pre-A4 adapter: no mode, no elapsed_s, no previous_source.
            self.ws.write_file("mypkg/calc.py", FIXED)
            return {"written": True, "path": "mypkg/calc.py", "summary": "fixed"}

        res = self.loop(synth, revert=lambda p, s: None).run()
        self.assertEqual(res.stats["syntheses"], 2)
        self.assertEqual(res.stats["synthesis_rewrites_skipped"], 1)
        self.assertEqual(res.stats["syntheses_unwritten"], 1)
        self.assertEqual(res.stats["synthesis_seconds"], 40.0)
        self.assertEqual(res.stats["syntheses_verified"], 1)
        self.assertNotIn("syntheses_by_function", res.stats)
        notes = [m.note for m in res.trajectory if m.move_type == "synthesize"]
        self.assertTrue(notes[0].startswith("synthesis wrote nothing: model sees no change to make"))
        self.assertTrue(notes[1].startswith("mypkg/calc.py [unknown]: module check passed"))


if __name__ == "__main__":
    unittest.main()
