"""Session 09.3-A: private article draft intake on an existing Daily Desk CEO link.

Accepts user/editor-supplied Markdown and source references; never invokes a
model, downloads media, approves editorial gates, or publishes anything.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from urllib.parse import urlsplit
from typing import Any

from .daily_ceo_queue_bridge import DailyCEOQueueBridge
from .workflows import WorkflowError, _ID


class PrivateArticleDraftIntake:
    def __init__(self, bridge: DailyCEOQueueBridge):
        self.bridge = bridge
        self.db = bridge.db
        self.lock = bridge.handoffs.engine._lock
        with self.lock, self.db:
            self.db.execute("""CREATE TABLE IF NOT EXISTS studio_private_article_drafts (
                draft_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES studio_daily_ceo_links(run_id),
                project_id TEXT NOT NULL,
                plan_id TEXT NOT NULL,
                body_markdown TEXT NOT NULL,
                body_sha256 TEXT NOT NULL,
                sources_json TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status='private_unverified')
            )""")

    def ingest(self, *, draft_id: str, run_id: str, body_markdown: str,
               source_urls: list[str], actor_id: str) -> dict[str, Any]:
        if not isinstance(draft_id, str) or not _ID.fullmatch(draft_id):
            raise WorkflowError('Invalid draft ID')
        if not isinstance(run_id, str) or not _ID.fullmatch(run_id):
            raise WorkflowError('Invalid run ID')
        if not isinstance(actor_id, str) or actor_id not in self.bridge.allocation.director_ids:
            raise WorkflowError('Director identity required for manual draft intake')
        if (not isinstance(body_markdown, str) or not body_markdown.strip()
                or len(body_markdown.encode('utf-8')) > 250_000):
            raise WorkflowError('Private Markdown draft must be non-empty and <=250KB')
        if not isinstance(source_urls, list) or not (1 <= len(source_urls) <= 25):
            raise WorkflowError('Between 1 and 25 source URLs required')
        normalized: list[str] = []
        for raw in source_urls:
            if not isinstance(raw, str) or len(raw) > 2048 or raw != raw.strip():
                raise WorkflowError('Invalid source URL')
            try:
                parsed = urlsplit(raw)
                valid = (parsed.scheme == 'https' and parsed.hostname is not None
                         and parsed.username is None and parsed.password is None
                         and parsed.port in (None, 443))
            except ValueError:
                valid = False
            if not valid or any(ord(c) < 33 for c in raw) or parsed.hostname in ('localhost',):
                raise WorkflowError('Only well-formed public HTTPS source URLs are accepted')
            normalized.append(raw)
        if len(set(normalized)) != len(normalized):
            raise WorkflowError('Duplicate source URLs')
        digest = hashlib.sha256(body_markdown.encode('utf-8')).hexdigest()
        with self.lock, self.db:
            link = self.db.execute(
                'SELECT project_id, plan_id FROM studio_daily_ceo_links WHERE run_id=?', (run_id,)
            ).fetchone()
            if link is None:
                raise WorkflowError('Existing Director-linked CEO Daily Desk plan required')
            project = self.bridge.handoffs.engine.pipeline.get_project(link['project_id'])
            if project['created_by'] != actor_id:
                raise WorkflowError('Director does not own this project')
            existing = self.db.execute(
                'SELECT * FROM studio_private_article_drafts WHERE draft_id=?', (draft_id,)
            ).fetchone()
            if existing:
                if (existing['run_id'] != run_id or existing['body_sha256'] != digest
                        or existing['body_markdown'] != body_markdown
                        or json.loads(existing['sources_json']) != normalized):
                    raise WorkflowError('Draft ID already used for different content')
                return self._summary(existing)
            created = datetime.now(timezone.utc).isoformat()
            self.db.execute("""INSERT INTO studio_private_article_drafts
                (draft_id,run_id,project_id,plan_id,body_markdown,body_sha256,
                 sources_json,actor_id,created_at,status)
                VALUES(?,?,?,?,?,?,?,?,?,'private_unverified')""",
                (draft_id, run_id, link['project_id'], link['plan_id'],
                 body_markdown, digest, json.dumps(normalized), actor_id, created))
            return {'draft_id': draft_id, 'run_id': run_id, 'project_id': link['project_id'],
                    'plan_id': link['plan_id'], 'body_sha256': digest,
                    'source_count': len(normalized), 'status': 'private_unverified',
                    'fact_checked': False, 'execution_performed': False,
                    'publication_enabled': False}

    @staticmethod
    def _summary(row: Any) -> dict[str, Any]:
        return {'draft_id': row['draft_id'], 'run_id': row['run_id'],
                'project_id': row['project_id'], 'plan_id': row['plan_id'],
                'body_sha256': row['body_sha256'],
                'source_count': len(json.loads(row['sources_json'])),
                'status': row['status'], 'fact_checked': False,
                'execution_performed': False, 'publication_enabled': False}

    def get(self, draft_id: str) -> dict[str, Any]:
        if not isinstance(draft_id, str) or not _ID.fullmatch(draft_id):
            raise WorkflowError('Invalid draft ID')
        with self.lock:
            row = self.db.execute('SELECT * FROM studio_private_article_drafts WHERE draft_id=?',
                                  (draft_id,)).fetchone()
            if not row:
                raise WorkflowError('Unknown draft')
            return {**self._summary(row), 'body_markdown': row['body_markdown'],
                    'source_urls': json.loads(row['sources_json'])}
