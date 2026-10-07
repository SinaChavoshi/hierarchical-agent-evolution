"""V7 heads: hypothesis features, the two logistic heads, shadow-mode wiring and the offline trainer.

The loop tests use a scripted gatekeeper that writes beliefs through a
`GatekeeperAuthority` exactly as the real one does (verdict + uncertainty
drop), so the PUCT path under test is the production one; only the probe
execution is faked. The company test runs the real runner with `call_llm`
mocked, as `tests/test_epistemic_runner.py` does.
"""

import csv
import importlib.util
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, ".")

from hae.epistemic.ledger import (  # noqa: E402
    CERTIFIED, FALSIFIED, Q_EXHAUSTED, Q_OPEN, Q_RESOLVED, SUPPORTED, UNVERIFIED,
    EpistemicState, Evidence, GatekeeperAuthority, Hypothesis, MoveRecord, Question,
)
from hae.epistemic.mcts import EpistemicSearchLoop, SearchResult  # noqa: E402
from hae.epistemic.moves import (  # noqa: E402
    MOVE_PROPOSE_HYPOTHESIS, MOVE_RUN_EXPERIMENT, MOVE_SYNTHESIZE, HypothesisProposal,
)
from hae.epistemic.value import (  # noqa: E402
    FEATURE_NAMES, HEADS_DIR, HYPOTHESIS_FEATURE_NAMES, EpistemicValueFunction, HypothesisPriorHead,
    LearnedValueHead, epistemic_features, failure_class, hypothesis_features, load_head,
    load_policy_heads, probe_imports_module, resolve_head_path,
)
from hae.genome.schema import EpistemicPolicyGene  # noqa: E402


def _load_script(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join("scripts", f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


tvh = _load_script("train_value_heads")
evt = _load_script("extract_value_telemetry")

PROBE = "from mypkg.calc import add\nprint('ADD', add(2, 2))\n"


def proposal(claim, prior, mechanism=None):
    return HypothesisProposal(claim, mechanism or claim, prior, PROBE,
                              {"expect_exit_code": 0, "expect_stdout_contains": "ADD"})


class ScriptedGatekeeper:
    """Verdicts by claim, written through the authority like `EvidenceGatekeeper.apply`."""

    def __init__(self, truth):
        self.truth = dict(truth)
        self.authority = GatekeeperAuthority("scripted")

    def run_experiment(self, state, h):
        ok = bool(self.truth.get(h.claim, False))
        return Evidence(evidence_id=state.new_evidence_id(), kind="probe", hypothesis_id=h.hypothesis_id,
                        question_id=h.question_id, command="scripted", exit_code=0 if ok else 1,
                        prediction=dict(h.prediction), matched_prediction=ok,
                        detail="prediction held" if ok else "prediction failed")

    def apply(self, state, h, evidence):
        state.record_evidence(evidence, self.authority)
        q = state.questions[h.question_id]
        if evidence.matched_prediction:
            state.set_hypothesis_verdict(h.hypothesis_id, SUPPORTED, 0.8, evidence.evidence_id, self.authority)
            new_u = q.initial_uncertainty * 0.2
            if q.status == Q_EXHAUSTED:
                state.set_question_status(q.question_id, Q_OPEN, self.authority)
        else:
            state.set_hypothesis_verdict(h.hypothesis_id, FALSIFIED, 0.0, evidence.evidence_id, self.authority)
            new_u = q.uncertainty * 0.8
            live = [s for s in state.hypotheses_for(q.question_id) if s.status in (UNVERIFIED, SUPPORTED, CERTIFIED)]
            if not live and q.status == Q_OPEN:
                state.set_question_status(q.question_id, Q_EXHAUSTED, self.authority)
        return state.lower_uncertainty(q.question_id, new_u, self.authority)

    def verify_module(self, state, path, question_id="", hypothesis_id=""):
        return Evidence(evidence_id=state.new_evidence_id(), kind="module_check", hypothesis_id=hypothesis_id,
                        question_id=question_id, command=f"verify_module {path}", exit_code=0,
                        prediction={"expect_exit_code": 0}, matched_prediction=True,
                        detail="module check passed: scripted")

    def apply_synthesis(self, state, q, h, evidence):
        state.record_evidence(evidence, self.authority)
        h.patch_applied = True
        state.set_question_status(q.question_id, Q_RESOLVED, self.authority)
        return state.lower_uncertainty(q.question_id, 0.02, self.authority)


class ScriptedSystem1:
    def __init__(self, rounds):
        self.rounds = [list(r) for r in rounds]

    def propose(self, question, state, k):
        return self.rounds.pop(0) if self.rounds else []

    def synthesize(self, question, hypothesis, state):
        return {"written": True, "path": question.module or "mypkg/calc.py", "summary": "patched"}


def seeded_state(name="firm", module="mypkg/calc.py"):
    state = EpistemicState(name)
    state.add_question("Why does test_add fail?", module=module, uncertainty=1.0,
                       source_failure_key="calc:test_add",
                       source_failure="[calc] FAIL: test_add -> AssertionError: 0 != 4")
    return state


def run_loop(state, rounds, truth, seed=7, prior_head=None, w=0.0, value_head=None, value_fn=None, **policy_kw):
    kw = dict(enabled=True, search_budget_moves=12, min_hypotheses_before_synthesis=2, max_stagnant_moves=6)
    kw.update(policy_kw)
    sys1 = ScriptedSystem1(rounds)
    loop = EpistemicSearchLoop(state, ScriptedGatekeeper(truth), value_fn or EpistemicValueFunction(0.6),
                               EpistemicPolicyGene(**kw), sys1.propose, sys1.synthesize, rng_seed=seed,
                               logger=lambda s: None, prior_head=prior_head, prior_head_weight=w, value_head=value_head)
    return loop.run()


def inverting_head():
    """p_head is high exactly where the stated prior is low."""
    return HypothesisPriorHead(weights={"stated_prior": -12.0}, bias=6.0)


# ---------------------------------------------------------------------------
# Features
# ---------------------------------------------------------------------------

class HypothesisFeatureTests(unittest.TestCase):

    def setUp(self):
        self.q = Question("q1", "Why?", module="hae/evaluation/harness.py", uncertainty=0.7,
                          source_failure_key="execution_harness:test_x",
                          source_failure="[execution_harness] ERROR: test_x (t.T.test_x) -> AttributeError: no 'evaluated'")
        self.q.hypothesis_rounds = 2
        self.h1 = Hypothesis("h1", "q1", "claim one " * 30, prior=0.8,
                             probe_code="from hae.evaluation import harness\nprint(harness)\n",
                             prediction={"expect_exit_code": 0, "expect_stdout_contains": "x"})
        self.h2 = Hypothesis("h2", "q1", "claim two", prior=0.2,
                             probe_code="try:\n    from hae.evaluation.harness import GateResult\nexcept ImportError:\n    pass\n" + "x = 1\n" * 50,
                             prediction={"expect_exit_code": 0})
        self.h3 = Hypothesis("h3", "q1", "claim three", prior=0.2, probe_code="import os\n", prediction={})
        self.sf = epistemic_features(EpistemicState("x"))

    def test_names_ranges_and_determinism(self):
        sibs = [self.h1, self.h2, self.h3]
        for h in sibs:
            f = hypothesis_features(self.q, h, sibs, self.sf)
            self.assertEqual(tuple(f), HYPOTHESIS_FEATURE_NAMES)
            self.assertTrue(all(0.0 <= v <= 1.0 for v in f.values()), f)
            self.assertEqual(f, hypothesis_features(self.q, h, list(reversed(sibs)), dict(self.sf)))
        self.assertEqual(len(HYPOTHESIS_FEATURE_NAMES), 32)
        self.assertEqual([n for n in HYPOTHESIS_FEATURE_NAMES if n.startswith("s_")], [f"s_{n}" for n in FEATURE_NAMES])

    def test_rank_share_and_lowest(self):
        sibs = [self.h1, self.h2, self.h3]
        f1, f2, f3 = (hypothesis_features(self.q, h, sibs, self.sf) for h in sibs)
        self.assertEqual(f1["prior_rank"], 1.0)
        self.assertEqual(f2["prior_rank"], 0.25)          # tie with h3 shares the rank
        self.assertEqual(f3["prior_rank"], 0.25)
        self.assertEqual((f1["is_lowest_prior"], f2["is_lowest_prior"], f3["is_lowest_prior"]), (0.0, 1.0, 1.0))
        self.assertAlmostEqual(f1["sibling_share"], 1 / 3, places=5)
        alone = hypothesis_features(self.q, self.h1, [self.h1], self.sf)
        self.assertEqual((alone["prior_rank"], alone["sibling_share"], alone["is_lowest_prior"]), (0.5, 1.0, 0.0))
        # The hypothesis need not be in `siblings`; it is counted once either way.
        self.assertEqual(hypothesis_features(self.q, self.h1, [self.h2, self.h3], self.sf)["prior_rank"], 1.0)

    def test_probe_prediction_question_and_onehots(self):
        sibs = [self.h1, self.h2, self.h3]
        f1, f2, f3 = (hypothesis_features(self.q, h, sibs, self.sf) for h in sibs)
        self.assertEqual((f1["probe_imports_module"], f2["probe_imports_module"], f3["probe_imports_module"]), (1.0, 1.0, 0.0))
        self.assertEqual((f1["probe_guarded_import"], f2["probe_guarded_import"]), (0.0, 1.0))
        self.assertEqual(f2["probe_lines"], 1.0)                                   # 54 lines, capped
        self.assertEqual((f1["prediction_exit_code"], f1["prediction_marker"]), (1.0, 1.0))
        self.assertEqual((f2["prediction_exit_code"], f2["prediction_marker"]), (1.0, 0.0))
        self.assertEqual((f3["prediction_exit_code"], f3["prediction_marker"]), (0.0, 0.0))
        self.assertEqual(f1["claim_length"], 1.0)
        self.assertAlmostEqual(f1["question_uncertainty"], 0.7)
        self.assertAlmostEqual(f1["question_rounds"], 2 / 3, places=5)
        self.assertEqual(f1["oracle_seeded"], 1.0)
        self.assertEqual((f1["fail_name"], f1["fail_import"], f1["fail_assertion"], f1["fail_other"]), (1.0, 0.0, 0.0, 0.0))
        self.assertEqual((f1["mod_harness"], f1["mod_verification_loop"], f1["mod_artifacts"], f1["mod_morphogenesis"]), (1.0, 0.0, 0.0, 0.0))
        self.assertEqual(f1["s_open_without_options"], self.sf["open_without_options"])

    def test_failure_classes_and_import_detection(self):
        self.assertEqual(failure_class("[t] ERROR: suite failed to import: ModuleNotFoundError: No module named 'x'"), "fail_import")
        self.assertEqual(failure_class("[t] FAIL: test_a (x) -> AssertionError: 1 != 2"), "fail_assertion")
        self.assertEqual(failure_class("[t] ERROR: test_a (x) -> UnboundLocalError: y"), "fail_name")
        self.assertEqual(failure_class("[t] ERROR: test_a (x) -> KeyError: 'k'"), "fail_other")
        self.assertEqual(failure_class(""), "")
        self.assertTrue(probe_imports_module("import hae.evaluation.harness as hz\n", "hae/evaluation/harness.py"))
        self.assertTrue(probe_imports_module("from hae.evaluation.harness import (\n    A,\n    B)\n", "hae/evaluation/harness.py"))
        self.assertFalse(probe_imports_module("from hae.evaluation.harnessx import A\n", "hae/evaluation/harness.py"))
        self.assertFalse(probe_imports_module("import hae\n", ""))


# ---------------------------------------------------------------------------
# Heads
# ---------------------------------------------------------------------------

def toy_prior_samples(n=40):
    """Separable: high prior_rank + short probe survive; the rest do not."""
    pos = [({"prior_rank": 0.9, "probe_lines": 0.1, "oracle_seeded": 1.0}, 1.0)] * n
    neg = [({"prior_rank": 0.1, "probe_lines": 0.9, "oracle_seeded": 1.0}, 0.0)] * n
    return pos, neg


class HeadTests(unittest.TestCase):

    def test_prior_head_fits_separable_toy_and_round_trips(self):
        pos, neg = toy_prior_samples()
        head = HypothesisPriorHead().fit(pos + neg, epochs=200, lr=0.5)
        acc = sum(head.predict(f) >= 0.5 for f, _ in pos) + sum(head.predict(f) < 0.5 for f, _ in neg)
        self.assertGreater(acc / float(len(pos) + len(neg)), 0.9)
        self.assertEqual(head.trained_on, 80)
        head.metadata = {"kind": "prior", "rows": 80}
        clone = HypothesisPriorHead.from_json(head.to_json())
        self.assertEqual(clone.to_dict(), head.to_dict())
        self.assertEqual(clone.metadata, {"kind": "prior", "rows": 80})
        self.assertEqual(clone.to_dict()["features"], list(HYPOTHESIS_FEATURE_NAMES))
        self.assertAlmostEqual(clone.predict(pos[0][0]), head.predict(pos[0][0]), places=12)

    def test_newton_reaches_a_better_or_equal_optimum_than_gd(self):
        pos, neg = toy_prior_samples()
        gd = HypothesisPriorHead().fit(pos + neg, epochs=200, lr=0.5, l2=1e-2)
        nt = HypothesisPriorHead().fit_newton(pos + neg, l2=1e-2)
        data = nt._vectorise(pos + neg)
        self.assertLessEqual(nt._objective(data, 1e-2), gd._objective(data, 1e-2) + 1e-9)
        self.assertGreater(nt.predict(pos[0][0]), 0.9)
        self.assertLess(nt.predict(neg[0][0]), 0.1)

    def test_learned_value_head_json_format_and_fit_unchanged(self):
        pos = [({"resolved_fraction": 0.9, "certified_ratio": 1.0}, 1.0)] * 20
        neg = [({"resolved_fraction": 0.1, "certified_ratio": 0.0}, 0.0)] * 20
        head = LearnedValueHead().fit(pos + neg, epochs=50, lr=0.5)
        # Numbers produced by the V6 implementation of `fit` on this very input.
        self.assertAlmostEqual(head.bias, -1.6647767667740307, places=12)
        self.assertAlmostEqual(head.weights["resolved_fraction"], 1.7114982490238155, places=12)
        self.assertAlmostEqual(head.weights["certified_ratio"], 2.3447343233080917, places=12)
        d = head.to_dict()
        self.assertEqual(set(d), {"weights", "bias", "trained_on", "features"})   # no `metadata` key unless set
        self.assertEqual(d["features"], list(FEATURE_NAMES))
        old_json = json.dumps({"weights": {"resolved_fraction": 1.5}, "bias": -0.5, "trained_on": 7, "features": list(FEATURE_NAMES)})
        loaded = LearnedValueHead.from_json(old_json)
        self.assertEqual((loaded.weights["resolved_fraction"], loaded.bias, loaded.trained_on, loaded.metadata), (1.5, -0.5, 7, {}))
        self.assertEqual(json.loads(loaded.to_json()), json.loads(old_json) | {"weights": loaded.weights})

    def test_unknown_weights_are_ignored(self):
        head = HypothesisPriorHead(weights={"not_a_feature": 3.0, "stated_prior": 1.0})
        self.assertNotIn("not_a_feature", head.weights)
        self.assertEqual(head.weights["stated_prior"], 1.0)


# ---------------------------------------------------------------------------
# Loading from `extra`
# ---------------------------------------------------------------------------

class LoadPolicyHeadsTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hae_heads_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.prior_path = os.path.join(self.tmp, "p.json")
        with open(self.prior_path, "w") as f:
            f.write(inverting_head().to_json())
        self.value_path = os.path.join(self.tmp, "v.json")
        with open(self.value_path, "w") as f:
            f.write(LearnedValueHead(weights={"certified_ratio": 2.0}, bias=-1.0).to_json())

    def test_resolution_rules(self):
        self.assertEqual(resolve_head_path(self.prior_path), self.prior_path)
        self.assertEqual(resolve_head_path("v7_prior_head.json"), os.path.join(HEADS_DIR, "v7_prior_head.json"))
        self.assertTrue(resolve_head_path("hae/epistemic/heads/v7_prior_head.json").endswith(
            os.path.join("hae", "epistemic", "heads", "v7_prior_head.json")))
        self.assertEqual(resolve_head_path(""), "")

    def test_loads_logs_and_defaults(self):
        logs = []
        heads = load_policy_heads({"prior_head_path": self.prior_path, "value_head_path": self.value_path,
                                   "prior_head_weight": "0.5", "value_head_live": "true"}, logger=logs.append)
        self.assertIsInstance(heads.prior_head, HypothesisPriorHead)
        self.assertIsInstance(heads.value_head, LearnedValueHead)
        self.assertEqual((heads.prior_head_weight, heads.value_head_live, heads.errors), (0.5, True, {}))
        self.assertTrue(any("prior head loaded" in m for m in logs))
        self.assertEqual(heads.prior_head.metadata["path"], self.prior_path)
        default = load_policy_heads({})
        self.assertEqual((default.prior_head, default.value_head, default.prior_head_weight, default.value_head_live),
                         (None, None, 0.0, False))
        self.assertEqual(load_policy_heads(None).errors, {})
        self.assertEqual(load_policy_heads({"prior_head_weight": 7}).prior_head_weight, 1.0)
        # A genome that nested the dict instead of flattening it still works.
        nested = load_policy_heads({"extra": {"prior_head_path": self.prior_path, "prior_head_weight": 0.25}})
        self.assertIsInstance(nested.prior_head, HypothesisPriorHead)
        self.assertEqual(nested.prior_head_weight, 0.25)

    def test_bad_path_bad_json_and_wrong_kind_never_raise(self):
        bad_json = os.path.join(self.tmp, "bad.json")
        with open(bad_json, "w") as f:
            f.write("{not json")
        logs = []
        heads = load_policy_heads({"prior_head_path": os.path.join(self.tmp, "missing.json"),
                                   "value_head_path": bad_json, "prior_head_weight": "x", "value_head_live": True},
                                  logger=logs.append)
        self.assertIsNone(heads.prior_head)
        self.assertIsNone(heads.value_head)
        self.assertFalse(heads.value_head_live)              # nothing to go live with
        self.assertEqual(heads.prior_head_weight, 0.0)
        self.assertEqual(set(heads.errors), {"prior_head_path", "value_head_path", "prior_head_weight"})
        self.assertEqual(sum("NOT loaded" in m for m in logs), 2)
        # A value head file offered as a prior head is refused: the feature sets differ.
        heads = load_policy_heads({"prior_head_path": self.value_path}, logger=logs.append)
        self.assertIsNone(heads.prior_head)
        self.assertIn("feature sets differ", heads.errors["prior_head_path"])
        with self.assertRaises(ValueError):
            load_head(self.value_path, "prior")


# ---------------------------------------------------------------------------
# Loop wiring
# ---------------------------------------------------------------------------

class LoopShadowModeTests(unittest.TestCase):

    ROUNDS = [[proposal("caller passes bad args", 0.8), proposal("stale bytecode", 0.8),
               proposal("operator is minus", 0.2), proposal("wrong module imported", 0.2)]]
    TRUTH = {"operator is minus": True}

    @staticmethod
    def trace(res):
        return [(m.move_type, m.hypothesis_id, m.forced_low_prior, round(m.delta_u, 6), m.value_before) for m in res.trajectory]

    def test_weight_zero_reproduces_the_headless_move_sequence_exactly(self):
        plain = run_loop(seeded_state(), self.ROUNDS, self.TRUTH, seed=7, low_prior_quota=0.34)
        shadow = run_loop(seeded_state(), self.ROUNDS, self.TRUTH, seed=7, low_prior_quota=0.34,
                          prior_head=inverting_head(), w=0.0)
        self.assertEqual(self.trace(plain), self.trace(shadow))
        self.assertGreaterEqual(sum(1 for m in plain.trajectory if m.move_type == MOVE_RUN_EXPERIMENT), 3)
        self.assertEqual(plain.stop_reason, shadow.stop_reason)
        self.assertEqual([m.extra for m in plain.trajectory], [{}] * len(plain.trajectory))
        self.assertEqual((plain.stats["prior_head_loaded"], plain.stats["value_head_loaded"], plain.stats["prior_head_steered"]),
                         (False, False, 0))
        self.assertEqual((shadow.stats["prior_head_loaded"], shadow.stats["prior_head_steered"]), (True, 0))
        # Ties among equal priors are broken by the same rng draws in both runs.
        experiments = [m for m in shadow.trajectory if m.move_type == MOVE_RUN_EXPERIMENT]
        for m in experiments:
            self.assertEqual(set(m.extra), {"head_p", "head_rank", "stated_rank", "prior_head_weight"})
            self.assertEqual(m.extra["prior_head_weight"], 0.0)
            self.assertTrue(0.0 <= m.extra["head_p"] <= 1.0)
        # The inverting head mirrors the stated ranking exactly (its only input
        # is the stated prior), ties included. branching_k=3 accepts three of
        # the four proposals: two at 0.8, one at 0.2.
        hyps = shadow.final_state.hypotheses
        self.assertEqual(sorted(h.prior for h in hyps.values()), [0.2, 0.8, 0.8])
        first, second, third = experiments[:3]
        self.assertEqual(hyps[first.hypothesis_id].prior, 0.8)
        self.assertEqual((first.extra["stated_rank"], first.extra["head_rank"]), (0.75, 0.25))   # tied with the other 0.8, above the 0.2
        self.assertEqual(hyps[second.hypothesis_id].prior, 0.8)
        self.assertEqual((second.extra["stated_rank"], second.extra["head_rank"]), (1.0, 0.0))   # only the 0.2 is left untested
        self.assertEqual((third.extra["stated_rank"], third.extra["head_rank"]), (0.5, 0.5))     # alone
        for m in experiments:
            self.assertAlmostEqual(m.extra["stated_rank"] + m.extra["head_rank"], 1.0)
        for m in shadow.trajectory:
            if m.move_type != MOVE_RUN_EXPERIMENT:
                self.assertEqual(m.extra, {})

    def test_weight_one_with_inverting_head_picks_the_other_hypothesis_first(self):
        rounds = [[proposal("caller passes bad args", 0.8), proposal("operator is minus", 0.2)]]
        plain = run_loop(seeded_state(), rounds, self.TRUTH, seed=3, low_prior_quota=0.0)
        steered = run_loop(seeded_state(), rounds, self.TRUTH, seed=3, low_prior_quota=0.0,
                           prior_head=inverting_head(), w=1.0)
        first_plain = next(m for m in plain.trajectory if m.move_type == MOVE_RUN_EXPERIMENT)
        first_steered = next(m for m in steered.trajectory if m.move_type == MOVE_RUN_EXPERIMENT)
        self.assertEqual(first_plain.hypothesis_id, "h1")      # stated prior 0.8 first, as in V6
        self.assertEqual(first_steered.hypothesis_id, "h2")    # the head inverted it
        self.assertEqual(steered.stats["prior_head_steered"], 1)
        self.assertEqual(first_steered.extra["prior_head_weight"], 1.0)
        self.assertEqual((first_steered.extra["stated_rank"], first_steered.extra["head_rank"]), (0.0, 1.0))
        self.assertGreater(first_steered.extra["head_p"], 0.9)
        # Beliefs still come from evidence only: both runs end with the same verdicts.
        for res in (plain, steered):
            statuses = {h.claim: h.status for h in res.final_state.hypotheses.values()}
            self.assertEqual(statuses, {"caller passes bad args": FALSIFIED, "operator is minus": SUPPORTED})

    def test_shadow_value_head_is_recorded_but_not_used_unless_live(self):
        vhead = LearnedValueHead(weights={"untested_ratio": 3.0}, bias=-2.0)
        plain = run_loop(seeded_state(), self.ROUNDS, self.TRUTH, seed=7)
        shadow = run_loop(seeded_state(), self.ROUNDS, self.TRUTH, seed=7, value_head=vhead)
        self.assertEqual(self.trace(plain), self.trace(shadow))      # value_before identical
        self.assertTrue(shadow.stats["value_head_loaded"])
        for m in shadow.trajectory:
            self.assertIn("head_v", m.extra)
            self.assertAlmostEqual(m.extra["head_v"], vhead.predict(m.features), places=6)
        live = run_loop(seeded_state(), self.ROUNDS, self.TRUTH, seed=7, value_head=vhead,
                        value_fn=EpistemicValueFunction(0.6, head=vhead))
        self.assertEqual([m.hypothesis_id for m in live.trajectory], [m.hypothesis_id for m in plain.trajectory])
        self.assertNotEqual([m.value_before for m in live.trajectory], [m.value_before for m in plain.trajectory])

    def test_move_record_extra_round_trips_and_old_dicts_load(self):
        m = MoveRecord(move_index=1, move_type=MOVE_RUN_EXPERIMENT, extra={"head_p": 0.3})
        self.assertEqual(MoveRecord.from_dict(m.to_dict()).extra, {"head_p": 0.3})
        old = {k: v for k, v in m.to_dict().items() if k != "extra"}
        self.assertEqual(MoveRecord.from_dict(old).extra, {})
        self.assertIn("extra", SearchResult([m], EpistemicState("x"), 1, 0.0, "x").to_dict()["trajectory"][0])


# ---------------------------------------------------------------------------
# Company wiring (real runner, mocked LLM)
# ---------------------------------------------------------------------------

BUGGY = "def add(a, b):\n    return a - b\n"
FIXED = "def add(a, b):\n    return a + b\n"
OBJECTIVE = ("Implement the module `mypkg/calc.py` exposing add(a, b).\n\nITERATION 2/3 — GROUND-TRUTH VERIFIER FEEDBACK\n"
             "Failing ground-truth checks:\n  - FAIL: test_add (tests.test_calc.TestCalc) -> AssertionError: -1 != 5\n")
FAILURES = ["FAIL: test_add (tests.test_calc.TestCalc) -> AssertionError: -1 != 5"]


def fake_llm(prompt, model_name, temperature, system_instruction, usage_sink=None, response_format=None,
             max_tokens=None, **_):
    if usage_sink is not None:
        usage_sink.update({"measured": True, "prompt_tokens": 1000, "output_tokens": 200})
    if "PROPOSE" in prompt and "HYPOTHESES" in prompt:
        qid = re.search(r'Set `question_id` to "([^"]+)"', prompt).group(1)
        return json.dumps({"packet": "HYPOTHESIS_SET", "question_id": qid, "hypotheses": [
            {"claim": "add multiplies its operands", "mechanism": "star operator", "prior": 0.7,
             "probe_code": "from mypkg.calc import add\nprint('PRODUCT', add(2, 3))\n",
             "prediction": {"expect_exit_code": 0, "expect_stdout_contains": "PRODUCT 6"}},
            {"claim": "add subtracts its operands", "mechanism": "minus operator", "prior": 0.3,
             "probe_code": "from mypkg.calc import add\nprint('RESULT', add(2, 3))\n",
             "prediction": {"expect_exit_code": 0, "expect_stdout_contains": "RESULT -1"}}]})
    if "REPAIR_PLAN packet" in prompt:
        return "I would rather rewrite the module."
    if "SYNTHESIZE a repair" in prompt:
        return "Action: write_file\nPath: mypkg/calc.py\n```python\n" + FIXED + "```\n"
    return json.dumps({"packet": "SPECIALIST", "summary": "n/a"})


class CompanyHeadLoadingTest(unittest.TestCase):

    def test_runner_loads_prior_head_from_extra_and_survives_a_bad_value_head_path(self):
        from hae.epistemic.gatekeeper import EvidenceGatekeeper
        from hae.evaluation.verification_loop import VerificationLoop
        from hae.genome.schema import AgentGenome, CompanyGenome, DepartmentGenome
        from hae.runtime.company import SUITE_TAG_TO_MODULE, HierarchicalCompanyRunner
        from hae.runtime.workspace import AgentWorkspace

        tmp = tempfile.mkdtemp(prefix="hae_company_heads_")
        self.addCleanup(shutil.rmtree, tmp, True)
        prior_path = os.path.join(tmp, "prior.json")
        with open(prior_path, "w") as f:
            f.write(inverting_head().to_json())
        engineer = AgentGenome(role="Lead Implementation Engineer", goal="Fix code", backstory="Systems",
                               temperature=0.2, model_tier="worker", tools_enabled=True)
        tester = AgentGenome(role="QA Analyst", goal="Verify", backstory="QA", temperature=0.2, model_tier="worker")
        genome = CompanyGenome(
            company_id="heads_firm", generation=16,
            ceo=AgentGenome(role="CEO", goal="Lead", backstory="Exec", temperature=0.4, model_tier="executive"),
            departments=[DepartmentGenome(dept_id="dept_systems_eng", name="Systems Engineering", mandate="Build",
                                          manager=engineer, agents=[engineer]),
                         DepartmentGenome(dept_id="dept_qa_redteam", name="QA", mandate="Verify",
                                          manager=tester, agents=[tester])],
            # The on-disk form: head keys sit directly inside `epistemic_policy`;
            # the schema collects unknown keys into `EpistemicPolicyGene.extra`.
            epistemic_policy={"enabled": True, "search_budget_moves": 10, "branching_k": 2, "low_prior_quota": 0.0,
                              "min_hypotheses_before_synthesis": 1, "experiment_timeout_s": 20,
                              "prior_head_path": prior_path, "prior_head_weight": 0.0,
                              "value_head_path": os.path.join(tmp, "does_not_exist.json"),
                              "value_head_live": True})
        runner = HierarchicalCompanyRunner(genome)
        self.assertEqual(runner.epistemic_policy.extra["prior_head_path"], prior_path)
        self.assertEqual(json.loads(genome.to_json())["epistemic_policy"]["prior_head_weight"], 0.0)
        runner.workspace = AgentWorkspace("heads_firm", base_dir=tmp)
        runner.verification_loop = VerificationLoop(runner.workspace)
        runner.workspace.write_file("mypkg/__init__.py", "")
        runner.workspace.write_file("mypkg/calc.py", BUGGY)
        runner._make_gatekeeper = lambda policy=None: EvidenceGatekeeper(
            runner.workspace, timeout_s=20, max_probe_lines=40, graded_modules=runner._required_modules,
            module_for_tag=SUITE_TAG_TO_MODULE, isolate=False, stage_reference=False)
        with redirect_stdout(io.StringIO()) as out, mock.patch("hae.runtime.company.call_llm", fake_llm):
            result = runner.run_epistemic_search(OBJECTIVE, FAILURES, iteration=2, max_iterations=3)
        log = out.getvalue()
        self.assertIn("prior head loaded from", log)
        self.assertIn("value head NOT loaded", log)
        stats = result["epistemic_search"]["stats"]
        self.assertEqual((stats["prior_head_loaded"], stats["value_head_loaded"], stats["prior_head_steered"]),
                         (True, False, 0))
        experiments = [m for m in result["epistemic_search"]["trajectory"] if m["move_type"] == MOVE_RUN_EXPERIMENT]
        self.assertGreaterEqual(len(experiments), 2)
        self.assertEqual(set(experiments[0]["extra"]), {"head_p", "head_rank", "stated_rank", "prior_head_weight"})
        self.assertNotIn("head_v", experiments[0]["extra"])
        # Weight 0: the V6 order (stated prior 0.7 first) and the V6 verdicts.
        self.assertEqual(experiments[0]["hypothesis_id"], "h1")
        by_claim = {h.claim: h.status for h in runner.epistemic_state.hypotheses.values()}
        self.assertEqual(by_claim, {"add multiplies its operands": FALSIFIED, "add subtracts its operands": SUPPORTED})
        self.assertEqual(result["epistemic_search"]["synthesized_paths"], ["mypkg/calc.py"])


# ---------------------------------------------------------------------------
# Extractor + trainer on synthetic trees
# ---------------------------------------------------------------------------

def build_tree(company, certify, prior_head=None):
    """One firm, one iteration: four hypotheses on q1, two of them tested; q1 is
    certified between iterations when `certify`, else left open. Built with the
    real ledger and the loop's own `_snapshot`/`_record`."""
    state = seeded_state(company)
    q1 = next(iter(state.questions.values()))
    auth = GatekeeperAuthority("test")
    loop = EpistemicSearchLoop(state, None, EpistemicValueFunction(0.6), EpistemicPolicyGene(enabled=True),
                               None, None, logger=lambda s: None, prior_head=prior_head)
    hyps = {}

    def move(move_type, mutate, hid="", delta=0.0, forced=False, extra=None):
        feats, v0, h0 = loop._snapshot()
        mutate()
        return loop._record(move_type, q1.question_id, hid, delta_u=delta, forced=forced,
                            features_before=feats, value_before=v0, hash_before=h0, extra=extra)

    def evidence(hid, matched):
        ev = Evidence(evidence_id=state.new_evidence_id(), kind="probe", hypothesis_id=hid,
                      question_id=q1.question_id, exit_code=0 if matched else 1,
                      prediction={"expect_exit_code": 0}, matched_prediction=matched)
        state.record_evidence(ev, auth)
        return ev

    def propose():
        for name, prior in (("wrong", 0.9), ("right", 0.3), ("other", 0.5), ("idle", 0.1)):
            hyps[name] = state.add_hypothesis(q1.question_id, f"the {name} mechanism in calc {company}", prior=prior,
                                              probe_code=PROBE, prediction={"expect_exit_code": 0})

    def falsify():
        ev = evidence(hyps["wrong"].hypothesis_id, False)
        state.set_hypothesis_verdict(hyps["wrong"].hypothesis_id, FALSIFIED, 0.0, ev.evidence_id, auth)
        state.lower_uncertainty(q1.question_id, 0.8, auth)

    def support():
        ev = evidence(hyps["right"].hypothesis_id, certify)
        status = SUPPORTED if certify else FALSIFIED
        state.set_hypothesis_verdict(hyps["right"].hypothesis_id, status, 0.8 if certify else 0.0, ev.evidence_id, auth)
        state.lower_uncertainty(q1.question_id, 0.3 if certify else 0.7, auth)

    move(MOVE_PROPOSE_HYPOTHESIS, propose)
    move(MOVE_RUN_EXPERIMENT, falsify, hyps["wrong"].hypothesis_id, delta=0.2, extra={"head_p": 0.4})
    move(MOVE_RUN_EXPERIMENT, support, hyps["right"].hypothesis_id, delta=0.5 if certify else 0.1, forced=True)
    if certify:
        oracle = Evidence(evidence_id=state.new_evidence_id(), kind="oracle", prediction={"expect_exit_code": 0},
                          matched_prediction=True)
        state.record_evidence(oracle, auth)
        state.certify(q1.question_id, hyps["right"].hypothesis_id, "test_add passes", [oracle.evidence_id], "oracle", auth)
    search = SearchResult(trajectory=list(loop.trajectory), final_state=state, moves_used=len(loop.trajectory),
                          delta_u_total=sum(m.delta_u for m in loop.trajectory), stop_reason="budget_moves").to_dict()
    search["iteration"] = 2
    return {"company_id": company, "generation": 16, "ledger": state.to_dict(), "searches": [search],
            "audit": None, "policy": None}


def write_tree(root, tree):
    d = os.path.join(root, "outputs", tree["company_id"], "generation_16")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, f"{tree['company_id']}_epistemic_tree.json"), "w") as f:
        json.dump(tree, f, indent=1, default=str)


class ExtractorExtraColumnsTest(unittest.TestCase):

    def test_x_columns_only_when_some_move_carries_extra(self):
        root = tempfile.mkdtemp(prefix="hae_vt_extra_")
        self.addCleanup(shutil.rmtree, root, True)
        write_tree(root, build_tree("firm_x", certify=True))
        csv_path = os.path.join(root, "out.csv")
        with redirect_stdout(io.StringIO()):
            summary = evt.main(["--root", root, "--out", csv_path])
        with open(csv_path, newline="") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(summary["columns"][-1], "x_head_p")
        self.assertEqual([r["x_head_p"] for r in rows], ["", "0.4", ""])
        # A tree whose moves have no `extra` (pre-V7) yields the pre-V7 column set.
        tree = build_tree("firm_old", certify=True)
        for m in tree["ledger"]["move_log"]:
            m.pop("extra", None)
        for s in tree["searches"]:
            for m in s["trajectory"]:
                m.pop("extra", None)
        root2 = tempfile.mkdtemp(prefix="hae_vt_old_")
        self.addCleanup(shutil.rmtree, root2, True)
        write_tree(root2, tree)
        with redirect_stdout(io.StringIO()):
            summary2 = evt.main(["--root", root2, "--out", os.path.join(root2, "out.csv")])
        self.assertEqual(summary2["columns"], list(evt.HEAD_COLUMNS) + [f"f_{n}" for n in FEATURE_NAMES] + list(evt.TAIL_COLUMNS))


class TrainerEndToEndTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp(prefix="hae_train_")
        for i in range(6):
            write_tree(cls.root, build_tree(f"firm_{i}", certify=(i % 2 == 0)))
        cls.out_dir = os.path.join(cls.root, "heads")
        cls.report = os.path.join(cls.root, "report", "README.md")
        with redirect_stdout(io.StringIO()) as out:
            cls.result = tvh.main(["--roots", cls.root, "--out-dir", cls.out_dir, "--report", cls.report,
                                   "--seed", "0", "--folds", "3"])
        cls.stdout = out.getvalue()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def test_rows_labels_and_replay(self):
        book, v, p = self.result["book"], self.result["value"], self.result["prior"]
        self.assertEqual((book["moves"], v["rows"], v["positives"]), (18, 18, 9))     # 3 moves x 6 firms; 3 firms certified
        self.assertEqual((p["rows"], p["positives"]), (12, 3))                         # 2 experiments x 6; `right` supported in 3
        self.assertEqual(book["prior_excluded_by_status"], {})
        self.assertEqual(book["prior_sibling_fallback_rows"], 0)
        self.assertEqual((book["replay_questions_checked"], book["replay_questions_matched"]), (3, 3))
        self.assertEqual((v["folds"], p["folds"], v["groups"]), (3, 3, 6))
        self.assertIn(v["chosen_l2"], (1e-3, 1e-2, 1e-1))

    def test_heads_are_written_loadable_and_carry_metadata(self):
        vpath, ppath = self.result["paths"]["value_head"], self.result["paths"]["prior_head"]
        vhead, phead = load_head(vpath, "value"), load_head(ppath, "prior")
        self.assertEqual((vhead.trained_on, phead.trained_on), (18, 12))
        for head, kind in ((vhead, "value"), (phead, "prior")):
            meta = head.metadata
            self.assertEqual(meta["kind"], kind)
            for key in ("rows", "positives", "folds", "oof", "roots", "git_sha", "trained_at", "feature_names", "l2", "verdict"):
                self.assertIn(key, meta, key)
            self.assertEqual(set(meta["oof"]) >= {"brier", "log_loss", "auc"}, True)
        self.assertEqual(phead.metadata["feature_names"], list(HYPOTHESIS_FEATURE_NAMES))
        # The prior rows separate perfectly on `prior_rank` in this toy, so the refit head must see it.
        self.assertGreater(phead.predict({"prior_rank": 0.0, "is_lowest_prior": 0.0}), 0.0)
        with open(vpath) as f:
            self.assertEqual(set(json.load(f)), {"weights", "bias", "trained_on", "features", "metadata"})

    def test_report_and_metrics(self):
        with open(self.report) as f:
            text = f.read()
        for needle in ("## 1. Data provenance", "## 4. State-value head", "## 5. Hypothesis-prior head",
                       "Out-of-fold metrics", "Out-of-fold calibration", "Verdict", "rank-only baseline",
                       "`heuristic_value`", "the proposer's stated prior"):
            self.assertIn(needle, text, needle)
        with open(os.path.join(os.path.dirname(self.report), "metrics.json")) as f:
            metrics = json.load(f)
        self.assertEqual(metrics["trees"], 6)
        self.assertIn("baselines", metrics["prior"])
        self.assertIn("prior_rank_only_logistic", metrics["prior"]["baselines"])
        self.assertIn("value_rows=18", self.stdout)

    def test_metric_helpers(self):
        self.assertEqual(tvh.auc([0.1, 0.4, 0.35, 0.8], [0, 0, 1, 1]), 0.75)
        self.assertEqual(tvh.auc([0.5, 0.5, 0.5], [0, 1, 0]), 0.5)
        self.assertIsNone(tvh.auc([0.2, 0.3], [1, 1]))
        self.assertAlmostEqual(tvh.brier([1.0, 0.0], [1, 0]), 0.0)
        self.assertAlmostEqual(tvh.log_loss([0.5, 0.5], [1, 0]), -2 * 0.5 * __import__("math").log(0.5))
        folds = tvh.grouped_folds([tvh.Row(g, "t", i, {}, 0) for i, g in enumerate("aabbbccd")], 3, 0)
        self.assertEqual(set(folds.values()), {0, 1, 2})
        self.assertEqual(len(folds), 4)


if __name__ == "__main__":
    unittest.main()
