"""09.3-B: local-only editorial evidence, never publication authorization."""
import unittest
import test_private_article_intake_093 as fixtures
import test_session_092_acceptance as acceptance
from studio.article_editorial_gate_093 import ArticleEditorialGate
from studio.workflows import WorkflowError


class EditorialGate093(unittest.TestCase):
    prepare_daily = acceptance.Session092Acceptance.prepare_daily
    tearDown = acceptance.Session092Acceptance.tearDown
    def setUp(self):
        fixtures.DraftIntake093.setUp(self)
        self.gate = ArticleEditorialGate(self.intake)
        self.intake.ingest(draft_id='draft-review', run_id=self.run_id,
            body_markdown='Draft claim for manual review',
            source_urls=['https://example.org/a', 'https://example.org/b'], actor_id=fixtures.DIRECTOR)

    def review(self, url='https://example.org/a', finding='supports', reviewer=fixtures.DIRECTOR):
        return self.gate.record_source_review(draft_id='draft-review', source_url=url,
            reviewer_id=reviewer, finding=finding, evidence_note='Manually checked source context')

    def test_missing_evidence_blocks(self):
        status = self.gate.evaluate('draft-review')
        self.assertEqual(len(status['unreviewed_sources']), 2)
        self.assertFalse(status['ready_for_editorial_review'])
        self.assertFalse(status['publication_enabled'])

    def test_complete_support_is_not_publication_approval(self):
        self.review()
        self.review('https://example.org/b')
        status = self.gate.evaluate('draft-review')
        self.assertTrue(status['ready_for_editorial_review'])
        self.assertFalse(status['fact_checked'])
        self.assertFalse(status['editorially_approved'])
        self.assertFalse(status['publication_enabled'])

    def test_contradiction_or_inconclusive_blocks(self):
        self.review(finding='contradicts')
        self.review('https://example.org/b', 'inconclusive')
        status = self.gate.evaluate('draft-review')
        self.assertFalse(status['ready_for_editorial_review'])
        self.assertEqual(len(status['contradicting_sources']), 1)
        self.assertEqual(len(status['inconclusive_sources']), 1)

    def test_no_fabricated_source_or_reviewer(self):
        with self.assertRaises(WorkflowError):
            self.review('https://example.org/not-in-draft')
        with self.assertRaises(WorkflowError):
            self.review(reviewer='unauthorized')
        with self.assertRaises(WorkflowError):
            self.review(finding='verified')

    def test_review_is_immutable(self):
        self.review()
        with self.assertRaises(WorkflowError):
            self.review(finding='contradicts')

    def test_review_is_persistent(self):
        self.review()
        second = ArticleEditorialGate(self.intake)
        self.assertEqual(second.evaluate('draft-review')['reviewed_sources'], 1)


if __name__ == '__main__':
    unittest.main()
