"""09.3-B: evidence intake and fail-closed editorial review for private articles.

This is a *manual evidence ledger*, not an online fact checker. Storing a URL,
review, or passing local checks never authorizes public delivery.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json

from .private_article_intake_093 import PrivateArticleDraftIntake
from .workflows import WorkflowError, _ID


class ArticleEditorialGate:
    VALID_FINDINGS = frozenset({'supports', 'contradicts', 'inconclusive'})

    def __init__(self, intake: PrivateArticleDraftIntake):
        self.intake = intake
        self.db = intake.db
        self.lock = intake.lock
        with self.lock, self.db:
            self.db.execute('''CREATE TABLE IF NOT EXISTS studio_article_source_reviews (
                draft_id TEXT NOT NULL REFERENCES studio_private_article_drafts(draft_id),
                source_url TEXT NOT NULL,
                reviewer_id TEXT NOT NULL,
                finding TEXT NOT NULL,
                evidence_note TEXT NOT NULL,
                draft_sha256 TEXT NOT NULL,
                recorded_at TEXT NOT NULL,
                PRIMARY KEY (draft_id, source_url)
            )''')

    def record_source_review(self, *, draft_id: str, source_url: str, reviewer_id: str,
                             finding: str, evidence_note: str) -> dict:
        """Record an explicitly human-attested source finding; no external fetch."""
        if not isinstance(draft_id, str) or not _ID.fullmatch(draft_id):
            raise WorkflowError('Invalid draft ID')
        if not isinstance(reviewer_id, str) or reviewer_id not in self.intake.bridge.allocation.director_ids:
            raise WorkflowError('Director reviewer identity required')
        if finding not in self.VALID_FINDINGS:
            raise WorkflowError('Invalid evidence finding')
        if not isinstance(evidence_note, str) or not evidence_note.strip() or len(evidence_note) > 2000:
            raise WorkflowError('Nonempty evidence note required (max 2000 characters)')
        with self.lock, self.db:
            draft = self.intake.get(draft_id)
            if source_url not in draft['source_urls']:
                raise WorkflowError('Source is not declared in the draft')
            if self.db.execute('SELECT 1 FROM studio_article_source_reviews WHERE draft_id=? AND source_url=?',
                               (draft_id, source_url)).fetchone():
                raise WorkflowError('Source review is immutable; new review requires new draft/version')
            timestamp = datetime.now(timezone.utc).isoformat()
            self.db.execute('''INSERT INTO studio_article_source_reviews
                (draft_id,source_url,reviewer_id,finding,evidence_note,draft_sha256,recorded_at)
                VALUES(?,?,?,?,?,?,?)''',
                (draft_id, source_url, reviewer_id, finding, evidence_note.strip(),
                 draft['body_sha256'], timestamp))
            return {'draft_id': draft_id, 'source_url': source_url, 'finding': finding,
                    'reviewer_id': reviewer_id, 'publication_enabled': False}

    def evaluate(self, draft_id: str) -> dict:
        """Descriptive review readiness only; NOT verification of external truth."""
        with self.lock:
            draft = self.intake.get(draft_id)
            rows = self.db.execute('''SELECT source_url,finding,draft_sha256 FROM studio_article_source_reviews
                                      WHERE draft_id=?''', (draft_id,)).fetchall()
            findings = {r['source_url']: r for r in rows}
            stale = any(r['draft_sha256'] != draft['body_sha256'] for r in rows)
            unreviewed = [url for url in draft['source_urls'] if url not in findings]
            contradictions = [url for url, r in findings.items() if r['finding'] == 'contradicts']
            inconclusive = [url for url, r in findings.items() if r['finding'] == 'inconclusive']
            supported = sum(r['finding'] == 'supports' for r in rows)
            ready_for_separate_editorial_review = (
                not stale and not unreviewed and not contradictions and not inconclusive and supported > 0
            )
            return {'draft_id': draft_id, 'draft_sha256': draft['body_sha256'],
                    'total_sources': len(draft['source_urls']), 'reviewed_sources': len(rows),
                    'unreviewed_sources': unreviewed, 'contradicting_sources': contradictions,
                    'inconclusive_sources': inconclusive, 'stale_evidence': stale,
                    'ready_for_editorial_review': ready_for_separate_editorial_review,
                    'fact_checked': False, 'editorially_approved': False,
                    'publication_enabled': False, 'execution_performed': False,
                    'status': ('review_evidence_complete_not_approved'
                               if ready_for_separate_editorial_review else 'blocked_source_review')}
