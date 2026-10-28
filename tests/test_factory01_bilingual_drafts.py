import tempfile
import unittest
from pathlib import Path

from studio.factory01_topic_intake import Factory01TopicIntake
from studio.factory01_research_evidence import Factory01ResearchEvidence
from studio.factory01_bilingual_drafts import Factory01BilingualDrafts, BilingualDraftError


class BilingualDraftTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'studio.sqlite'
        self.intake = Factory01TopicIntake(self.path)
        self.intake.submit(candidate_id='story-1', category='Technology & AI',
                           headline='Research item', source_urls=['https://example.org/source'],
                           submitted_by='editor')
        self.evidence = Factory01ResearchEvidence(self.intake)
        self.drafts = Factory01BilingualDrafts(self.evidence)

    def tearDown(self):
        self.intake.close()
        self.tmp.cleanup()

    def attestation(self, finding='supports'):
        self.evidence.record(candidate_id='story-1',source_url='https://example.org/source',
                             reviewer_id='human-reviewer',finding=finding,
                             evidence_note='Human source assessment only')

    def create(self, **kwargs):
        args=dict(candidate_id='story-1', english_markdown='# Original English',
                  hindi_markdown='# मूल हिंदी', submitted_by='writer-1')
        args.update(kwargs)
        return self.drafts.submit(**args)

    def test_blocked_without_complete_supporting_evidence(self):
        with self.assertRaises(BilingualDraftError):
            self.create()
        self.attestation('inconclusive')
        with self.assertRaises(BilingualDraftError):
            self.create()

    def test_private_bilingual_pair_and_integrity(self):
        self.attestation()
        record=self.create()
        self.assertEqual(set(record['versions']), {'en','hi'})
        self.assertEqual(record['versions']['hi']['version'],1)
        self.assertFalse(record['translation_verified'])
        self.assertFalse(record['independently_fact_checked'])
        self.assertFalse(record['ai_generation_performed'])
        self.assertFalse(record['publishing_enabled'])
        self.assertEqual(self.create(),record)

    def test_immutable_pair_and_missing_language_rejected(self):
        self.attestation()
        self.create()
        with self.assertRaises(BilingualDraftError):
            self.create(hindi_markdown='Changed content')
        with self.assertRaises(BilingualDraftError):
            self.create(english_markdown=' ')
        with self.assertRaises(BilingualDraftError):
            self.create(submitted_by=' ')

    def test_persistence_and_tamper_fail_closed(self):
        self.attestation()
        self.create()
        self.intake.close()
        self.intake=Factory01TopicIntake(self.path)
        self.evidence=Factory01ResearchEvidence(self.intake)
        self.drafts=Factory01BilingualDrafts(self.evidence)
        self.assertIn('मूल',self.drafts.get('story-1')['versions']['hi']['markdown'])
        self.intake.db.execute("UPDATE studio_factory01_bilingual_drafts SET hindi_markdown='tampered' WHERE candidate_id='story-1'")
        self.intake.db.commit()
        with self.assertRaises(BilingualDraftError):
            self.drafts.get('story-1')

    def test_unknown_candidate_and_large_draft_rejected(self):
        self.attestation()
        with self.assertRaises(Exception):
            self.create(candidate_id='missing')
        with self.assertRaises(BilingualDraftError):
            self.create(english_markdown='x'*250001)
