"""SWE-bench scripts: argv construction for the Docker batch, prediction merging, split disjointness."""

import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, ".")

from hae.swebench.export import write_prediction  # noqa: E402


def load_script(name):
    path = os.path.join("scripts", name)
    spec = importlib.util.spec_from_file_location(name[:-3], path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class BatchArgvTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.B = load_script("swebench_docker_batch.py")

    def test_image_and_container_names(self):
        B = self.B
        self.assertEqual(B.image_name("sqlfluff__sqlfluff-1625"),
                         "swebench/sweb.eval.x86_64.sqlfluff_1776_sqlfluff-1625:latest")
        self.assertEqual(B.container_name("sqlfluff__sqlfluff-1625", "v8c"), "hae_v8c_sqlfluff__sqlfluff-1625")
        self.assertEqual(B.container_name("a b/c", ""), "hae_a_b_c")

    def test_docker_argv_builders(self):
        B = self.B
        img = "swebench/sweb.eval.x86_64.x_1776_y-1:latest"
        self.assertEqual(B.docker_pull_argv(img), ["docker", "pull", img])
        self.assertEqual(B.docker_run_argv(img, "hae_x"), ["docker", "run", "-d", "--name", "hae_x", img, "sleep", "infinity"])
        self.assertEqual(B.docker_rm_argv("hae_x", docker="/usr/bin/docker"), ["/usr/bin/docker", "rm", "-f", "hae_x"])

    def test_instance_argv_targets_the_docker_executor(self):
        argv = self.B.instance_argv("x__y-1", "data/swebench/swebench_dev.jsonl", "hae_x", "g.json", "out/x__y-1",
                                    extra=["--budget-moves", "30"], python="/py")
        self.assertEqual(argv[0], "/py")
        self.assertTrue(argv[1].endswith(os.path.join("scripts", "run_swebench_instance.py")))
        self.assertEqual(argv[argv.index("--executor") + 1], "docker")
        self.assertEqual(argv[argv.index("--container") + 1], "hae_x")
        self.assertEqual(argv[argv.index("--instance-id") + 1], "x__y-1")
        self.assertEqual(argv[-2:], ["--budget-moves", "30"])

    def test_select_instances_skips_unpublished_unless_asked(self):
        info = {"instance_ids": ["a__a-1", "b__b-1", "c__c-1"], "image_published": {"a__a-1": True, "b__b-1": False}}
        self.assertEqual(self.B.select_instances(info), {"run": ["a__a-1", "c__c-1"], "skipped_unpublished": ["b__b-1"]})
        self.assertEqual(self.B.select_instances(info, include_unpublished=True)["run"], ["a__a-1", "b__b-1", "c__c-1"])
        self.assertEqual(self.B.select_instances(info, n=1)["run"], ["a__a-1"])
        self.assertEqual(self.B.select_instances(info, explicit=["z__z-9", "z__z-9"]), {"run": ["z__z-9"], "skipped_unpublished": []})

    def test_pending_and_merge_are_resumable(self):
        with tempfile.TemporaryDirectory() as root:
            write_prediction(os.path.join(root, "a__a-1", "prediction.json"),
                             {"instance_id": "a__a-1", "model_name_or_path": "hae-v8", "model_patch": "diff\n"})
            self.assertEqual(self.B.pending(root, ["a__a-1", "b__b-1"]), ["b__b-1"])
            merged = self.B.merge_preds(root, ["a__a-1", "b__b-1"])
            self.assertEqual(merged["count"], 1)
            with open(merged["path"]) as fh:
                rows = [json.loads(l) for l in fh if l.strip()]
            self.assertEqual(rows[0]["instance_id"], "a__a-1")
            self.assertEqual(set(rows[0]), {"instance_id", "model_name_or_path", "model_patch"})

    def test_run_one_sequence_with_fake_runner(self):
        B = self.B
        calls = []

        def fake(argv, timeout_s, log_path=None):
            calls.append(argv)
            if argv[1:3] == ["run", "-d"]:
                return {"rc": 0, "output": "cid", "timed_out": False, "elapsed_s": 0.1}
            if argv[1] == "pull":
                return {"rc": 0, "output": "", "timed_out": False, "elapsed_s": 0.1}
            if argv[1] == "rm":
                return {"rc": 0, "output": "", "timed_out": False, "elapsed_s": 0.1}
            # the instance script: emulate a successful run by writing a prediction
            out_dir = argv[argv.index("--out-dir") + 1]
            write_prediction(os.path.join(out_dir, "prediction.json"), {"instance_id": "x__y-1", "model_patch": "d\n"})
            return {"rc": 0, "output": "", "timed_out": False, "elapsed_s": 1.0}

        with tempfile.TemporaryDirectory() as root:
            args = B.parse_args(["--run-id", "t", "--genome", "g.json", "--out-root", root, "--instances", "x__y-1"])
            row = B.run_one("x__y-1", args, runner=fake)
            self.assertEqual(row["status"], "ok", row)
            kinds = [("docker " + c[1]) if c[0] == "docker" else "instance" for c in calls]
            self.assertEqual(kinds, ["docker pull", "docker rm", "docker run", "instance", "docker rm"])
            summary = B.run_batch(args, runner=fake)  # resumable: nothing left to do
            self.assertEqual(summary["already_done"], ["x__y-1"])
            self.assertEqual(summary["rows"], [])
            self.assertEqual(summary["merged"]["count"], 1)

    def test_dry_run_builds_commands_without_docker(self):
        B = self.B
        with tempfile.TemporaryDirectory() as root:
            args = B.parse_args(["--run-id", "t", "--genome", "g.json", "--out-root", root,
                                 "--instances", "x__y-1", "--dry-run"])
            with contextlib.redirect_stdout(io.StringIO()):
                summary = B.run_batch(args, runner=lambda *a, **k: self.fail("docker must not run in dry-run"))
            self.assertEqual(summary["rows"][0]["status"], "dry-run")
            self.assertEqual(summary["rows"][0]["argv"][0][:2], ["docker", "pull"])
            self.assertFalse(os.path.exists(os.path.join(root, "batch_status.json")))


class SplitCheckTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.C = load_script("check_swebench_splits.py")

    def test_disjoint_sets_pass(self):
        ids = {"dev_slice_50.json": {"a", "b"}, "swebench_dev.jsonl": {"a", "b", "c"},
               "lite_dev.jsonl": {"a"}, "verified_test.jsonl": {"x", "y"}}
        report = self.C.check_splits(ids)
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["overlaps"], {"dev_slice_50.json": 0, "swebench_dev.jsonl": 0, "lite_dev.jsonl": 0})
        self.assertTrue(report["lite_subset_of_dev"])
        self.assertIn("OK", self.C.render(report))

    def test_overlap_and_missing_files_fail(self):
        ids = {"dev_slice_50.json": {"a", "x"}, "swebench_dev.jsonl": {"a", "x"},
               "lite_dev.jsonl": None, "verified_test.jsonl": {"x"}}
        report = self.C.check_splits(ids)
        self.assertFalse(report["ok"])
        self.assertEqual(report["overlaps"]["dev_slice_50.json"], 1)
        self.assertTrue(any("shares 1 id" in p for p in report["problems"]))
        missing = self.C.check_splits({"dev_slice_50.json": {"a"}, "verified_test.jsonl": None})
        self.assertFalse(missing["ok"])
        self.assertIn("FAILED", self.C.render(missing))

    def test_slice_outside_dev_is_flagged(self):
        ids = {"dev_slice_50.json": {"zzz"}, "swebench_dev.jsonl": {"a"}, "lite_dev.jsonl": {"a"},
               "verified_test.jsonl": {"x"}}
        report = self.C.check_splits(ids)
        self.assertFalse(report["ok"])
        self.assertTrue(any("not in swebench_dev" in p for p in report["problems"]))

    def test_collect_ids_from_files(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "dev_slice_50.json"), "w") as fh:
                json.dump({"instance_ids": ["a__a-1"]}, fh)
            with open(os.path.join(d, "verified_test.jsonl"), "w") as fh:
                fh.write(json.dumps({"instance_id": "v__v-1"}) + "\n")
            ids = self.C.collect_ids(d)
            self.assertEqual(ids["dev_slice_50.json"], {"a__a-1"})
            self.assertEqual(ids["verified_test.jsonl"], {"v__v-1"})
            self.assertIsNone(ids["swebench_dev.jsonl"])
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(self.C.main(["--data-dir", d]), 0)

    def test_committed_data_is_disjoint_when_present(self):
        if not os.path.exists(os.path.join("data", "swebench", "verified_test.jsonl")):
            self.skipTest("verified_test.jsonl not fetched (never committed)")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(self.C.main([]), 0)
        self.assertIn("overlap dev_slice_50.json ∩ verified_test.jsonl: 0", buf.getvalue())


class EntryPointTests(unittest.TestCase):
    def test_instance_script_exposes_runner_main(self):
        mod = load_script("run_swebench_instance.py")
        from hae.swebench.runner import main
        self.assertIs(mod.main, main)

    def test_cli_mode_dispatch_is_pure(self):
        from hae.cli import swebench_argv
        self.assertEqual(swebench_argv(["--mode", "swebench", "--instance-id", "x", "--genome", "g"]),
                         ["--instance-id", "x", "--genome", "g"])
        self.assertEqual(swebench_argv(["--instance-id", "x", "--mode=swebench"]), ["--instance-id", "x"])
        self.assertIsNone(swebench_argv(["--mode", "tournament"]))
        self.assertIsNone(swebench_argv([]))


if __name__ == "__main__":
    unittest.main()
