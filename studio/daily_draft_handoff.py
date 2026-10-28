"""Session 09.2 additive local-only Daily Desk handoff.

Turns a persisted, eligible schedule intent into an idempotent PRIVATE work item.
It neither invokes an agent nor performs publication or external network access.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import sqlite3
from typing import Any

from .workflows import WorkflowEngine, WorkflowError, _ID


class DailyDraftHandoff:
    def __init__(self, engine: WorkflowEngine):
        self.engine = engine
        self.connection = engine._connection
        with engine._lock, self.connection:
            self.connection.execute("""
                CREATE TABLE IF NOT EXISTS studio_daily_draft_handoffs (
                    run_id TEXT PRIMARY KEY REFERENCES studio_daily_runs(run_id),
                    workflow_id TEXT NOT NULL CHECK(workflow_id='ai_daily_desk'),
                    status TEXT NOT NULL CHECK(status='awaiting_private_research'),
                    handoff_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)

    def prepare(self, run_id: str) -> dict[str, Any]:
        """Produce a private research/drafting handoff from an eligible persisted run."""
        if not isinstance(run_id, str) or not _ID.fullmatch(run_id):
            raise WorkflowError("Invalid run ID")
        with self.engine._lock, self.connection:
            existing = self.connection.execute(
                "SELECT handoff_json FROM studio_daily_draft_handoffs WHERE run_id=?", (run_id,)
            ).fetchone()
            if existing:
                return json.loads(existing[0])
            run = self.connection.execute(
                "SELECT * FROM studio_daily_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if run is None or run['status'] != 'planned':
                raise WorkflowError("Only planned Daily Desk runs can be handed off")
            policy = self.engine._approved_policy(run['policy_id'])
            if run['category'] not in policy['categories'] or run['content_type'] not in policy['content_types']:
                raise WorkflowError("Run is outside current editorial policy")
            if run['fallback_candidate_id']:
                candidate = self.connection.execute(
                    "SELECT * FROM studio_evergreen_candidates WHERE candidate_id=?",
                    (run['fallback_candidate_id'],),
                ).fetchone()
                if (candidate is None or candidate['status'] != 'verified'
                        or datetime.fromisoformat(candidate['expires_at']) <= datetime.now(timezone.utc)):
                    raise WorkflowError("Fallback unavailable or expired")
            item = {
                'run_id': run_id, 'workflow_id': 'ai_daily_desk',
                'schedule_id': run['schedule_id'], 'category': run['category'],
                'content_type': run['content_type'], 'policy_id': run['policy_id'],
                'fallback_candidate_id': run['fallback_candidate_id'],
                'requested_steps': ['research', 'source_verification', 'private_draft', 'editorial_review'],
                'status': 'awaiting_private_research',
                'execution_performed': False, 'publication_enabled': False,
            }
            self.connection.execute(
                "INSERT INTO studio_daily_draft_handoffs VALUES (?, 'ai_daily_desk', ?, ?, ?)",
                (run_id, 'awaiting_private_research', json.dumps(item, sort_keys=True), datetime.now(timezone.utc).isoformat()),
            )
            self.engine._audit('ai_daily_desk', run_id, 'private_draft_handoff_prepared',
                               'local-scheduler', {'schedule_id': run['schedule_id']}, datetime.now(timezone.utc))
            return item

    def list_private_handoffs(self) -> list[dict[str, Any]]:
        with self.engine._lock:
            rows = self.connection.execute(
                'SELECT handoff_json FROM studio_daily_draft_handoffs ORDER BY created_at,run_id'
            ).fetchall()
            return [json.loads(r[0]) for r in rows]
