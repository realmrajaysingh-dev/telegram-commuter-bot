import hashlib
import tempfile
import unittest
from pathlib import Path
from studio.factory01_topic_intake import Factory01TopicIntake
from studio.factory01_research_evidence import Factory01ResearchEvidence
from studio.factory01_bilingual_drafts import Factory01BilingualDrafts
from studio.factory01_seo_quality import Factory01SEOQuality
from studio.factory01_release_prep import Factory01ReleasePreparation, ReleasePreparationError


class PrivateReleasePreparationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.intake = Factory01TopicIntake(Path(self.tmp.name) / 'studio.sqlite')
        self.intake.submit(candidate_id='topic-a', category='Technology & AI',
            headline='Sample', source_urls=['https://example.org/research'], submitted_by='reporter')
        evidence = Factory01ResearchEvidence(self.intake)
        evidence.record(candidate_id='topic-a', source_url='https://example.org/research',
                        reviewer_id='researcher', finding='supports', evidence_note='Reviewed manually')
        drafts = Factory01BilingualDrafts(evidence)
        drafts.submit(candidate_id='topic-a', english_markdown='# English',
                      hindi_markdown='# हिन्दी', submitted_by='author')
        self.quality = Factory01SEOQuality(drafts)
        self.metadata = {'en': {'title':'English','description':'Example summary','slug':'english-example'},
                         'hi': {'title':'हिन्दी','description':'हिन्दी सारांश','slug':'hindi-example'}}
        self.checks = {check: True for check in self.quality.CHECKS}
        self.quality.submit(candidate_id='topic-a',metadata=self.metadata,
                            checks=self.checks,reviewer_id='editor')
        self.release = Factory01ReleasePreparation(self.quality)

    def tearDown(self):
        self.intake.close()
        self.tmp.cleanup()

    def prepare(self, **kw):
        args={'candidate_id':'topic-a','website_origin':'https://example.com','prepared_by':'director'}
        args.update(kw)
        return self.release.prepare(**args)

    def test_bilingual_private_manifest_and_no_delivery(self):
        result=self.prepare()
        self.assertEqual(set(result['package']['variants']), {'en','hi'})
        self.assertEqual(result['package']['variants']['hi']['proposed_path'], '/hi/hindi-example')
        self.assertFalse(result['publication_authorized'])
        self.assertFalse(result['delivery_performed'])
        self.assertTrue(result['private_only'])

    def test_idempotency_and_conflict(self):
        first=self.prepare()
        self.assertEqual(first,self.prepare())
        with self.assertRaises(ReleasePreparationError):
            self.prepare(website_origin='https://different.example')

    def test_destination_restrictions(self):
        for origin in ['http://example.com','https://localhost','https://example.com/a',
                       'https://example.com?x=y','https://name:secret@example.com']:
            with self.subTest(origin=origin),self.assertRaises(ReleasePreparationError):
                self.prepare(website_origin=origin)

    def test_missing_quality_blocks(self):
        self.intake.submit(candidate_id='topic-b',category='Science & Innovation',
            headline='Other',source_urls=['https://example.org/b'],submitted_by='editor')
        with self.assertRaises(ReleasePreparationError):
            self.release.prepare(candidate_id='topic-b', website_origin='https://example.com',prepared_by='director')

    def test_tamper_detection(self):
        self.prepare()
        with self.intake.db:
            self.intake.db.execute('''UPDATE studio_factory01_private_release_prep
                SET package_sha256=? WHERE candidate_id=?''', ('0'*64,'topic-a'))
        with self.assertRaises(ReleasePreparationError):
            self.release.get('topic-a')
