import tempfile
import unittest
from pathlib import Path
from studio.factory01_topic_intake import Factory01TopicIntake, TopicIntakeError


class Factory01TopicIntakeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'studio.sqlite'
        self.intake = Factory01TopicIntake(self.path)

    def tearDown(self):
        self.intake.close()
        self.tmp.cleanup()

    def proposal(self, **changes):
        data = {'candidate_id':'candidate-001','category':'Technology & AI',
                'headline':'Example subject pending independent review',
                'source_urls':['https://example.org/research'], 'submitted_by':'editor-1'}
        data.update(changes)
        return self.intake.submit(**data)

    def test_persistent_private_candidate_and_no_execution_claims(self):
        item = self.proposal()
        self.assertEqual(item['status'],'private_unverified')
        self.assertIsNone(item['trending_rank'])
        self.assertFalse(item['research_performed'])
        self.assertFalse(item['publishing_enabled'])
        self.assertEqual(self.intake.list_private('Technology & AI')[0]['candidate_id'],'candidate-001')
        self.intake.close()
        self.intake = Factory01TopicIntake(self.path)
        self.assertEqual(self.intake.get('candidate-001')['headline'],item['headline'])

    def test_idempotent_replay_and_conflicting_id_rejected(self):
        first = self.proposal()
        self.assertEqual(first,self.proposal())
        with self.assertRaises(TopicIntakeError):
            self.proposal(headline='Changed subject')
        self.assertEqual(len(self.intake.list_private()),1)

    def test_rejects_unapproved_category_bad_urls_and_duplicate_references(self):
        cases = [dict(category='Politics'), dict(source_urls=['http://example.org']),
                 dict(source_urls=['https://localhost/a']),
                 dict(source_urls=['https://192.168.1.4/a']),
                 dict(source_urls=['https://example.org/a']*2),
                 dict(source_urls=[])]
        for case in cases:
            with self.subTest(case=case),self.assertRaises(TopicIntakeError):
                self.proposal(**case)
        self.assertEqual(self.intake.list_private(),[])

    def test_requires_named_submitter_and_valid_ids(self):
        with self.assertRaises(TopicIntakeError):
            self.proposal(submitted_by=' ')
        with self.assertRaises(TopicIntakeError):
            self.proposal(candidate_id='../bad')
        with self.assertRaises(TopicIntakeError):
            self.intake.get('../bad')
