"""Local integration checks: existing CEO queue, no execution or publication."""
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from studio.allocation import TaskAllocationEngine
from studio.daily_draft_handoff import DailyDraftHandoff
from studio.daily_ceo_queue_bridge import DailyCEOQueueBridge
from studio.pipeline import ProjectPipeline
from studio.registry import RoleRegistry
from studio.workflows import WorkflowEngine, WorkflowError

ROOT = Path(__file__).resolve().parents[1]
DIRECTOR = 'director-local'
NOW = datetime.now(timezone.utc).replace(microsecond=0)

class DailyCEOBridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        registry = RoleRegistry.from_markdown(ROOT / 'docs/AGENT_REGISTRY.md')
        db = Path(self.tmp.name) / 'studio.db'
        self.pipeline = ProjectPipeline(db, {DIRECTOR}, registry)
        self.workflow = WorkflowEngine(self.pipeline)
        self.handoff = DailyDraftHandoff(self.workflow)
        self.allocation = TaskAllocationEngine(db, registry, {DIRECTOR})
        self.bridge = DailyCEOQueueBridge(self.handoff, self.allocation)
        self.workflow.create_policy(director_id=DIRECTOR,policy_id='daily-tech',categories=['technology'],content_types=['article'],source_policies=['primary-sources'],allowed_channels=['website'],prohibited_topics=['rumors'],routine_publish_rules=[{'category':'technology','content_type':'article','channel':'website'}],now=NOW)
        self.workflow.approve_policy('daily-tech',1,director_id=DIRECTOR,now=NOW)
        self.workflow.create_daily_schedule(director_id=DIRECTOR,schedule_id='daily-1',policy_id='daily-tech',category='technology',content_type='article',recurrence='daily',next_due_at=NOW,now=NOW)
        self.run_id = self.workflow.run_due_schedules(now=NOW)[0]['run_id']
        self.project = self.pipeline.create_project(director_id=DIRECTOR,title='Daily technology research',project_type='news_article',department_id='D02',language='en',priority='normal',role_id='D02-R01',now=NOW)
        self.project_id = self.project['project_id']
        self.workflow.route_project(self.project_id,'ai_daily_desk',director_id=DIRECTOR,now=NOW)
        self.pipeline.transition_status(self.project_id, 'queued', director_id=DIRECTOR, now=NOW)

    def tearDown(self):
        self.allocation.close()
        self.workflow.close()
        self.pipeline.close()
        self.tmp.cleanup()

    def test_bridges_into_existing_unapproved_ceo_queue_and_is_idempotent(self):
        self.handoff.prepare(self.run_id)
        first=self.bridge.link_existing_project(self.run_id,self.project_id,director_id=DIRECTOR)
        again=self.bridge.link_existing_project(self.run_id,self.project_id,director_id=DIRECTOR)
        self.assertEqual(first,again)
        self.assertFalse(first['publication_enabled'])
        self.assertFalse(first['execution_performed'])
        plan=self.allocation.get_plan_for_project(self.project_id)
        self.assertEqual(plan['plan_id'],first['plan_id'])
        self.assertEqual(plan['status'],'draft')
        self.assertEqual(plan['template'],'news_article')

    def test_missing_private_handoff_is_rejected(self):
        with self.assertRaises(WorkflowError):
            self.bridge.link_existing_project(self.run_id,self.project_id,director_id=DIRECTOR)
        self.assertIsNone(self.allocation.get_plan_for_project(self.project_id))

    def test_unknown_director_is_rejected(self):
        self.handoff.prepare(self.run_id)
        with self.assertRaises(WorkflowError):
            self.bridge.link_existing_project(self.run_id,self.project_id,director_id='unknown')
        self.assertIsNone(self.allocation.get_plan_for_project(self.project_id))

    def test_creative_project_cannot_be_linked(self):
        self.handoff.prepare(self.run_id)
        self.workflow.route_project(self.project_id,'human_creative_desk',director_id=DIRECTOR,now=NOW)
        with self.assertRaises(WorkflowError):
            self.bridge.link_existing_project(self.run_id,self.project_id,director_id=DIRECTOR)
        self.assertIsNone(self.allocation.get_plan_for_project(self.project_id))
