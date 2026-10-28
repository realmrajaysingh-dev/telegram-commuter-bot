"""Session 09.3-E: revised article must receive new source review and editorial decision."""
from studio.article_recheck_eligibility_093 import ArticleCorrectionEligibility
from studio.workflows import WorkflowError
from test_article_recheck_routing_093 import ArticleRecheck093
from test_private_article_intake_093 import DIRECTOR


class ArticleCorrectionReverification093(ArticleRecheck093):
    def setUp(self):
        super().setUp()
        self.eligibility = ArticleCorrectionEligibility(self.router)

    def revised(self):
        self.issue()
        self.intake.ingest(
            draft_id="release-draft-v2", run_id=self.run_id,
            body_markdown="# Revised headline\\nFresh context",
            source_urls=["https://example.org/one", "https://example.org/two"],
            actor_id=DIRECTOR,
        )
        self.router.submit_revised_draft(
            ticket_id="fix-01", revised_draft_id="release-draft-v2", director_id=DIRECTOR
        )

    def review_revised(self, second=True, outcome="supports"):
        self.gate.record_source_review(
            draft_id="release-draft-v2", source_url="https://example.org/one",
            reviewer_id=DIRECTOR, finding=outcome,
            evidence_note="Fresh review of revised context",
        )
        if second:
            self.gate.record_source_review(
                draft_id="release-draft-v2", source_url="https://example.org/two",
                reviewer_id=DIRECTOR, finding="supports",
                evidence_note="Fresh second source review",
            )

    def test_original_candidate_invalidated_after_correction(self):
        self.review()
        self.prepare()
        self.issue()
        with self.assertRaises(WorkflowError):
            self.release.read("release-draft")

    def test_pending_revised_draft_cannot_release(self):
        self.issue()
        state = self.eligibility.evaluate("fix-01")
        self.assertEqual(state["status"], "awaiting_revised_draft")
        with self.assertRaises(WorkflowError):
            self.eligibility.prepare_revised_candidate(
                ticket_id="fix-01", reviewer_id=DIRECTOR, editorial_note="OK")

    def test_fresh_reviews_required_and_not_inherited(self):
        self.revised()
        self.assertEqual(self.eligibility.evaluate("fix-01")["status"],
                         "fresh_source_review_required")
        self.review_revised(second=False)
        self.assertFalse(self.eligibility.evaluate("fix-01")["eligible_for_private_editorial_decision"])
        self.gate.record_source_review(
            draft_id="release-draft-v2", source_url="https://example.org/two",
            reviewer_id=DIRECTOR, finding="supports",
            evidence_note="Separately inspected source")
        self.assertTrue(self.eligibility.evaluate("fix-01")["eligible_for_private_editorial_decision"])

    def test_contradiction_on_revised_draft_blocks(self):
        self.revised()
        self.review_revised(outcome="contradicts")
        with self.assertRaises(WorkflowError):
            self.eligibility.prepare_revised_candidate(
                ticket_id="fix-01",reviewer_id=DIRECTOR, editorial_note="OK")

    def test_new_private_candidate_requires_explicit_director_and_note(self):
        self.revised()
        self.review_revised()
        with self.assertRaises(WorkflowError):
            self.eligibility.prepare_revised_candidate(
                ticket_id="fix-01", reviewer_id="intruder", editorial_note="OK")
        with self.assertRaises(WorkflowError):
            self.eligibility.prepare_revised_candidate(
                ticket_id="fix-01", reviewer_id=DIRECTOR, editorial_note=" ")
        item=self.eligibility.prepare_revised_candidate(
            ticket_id="fix-01",reviewer_id=DIRECTOR,editorial_note="Revised sources manually checked")
        self.assertEqual(item["draft_id"],"release-draft-v2")
        self.assertFalse(item["public_publishing_enabled"])
        self.assertFalse(item["delivery_performed"])
