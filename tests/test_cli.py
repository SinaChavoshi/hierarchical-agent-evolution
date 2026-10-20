"""Unit tests for `hae.cli` including `--mode ratchet` and existing CLI modes."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest

from hae.cli import parse_args, run_ratchet_mode, swebench_argv


class TestCli(unittest.TestCase):
    def test_swebench_argv_passthrough(self) -> None:
        self.assertIsNone(swebench_argv(["--mode", "tournament"]))
        self.assertEqual(
            swebench_argv(["--mode", "swebench", "--instance-id", "demo__pkg-1"]),
            ["--instance-id", "demo__pkg-1"],
        )
        self.assertEqual(
            swebench_argv(["--mode=swebench", "--executor", "local"]),
            ["--executor", "local"],
        )

    def test_parse_args_ratchet_mode(self) -> None:
        args = parse_args(
            [
                "--mode",
                "ratchet",
                "--milestone-spec",
                "specs/milestones.json",
                "--workspace-dir",
                "/tmp/my_workspace",
                "--resume",
            ]
        )
        self.assertEqual(args.mode, "ratchet")
        self.assertEqual(args.milestone_spec, "specs/milestones.json")
        self.assertEqual(args.workspace_dir, "/tmp/my_workspace")
        self.assertTrue(args.resume)

    def test_run_ratchet_mode_with_declarative_spec(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init", "-q"], cwd=tmp, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp, check=True)
            with open(os.path.join(tmp, "mod.py"), "w", encoding="utf-8") as fh:
                fh.write("X = 42\n")
            subprocess.run(["git", "add", "mod.py"], cwd=tmp, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp, check=True)

            spec_path = os.path.join(tmp, "milestones.json")
            with open(spec_path, "w", encoding="utf-8") as fh:
                json.dump(
                    {
                        "system_objective": "Verify pre-seeded module",
                        "milestones": [
                            {
                                "milestone_id": "m1_check",
                                "title": "Verify X == 42",
                                "objective": "Ensure mod.X is 42",
                                "depends_on": [],
                                "target_files": ["mod.py"],
                                "acceptance_cmd": f"{sys.executable} -c 'import mod; assert mod.X == 42'",
                                "status": "certified",
                            }
                        ],
                    },
                    fh,
                )

            args = parse_args(
                [
                    "--mode",
                    "ratchet",
                    "--milestone-spec",
                    spec_path,
                    "--workspace-dir",
                    tmp,
                ]
            )
            rc = run_ratchet_mode(args)
            self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
