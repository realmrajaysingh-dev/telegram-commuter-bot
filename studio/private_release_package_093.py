"""Session 09.3-C: version-bound, private editorial decision and release *candidate*.

No public release API, scheduler, publishing permission, or factual verification.
The candidate is an internal handoff only; source findings are human assertions.
"""
from __future__ import annotations
from datetime import datetime, timezone
import hashlib
import json
from .article_editorial_gate_093 import ArticleEditorialGate
from .workflows import WorkflowError, _ID


class PrivateArticleReleaseCandidate:
    def __init__(self, gate: ArticleEditorialGate):
        self.gate = gate
        self.intake = gate.intake
        self.db = gate.db
        self.lock = gate.lock
        with self.lock, self.db:
            self.db.execute('''CREATE TABLE IF NOT EXISTS studio_private_article_release_candidates (
                draft_id TEXT PRIMARY KEY REFERENCES studio_private_article_drafts(draft_id),
                body_sha256 TEXT NOT NULL,
                reviewer_id TEXT NOT NULL,
                editorial_note TEXT NOT NULL,
                source_snapshot_sha256 TEXT NOT NULL,
                package_sha256 TEXT NOT NULL,
                candidate_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )''')

    def prepare(self, *, draft_id: str, reviewer_id: str, editorial_note: str) -> dict:
        """Create an immutable *private* package after source review and explicit editorial decision.

        A package is not human authorization to publish. It cannot be edited in place.
        """
        if not isinstance(draft_id, str) or not _ID.fullmatch(draft_id):
            raise WorkflowError('Invalid draft ID')
        if not isinstance(reviewer_id, str) or reviewer_id not in self.intake.bridge.allocation.director_ids:
            raise WorkflowError('Director editorial reviewer identity required')
        if not isinstance(editorial_note, str) or not editorial_note.strip() or len(editorial_note) > 2000:
            raise WorkflowError('Explicit editorial decision note required')
        with self.lock, self.db:
            draft = self.intake.get(draft_id)
            status = self.gate.evaluate(draft_id)
            if not status['ready_for_editorial_review']:
                raise WorkflowError('Source review incomplete or blocked; editorial package prohibited')
            reviews = self.db.execute('''SELECT source_url, reviewer_id, finding, evidence_note, draft_sha256
                FROM studio_article_source_reviews WHERE draft_id=? ORDER BY source_url''', (draft_id,)).fetchall()
            source_records = [dict(r) for r in reviews]
            evidence_json = json.dumps(source_records, sort_keys=True, separators=(',', ':'))
            evidence_hash = hashlib.sha256(evidence_json.encode()).hexdigest()
            package = {
                'draft_id': draft_id,
                'run_id': draft['run_id'],
                'project_id': draft['project_id'],
                'plan_id': draft['plan_id'],
                'body_sha256': draft['body_sha256'],
                'source_urls': draft['source_urls'],
                'source_evidence_sha256': evidence_hash,
                'reviewer_id': reviewer_id,
                'editorial_note': editorial_note.strip(),
                'status': 'private_release_candidate_pending_separate_release_authorization',
                'fact_checked_automatically': False,
                'public_publishing_enabled': False,
                'delivery_performed': False,
            }
            canonical = json.dumps(package, sort_keys=True, separators=(',', ':'))
            digest = hashlib.sha256(canonical.encode()).hexdigest()
            found = self.db.execute('SELECT * FROM studio_private_article_release_candidates WHERE draft_id=?',
                                    (draft_id,)).fetchone()
            if found:
                if found['package_sha256'] != digest or found['candidate_json'] != canonical:
                    raise WorkflowError('Immutable candidate already exists; use a new draft/version')
                return {**package, 'package_sha256': digest}
            self.db.execute('''INSERT INTO studio_private_article_release_candidates
                (draft_id,body_sha256,reviewer_id,editorial_note,source_snapshot_sha256,
                 package_sha256,candidate_json,created_at) VALUES (?,?,?,?,?,?,?,?)''',
                (draft_id,draft['body_sha256'],reviewer_id,editorial_note.strip(),
                 evidence_hash,digest,canonical,datetime.now(timezone.utc).isoformat()))
            return {**package, 'package_sha256': digest}

    def read(self, draft_id: str) -> dict:
        if not isinstance(draft_id, str) or not _ID.fullmatch(draft_id):
            raise WorkflowError('Invalid draft ID')
        with self.lock:
            found = self.db.execute('SELECT * FROM studio_private_article_release_candidates WHERE draft_id=?',
                                    (draft_id,)).fetchone()
            if found is None:
                raise WorkflowError('No private release candidate')
            digest = hashlib.sha256(found['candidate_json'].encode()).hexdigest()
            if digest != found['package_sha256']:
                raise WorkflowError('Stored candidate integrity failure')
            package = json.loads(found['candidate_json'])
            draft = self.intake.get(draft_id)
            if draft['body_sha256'] != found['body_sha256']:
                raise WorkflowError('Candidate draft integrity failure')
            return {**package, 'package_sha256': digest}
