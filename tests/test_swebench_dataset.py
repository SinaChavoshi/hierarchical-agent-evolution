"""SWE-bench records: the firm-facing task carries no gold fields; fetch, slice and leakage checks."""

import ast
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, ".")

from hae.swebench import dataset as D  # noqa: E402

REC = {
    "repo": "owner/proj-python", "instance_id": "owner__proj-python-7", "base_commit": "abc123",
    "patch": "diff --git a/x.py b/x.py\n", "test_patch": "diff --git a/tests/test_x.py b/tests/test_x.py\n",
    "problem_statement": "Title line\n\nBody of the issue.", "hints_text": "try changing x",
    "created_at": "2020-01-01T00:00:00Z", "version": "1.2", "FAIL_TO_PASS": "[\"tests/test_x.py::test_a\"]",
    "PASS_TO_PASS": "[\"tests/test_x.py::test_b\"]", "environment_setup_commit": "def456",
}

RUNTIME_MODULES = ("hae/swebench/runner.py", "hae/swebench/task.py", "hae/swebench/export.py",
                   "hae/epistemic/gatekeeper.py", "hae/swebench/executor.py")
GOLD_TOKENS = {"GradingInfo", "load_grading_info", "FAIL_TO_PASS", "PASS_TO_PASS", "test_patch"}


def _code_tokens(path):
    """Names, attributes and string literals used by *code* -- docstrings excluded."""
    with open(path, "r", encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), path)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.body and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant) \
                    and isinstance(node.body[0].value.value, str):
                docstrings.add(id(node.body[0].value))
    toks = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            toks.add(node.id)
        elif isinstance(node, ast.Attribute):
            toks.add(node.attr)
        elif isinstance(node, ast.alias):
            toks.add(node.name.split(".")[-1])
            if node.asname:
                toks.add(node.asname)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            for tok in GOLD_TOKENS:
                if tok in node.value:
                    toks.add(tok)
    return toks


class TaskRecordTests(unittest.TestCase):
    def test_task_never_carries_grading_fields(self):
        task = D.SweTask.from_record(REC)
        as_dict = task.to_dict()
        for gold in D.GRADING_FIELDS:
            self.assertNotIn(gold, as_dict)
            self.assertFalse(hasattr(task, gold), gold)
        self.assertEqual(set(D.TASK_FIELDS) | {"hints_text"}, set(as_dict))
        self.assertFalse(set(D.TASK_FIELDS) & set(D.GRADING_FIELDS))
        self.assertEqual(task.title, "Title line")
        self.assertEqual(task.repo_name, "proj-python")
        self.assertEqual(task.version, "1.2")

    def test_hints_dropped_unless_requested(self):
        self.assertEqual(D.SweTask.from_record(REC).hints_text, "")
        self.assertEqual(D.SweTask.from_record(REC, include_hints=True).hints_text, "try changing x")

    def test_missing_core_field_rejected(self):
        bad = dict(REC)
        bad["problem_statement"] = ""
        with self.assertRaises(ValueError):
            D.SweTask.from_record(bad)

    def test_grading_info_is_separate_and_parses_lists(self):
        info = D.GradingInfo.from_record(REC)
        self.assertEqual(info.FAIL_TO_PASS, ["tests/test_x.py::test_a"])
        self.assertEqual(info.PASS_TO_PASS, ["tests/test_x.py::test_b"])
        self.assertTrue(info.patch.startswith("diff --git"))
        self.assertIsNot(type(info), D.SweTask)

    def test_image_name_follows_upstream_rule(self):
        self.assertEqual(D.instance_image("astropy__astropy-12907"),
                         "swebench/sweb.eval.x86_64.astropy_1776_astropy-12907:latest")
        self.assertEqual(D.instance_image("Owner__Proj-1", tag="v2"),
                         "swebench/sweb.eval.x86_64.owner_1776_proj-1:v2")
        self.assertEqual(D.SweTask.from_record(REC).image,
                         "swebench/sweb.eval.x86_64.owner_1776_proj-python-7:latest")


class FilesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "split.jsonl")
        other = dict(REC, instance_id="owner__proj-python-8", repo="owner/proj-python")
        D.write_jsonl(self.path, [REC, other])

    def tearDown(self):
        self.tmp.cleanup()

    def test_round_trip_and_lookup(self):
        self.assertEqual(D.instance_ids(self.path), {"owner__proj-python-7", "owner__proj-python-8"})
        tasks = D.load_tasks(self.path)
        self.assertEqual([t.instance_id for t in tasks], ["owner__proj-python-7", "owner__proj-python-8"])
        self.assertEqual(D.find_task(self.path, "owner__proj-python-8").instance_id, "owner__proj-python-8")
        with self.assertRaises(KeyError):
            D.find_task(self.path, "nope")
        info = D.load_grading_info(self.path, "owner__proj-python-7")
        self.assertEqual(info.instance_id, "owner__proj-python-7")

    def test_dev_slice_round_trip(self):
        out = os.path.join(self.tmp.name, "slice.json")
        info = D.select_dev_slice([REC], n=1)
        D.write_dev_slice(out, info)
        self.assertEqual(D.read_dev_slice(out)["instance_ids"], ["owner__proj-python-7"])


class FetchTests(unittest.TestCase):
    def test_paginates_until_total_and_counts_truncation(self):
        rows = [{"row_idx": i, "row": {"instance_id": f"r__r-{i}", "repo": "r/r"},
                 "truncated_cells": ["problem_statement"] if i == 3 else []} for i in range(7)]
        calls = []

        def fake(url, timeout=60.0, retries=3):
            calls.append(url)
            off = int(url.split("offset=")[1].split("&")[0])
            return {"num_rows_total": 7, "rows": rows[off: off + 3]}

        out = D.fetch_split("princeton-nlp/SWE-bench", "dev", page=3, fetch=fake)
        self.assertEqual([r["instance_id"] for r in out["rows"]], [f"r__r-{i}" for i in range(7)])
        self.assertEqual(out["num_rows_total"], 7)
        self.assertEqual(out["truncated_rows"], 1)
        self.assertEqual(len(calls), 3)
        self.assertIn("dataset=princeton-nlp%2FSWE-bench", calls[0])
        self.assertIn("split=dev", calls[0])

    def test_splits_table(self):
        self.assertEqual(D.SPLITS["swebench_dev"], ("princeton-nlp/SWE-bench", "dev"))
        self.assertEqual(D.SPLITS["lite_dev"], ("princeton-nlp/SWE-bench_Lite", "dev"))
        self.assertEqual(D.SPLITS["verified_test"], ("princeton-nlp/SWE-bench_Verified", "test"))


class DevSliceTests(unittest.TestCase):
    def records(self):
        recs = []
        for repo, n in (("a/a", 10), ("b/b", 4), ("c/c", 1)):
            for i in range(n):
                recs.append({"instance_id": f"{repo.replace('/', '__')}-{i}", "repo": repo})
        return recs

    def test_deterministic_and_repo_balanced(self):
        one = D.select_dev_slice(self.records(), n=9, seed=0)
        two = D.select_dev_slice(self.records(), n=9, seed=0)
        self.assertEqual(one["instance_ids"], two["instance_ids"])
        self.assertEqual(one["n"], 9)
        self.assertEqual(one["repo_counts"], {"a/a": 4, "b/b": 4, "c/c": 1})
        self.assertEqual(one["pool_size"], 15)
        self.assertNotEqual(one["instance_ids"], D.select_dev_slice(self.records(), n=9, seed=1)["instance_ids"])

    def test_eligible_restricts_pool_and_dedups(self):
        recs = self.records() + [self.records()[0]]
        out = D.select_dev_slice(recs, n=50, eligible=["a__a-1", "b__b-2", "zzz"])
        self.assertEqual(sorted(out["instance_ids"]), ["a__a-1", "b__b-2"])
        self.assertEqual(out["pool_size"], 2)

    def test_committed_slice_is_consistent(self):
        path = os.path.join("data", "swebench", "dev_slice_50.json")
        if not os.path.exists(path):
            self.skipTest("slice file not present")
        with open(path) as fh:
            info = json.load(fh)
        ids = info["instance_ids"]
        self.assertEqual(len(ids), 50)
        self.assertEqual(len(set(ids)), 50)
        self.assertEqual(sum(info["repo_counts"].values()), 50)
        self.assertTrue(all("__" in i for i in ids))
        self.assertTrue(info.get("restricted_to_published_images"))
        self.assertTrue(all(info["image_published"].get(i) is True for i in ids))


class LeakageTests(unittest.TestCase):
    """The firm's code path must not touch the gold fields, not even by name."""

    def test_runtime_modules_do_not_name_grading_fields(self):
        for rel in RUNTIME_MODULES:
            toks = _code_tokens(rel)
            self.assertFalse(toks & GOLD_TOKENS, f"{rel} references {sorted(toks & GOLD_TOKENS)}")

    def test_runtime_modules_do_not_read_patch_column(self):
        # `patch` is a common word; what matters is that no record subscript or
        # attribute reads it. Scan for Subscript/Attribute with that literal.
        for rel in RUNTIME_MODULES:
            with open(rel, "r", encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), rel)
            hits = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) \
                        and node.slice.value in ("patch", "test_patch"):
                    hits.append(ast.unparse(node))
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get" \
                        and node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value in ("patch", "test_patch"):
                    hits.append(ast.unparse(node))
            self.assertEqual(hits, [], f"{rel} reads a gold column: {hits}")

    def test_only_dataset_defines_grading_loader(self):
        import hae.swebench.runner as runner
        import hae.swebench.task as task
        import hae.swebench.export as export
        import hae.epistemic.gatekeeper as gk
        for mod in (runner, task, export, gk):
            self.assertFalse(hasattr(mod, "load_grading_info"), mod.__name__)
            self.assertFalse(hasattr(mod, "GradingInfo"), mod.__name__)


if __name__ == "__main__":
    unittest.main()
