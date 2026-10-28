import tempfile
import unittest
from pathlib import Path

from studio.factory01_topic_intake import Factory01TopicIntake
from studio.factory01_research_evidence import Factory01ResearchEvidence, ResearchEvidenceError


class ResearchEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.intake = Factory01TopicIntake(Path(self.tmp.name) / 'studio.sqlite')
        self.intake.submit(candidate_id='topic-1', category='Technology & AI',
                           headline='Example topic', submitted_by='editor',
                           source_urls=['https://example.org/one', 'https://example.org/two'])
        self.review = Factory01ResearchEvidence(self.intake)

    def tearDown(self):
        self.intake.close()
        self.tmp.cleanup()

    def record(self, url='https://example.org/one', finding='supports'):
        return self.review.record(candidate_id='topic-1', source_url=url,
                                  reviewer_id='reviewer-1', finding=finding,
                                  evidence_note='Human-reviewed source excerpt and context')

    def test_missing_sources_fail_closed(self):
        report = self.review.evaluate('topic-1')
        self.assertFalse(report['ready_for_separate_editorial_assessment'])
        self.assertEqual(len(report['missing_sources']), 2)
        self.record()
        self.assertFalse(self.review.evaluate('topic-1')['ready_for_separate_editorial_assessment'])

    def test_manual_supports_only_enables_separate_review_not_publication(self):
        self.record()
        result = self.record('https://example.org/two')
        report = self.review.evaluate('topic-1')
        self.assertTrue(report['ready_for_separate_editorial_assessment'])
        self.assertFalse(report['independently_fact_checked'])
        self.assertFalse(result['publication_enabled'])
        self.assertFalse(report['publication_enabled'])

    def test_contradiction_and_inconclusive_are_blocking(self):
        self.record(finding='contradicts')
        self.record('https://example.org/two', finding='inconclusive')
        result = self.review.evaluate('topic-1')
        self.assertEqual(len(result['contradicting_sources']), 1)
        self.assertEqual(len(result['inconclusive_sources']), 1)
        self.assertFalse(result['ready_for_separate_editorial_assessment'])

    def test_immutable_and_unknown_source_rejected(self):
        self.record()
        for kw in [dict(url='https://example.org/one'), dict(url='https://other.org')]:
            with self.assertRaises(ResearchEvidenceError):
                self.record(**kw)
        with self.assertRaises(ResearchEvidenceError):
            self.review.evaluate('does-not-exist')

    def test_persistence_and_invalid_inputs(self):
        self.record()
        self.assertEqual(self.review.evaluate('topic-1')['reviewed_sources'], 1)
        self.intake.close()
        self.intake = Factory01TopicIntake(Path(self.tmp.name) / 'studio.sqlite')
        self.review = Factory01ResearchEvidence(self.intake)
        self.assertEqual(self.review.evaluate('topic-1')['reviewed_sources'], 1)
        with self.assertRaises(ResearchEvidenceError):
            self.review.record(candidate_id='topic-1', source_url='https://example.org/two',
                               reviewer_id=' ', finding='supports', evidence_note='note')
