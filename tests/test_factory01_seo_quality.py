import tempfile
import unittest
from pathlib import Path
from studio.factory01_topic_intake import Factory01TopicIntake
from studio.factory01_research_evidence import Factory01ResearchEvidence
from studio.factory01_bilingual_drafts import Factory01BilingualDrafts
from studio.factory01_seo_quality import Factory01SEOQuality, SEOQualityError


class SEOQualityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.intake = Factory01TopicIntake(Path(self.tmp.name)/'studio.sqlite')
        self.intake.submit(candidate_id='story-1', category='Technology & AI',
            headline='Test story', source_urls=['https://example.org/source'], submitted_by='author')
        self.evidence = Factory01ResearchEvidence(self.intake)
        self.evidence.record(candidate_id='story-1',source_url='https://example.org/source',
            reviewer_id='source-reviewer', finding='supports', evidence_note='Manually checked')
        self.drafts = Factory01BilingualDrafts(self.evidence)
        self.drafts.submit(candidate_id='story-1', english_markdown='# English',
                           hindi_markdown='# हिंदी', submitted_by='author')
        self.quality = Factory01SEOQuality(self.drafts)

    def tearDown(self):
        self.intake.close()
        self.tmp.cleanup()

    def args(self):
        return {'candidate_id':'story-1', 'metadata':{
            'en':{'title':'Research update','description':'A research update for readers','slug':'research-update-en'},
            'hi':{'title':'शोध समाचार','description':'पाठकों के लिए शोध समाचार','slug':'research-update-hi'}},
            'checks':{key:True for key in Factory01SEOQuality.CHECKS},'reviewer_id':'editor'}

    def test_missing_review_is_blocked(self):
        self.assertFalse(self.quality.evaluate('story-1')['ready_for_next_private_gate'])

    def test_human_review_record_is_private_not_fact_check(self):
        report=self.quality.submit(**self.args())
        self.assertTrue(report['ready_for_next_private_gate'])
        self.assertFalse(report['publishing_enabled'])
        self.assertFalse(report['independently_fact_checked'])
        self.assertFalse(report['seo_ranking_verified'])
        self.assertEqual(report,self.quality.submit(**self.args()))

    def test_failed_check_blocks(self):
        args=self.args(); args['checks']['rights_review']=False
        report=self.quality.submit(**args)
        self.assertFalse(report['ready_for_next_private_gate'])

    def test_invalid_slug_and_incomplete_metadata(self):
        args=self.args(); args['metadata']['en']['slug']='Invalid Slug'
        with self.assertRaises(SEOQualityError):self.quality.submit(**args)
        args=self.args(); del args['metadata']['hi']
        with self.assertRaises(SEOQualityError):self.quality.submit(**args)

    def test_immutable_independent_reviewer(self):
        args=self.args(); args['reviewer_id']='author'
        with self.assertRaises(SEOQualityError):self.quality.submit(**args)
        self.quality.submit(**self.args())
        args=self.args(); args['metadata']['en']['title']='Changed'
        with self.assertRaises(SEOQualityError):self.quality.submit(**args)
