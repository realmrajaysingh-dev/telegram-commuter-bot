"""VM-F01-006: immutable private, bilingual website-release preparation.

A prepared bundle is NOT authorization to publish. This module never contacts a
website, schedules delivery, or creates a live publishing permission.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import sqlite3
from urllib.parse import urlsplit

from .factory01_seo_quality import Factory01SEOQuality


class ReleasePreparationError(ValueError):
    pass


class Factory01ReleasePreparation:
    def __init__(self, quality: Factory01SEOQuality):
        self.quality = quality
        self.db = quality.db
        with self.db:
            self.db.execute('''CREATE TABLE IF NOT EXISTS studio_factory01_private_release_prep (
                candidate_id TEXT PRIMARY KEY REFERENCES studio_factory01_bilingual_drafts(candidate_id),
                destination TEXT NOT NULL,
                package_json TEXT NOT NULL,
                package_sha256 TEXT NOT NULL,
                prepared_by TEXT NOT NULL,
                prepared_at TEXT NOT NULL
            )''')

    @staticmethod
    def _destination(value: object) -> str:
        if not isinstance(value, str) or len(value) > 2048 or value.strip() != value:
            raise ReleasePreparationError('Valid HTTPS website origin required')
        try:
            parsed = urlsplit(value)
            valid = (parsed.scheme == 'https' and bool(parsed.hostname)
                     and parsed.username is None and parsed.password is None
                     and parsed.port in (None, 443) and not parsed.path
                     and not parsed.query and not parsed.fragment
                     and parsed.hostname not in ('localhost',)
                     and not parsed.hostname.endswith(('.local', '.internal')))
        except ValueError:
            valid = False
        if not valid:
            raise ReleasePreparationError('Destination must be a bare HTTPS website origin')
        return value.rstrip('/')

    def prepare(self, *, candidate_id: str, website_origin: str, prepared_by: str) -> dict:
        if not isinstance(prepared_by, str) or not prepared_by.strip() or len(prepared_by.strip()) > 120:
            raise ReleasePreparationError('Named preparer required')
        origin = self._destination(website_origin)
        try:
            review = self.quality.evaluate(candidate_id)
        except ValueError as exc:
            raise ReleasePreparationError('Research or bilingual drafts not eligible') from exc
        if not review['ready_for_next_private_gate']:
            raise ReleasePreparationError('Passing, hash-matching editorial record required')
        drafts = self.quality.drafts.get(candidate_id)
        topic = self.quality.drafts.evidence.intake.get(candidate_id)
        bundle = {
            'schema_version': 1,
            'candidate_id': candidate_id,
            'category': topic['category'],
            'target_website_origin': origin,
            'variants': {
                language: {
                    'draft_sha256': drafts['versions'][language]['sha256'],
                    'slug': review['metadata'][language]['slug'],
                    'title': review['metadata'][language]['title'],
                    'description': review['metadata'][language]['description'],
                    'proposed_path': '/' + language + '/' + review['metadata'][language]['slug'],
                } for language in ('en', 'hi')
            },
            'source_urls': topic['source_urls'],
            'editorial_reviewer': review['reviewer_id'],
            'editorial_reviewed_at': review['reviewed_at'],
            'status': 'private_prepared_pending_separate_release_authorization',
            'independently_fact_checked': False,
            'public_publishing_enabled': False,
            'delivery_performed': False,
        }
        canonical = json.dumps(bundle, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
        digest = hashlib.sha256(canonical.encode('utf-8')).hexdigest()
        with self.db:
            existing = self.db.execute('''SELECT package_json, package_sha256, destination, prepared_by
                FROM studio_factory01_private_release_prep WHERE candidate_id=?''', (candidate_id,)).fetchone()
            if existing:
                if (existing['package_json'] != canonical or existing['package_sha256'] != digest
                        or existing['destination'] != origin or existing['prepared_by'] != prepared_by.strip()):
                    raise ReleasePreparationError('Immutable release package conflict')
            else:
                try:
                    self.db.execute('''INSERT INTO studio_factory01_private_release_prep
                        (candidate_id,destination,package_json,package_sha256,prepared_by,prepared_at)
                        VALUES(?,?,?,?,?,?)''',
                        (candidate_id, origin, canonical, digest, prepared_by.strip(),
                         datetime.now(timezone.utc).isoformat()))
                except sqlite3.IntegrityError as exc:
                    raise ReleasePreparationError('Duplicate private package') from exc
        return self.get(candidate_id)

    def get(self, candidate_id: str) -> dict:
        try:
            review = self.quality.evaluate(candidate_id)
        except ValueError as exc:
            raise ReleasePreparationError('Research or bilingual drafts not eligible') from exc
        if not review['ready_for_next_private_gate']:
            raise ReleasePreparationError('Editorial review no longer eligible')
        record = self.db.execute('''SELECT * FROM studio_factory01_private_release_prep
            WHERE candidate_id=?''', (candidate_id,)).fetchone()
        if record is None:
            raise ReleasePreparationError('Private release package not prepared')
        canonical = record['package_json']
        digest = hashlib.sha256(canonical.encode('utf-8')).hexdigest()
        if digest != record['package_sha256']:
            raise ReleasePreparationError('Release package integrity failed')
        bundle = json.loads(canonical)
        drafts = self.quality.drafts.get(candidate_id)
        for language in ('en', 'hi'):
            if (bundle['variants'][language]['draft_sha256'] != drafts['versions'][language]['sha256']
                    or bundle['variants'][language]['slug'] != review['metadata'][language]['slug']):
                raise ReleasePreparationError('Source drafts or metadata changed')
        return {'package': bundle, 'package_sha256': digest, 'prepared_by': record['prepared_by'],
                'prepared_at': record['prepared_at'], 'private_only': True,
                'publication_authorized': False, 'delivery_performed': False}
