"""Session 09.2: independent local desk governance regression coverage.

These tests exercise policy contracts and private schedule planning; they do not
claim that production publishing, news collection or background workers operate.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from studio.pipeline import ProjectPipeline
from studio.registry import RoleRegistry
from studio.workflows import WorkflowEngine, WorkflowError

ROOT = Path(__file__).resolve().parents[1]
DIRECTOR = "director-local"
NOW = datetime(2026, 10, 28, 9, 0, tzinfo=timezone.utc)


class DualDeskGovernanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        registry = RoleRegistry.from_markdown(ROOT / "docs" / "AGENT_REGISTRY.md")
        self.pipeline = ProjectPipeline(Path(self.tmp.name) / "studio.db", {DIRECTOR}, registry)
        self.workflow = WorkflowEngine(self.pipeline)

    def tearDown(self):
        self.workflow.close()
        self.pipeline.close()
        self.tmp.cleanup()

    def policy(self, approved=True):
        result = self.workflow.create_policy(
            director_id=DIRECTOR, policy_id="daily-tech", categories=["technology"],
            content_types=["article"], source_policies=["verified-primary-sources"],
            allowed_channels=["website"], prohibited_topics=["unverified-rumors"],
            routine_publish_rules=[{"category": "technology", "content_type": "article", "channel": "website"}],
            now=NOW,
        )
        if approved:
            self.workflow.approve_policy("daily-tech", result["version"], director_id=DIRECTOR, now=NOW)
        return result

    def test_schedule_requires_director_approved_policy(self):
        self.policy(approved=False)
        with self.assertRaises(WorkflowError):
            self.workflow.create_daily_schedule(
                director_id=DIRECTOR, schedule_id="tech-daily", policy_id="daily-tech",
                category="technology", content_type="article", recurrence="daily",
                next_due_at=NOW + timedelta(minutes=1), now=NOW,
            )

    def test_due_schedule_is_private_and_idempotent(self):
        self.policy()
        self.workflow.create_daily_schedule(
            director_id=DIRECTOR, schedule_id="tech-daily", policy_id="daily-tech",
            category="technology", content_type="article", recurrence="daily",
            next_due_at=NOW + timedelta(minutes=1), now=NOW,
        )
        due = NOW + timedelta(minutes=2)
        first = self.workflow.run_due_schedules(now=due)
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]["status"], "planned")
        self.assertFalse(first[0]["publication_enabled"])
        self.assertEqual(self.workflow.run_due_schedules(now=due), [])
        self.assertEqual(self.workflow.snapshot(now=due)["workflows"]["human_creative_desk"]["projects"], 0)

    def test_no_current_story_blocks_without_verified_evergreen(self):
        self.policy()
        self.workflow.create_daily_schedule(
            director_id=DIRECTOR, schedule_id="tech-daily", policy_id="daily-tech",
            category="technology", content_type="article", recurrence="daily",
            next_due_at=NOW, now=NOW,
        )
        result = self.workflow.run_due_schedules(now=NOW, current_story_available=False)
        self.assertEqual(result[0]["status"], "blocked")
        self.assertEqual(result[0]["reason_code"], "no_verified_evergreen_fallback")
        self.assertFalse(result[0]["publication_enabled"])

    def test_routine_contract_does_not_authorize_public_dispatch(self):
        self.policy()
        checks = {name: True for name in (
            "quality_verified", "sources_verified", "copyright_verified", "duplicate_checked",
            "risk_cleared", "evidence_complete",
        )}
        result = self.workflow.evaluate_routine_release(
            policy_id="daily-tech", category="technology", content_type="article", channel="website",
            artifact_sha256="a" * 64, checks=checks, evidence_refs=["evidence-1"],
        )
        self.assertTrue(result["eligible_under_policy_contract"])
        self.assertFalse(result["publication_enabled"])
        with self.assertRaises(WorkflowError):
            self.workflow.dispatch_publish()
        checks["sources_verified"] = False
        rejected = self.workflow.evaluate_routine_release(
            policy_id="daily-tech", category="technology", content_type="article", channel="website",
            artifact_sha256="a" * 64, checks=checks, evidence_refs=["evidence-1"],
        )
        self.assertFalse(rejected["eligible_under_policy_contract"])

    def test_unauthorized_actor_cannot_set_policy_or_budget(self):
        with self.assertRaises(WorkflowError):
            self.workflow.create_policy(
                director_id="unknown", policy_id="daily-tech", categories=["technology"],
                content_types=["article"], source_policies=["verified"],
                allowed_channels=["website"], prohibited_topics=["rumors"],
                routine_publish_rules=[], now=NOW,
            )
        with self.assertRaises(WorkflowError):
            self.workflow.set_workflow_budget(
                "ai_daily_desk", director_id="unknown", max_active_projects=1,
                max_daily_items=1, max_runtime_seconds=60, max_storage_bytes=1000, now=NOW,
            )


if __name__ == "__main__":
    unittest.main()
