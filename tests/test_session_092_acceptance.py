"""Session 09.2 acceptance checks: real local components, synthetic data only.

No provider calls, worker execution, scheduling daemon, or publication is performed.
"""
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from studio.allocation import TaskAllocationEngine
from studio.daily_ceo_queue_bridge import DailyCEOQueueBridge
from studio.daily_draft_handoff import DailyDraftHandoff
from studio.dual_lane_monitor_092 import DualLaneMonitor092
from studio.pipeline import ProjectPipeline
from studio.registry import RoleRegistry
from studio.workflows import WorkflowEngine, WorkflowError

ROOT = Path(__file__).resolve().parents[1]
DIRECTOR = "director-local"
NOW = datetime.now(timezone.utc).replace(microsecond=0)


class Session092Acceptance(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db = Path(self.tmp.name) / "acceptance.sqlite"
        roles = RoleRegistry.from_markdown(ROOT / "docs/AGENT_REGISTRY.md")
        self.pipeline = ProjectPipeline(db, {DIRECTOR}, roles)
        self.workflow = WorkflowEngine(self.pipeline)
        self.handoffs = DailyDraftHandoff(self.workflow)
        self.allocation = TaskAllocationEngine(db, roles, {DIRECTOR})
        self.bridge = DailyCEOQueueBridge(self.handoffs, self.allocation)
        self.monitor = DualLaneMonitor092(self.bridge)

        self.pipeline.create_project(
            director_id=DIRECTOR, title="Synthetic documentary", project_type="documentary",
            department_id="D03", language="en", priority="normal",
            role_id="D03-R03", project_id="creative-demo", is_demo=True, now=NOW,
        )
        self.workflow.route_project("creative-demo", "human_creative_desk", director_id=DIRECTOR, now=NOW)

    def tearDown(self):
        self.allocation.close()
        self.workflow.close()
        self.pipeline.close()
        self.tmp.cleanup()

    def prepare_daily(self):
        policy = self.workflow.create_policy(
            director_id=DIRECTOR, policy_id="daily-tech", categories=["technology"],
            content_types=["article"], source_policies=["primary-sources"],
            allowed_channels=["website"], prohibited_topics=["rumors"],
            routine_publish_rules=[{"category": "technology", "content_type": "article", "channel": "website"}],
            now=NOW,
        )
        self.workflow.approve_policy("daily-tech", policy["version"], director_id=DIRECTOR, now=NOW)
        self.workflow.create_daily_schedule(
            director_id=DIRECTOR, schedule_id="daily-1", policy_id="daily-tech",
            category="technology", content_type="article", recurrence="daily",
            next_due_at=NOW, now=NOW,
        )
        run = self.workflow.run_due_schedules(now=NOW)[0]
        draft = self.handoffs.prepare(run["run_id"])
        project = self.pipeline.create_project(
            director_id=DIRECTOR, title="Synthetic daily article", project_type="news_article",
            department_id="D02", language="en", priority="normal", role_id="D02-R01", now=NOW,
        )
        project_id = project["project_id"]
        self.workflow.route_project(project_id, "ai_daily_desk", director_id=DIRECTOR, now=NOW)
        self.pipeline.transition_status(project_id, "queued", director_id=DIRECTOR, now=NOW)
        return run, draft, project_id

    def test_local_daily_pipeline_handoff_bridge_monitor_and_idempotence(self):
        run, draft, project_id = self.prepare_daily()
        self.assertEqual(draft["status"], "awaiting_private_research")
        self.assertEqual(self.handoffs.prepare(run["run_id"]), draft)
        linked = self.bridge.link_existing_project(run["run_id"], project_id, director_id=DIRECTOR)
        self.assertEqual(self.bridge.link_existing_project(run["run_id"], project_id, director_id=DIRECTOR), linked)
        view = self.monitor.snapshot()
        self.assertEqual(view["daily_desk"]["private_handoff_count"], 1)
        self.assertEqual(view["daily_desk"]["linked_ceo_plan_count"], 1)
        self.assertEqual(view["daily_desk"]["linked_plans"][0]["plan_status"], "draft")
        self.assertFalse(linked["execution_performed"])
        self.assertFalse(view["publication_enabled"])
        self.assertEqual(self.monitor.creative_review("creative-demo")["next_required_gate"], "scope_script")

    def test_creative_project_cannot_be_spoofed_as_daily_and_no_cross_lane_approval(self):
        run, _, project_id = self.prepare_daily()
        with self.assertRaises(WorkflowError):
            self.bridge.link_existing_project(run["run_id"], "creative-demo", director_id=DIRECTOR)
        with self.assertRaises(WorkflowError):
            self.monitor.creative_review(project_id)
        self.assertEqual(self.monitor.snapshot()["daily_desk"]["linked_ceo_plan_count"], 0)

    def test_daily_plan_survives_creative_approval_block_without_publishing(self):
        run, _, project_id = self.prepare_daily()
        self.bridge.link_existing_project(run["run_id"], project_id, director_id=DIRECTOR)
        with self.assertRaises(WorkflowError):
            self.workflow.record_creative_gate(
                "creative-demo", "final_release", director_id=DIRECTOR,
                outcome="pass", artifact_sha256="a" * 64, evidence_refs=["fixture:rough"], now=NOW,
            )
        first = self.monitor.snapshot()
        second = self.monitor.snapshot()
        self.assertEqual(first, second)
        self.assertEqual(first["daily_desk"]["linked_ceo_plan_count"], 1)
        self.assertFalse(first["execution_performed"])
        self.assertFalse(first["publication_enabled"])
        self.assertFalse(self.workflow.creative_gate_status("creative-demo")["release_approved"])


if __name__ == "__main__":
    unittest.main()
