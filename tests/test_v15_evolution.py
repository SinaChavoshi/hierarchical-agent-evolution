"""Unit tests for the V15 harness-evolution package (no network, no cluster)."""

import json
import os
import random
import tempfile
import unittest

from hae.evolution.genome import HarnessGenome, TaskResult
from hae.evolution.leakage import LeakageGate
from hae.evolution.mutation import apply_edit_blocks, extract_function_source, parse_edit_blocks
from hae.swebench.harness_knobs import (
    KNOB_SPACE,
    V14_DEFAULT_KNOBS,
    crossover_knobs,
    mutate_knobs,
    resolve_knobs,
    sample_knobs,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class KnobTests(unittest.TestCase):
    def test_defaults_match_v14(self):
        k = resolve_knobs({})
        self.assertEqual(k["max_episodes"], 4)
        self.assertEqual(k["falsifier_max_turns"], 6)
        self.assertEqual(k["min_budget_moves"], 68)
        self.assertEqual(k, V14_DEFAULT_KNOBS)

    def test_clip_unknown_and_order(self):
        k = resolve_knobs({"max_episodes": 999, "nope": 1, "gen1_turns_min": 25, "gen1_turns_max": 9,
                           "enable_falsifier": "false"})
        self.assertEqual(k["max_episodes"], KNOB_SPACE["max_episodes"].high)
        self.assertNotIn("nope", k)
        self.assertLessEqual(k["gen1_turns_min"], k["gen1_turns_max"])
        self.assertFalse(k["enable_falsifier"])

    def test_mutation_sampling_crossover_stay_in_range(self):
        rng = random.Random(0)
        for _ in range(50):
            for k in (sample_knobs(rng), mutate_knobs(V14_DEFAULT_KNOBS, rng, n_genes=3),
                      crossover_knobs(sample_knobs(rng), V14_DEFAULT_KNOBS, rng)):
                for name, spec in KNOB_SPACE.items():
                    if spec.kind != "bool":
                        self.assertGreaterEqual(k[name], spec.low)
                        self.assertLessEqual(k[name], spec.high)

    def test_engine_reads_knobs(self):
        from hae.swebench import hierarchical_mcts as hm
        src = open(hm.__file__, encoding="utf-8").read()
        self.assertIn('self.knobs["max_episodes"]', src)
        self.assertIn('self.knobs["falsifier_max_turns"]', src)


class InjectorRemovalTests(unittest.TestCase):
    def test_v14_gold_fix_detectors_are_gone(self):
        src = open(os.path.join(REPO_ROOT, "hae", "swebench", "hierarchical_mcts.py"), encoding="utf-8").read()
        for needle in ("_check_range", "unmatched_segments", "range_setter_targets", "runtime_error_translations",
                       "deprecated_replacements", "unreferenced_init_attrs", "DataElement.from_json", "themes.py"):
            self.assertNotIn(needle, src, needle)

    def test_v14_task_specific_prompt_examples_are_gone(self):
        for rel in ("hae/swebench/hierarchical_mcts.py", "hae/swebench/agentic.py"):
            src = open(os.path.join(REPO_ROOT, rel), encoding="utf-8").read()
            for needle in ("foo{1,3}", "GenericAliasMixin", "additional_metadata", "cachedproperty",
                           "short CLI option", "window - 1", "bad-name-rgxs", "weakref"):
                self.assertNotIn(needle, src, f"{needle} in {rel}")


class GenomeTests(unittest.TestCase):
    def test_roundtrip_and_fitness(self):
        g = HarnessGenome(genome_id="g1", knobs={"max_episodes": 5}, patch="+++ b/x\n+a\n-b\n")
        g.results["t1"] = TaskResult("t1", resolved=True, tokens=200_000)
        g.results["t2"] = TaskResult("t2", resolved=False, tokens=400_000)
        self.assertAlmostEqual(g.success_rate(), 0.5)
        self.assertAlmostEqual(g.fitness(token_lambda=0.1), 0.5 - 0.1 * 0.3)
        self.assertEqual(g.patch_lines, 2)
        g2 = HarnessGenome.from_dict(json.loads(json.dumps(g.to_dict())))
        self.assertEqual(g2.content_hash, g.content_hash)
        self.assertTrue(g2.results["t1"].resolved)


class LeakageTests(unittest.TestCase):
    def test_flags_gold_tokens_and_names_but_not_baseline(self):
        rows = [
            {"instance_id": "acme__widget-1", "repo": "acme/widget",
             "patch": "+def _very_specific_helper(x):\n+    raise ValueError('totally unique message here')\n",
             "test_patch": ""},
            {"instance_id": "acme__widget-2", "repo": "acme/widget", "patch": "+import warnings\n", "test_patch": ""},
        ]
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")
            path = fh.name
        try:
            gate = LeakageGate.build([path], ["acme__widget-1"], baseline_source="def existing_function(): pass\n")
            self.assertIsNone(gate.check("+def existing_function(): return 1\n"))
            self.assertIn("_very_specific_helper", gate.check("+x = _very_specific_helper(1)\n"))
            self.assertIn("totally unique message here", gate.check("+msg = 'totally unique message here'\n"))
            self.assertIsNotNone(gate.check("+# handle the widget project specially\n"))
        finally:
            os.remove(path)


class EditBlockTests(unittest.TestCase):
    def test_parse_and_apply(self):
        with tempfile.TemporaryDirectory() as d:
            rel = "hae/swebench/agentic.py"
            os.makedirs(os.path.join(d, "hae", "swebench"))
            with open(os.path.join(d, rel), "w") as fh:
                fh.write("def f():\n    return 1\n")
            text = ("<<<<<<< SEARCH hae/swebench/agentic.py\n    return 1\n=======\n    return 2\n>>>>>>> REPLACE\n"
                    "RATIONALE: returns two.")
            blocks, why = parse_edit_blocks(text)
            self.assertEqual(len(blocks), 1)
            self.assertEqual(why, "returns two.")
            self.assertIsNone(apply_edit_blocks(d, blocks))
            self.assertIn("return 2", open(os.path.join(d, rel)).read())
            self.assertIsNotNone(apply_edit_blocks(d, [("setup.py", "x", "y")]))
            self.assertIsNotNone(apply_edit_blocks(d, [(rel, "missing text", "y")]))

    def test_extract_function_source(self):
        src = "class A:\n    def m(self):\n        return 1\n\ndef g():\n    pass\n"
        self.assertEqual(extract_function_source(src, "A.m"), "    def m(self):\n        return 1")
        self.assertEqual(extract_function_source(src, "g"), "def g():\n    pass")
        self.assertIsNone(extract_function_source(src, "A.zz"))


if __name__ == "__main__":
    unittest.main()
