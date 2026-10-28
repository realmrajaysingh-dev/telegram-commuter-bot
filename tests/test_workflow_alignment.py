"""Regression checks for the interrupted Session 09.2 workflow dashboard."""
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from studio.issues import IssueEngine
from studio.pipeline import ProjectPipeline
from studio.registry import RoleRegistry
from studio.workflows import WorkflowEngine
from studio.dashboard import create_dashboard_server

ROOT = Path(__file__).resolve().parents[1]
DIRECTOR = "director-local"


class DualWorkflowAlignmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "office.db"
        self.registry = RoleRegistry.from_markdown(ROOT / "docs" / "AGENT_REGISTRY.md")
        self.issues = IssueEngine(self.db, {DIRECTOR})
        self.pipeline = ProjectPipeline(self.db, {DIRECTOR}, self.registry)

    def tearDown(self):
        self.pipeline.close()
        self.issues.close()
        self.tmp.cleanup()

    def test_snapshot_has_both_desks_and_no_public_publishing(self):
        workflow = WorkflowEngine(self.pipeline)
        try:
            result = workflow.snapshot()
            self.assertFalse(result["publication_enabled"])
            self.assertEqual(result["mode"], "local_only")
            self.assertIn("ai_daily_desk", result["workflows"])
            self.assertIn("human_creative_desk", result["workflows"])
            self.assertEqual(result["workflows"]["human_creative_desk"]["human_gates"], ["scope_script", "rough_cut", "final_release"])
            self.assertTrue(result["audit_chain_valid"])
        finally:
            workflow.close()

    def test_dashboard_owns_and_closes_workflow_resource(self):
        server = create_dashboard_server(self.issues, host="127.0.0.1", port=0)
        self.assertTrue(any(isinstance(x, WorkflowEngine) for x in server._owned_resources))
        server.server_close()


if __name__ == "__main__":
    unittest.main()
