"""Session 09.3-A private draft intake: synthetic local contract tests."""
import unittest
from test_session_092_acceptance import Session092Acceptance, DIRECTOR
from studio.private_article_intake_093 import PrivateArticleDraftIntake
from studio.workflows import WorkflowError


class DraftIntake093(unittest.TestCase):
    prepare_daily = Session092Acceptance.prepare_daily
    tearDown = Session092Acceptance.tearDown
    def setUp(self):
        Session092Acceptance.setUp(self)
        run, _, self.project_id = self.prepare_daily()
        self.run_id = run['run_id']
        self.bridge.link_existing_project(self.run_id, self.project_id, director_id=DIRECTOR)
        self.intake = PrivateArticleDraftIntake(self.bridge)

    def test_ingest_persists_unverified_draft_and_sources(self):
        result = self.intake.ingest(draft_id='draft-a', run_id=self.run_id,
            body_markdown='# Article\nDraft only', source_urls=['https://example.org/reference'], actor_id=DIRECTOR)
        self.assertFalse(result['publication_enabled'])
        self.assertFalse(result['fact_checked'])
        self.assertEqual(self.intake.get('draft-a')['body_markdown'], '# Article\nDraft only')
        self.assertEqual(self.intake.get('draft-a')['source_urls'], ['https://example.org/reference'])

    def test_idempotent_but_reject_changed_payload(self):
        args=dict(draft_id='draft-b',run_id=self.run_id,body_markdown='Draft',
                  source_urls=['https://example.org/one'],actor_id=DIRECTOR)
        first=self.intake.ingest(**args)
        self.assertEqual(first,self.intake.ingest(**args))
        with self.assertRaises(WorkflowError):
            self.intake.ingest(**{**args,'body_markdown':'Changed'})

    def test_reject_unsafe_sources_and_unauthorized_actor(self):
        args=dict(draft_id='draft-c',run_id=self.run_id,body_markdown='Draft',actor_id=DIRECTOR)
        for source in ['http://example.org', 'file:///tmp/foo', 'https://user:pw@example.org', 'https://localhost/test']:
            with self.subTest(source=source), self.assertRaises(WorkflowError):
                self.intake.ingest(**args,source_urls=[source])
        with self.assertRaises(WorkflowError):
            self.intake.ingest(**{**args,'actor_id':'outsider'},source_urls=['https://example.org'])

    def test_cannot_ingest_into_unlinked_daily_run_or_creative(self):
        with self.assertRaises(WorkflowError):
            self.intake.ingest(draft_id='draft-d',run_id='notlinked',body_markdown='Draft',
                              source_urls=['https://example.org'],actor_id=DIRECTOR)
        self.assertEqual(self.monitor.creative_review('creative-demo')['next_required_gate'],'scope_script')
