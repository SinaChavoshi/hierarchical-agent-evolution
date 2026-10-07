"""V8 genes: `role_library` and `ceo_policy` on the genome, the seed libraries,
and the runner's turn-0 organisation selection.

Three things are locked down. First, byte identity: every archived population
file must round-trip through `CompanyGenome` unchanged, because a genome that
serialises differently after a schema change is a generation whose files can
no longer be diffed against their results. Second, the disabled path: with
`ceo_policy.enabled` false the epistemic loop must be constructed exactly as
before (no organisation kwargs, no `org` keys in the output), so the V6
cohorts' results stay comparable. Third, the enabled path up to the turn-0
selection: a deterministic, probe-and-synthesis-capable team drawn from the
genome's library or the legacy seeds.
"""

import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from hae.epistemic.gatekeeper import EvidenceGatekeeper
from hae.epistemic.mcts import EpistemicSearchLoop
from hae.epistemic.org import CEOPolicyGene as OrgCEOPolicyGene, OrgState, RoleAllele as OrgRoleAllele
from hae.evaluation.verification_loop import VerificationLoop
from hae.genome import role_seeds
from hae.genome.role_seeds import (
    SEED_FLAVOURS,
    ensure_probe_and_synthesis,
    role_library_from_departments,
    seed_role_library,
)
from hae.genome.schema import (
    MAX_ROLE_LIBRARY,
    AgentGenome,
    CEOPolicyGene,
    CompanyGenome,
    DepartmentGenome,
    GenomeValidationError,
    RoleAllele,
)
from hae.runtime import company as company_module
from hae.runtime.company import SUITE_TAG_TO_MODULE, TECHNICAL_DEPT_KEYWORDS, HierarchicalCompanyRunner
from hae.runtime.workspace import AgentWorkspace
from tests.test_epistemic_runner import BUGGY, FAILURES, OBJECTIVE, FakeSystem1

ROOT = os.path.join(os.path.dirname(__file__), "..")
CONFIGS = os.path.join(ROOT, "configs")

GENOME = {
    "company_id": "v8_firm",
    "generation": 16,
    "ceo": {"role": "CEO", "goal": "Lead", "backstory": "Exec", "temperature": 0.4, "model_tier": "executive"},
    "departments": [
        {"dept_id": "dept_systems_eng", "name": "Systems Engineering", "mandate": "Build and repair modules",
         "manager": {"role": "Eng Manager", "model_tier": "worker"},
         "agents": [{"role": "Lead Implementation Engineer", "goal": "Fix code", "backstory": "Systems",
                     "model_tier": "worker", "tools_enabled": True}]},
        {"dept_id": "dept_qa_redteam", "name": "QA & Verification", "mandate": "Verify behaviour",
         "manager": {"role": "QA Manager", "model_tier": "worker"},
         "agents": [{"role": "QA Analyst", "goal": "Verify", "backstory": "QA", "model_tier": "worker"}]},
        {"dept_id": "dept_marketing", "name": "Marketing", "mandate": "Tell the story",
         "manager": {"role": "CMO", "model_tier": "worker"},
         "agents": [{"role": "Copywriter", "goal": "Write", "backstory": "Words", "model_tier": "worker"}]},
    ],
    "epistemic_policy": {"enabled": True, "search_budget_moves": 10, "branching_k": 2,
                         "low_prior_quota": 0.0, "min_hypotheses_before_synthesis": 1,
                         "experiment_timeout_s": 20},
}


def _role(role_id, kind="both", **kw):
    data = dict(role_id=role_id, name=role_id.replace("_", " ").title(), goal="g", backstory="b",
                domain_tags=["harness"], kind=kind)
    data.update(kw)
    return RoleAllele.from_dict(data)


# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #

class GenomeRoundTripTest(unittest.TestCase):

    def _population(self, name):
        with open(os.path.join(CONFIGS, name), "r", encoding="utf-8") as fh:
            return json.load(fh)["population"]

    def test_run3_population_round_trips_byte_identically(self):
        firms = self._population("generation_16_r3_population.json")
        self.assertEqual(len(firms), 26)
        for firm in firms:
            genome = CompanyGenome.from_dict(firm)
            self.assertEqual(genome.to_dict(), firm, firm["company_id"])
            # ... and the serialised bytes, not just the dict.
            self.assertEqual(json.dumps(genome.to_dict(), sort_keys=True), json.dumps(firm, sort_keys=True))
            self.assertFalse(genome.org_enabled)
            self.assertEqual(genome.role_library, [])

    def test_v8_smoke_population_round_trips_and_is_enabled(self):
        firms = self._population("generation_v8_smoke_population.json")
        self.assertEqual(len(firms), 4)
        for firm in firms:
            genome = CompanyGenome.from_dict(firm)
            self.assertEqual(genome.to_dict(), firm, firm["company_id"])
            self.assertTrue(genome.org_enabled)
            self.assertEqual(len(genome.role_library), 8)

    def test_defaults_are_omitted_and_set_values_are_written(self):
        plain = CompanyGenome.from_dict(GENOME)
        self.assertNotIn("role_library", plain.to_dict())
        self.assertNotIn("ceo_policy", plain.to_dict())
        self.assertIsInstance(plain.ceo_policy, CEOPolicyGene)
        self.assertFalse(plain.org_enabled)

        # A disabled gene with a non-default knob is information and is kept.
        tuned = CompanyGenome.from_dict(dict(GENOME, ceo_policy={"headcount_lambda": 0.4}))
        self.assertIn("ceo_policy", tuned.to_dict())
        self.assertEqual(tuned.to_dict()["ceo_policy"]["headcount_lambda"], 0.4)
        self.assertFalse(tuned.org_enabled)

        enabled = CompanyGenome.from_dict(dict(
            GENOME, ceo_policy={"enabled": True}, role_library=[_role("r_a").to_dict()]))
        out = enabled.to_dict()
        self.assertTrue(out["ceo_policy"]["enabled"])
        self.assertEqual([r["role_id"] for r in out["role_library"]], ["r_a"])
        self.assertTrue(enabled.org_enabled)
        self.assertEqual(CompanyGenome.from_dict(out).to_dict(), out)

    def test_validation(self):
        with self.assertRaises(GenomeValidationError):
            CompanyGenome.from_dict(dict(GENOME, role_library=[_role("r_a").to_dict(), _role("r_a").to_dict()]))
        with self.assertRaises(GenomeValidationError):
            CompanyGenome.from_dict(dict(GENOME, role_library=[
                _role(f"r_{i}").to_dict() for i in range(MAX_ROLE_LIBRARY + 1)]))
        with self.assertRaises(GenomeValidationError):
            CompanyGenome.from_dict(dict(GENOME, role_library="not a list"))
        with self.assertRaises(GenomeValidationError):
            CompanyGenome.from_dict(dict(GENOME, role_library=[42]))
        with self.assertRaises(GenomeValidationError):
            CompanyGenome.from_dict(dict(GENOME, ceo_policy="on"))
        with self.assertRaises(GenomeValidationError):
            CompanyGenome.from_dict(dict(GENOME, ceo_policy={"enabled": True, "headcount_lambda": 7.0}))
        with self.assertRaises(GenomeValidationError):
            CompanyGenome.from_dict(dict(GENOME, ceo_policy={"min_initial_roles": 5, "max_initial_roles": 2}))
        # Exactly the cap is fine.
        ok = CompanyGenome.from_dict(dict(GENOME, role_library=[
            _role(f"r_{i}").to_dict() for i in range(MAX_ROLE_LIBRARY)]))
        self.assertEqual(len(ok.role_library), MAX_ROLE_LIBRARY)

    def test_org_module_re_exports_the_schema_types(self):
        # Track B/C import these from hae.epistemic.org; they must be the same objects.
        self.assertIs(OrgRoleAllele, RoleAllele)
        self.assertIs(OrgCEOPolicyGene, CEOPolicyGene)


# --------------------------------------------------------------------------- #
# Seeds
# --------------------------------------------------------------------------- #

class SeedLibraryTest(unittest.TestCase):

    def test_flavours_are_well_formed(self):
        self.assertEqual(SEED_FLAVOURS, ("legacy", "swebench"))
        for flavour in SEED_FLAVOURS:
            library = seed_role_library(flavour)
            ids = [r.role_id for r in library]
            self.assertEqual(len(ids), len(set(ids)), flavour)
            self.assertTrue(any(r.can("probe") for r in library), flavour)
            self.assertTrue(any(r.can("synthesis") for r in library), flavour)
            self.assertLessEqual(len(library), MAX_ROLE_LIBRARY)
            for r in library:
                self.assertTrue(r.origin.startswith(f"seed:{flavour}"), r.role_id)
                self.assertEqual(r.uses, 0)
                self.assertTrue(r.domain_tags, r.role_id)
                self.assertTrue(r.goal and r.backstory, r.role_id)
                # Serialises and reloads to the same thing.
                self.assertEqual(RoleAllele.from_dict(r.to_dict()).to_dict(), r.to_dict())
        self.assertEqual(len(seed_role_library("legacy")), 8)
        self.assertEqual(len(seed_role_library("swebench")), 10)
        # A fresh list each call: a caller mutating its library must not change the seeds.
        a, b = seed_role_library("legacy"), seed_role_library("legacy")
        self.assertIsNot(a[0], b[0])
        with self.assertRaises(ValueError):
            seed_role_library("bogus")

    def test_legacy_seeds_cover_the_benchmark_failure_vocabulary(self):
        tags = {t for r in seed_role_library("legacy") for t in r.domain_tags}
        for tag in ("harness", "verification_loop", "morphogenesis", "artifacts",
                    "assertionerror", "unboundlocalerror", "nameerror", "modulenotfounderror"):
            self.assertIn(tag, tags)

    def test_technical_keywords_mirror_the_runtime(self):
        self.assertEqual(role_seeds.TECHNICAL_DEPT_KEYWORDS, TECHNICAL_DEPT_KEYWORDS)

    def test_library_from_departments(self):
        genome = CompanyGenome.from_dict(GENOME)
        library = role_library_from_departments(genome)
        ids = [r.role_id for r in library]
        self.assertEqual(len(ids), len(set(ids)))
        by_origin = {r.origin: r for r in library}
        self.assertIn("department:dept_systems_eng", by_origin)
        self.assertIn("department:dept_qa_redteam", by_origin)
        self.assertNotIn("department:dept_marketing", by_origin)  # not technical
        eng = by_origin["department:dept_systems_eng"]
        self.assertEqual(eng.name, "Lead Implementation Engineer")
        self.assertEqual(eng.kind, "both")  # tools on -> may synthesise
        qa = by_origin["department:dept_qa_redteam"]
        self.assertEqual(qa.kind, "probe")  # no tools -> probe only
        self.assertTrue(any(r.can("probe") for r in library))
        self.assertTrue(any(r.can("synthesis") for r in library))
        # A custom predicate can widen the net.
        everyone = role_library_from_departments(genome, is_technical=lambda d: True)
        self.assertIn("department:dept_marketing", {r.origin for r in everyone})

    def test_library_from_a_real_run3_genome(self):
        with open(os.path.join(CONFIGS, "generation_16_r3_population.json"), "r", encoding="utf-8") as fh:
            firm = json.load(fh)["population"][0]
        genome = CompanyGenome.from_dict(firm)
        library = role_library_from_departments(genome)
        self.assertTrue(library)
        self.assertLessEqual(len(library), MAX_ROLE_LIBRARY)
        self.assertEqual(len({r.role_id for r in library}), len(library))
        # The genome must still accept its own derived library.
        CompanyGenome.from_dict(dict(firm, role_library=[r.to_dict() for r in library]))

    def test_ensure_probe_and_synthesis_tops_up(self):
        only_probe = [_role("r_p", kind="probe")]
        topped = ensure_probe_and_synthesis(only_probe, "legacy")
        self.assertTrue(any(r.can("synthesis") for r in topped))
        self.assertEqual(topped[0].role_id, "r_p")
        self.assertEqual(ensure_probe_and_synthesis([_role("r_b")], "legacy"), [_role("r_b")])


# --------------------------------------------------------------------------- #
# Runner: turn-0 organisation selection
# --------------------------------------------------------------------------- #

class RecordingLoop(EpistemicSearchLoop):
    """The real loop, remembering what the runner constructed it with."""

    seen = []

    def __init__(self, *args, **kwargs):
        RecordingLoop.seen.append(dict(kwargs))
        super().__init__(*args, **kwargs)


class RunnerOrganisationTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hae_v8_runner_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        RecordingLoop.seen = []

    def _runner(self, **genome_overrides):
        runner = HierarchicalCompanyRunner(CompanyGenome.from_dict(dict(GENOME, **genome_overrides)))
        runner.workspace = AgentWorkspace("v8_firm", base_dir=self.tmp)
        runner.verification_loop = VerificationLoop(runner.workspace)
        runner.workspace.write_file("mypkg/__init__.py", "")
        runner.workspace.write_file("mypkg/calc.py", BUGGY)
        runner._make_gatekeeper = lambda policy=None: EvidenceGatekeeper(
            runner.workspace, timeout_s=20, max_probe_lines=40,
            graded_modules=runner._required_modules, module_for_tag=SUITE_TAG_TO_MODULE,
            isolate=False, stage_reference=False)
        return runner

    def test_disabled_gene_binds_nothing(self):
        runner = self._runner()
        self.assertIsNone(runner._bind_dynamic_organization(OBJECTIVE, FAILURES, iteration=2))
        self.assertFalse(getattr(runner, "org_audits", None))

    def test_disabled_path_constructs_the_loop_exactly_as_before(self):
        runner = self._runner()
        with mock.patch.object(company_module, "EpistemicSearchLoop", RecordingLoop), \
                mock.patch("hae.runtime.company.call_llm", FakeSystem1()):
            out = runner.run_epistemic_search(OBJECTIVE, FAILURES, iteration=2, max_iterations=3)
        self.assertEqual(len(RecordingLoop.seen), 1)
        kwargs = RecordingLoop.seen[0]
        self.assertNotIn("org_state", kwargs)
        self.assertNotIn("recruit_specialist", kwargs)
        self.assertEqual(set(kwargs), {"propose_hypotheses", "synthesize_patch", "revert_patch", "may_continue",
                                       "agent_roles", "rng_seed", "logger", "prior_head", "prior_head_weight",
                                       "value_head"})
        for key in ("org", "org_audit", "org_history"):
            self.assertNotIn(key, out)
        self.assertIn("epistemic_search", out)
        self.assertEqual(out["epistemic_search"]["synthesized_paths"], ["mypkg/calc.py"])
        # Static bindings, as in V6.
        self.assertIn("Lead Implementation Engineer", kwargs["agent_roles"].values())

    def test_enabled_gene_without_a_library_staffs_from_the_legacy_seeds(self):
        runner = self._runner(ceo_policy={"enabled": True})
        org = runner._bind_dynamic_organization(OBJECTIVE, FAILURES, iteration=2)
        self.assertIsInstance(org, OrgState)
        self.assertTrue(2 <= len(org.active_roles) <= 4)
        self.assertTrue(any(r.can("probe") for r in org.active_roles))
        self.assertTrue(any(r.can("synthesis") for r in org.active_roles))
        self.assertEqual(len(org.library), 8)
        legacy_ids = {r.role_id for r in seed_role_library("legacy")}
        self.assertTrue(set(org.active_ids) <= legacy_ids)
        audit = runner.org_audits[-1]
        self.assertEqual(audit["library_source"], "seed:legacy")
        self.assertEqual(audit["library_size"], 8)
        self.assertEqual(audit["iteration"], 2)
        self.assertEqual(audit["team"], org.active_ids)
        self.assertGreater(audit["candidates"], 0)
        self.assertEqual(audit["task"]["source"], "failures")
        self.assertIn("assertionerror", audit["task"]["tags"])
        # Deterministic from company id + iteration.
        again = self._runner(ceo_policy={"enabled": True})._bind_dynamic_organization(
            OBJECTIVE, FAILURES, iteration=2)
        self.assertEqual(again.active_ids, org.active_ids)

    def test_enabled_gene_with_a_library_uses_it_and_carries_recruits(self):
        library = [_role("r_probe", kind="probe", domain_tags=["test_add", "assertionerror"]),
                   _role("r_synth", kind="synthesis", domain_tags=["assertionerror"]),
                   _role("r_other", kind="both", domain_tags=["marketing"])]
        runner = self._runner(ceo_policy={"enabled": True, "min_initial_roles": 2, "max_initial_roles": 2},
                              role_library=[r.to_dict() for r in library])
        org = runner._bind_dynamic_organization(OBJECTIVE, FAILURES, iteration=2)
        self.assertEqual(sorted(org.active_ids), ["r_probe", "r_synth"])
        self.assertEqual(runner.org_audits[-1]["library_source"], "genome")
        # A role hired in an earlier iteration is a candidate at the next turn 0.
        runner._recruited_roles = {"r_new": _role("r_new", kind="both", domain_tags=["calc"])}
        org2 = runner._bind_dynamic_organization(OBJECTIVE, FAILURES, iteration=3)
        self.assertEqual(len(org2.library), 4)
        self.assertEqual(runner.org_audits[-1]["library_source"], "genome+recruited:1")
        self.assertEqual(len(runner.org_audits), 2)

    def test_dynamic_bindings_replace_probe_and_synthesis_but_not_question(self):
        runner = self._runner(ceo_policy={"enabled": True})
        static = runner._bind_epistemic_agents(runner.epistemic_policy)
        team = [_role("r_probe", kind="probe"), _role("r_synth", kind="synthesis")]
        org = OrgState(team, runner.genome.ceo_policy, team)
        bound = runner._dynamic_org_bindings(org, static)
        self.assertEqual(set(bound), set(static))
        self.assertIs(bound["question"], static["question"])
        self.assertEqual(bound["hypothesis"].extra["role_id"], "r_probe")
        self.assertEqual(bound["experiment"].extra["role_id"], "r_probe")
        self.assertEqual(bound["synthesis"].extra["role_id"], "r_synth")
        self.assertIsInstance(bound["synthesis"], AgentGenome)
        self.assertEqual(bound["synthesis"].role, "R Synth")
        # A team that cannot synthesise keeps the static synthesis binding.
        probe_only = OrgState([_role("r_probe", kind="probe")], runner.genome.ceo_policy)
        self.assertIs(runner._dynamic_org_bindings(probe_only, static)["synthesis"], static["synthesis"])


if __name__ == "__main__":
    unittest.main()
