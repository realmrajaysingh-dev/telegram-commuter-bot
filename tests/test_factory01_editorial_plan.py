import unittest
from dataclasses import replace
from studio.factory01_editorial_plan import DEFAULT_FACTORY01_PLAN


class Factory01EditorialPlanTests(unittest.TestCase):
    def test_daily_categories_and_bilingual_story_counts(self):
        cfg = DEFAULT_FACTORY01_PLAN.as_dict()
        self.assertEqual(cfg['daily_story_target'], 4)
        self.assertEqual(len(cfg['category_daily_quota']), 4)
        self.assertTrue(all(x == 1 for x in cfg['category_daily_quota'].values()))
        self.assertEqual(cfg['story_languages'], ['en', 'hi'])
        self.assertEqual(cfg['max_language_variants_per_day'], 8)

    def test_no_permission_to_publish(self):
        cfg = DEFAULT_FACTORY01_PLAN.as_dict()
        draft = DEFAULT_FACTORY01_PLAN.workflow_policy_draft()
        self.assertFalse(cfg['automatic_publishing_enabled'])
        self.assertEqual(cfg['status'], 'planning_only_not_approved')
        self.assertEqual(draft['routine_publish_rules'], [])
        self.assertEqual(draft['allowed_channels'], ['private_preview'])

    def test_mutated_quotas_and_permissions_rejected(self):
        for variant in (
            replace(DEFAULT_FACTORY01_PLAN, stories_per_category=(2, 1, 1, 1)),
            replace(DEFAULT_FACTORY01_PLAN, languages=('en',)),
            replace(DEFAULT_FACTORY01_PLAN, automatic_publishing_enabled=True),
        ):
            with self.subTest(variant=variant), self.assertRaises(ValueError):
                variant.as_dict()

    def test_source_and_rights_standards_explicit(self):
        checks = DEFAULT_FACTORY01_PLAN.as_dict()['source_standards']
        self.assertTrue(all(checks.values()))
