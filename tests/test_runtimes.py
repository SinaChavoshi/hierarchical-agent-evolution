"""Contract tests between KubernetesRuntime and the scripts it drives.

These exist because `--mode campaign` was committed, unit-tested and declared
ready while being incapable of launching anything. `tests/test_controller.py`
injects fake `launch` and `harvest` callables -- correctly, since the control
logic must be testable without a cluster -- so all thirteen of its tests passed
over an adapter with three independent defects:

  * it never passed `--image-tag`, which `render_job.py` requires;
  * it passed `--population-file`, which `render_job.py` does not accept;
  * nothing published the population ConfigMap the Job mounts.

The first two are argparse errors. The third is worse: a Job whose ConfigMap
is absent does not fail, it sits in `ContainerCreating` indefinitely, and the
controller would have blocked on it for its full two-hour timeout.

A mock cannot catch a disagreement between two real components. The point of
what follows is to check the seam itself -- the actual argv against the actual
parser -- without needing a cluster.
"""

import os
import unittest
from unittest import mock

from hae.orchestration.runtimes import KubernetesRuntime, LaunchError
from hae.task.spec import legacy_task

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ImageTagTest(unittest.TestCase):

    def test_tag_is_required(self):
        with self.assertRaises(LaunchError):
            KubernetesRuntime(image_tag="")

    def test_latest_is_refused(self):
        """A campaign runs for hours. `latest` lets generation 3 execute
        different code from generation 1 and reports the difference as
        evolution."""
        with self.assertRaises(LaunchError):
            KubernetesRuntime(image_tag="latest")

    def test_immutable_tag_accepted(self):
        self.assertEqual(KubernetesRuntime(image_tag="v2-gen1").image_tag,
                         "v2-gen1")


class RenderJobContractTest(unittest.TestCase):
    """Every flag the runtime passes must be one render_job.py accepts."""

    def setUp(self):
        self.runtime = KubernetesRuntime(image_tag="v2-test",
                                         repo_root=REPO_ROOT)

    def _captured_argv(self):
        with mock.patch.object(KubernetesRuntime, "_kubectl", return_value=""):
            with mock.patch.object(KubernetesRuntime, "_wait"):
                with mock.patch("subprocess.run") as run:
                    run.return_value = mock.Mock(returncode=0, stdout="---\n",
                                                 stderr="")
                    with mock.patch("builtins.open", mock.mock_open()):
                        with mock.patch("os.makedirs"):
                            self.runtime(1, "/tmp/pop.json", legacy_task())
                    return run.call_args[0][0]

    def test_runtime_argv_parses(self):
        """The regression test for the original break: this raised SystemExit
        because `--image-tag` was missing and `--population-file` was not a
        thing."""
        argv = self._captured_argv()
        self.assertEqual(argv[0], "python3")
        self.assertTrue(argv[1].endswith("render_job.py"))
        self.assertIn("--image-tag", argv)
        self.assertIn("--configmap", argv)
        self.assertNotIn("--population-file", argv)

    def test_every_flag_is_known_to_render_job(self):
        """Checks the argv against render_job.py's actual option strings, so a
        flag renamed in one place and not the other fails here rather than at
        2am in an unattended campaign."""
        argv = self._captured_argv()
        with open(os.path.join(REPO_ROOT, "scripts", "render_job.py"),
                  encoding="utf-8") as fh:
            script = fh.read()
        flags = [a for a in argv if a.startswith("--")]
        self.assertTrue(flags, "runtime passed no flags at all")
        for flag in flags:
            self.assertIn(f'"{flag}"', script,
                          f"{flag} is not declared in render_job.py")

    def test_image_tag_reaches_the_argv(self):
        argv = self._captured_argv()
        self.assertEqual(argv[argv.index("--image-tag") + 1], "v2-test")


class ConfigMapPublishTest(unittest.TestCase):
    """The defect that would not have raised -- it would have hung."""

    def setUp(self):
        self.runtime = KubernetesRuntime(image_tag="v2-test",
                                         repo_root=REPO_ROOT)

    def test_population_is_published_before_the_job(self):
        calls = []
        with mock.patch.object(KubernetesRuntime, "_kubectl",
                               side_effect=lambda a, **k: calls.append(a) or ""):
            with mock.patch.object(KubernetesRuntime, "_wait"):
                with mock.patch("subprocess.run") as run:
                    run.return_value = mock.Mock(returncode=0, stdout="---\n",
                                                 stderr="")
                    with mock.patch("builtins.open", mock.mock_open()):
                        with mock.patch("os.makedirs"):
                            self.runtime(1, "/tmp/pop.json", legacy_task())

        create = next(i for i, c in enumerate(calls)
                      if c[0] == "create" and c[1] == "configmap")
        apply_at = next(i for i, c in enumerate(calls) if c[0] == "apply")
        self.assertLess(create, apply_at,
                        f"ConfigMap must exist before the Job: {calls}")

    def test_key_matches_the_path_the_worker_is_given(self):
        """The Job's command line says
        `/configs/generation_<N>_population.json`. If the ConfigMap key differs
        the pod starts and cannot find its own population."""
        calls = []
        with mock.patch.object(KubernetesRuntime, "_kubectl",
                               side_effect=lambda a, **k: calls.append(a) or ""):
            self.runtime._publish_population(7, "/tmp/pop.json")
        create = next(c for c in calls if c[0] == "create")
        from_file = next(a for a in create if a.startswith("--from-file="))
        self.assertTrue(
            from_file.startswith("--from-file=generation_7_population.json="),
            from_file)

    def test_delete_precedes_create(self):
        """`create --dry-run | apply` blows the 262144-byte annotation limit on
        a ten-genome population, so this upserts by deleting first."""
        calls = []
        with mock.patch.object(KubernetesRuntime, "_kubectl",
                               side_effect=lambda a, **k: calls.append(a) or ""):
            self.runtime._publish_population(1, "/tmp/pop.json")
        self.assertEqual(calls[0][0], "delete")
        self.assertEqual(calls[1][0], "create")


if __name__ == "__main__":
    unittest.main()
