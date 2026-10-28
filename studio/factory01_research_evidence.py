"""VM-F01-003: local human-attested research evidence for private topic candidates.

Records are NOT web fetches, automated fact checks, or permission to publish.
"""
from __future__ import annotations

from datetime import datetime, timezone
import sqlite3
from typing import Any

from .factory01_topic_intake import Factory01TopicIntake, TopicIntakeError, _ID


class ResearchEvidenceError(ValueError):
    pass


class Factory01ResearchEvidence:
    FINDINGS = frozenset({'supports', 'contradicts', 'inconclusive'})

    def __init__(self, intake: Factory01TopicIntake):
        self.intake = intake
        self.db = intake.db
        with self.db:
            self.db.execute('''CREATE TABLE IF NOT EXISTS studio_factory01_research_evidence (
                candidate_id TEXT NOT NULL REFERENCES studio_factory01_topics(candidate_id),
                source_url TEXT NOT NULL,
                reviewer_id TEXT NOT NULL,
                finding TEXT NOT NULL CHECK(finding IN ('supports','contradicts','inconclusive')),
                evidence_note TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                PRIMARY KEY(candidate_id, source_url)
            )''')

    def record(self, *, candidate_id: str, source_url: str, reviewer_id: str,
               finding: str, evidence_note: str) -> dict[str, Any]:
        if not isinstance(candidate_id, str) or not _ID.fullmatch(candidate_id):
            raise ResearchEvidenceError('Invalid candidate ID')
        if not isinstance(source_url, str):
            raise ResearchEvidenceError('Source URL required')
        if not isinstance(reviewer_id, str) or not reviewer_id.strip() or len(reviewer_id) > 120:
            raise ResearchEvidenceError('Named reviewer required')
        if finding not in self.FINDINGS:
            raise ResearchEvidenceError('Invalid research finding')
        if (not isinstance(evidence_note, str) or not evidence_note.strip()
                or len(evidence_note.strip()) > 2000):
            raise ResearchEvidenceError('Evidence note must be 1-2000 characters')
        try:
            topic = self.intake.get(candidate_id)
        except TopicIntakeError as exc:
            raise ResearchEvidenceError(str(exc)) from exc
        if source_url not in topic['source_urls']:
            raise ResearchEvidenceError('Source must be declared on this candidate')
        with self.db:
            existing = self.db.execute('''SELECT 1 FROM studio_factory01_research_evidence
                    WHERE candidate_id=? AND source_url=?''', (candidate_id, source_url)).fetchone()
            if existing:
                raise ResearchEvidenceError('Evidence is immutable; create a new topic candidate for revision')
            observed = datetime.now(timezone.utc).isoformat()
            self.db.execute('''INSERT INTO studio_factory01_research_evidence
                (candidate_id,source_url,reviewer_id,finding,evidence_note,observed_at)
                VALUES (?,?,?,?,?,?)''', (candidate_id,source_url,reviewer_id.strip(),
                    finding,evidence_note.strip(),observed))
        return {'candidate_id': candidate_id, 'source_url': source_url, 'finding': finding,
                'reviewer_id': reviewer_id.strip(), 'observed_at': observed,
                'human_attested_only': True, 'independently_fact_checked': False,
                'publication_enabled': False}

    def evaluate(self, candidate_id: str) -> dict[str, Any]:
        try:
            topic = self.intake.get(candidate_id)
        except TopicIntakeError as exc:
            raise ResearchEvidenceError(str(exc)) from exc
        rows = self.db.execute('''SELECT source_url,finding,reviewer_id,evidence_note,observed_at
            FROM studio_factory01_research_evidence WHERE candidate_id=? ORDER BY source_url''',
            (candidate_id,)).fetchall()
        findings = {row['source_url']: row['finding'] for row in rows}
        missing = [url for url in topic['source_urls'] if url not in findings]
        contradicting = [url for url in topic['source_urls'] if findings.get(url) == 'contradicts']
        inconclusive = [url for url in topic['source_urls'] if findings.get(url) == 'inconclusive']
        ready = not missing and not contradicting and not inconclusive and bool(rows)
        return {'candidate_id': candidate_id, 'reviewed_sources': len(rows),
                'total_sources': len(topic['source_urls']), 'missing_sources': missing,
                'contradicting_sources': contradicting, 'inconclusive_sources': inconclusive,
                'ready_for_separate_editorial_assessment': ready,
                'status': 'manual_evidence_recorded_not_fact_checked' if ready else 'blocked_evidence_review',
                'source_findings': [dict(row) for row in rows],
                'independently_fact_checked': False, 'research_performed_by_system': False,
                'publication_enabled': False}
