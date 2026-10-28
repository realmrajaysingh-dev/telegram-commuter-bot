"""Session 09.2: local-only human creative approval regression tests.

No real publishing, model execution, or authentication is exercised here.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from studio.pipeline import ProjectPipeline
from studio.registry import RoleRegistry
from studio.workflows import WorkflowEngine, WorkflowError

ROOT = Path(__file__).resolve().parents[1]
DIRECTOR = "director-local"
NOW = datetime(2026, 10, 28, 9, 0, tzinfo=timezone.utc)


class CreativeGateGovernanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        registry = RoleRegistry.from_markdown(ROOT / "docs" / "AGENT_REGISTRY.md")
        self.pipeline = ProjectPipeline(Path(self.tmp.name) / "studio.db", {DIRECTOR}, registry)
        self.workflow = WorkflowEngine(self.pipeline)
        self.pipeline.create_project(
            director_id=DIRECTOR, title="Synthetic documentary", project_type="documentary",
            department_id="D03", language="en", priority="normal", role_id="D03-R03",
            project_id="creative-demo", is_demo=True, now=NOW,
        )
        self.workflow.route_project("creative-demo", "human_creative_desk", director_id=DIRECTOR, now=NOW)

    def tearDown(self):
        self.workflow.close()
        self.pipeline.close()
        self.tmp.cleanup()

    def gate(self, gate_id, outcome="pass", digest="a" * 64, director=DIRECTOR):
        return self.workflow.record_creative_gate(
            "creative-demo", gate_id, director_id=director, outcome=outcome,
            artifact_sha256=digest, evidence_refs=["synthetic-review-evidence"], now=NOW,
        )

    def test_no_gate_or_publication_before_human_review(self):
        status = self.workflow.creative_gate_status("creative-demo")
        self.assertEqual(status["next_required_gate"], "scope_script")
        self.assertFalse(status["release_approved"])
        self.assertFalse(status["publication_enabled"])
        with self.assertRaises(WorkflowError):
            self.workflow.dispatch_publish()

    def test_gate_two_and_three_cannot_be_skipped(self):
        for gate_id in ("rough_cut", "final_release"):
            with self.subTest(gate_id=gate_id), self.assertRaises(WorkflowError):
                self.gate(gate_id)
        self.gate("scope_script")
        with self.assertRaises(WorkflowError):
            self.gate("final_release")
        self.assertEqual(self.workflow.creative_gate_status("creative-demo")["next_required_gate"], "rough_cut")

    def test_three_gate_approval_chain_and_no_real_publication(self):
        g1 = self.gate("scope_script", digest="a" * 64)
        g2 = self.gate("rough_cut", digest="b" * 64)
        g3 = self.gate("final_release", digest="c" * 64)
        self.assertEqual(g2["predecessor_event_id"], g1["event_id"])
        self.assertEqual(g3["predecessor_event_id"], g2["event_id"])
        state = self.workflow.creative_gate_status("creative-demo")
        self.assertTrue(state["release_approved"])
        self.assertEqual(state["next_required_gate"], "complete")
        self.assertFalse(state["publication_enabled"])
        self.assertTrue(self.workflow.verify_audit_chain())

    def test_revised_earlier_gate_invalidates_downstream_approval(self):
        self.gate("scope_script")
        self.gate("rough_cut")
        self.gate("final_release")
        self.gate("scope_script", outcome="revise", digest="d" * 64)
        state = self.workflow.creative_gate_status("creative-demo")
        self.assertFalse(state["release_approved"])
        self.assertEqual(state["next_required_gate"], "scope_script")
        with self.assertRaises(WorkflowError):
            self.gate("rough_cut")

    def test_unapproved_director_and_invalid_artifact_rejected(self):
        with self.assertRaises(WorkflowError):
            self.gate("scope_script", director="unapproved-user")
        with self.assertRaises(WorkflowError):
            self.gate("scope_script", digest="not-a-sha256")
        with self.assertRaises(WorkflowError):
            self.workflow.record_creative_gate(
                "creative-demo", "scope_script", director_id=DIRECTOR,
                outcome="pass", artifact_sha256="a" * 64, evidence_refs=[], now=NOW,
            )

    def test_ai_daily_project_cannot_use_creative_approval(self):
        self.workflow.route_project("creative-demo", "ai_daily_desk", director_id=DIRECTOR, now=NOW)
        with self.assertRaises(WorkflowError):
            self.gate("scope_script")
        with self.assertRaises(WorkflowError):
            self.workflow.creative_gate_status("creative-demo")


if __name__ == "__main__":
    unittest.main()
