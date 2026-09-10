"""Inter-Firm Strategic Co-opetition, Executive Communication Channels, and Consortium Formation."""

from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, field
import json
from .schema import CompanyGenome, CorporateAsset

class AllianceType:
    JOINT_VENTURE = "joint_venture"
    TECHNOLOGY_LICENSING = "technology_licensing"
    CONSORTIUM = "consortium"

@dataclass
class TermSheet:
    """Bilateral or multilateral term sheet negotiated between executive suites."""
    term_sheet_id: str
    proposer_id: str
    target_id: str
    alliance_type: str = AllianceType.JOINT_VENTURE
    proposer_focus: str = "technical_systems"
    target_focus: str = "strategic_gtm"
    fitness_split_ratio: float = 0.50
    synergy_bonus_pts: float = 2.50
    terms_summary: str = ""
    accepted: bool = False

class ExecutiveCommunicationHub:
    """Inter-firm executive communication bus facilitating strategic negotiations and joint ventures."""

    def __init__(self):
        self.active_term_sheets: Dict[str, TermSheet] = {}
        self.consortium_alliances: List[Tuple[str, str]] = []

    def propose_alliance(
        self,
        proposer: CompanyGenome,
        target: CompanyGenome,
        alliance_type: str = AllianceType.JOINT_VENTURE,
        proposer_focus: str = "technical_systems",
        target_focus: str = "strategic_gtm"
    ) -> TermSheet:
        """Issues a formal executive term sheet from one firm to another."""
        ts_id = f"ts_{proposer.company_id}_{target.company_id}"
        term_sheet = TermSheet(
            term_sheet_id=ts_id,
            proposer_id=proposer.company_id,
            target_id=target.company_id,
            alliance_type=alliance_type,
            proposer_focus=proposer_focus,
            target_focus=target_focus,
            fitness_split_ratio=0.50,
            terms_summary=f"Bilateral strategic alliance between {proposer.company_id} and {target.company_id}."
        )
        self.active_term_sheets[ts_id] = term_sheet
        return term_sheet

    def evaluate_and_respond(
        self,
        term_sheet: TermSheet,
        target_firm: CompanyGenome,
        complementary_threshold: float = 0.5
    ) -> bool:
        """Target CEO evaluates the term sheet using non-zero-sum game-theoretic principles."""
        # Check if alliance type and focus areas are complementary rather than conflicting
        is_complementary = (term_sheet.proposer_focus != term_sheet.target_focus)
        # Check if split ratio is equitable (within 0.40 - 0.60)
        is_equitable = 0.40 <= term_sheet.fitness_split_ratio <= 0.60

        if is_complementary and is_equitable:
            term_sheet.accepted = True
            self.consortium_alliances.append((term_sheet.proposer_id, term_sheet.target_id))
            return True
        term_sheet.accepted = False
        return False

    def merge_consortium_deliverables(
        self,
        firm_a_deliverable: str,
        firm_a_briefs: Dict[str, str],
        firm_b_deliverable: str,
        firm_b_briefs: Dict[str, str]
    ) -> Tuple[str, Dict[str, str]]:
        """Synthesizes a joint unified deliverable co-authored by allied consortium partners."""
        joint_deliverable = (
            f"# JOINT CONSORTIUM STRATEGIC DELIVERABLE\n\n"
            f"## Executive Alliance Overview\n"
            f"This unified submission co-developed by strategic consortium partners combines complementary "
            f"systems architecture, governance, risk mitigation, and commercial execution.\n\n"
            f"### Pillar I: Systems Architecture & Technical Infrastructure\n"
            f"{firm_a_deliverable}\n\n"
            f"### Pillar II: Commercial Moats, Governance & Operational Strategy\n"
            f"{firm_b_deliverable}\n"
        )
        joint_briefs = {}
        for k, v in firm_a_briefs.items():
            joint_briefs[f"partner_a_{k}"] = v
        for k, v in firm_b_briefs.items():
            joint_briefs[f"partner_b_{k}"] = v

        return joint_deliverable, joint_briefs
