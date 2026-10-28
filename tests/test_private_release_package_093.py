"""Session 09.3-C: local-only editorial handoff acceptance."""
import unittest
import test_private_article_intake_093 as fixtures
import test_session_092_acceptance as acceptance
from studio.article_editorial_gate_093 import ArticleEditorialGate
from studio.private_release_package_093 import PrivateArticleReleaseCandidate
from studio.workflows import WorkflowError

class PrivateCandidate093(unittest.TestCase):
    prepare_daily = acceptance.Session092Acceptance.prepare_daily
    tearDown = acceptance.Session092Acceptance.tearDown

    def setUp(self):
        fixtures.DraftIntake093.setUp(self)
        self.intake.ingest(draft_id='release-draft',run_id=self.run_id,
            body_markdown='# Private headline\nDraft',
            source_urls=['https://example.org/one','https://example.org/two'],
            actor_id=fixtures.DIRECTOR)
        self.gate = ArticleEditorialGate(self.intake)
        self.release = PrivateArticleReleaseCandidate(self.gate)

    def review(self, second=True, finding='supports'):
        self.gate.record_source_review(draft_id='release-draft',source_url='https://example.org/one',
            reviewer_id=fixtures.DIRECTOR,finding=finding,evidence_note='Source context manually inspected')
        if second:
            self.gate.record_source_review(draft_id='release-draft',source_url='https://example.org/two',
                reviewer_id=fixtures.DIRECTOR,finding='supports',evidence_note='Second source manually inspected')

    def prepare(self, **overrides):
        return self.release.prepare(**{'draft_id':'release-draft','reviewer_id':fixtures.DIRECTOR,
            'editorial_note':'Reviewed wording and context; private handoff only', **overrides})

    def test_unreviewed_sources_block(self):
        with self.assertRaises(WorkflowError): self.prepare()
        self.review(second=False)
        with self.assertRaises(WorkflowError): self.prepare()

    def test_contradiction_blocks(self):
        self.review(finding='contradicts')
        with self.assertRaises(WorkflowError): self.prepare()

    def test_private_candidate_is_persistent_and_not_public(self):
        self.review()
        result = self.prepare()
        self.assertFalse(result['public_publishing_enabled'])
        self.assertFalse(result['delivery_performed'])
        self.assertFalse(result['fact_checked_automatically'])
        self.assertEqual(self.release.read('release-draft'), result)
        self.assertEqual(PrivateArticleReleaseCandidate(self.gate).prepare(
            draft_id='release-draft',reviewer_id=fixtures.DIRECTOR,
            editorial_note='Reviewed wording and context; private handoff only'),result)

    def test_director_only_and_explicit_note(self):
        self.review()
        with self.assertRaises(WorkflowError): self.prepare(reviewer_id='stranger')
        with self.assertRaises(WorkflowError): self.prepare(editorial_note=' ')

    def test_immutable_candidate_requires_new_draft_for_revision(self):
        self.review()
        self.prepare()
        with self.assertRaises(WorkflowError): self.prepare(editorial_note='Revised editorial assessment')

    def test_stored_candidate_tampering_detected(self):
        self.review()
        self.prepare()
        with self.intake.lock,self.intake.db:
            self.intake.db.execute('UPDATE studio_private_article_release_candidates SET candidate_json=? WHERE draft_id=?',
                                   ('{}','release-draft'))
        with self.assertRaises(WorkflowError): self.release.read('release-draft')

if __name__=='__main__': unittest.main()
