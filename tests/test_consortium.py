"""Unit tests for Inter-Firm Strategic Co-opetition and Consortium Formation."""

import unittest
from src.schema import CompanyGenome, AgentGenome
from src.consortium import ExecutiveCommunicationHub, AllianceType, TermSheet

class TestConsortium(unittest.TestCase):

    def setUp(self):
        self.firm_a = CompanyGenome(
            company_id="firm_sys_eng",
            ceo=AgentGenome(role="CEO", goal="Deliver high-performance infrastructure")
        )
        self.firm_b = CompanyGenome(
            company_id="firm_gtm_strategy",
            ceo=AgentGenome(role="CEO", goal="Secure commercial moats and enterprise compliance")
        )
        self.hub = ExecutiveCommunicationHub()

    def test_propose_and_accept_consortium(self):
        ts = self.hub.propose_alliance(
            proposer=self.firm_a,
            target=self.firm_b,
            proposer_focus="technical_infrastructure",
            target_focus="commercial_gtm"
        )
        self.assertFalse(ts.accepted)
        
        accepted = self.hub.evaluate_and_respond(ts, self.firm_b)
        self.assertTrue(accepted)
        self.assertTrue(ts.accepted)
        self.assertIn((self.firm_a.company_id, self.firm_b.company_id), self.hub.consortium_alliances)

    def test_reject_unbalanced_alliance(self):
        ts = self.hub.propose_alliance(
            proposer=self.firm_a,
            target=self.firm_b,
            proposer_focus="technical_infrastructure",
            target_focus="technical_infrastructure"  # Conflicting, redundant focus
        )
        accepted = self.hub.evaluate_and_respond(ts, self.firm_b)
        self.assertFalse(accepted)
        self.assertFalse(ts.accepted)

    def test_merge_consortium_deliverables(self):
        deliv_a = "High-throughput async execution runtime with OTel anchors."
        deliv_b = "Enterprise SOC2 compliance roadmap and Tier-1 customer contracts."
        briefs_a = {"dept_eng": "Built core pipeline."}
        briefs_b = {"dept_gtm": "Negotiated enterprise pilots."}

        joint_deliv, joint_briefs = self.hub.merge_consortium_deliverables(
            firm_a_deliverable=deliv_a,
            firm_a_briefs=briefs_a,
            firm_b_deliverable=deliv_b,
            firm_b_briefs=briefs_b
        )
        self.assertIn("JOINT CONSORTIUM STRATEGIC DELIVERABLE", joint_deliv)
        self.assertIn("partner_a_dept_eng", joint_briefs)
        self.assertIn("partner_b_dept_gtm", joint_briefs)

if __name__ == "__main__":
    unittest.main()
