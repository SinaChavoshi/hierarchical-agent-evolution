"""Unit tests for all three V11 Synthesis-Handoff improvements.

Covers:
  1. Error-conditioned synthesis retries (`Hypothesis.synthesis_attempts` unified diff +
     module check / probe effect diagnostics recorded before revert and fed to retry prompt).
  2. Strengthened post-synthesis probe-inversion & crash rejection (`check_synthesis_effect`
     detects regressed sibling probes / partial root fixes / crashed probes, and
     `apply_synthesis` + `_do_synthesize` reject & revert any non-`effective` verdict).
  3. Coordinated multi-hunk / multi-file repair plans (`SecondaryEdit`, line-number gutter
     stripping, `path:Class.method` normalization in `_resolve_definition`, collaborator
     context injection, multi-file verification, and atomic multi-file rollback).
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from hae.epistemic.gatekeeper import (
    EFFECT_EFFECTIVE,
    EFFECT_INCONCLUSIVE,
    EFFECT_INEFFECTIVE,
    EvidenceGatekeeper,
)
from hae.epistemic.ledger import EpistemicState, Q_OPEN, SUPPORTED
from hae.epistemic.mcts import EpistemicSearchLoop
from hae.epistemic.moves import (
    FunctionRewrite,
    RepairPlan,
    SecondaryEdit,
    apply_function_rewrite,
    apply_repair_plan,
    parse_repair_plan,
)
from hae.epistemic.value import EpistemicValueFunction
from hae.genome.schema import EpistemicPolicyGene
from hae.runtime.workspace import AgentWorkspace


class V11SynthesisHandoffTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = AgentWorkspace("v11_test", base_dir=self._tmp.name)
        self.gk = EvidenceGatekeeper(self.ws, isolate=False, stage_reference=False)
        self.state = EpistemicState("v11_test")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_parse_repair_plan_strips_line_gutters_and_parses_secondary_edits(self) -> None:
        raw = json.dumps({
            "packet": "REPAIR_PLAN",
            "hypothesis_id": "h1",
            "function": "Foo.bar",
            "rationale": "Fix both coupled helpers",
            "old_lines": ["  144|     return x + 1"],
            "new_lines": ["  144|     return x"],
            "secondary_edits": [
                {
                    "path": "pkg/collab.py",
                    "function": "helper",
                    "old_lines": ["   12|     return False"],
                    "new_lines": ["   12|     return True"],
                }
            ],
        })
        plan = parse_repair_plan(raw)
        assert plan is not None
        self.assertEqual(plan.old_lines, ["    return x + 1"])
        self.assertEqual(plan.new_lines, ["    return x"])
        self.assertEqual(len(plan.secondary_edits), 1)
        self.assertEqual(plan.secondary_edits[0].path, "pkg/collab.py")
        self.assertEqual(plan.secondary_edits[0].old_lines, ["    return False"])
        self.assertEqual(plan.secondary_edits[0].new_lines, ["    return True"])

    def test_function_rewrite_resolves_path_prefixed_method_names(self) -> None:
        source = (
            "class Url:\n"
            "    def __init__(self, x: int) -> None:\n"
            "        self.x = x + 1\n"
        )
        rw = FunctionRewrite(
            function="src/marshmallow/fields.py:Url.__init__",
            rationale="Fix init offset",
            new_source=[
                "def __init__(self, x: int) -> None:",
                "    self.x = x",
            ],
            add_imports=[],
            hypothesis_id="h1",
        )
        updated, note = apply_function_rewrite(source, rw)
        self.assertIsNotNone(updated, note)
        self.assertIn("self.x = x\n", updated + "\n")
        self.assertNotIn("x + 1", updated or "")

    def test_error_conditioned_retry_records_diff_and_reverts_all_files_atomically(self) -> None:
        os.makedirs(os.path.join(self._tmp.name, "pkg"), exist_ok=True)
        self.ws.write_file("pkg/__init__.py", "")
        self.ws.write_file("pkg/primary.py", "def compute(x):\n    return x - 1\n")
        self.ws.write_file("pkg/collab.py", "def helper(x):\n    return x * 2\n")

        q = self.state.add_question("Fix compute + helper", module="pkg/primary.py")
        h = self.state.add_hypothesis(
            q.question_id,
            claim="compute subtracts 1 instead of adding helper(x)",
            mechanism="off-by-one in compute",
            prior=0.9,
            probe_code="from pkg.primary import compute\nprint(compute(3))\n",
            prediction={"expect_exit_code": 0, "expect_stdout_contains": "2"},
        )
        ev = self.gk.run_experiment(self.state, h)
        self.gk.apply(self.state, h, ev)
        self.assertEqual(h.status, SUPPORTED)

        # Attempt 1 writes broken syntax into collab.py alongside primary.py:
        # BOTH files must be reverted atomically AND unified diff + error stored in h.synthesis_attempts!
        def bad_synth(question, hyp, st):
            bad_primary = "def compute(x):\n    return x + 10\n"
            bad_collab = "def helper(x):\n    return (\n"  # syntax error in secondary collaborator!
            self.ws.write_file("pkg/primary.py", bad_primary)
            self.ws.write_file("pkg/collab.py", bad_collab)
            return {
                "written": True,
                "path": "pkg/primary.py",
                "paths": ["pkg/primary.py", "pkg/collab.py"],
                "previous_sources": {
                    "pkg/primary.py": "def compute(x):\n    return x - 1\n",
                    "pkg/collab.py": "def helper(x):\n    return x * 2\n",
                },
                "written_sources": {
                    "pkg/primary.py": bad_primary,
                    "pkg/collab.py": bad_collab,
                },
                "mode": "plan",
            }

        loop = EpistemicSearchLoop(
            self.state,
            self.gk,
            EpistemicValueFunction(0.6),
            EpistemicPolicyGene(strict_probe_inversion=True),
            lambda q_, st_, k: [],
            bad_synth,
            revert_patch=lambda p, src: self.ws.write_file(p, src),
            root_question_id=q.question_id,
        )
        loop._do_synthesize(q, h)

        # Both files atomically reverted to pre-synthesis content
        self.assertEqual(self.ws.read_file("pkg/primary.py")["content"], "def compute(x):\n    return x - 1\n")
        self.assertEqual(self.ws.read_file("pkg/collab.py")["content"], "def helper(x):\n    return x * 2\n")
        self.assertEqual(q.status, Q_OPEN)
        # Error-conditioned synthesis feedback recorded with diff across both files
        self.assertEqual(len(h.synthesis_attempts), 1)
        att = h.synthesis_attempts[0]
        self.assertEqual(att["stage"], "module_check")
        self.assertIn("a/pkg/primary.py", att["diff"])
        self.assertIn("a/pkg/collab.py", att["diff"])

    def test_crashing_probe_or_regressed_sibling_probe_rejected_by_apply_synthesis(self) -> None:
        os.makedirs(os.path.join(self._tmp.name, "pkg"), exist_ok=True)
        self.ws.write_file("pkg/__init__.py", "")
        self.ws.write_file("pkg/mod.py", "def f():\n    return 'BUG'\n")
        q = self.state.add_question("Fix f()", module="pkg/mod.py")
        h = self.state.add_hypothesis(
            q.question_id,
            claim="f returns BUG",
            mechanism="returns literal BUG",
            prior=0.8,
            probe_code="from pkg.mod import f\nprint(f())\n",
            prediction={"expect_exit_code": 0, "expect_stdout_contains": "BUG"},
        )
        ev = self.gk.run_experiment(self.state, h)
        self.gk.apply(self.state, h, ev)
        self.assertEqual(h.status, SUPPORTED)

        # Patch causes f() to raise ZeroDivisionError (probe exits non-zero -> EFFECT_INCONCLUSIVE)
        self.ws.write_file("pkg/mod.py", "def f():\n    return 1 / 0\n")
        mod_ev = self.gk.verify_module(self.state, "pkg/mod.py", q.question_id, h.hypothesis_id)
        self.assertTrue(mod_ev.matched_prediction)
        effect = self.gk.check_synthesis_effect(self.state, q, h, root_question_id=q.question_id, module_evidence=mod_ev)
        self.assertEqual(effect["verdict"], EFFECT_INCONCLUSIVE)
        # Under V11 strict_probe_inversion=True, apply_synthesis rejects crashing writes and leaves q OPEN
        delta = self.gk.apply_synthesis(self.state, q, h, mod_ev, effect=effect, strict_effect=True)
        self.assertEqual(delta, 0.0)
        self.assertFalse(h.patch_applied)
        self.assertEqual(q.status, Q_OPEN)

    def test_elided_class_docstring_and_json_escape_preservation(self):
        src = (
            "class cachedproperty:\n"
            '    """Docstring line 1.\n'
            '    Docstring line 2.\n'
            '    """\n\n'
            '    __slots__ = ("wrapped",)\n\n'
            "    def __init__(self, wrapped):\n"
            "        self.wrapped = wrapped\n\n"
            "    @property\n"
            "    def __doc__(self):\n"
            '        return "\\n%s" % (self.wrapped.__doc__ or "")\n'
        )
        old_lines = [
            "class cachedproperty:",
            "    def __init__(self, wrapped):",
            "        self.wrapped = wrapped",
            "",
            "    @property",
            "    def __doc__(self):",
            '        return "n%s" % (self.wrapped.__doc__ or "")',
        ]
        new_lines = old_lines + [
            "",
            "if sys.version_info >= (3, 8):",
            "    from functools import cached_property",
            "else:",
            "    cached_property = cachedproperty",
        ]
        plan = RepairPlan(
            function="cachedproperty",
            rationale="alias cached_property on Python >= 3.8",
            old_lines=old_lines,
            new_lines=new_lines,
        )
        res, _note = apply_repair_plan(src, plan)
        self.assertIsNotNone(res)
        self.assertIn('__slots__ = ("wrapped",)', res)
        self.assertIn('"\\n%s"', res)
        self.assertIn("\nif sys.version_info >= (3, 8):\n", res)
        self.assertNotIn("    class cachedproperty:", res)


if __name__ == "__main__":
    unittest.main()
