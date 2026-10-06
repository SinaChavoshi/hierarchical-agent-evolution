"""The telemetry extractor must not drift from the ledger schema it flattens.

The synthetic tree is built with the real `EpistemicState`, `Evidence`,
`GatekeeperAuthority`, the search loop's own `_snapshot`/`_record` (the code
that writes every production `MoveRecord`) and `SearchResult.to_dict`, then
wrapped exactly as `hae/orchestration/worker.py` writes the sidecar. If a
ledger field is renamed, this test breaks here instead of the extractor
silently emitting empty columns.
"""

import csv
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, ".")

from hae.epistemic.ledger import (  # noqa: E402
    CERTIFIED, FALSIFIED, Q_CERTIFIED, Q_OPEN, Q_RESOLVED, SUPPORTED, UNTESTABLE, UNVERIFIED,
    EpistemicState, Evidence, GatekeeperAuthority,
)
from hae.epistemic.mcts import EpistemicSearchLoop, SearchResult  # noqa: E402
from hae.epistemic.moves import (  # noqa: E402
    MOVE_ASK_QUESTION, MOVE_PROPOSE_HYPOTHESIS, MOVE_RUN_EXPERIMENT, MOVE_SYNTHESIZE,
)
from hae.epistemic.value import FEATURE_NAMES, EpistemicValueFunction, brier_score  # noqa: E402
from hae.genome.schema import EpistemicPolicyGene  # noqa: E402

_SPEC = importlib.util.spec_from_file_location(
    "extract_value_telemetry", os.path.join("scripts", "extract_value_telemetry.py"))
evt = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(evt)


def build_tree():
    """Two iterations on one ledger: a falsified, a certified, an untestable and an idle hypothesis."""
    state = EpistemicState("firm_a")
    auth = GatekeeperAuthority("test")
    loop = EpistemicSearchLoop(state, None, EpistemicValueFunction(0.6), EpistemicPolicyGene(enabled=True),
                               None, None, logger=lambda s: None)
    q1 = state.add_question("Why does test_add fail?", module="mypkg/calc.py", source_failure_key="calc:test_add")
    q2 = state.add_question("Why does test_mul fail?", module="mypkg/mul.py", source_failure_key="calc:test_mul")
    hyps = {}

    def move(move_type, mutate, qid="", hid="", delta=0.0, forced=False):
        feats, v0, h0 = loop._snapshot()
        mutate()
        return loop._record(move_type, qid, hid, delta_u=delta, forced=forced,
                            features_before=feats, value_before=v0, hash_before=h0)

    def evidence(kind, hid, matched):
        ev = Evidence(evidence_id=state.new_evidence_id(), kind=kind, hypothesis_id=hid,
                      question_id=q1.question_id, exit_code=0 if matched else 1,
                      prediction={"expect_exit_code": 0}, matched_prediction=matched)
        state.record_evidence(ev, auth)
        return ev

    def propose():
        for name, prior in (("wrong", 0.9), ("right", 0.3), ("broken", 0.5), ("idle", 0.1)):
            hyps[name] = state.add_hypothesis(q1.question_id, f"the {name} mechanism in calc", prior=prior)

    def falsify():
        ev = evidence("probe", hyps["wrong"].hypothesis_id, matched=False)
        state.set_hypothesis_verdict(hyps["wrong"].hypothesis_id, FALSIFIED, 0.0, ev.evidence_id, auth)
        state.lower_uncertainty(q1.question_id, 0.8, auth)

    def support():
        ev = evidence("probe", hyps["right"].hypothesis_id, matched=True)
        state.set_hypothesis_verdict(hyps["right"].hypothesis_id, SUPPORTED, 0.8, ev.evidence_id, auth)
        state.lower_uncertainty(q1.question_id, 0.3, auth)

    def refuse():
        ev = evidence("probe_rejected", hyps["broken"].hypothesis_id, matched=False)
        state.mark_untestable(hyps["broken"].hypothesis_id, ev.evidence_id, "SyntaxError", auth)

    def synthesize():
        evidence("module_check", hyps["right"].hypothesis_id, matched=True)
        hyps["right"].patch_applied = True
        state.set_question_status(q1.question_id, Q_RESOLVED, auth)
        state.lower_uncertainty(q1.question_id, 0.2, auth)

    move(MOVE_PROPOSE_HYPOTHESIS, propose, qid=q1.question_id)                                     # 1
    move(MOVE_RUN_EXPERIMENT, falsify, q1.question_id, hyps["wrong"].hypothesis_id, delta=0.2)      # 2
    move(MOVE_RUN_EXPERIMENT, support, q1.question_id, hyps["right"].hypothesis_id, 0.5, True)      # 3
    move(MOVE_RUN_EXPERIMENT, refuse, q1.question_id, hyps["broken"].hypothesis_id)                 # 4
    move(MOVE_SYNTHESIZE, synthesize, q1.question_id, hyps["right"].hypothesis_id, delta=0.1)       # 5
    first = list(loop.trajectory)
    # Between iterations the oracle certifies q1, as reconcile_with_oracle does:
    # the hypothesis that was SUPPORTED when move 3 ran ends the tree CERTIFIED.
    oracle = Evidence(evidence_id=state.new_evidence_id(), kind="oracle",
                      prediction={"expect_exit_code": 0}, matched_prediction=True)
    state.record_evidence(oracle, auth)
    state.certify(q1.question_id, hyps["right"].hypothesis_id, "test_add passes", [oracle.evidence_id], "oracle", auth)

    def propose_q2():
        hyps["late"] = state.add_hypothesis(q2.question_id, "mul delegates to add", prior=0.7)

    move(MOVE_PROPOSE_HYPOTHESIS, propose_q2, qid=q2.question_id)                                  # 6
    move(MOVE_ASK_QUESTION, lambda: None)                                                          # 7
    second = loop.trajectory[len(first):]

    searches = []
    for iteration, traj in ((2, first), (3, second)):
        s = SearchResult(trajectory=traj, final_state=state, moves_used=len(traj),
                         delta_u_total=sum(m.delta_u for m in traj), stop_reason="all_resolved").to_dict()
        s["iteration"] = iteration
        searches.append(s)
    tree = {"company_id": "firm_a", "generation": 16, "ledger": state.to_dict(),
            "searches": searches, "audit": None, "policy": None}
    return tree, state, hyps


def run_on(root, tree, name="firm_a"):
    tree_dir = os.path.join(root, "outputs", name, "generation_16")
    os.makedirs(tree_dir, exist_ok=True)
    with open(os.path.join(tree_dir, f"{name}_epistemic_tree.json"), "w") as f:
        json.dump(tree, f, indent=2, default=str)
    csv_path = os.path.join(root, "out", "value_telemetry.csv")
    json_path = os.path.join(root, "out", "summary.json")
    with redirect_stdout(io.StringIO()) as out:
        summary = evt.main(["--root", root, "--out", csv_path, "--json", json_path])
    with open(csv_path, newline="") as f:
        rows = list(csv.DictReader(f))
    return summary, rows, out.getvalue(), json_path


class ExtractValueTelemetryTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="hae_vt_test_")
        cls.tree, cls.state, cls.hyps = build_tree()
        cls.summary, cls.rows, cls.stdout, cls.json_path = run_on(cls.tmp, cls.tree)
        cls.by_index = {int(r["move_index"]): r for r in cls.rows}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_one_row_per_move_with_feature_columns_in_schema_order(self):
        self.assertEqual(len(self.rows), len(self.state.move_log))
        self.assertEqual(len(self.rows), 7)
        expected = (list(evt.HEAD_COLUMNS) + [f"f_{n}" for n in FEATURE_NAMES] + list(evt.TAIL_COLUMNS))
        self.assertEqual(list(self.rows[0].keys()), expected)
        self.assertEqual(self.summary["columns"], expected)

    def test_move_fields_and_before_features_copied_verbatim(self):
        for move in self.state.move_log:
            row = self.by_index[move.move_index]
            self.assertEqual(row["move_type"], move.move_type)
            self.assertEqual(row["question_id"], move.question_id)
            self.assertEqual(row["hypothesis_id"], move.hypothesis_id)
            self.assertEqual(row["forced_low_prior"], "1" if move.forced_low_prior else "0")
            self.assertAlmostEqual(float(row["delta_u"]), move.delta_u)
            self.assertAlmostEqual(float(row["value_before"]), move.value_before)
            self.assertAlmostEqual(float(row["value_after"]), move.value_after)
            for name in FEATURE_NAMES:
                self.assertAlmostEqual(float(row[f"f_{name}"]), move.features[name], msg=name)
        # Features are the ledger *before* the move: all four hypotheses are
        # still untested when the first experiment runs, one is ruled out by the second.
        self.assertEqual(float(self.by_index[2]["f_untested_ratio"]), 1.0)
        self.assertEqual(float(self.by_index[3]["f_ruled_out_ratio"]), 0.25)
        self.assertEqual(self.rows[0]["company_id"], "firm_a")
        self.assertEqual(self.rows[0]["generation"], "16")

    def test_iteration_recovered_from_search_trajectories(self):
        self.assertEqual([self.by_index[i]["iteration"] for i in range(1, 8)], ["2"] * 5 + ["3"] * 2)
        self.assertEqual(self.summary["moves_iteration_unrecovered"], 0)

    def test_terminal_outcome_is_end_of_tree_status(self):
        r = self.by_index
        self.assertEqual((r[2]["terminal_outcome"], r[2]["terminal_hypothesis_status"],
                          r[2]["terminal_question_status"], r[2]["hypothesis_prior"]),
                         (FALSIFIED, FALSIFIED, Q_CERTIFIED, "0.9"))
        # Move 3 supported the hypothesis; the oracle later certified it. End-of-tree wins.
        self.assertEqual(r[3]["terminal_outcome"], CERTIFIED)
        self.assertEqual(r[3]["forced_low_prior"], "1")
        self.assertEqual(r[4]["terminal_outcome"], UNTESTABLE)
        self.assertEqual(r[5]["terminal_outcome"], CERTIFIED)
        # No hypothesis named: the question's final status stands in.
        self.assertEqual((r[1]["terminal_hypothesis_status"], r[1]["terminal_outcome"]), ("", Q_CERTIFIED))
        self.assertEqual(r[6]["terminal_outcome"], Q_OPEN)
        self.assertEqual((r[7]["question_id"], r[7]["terminal_outcome"], r[7]["terminal_question_status"]), ("", "", ""))
        self.assertEqual(self.hyps["idle"].status, UNVERIFIED)  # never probed, so on no experiment row
        self.assertNotIn(self.hyps["idle"].hypothesis_id, {row["hypothesis_id"] for row in self.rows})

    def test_calibration_summary(self):
        cal = self.summary["calibration"]
        self.assertEqual(cal["hypotheses"], 5)
        self.assertEqual(cal["hypotheses_by_status"],
                         {CERTIFIED: 1, FALSIFIED: 1, UNTESTABLE: 1, UNVERIFIED: 2})
        self.assertEqual((cal["tested"], cal["hits"], cal["base_rate"]), (2, 1, 0.5))
        self.assertAlmostEqual(cal["brier"], 0.65)
        self.assertAlmostEqual(cal["brier"], brier_score([0.9, 0.3], [0.0, 1.0]))
        self.assertAlmostEqual(cal["brier_of_base_rate"], 0.25)
        bins = {b["bin"]: b for b in cal["bins"]}
        self.assertEqual(list(bins), ["0.0-0.2", "0.2-0.4", "0.4-0.6", "0.6-0.8", "0.8-1.0"])
        self.assertEqual((bins["0.8-1.0"]["tested"], bins["0.8-1.0"]["hits"], bins["0.8-1.0"]["hit_rate"],
                          bins["0.8-1.0"]["mean_prior"]), (1, 0, 0.0, 0.9))
        self.assertEqual((bins["0.2-0.4"]["tested"], bins["0.2-0.4"]["hits"], bins["0.2-0.4"]["hit_rate"]), (1, 1, 1.0))
        for name in ("0.0-0.2", "0.4-0.6", "0.6-0.8"):
            self.assertEqual((bins[name]["tested"], bins[name]["hit_rate"]), (0, None), name)

    def test_counts_stdout_and_json(self):
        s = self.summary
        self.assertEqual((s["trees"], s["moves"], s["trees_without_ledger"], s["trees_unreadable"]), (1, 7, 0, 0))
        self.assertEqual(s["moves_by_type"], {MOVE_ASK_QUESTION: 1, MOVE_PROPOSE_HYPOTHESIS: 2,
                                              MOVE_RUN_EXPERIMENT: 3, MOVE_SYNTHESIZE: 1})
        self.assertEqual((s["moves_forced_low_prior"], s["moves_with_progress"]), (1, 3))
        with open(self.json_path) as f:
            self.assertEqual(json.load(f), s)
        self.assertIn("trees: 1", self.stdout)
        self.assertIn("brier=0.6500", self.stdout)
        rendered = [" ".join(line.split()) for line in self.stdout.splitlines()]
        self.assertIn("0.8-1.0 1 0 0.000 0.900", rendered)

    def test_status_vocabulary_matches_ledger(self):
        self.assertEqual(set(evt.TESTED_STATUSES), {SUPPORTED, FALSIFIED, CERTIFIED})
        self.assertEqual(set(evt.POSITIVE_STATUSES), {SUPPORTED, CERTIFIED})

    def test_tree_without_ledger_falls_back_to_trajectories(self):
        root = tempfile.mkdtemp(prefix="hae_vt_noledger_")
        try:
            summary, rows, _, _ = run_on(root, {"company_id": "firm_b", "generation": 16, "ledger": None,
                                                "searches": self.tree["searches"]}, name="firm_b")
        finally:
            shutil.rmtree(root, ignore_errors=True)
        self.assertEqual((summary["trees"], summary["trees_without_ledger"], len(rows)), (1, 1, 7))
        self.assertEqual({r["iteration"] for r in rows}, {"2", "3"})
        self.assertEqual({r["terminal_outcome"] for r in rows}, {""})
        self.assertEqual(summary["calibration"]["tested"], 0)
        self.assertIsNone(summary["calibration"]["brier"])

    def test_bare_ledger_dump_is_accepted(self):
        root = tempfile.mkdtemp(prefix="hae_vt_bare_")
        try:
            summary, rows, _, _ = run_on(root, self.state.to_dict(), name="firm_c")
        finally:
            shutil.rmtree(root, ignore_errors=True)
        self.assertEqual((len(rows), summary["moves_iteration_unrecovered"]), (7, 7))
        self.assertEqual(rows[1]["terminal_outcome"], FALSIFIED)
        self.assertEqual(summary["calibration"]["tested"], 2)

    def test_default_out_path_uses_root_basename(self):
        self.assertEqual(evt.default_out_path(["results/hae_gen16_v6_pilot/"]),
                         os.path.join("results", "hae_gen16_v6_pilot", "value_telemetry.csv"))


if __name__ == "__main__":
    unittest.main()
