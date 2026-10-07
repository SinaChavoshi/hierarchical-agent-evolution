"""The static undefined-name gate: catches what `py_compile` and `import` cannot, and nothing else.

Two kinds of test. The positive ones replay the three Gen 16 cohort run 2
shapes (`mnf_err` in an f-string, `e` after its `except` handler, a plain
undefined global in a function body). The negative ones are the more important
half: every legal binding form Python offers must be silent, because a false
positive here blocks a repair that may have been right.
"""

import os
import tempfile
import textwrap
import unittest
from contextlib import redirect_stdout
from io import StringIO

from hae.epistemic.static_check import (
    KIND_UNBOUND_AFTER_EXCEPT, KIND_UNDEFINED,
    describe_findings, main, undefined_names,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _check(source):
    return undefined_names(textwrap.dedent(source), filename="firm.py")


class FindsRuntimeNameErrorsTests(unittest.TestCase):

    def test_undefined_global_inside_a_function_body(self):
        findings = _check("""
            def f():
                return oops
        """)
        self.assertEqual([(f.name, f.line, f.scope, f.kind) for f in findings],
                         [("oops", 3, "f", KIND_UNDEFINED)])
        self.assertEqual(findings[0].describe(), "undefined name 'oops' at line 3 in f")

    def test_name_interpolated_in_an_fstring(self):
        # gen_14_mutant_3__s1: `mnf_err` and `imp_err` were meant for a
        # generated script and never existed in harness.py.
        findings = _check("""
            def _gate_smoke(script):
                return f"import sys\\nprint({mnf_err}, {imp_err})"
        """)
        self.assertEqual(sorted(f.name for f in findings), ["imp_err", "mnf_err"])
        self.assertTrue(all(f.scope == "_gate_smoke" and f.kind == KIND_UNDEFINED for f in findings))

    def test_except_name_used_after_its_handler(self):
        # gen_14_elite_2__s3: UnboundLocalError. Python deletes `e` when the
        # handler exits, so the later read always raises.
        findings = _check("""
            def _gate_smoke():
                try:
                    print()
                except Exception as e:
                    failed = True
                if failed:
                    return str(e)
                return e
        """)
        self.assertEqual([(f.name, f.line, f.kind) for f in findings],
                         [("e", 8, KIND_UNBOUND_AFTER_EXCEPT), ("e", 9, KIND_UNBOUND_AFTER_EXCEPT)])
        self.assertIn("bound only by 'except ... as e'", findings[0].describe())
        self.assertIn("line 8 in _gate_smoke", findings[0].describe())

    def test_except_name_used_inside_its_handler_is_fine(self):
        self.assertEqual(_check("""
            def f():
                try:
                    print()
                except Exception as e:
                    return str(e)
                return None
        """), [])

    def test_except_name_also_bound_elsewhere_is_not_a_leak(self):
        self.assertEqual(_check("""
            def f():
                e = None
                try:
                    print()
                except Exception as e:
                    pass
                return e
        """), [])

    def test_undefined_name_at_module_level(self):
        # gen_14_mutant_3__s1's latent bug: an annotation in a never-executed
        # fallback `def` at module level, referring to an import that failed.
        findings = _check("""
            try:
                from typing import Iterable
            except ImportError:
                def helper(x: Iterable) -> int:
                    return 0
            VALUE = missing_constant
        """)
        self.assertEqual([(f.name, f.scope) for f in findings],
                         [("missing_constant", "<module>")])
        self.assertEqual(findings[0].describe(), "undefined name 'missing_constant' at line 7 at module level")

    def test_findings_are_sorted_and_deduplicated_per_line(self):
        findings = _check("""
            def g():
                return zzz + aaa
            def f():
                return aaa, aaa
        """)
        self.assertEqual([(f.line, f.name) for f in findings], [(3, "aaa"), (3, "zzz"), (5, "aaa")])

    def test_describe_findings_caps_the_list(self):
        source = "def f():\n" + "".join(f"    x{i} = y{i}\n" for i in range(12))
        findings = undefined_names(source)
        self.assertEqual(len(findings), 12)
        lines = describe_findings(findings, limit=10)
        self.assertEqual(len(lines), 11)
        self.assertEqual(lines[-1], "... and 2 more")

    def test_syntax_errors_propagate(self):
        with self.assertRaises(SyntaxError):
            undefined_names("def f(:\n  pass\n")


class StaysSilentOnLegalCodeTests(unittest.TestCase):
    """Every binding form the checker must understand. Any finding here is a bug in the checker."""

    def assertClean(self, source):
        findings = _check(source)
        self.assertEqual(findings, [], [f.describe() for f in findings])

    def test_module_level_bindings_of_every_kind(self):
        self.assertClean("""
            import os, sys as system
            from typing import List
            from os import path as p
            CONST = 1
            a, (b, c) = 1, (2, 3)
            d = e = 4
            f = 0
            f += 1
            for i in range(3):
                pass
            with open(__file__) as fh:
                pass
            try:
                pass
            except Exception as exc:
                pass
            if (n := 5) > 3:
                pass
            class K: pass
            match CONST:
                case [first, *rest]:
                    pass
                case {"k": v, **others}:
                    pass
                case K(x=px) if px:
                    pass
                case _:
                    pass
            def fn(): pass
            async def afn(): pass
            def uses_all():
                return (os, system, List, p, CONST, a, b, c, d, e, f, i, fh, exc, n,
                        first, rest, v, others, px, fn, K, afn)
        """)

    def test_builtins_and_dunders(self):
        self.assertClean("""
            def f():
                print(len([]), __name__, __file__, __doc__, __builtins__)
            class C:
                NAME = __qualname__ + __module__
        """)

    def test_function_locals_parameters_and_closures(self):
        self.assertClean("""
            def outer(a, /, b, *args, c=1, **kw):
                local = a + b
                def inner():
                    nonlocal local
                    local += c
                    return args, kw, local
                lam = lambda x, y=local: x + y + inner()
                return lam
        """)

    def test_global_statement_binds_the_module_name(self):
        self.assertClean("""
            def init():
                global _CACHE
                _CACHE = {}
            def read():
                return _CACHE
        """)

    def test_comprehensions_and_generator_expressions(self):
        self.assertClean("""
            def f(rows):
                a = [x for x in rows if x]
                b = {k: v for k, v in rows}
                c = {y for y in rows}
                d = list(z for z in rows for w in z if w)
                e = [[i * j for j in range(i)] for i in range(3)]
                return a, b, c, d, e
            TOP = [q for q in range(3)]
            GEN = sum(q for q in range(3))
        """)

    def test_conditional_and_try_except_imports(self):
        self.assertClean("""
            try:
                import tomllib
            except ImportError:
                tomllib = None
            if tomllib is None:
                import json as tomllib
            try:
                from importlib import metadata
            except ImportError:  # pragma: no cover
                metadata = None
            def f():
                return tomllib, metadata
        """)

    def test_class_attributes_and_self_access(self):
        self.assertClean("""
            class Gate:
                TIMEOUT = 5
                def __init__(self, workdir):
                    self.workdir = workdir
                def run(self):
                    return self.workdir, self.TIMEOUT, Gate.TIMEOUT
                @staticmethod
                def helper(x):
                    return x
                @property
                def ok(self):
                    return True
        """)

    def test_names_defined_later_in_the_module(self):
        # Function bodies run after the module finishes loading.
        self.assertClean("""
            def f():
                return LATER + g()
            def g():
                return 1
            LATER = 2
        """)

    def test_annotations(self):
        self.assertClean("""
            from __future__ import annotations
            def f(x: NotDefinedAnywhere) -> AlsoNot:
                y: NorThis = 1
                return y
            class C:
                attr: Nope
        """)
        # Without the future import a function-local annotation is still never evaluated.
        self.assertClean("""
            def f():
                y: NotEvaluated = 1
                return y
        """)

    def test_loop_and_with_targets_inside_functions(self):
        self.assertClean("""
            def f(items):
                total = 0
                for idx, (k, v) in enumerate(items):
                    total += idx
                with open("x") as fh, open("y") as (gh):
                    pass
                while (line := fh.readline()):
                    total += len(line)
                return total, k, v, gh
        """)

    def test_del_and_augmented_assignment(self):
        self.assertClean("""
            def f():
                count = 0
                count += 1
                del count
        """)

    def test_type_aliases_and_generics(self):
        self.assertClean("""
            from typing import Generic, TypeVar
            T = TypeVar("T")
            class Box(Generic[T]):
                def get(self) -> T:
                    return self.item
            def first[U](xs: list[U]) -> U:
                return xs[0]
            type Pair[V] = tuple[V, V]
        """)

    def test_star_import_disables_the_check(self):
        self.assertEqual(_check("""
            from os.path import *
            def f():
                return clearly_undefined
        """), [])

    def test_dynamic_globals_disable_the_check(self):
        for marker in ("exec('x = 1')", "globals()['x'] = 1", "globals().update({'x': 1})"):
            findings = _check(f"""
                {marker}
                def f():
                    return x
            """)
            self.assertEqual(findings, [], marker)

    def test_repository_modules_have_no_findings(self):
        # The checker gates synthesised reimplementations of these very files.
        for rel in ("hae/evaluation/harness.py", "hae/evaluation/artifacts.py",
                    "hae/evaluation/verification_loop.py", "hae/genome/morphogenesis.py",
                    "hae/epistemic/gatekeeper.py", "hae/epistemic/ledger.py",
                    "hae/epistemic/static_check.py"):
            with open(os.path.join(REPO_ROOT, rel), encoding="utf-8") as fh:
                findings = undefined_names(fh.read(), filename=rel)
            self.assertEqual(findings, [], f"{rel}: {[f.describe() for f in findings]}")


class CommandLineTests(unittest.TestCase):

    def test_main_prints_findings_and_returns_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "m.py")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("def f():\n    return oops\n")
            buf = StringIO()
            with redirect_stdout(buf):
                rc = main(path)
            self.assertEqual(rc, 1)
            self.assertEqual(buf.getvalue().strip(), f"{path}: undefined name 'oops' at line 2 in f")

    def test_main_is_silent_and_returns_zero_for_a_clean_module(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "m.py")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("def f():\n    return 1\n")
            buf = StringIO()
            with redirect_stdout(buf):
                rc = main(path)
            self.assertEqual(rc, 0)
            self.assertEqual(buf.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
