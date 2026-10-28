"""Session 09.4-A: private preview and HTML injection safety checks."""
import unittest
from studio.private_article_preview_094 import PrivateArticlePreview
from studio.workflows import WorkflowError
from test_private_release_package_093 import PrivateCandidate093
from test_private_article_intake_093 import DIRECTOR
from studio.article_recheck_routing_093 import ArticleRecheckRouter

class PrivatePreview094(unittest.TestCase):
    setUp=PrivateCandidate093.setUp
    tearDown=PrivateCandidate093.tearDown
    prepare_daily=PrivateCandidate093.prepare_daily
    review=PrivateCandidate093.review
    prepare=PrivateCandidate093.prepare

    def test_unreviewed_article_cannot_preview(self):
        with self.assertRaises(WorkflowError):PrivateArticlePreview(self.release).render('release-draft')

    def test_safe_private_preview_with_sources_and_hash(self):
        self.review(); self.prepare()
        result=PrivateArticlePreview(self.release).render('release-draft')
        self.assertIn('PRIVATE REVIEW ONLY',result['preview_html'])
        self.assertIn('https://example.org/one',result['preview_html'])
        self.assertFalse(result['delivery_performed'])
        self.assertFalse(result['public_publishing_enabled'])
        self.assertTrue(result['private_only'])
        self.assertEqual(result,PrivateArticlePreview(self.release).render('release-draft'))

    def test_injected_html_and_scripts_are_escaped(self):
        self.intake.ingest(draft_id='html-injection',run_id=self.run_id,
            body_markdown='# <img src=x onerror=alert(1)>\n<script>alert(1)</script>',
            source_urls=['https://example.org/one'],actor_id=DIRECTOR)
        self.gate.record_source_review(draft_id='html-injection',source_url='https://example.org/one',
            reviewer_id=DIRECTOR,finding='supports',evidence_note='Explicit manual review')
        self.release.prepare(draft_id='html-injection',reviewer_id=DIRECTOR,editorial_note='Internal preview test')
        html=PrivateArticlePreview(self.release).render('html-injection')['preview_html']
        self.assertIn('&lt;script&gt;',html)
        self.assertNotIn('<script>',html)
        self.assertIn('&lt;img',html)

    def test_existing_candidate_invalid_after_correction(self):
        self.review();self.prepare()
        router=ArticleRecheckRouter(self.release)
        router.request(ticket_id='preview-issue',draft_id='release-draft',issue_type='seo',
            reason='Needs revision',director_id=DIRECTOR)
        with self.assertRaises(WorkflowError):PrivateArticlePreview(self.release).render('release-draft')

if __name__=='__main__':unittest.main()
