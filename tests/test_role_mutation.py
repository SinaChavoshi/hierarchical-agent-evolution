"""V8: LLM revision of underperforming role alleles between generations
(`hae.genome.role_mutation`, `mutate_role_text`, `distill_role_evidence`).

What is locked down: the packet grammar and the tolerant parser (fences and
prose survive, a missing goal does not, an unknown kind keeps the parent's);
the prompt carrying the falsified claims and the unmatched modules; the new
allele's id / origin / `extra` convention and reset statistics; every failure
of the LLM path becoming `(None, reason)`; the evidence distillation on
records with `role_id`s and its silence on V6 records (synthetic and a real
Gen-16 tree); the selection thresholds and the 0.25 fallback; and the
invariant that mode `tags` -- the default for every spec -- is byte-identical
to the pre-existing `mutate_role_library`, so nothing bred before this
operator existed breeds differently. The LLM is a fake throughout: nothing
here has been run against a live model.
"""

import glob
import json
import os
import random
import shutil
import tempfile
import unittest
from unittest import mock

from hae.genome.mutator import (
    REVISE_HEALTHY_PROBABILITY,
    REVISE_MAX_MEAN_DELTA_U,
    REVISE_MAX_SUPPORT_RATE,
    REVISE_MIN_USES,
    REVISION_NOTE_MARKER,
    ROLE_TEXT_MUTATION_MODES,
    RoleStatsSummary,
    mutate_role_library,
    mutate_role_text,
    select_roles_for_revision,
)
from hae.genome.role_mutation import (
    CLAIM_CHARS,
    MAX_BACKSTORY_CHARS,
    MAX_FALSIFIED_CLAIMS,
    MAX_GOAL_CHARS,
    MAX_NAME_CHARS,
    MAX_RATIONALE_CHARS,
    MAX_REVISION_TAGS,
    REVISION_TEMPERATURE,
    ROLE_REVISION_SCHEMA,
    RoleEvidence,
    RoleRevision,
    build_role_revision_prompt,
    parse_role_revision,
    revise_role,
)
from hae.genome.schema import ROLE_KINDS, CompanyGenome, RoleAllele
from hae.orchestration.breeder import Breeder, BreedingError, GenerationSpec, distill_role_evidence
from tests.test_epistemic_breeder import GENOME, PARENT_B, scorecard
from tests.test_v8_breeder import PROBE, SEEDS, SYNTH, _org, _stats, v8_record

ROOT = os.path.join(os.path.dirname(__file__), "..")
V6_TREE_GLOB = os.path.join(ROOT, "results", "hae_gen16_v6_cohort_r2", "outputs", "*", "generation_16",
                            "*_epistemic_tree.json")

OLD_GOAL = "Find the Python scoping mistake behind every failing harness test."
OLD_BACKSTORY = "Spent a decade reading tracebacks for UnboundLocalError and except-clause name deletion."
NEW_GOAL = "Reproduce the failing harness fixture end to end before hypothesising about any scoping mechanism."
NEW_BACKSTORY = ("Ten years inside pytest internals; has debugged fixture teardown ordering and subprocess "
                 "harness plumbing in CI farms, and treats a traceback as a symptom, not a mechanism.")
FALSIFIED = "The `tests` gate deletes `e` after the except block so the summary reads an unbound name."
UNMATCHED = "hae/evaluation/harness.py"


def parent(**over):
    data = dict(role_id="r_scoping_sleuth", name="Scoping Sleuth", goal=OLD_GOAL, backstory=OLD_BACKSTORY,
                domain_tags=["scoping", "unboundlocalerror"], kind="probe", uses=12, mean_delta_u=0.01,
                support_rate=0.05, extra={"source": "library"})
    data.update(over)
    return RoleAllele(**data)


def packet(**over):
    data = {"packet": "ROLE_REVISION", "name": "Harness Fixture Forensic", "goal": NEW_GOAL,
            "backstory": NEW_BACKSTORY, "domain_tags": ["harness", "fixture", "pytest"], "kind": "probe",
            "rationale": "Stop proposing scoping mechanisms; cover the harness fixtures nobody owned."}
    data.update(over)
    return json.dumps(data)


class FakeLLM:
    """Records every call; replies with `reply` or raises `error`."""

    def __init__(self, reply=None, error=None):
        self.calls = []
        self.reply = packet() if reply is None else reply
        self.error = error

    def __call__(self, prompt, **kwargs):
        self.calls.append((prompt, kwargs))
        if self.error is not None:
            raise self.error
        return self.reply


def evidence(role_id="r_scoping_sleuth"):
    ev = RoleEvidence(role_id=role_id)
    ev.add("falsified_claims", FALSIFIED)
    ev.add("falsified_claims", "The harness swallows the AssertionError raised inside the smoke gate.")
    ev.add("refused_probe_reasons", "probe imports the network")
    ev.add("supported_claims", "The telemetry gate reads the wrong key.")
    ev.add("questions_seen", "Why does test_failing_assertion_is_detected fail?")
    ev.add("modules_seen", UNMATCHED)
    ev.add("unmatched_modules", "hae/genome/morphogenesis.py")
    ev.syntheses_reverted = 2
    return ev


# --------------------------------------------------------------------------- #
# Schema and parser
# --------------------------------------------------------------------------- #

class SchemaAndParseTest(unittest.TestCase):

    def test_schema_shape(self):
        js = ROLE_REVISION_SCHEMA["json_schema"]
        schema = js["schema"]
        props = schema["properties"]
        self.assertEqual(ROLE_REVISION_SCHEMA["type"], "json_schema")
        self.assertTrue(js["strict"])
        self.assertEqual(props["packet"]["enum"], ["ROLE_REVISION"])
        self.assertEqual(props["kind"]["enum"], list(ROLE_KINDS))
        self.assertEqual((props["name"]["maxLength"], props["goal"]["maxLength"], props["backstory"]["maxLength"],
                          props["rationale"]["maxLength"]), (60, 400, 600, 300))
        self.assertEqual((props["domain_tags"]["minItems"], props["domain_tags"]["maxItems"]), (3, 8))
        self.assertEqual(set(schema["required"]), set(props))
        self.assertFalse(schema["additionalProperties"])

    def test_fenced_and_prose_wrapped_packets_parse(self):
        fenced = "```json\n" + packet() + "\n```"
        rev = parse_role_revision(fenced)
        self.assertIsInstance(rev, RoleRevision)
        self.assertEqual((rev.name, rev.goal, rev.backstory, rev.kind), ("Harness Fixture Forensic", NEW_GOAL,
                                                                          NEW_BACKSTORY, "probe"))
        self.assertEqual(rev.domain_tags, ["harness", "fixture", "pytest"])
        self.assertTrue(rev.rationale.startswith("Stop proposing"))
        wrapped = "Here is the revision you asked for:\n" + packet() + "\nLet me know."
        self.assertEqual(parse_role_revision(wrapped), rev)

    def test_missing_or_empty_goal_is_none(self):
        data = json.loads(packet())
        del data["goal"]
        self.assertIsNone(parse_role_revision(json.dumps(data)))
        self.assertIsNone(parse_role_revision(packet(goal="")))
        self.assertIsNone(parse_role_revision(packet(goal="   \n ")))
        self.assertIsNone(parse_role_revision(""))
        self.assertIsNone(parse_role_revision("I would keep this role exactly as it is."))

    def test_unknown_kind_is_coerced_to_keep_the_parent(self):
        self.assertEqual(parse_role_revision(packet(kind="wizard")).kind, "")
        data = json.loads(packet())
        del data["kind"]
        self.assertEqual(parse_role_revision(json.dumps(data)).kind, "")
        self.assertEqual(parse_role_revision(packet(kind=" Synthesis ")).kind, "synthesis")
        role = parent(kind="synthesis")
        new, _ = revise_role(role, None, None, 17, random.Random(1), llm=FakeLLM(packet(kind="wizard")))
        self.assertEqual(new.kind, "synthesis")

    def test_string_tags_and_over_long_fields_are_normalised_not_rejected(self):
        rev = parse_role_revision(packet(name="n" * 100, goal="g" * 500, backstory="b" * 700,
                                         rationale="r" * 400, domain_tags="Harness, Fixture pytest",
                                         kind="both"))
        self.assertEqual((len(rev.name), len(rev.goal), len(rev.backstory), len(rev.rationale)),
                         (MAX_NAME_CHARS, MAX_GOAL_CHARS, MAX_BACKSTORY_CHARS, MAX_RATIONALE_CHARS))
        self.assertEqual(rev.domain_tags, ["harness", "fixture", "pytest"])
        many = parse_role_revision(packet(domain_tags=[f"t{i}" for i in range(12)]))
        self.assertEqual(len(many.domain_tags), MAX_REVISION_TAGS)


# --------------------------------------------------------------------------- #
# Prompt
# --------------------------------------------------------------------------- #

class PromptTest(unittest.TestCase):

    def test_prompt_carries_the_role_its_numbers_and_the_evidence(self):
        role = parent()
        stats = RoleStatsSummary(role_id=role.role_id, uses=4, cumulative_delta_u=-0.1, supported=0, falsified=4,
                                 untestable=1, syntheses_written=0, firms=2)
        text = build_role_revision_prompt(role, stats, evidence(), 17)
        for needle in (role.role_id, role.name, OLD_GOAL, OLD_BACKSTORY, "scoping, unboundlocalerror",
                       "12 uses", "+0.010", "support rate 0.05", "4 moves", "falsified 4", "across 2 firm(s)",
                       FALSIFIED, "probe imports the network", "The telemetry gate reads the wrong key.",
                       "Why does test_failing_assertion_is_detected fail?", UNMATCHED,
                       "hae/genome/morphogenesis.py", "REVERTED: 2", "would NOT have proposed",
                       "unmatched modules", "ROLE_REVISION", "generation 17"):
            self.assertIn(needle, text, needle)
        self.assertIn("end-of-move credit", text)

    def test_prompt_without_evidence_or_stats_says_so(self):
        text = build_role_revision_prompt(parent(), None, None, 3)
        self.assertIn("no per-role statistics recorded", text)
        self.assertEqual(text.count("(none recorded)"), 6)
        self.assertEqual(build_role_revision_prompt(parent(), None, RoleEvidence("r_scoping_sleuth"), 3), text)


# --------------------------------------------------------------------------- #
# revise_role
# --------------------------------------------------------------------------- #

class ReviseRoleTest(unittest.TestCase):

    def test_new_allele_has_the_id_origin_extra_and_reset_statistics(self):
        role = parent()
        before = role.to_dict()
        llm = FakeLLM()
        new, note = revise_role(role, None, evidence(), 17, random.Random(3), llm=llm)
        self.assertIsNotNone(new, note)
        self.assertEqual(new.role_id, "r_scoping_sleuth__g17")
        self.assertEqual(new.origin, "mutated:r_scoping_sleuth:g17")
        self.assertEqual(new.created_generation, 17)
        self.assertEqual((new.uses, new.mean_delta_u, new.support_rate, new.tasks_resolved), (0, 0.0, 0.0, 0))
        self.assertEqual(new.extra, {"source": "library", "parent_role_id": "r_scoping_sleuth",
                                     "revision_rationale": json.loads(packet())["rationale"],
                                     "revision_source": "llm"})
        self.assertEqual((new.name, new.goal, new.backstory, new.kind), ("Harness Fixture Forensic", NEW_GOAL,
                                                                          NEW_BACKSTORY, "probe"))
        self.assertEqual(new.domain_tags, ["harness", "fixture", "pytest"])
        self.assertEqual((new.model_tier, new.temperature, new.tools_enabled, new.cost_per_move),
                         (role.model_tier, role.temperature, role.tools_enabled, role.cost_per_move))
        self.assertTrue(note.startswith("revised: Stop proposing"))
        self.assertEqual(role.to_dict(), before)  # pure
        # The call: guided JSON, the documented temperature, no seed for a callable that declares none.
        prompt, kwargs = llm.calls[0]
        self.assertIn(role.role_id, prompt)
        self.assertIn(FALSIFIED, prompt)
        self.assertIs(kwargs["response_format"], ROLE_REVISION_SCHEMA)
        self.assertEqual((kwargs["temperature"], kwargs["max_tokens"], kwargs["model_tier"]),
                         (REVISION_TEMPERATURE, 700, "executive"))
        self.assertNotIn("seed", kwargs)
        self.assertNotIn("model_name", kwargs)
        llm2 = FakeLLM()
        revise_role(role, None, None, 17, random.Random(3), llm=llm2, model="kimi-k3", max_tokens=900)
        self.assertEqual((llm2.calls[0][1]["model_name"], llm2.calls[0][1]["max_tokens"]), ("kimi-k3", 900))

    def test_seed_is_drawn_always_and_passed_only_to_a_callable_that_declares_it(self):
        seen = {}

        def with_seed(prompt, seed=None, **kwargs):
            seen["seed"] = seed
            return packet()

        rng_a, rng_b = random.Random(5), random.Random(5)
        new_a, _ = revise_role(parent(), None, None, 17, rng_a, llm=with_seed)
        new_b, _ = revise_role(parent(), None, None, 17, rng_b, llm=FakeLLM())
        self.assertEqual(seen["seed"], random.Random(5).randrange(2 ** 31))
        self.assertEqual(rng_a.random(), rng_b.random())  # same draws regardless of the callable
        self.assertEqual(new_a.to_dict(), new_b.to_dict())

    def test_empty_packet_fields_keep_the_parent_values(self):
        role = parent()
        new, _ = revise_role(role, None, None, 17, random.Random(1),
                             llm=FakeLLM(packet(name="", backstory="", domain_tags=[], kind="")))
        self.assertEqual((new.name, new.backstory, new.domain_tags, new.kind),
                         (role.name, role.backstory, role.domain_tags, role.kind))
        self.assertEqual(new.goal, NEW_GOAL)

    def test_identical_unparseable_and_failing_replies_decline_without_raising(self):
        role = parent()
        same = FakeLLM(packet(goal=OLD_GOAL, backstory=OLD_BACKSTORY, name="Renamed", domain_tags=["x", "y", "z"]))
        new, reason = revise_role(role, None, None, 17, random.Random(1), llm=same)
        self.assertIsNone(new)
        self.assertIn("unchanged", reason)
        new, reason = revise_role(role, None, None, 17, random.Random(1), llm=FakeLLM("no packet here"))
        self.assertIsNone(new)
        self.assertIn("no usable ROLE_REVISION packet", reason)
        new, reason = revise_role(role, None, None, 17, random.Random(1), llm=FakeLLM(error=RuntimeError("502")))
        self.assertIsNone(new)
        self.assertEqual(reason, "revision failed: RuntimeError: 502")
        new, reason = revise_role(role, None, None, 17, random.Random(1), llm=lambda prompt, **kw: None)
        self.assertIsNone(new)  # a non-string reply is handled, not raised
        self.assertIn("no usable ROLE_REVISION packet", reason)
        new, reason = revise_role(role, None, None, 17, None, llm=FakeLLM())
        self.assertIsNotNone(new)  # rng is optional

    def test_id_collision_appends_a_hash_of_the_new_goal(self):
        new, _ = revise_role(parent(), None, None, 17, random.Random(1), llm=FakeLLM(),
                             existing_ids={"r_scoping_sleuth", "r_scoping_sleuth__g17"})
        self.assertTrue(new.role_id.startswith("r_scoping_sleuth__g17_"))
        suffix = new.role_id.rsplit("_", 1)[1]
        self.assertEqual(len(suffix), 6)
        int(suffix, 16)
        again, _ = revise_role(parent(), None, None, 17, random.Random(1), llm=FakeLLM(),
                               existing_ids={"r_scoping_sleuth__g17"})
        self.assertEqual(again.role_id, new.role_id)  # the hash is of the goal, so it is stable

    def test_revised_allele_round_trips_through_a_company_genome(self):
        new, _ = revise_role(parent(), None, None, 17, random.Random(1), llm=FakeLLM())
        genome = CompanyGenome.from_dict(dict(GENOME, company_id="rt", role_library=[parent().to_dict(), new.to_dict()],
                                              ceo_policy={"enabled": True}))
        back = CompanyGenome.from_dict(json.loads(json.dumps(genome.to_dict())))
        self.assertEqual(back.to_dict(), genome.to_dict())
        revised = next(r for r in back.role_library if r.role_id == "r_scoping_sleuth__g17")
        self.assertEqual(revised.extra["parent_role_id"], "r_scoping_sleuth")
        self.assertEqual(revised.extra["revision_source"], "llm")
        self.assertEqual(revised.to_dict(), new.to_dict())


# --------------------------------------------------------------------------- #
# Evidence distillation
# --------------------------------------------------------------------------- #

def ledger_with_roles():
    return {
        "questions": [
            {"question_id": "q1", "text": "Why does test_failing_assertion_is_detected fail?",
             "module": "hae/evaluation/harness.py"},
            {"question_id": "q2", "text": "Why does morph_genome_topology raise IndexError?", "module": "",
             "location": "hae/genome/morphogenesis.py:88 in morph_genome_topology"},
        ],
        "hypotheses": [
            {"hypothesis_id": "h1", "question_id": "q1", "role_id": "r_a", "status": "FALSIFIED", "claim": FALSIFIED},
            {"hypothesis_id": "h2", "question_id": "q1", "role_id": "r_a", "status": "FALSIFIED",
             "claim": "  " + FALSIFIED + "  "},   # duplicate after whitespace normalisation
            {"hypothesis_id": "h3", "question_id": "q1", "role_id": "r_a", "status": "SUPPORTED",
             "claim": "The telemetry gate reads the wrong key."},
            {"hypothesis_id": "h4", "question_id": "q1", "role_id": "r_a", "status": "UNTESTABLE",
             "claim": "c4", "last_rejection": "probe imports the network"},
            {"hypothesis_id": "h5", "question_id": "q1", "role_id": "r_a", "status": "UNTESTABLE",
             "claim": "c5", "last_rejection": "", "evidence_ids": ["e9"]},
            {"hypothesis_id": "h6", "question_id": "q1", "status": "FALSIFIED", "claim": "no role id -> ignored"},
            {"hypothesis_id": "h7", "question_id": "q2", "role_id": "r_b", "status": "FALSIFIED",
             "claim": "The department list is empty when the mutant is cloned."},
            {"hypothesis_id": "h8", "question_id": "q1", "role_id": "r_a", "status": "UNVERIFIED", "claim": "open"},
        ],
        "evidence_log": [
            {"evidence_id": "e9", "kind": "probe_rejected", "hypothesis_id": "h5", "detail": "probe exceeds 40 lines"},
        ],
        "move_log": [
            {"move_index": 1, "move_type": "propose_hypothesis", "question_id": "q2", "role_id": "r_a"},
            {"move_index": 2, "move_type": "synthesize", "question_id": "q2", "role_id": "r_b",
             "note": "hae/genome/morphogenesis.py [plan]: import failed; reverted to pre-synthesis module"},
            {"move_index": 3, "move_type": "synthesize", "question_id": "q2", "role_id": "r_b",
             "note": "hae/genome/morphogenesis.py [plan]: module verified"},
            {"move_index": 4, "move_type": "run_experiment", "question_id": "q1", "role_id": ""},
        ],
    }


class DistillEvidenceTest(unittest.TestCase):

    def test_synthetic_record_with_role_ids(self):
        card = scorecard("v8", policy=PARENT_B)
        card["run_output"] = {
            "epistemic_ledger": ledger_with_roles(),
            "org_history": [{"iteration": 2, "org": {"active_roles": [{"role_id": "r_a", "name": "A"},
                                                                       {"role_id": "r_b", "name": "B"}],
                                                      "stats": {}, "unmatched_modules": ["hae/x/y.py", " "]}}],
        }
        out = distill_role_evidence([card, scorecard("v6", policy=PARENT_B), "not a record"])
        self.assertEqual(set(out), {"r_a", "r_b"})
        a, b = out["r_a"], out["r_b"]
        self.assertEqual(a.falsified_claims, [FALSIFIED])
        self.assertEqual(a.supported_claims, ["The telemetry gate reads the wrong key."])
        self.assertEqual(a.refused_probe_reasons, ["probe imports the network", "probe exceeds 40 lines"])
        self.assertEqual(a.questions_seen, ["Why does test_failing_assertion_is_detected fail?",
                                            "Why does morph_genome_topology raise IndexError?"])
        self.assertEqual(a.modules_seen, ["hae/evaluation/harness.py", "hae/genome/morphogenesis.py"])
        self.assertEqual(a.unmatched_modules, ["hae/x/y.py"])
        self.assertEqual(a.syntheses_reverted, 0)
        self.assertEqual(b.falsified_claims, ["The department list is empty when the mutant is cloned."])
        self.assertEqual(b.syntheses_reverted, 1)
        self.assertEqual(b.unmatched_modules, ["hae/x/y.py"])
        self.assertFalse(a.is_empty())
        self.assertEqual(set(a.to_dict()), {"role_id", "falsified_claims", "refused_probe_reasons", "questions_seen",
                                            "modules_seen", "unmatched_modules", "syntheses_reverted",
                                            "supported_claims"})

    def test_caps_and_clipping(self):
        hyps = [{"hypothesis_id": f"h{i}", "question_id": "q", "role_id": "r", "status": "FALSIFIED",
                 "claim": f"claim {i} " + "x" * 300} for i in range(12)]
        out = distill_role_evidence([{"epistemic_ledger": {"hypotheses": hyps}}])
        self.assertEqual(len(out["r"].falsified_claims), MAX_FALSIFIED_CLAIMS)
        self.assertTrue(all(len(c) == CLAIM_CHARS for c in out["r"].falsified_claims))
        ev = RoleEvidence("r")
        self.assertTrue(ev.add("supported_claims", "a"))
        self.assertFalse(ev.add("supported_claims", "a"))   # duplicate
        self.assertFalse(ev.add("supported_claims", "  "))  # empty
        self.assertTrue(ev.add("supported_claims", "b"))
        self.assertTrue(ev.add("supported_claims", "c"))
        self.assertFalse(ev.add("supported_claims", "d"))   # cap 3

    def test_tree_sidecar_shape_and_trajectories_are_read(self):
        ledger = ledger_with_roles()
        moves = ledger.pop("move_log")
        tree = {"company_id": "x", "ledger": ledger, "searches": [{"trajectory": moves}]}
        out = distill_role_evidence([tree])
        self.assertEqual(out["r_b"].syntheses_reverted, 1)
        self.assertIn("Why does morph_genome_topology raise IndexError?", out["r_a"].questions_seen)
        bare = {"epistemic_ledger": ledger, "epistemic_search": {"trajectory": moves}}
        self.assertEqual(distill_role_evidence([bare])["r_b"].syntheses_reverted, 1)

    def test_v6_records_yield_nothing(self):
        self.assertEqual(distill_role_evidence([scorecard("v6", policy=PARENT_B)]), {})
        self.assertEqual(distill_role_evidence([]), {})
        trees = sorted(glob.glob(V6_TREE_GLOB))
        if not trees:
            self.skipTest("Gen-16 V6 cohort trees are not checked out")
        with open(trees[0], "r", encoding="utf-8") as fh:
            tree = json.load(fh)
        self.assertTrue(tree["ledger"]["hypotheses"])  # a real, non-empty trail ...
        self.assertTrue(any(h.get("status") == "FALSIFIED" for h in tree["ledger"]["hypotheses"]))
        self.assertEqual(distill_role_evidence([tree]), {})  # ... with no role_id anywhere
        card = scorecard("real", policy=PARENT_B)
        card["run_output"] = {"epistemic_ledger": tree["ledger"], "epistemic_searches": tree["searches"]}
        self.assertEqual(distill_role_evidence([card]), {})


# --------------------------------------------------------------------------- #
# Selection
# --------------------------------------------------------------------------- #

def lib_role(role_id, uses=0, mean=0.0, support=0.0, resolved=0, kind="both"):
    return RoleAllele(role_id=role_id, name=role_id, goal="g", backstory="b", domain_tags=["harness"], kind=kind,
                      uses=uses, mean_delta_u=mean, support_rate=support, tasks_resolved=resolved)


class SelectionTest(unittest.TestCase):

    def setUp(self):
        self.good = lib_role("r_good", uses=10, mean=0.2, support=0.5)
        self.weak1 = lib_role("r_weak1", uses=5, mean=0.04, support=0.3)
        self.weak2 = lib_role("r_weak2", uses=3, mean=0.0, support=0.0)
        self.weak_resolved = lib_role("r_weak_resolved", uses=6, mean=0.0, support=0.0, resolved=1)
        self.low_support = lib_role("r_low_support", uses=4, mean=0.1, support=0.05)
        self.fresh = lib_role("r_fresh", uses=2, mean=0.0, support=0.0)
        self.library = [self.good, self.weak1, self.weak2, self.weak_resolved, self.low_support, self.fresh]

    def test_thresholds_are_the_documented_ones(self):
        self.assertEqual((REVISE_MIN_USES, REVISE_MAX_MEAN_DELTA_U, REVISE_MAX_SUPPORT_RATE,
                          REVISE_HEALTHY_PROBABILITY), (3, 0.05, 0.10, 0.25))

    def test_candidates_worst_first_and_resolvers_never(self):
        rng = random.Random(0)
        state = rng.getstate()
        picked = select_roles_for_revision(self.library, {}, rng, max_roles=3)
        self.assertEqual([r.role_id for r in picked], ["r_weak2", "r_weak1", "r_low_support"])
        self.assertEqual(rng.getstate(), state)  # no draw when there are candidates
        self.assertEqual([r.role_id for r in select_roles_for_revision(self.library, {}, rng)], ["r_weak2"])
        # A task resolved this generation (stats) protects a role exactly like a lifetime resolution does.
        stats = {"r_weak2": RoleStatsSummary(role_id="r_weak2", uses=3, tasks_resolved=1)}
        self.assertEqual([r.role_id for r in select_roles_for_revision(self.library, stats, rng)], ["r_weak1"])
        self.assertEqual(select_roles_for_revision(self.library, {}, rng, max_roles=0), [])
        self.assertEqual(select_roles_for_revision([], {}, rng), [])
        # Ties on the numbers break by id.
        twins = [lib_role("r_z", uses=4, mean=0.0), lib_role("r_a", uses=4, mean=0.0)]
        self.assertEqual(select_roles_for_revision(twins, {}, rng)[0].role_id, "r_a")

    def test_healthy_library_fallback_fires_about_a_quarter_of_the_time(self):
        healthy = [lib_role("r_h1", uses=10, mean=0.2, support=0.5), lib_role("r_h2", uses=8, mean=0.15, support=0.4),
                   lib_role("r_h3", uses=1, mean=0.0, support=0.0), self.weak_resolved]
        picks = [select_roles_for_revision(healthy, {}, random.Random(seed)) for seed in range(400)]
        fired = [p[0].role_id for p in picks if p]
        rate = len(fired) / 400.0
        self.assertTrue(0.17 < rate < 0.33, rate)
        self.assertNotIn("r_weak_resolved", fired)
        self.assertEqual(set(fired), {"r_h1", "r_h2", "r_h3"})
        self.assertEqual(select_roles_for_revision(healthy, {}, random.Random(11)),
                         select_roles_for_revision(healthy, {}, random.Random(11)))
        self.assertEqual(select_roles_for_revision([self.weak_resolved], {}, random.Random(0)), [])


# --------------------------------------------------------------------------- #
# mutate_role_text
# --------------------------------------------------------------------------- #

class MutateRoleTextTest(unittest.TestCase):

    def setUp(self):
        self.weak = parent(role_id="r_weak", name="Weak", uses=4, mean_delta_u=0.0, support_rate=0.0)
        self.library = [lib_role("r_good", uses=10, mean=0.2, support=0.5, kind="synthesis"), self.weak,
                        lib_role("r_fresh", kind="probe")]
        self.pool = ["widget", "fixture", "morphogenesis"]

    @staticmethod
    def view(roles):
        return [r.to_dict() for r in roles]

    def test_tags_mode_is_mutate_role_library(self):
        for seed in range(10):
            expected = mutate_role_library(self.library, random.Random(seed), self.pool, 17)
            got = mutate_role_text(self.library, {}, {}, random.Random(seed), 17, mode="tags", tag_pool=self.pool)
            self.assertEqual((self.view(got[0]), got[1]), (self.view(expected[0]), expected[1]), seed)
        self.assertEqual(mutate_role_text([], {}, {}, random.Random(1), 17, mode="tags"), ([], []))
        self.assertEqual(ROLE_TEXT_MUTATION_MODES, ("off", "tags", "llm"))

    def test_off_mode_copies_without_notes(self):
        out, notes = mutate_role_text(self.library, {}, {}, random.Random(1), 17, mode="off", tag_pool=self.pool)
        self.assertEqual(notes, [])
        self.assertEqual(self.view(out), self.view(self.library))
        self.assertTrue(all(a is not b for a, b in zip(out, self.library)))
        with self.assertRaises(ValueError):
            mutate_role_text(self.library, {}, {}, random.Random(1), 17, mode="bogus")

    def test_llm_mode_replaces_the_parent_and_writes_the_note(self):
        llm = FakeLLM()
        ev = {"r_weak": evidence("r_weak")}
        stats = {"r_weak": RoleStatsSummary(role_id="r_weak", uses=4, falsified=4)}
        out, notes = mutate_role_text(self.library, stats, ev, random.Random(2), 17, mode="llm", llm=llm,
                                      tag_pool=self.pool)
        ids = [r.role_id for r in out]
        self.assertNotIn("r_weak", ids)
        self.assertEqual(ids[1], "r_weak__g17")  # replaced in place
        revised = out[1]
        self.assertEqual(revised.extra["parent_role_id"], "r_weak")
        self.assertEqual((revised.goal, revised.uses), (NEW_GOAL, 0))
        self.assertEqual(len(llm.calls), 1)
        self.assertIn(FALSIFIED, llm.calls[0][0])
        self.assertIn("falsified 4", llm.calls[0][0])
        revision_notes = [n for n in notes if REVISION_NOTE_MARKER in n]
        self.assertEqual(len(revision_notes), 1)
        self.assertEqual(revision_notes[0], f"role r_weak revised by LLM -> r_weak__g17: goal {OLD_GOAL[:60]!r} -> "
                                            f"{NEW_GOAL[:60]!r} ({json.loads(packet())['rationale'][:80]})")
        self.assertTrue(any("tag" in n and "pool" in n for n in notes))  # the tag mutation still happened
        self.assertEqual(self.library[1].role_id, "r_weak")  # pure
        # The tag half of `llm` mode is the `tags` output for the same seed.
        tags_only, _ = mutate_role_text(self.library, {}, {}, random.Random(2), 17, mode="tags", tag_pool=self.pool)
        self.assertEqual([r.domain_tags for i, r in enumerate(out) if i != 1],
                         [r.domain_tags for i, r in enumerate(tags_only) if i != 1])

    def test_llm_failure_falls_back_to_the_tag_mutation_with_a_note(self):
        failing = FakeLLM(error=RuntimeError("vLLM down"))
        out, notes = mutate_role_text(self.library, {}, {}, random.Random(2), 17, mode="llm", llm=failing,
                                      tag_pool=self.pool)
        tags_only = mutate_role_text(self.library, {}, {}, random.Random(2), 17, mode="tags", tag_pool=self.pool)
        self.assertEqual(self.view(out), self.view(tags_only[0]))
        self.assertEqual(notes[:-1], tags_only[1])
        self.assertIn("role r_weak kept, LLM revision declined (revision failed: RuntimeError: vLLM down)", notes[-1])
        self.assertFalse(any(REVISION_NOTE_MARKER in n for n in notes))
        # A healthy library under a seed where the 0.25 fallback does not fire: a note says nothing was selected.
        healthy = [lib_role("r_h1", uses=10, mean=0.2, support=0.5), lib_role("r_h2", uses=8, mean=0.15, support=0.4)]
        quiet = next(s for s in range(50) if not select_roles_for_revision(healthy, {}, _after_tags(healthy, s)))
        _, notes = mutate_role_text(healthy, {}, {}, random.Random(quiet), 17, mode="llm", llm=FakeLLM(),
                                    tag_pool=self.pool)
        self.assertIn("role text revision skipped: no role selected", notes)


def _after_tags(library, seed):
    """The rng state `mutate_role_text(..., mode='llm')` reaches selection with, for `seed`."""
    rng = random.Random(seed)
    mutate_role_library(library, rng, ["widget", "fixture", "morphogenesis"], 17)
    return rng


# --------------------------------------------------------------------------- #
# Breeder integration
# --------------------------------------------------------------------------- #

class BreederIntegrationTest(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="hae_role_mut_")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.cards = os.path.join(self.root, "cards")
        os.makedirs(self.cards)
        # Firm a (best): the probe seed was tried four times for nothing; the
        # ledger records which claims it proposed and which module nobody covered.
        ledger = {"questions": [{"question_id": "q1", "text": "Why does the harness swallow the assertion?",
                                 "module": UNMATCHED}],
                  "hypotheses": [{"hypothesis_id": "h1", "question_id": "q1", "role_id": PROBE.role_id,
                                  "status": "FALSIFIED", "claim": FALSIFIED}],
                  "move_log": []}
        card_a = v8_record("a", resolved=False, judged=90.0, library=SEEDS, orgs=[
            _org([PROBE, SYNTH], {PROBE.role_id: _stats(4, 0.0, falsified=4), SYNTH.role_id: _stats(1, 0.1, written=1)},
                 unmatched=[UNMATCHED])])
        card_a["run_output"]["epistemic_ledger"] = ledger
        card_b = v8_record("b", resolved=False, judged=80.0, library=SEEDS, orgs=[
            _org([PROBE, SYNTH], {PROBE.role_id: _stats(0), SYNTH.role_id: _stats(1, 0.1)})])
        for card in (card_a, card_b):
            with open(os.path.join(self.cards, f"{card['company_id']}.json"), "w", encoding="utf-8") as fh:
                json.dump(card, fh)

    def spec(self, **overrides):
        base = dict(generation=17, name="V8d", parent_scorecards="cards", survivors=2,
                    elite=1, crossover=1, pareto=1, mutant=1, objective="repair")
        base.update(overrides)
        return GenerationSpec(**base)

    @staticmethod
    def by_kind(population):
        return {g.company_id.split("_")[2]: g for g in population}

    @staticmethod
    def revision_lines(genome):
        return [h for h in genome.mutation_history if REVISION_NOTE_MARKER in h]

    def test_llm_mode_breeds_a_revised_mutant_from_the_distilled_evidence(self):
        llm = FakeLLM()
        with mock.patch("hae.genome.role_mutation.call_llm", llm):
            breeder = Breeder(self.spec(role_text_mutation="llm"), repo_root=self.root)
            population = breeder.breed()
        self.assertEqual(breeder.role_evidence[PROBE.role_id].falsified_claims, [FALSIFIED])
        self.assertEqual(breeder.role_evidence[PROBE.role_id].unmatched_modules, [UNMATCHED])
        kinds = self.by_kind(population)
        mutant = kinds["mutant"]
        ids = [r.role_id for r in mutant.role_library]
        new_id = f"{PROBE.role_id}__g17"
        self.assertIn(new_id, ids)
        self.assertNotIn(PROBE.role_id, ids)
        revised = next(r for r in mutant.role_library if r.role_id == new_id)
        self.assertEqual(revised.extra["parent_role_id"], PROBE.role_id)
        self.assertEqual(revised.origin, f"mutated:{PROBE.role_id}:g17")
        self.assertEqual((revised.goal, revised.uses, revised.created_generation), (NEW_GOAL, 0, 17))
        lines = self.revision_lines(mutant)
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith(f"Generation 17: role {PROBE.role_id} revised by LLM -> {new_id}: goal "))
        self.assertIn(f"-> {NEW_GOAL[:60]!r}", lines[0])
        # Exactly one call, for the mutant, carrying the distilled evidence.
        self.assertEqual(len(llm.calls), 1)
        prompt, kwargs = llm.calls[0]
        self.assertIn(FALSIFIED, prompt)
        self.assertIn(UNMATCHED, prompt)
        self.assertIn(PROBE.role_id, prompt)
        self.assertIs(kwargs["response_format"], ROLE_REVISION_SCHEMA)
        for kind in ("elite", "crossover", "pareto"):
            self.assertFalse(self.revision_lines(kinds[kind]), kind)
            self.assertFalse(any("__g" in r.role_id for r in kinds[kind].role_library), kind)
        for genome in population:
            CompanyGenome.from_dict(json.loads(json.dumps(genome.to_dict())))

    def test_default_spec_breeds_without_revision_and_without_an_llm_call(self):
        llm = FakeLLM()

        def v8_view(population):
            return [(g.company_id, [r.to_dict() for r in g.role_library],
                     [h for h in g.mutation_history if "role library" in h or REVISION_NOTE_MARKER in h])
                    for g in population]

        with mock.patch("hae.genome.role_mutation.call_llm", llm):
            first = Breeder(self.spec(), repo_root=self.root).breed()
            second = Breeder(self.spec(), repo_root=self.root).breed()
            explicit = Breeder(self.spec(role_text_mutation="tags"), repo_root=self.root).breed()
        self.assertEqual(llm.calls, [])
        self.assertEqual(self.spec().role_text_mutation, "tags")
        for genome in first:
            self.assertFalse(self.revision_lines(genome), genome.company_id)
            self.assertFalse(any("__g" in r.role_id for r in genome.role_library), genome.company_id)
            self.assertFalse(any("revision" in h for h in genome.mutation_history), genome.company_id)
        self.assertEqual(v8_view(first), v8_view(second))
        self.assertEqual(v8_view(first), v8_view(explicit))
        self.assertTrue(any("tag" in h for h in self.by_kind(first)["mutant"].mutation_history))
        with mock.patch("hae.genome.role_mutation.call_llm", llm):
            off = Breeder(self.spec(role_text_mutation="off"), repo_root=self.root).breed()
        self.assertFalse(any("tag" in h and "pool" in h for h in self.by_kind(off)["mutant"].mutation_history))

    def test_llm_failure_leaves_a_tag_only_mutant_and_a_note(self):
        with mock.patch("hae.genome.role_mutation.call_llm", FakeLLM(error=RuntimeError("no endpoint"))):
            population = Breeder(self.spec(role_text_mutation="llm"), repo_root=self.root).breed()
        mutant = self.by_kind(population)["mutant"]
        self.assertFalse(self.revision_lines(mutant))
        self.assertFalse(any("__g" in r.role_id for r in mutant.role_library))
        history = " ".join(mutant.mutation_history)
        self.assertIn(f"role {PROBE.role_id} kept, LLM revision declined (revision failed: RuntimeError: no endpoint)",
                      history)
        self.assertIn("pool", history)

    def test_mode_is_validated_in_the_spec_and_the_breeder(self):
        with self.assertRaises(BreedingError):
            Breeder(self.spec(role_text_mutation="bogus"), repo_root=self.root)
        path = os.path.join(self.root, "gen17.json")
        for mode, ok in (("bogus", False), ("llm", True), ("off", True), ("tags", True)):
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"generation": 17, "name": "V8d", "parent_scorecards": "cards", "objective": "repair",
                           "role_text_mutation": mode}, fh)
            if ok:
                self.assertEqual(GenerationSpec.load(path).role_text_mutation, mode)
            else:
                with self.assertRaises(BreedingError):
                    GenerationSpec.load(path)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"generation": 17, "name": "V8d", "parent_scorecards": "cards", "objective": "repair"}, fh)
        self.assertEqual(GenerationSpec.load(path).role_text_mutation, "tags")


if __name__ == "__main__":
    unittest.main()
