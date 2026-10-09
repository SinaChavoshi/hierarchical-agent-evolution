"""Issue-seeded search: one root reproduction question, at most three path sub-questions, no oracle."""

import sys
import unittest

sys.path.insert(0, ".")

from hae.epistemic.gatekeeper import EvidenceGatekeeper  # noqa: E402
from hae.epistemic.ledger import EpistemicState, GatekeeperAuthority  # noqa: E402
from hae.swebench import task as T  # noqa: E402
from hae.swebench.dataset import SweTask  # noqa: E402

TRACEBACK_STATEMENT = """Table.read fails on empty header

**Description**
Reading a file with an empty header raises.

```
Traceback (most recent call last):
  File "/usr/lib/python3.8/site-packages/astropy/io/ascii/ui.py", line 376, in read
    dat = reader.read(table)
  File "/usr/lib/python3.8/site-packages/astropy/io/ascii/core.py", line 1300, in read
    self.header.get_cols(self.lines)
IndexError: list index out of range
```
Also see `astropy/table/table.py` for the caller and docs/index.rst.
"""

NO_PATH_STATEMENT = "Wrong units after conversion\n\nConverting from m to km multiplies instead of dividing.\n"

KNOWN = ["astropy/io/ascii/ui.py", "astropy/io/ascii/core.py", "astropy/io/ascii/__init__.py",
         "astropy/table/table.py", "astropy/table/core.py", "astropy/units/core.py", "setup.py",
         "astropy/io/ascii/tests/test_read.py", "docs/conf.py"]


def make_task(statement, repo="astropy/astropy", iid="astropy__astropy-1"):
    return SweTask(instance_id=iid, repo=repo, base_commit="deadbeef", version="5.0", problem_statement=statement)


def gatekeeper_authority():
    return EvidenceGatekeeper(workspace=None, timeout_s=5).authority


class StatementParsingTests(unittest.TestCase):
    def test_relativize_handles_site_packages_src_and_relative(self):
        self.assertEqual(T.relativize("/usr/lib/python3.8/site-packages/astropy/io/ascii/core.py", "astropy/astropy"),
                         "astropy/io/ascii/core.py")
        self.assertEqual(T.relativize("/home/me/sqlfluff/src/sqlfluff/core/parser.py", "sqlfluff/sqlfluff"),
                         "src/sqlfluff/core/parser.py")
        self.assertEqual(T.relativize("./pkg/mod.py", "o/pkg"), "pkg/mod.py")
        self.assertEqual(T.relativize("/a/b/c/d/e.py", "o/unrelated"), "c/d/e.py")

    def test_traceback_frames_and_primary_failure(self):
        frames = T.traceback_frames(TRACEBACK_STATEMENT)
        self.assertEqual([f[2] for f in frames], ["read", "read"])
        self.assertEqual(frames[1][1], 1300)
        self.assertEqual(T.primary_failure(TRACEBACK_STATEMENT), "IndexError: list index out of range")
        self.assertEqual(T.primary_failure(NO_PATH_STATEMENT), "")
        self.assertEqual(T.exception_lines("ValueError: a\nnot one\nTypeError: b\nValueError: a"),
                         ["ValueError: a", "TypeError: b"])

    def test_extract_paths_prefers_traceback_frames_deepest_first(self):
        paths = T.extract_paths(TRACEBACK_STATEMENT, "astropy/astropy")
        self.assertEqual(paths[:2], ["astropy/io/ascii/core.py", "astropy/io/ascii/ui.py"])
        self.assertIn("astropy/table/table.py", paths)
        self.assertNotIn("docs/index.rst", paths)
        self.assertEqual(T.extract_paths(NO_PATH_STATEMENT, "astropy/astropy"), [])

    def test_resolve_paths_exact_suffix_and_unique_basename(self):
        self.assertEqual(T.resolve_paths(["astropy/io/ascii/core.py"], KNOWN), ["astropy/io/ascii/core.py"])
        self.assertEqual(T.resolve_paths(["ascii/ui.py"], KNOWN), ["astropy/io/ascii/ui.py"])
        self.assertEqual(T.resolve_paths(["core.py"], KNOWN), [])  # ambiguous basename
        self.assertEqual(T.resolve_paths(["table.py"], KNOWN), ["astropy/table/table.py"])
        self.assertEqual(T.resolve_paths(["nope/missing.py", "setup.py", "setup.py"], KNOWN), ["setup.py"])

    def test_quoted_lines_and_texts(self):
        task = make_task(TRACEBACK_STATEMENT)
        quotes = T.quoted_lines(TRACEBACK_STATEMENT, "astropy/io/ascii/core.py")
        self.assertTrue(quotes and "core.py" in quotes[0])
        self.assertTrue(T.root_question_text(task).startswith("Reproduce the reported behaviour: Table.read fails"))
        self.assertIn("(traceback line 1300)", T.sub_question_text(task, "astropy/io/ascii/core.py", quotes, line=1300))
        self.assertEqual(T.failure_key(task), "swebench:astropy__astropy-1")
        self.assertEqual(T.sub_question_key(task, "a/b.py"), "swebench:astropy__astropy-1:a/b.py")


class SeedingTests(unittest.TestCase):
    def test_requires_gatekeeper_authority(self):
        state = EpistemicState("firm")
        with self.assertRaises(TypeError):
            T.seed_from_problem_statement(state, make_task(TRACEBACK_STATEMENT), authority=object())
        # A forged token of the right type is accepted by design: the type is
        # the capability, and only the gatekeeper constructs one in production.
        self.assertIsInstance(gatekeeper_authority(), GatekeeperAuthority)

    def test_root_plus_at_most_three_sub_questions(self):
        state = EpistemicState("firm")
        task = make_task(TRACEBACK_STATEMENT)
        seed = T.seed_from_problem_statement(state, task, gatekeeper_authority(), known_files=KNOWN)
        root = state.questions[seed["root_question_id"]]
        self.assertEqual(root.uncertainty, 1.0)
        self.assertEqual(root.source_failure_key, "swebench:astropy__astropy-1")
        self.assertEqual(root.module, "astropy/io/ascii/core.py")
        self.assertEqual(root.source_failure, "IndexError: list index out of range")
        self.assertEqual(root.location, "astropy/io/ascii/core.py:1300")
        self.assertEqual(seed["paths"], ["astropy/io/ascii/core.py", "astropy/io/ascii/ui.py", "astropy/table/table.py"])
        self.assertEqual(len(seed["sub_question_ids"]), 3)
        self.assertLessEqual(len(seed["sub_question_ids"]), T.MAX_SUB_QUESTIONS)
        for qid in seed["sub_question_ids"]:
            q = state.questions[qid]
            self.assertEqual(q.uncertainty, T.SUB_QUESTION_UNCERTAINTY)
            self.assertEqual(q.source_failure, "")
            self.assertTrue(q.source_failure_key.startswith("swebench:astropy__astropy-1:"))
        self.assertEqual(len(state.questions), 4)
        self.assertEqual(len(state.hypotheses), 0)
        self.assertEqual(T.root_question(state, task).question_id, root.question_id)

    def test_sub_question_cap_is_respected(self):
        state = EpistemicState("firm")
        seed = T.seed_from_problem_statement(state, make_task(TRACEBACK_STATEMENT), gatekeeper_authority(),
                                             known_files=KNOWN, max_sub_questions=1)
        self.assertEqual(len(seed["sub_question_ids"]), 1)
        self.assertEqual(len(state.questions), 2)

    def test_reseeding_is_idempotent(self):
        state = EpistemicState("firm")
        task = make_task(TRACEBACK_STATEMENT)
        a = T.seed_from_problem_statement(state, task, gatekeeper_authority(), known_files=KNOWN)
        b = T.seed_from_problem_statement(state, task, gatekeeper_authority(), known_files=KNOWN)
        self.assertEqual(a["root_question_id"], b["root_question_id"])
        self.assertEqual(a["sub_question_ids"], b["sub_question_ids"])
        self.assertEqual(len(state.questions), 4)

    def test_statement_without_path_seeds_only_the_root(self):
        state = EpistemicState("firm")
        task = make_task(NO_PATH_STATEMENT, iid="astropy__astropy-2")
        seed = T.seed_from_problem_statement(state, task, gatekeeper_authority(), known_files=KNOWN)
        self.assertEqual(seed["sub_question_ids"], [])
        self.assertEqual(seed["primary_module"], "")
        root = state.questions[seed["root_question_id"]]
        self.assertEqual(root.module, "")
        self.assertEqual(root.source_failure, "Wrong units after conversion")  # title stands in for the exception
        self.assertEqual(root.uncertainty, 1.0)

    def test_without_known_files_absolute_candidates_are_relativised(self):
        state = EpistemicState("firm")
        seed = T.seed_from_problem_statement(state, make_task(TRACEBACK_STATEMENT), gatekeeper_authority())
        self.assertEqual(seed["paths"][0], "astropy/io/ascii/core.py")
        self.assertTrue(all(not p.startswith("/") for p in seed["paths"]))


class PromptContextTests(unittest.TestCase):
    def test_repo_map_ranks_issue_paths_then_siblings_then_tokens(self):
        task = make_task(TRACEBACK_STATEMENT)
        paths = ["astropy/io/ascii/core.py"]
        ranked = T.build_repo_map(KNOWN + ["x/y.txt"], task, paths, cap=5)
        self.assertEqual(ranked[0], "astropy/io/ascii/core.py")
        self.assertIn("astropy/io/ascii/ui.py", ranked[:3])
        self.assertEqual(len(ranked), 5)
        self.assertTrue(all(f.endswith(".py") for f in ranked))
        tests_idx = ranked.index("astropy/io/ascii/tests/test_read.py") if "astropy/io/ascii/tests/test_read.py" in ranked else 99
        self.assertGreater(tests_idx, ranked.index("astropy/io/ascii/ui.py"))

    def test_repo_map_falls_back_to_shallow_files(self):
        task = make_task("nothing relevant here", repo="o/zzz")
        ranked = T.build_repo_map(["deep/er/mod.py", "top.py", "mid/m.py"], task, (), cap=2)
        self.assertEqual(ranked, ["top.py", "mid/m.py"])
        self.assertEqual(T.build_repo_map([], task), [])

    def test_module_view_is_whole_when_small_and_windowed_when_large(self):
        small = "def a():\n    return 1\n"
        self.assertEqual(T.module_view(small, [1]), small)
        big = "\n".join(f"x{i} = {i}" if i % 50 else f"def func_{i}():\n    return {i}" for i in range(1, 2001))
        view = T.module_view(big, focus_lines=[1000], max_chars=4000, window=5)
        self.assertLessEqual(len(view), 4200)
        self.assertIn("MODULE IS LARGE", view)
        self.assertIn(" 1000| ", view)
        self.assertIn("---- lines 995-1005 ----", view)
        self.assertIn("def func_", view)  # definitions index

    def test_focus_lines_from_traceback_and_named_definitions(self):
        source = "class Reader:\n    pass\n\ndef read(x):\n    return x\n\ndef other():\n    pass\n"
        lines = T.focus_lines_for(source, TRACEBACK_STATEMENT, "astropy/io/ascii/core.py")
        self.assertIn(1300, lines)   # traceback line for core.py
        self.assertIn(4, lines)      # `read` is named in the statement
        self.assertNotIn(7, lines)

    def test_focus_lines_includes_extra_text_line_refs_and_definitions(self):
        source = "\n".join(f"x{i} = {i}" for i in range(1, 500)) + "\ndef root(self):\n    return None\n"
        # Statement mentions neither `root` nor lines 411-420; hypothesis extra_text names both.
        extra = "In `marshmallow/fields.py` (lines 411-420), `Field.root` traverses parent hierarchy."
        lines = T.focus_lines_for(source, "Something broke in schema", "src/marshmallow/fields.py", extra_text=extra)
        self.assertIn(411, lines)
        self.assertIn(420, lines)
        self.assertIn(500, lines)    # `def root` at line 500

    def test_is_test_path_and_hypothesis_target_module(self):
        self.assertTrue(T.is_test_path("tests/test_fields.py"))
        self.assertTrue(T.is_test_path("astropy/io/ascii/tests/test_read.py"))
        self.assertTrue(T.is_test_path("pkg/mod_test.py"))
        self.assertTrue(T.is_test_path("conftest.py"))
        self.assertFalse(T.is_test_path("src/marshmallow/fields.py"))
        self.assertFalse(T.is_test_path("astropy/io/ascii/core.py"))

        known = [
            "src/marshmallow/schema.py",
            "src/marshmallow/fields.py",
            "src/marshmallow/base.py",
            "tests/test_fields.py",
        ]
        # Hypothesis on q1 (seeded at schema.py) that locates the bug in fields.py resolves to fields.py.
        target = T.hypothesis_target_module(
            "src/marshmallow/schema.py",
            "Calling load raises AttributeError in Field.root when parent is a Dict field.",
            "In `marshmallow/fields.py` (lines 411-420), `Field.root` walks `root.parent`.",
            known,
            repo="marshmallow-code/marshmallow",
        )
        self.assertEqual(target, "src/marshmallow/fields.py")
        # Mentioning only a test path (or no path) preserves question_module.
        self.assertEqual(
            T.hypothesis_target_module(
                "src/marshmallow/schema.py",
                "See tests/test_fields.py for reproduction",
                "The helper fails on nested dict",
                known,
                repo="marshmallow-code/marshmallow",
            ),
            "src/marshmallow/schema.py",
        )
        self.assertEqual(
            T.hypothesis_target_module(
                "src/marshmallow/schema.py",
                "No path mentioned here",
                " Nor in mechanism",
                known,
                repo="marshmallow-code/marshmallow",
            ),
            "src/marshmallow/schema.py",
        )

    def test_task_features_are_llm_free(self):
        feats = T.task_features(make_task(TRACEBACK_STATEMENT), ["astropy/io/ascii/core.py"]).to_dict()
        self.assertEqual(feats["n_exception_classes"], 1)
        self.assertIn("indexerror", feats["tags"])
        self.assertEqual(feats["source"], "problem_statement")


if __name__ == "__main__":
    unittest.main()

