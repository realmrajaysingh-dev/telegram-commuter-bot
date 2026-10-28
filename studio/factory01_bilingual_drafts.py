"""VM-F01-004: private, human-supplied English/Hindi paired draft storage.

Evidence readiness is only a prerequisite to *drafting*, not a fact-check or
publication authorization. No translator, AI provider, network or CMS calls.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import sqlite3

from .factory01_research_evidence import Factory01ResearchEvidence
from .factory01_topic_intake import _ID


class BilingualDraftError(ValueError):
    pass


class Factory01BilingualDrafts:
    def __init__(self, evidence: Factory01ResearchEvidence):
        self.evidence = evidence
        self.db = evidence.db
        with self.db:
            self.db.execute('''CREATE TABLE IF NOT EXISTS studio_factory01_bilingual_drafts (
                candidate_id TEXT PRIMARY KEY REFERENCES studio_factory01_topics(candidate_id),
                english_markdown TEXT NOT NULL,
                hindi_markdown TEXT NOT NULL,
                english_sha256 TEXT NOT NULL,
                hindi_sha256 TEXT NOT NULL,
                submitted_by TEXT NOT NULL,
                submitted_at TEXT NOT NULL
            )''')

    @staticmethod
    def _valid_body(body: object, label: str) -> str:
        if not isinstance(body, str) or not body.strip() or len(body.encode('utf-8')) > 250_000:
            raise BilingualDraftError(f'{label} must be nonempty Markdown no larger than 250KB')
        return body

    def _evidence_ready(self, candidate_id: str) -> None:
        if not isinstance(candidate_id, str) or not _ID.fullmatch(candidate_id):
            raise BilingualDraftError('Invalid candidate ID')
        report = self.evidence.evaluate(candidate_id)
        if not report['ready_for_separate_editorial_assessment']:
            raise BilingualDraftError('Research evidence incomplete or blocked; drafting not allowed')

    def submit(self, *, candidate_id: str, english_markdown: str,
               hindi_markdown: str, submitted_by: str) -> dict:
        english = self._valid_body(english_markdown, 'English draft')
        hindi = self._valid_body(hindi_markdown, 'Hindi draft')
        if not isinstance(submitted_by, str) or not submitted_by.strip() or len(submitted_by.strip()) > 120:
            raise BilingualDraftError('Named submitter required')
        self._evidence_ready(candidate_id)
        en_hash = hashlib.sha256(english.encode('utf-8')).hexdigest()
        hi_hash = hashlib.sha256(hindi.encode('utf-8')).hexdigest()
        with self.db:
            existing = self.db.execute('SELECT * FROM studio_factory01_bilingual_drafts WHERE candidate_id=?',
                                       (candidate_id,)).fetchone()
            if existing:
                if (existing['english_markdown'] != english or existing['hindi_markdown'] != hindi or
                        existing['submitted_by'] != submitted_by.strip()):
                    raise BilingualDraftError('Draft pair immutable; use a new candidate ID for revision')
                return self.get(candidate_id)
            self.db.execute('''INSERT INTO studio_factory01_bilingual_drafts
                (candidate_id,english_markdown,hindi_markdown,english_sha256,hindi_sha256,submitted_by,submitted_at)
                VALUES (?,?,?,?,?,?,?)''', (candidate_id,english,hindi,en_hash,hi_hash,
                    submitted_by.strip(),datetime.now(timezone.utc).isoformat()))
        return self.get(candidate_id)

    def get(self, candidate_id: str) -> dict:
        self._evidence_ready(candidate_id)
        row = self.db.execute('SELECT * FROM studio_factory01_bilingual_drafts WHERE candidate_id=?',
                              (candidate_id,)).fetchone()
        if row is None:
            raise BilingualDraftError('No bilingual drafts for candidate')
        for lang, col in [('en','english'), ('hi','hindi')]:
            if hashlib.sha256(row[f'{col}_markdown'].encode('utf-8')).hexdigest() != row[f'{col}_sha256']:
                raise BilingualDraftError(f'{lang} draft integrity failed')
        return {'candidate_id': candidate_id,
                'versions': {'en': {'markdown':row['english_markdown'], 'sha256':row['english_sha256'], 'version':1},
                             'hi': {'markdown':row['hindi_markdown'], 'sha256':row['hindi_sha256'], 'version':1}},
                'submitted_by':row['submitted_by'],'submitted_at':row['submitted_at'],
                'status':'private_drafts_pending_independent_editorial_review',
                'translation_verified':False,'independently_fact_checked':False,
                'ai_generation_performed':False,'publishing_enabled':False}
