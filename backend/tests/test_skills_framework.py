"""Skills framework — structure & consistency validation.

Every registered agent must have a versioned Skill folder with
SKILL.md and policy.md. The validator also cross-checks a few policy
claims against the actual code so the documents cannot silently drift
from behaviour.
"""

import os
import re

import pytest

from app.agents.p1_purchase_intake import P1PurchaseIntake
from app.agents.p4_invoice_matching import (
    P4InvoiceMatching,
    TOLERANCE,
)
from app.agents.v1_service_desk import V1ServiceDesk
from decimal import Decimal


SKILLS_DIR = os.path.join(
    os.path.dirname(__file__), "..", "skills"
)

REQUIRED_AGENTS = {
    "P1": "p1_purchase_intake",
    "P2": "p2_sourcing_rfq",
    "P4": "p4_invoice_matching",
    "S1": "s1_grn_serialisation",
    "F1": "f1_collections",
    "F3": "f3_books_to_tally",
    "V1": "v1_service_desk",
    "D1": "d1_ask_business",
}


def _skill_path(agent_dir: str, filename: str) -> str:
    return os.path.join(SKILLS_DIR, agent_dir, filename)


class TestSkillStructure:

    @pytest.mark.parametrize("agent_dir", REQUIRED_AGENTS.values())
    def test_skill_md_exists_and_nonempty(self, agent_dir):
        path = _skill_path(agent_dir, "SKILL.md")
        assert os.path.isfile(path), f"missing {path}"
        content = open(path, encoding="utf-8").read()
        assert len(content.strip()) > 100, f"{path} is stub-empty"

    @pytest.mark.parametrize("agent_dir", REQUIRED_AGENTS.values())
    def test_policy_md_exists_and_nonempty(self, agent_dir):
        path = _skill_path(agent_dir, "policy.md")
        assert os.path.isfile(path), f"missing {path}"
        content = open(path, encoding="utf-8").read()
        assert len(content.strip()) > 50, f"{path} is stub-empty"


class TestPolicyConsistency:
    """Policy claims must match code reality."""

    def test_p4_tolerance_documented_matches_code(self):
        doc = open(
            _skill_path("p4_invoice_matching", "policy.md"),
            encoding="utf-8",
        ).read()
        assert "0.01" in doc
        # Code really matches to one paisa.
        assert TOLERANCE == Decimal("0.01")

    def test_p4_never_match_without_grn_is_true(self):
        """The 'hard refusal' documented in policy is enforced."""
        doc = open(
            _skill_path("p4_invoice_matching", "policy.md"),
            encoding="utf-8",
        ).read()
        assert "No GRN on file" in doc or "no GRN" in doc.lower()

    def test_v1_supported_types_documented(self):
        doc = open(
            _skill_path("v1_service_desk", "SKILL.md"),
            encoding="utf-8",
        ).read()
        for request_type in V1ServiceDesk.SUPPORTED_TYPES:
            assert request_type in doc, (
                f"policy doc missing request type {request_type}"
            )

    def test_v1_sla_table_covers_all_types(self):
        doc = open(
            _skill_path("v1_service_desk", "policy.md"),
            encoding="utf-8",
        ).read()
        for request_type in V1ServiceDesk.SUPPORTED_TYPES:
            assert request_type in doc

    def test_f3_skill_documents_staging_not_posting(self):
        doc = open(
            _skill_path("f3_books_to_tally", "SKILL.md"),
            encoding="utf-8",
        ).read()
        assert "STAGED" in doc
        assert "post_approved" in doc

    def test_every_skill_declares_autonomy(self):
        for agent_dir in REQUIRED_AGENTS.values():
            doc = open(
                _skill_path(agent_dir, "SKILL.md"), encoding="utf-8"
            ).read()
            assert re.search(r"\bA[123]\b", doc), (
                f"{agent_dir}/SKILL.md declares no autonomy level"
            )

    def test_every_skill_has_a_never_section(self):
        for agent_dir in REQUIRED_AGENTS.values():
            doc = open(
                _skill_path(agent_dir, "SKILL.md"), encoding="utf-8"
            ).read()
            assert "Never" in doc, f"{agent_dir}/SKILL.md has no Never list"
