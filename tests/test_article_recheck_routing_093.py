"""09.3-D correction routing and version invalidation tests."""
import unittest
from studio.article_recheck_routing_093 import ArticleRecheckRouter
from studio.workflows import WorkflowError
from test_private_release_package_093 import PrivateCandidate093
from test_private_article_intake_093 import DIRECTOR


class ArticleRecheck093(PrivateCandidate093):
    def setUp(self):
        super().setUp()
        self.router = ArticleRecheckRouter(self.release)

    def issue(self, **kwargs):
        return self.router.request(**{**dict(ticket_id="fix-01", draft_id="release-draft",
            issue_type="source_accuracy",reason="Timestamp and source context mismatch",
            director_id=DIRECTOR), **kwargs})

    def test_issue_routes_to_research_without_publishing(self):
        result = self.issue()
        self.assertEqual(result["department"], "research")
        self.assertEqual(result["status"], "open")
        self.assertFalse(result["publication_enabled"])
        self.assertTrue(self.router.status("release-draft")["release_blocked"])

    def test_unauthorized_and_unknown_categories_rejected(self):
        with self.assertRaises(WorkflowError):
            self.router.request(ticket_id="other",draft_id="release-draft",
                issue_type="seo",reason="Improve title",director_id="stranger")
        with self.assertRaises(WorkflowError):
            self.router.request(ticket_id="other",draft_id="release-draft",
                issue_type="unrecognized",reason="Something",director_id=DIRECTOR)

    def test_correction_blocks_release_even_after_source_review(self):
        self.review()
        self.issue()
        with self.assertRaises(WorkflowError):
            self.prepare()

    def test_submit_requires_new_content_and_keeps_review_pending(self):
        self.issue()
        with self.assertRaises(WorkflowError):
            self.router.submit_revised_draft(ticket_id="fix-01",
                revised_draft_id="release-draft",director_id=DIRECTOR)
        self.intake.ingest(draft_id="release-draft-v2",run_id=self.run_id,
            body_markdown="# Corrected headline\\nUpdated context",
            source_urls=["https://example.org/one","https://example.org/two"],
            actor_id=DIRECTOR)
        result = self.router.submit_revised_draft(ticket_id="fix-01",
            revised_draft_id="release-draft-v2",director_id=DIRECTOR)
        self.assertEqual(result["status"], "submitted_for_recheck")
        self.assertFalse(self.gate.evaluate("release-draft-v2")["ready_for_editorial_review"])
        with self.assertRaises(WorkflowError):
            self.router.submit_revised_draft(ticket_id="fix-01",
                revised_draft_id="release-draft-v2",director_id=DIRECTOR)

    def test_duplicate_ticket_rejected(self):
        self.issue()
        with self.assertRaises(WorkflowError):
            self.issue()


if __name__ == "__main__":
    unittest.main()
