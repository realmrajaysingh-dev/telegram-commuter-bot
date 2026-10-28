"""Session 09.3-F: cross-component, private-only article acceptance.

Verifies the whole local correction cycle without publishing or external calls.
"""
import unittest

from studio.article_recheck_routing_093 import ArticleRecheckRouter
from studio.article_recheck_eligibility_093 import ArticleCorrectionEligibility
from studio.workflows import WorkflowError
from test_private_release_package_093 import PrivateCandidate093
from test_private_article_intake_093 import DIRECTOR


class Session093Acceptance(unittest.TestCase):
    setUp = PrivateCandidate093.setUp
    tearDown = PrivateCandidate093.tearDown
    prepare_daily = PrivateCandidate093.prepare_daily
    review = PrivateCandidate093.review
    prepare = PrivateCandidate093.prepare

    def _components(self):
        router = ArticleRecheckRouter(self.release)
        return router, ArticleCorrectionEligibility(router)

    def _ticket(self, router, issue_type='source_accuracy'):
        return router.request(ticket_id='final-issue', draft_id='release-draft',
                              issue_type=issue_type, reason='Recorded evidence needs correction',
                              director_id=DIRECTOR)

    def _revised(self, router):
        self.intake.ingest(draft_id='final-draft-v2', run_id=self.run_id,
                           body_markdown='# Corrected story\nAdditional context and dates.',
                           source_urls=['https://example.org/one', 'https://example.org/two'],
                           actor_id=DIRECTOR)
        return router.submit_revised_draft(ticket_id='final-issue',
                                           revised_draft_id='final-draft-v2', director_id=DIRECTOR)

    def _review_new(self, finding='supports'):
        for url in ['https://example.org/one', 'https://example.org/two']:
            self.gate.record_source_review(draft_id='final-draft-v2', source_url=url,
                                           reviewer_id=DIRECTOR, finding=finding,
                                           evidence_note='Manually rechecked revised draft source')

    def test_full_cycle_revised_private_release_requires_new_review(self):
        router, eligibility = self._components()
        self.review()
        original = self.prepare()
        self.assertFalse(original['delivery_performed'])
        self._ticket(router)
        with self.assertRaises(WorkflowError):
            self.release.read('release-draft')
        with self.assertRaises(WorkflowError):
            self.prepare()
        self._revised(router)
        self.assertFalse(eligibility.evaluate('final-issue')['eligible_for_private_editorial_decision'])
        self._review_new()
        state = eligibility.evaluate('final-issue')
        self.assertTrue(state['eligible_for_private_editorial_decision'])
        result = eligibility.prepare_revised_candidate(ticket_id='final-issue', reviewer_id=DIRECTOR,
                                                        editorial_note='Revised sources independently reviewed')
        self.assertEqual(result['draft_id'], 'final-draft-v2')
        self.assertNotEqual(result['body_sha256'], original['body_sha256'])
        self.assertFalse(result['public_publishing_enabled'])
        self.assertFalse(result['delivery_performed'])
        self.assertEqual(self.release.read('final-draft-v2'), result)

    def test_department_routes_and_ticket_persistence(self):
        router, _ = self._components()
        ticket = self._ticket(router, 'seo')
        self.assertEqual(ticket['department'], 'seo')
        self.assertEqual(ArticleRecheckRouter(self.release).read('final-issue')['status'], 'open')
        self.assertTrue(router.status('release-draft')['requires_new_version_and_fresh_review'])

    def test_contradictory_revised_evidence_blocks_end_to_end(self):
        router, eligibility = self._components()
        self._ticket(router)
        self._revised(router)
        self._review_new(finding='contradicts')
        self.assertFalse(eligibility.evaluate('final-issue')['eligible_for_private_editorial_decision'])
        with self.assertRaises(WorkflowError):
            eligibility.prepare_revised_candidate(ticket_id='final-issue', reviewer_id=DIRECTOR,
                                                   editorial_note='Incorrectly accept')

    def test_unauthorized_recheck_cannot_produce_candidate(self):
        router, eligibility = self._components()
        self._ticket(router)
        self._revised(router)
        self._review_new()
        with self.assertRaises(WorkflowError):
            eligibility.prepare_revised_candidate(ticket_id='final-issue', reviewer_id='unknown',
                                                   editorial_note='Attempt bypass')
        with self.assertRaises(WorkflowError):
            self.release.read('final-draft-v2')


if __name__ == '__main__':
    unittest.main()
