"""Session 09.2: two lanes remain isolated, local-only and nonexecuting."""
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
DIRECTOR = 'director-local'
NOW = datetime.now(timezone.utc).replace(microsecond=0)

class DualLaneMonitorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        registry = RoleRegistry.from_markdown(ROOT / 'docs/AGENT_REGISTRY.md')
        db = Path(self.tmp.name) / 'studio.db'
        self.pipeline = ProjectPipeline(db, {DIRECTOR}, registry)
        self.engine = WorkflowEngine(self.pipeline)
        self.handoff = DailyDraftHandoff(self.engine)
        self.allocation = TaskAllocationEngine(db, registry, {DIRECTOR})
        self.bridge = DailyCEOQueueBridge(self.handoff, self.allocation)
        self.monitor = DualLaneMonitor092(self.bridge)
        self.pipeline.create_project(director_id=DIRECTOR,title='Synthetic documentary',
            project_type='documentary',department_id='D03',language='en',priority='normal',
            role_id='D03-R03',project_id='creative-demo',is_demo=True,now=NOW)
        self.engine.route_project('creative-demo','human_creative_desk',director_id=DIRECTOR,now=NOW)

    def tearDown(self):
        self.allocation.close()
        self.engine.close()
        self.pipeline.close()
        self.tmp.cleanup()

    def make_daily(self):
        p = self.engine.create_policy(director_id=DIRECTOR,policy_id='daily-tech',
            categories=['technology'],content_types=['article'],source_policies=['primary-sources'],
            allowed_channels=['website'],prohibited_topics=['rumors'],
            routine_publish_rules=[{'category':'technology','content_type':'article','channel':'website'}],now=NOW)
        self.engine.approve_policy('daily-tech',p['version'],director_id=DIRECTOR,now=NOW)
        self.engine.create_daily_schedule(director_id=DIRECTOR,schedule_id='daily-1',
            policy_id='daily-tech',category='technology',content_type='article',
            recurrence='daily',next_due_at=NOW,now=NOW)
        run_id=self.engine.run_due_schedules(now=NOW)[0]['run_id']
        self.handoff.prepare(run_id)
        project=self.pipeline.create_project(director_id=DIRECTOR,title='Daily tech draft',
            project_type='news_article',department_id='D02',language='en',priority='normal',
            role_id='D02-R01',now=NOW)
        project_id=project['project_id']
        self.engine.route_project(project_id,'ai_daily_desk',director_id=DIRECTOR,now=NOW)
        self.pipeline.transition_status(project_id,'queued',director_id=DIRECTOR,now=NOW)
        self.bridge.link_existing_project(run_id,project_id,director_id=DIRECTOR)
        return run_id,project_id

    def test_creative_pending_approval_does_not_prevent_daily_queue_link(self):
        self.assertEqual(self.monitor.creative_review('creative-demo')['next_required_gate'],'scope_script')
        self.make_daily()
        result=self.monitor.snapshot()
        self.assertEqual(result['daily_desk']['linked_ceo_plan_count'],1)
        self.assertEqual(result['creative_desk']['projects'],1)
        self.assertFalse(result['publication_enabled'])

    def test_snapshot_is_read_only_and_does_not_start_ceo_plan(self):
        self.make_daily()
        before=len(self.allocation.audit_events())
        one=self.monitor.snapshot()
        two=self.monitor.snapshot()
        self.assertEqual(one,two)
        self.assertEqual(len(self.allocation.audit_events()),before)
        self.assertEqual(one['daily_desk']['linked_plans'][0]['plan_status'],'draft')
        self.assertFalse(one['execution_performed'])

    def test_creative_gate_status_cannot_grant_approval(self):
        for _ in range(2):
            state=self.monitor.creative_review('creative-demo')
            self.assertFalse(state['release_approved'])
            self.assertFalse(state['publication_enabled'])
        self.assertEqual(self.monitor.snapshot()['daily_desk']['linked_ceo_plan_count'],0)

    def test_daily_project_cannot_be_presented_as_creative_review(self):
        _,project_id=self.make_daily()
        with self.assertRaises(WorkflowError):
            self.monitor.creative_review(project_id)
        self.assertFalse(self.monitor.snapshot()['publication_enabled'])

if __name__=='__main__':
    unittest.main()
