"""V8 Track B: `MOVE_RECRUIT_SPECIALIST`, per-role routing and evidence credit in the epistemic loop.

Three things are pinned down here, in order of importance:

  1. The None branch is the V6 loop. The same seeded search run with and
     without the V8 keywords (`org_state=None, recruit_specialist=None`)
     produces the same move log, the same hypotheses and the same stats --
     and the pre-V8 adapters (no `role=` keyword) are never handed one.
  2. With an `OrgState`, PROPOSE and SYNTHESIZE are routed to a role
     (`pick_role`), hypotheses and move records carry the `role_id`, and the
     gatekeeper's verdicts are credited to the role that *proposed* the
     hypothesis, the synthesis to the role that wrote (end-of-move credit,
     org.py "Honesty notes").
  3. The recruit move fires only under pressure (stall or an uncovered
     module), goes through the CEO callback, records a move whether or not a
     role was hired, starts the cooldown, and routes the very next proposal
     to the recruit through the optimistic prior.

Everything below the (fake) System 1 is real: workspace, gatekeeper probes in
a subprocess, ledger, loop. The runner's CEO adapter is tested with
`call_llm` mocked, as in tests/test_epistemic_runner.py.
"""

import csv
import importlib.util
import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

from hae.epistemic.gatekeeper import EvidenceGatekeeper
from hae.epistemic.ledger import FALSIFIED, Q_RESOLVED, SUPPORTED, EpistemicState
from hae.epistemic.mcts import STOP_RESOLVED, EpistemicSearchLoop
from hae.epistemic.moves import (
    MAX_RECRUIT_TAGS, MOVE_PROPOSE_HYPOTHESIS, MOVE_RECRUIT_SPECIALIST, MOVE_RUN_EXPERIMENT,
    MOVE_SYNTHESIZE, MOVE_TYPES, V8_RECRUIT_SCHEMA, parse_recruit_packet,
)
from hae.epistemic.org import CEOPolicyGene, OrgState, RoleAllele, stable_role_id
from hae.epistemic.value import EpistemicValueFunction
from hae.evaluation.verification_loop import VerificationLoop
from hae.genome.schema import EpistemicPolicyGene
from hae.runtime.company import SUITE_TAG_TO_MODULE, HierarchicalCompanyRunner
from hae.runtime.workspace import AgentWorkspace
from tests.test_epistemic_runner import BUGGY as RUNNER_BUGGY, OBJECTIVE, _genome
from tests.test_epistemic_search import BUGGY, FIXED, FakeSystem1, right, wrong


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #

class RoleAwareSystem1(FakeSystem1):
    """A V8-aware System 1: accepts `role=` and records who was asked."""

    def __init__(self, ws, rounds, synth_fix=True):
        super().__init__(ws, rounds, synth_fix)
        self.propose_roles = []
        self.synth_roles = []

    def propose(self, question, state, k, role=None):
        self.propose_roles.append(role.role_id if role is not None else None)
        return super().propose(question, state, k)

    def synthesize(self, question, hypothesis, state, role=None):
        self.synth_roles.append(role.role_id if role is not None else None)
        return super().synthesize(question, hypothesis, state)


def role(name, kind="both", tags=("calc", "mypkg"), **kw):
    return RoleAllele(name=name, goal=f"{name} goal", backstory=f"{name} backstory",
                      domain_tags=list(tags), kind=kind, **kw)


# A probe the gatekeeper refuses (`__file__` reaches outside the stage): the
# hypothesis is parked UNTESTABLE and the move earns dU 0 -- a genuine stall.
BAD_PROBE = "print(__file__)\nfrom mypkg.calc import add\nprint('ADD', add(2, 2))\n"


def refused(prior=0.8, variant=0):
    p = wrong(prior, variant)
    return p.__class__(p.claim, p.mechanism, prior, BAD_PROBE, dict(p.prediction))


def ceo_policy(**kw):
    # Eager recruiting so the tests do not depend on the rng draw: with
    # stall >= 2 or one uncovered module the prior is sigmoid(>= 5) > 0.99.
    base = dict(enabled=True, stall_moves=2, stall_delta_u=0.05, recruit_bias=2.0, recruit_w_stall=3.0,
                recruit_w_unmatched=3.0, recruit_w_headcount=0.0, recruit_cooldown_moves=3,
                max_active_roles=6, exploration_c=0.5, optimistic_prior=0.3)
    base.update(kw)
    return CEOPolicyGene(**base)


class RecordingRecruiter:
    """A CEO callback that hands out a scripted role (or a refusal) and records the call."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def __call__(self, org, state, question, move_index):
        self.calls.append({"org": org, "question_id": question.question_id, "move_index": move_index,
                           "stall": org.stall_counter, "unmatched": list(org.unmatched_modules)})
        if not self.outcomes:
            return None, "nothing left to offer"
        out = self.outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        return out


def strip_volatile(move_dict):
    d = dict(move_dict)
    for k in ("created_at", "state_hash_before", "state_hash_after"):
        d.pop(k, None)
    return d


# --------------------------------------------------------------------------- #
# Packet parsing
# --------------------------------------------------------------------------- #

class RecruitPacketTests(unittest.TestCase):

    def test_move_type_and_schema_are_registered(self):
        self.assertIn(MOVE_RECRUIT_SPECIALIST, MOVE_TYPES)
        self.assertEqual(V8_RECRUIT_SCHEMA["type"], "json_schema")
        schema = V8_RECRUIT_SCHEMA["json_schema"]["schema"]
        self.assertEqual(schema["properties"]["packet"]["enum"], ["RECRUIT_SPECIALIST"])
        self.assertEqual(schema["properties"]["domain_tags"]["minItems"], 3)
        self.assertEqual(schema["properties"]["domain_tags"]["maxItems"], MAX_RECRUIT_TAGS)
        self.assertFalse(schema["additionalProperties"])
        for key in ("name", "goal", "backstory", "domain_tags", "kind", "rationale"):
            self.assertIn(key, schema["required"])

    def test_fenced_packet_parses_and_tags_are_normalised(self):
        raw = ("Here is the hire.\n```json\n" + json.dumps({
            "packet": "RECRUIT_SPECIALIST", "name": "  Numerical  Semantics Engineer ",
            "goal": "Own arithmetic correctness.", "backstory": "Ten years of numerics.",
            "domain_tags": ["Calc", "ARITHMETIC", "calc", "Operator-Precedence", "mypkg/calc.py"],
            "kind": "probe", "rationale": "nobody on the team reads operators",
        }) + "\n```\n")
        p = parse_recruit_packet(raw)
        self.assertIsNotNone(p)
        self.assertEqual(p.name, "Numerical Semantics Engineer")
        self.assertEqual(p.kind, "probe")
        self.assertEqual(p.domain_tags, ["calc", "arithmetic", "operator_precedence", "mypkg_calc_py"])
        self.assertEqual(p.rationale, "nobody on the team reads operators")

    def test_string_tags_and_unknown_kind_are_tolerated(self):
        p = parse_recruit_packet(json.dumps({"name": "Harness Whisperer", "domain_tags": "harness, timeout retry",
                                             "kind": "manager"}))
        self.assertEqual(p.domain_tags, ["harness", "timeout", "retry"])
        self.assertEqual(p.kind, "both")
        self.assertEqual(p.goal, "")

    def test_tag_cap_and_missing_name(self):
        many = [f"tag{i}" for i in range(MAX_RECRUIT_TAGS + 5)]
        p = parse_recruit_packet(json.dumps({"name": "X", "domain_tags": many}))
        self.assertEqual(len(p.domain_tags), MAX_RECRUIT_TAGS)
        self.assertIsNone(parse_recruit_packet(json.dumps({"goal": "no name"})))
        self.assertIsNone(parse_recruit_packet("I decline to hire anyone."))
        self.assertIsNone(parse_recruit_packet(""))


# --------------------------------------------------------------------------- #
# The loop
# --------------------------------------------------------------------------- #

class LoopFixture(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hae_recruit_test_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.ws = AgentWorkspace("recruit_firm", base_dir=self.tmp)
        self.ws.write_file("mypkg/__init__.py", "")
        self.ws.write_file("mypkg/calc.py", BUGGY)
        self.state = EpistemicState("recruit_firm")
        self.gk = EvidenceGatekeeper(self.ws, timeout_s=15, isolate=False, stage_reference=False,
                                     module_for_tag={"calc": "mypkg/calc.py"})
        self.gk.reconcile_with_oracle(
            self.state, ["[calc] FAIL: test_add (t.T.test_add) -> AssertionError: 0 != 4"], iteration=1)
        self.q = next(iter(self.state.questions.values()))

    def loop(self, sys1, org=None, recruiter=None, with_v8_kwargs=True, **policy_kw):
        kw = dict(enabled=True, search_budget_moves=20, min_hypotheses_before_synthesis=2,
                  low_prior_quota=0.0, max_stagnant_moves=8)
        kw.update(policy_kw)
        policy = EpistemicPolicyGene(**kw)
        extra = {}
        if with_v8_kwargs:
            extra = dict(org_state=org, recruit_specialist=recruiter)
        return EpistemicSearchLoop(self.state, self.gk, EpistemicValueFunction(0.6), policy,
                                   sys1.propose, sys1.synthesize, rng_seed=7, logger=lambda s: None,
                                   agent_roles={MOVE_PROPOSE_HYPOTHESIS: "Bound Proposer",
                                                MOVE_RUN_EXPERIMENT: "EvidenceGatekeeper",
                                                MOVE_SYNTHESIZE: "Bound Synthesiser"},
                                   **extra)


class NoneBranchIdentityTests(LoopFixture):
    """`org_state=None` is the V6 loop: same decisions, same records, no `role=` keyword."""

    def run_once(self, with_v8_kwargs):
        # A fresh fixture per run so the two searches start from identical ledgers.
        self.setUp()
        sys1 = FakeSystem1(self.ws, rounds=[[wrong(0.8), right(0.2)]])   # the pre-V8 fake: no `role=`
        res = self.loop(sys1, with_v8_kwargs=with_v8_kwargs).run()
        return res, sys1

    def test_same_search_with_and_without_the_v8_keywords(self):
        a, sys_a = self.run_once(with_v8_kwargs=False)
        b, sys_b = self.run_once(with_v8_kwargs=True)
        self.assertEqual(a.stop_reason, STOP_RESOLVED)
        self.assertEqual(a.stop_reason, b.stop_reason)
        self.assertEqual([strip_volatile(m.to_dict()) for m in a.trajectory],
                         [strip_volatile(m.to_dict()) for m in b.trajectory])
        self.assertEqual(a.stats, b.stats)
        self.assertNotIn("recruits", a.stats)
        self.assertNotIn("org", a.to_dict())
        self.assertNotIn("org", b.to_dict())
        self.assertEqual(sys_a.propose_calls, sys_b.propose_calls)
        self.assertEqual(sys_a.synth_calls, sys_b.synth_calls)
        for move in b.trajectory:
            self.assertEqual(move.role_id, "")
        self.assertEqual({m.move_type: m.agent_role for m in b.trajectory},
                         {MOVE_PROPOSE_HYPOTHESIS: "Bound Proposer", MOVE_RUN_EXPERIMENT: "EvidenceGatekeeper",
                          MOVE_SYNTHESIZE: "Bound Synthesiser"})
        for h in b.final_state.hypotheses.values():
            self.assertEqual(h.role_id, "")
            self.assertEqual(h.proposed_by, "Bound Proposer")

    def test_an_empty_team_routes_nothing_and_the_pre_v8_adapter_still_works(self):
        # Turn-0 selection can fail to seat anyone; `pick_role` is then None
        # for every move, the adapter is called without `role=` (the pre-V8
        # fake would raise on it) and nothing about roles is recorded.
        org = OrgState([], ceo_policy(recruit_bias=-8.0))
        sys1 = FakeSystem1(self.ws, rounds=[[wrong(0.8), right(0.2)]])
        res = self.loop(sys1, org=org, recruiter=None).run()
        self.assertEqual(res.stop_reason, STOP_RESOLVED)
        self.assertEqual({m.move_type: (m.agent_role, m.role_id) for m in res.trajectory},
                         {MOVE_PROPOSE_HYPOTHESIS: ("Bound Proposer", ""), MOVE_RUN_EXPERIMENT: ("EvidenceGatekeeper", ""),
                          MOVE_SYNTHESIZE: ("Bound Synthesiser", "")})
        self.assertFalse(res.stats["roles_routed"])
        self.assertEqual(res.stats["moves_by_role"], {})
        self.assertEqual(res.stats["recruits"], 0)
        self.assertIn("org", res.to_dict())                                 # the org is still reported
        self.assertEqual(res.to_dict()["org"]["active_roles"], [])

    def test_a_synthesis_only_team_falls_back_on_propose_and_routes_synthesize(self):
        writer = role("Writer", kind="synthesis")
        org = OrgState([writer], ceo_policy(recruit_bias=-8.0))
        sys1 = RoleAwareSystem1(self.ws, rounds=[[wrong(0.8), right(0.2)]])
        res = self.loop(sys1, org=org, recruiter=None).run()
        self.assertEqual(res.stop_reason, STOP_RESOLVED)
        self.assertEqual(sys1.propose_roles, [None])
        self.assertEqual(sys1.synth_roles, [writer.role_id])
        proposes = [m for m in res.trajectory if m.move_type == MOVE_PROPOSE_HYPOTHESIS]
        self.assertEqual((proposes[0].agent_role, proposes[0].role_id), ("Bound Proposer", ""))
        for h in self.state.hypotheses.values():
            self.assertEqual((h.proposed_by, h.role_id), ("Bound Proposer", ""))
        experiments = [m for m in res.trajectory if m.move_type == MOVE_RUN_EXPERIMENT]
        self.assertTrue(all(m.role_id == "" for m in experiments))         # nobody to credit
        self.assertEqual(org.stats[writer.role_id].visits, 1)              # only the synthesis
        self.assertEqual(res.stats["moves_by_role"], {writer.role_id: 1})
        self.assertTrue(res.stats["roles_routed"])


class RoutingAndCreditTests(LoopFixture):

    def test_propose_and_synthesize_are_routed_and_verdicts_credit_the_proposer(self):
        analyst = role("Harness Analyst", kind="probe")
        engineer = role("Repair Engineer", kind="synthesis")
        org = OrgState([analyst, engineer], ceo_policy(recruit_bias=-8.0))
        sys1 = RoleAwareSystem1(self.ws, rounds=[[wrong(0.8), right(0.2)]])
        res = self.loop(sys1, org=org, recruiter=None).run()
        self.assertEqual(res.stop_reason, STOP_RESOLVED)
        self.assertEqual(self.q.status, Q_RESOLVED)
        types = [m.move_type for m in res.trajectory]
        self.assertEqual(types, [MOVE_PROPOSE_HYPOTHESIS, MOVE_RUN_EXPERIMENT, MOVE_RUN_EXPERIMENT, MOVE_SYNTHESIZE])
        self.assertEqual(sys1.propose_roles, [analyst.role_id])
        self.assertEqual(sys1.synth_roles, [engineer.role_id])

        propose, exp1, exp2, synth = res.trajectory
        self.assertEqual((propose.agent_role, propose.role_id), ("Harness Analyst", analyst.role_id))
        self.assertEqual((synth.agent_role, synth.role_id), ("Repair Engineer", engineer.role_id))
        # Experiments keep the gatekeeper's label but name the credited proposer.
        for exp in (exp1, exp2):
            self.assertEqual(exp.agent_role, "EvidenceGatekeeper")
            self.assertEqual(exp.role_id, analyst.role_id)
        for h in self.state.hypotheses.values():
            self.assertEqual(h.role_id, analyst.role_id)
            self.assertEqual(h.proposed_by, "Harness Analyst")

        st_a, st_e = org.stats[analyst.role_id], org.stats[engineer.role_id]
        self.assertEqual((st_a.visits, st_a.supported, st_a.falsified), (2, 1, 1))
        self.assertAlmostEqual(st_a.cumulative_delta_u, exp1.delta_u + exp2.delta_u, places=6)
        self.assertEqual((st_e.visits, st_e.syntheses_written), (1, 1))
        self.assertAlmostEqual(st_e.cumulative_delta_u, synth.delta_u, places=6)
        self.assertGreater(synth.delta_u, 0.0)

        self.assertTrue(res.stats["roles_routed"])
        self.assertEqual(res.stats["moves_by_role"], {analyst.role_id: 3, engineer.role_id: 1})
        self.assertAlmostEqual(res.stats["delta_u_by_role"][analyst.role_id], exp1.delta_u + exp2.delta_u, places=5)
        self.assertAlmostEqual(res.stats["delta_u_by_role"][engineer.role_id], synth.delta_u, places=5)
        self.assertEqual(res.stats["recruits"], 0)
        out = res.to_dict()
        self.assertEqual(out["org"]["stats"][analyst.role_id]["visits"], 2)
        self.assertEqual([r["role_id"] for r in out["org"]["active_roles"]], [analyst.role_id, engineer.role_id])
        self.assertEqual(self.ws.read_file("mypkg/calc.py")["content"], FIXED)

    def test_failed_syntheses_are_credited_as_stalls_not_writes(self):
        writer = role("Writer", kind="both")
        org = OrgState([writer], ceo_policy(recruit_bias=-8.0, stall_delta_u=0.05))
        sys1 = RoleAwareSystem1(self.ws, rounds=[[right(0.9), wrong(0.1)]], synth_fix=False)
        res = self.loop(sys1, org=org, recruiter=None, min_hypotheses_before_synthesis=1).run()
        synths = [m for m in res.trajectory if m.move_type == MOVE_SYNTHESIZE]
        self.assertTrue(synths)
        self.assertTrue(all(m.role_id == writer.role_id for m in synths))
        self.assertEqual(org.stats[writer.role_id].syntheses_written, 0)
        self.assertGreaterEqual(org.stats[writer.role_id].consecutive_stalls, 1)
        self.assertEqual(res.stats["syntheses_unwritten"], len(synths))


class RecruitMoveTests(LoopFixture):
    """Stalls here are refused probes: UNTESTABLE verdicts, dU 0, nothing learnt."""

    STALL_THEN_TRUTH = [[refused(0.8, 0), refused(0.7, 1)], [right(0.3)]]

    def test_no_callback_means_no_recruit_move_even_under_pressure(self):
        org = OrgState([role("Generalist")], ceo_policy())
        sys1 = RoleAwareSystem1(self.ws, rounds=self.STALL_THEN_TRUTH)
        res = self.loop(sys1, org=org, recruiter=None, max_hypothesis_rounds=2).run()
        self.assertNotIn(MOVE_RECRUIT_SPECIALIST, [m.move_type for m in res.trajectory])
        self.assertEqual(res.stats["recruits"], 0)
        self.assertEqual(res.stats["rejected_probes"], 2)
        self.assertGreaterEqual(org.stall_counter, 0)
        self.assertEqual(res.stop_reason, STOP_RESOLVED)

    def test_stall_triggers_a_recruit_and_the_next_proposal_goes_to_the_recruit(self):
        generalist = role("Generalist")
        org = OrgState([generalist], ceo_policy())
        hire = role("Arithmetic Debugger", tags=("calc", "arithmetic"),
                    origin="recruited:test", extra={"source": "synthesized"})
        recruiter = RecordingRecruiter([(hire, "synthesized by CEO: operators")])
        sys1 = RoleAwareSystem1(self.ws, rounds=self.STALL_THEN_TRUTH)
        res = self.loop(sys1, org=org, recruiter=recruiter, max_hypothesis_rounds=2).run()

        types = [m.move_type for m in res.trajectory]
        self.assertEqual(types, [MOVE_PROPOSE_HYPOTHESIS, MOVE_RUN_EXPERIMENT, MOVE_RUN_EXPERIMENT,
                                 MOVE_RECRUIT_SPECIALIST, MOVE_PROPOSE_HYPOTHESIS, MOVE_RUN_EXPERIMENT,
                                 MOVE_SYNTHESIZE])
        self.assertEqual(res.stop_reason, STOP_RESOLVED)
        # The callback saw the live organisation, the stall that justified the
        # hire and the index the recruit move was about to take.
        self.assertEqual(len(recruiter.calls), 1)
        call = recruiter.calls[0]
        self.assertIs(call["org"], org)
        self.assertEqual((call["move_index"], call["stall"], call["question_id"]), (4, 2, self.q.question_id))

        recruit = res.trajectory[3]
        self.assertEqual((recruit.move_index, recruit.agent_role, recruit.role_id), (4, "Arithmetic Debugger", hire.role_id))
        self.assertEqual(recruit.delta_u, 0.0)
        self.assertEqual(recruit.note, "recruited Arithmetic Debugger [synthesized] (stall=2, unmatched=0; "
                                       "tags: calc, arithmetic): synthesized by CEO: operators")
        self.assertGreater(recruit.extra["recruit_prior"], 0.99)
        self.assertEqual(recruit.extra["stall_counter"], 2.0)

        self.assertEqual(res.stats["recruits"], 1)
        self.assertEqual(res.stats["recruits_synthesized"], 1)
        self.assertEqual(res.stats["recruits_from_library"], 0)
        self.assertEqual(res.stats["recruit_declined"], 0)
        self.assertEqual(org.active_ids, [generalist.role_id, hire.role_id])
        self.assertEqual(org.stats[hire.role_id].recruited_at_move, 4)
        self.assertEqual(org.stats[generalist.role_id].untestable, 2)
        self.assertEqual(org.recruit_log[0]["move_index"], 4)
        self.assertEqual(org.recruit_log[0]["source"], "synthesized")
        self.assertEqual(org.recruit_log[0]["reason"], "stall=2, unmatched=0")
        self.assertEqual(org.recruit_log[0]["stall_counter"], 2)

        # Optimistic prior: the incumbent's mean dU is 0 after two refused
        # probes, the recruit starts at the CEO's prior plus the full
        # exploration bonus, so it proposes next -- and the supported
        # hypothesis it proposed is credited to it, as is the synthesis.
        self.assertEqual(sys1.propose_roles, [generalist.role_id, hire.role_id])
        self.assertEqual(sys1.synth_roles, [hire.role_id])
        second_propose = res.trajectory[4]
        self.assertEqual((second_propose.agent_role, second_propose.role_id), ("Arithmetic Debugger", hire.role_id))
        supported = [h for h in self.state.hypotheses.values() if h.status == SUPPORTED]
        self.assertEqual(len(supported), 1)
        self.assertEqual(supported[0].role_id, hire.role_id)
        self.assertEqual(org.stats[hire.role_id].supported, 1)
        self.assertEqual(org.stats[hire.role_id].syntheses_written, 1)
        self.assertEqual(org.stats[hire.role_id].visits, 2)
        self.assertEqual(res.trajectory[5].role_id, hire.role_id)
        self.assertEqual(res.moves_used, 7)                          # the recruit move cost a move
        self.assertEqual(res.to_dict()["org"]["recruit_log"][0]["name"], "Arithmetic Debugger")
        self.assertEqual(res.stats["moves_by_role"], {generalist.role_id: 3, hire.role_id: 3})

    def test_uncovered_module_is_pressure_and_a_library_pick_covers_it(self):
        # Nobody on the team carries `calc` or `mypkg`; the frontier admission
        # reports the module and the very first move is the hire.
        generalist = role("Harness Generalist", tags=("harness", "timeout"))
        specialist = role("Calc Specialist", tags=("calc", "arithmetic"))
        org = OrgState([generalist], ceo_policy(), library=[specialist])

        def from_library(org_, state, question, move_index):
            cands = org_.library_candidates(["calc"])
            self.assertEqual([c.role_id for _s, c in cands], [specialist.role_id])
            pick = cands[0][1].copy()
            pick.extra["source"] = "library"
            return pick, "library pick"

        sys1 = RoleAwareSystem1(self.ws, rounds=[[wrong(0.8), right(0.2)]])
        res = self.loop(sys1, org=org, recruiter=from_library).run()
        first = res.trajectory[0]
        self.assertEqual(first.move_type, MOVE_RECRUIT_SPECIALIST)
        self.assertEqual(first.move_index, 1)
        self.assertEqual(first.note, "recruited Calc Specialist [library] (stall=0, unmatched=1; "
                                     "tags: calc, arithmetic): library pick")
        self.assertEqual(first.extra["unmatched_modules"], 1.0)
        self.assertEqual(res.stats["recruits_from_library"], 1)
        self.assertEqual(res.stats["recruits_synthesized"], 0)
        self.assertEqual(org.unmatched_modules, [])                   # covered by the hire
        self.assertEqual(org.recruit_log[0]["unmatched_modules"], ["mypkg/calc.py"])
        self.assertEqual(res.stop_reason, STOP_RESOLVED)
        # The copy, not the library object, joined the team.
        self.assertIsNot(org.role(specialist.role_id), specialist)
        self.assertNotIn("source", specialist.extra)

    def test_declined_recruit_is_a_move_and_starts_the_cooldown(self):
        generalist = role("Generalist")
        org = OrgState([generalist], ceo_policy(recruit_cooldown_moves=20))
        recruiter = RecordingRecruiter([(None, "budget exhausted")])
        sys1 = RoleAwareSystem1(self.ws, rounds=self.STALL_THEN_TRUTH)
        res = self.loop(sys1, org=org, recruiter=recruiter, max_hypothesis_rounds=2).run()
        types = [m.move_type for m in res.trajectory]
        self.assertEqual(types, [MOVE_PROPOSE_HYPOTHESIS, MOVE_RUN_EXPERIMENT, MOVE_RUN_EXPERIMENT,
                                 MOVE_RECRUIT_SPECIALIST, MOVE_PROPOSE_HYPOTHESIS, MOVE_RUN_EXPERIMENT,
                                 MOVE_SYNTHESIZE])
        declined = res.trajectory[3]
        self.assertEqual(declined.note, "recruit declined (stall=2, unmatched=0): budget exhausted")
        self.assertEqual((declined.agent_role, declined.role_id), ("", ""))
        self.assertEqual(declined.extra["recruit_prior"], res.to_dict()["trajectory"][3]["extra"]["recruit_prior"])
        self.assertEqual(res.stats["recruit_declined"], 1)
        self.assertEqual(res.stats["recruits"], 0)
        self.assertEqual(org.last_recruit_move, 4)                   # cooldown started
        self.assertEqual(org.stall_counter, 0)                       # the SUPPORTED verdict reset it later
        self.assertEqual(org.active_ids, [generalist.role_id])
        self.assertEqual(org.recruit_log, [])
        self.assertEqual(len(recruiter.calls), 1)                    # not asked again within the cooldown
        self.assertEqual(res.stop_reason, STOP_RESOLVED)
        self.assertEqual(sys1.propose_roles, [generalist.role_id, generalist.role_id])

    def test_callback_exception_and_duplicate_role_are_declined_not_fatal(self):
        generalist = role("Generalist")
        org = OrgState([generalist], ceo_policy(recruit_cooldown_moves=2))
        recruiter = RecordingRecruiter([RuntimeError("CEO offline"),
                                        (role("Generalist", tags=("calc",)), "same person again")])
        sys1 = RoleAwareSystem1(self.ws, rounds=self.STALL_THEN_TRUTH)
        res = self.loop(sys1, org=org, recruiter=recruiter, max_hypothesis_rounds=2).run()
        types = [m.move_type for m in res.trajectory]
        self.assertEqual(types, [MOVE_PROPOSE_HYPOTHESIS, MOVE_RUN_EXPERIMENT, MOVE_RUN_EXPERIMENT,
                                 MOVE_RECRUIT_SPECIALIST, MOVE_PROPOSE_HYPOTHESIS, MOVE_RECRUIT_SPECIALIST,
                                 MOVE_RUN_EXPERIMENT, MOVE_SYNTHESIZE])
        recruits = [m for m in res.trajectory if m.move_type == MOVE_RECRUIT_SPECIALIST]
        self.assertEqual(recruits[0].note, "recruit declined (stall=2, unmatched=0): recruit failed: RuntimeError: CEO offline")
        self.assertEqual(recruits[1].note, "recruit declined (stall=2, unmatched=0): duplicate of active role Generalist")
        self.assertEqual([c["move_index"] for c in recruiter.calls], [4, 6])
        self.assertEqual(res.stats["recruit_declined"], 2)
        self.assertEqual(res.stats["recruits"], 0)
        self.assertEqual(org.active_ids, [generalist.role_id])
        self.assertEqual(res.stop_reason, STOP_RESOLVED)

    def test_zero_cooldown_never_yields_two_recruit_moves_in_a_row(self):
        org = OrgState([role("Generalist")], ceo_policy(recruit_cooldown_moves=0))
        recruiter = RecordingRecruiter([])                            # always "nothing left to offer"
        sys1 = RoleAwareSystem1(self.ws, rounds=self.STALL_THEN_TRUTH)
        res = self.loop(sys1, org=org, recruiter=recruiter, max_hypothesis_rounds=2, max_stagnant_moves=12).run()
        types = [m.move_type for m in res.trajectory]
        self.assertGreaterEqual(types.count(MOVE_RECRUIT_SPECIALIST), 2)
        for a, b in zip(types, types[1:]):
            self.assertFalse(a == b == MOVE_RECRUIT_SPECIALIST, types)
        self.assertEqual(res.stats["recruit_declined"], types.count(MOVE_RECRUIT_SPECIALIST))
        self.assertEqual(res.stop_reason, STOP_RESOLVED)

    def test_headcount_cap_blocks_the_recruit(self):
        org = OrgState([role("A"), role("B")], ceo_policy(max_initial_roles=2, max_active_roles=2))
        recruiter = RecordingRecruiter([(role("C"), "")])
        sys1 = RoleAwareSystem1(self.ws, rounds=self.STALL_THEN_TRUTH)
        res = self.loop(sys1, org=org, recruiter=recruiter, max_hypothesis_rounds=2).run()
        self.assertEqual(recruiter.calls, [])
        self.assertNotIn(MOVE_RECRUIT_SPECIALIST, [m.move_type for m in res.trajectory])
        self.assertEqual(res.stop_reason, STOP_RESOLVED)


# --------------------------------------------------------------------------- #
# The runner's adapters
# --------------------------------------------------------------------------- #

CEO_PACKET = json.dumps({
    "packet": "RECRUIT_SPECIALIST", "name": "Operator Semantics Engineer",
    "goal": "Find and fix wrong arithmetic operators.", "backstory": "Wrote a linter for operator misuse.",
    "domain_tags": ["calc", "arithmetic", "operators"], "kind": "both",
    "rationale": "the current team keeps probing the harness instead of the operator",
})


class ScriptedCEO:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def __call__(self, prompt, model_name, temperature, system_instruction,
                 usage_sink=None, response_format=None, max_tokens=None, **_):
        self.calls.append({"prompt": prompt, "model": model_name, "system": system_instruction,
                           "schema": (response_format or {}).get("json_schema", {}).get("name"),
                           "max_tokens": max_tokens})
        if usage_sink is not None:
            usage_sink.update({"measured": True, "prompt_tokens": 400, "output_tokens": 120})
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


class RecruitAdapterTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hae_recruit_adapter_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.runner = HierarchicalCompanyRunner(_genome())
        self.runner.workspace = AgentWorkspace("epi_runner_firm", base_dir=self.tmp)
        self.runner.verification_loop = VerificationLoop(self.runner.workspace)
        self.runner.workspace.write_file("mypkg/__init__.py", "")
        self.runner.workspace.write_file("mypkg/calc.py", RUNNER_BUGGY)
        self.runner._init_required_modules_for_objective(OBJECTIVE)
        self.runner._make_gatekeeper = lambda policy=None: EvidenceGatekeeper(
            self.runner.workspace, timeout_s=20, max_probe_lines=40,
            graded_modules=self.runner._required_modules, module_for_tag=SUITE_TAG_TO_MODULE,
            isolate=False, stage_reference=False)
        self.state = EpistemicState("epi_runner_firm")
        self.q = self.state.add_question("Why does test_add fail in `mypkg/calc.py`?", module="mypkg/calc.py",
                                         source_failure="FAIL: test_add (t.T) -> AssertionError: -1 != 5")
        self.policy = self.runner.epistemic_policy

    def adapter(self, org):
        return self.runner._recruit_specialist_adapter(OBJECTIVE, self.policy, org)

    def test_library_mode_picks_an_overlapping_role_without_an_llm_call(self):
        harness = role("Harness Engineer", tags=("harness", "timeout"))
        unrelated = role("Docs Writer", tags=("docs", "markdown"))
        calc = role("Calc Specialist", tags=("calc", "arithmetic"))
        org = OrgState([harness], ceo_policy(recruit_mode="library"), library=[unrelated, calc])
        org.observe_modules(["mypkg/calc.py"])
        llm = ScriptedCEO(CEO_PACKET)
        with mock.patch("hae.runtime.company.call_llm", llm):
            picked, note = self.adapter(org)(org, self.state, self.q, 3)
        self.assertEqual(llm.calls, [])
        self.assertIsNotNone(picked)
        self.assertEqual(picked.role_id, calc.role_id)
        self.assertIsNot(picked, calc)                               # a copy; the library object is untouched
        self.assertEqual(picked.extra["source"], "library")
        self.assertNotIn("source", calc.extra)
        self.assertIn("library pick", note)

    def test_library_mode_declines_when_nothing_overlaps(self):
        harness = role("Harness Engineer", tags=("harness", "timeout"))
        unrelated = role("Docs Writer", tags=("docs", "markdown"))
        org = OrgState([harness], ceo_policy(recruit_mode="library"), library=[unrelated])
        llm = ScriptedCEO(CEO_PACKET)
        with mock.patch("hae.runtime.company.call_llm", llm):
            picked, note = self.adapter(org)(org, self.state, self.q, 3)
        self.assertIsNone(picked)
        self.assertEqual(llm.calls, [])
        self.assertIn("no library role overlaps", note)

    def test_synthesize_mode_asks_the_ceo_under_the_grammar_and_builds_a_role(self):
        harness = role("Harness Engineer", tags=("harness", "timeout"))
        org = OrgState([harness], ceo_policy(recruit_mode="synthesize"))
        org.stall_counter = 3
        org.observe_modules(["mypkg/calc.py"])
        llm = ScriptedCEO(CEO_PACKET)
        with mock.patch("hae.runtime.company.call_llm", llm):
            hired, note = self.adapter(org)(org, self.state, self.q, 5)
        self.assertEqual(len(llm.calls), 1)
        call = llm.calls[0]
        self.assertEqual(call["schema"], "RecruitSpecialistPacket")
        self.assertEqual(call["model"], "gemini-2.5-pro")             # the CEO is executive tier
        self.assertEqual(call["max_tokens"], 800)
        self.assertIn("RECRUIT ONE SPECIALIST", call["prompt"])
        self.assertIn("stalled for 3 consecutive move(s)", call["prompt"])
        self.assertIn("mypkg/calc.py", call["prompt"])
        self.assertIn("Harness Engineer [both; harness, timeout]", call["prompt"])
        self.assertIn("You are the CEO.", call["system"])
        self.assertIsNotNone(hired)
        self.assertEqual(hired.name, "Operator Semantics Engineer")
        self.assertEqual(hired.role_id, stable_role_id("Operator Semantics Engineer"))
        self.assertEqual(hired.domain_tags, ["calc", "arithmetic", "operators"])
        self.assertEqual(hired.kind, "both")
        self.assertEqual(hired.model_tier, "worker")
        self.assertTrue(hired.tools_enabled)
        self.assertEqual(hired.origin, "recruited:epi_runner_firm:g16:m5")
        self.assertEqual(hired.created_generation, 16)
        self.assertEqual(hired.extra["source"], "synthesized")
        self.assertIn("operator", hired.extra["rationale"])
        self.assertIn("synthesized by CEO", note)
        # And the hire executes as itself when routed a PROPOSE.
        agent = hired.to_agent_genome()
        self.assertEqual(agent.role, "Operator Semantics Engineer")
        self.assertEqual(agent.extra["role_id"], hired.role_id)

    def test_both_mode_falls_back_to_the_ceo_when_the_library_has_nothing(self):
        harness = role("Harness Engineer", tags=("harness", "timeout"))
        unrelated = role("Docs Writer", tags=("docs", "markdown"))
        org = OrgState([harness], ceo_policy(recruit_mode="both"), library=[unrelated])
        llm = ScriptedCEO(CEO_PACKET)
        with mock.patch("hae.runtime.company.call_llm", llm):
            hired, note = self.adapter(org)(org, self.state, self.q, 2)
        self.assertEqual(len(llm.calls), 1)
        self.assertEqual(hired.extra["source"], "synthesized")
        self.assertIn("no library role overlaps", note)
        self.assertIn("synthesized by CEO", note)

    def test_duplicate_unparseable_and_failing_ceo_replies_are_declined(self):
        existing = role("Operator Semantics Engineer", tags=("calc",))
        org = OrgState([existing], ceo_policy(recruit_mode="synthesize"))
        with mock.patch("hae.runtime.company.call_llm", ScriptedCEO(CEO_PACKET)):
            hired, note = self.adapter(org)(org, self.state, self.q, 2)
        self.assertIsNone(hired)
        self.assertEqual(note, "duplicate of active role Operator Semantics Engineer")

        org = OrgState([role("Harness Engineer", tags=("harness",))], ceo_policy(recruit_mode="synthesize"))
        with mock.patch("hae.runtime.company.call_llm", ScriptedCEO("I would not hire anyone right now.")):
            hired, note = self.adapter(org)(org, self.state, self.q, 2)
        self.assertIsNone(hired)
        self.assertIn("no usable RECRUIT_SPECIALIST packet", note)

        with mock.patch("hae.runtime.company.call_llm", ScriptedCEO(RuntimeError("vLLM down"))), \
                redirect_stdout(io.StringIO()):
            hired, note = self.adapter(org)(org, self.state, self.q, 2)
        self.assertIsNone(hired)
        self.assertEqual(note, "recruit failed: RuntimeError: vLLM down")

    def test_budget_exhausted_declines_before_the_call(self):
        org = OrgState([role("Harness Engineer", tags=("harness",))], ceo_policy(recruit_mode="synthesize"))
        llm = ScriptedCEO(CEO_PACKET)
        with mock.patch.object(self.runner, "_may_call", return_value=False), \
                mock.patch("hae.runtime.company.call_llm", llm):
            hired, note = self.adapter(org)(org, self.state, self.q, 2)
        self.assertIsNone(hired)
        self.assertEqual(note, "budget exhausted")
        self.assertEqual(llm.calls, [])


class RoutedSystem1AdapterTests(unittest.TestCase):
    """`propose(..., role=)` and `synthesize(..., role=)`: the specialist executes, the bound agent otherwise."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hae_recruit_adapter_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.runner = HierarchicalCompanyRunner(_genome())
        self.runner.workspace = AgentWorkspace("epi_runner_firm", base_dir=self.tmp)
        self.runner.verification_loop = VerificationLoop(self.runner.workspace)
        self.runner.workspace.write_file("mypkg/__init__.py", "")
        self.runner.workspace.write_file("mypkg/calc.py", RUNNER_BUGGY)
        self.runner._init_required_modules_for_objective(OBJECTIVE)
        self.state = EpistemicState("epi_runner_firm")
        self.q = self.state.add_question("Why does test_add fail in `mypkg/calc.py`?", module="mypkg/calc.py",
                                         source_failure="FAIL: test_add (t.T) -> AssertionError: -1 != 5")
        self.h = self.state.add_hypothesis(self.q.question_id, "add subtracts its operands", "minus instead of plus",
                                           0.3, "from mypkg.calc import add\nprint(add(2, 3))\n",
                                           {"expect_exit_code": 0, "expect_stdout_contains": "-1"})
        self.agents = self.runner._bind_epistemic_agents(self.runner.epistemic_policy)

    def test_propose_is_executed_by_the_routed_role(self):
        specialist = role("Operator Semantics Engineer", tags=("calc", "operators"), temperature=0.1)
        llm = ScriptedCEO(json.dumps({"packet": "HYPOTHESIS_SET", "question_id": self.q.question_id, "hypotheses": []}))
        propose = self.runner._propose_hypotheses_adapter(self.agents["hypothesis"], OBJECTIVE,
                                                           self.runner.epistemic_policy)
        with mock.patch("hae.runtime.company.call_llm", llm), redirect_stdout(io.StringIO()):
            propose(self.q, self.state, 2, role=specialist)
            propose(self.q, self.state, 2)
        routed, bound = llm.calls
        self.assertIn("You are the Operator Semantics Engineer.", routed["system"])
        self.assertIn("YOU ARE THE FIRM'S OPERATOR SEMANTICS ENGINEER (domain: calc, operators)", routed["prompt"])
        self.assertIn(f"You are the {self.agents['hypothesis'].role}.", bound["system"])
        self.assertNotIn("YOU ARE THE FIRM'S", bound["prompt"])

    def test_synthesize_is_executed_by_the_routed_role(self):
        specialist = role("Repair Specialist", kind="synthesis", tags=("calc",))
        llm = ScriptedCEO("no plan from me")
        synthesize = self.runner._synthesize_patch_adapter(self.agents["synthesis"], OBJECTIVE)
        with mock.patch("hae.runtime.company.call_llm", llm):
            res = synthesize(self.q, self.h, self.state, role=specialist)
        self.assertFalse(res["written"])
        self.assertTrue(llm.calls)
        self.assertTrue(all("You are the Repair Specialist." in c["system"] for c in llm.calls))


# --------------------------------------------------------------------------- #
# Telemetry scripts
# --------------------------------------------------------------------------- #

def _load_script(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join("scripts", f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TelemetryTests(LoopFixture):

    def tree_for(self, res, company="recruit_firm"):
        search = res.to_dict()
        search["iteration"] = 2
        return {"company_id": company, "generation": 17, "ledger": self.state.to_dict(),
                "searches": [search], "audit": None, "policy": None}

    def extract(self, tree):
        evt = _load_script("extract_value_telemetry")
        root = tempfile.mkdtemp(prefix="hae_recruit_tree_", dir=self.tmp)
        tree_dir = os.path.join(root, "outputs", tree["company_id"], "generation_17")
        os.makedirs(tree_dir)
        with open(os.path.join(tree_dir, f"{tree['company_id']}_epistemic_tree.json"), "w") as f:
            json.dump(tree, f, default=str)
        csv_path, json_path = os.path.join(root, "vt.csv"), os.path.join(root, "vt.json")
        with redirect_stdout(io.StringIO()) as out:
            summary = evt.main(["--root", root, "--out", csv_path, "--json", json_path])
        with open(csv_path, newline="") as f:
            rows = list(csv.DictReader(f))
        return evt, summary, rows, out.getvalue()

    def test_routed_tree_gets_a_trailing_role_id_column_and_a_per_role_count(self):
        analyst, engineer = role("Harness Analyst", kind="probe"), role("Repair Engineer", kind="synthesis")
        org = OrgState([analyst, engineer], ceo_policy(recruit_bias=-8.0))
        res = self.loop(RoleAwareSystem1(self.ws, rounds=[[wrong(0.8), right(0.2)]]), org=org).run()
        tree = self.tree_for(res)
        self.assertIn("org", tree["searches"][0])
        evt, summary, rows, stdout = self.extract(tree)
        self.assertEqual(summary["columns"][-1], "role_id")
        self.assertEqual(summary["columns"][:len(evt.HEAD_COLUMNS)], list(evt.HEAD_COLUMNS))
        self.assertEqual(len(rows), 4)
        self.assertEqual([r["role_id"] for r in rows],
                         [analyst.role_id, analyst.role_id, analyst.role_id, engineer.role_id])
        self.assertEqual([r["agent_role"] for r in rows],
                         ["Harness Analyst", "EvidenceGatekeeper", "EvidenceGatekeeper", "Repair Engineer"])
        self.assertEqual(summary["moves_by_role"], {analyst.role_id: 3, engineer.role_id: 1})
        self.assertIn("moves by role (V8)", stdout)

        cohort = _load_script("summarize_gen16_cohort")
        org_summary = cohort.org_summary(tree["searches"])
        self.assertEqual((org_summary["team0"], org_summary["team_final"], org_summary["recruited"]), (2, 2, 0))
        self.assertTrue(org_summary["roles_routed"])
        by_name = {r["name"]: r for r in org_summary["roles"]}
        self.assertEqual(by_name["Harness Analyst"]["visits"], 2)
        self.assertEqual(by_name["Repair Engineer"]["syntheses_written"], 1)
        pooled = cohort.pooled_search_stats(tree["searches"])
        self.assertEqual(pooled["recruits"], 0)

    def test_tree_without_an_organisation_keeps_the_pre_v8_columns(self):
        res = self.loop(FakeSystem1(self.ws, rounds=[[wrong(0.8), right(0.2)]]), with_v8_kwargs=False).run()
        tree = self.tree_for(res)
        self.assertNotIn("org", tree["searches"][0])
        evt, summary, rows, stdout = self.extract(tree)
        self.assertNotIn("role_id", summary["columns"])
        self.assertNotIn("moves_by_role", summary)
        self.assertNotIn("moves by role", stdout)
        self.assertEqual(summary["columns"][-len(evt.TAIL_COLUMNS):], list(evt.TAIL_COLUMNS))
        cohort = _load_script("summarize_gen16_cohort")
        self.assertIsNone(cohort.org_summary(tree["searches"]))
        self.assertNotIn("recruits", cohort.pooled_search_stats(tree["searches"]))

    def test_recruit_move_is_a_row_with_the_hired_role(self):
        generalist = role("Harness Generalist", tags=("harness",))
        specialist = role("Calc Specialist", tags=("calc",), extra={"source": "library"})
        org = OrgState([generalist], ceo_policy(), library=[specialist])
        res = self.loop(RoleAwareSystem1(self.ws, rounds=[[wrong(0.8), right(0.2)]]), org=org,
                        recruiter=lambda o, s, q, i: (specialist.copy(), "library pick")).run()
        tree = self.tree_for(res)
        _evt, summary, rows, _ = self.extract(tree)
        self.assertEqual(rows[0]["move_type"], MOVE_RECRUIT_SPECIALIST)
        self.assertEqual((rows[0]["role_id"], rows[0]["agent_role"], rows[0]["delta_u"]),
                         (specialist.role_id, "Calc Specialist", "0.0"))
        self.assertEqual(summary["moves_by_type"][MOVE_RECRUIT_SPECIALIST], 1)
        cohort = _load_script("summarize_gen16_cohort")
        org_summary = cohort.org_summary(tree["searches"])
        self.assertEqual((org_summary["team0"], org_summary["team_final"], org_summary["recruited"]), (1, 2, 1))
        self.assertEqual(org_summary["recruit_log"], [{"move_index": 1, "name": "Calc Specialist", "source": "library"}])
        self.assertEqual(cohort.pooled_search_stats(tree["searches"])["recruits_from_library"], 1)


if __name__ == "__main__":
    unittest.main()
