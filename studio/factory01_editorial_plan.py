"""VM-F01-001: immutable, local-only Factory 01 planning contract.

This module does NOT approve a standing policy, schedule any jobs, invoke AI,
write to the website, or grant publication permissions. A separate explicit
Director action in the existing WorkflowEngine is required for activation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

CATEGORIES = (
    'Technology & AI',
    'Science & Innovation',
    'Business & Industry',
    'Entertainment & Media',
)

@dataclass(frozen=True)
class Factory01Plan:
    policy_id: str = 'voocks-factory-01'
    daily_stories: int = 4
    categories: tuple[str, ...] = CATEGORIES
    stories_per_category: tuple[int, ...] = (1, 1, 1, 1)
    languages: tuple[str, ...] = ('en', 'hi')
    channel: str = 'website'
    publication_mode: str = 'policy_authorized_future'
    automatic_publishing_enabled: bool = False

    def validate(self) -> None:
        if self.daily_stories != 4 or self.categories != CATEGORIES:
            raise ValueError('Factory 01 baseline permits exactly four named categories and stories')
        if (len(self.stories_per_category) != 4 or
            any(type(x) is not int or x != 1 for x in self.stories_per_category) or
            sum(self.stories_per_category) != self.daily_stories):
            raise ValueError('Exactly one story per category per day is required')
        if self.languages != ('en', 'hi'):
            raise ValueError('English and Hindi variants must both be planned')
        if self.channel != 'website' or self.publication_mode != 'policy_authorized_future':
            raise ValueError('Unsupported channel or policy mode')
        if self.automatic_publishing_enabled is not False:
            raise ValueError('Automatic publishing cannot be enabled by this plan')
        if self.policy_id != 'voocks-factory-01':
            raise ValueError('Factory policy ID differs from baseline')

    def as_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            'schema_version': 1,
            'policy_id': self.policy_id,
            'daily_story_target': self.daily_stories,
            'category_daily_quota': dict(zip(self.categories, self.stories_per_category)),
            'story_languages': list(self.languages),
            'max_language_variants_per_day': 8,
            'target_channel': self.channel,
            'publication_mode': self.publication_mode,
            'automatic_publishing_enabled': False,
            'status': 'planning_only_not_approved',
            'source_standards': {
                'require_source_references': True,
                'require_factual_evidence_review': True,
                'block_contradictory_or_inconclusive_evidence': True,
                'require_asset_rights_review': True,
                'require_english_hindi_consistency_review': True,
                'no_forced_publication_if_unverified': True,
            },
        }

    def workflow_policy_draft(self) -> dict[str, Any]:
        """Parameters for an EXPLICIT director-led WorkflowEngine.create_policy call.

        Excludes all publish rules and channels to prevent unintentional grant.
        Existing WorkflowEngine source standards remain separate manual policy inputs.
        """
        self.validate()
        return {
            'policy_id': self.policy_id,
            'categories': list(self.categories),
            'content_types': ['news_article'],
            'source_policies': [
                'primary_source_preferred', 'independent_review_required',
                'asset_rights_check_required', 'bilingual_consistency_review',
            ],
            'allowed_channels': ['private_preview'],
            'prohibited_topics': ['unverified_claims'],
            'routine_publish_rules': [],
        }

DEFAULT_FACTORY01_PLAN = Factory01Plan()
