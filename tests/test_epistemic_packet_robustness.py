"""Regression tests for the Gen 16 pilot hypothesis-packet failure.

On 2026-10-06 three of four pilot firms logged `no parseable hypotheses` on
every question although the reply head was a well-formed HYPOTHESIS_SET.
Direct probing of the gateway showed `finish_reason=length` with the reply
degenerating inside `probe_code` into `import inspect, sys, ..., _base._base`
until max_tokens: under grammar-constrained decoding a JSON *string* cannot
hold raw newlines, so the model never reached the end of the first probe.
These tests pin the three parts of the fix.
"""
import json
import unittest

from hae.epistemic.moves import (
    MAX_PROBE_LINES_SCHEMA,
    V6_HYPOTHESIS_SCHEMA,
    parse_hypothesis_packet,
    parse_question_packet,
    salvage_truncated_items,
)


def _item(claim, lines, prior=0.4, prediction=None):
    return {
        "claim": claim,
        "mechanism": f"mechanism for {claim}",
        "prior": prior,
        "probe_lines": lines,
        "prediction": prediction or {"expect_exit_code": 0, "expect_stdout_contains": "MARK"},
    }


class SchemaUsesLineArrayTest(unittest.TestCase):
    def test_probe_is_an_array_of_lines_not_a_string(self):
        props = V6_HYPOTHESIS_SCHEMA["json_schema"]["schema"]["properties"]["hypotheses"]["items"]
        self.assertIn("probe_lines", props["properties"])
        self.assertNotIn("probe_code", props["properties"])
        self.assertEqual(props["properties"]["probe_lines"]["type"], "array")
        self.assertEqual(props["properties"]["probe_lines"]["maxItems"], MAX_PROBE_LINES_SCHEMA)
        self.assertIn("probe_lines", props["required"])
        self.assertNotIn("probe_code", props["required"])
        # A newline must never be required inside any string the grammar emits.
        self.assertEqual(props["properties"]["probe_lines"]["items"]["type"], "string")


class ProbeLinesParsingTest(unittest.TestCase):
    def test_lines_are_joined_into_runnable_source(self):
        raw = json.dumps({"packet": "HYPOTHESIS_SET", "question_id": "q1", "hypotheses": [
            _item("collector skips module functions",
                  ["from hae.evaluation.harness import ExecutionHarness",
                   "h = ExecutionHarness()",
                   "print('MARK', hasattr(h, 'collect'))"]),
        ]})
        props = parse_hypothesis_packet(raw)
        self.assertEqual(len(props), 1)
        self.assertEqual(props[0].probe_code.splitlines()[0], "from hae.evaluation.harness import ExecutionHarness")
        self.assertEqual(len(props[0].probe_code.splitlines()), 3)
        self.assertEqual(props[0].prediction["expect_stdout_contains"], "MARK")

    def test_legacy_probe_code_string_still_accepted(self):
        raw = json.dumps({"packet": "HYPOTHESIS_SET", "question_id": "q1", "hypotheses": [
            {"claim": "legacy", "mechanism": "m", "prior": 0.3,
             "probe_code": "import sys\\nprint('MARK')",
             "prediction": {"expect_exit_code": 0, "expect_stdout_contains": "MARK"}},
        ]})
        props = parse_hypothesis_packet(raw)
        self.assertEqual(len(props), 1)
        self.assertEqual(props[0].probe_code, "import sys\nprint('MARK')")

    def test_empty_line_array_is_dropped_as_unfalsifiable(self):
        raw = json.dumps({"packet": "HYPOTHESIS_SET", "question_id": "q1",
                          "hypotheses": [_item("no probe", [])]})
        self.assertEqual(parse_hypothesis_packet(raw), [])


class TruncatedPacketSalvageTest(unittest.TestCase):
    _CLAIMS = ["collector ignores module functions", "runner swallows import errors", "fixture scope leaks state"]

    def _packet(self, n):
        return {"packet": "HYPOTHESIS_SET", "question_id": "q2", "hypotheses": [
            _item(self._CLAIMS[i], [f"print('MARK{i}')"], prior=0.2 + 0.1 * i) for i in range(n)]}

    def test_whole_items_before_the_cut_are_kept(self):
        full = json.dumps(self._packet(3), indent=2)
        # cut in the middle of the third item's probe
        cut = full.index("MARK2") - 3
        truncated = full[:cut]
        with self.assertRaises(ValueError):
            json.loads(truncated)
        items = salvage_truncated_items(truncated, "hypotheses")
        self.assertEqual([it["claim"] for it in items], self._CLAIMS[:2])
        props = parse_hypothesis_packet(truncated)
        self.assertEqual([p.claim for p in props], self._CLAIMS[:2])
        self.assertAlmostEqual(props[1].prior, 0.3)

    def test_degenerate_repetition_loop_yields_nothing_but_does_not_raise(self):
        # The exact shape observed in the pilot: first probe string never closes.
        raw = ('{\n  "packet": "HYPOTHESIS_SET",\n  "question_id": "q2",\n  "hypotheses": [\n    {\n'
               '      "claim": "The fallback runner ignores module-level test functions",\n'
               '      "mechanism": "collection predicate only inspects classes",\n'
               '      "prior": 0.65,\n'
               '      "probe_code": "import inspect, sys, types, importlib.util, os' + ", _base" * 400)
        self.assertEqual(parse_hypothesis_packet(raw), [])
        self.assertEqual(salvage_truncated_items(raw, "hypotheses"), [])

    def test_question_packets_salvage_too(self):
        full = json.dumps({"packet": "QUESTION_SET", "questions": [
            {"text": "why A", "module": "hae/a.py", "uncertainty": 0.9},
            {"text": "why B", "module": "hae/b.py", "uncertainty": 0.5},
            {"text": "why C", "module": "hae/c.py", "uncertainty": 0.7}]})
        truncated = full[:full.index("why C") + 2]
        qs = parse_question_packet(truncated)
        self.assertEqual([q.text for q in qs], ["why A", "why B"])

    def test_salvage_ignores_text_without_the_key(self):
        self.assertEqual(salvage_truncated_items("", "hypotheses"), [])
        self.assertEqual(salvage_truncated_items("no json here", "hypotheses"), [])
        self.assertEqual(salvage_truncated_items('{"hypotheses": "not-a-list"}', "hypotheses"), [])


if __name__ == "__main__":
    unittest.main()
