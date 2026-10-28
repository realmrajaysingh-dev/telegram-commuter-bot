"""Local-only functional verification for Session 09.2 private Daily Desk handoff."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from studio.pipeline import ProjectPipeline
from studio.registry import RoleRegistry
from studio.workflows import WorkflowEngine, WorkflowError
from studio.daily_draft_handoff import DailyDraftHandoff

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime.now(timezone.utc).replace(microsecond=0)
DIRECTOR = 'director-local'

class DailyDraftHandoffTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        registry = RoleRegistry.from_markdown(ROOT / 'docs/AGENT_REGISTRY.md')
        self.pipeline = ProjectPipeline(Path(self.tmp.name) / 'studio.db', {DIRECTOR}, registry)
        self.engine = WorkflowEngine(self.pipeline)
        self.handoff = DailyDraftHandoff(self.engine)

    def tearDown(self):
        self.engine.close()
        self.pipeline.close()
        self.tmp.cleanup()

    def planned_run(self):
        p = self.engine.create_policy(
            director_id=DIRECTOR, policy_id='daily-tech', categories=['technology'],
            content_types=['article'], source_policies=['primary-sources'],
            allowed_channels=['website'], prohibited_topics=['rumors'],
            routine_publish_rules=[{'category':'technology','content_type':'article','channel':'website'}], now=NOW)
        self.engine.approve_policy('daily-tech', p['version'], director_id=DIRECTOR, now=NOW)
        self.engine.create_daily_schedule(
            director_id=DIRECTOR, schedule_id='daily-1', policy_id='daily-tech',
            category='technology', content_type='article', recurrence='daily',
            next_due_at=NOW, now=NOW)
        return self.engine.run_due_schedules(now=NOW)[0]['run_id']

    def test_handoff_is_private_persistent_and_idempotent(self):
        run_id = self.planned_run()
        first = self.handoff.prepare(run_id)
        self.assertEqual(first, self.handoff.prepare(run_id))
        self.assertFalse(first['publication_enabled'])
        self.assertFalse(first['execution_performed'])
        self.assertEqual(len(self.handoff.list_private_handoffs()), 1)
        self.assertTrue(self.engine.verify_audit_chain())
        self.assertEqual(first['requested_steps'][0], 'research')

    def test_unknown_run_is_rejected(self):
        with self.assertRaises(WorkflowError):
            self.handoff.prepare('unknown-run')

    def test_invalid_run_id_is_rejected(self):
        with self.assertRaises(WorkflowError):
            self.handoff.prepare('../secret')

    def test_blocked_daily_run_cannot_create_handoff(self):
        self.planned_run()  # creates first schedule occurrence
        # A separate schedule with no current story is blocked, not handed off.
        self.engine.create_daily_schedule(
            director_id=DIRECTOR, schedule_id='daily-2', policy_id='daily-tech',
            category='technology', content_type='article', recurrence='daily',
            next_due_at=NOW + timedelta(minutes=1), now=NOW)
        run = self.engine.run_due_schedules(now=NOW + timedelta(minutes=2), current_story_available=False)[0]
        self.assertEqual(run['status'], 'blocked')
        with self.assertRaises(WorkflowError):
            self.handoff.prepare(run['run_id'])
