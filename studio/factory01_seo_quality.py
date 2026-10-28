# VM-F01-005: private SEO metadata and human-attested bilingual editorial gate.

 # This module stores editorial assessments only; automatic translation, SEO ranking,
# independent fact-checking, rights verification and publication are NOT performed.
from __future__ import annotations
from datetime import datetime, timezone
import hashlib
import json
import re

from .factory01_bilingual_drafts import Factory01BilingualDrafts


class SEOQualityError(ValueError):
    pass


class Factory01SEOQuality:
    CHECKS = ('clarity', 'originality', 'source_attribution', 'bilingual_consistency',
              'rights_review', 'privacy_review')

    def __init__(self, drafts: Factory01BilingualDrafts):
        self.drafts = drafts
        self.db = drafts.db
        with self.db:
            self.db.execute("""CREATE TABLE IF NOT EXISTS studio_factory01_seo_quality (
                candidate_id TEXT PRIMARY KEY REFERENCES studio_factory01_bilingual_drafts(candidate_id),
                en_hash TEXT NOT NULL, hi_hash TEXT NOT NULL,
                metadata_json TEXT NOT NULL, checks_json TEXT NOT NULL,
                reviewer_id TEXT NOT NULL, reviewed_at TEXT NOT NULL
            )""")

    @staticmethod
    def _short(value: object, label: str, maximum: int) -> str:
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum or '<' in value or '>' in value:
            raise SEOQualityError(f'{label} must be nonempty plain text up to {maximum} characters')
        return value.strip()

    def submit(self, *, candidate_id: str, metadata: dict, checks: dict,
               reviewer_id: str) -> dict:
        draft = self.drafts.get(candidate_id)
        if not isinstance(metadata, dict) or set(metadata) != {'en','hi'}:
            raise SEOQualityError('Both languages require SEO metadata')
        clean = {}
        for lang in ('en','hi'):
            entry = metadata[lang]
            if not isinstance(entry, dict) or set(entry) != {'title','description','slug'}:
                raise SEOQualityError('Title, description and slug required')
            slug = self._short(entry['slug'], 'Slug', 100)
            if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', slug):
                raise SEOQualityError('Slug must be lowercase ASCII words with hyphens')
            clean[lang] = {'title':self._short(entry['title'],'Title',90),
                           'description':self._short(entry['description'],'Description',220),
                           'slug':slug}
        if clean['en']['slug'] == clean['hi']['slug']:
            raise SEOQualityError('Language-specific slugs must differ')
        if not isinstance(checks, dict) or set(checks) != set(self.CHECKS) or any(type(v) is not bool for v in checks.values()):
            raise SEOQualityError('All explicit editorial checks must be booleans')
        reviewer = self._short(reviewer_id,'Reviewer',120)
        if reviewer == draft['submitted_by']:
            raise SEOQualityError('Editorial reviewer must differ from draft submitter')
        hashes = {lang:draft['versions'][lang]['sha256'] for lang in ('en','hi')}
        with self.db:
            previous = self.db.execute('SELECT * FROM studio_factory01_seo_quality WHERE candidate_id=?', (candidate_id,)).fetchone()
            if previous:
                if (previous['en_hash'] != hashes['en'] or previous['hi_hash'] != hashes['hi']
                    or json.loads(previous['metadata_json']) != clean
                    or json.loads(previous['checks_json']) != checks or previous['reviewer_id'] != reviewer):
                    raise SEOQualityError('Editorial record immutable; create new candidate for corrections')
                return self.evaluate(candidate_id)
            self.db.execute("""INSERT INTO studio_factory01_seo_quality
                (candidate_id,en_hash,hi_hash,metadata_json,checks_json,reviewer_id,reviewed_at)
                VALUES (?,?,?,?,?,?,?)""", (candidate_id,hashes['en'],hashes['hi'],
                    json.dumps(clean,ensure_ascii=False,sort_keys=True),json.dumps(checks,sort_keys=True),
                    reviewer,datetime.now(timezone.utc).isoformat()))
        return self.evaluate(candidate_id)

    def evaluate(self, candidate_id: str) -> dict:
        draft = self.drafts.get(candidate_id)
        row = self.db.execute('SELECT * FROM studio_factory01_seo_quality WHERE candidate_id=?', (candidate_id,)).fetchone()
        if row is None:
            return {'candidate_id':candidate_id,'status':'missing_editorial_review',
                    'ready_for_next_private_gate':False,'publishing_enabled':False}
        hashes_match = all(row[lang+'_hash'] == draft['versions'][lang]['sha256'] for lang in ('en','hi'))
        checks = json.loads(row['checks_json'])
        ready = hashes_match and all(checks.values())
        return {'candidate_id':candidate_id,'metadata':json.loads(row['metadata_json']),
                'checks':checks,'reviewer_id':row['reviewer_id'],'reviewed_at':row['reviewed_at'],
                'draft_hashes_match':hashes_match,'ready_for_next_private_gate':ready,
                'status':'human_editorial_checks_recorded_not_independently_verified' if ready else 'blocked_editorial_quality',
                'seo_ranking_verified':False,'translation_verified_by_system':False,
                'independently_fact_checked':False,'publishing_enabled':False}
