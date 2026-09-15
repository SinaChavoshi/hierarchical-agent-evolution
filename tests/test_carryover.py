"""Artifact carryover between generations.

Carryover turns the loop from "evolve an organisation that makes one-shot
attempts" into "iteratively improve a deliverable". The risk it introduces is
bookkeeping: once a firm can inherit files, "this firm produced 12 files" and
"this firm was handed 11 and wrote 1" look identical unless provenance is
tracked. Every artifact-count trend in the V1 record would have become
meaningless.
"""

import unittest

from hae.genome.schema import AgentGenome, CompanyGenome, DepartmentGenome
from hae.runtime.company import HierarchicalCompanyRunner


def _genome():
    return CompanyGenome(
        company_id="carryover_probe",
        generation=2,
        ceo=AgentGenome(role="CEO", goal="lead", model_tier="executive"),
        departments=[DepartmentGenome(
            dept_id="dept_eng", name="Engineering", mandate="build",
            manager=AgentGenome(role="Eng Manager", goal="manage"),
            agents=[AgentGenome(role="Engineer", goal="write code")])],
    )


class CarryoverTest(unittest.TestCase):

    def tearDown(self):
        if getattr(self, "runner", None):
            self.runner.workspace.cleanup()

    def test_seed_files_land_in_the_workspace(self):
        self.runner = HierarchicalCompanyRunner(
            _genome(), seed_files={"pkg/core.py": "VALUE = 1\n"})
        bundle = self.runner.workspace.export_bundle()
        self.assertIn("pkg/core.py", bundle)
        self.assertEqual(bundle["pkg/core.py"], "VALUE = 1\n")

    def test_no_seed_files_means_an_empty_workspace(self):
        self.runner = HierarchicalCompanyRunner(_genome())
        self.assertEqual(self.runner.workspace.export_bundle(), {})
        self.assertEqual(self.runner.seeded_files, {})

    def test_inherited_files_are_recorded_separately(self):
        self.runner = HierarchicalCompanyRunner(
            _genome(), seed_files={"a.py": "1\n", "b.py": "2\n"})
        self.assertEqual(sorted(self.runner.seeded_files), ["a.py", "b.py"])

    def test_an_untouched_inherited_file_is_not_credited_as_authored(self):
        """The distinction the artifact-count trend depends on."""
        self.runner = HierarchicalCompanyRunner(
            _genome(), seed_files={"kept.py": "1\n", "edited.py": "old\n"})
        self.runner.workspace.write_file("edited.py", "new\n")
        self.runner.workspace.write_file("fresh.py", "3\n")

        bundle = self.runner.workspace.export_bundle()
        seeded = self.runner.seeded_files
        authored = sorted(
            p for p in bundle
            if p not in seeded or bundle[p] != seeded.get(p))

        self.assertEqual(authored, ["edited.py", "fresh.py"])
        self.assertNotIn("kept.py", authored)

    def test_seeded_files_are_copied_not_aliased(self):
        """Mutating the caller's dict afterwards must not rewrite history."""
        seed = {"a.py": "1\n"}
        self.runner = HierarchicalCompanyRunner(_genome(), seed_files=seed)
        seed["a.py"] = "tampered\n"
        self.assertEqual(self.runner.seeded_files["a.py"], "1\n")


if __name__ == "__main__":
    unittest.main()
