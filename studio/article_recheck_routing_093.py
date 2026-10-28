"""Session 09.3-D: private, department-routed article correction tickets.

This ledger routes editorial defects without rewriting source evidence or automatically
approving revised articles. All decisions require an authorized Director identity.
"""
from __future__ import annotations

from datetime import datetime, timezone
from .workflows import WorkflowError, _ID


DEPARTMENTS = {
    "source_accuracy": "research",
    "missing_evidence": "research",
    "writing_clarity": "editorial",
    "language": "editorial",
    "seo": "seo",
    "rights": "rights",
    "formatting": "editorial",
}


class ArticleRecheckRouter:
    def __init__(self, release):
        self.release = release
        self.intake = release.intake
        self.db = release.db
        self.lock = release.lock
        with self.lock, self.db:
            self.db.execute("""CREATE TABLE IF NOT EXISTS studio_article_correction_tickets (
                ticket_id TEXT PRIMARY KEY,
                draft_id TEXT NOT NULL REFERENCES studio_private_article_drafts(draft_id),
                draft_sha256 TEXT NOT NULL,
                issue_type TEXT NOT NULL,
                department TEXT NOT NULL,
                reason TEXT NOT NULL,
                requested_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('open','submitted_for_recheck')),
                revised_draft_id TEXT,
                submitted_by TEXT,
                submitted_at TEXT
            )""")

    def request(self, *, ticket_id: str, draft_id: str, issue_type: str,
                reason: str, director_id: str) -> dict:
        if not isinstance(ticket_id, str) or not _ID.fullmatch(ticket_id):
            raise WorkflowError("Invalid ticket ID")
        if issue_type not in DEPARTMENTS:
            raise WorkflowError("Unsupported correction category")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
            raise WorkflowError("Specific correction reason required")
        self._check_director(director_id)
        with self.lock, self.db:
            draft = self.intake.get(draft_id)
            existing = self.db.execute(
                "SELECT * FROM studio_article_correction_tickets WHERE ticket_id=?", (ticket_id,)
            ).fetchone()
            if existing:
                raise WorkflowError("Correction ticket ID already exists")
            self.db.execute("""INSERT INTO studio_article_correction_tickets
                (ticket_id,draft_id,draft_sha256,issue_type,department,reason,
                 requested_by,created_at,status)
                VALUES(?,?,?,?,?,?,?,?,'open')""",
                (ticket_id,draft_id,draft["body_sha256"],issue_type,
                 DEPARTMENTS[issue_type],reason.strip(),director_id,
                 datetime.now(timezone.utc).isoformat()))
            return self.read(ticket_id)

    def _check_director(self, director_id: str) -> None:
        if (not isinstance(director_id, str)
                or director_id not in self.intake.bridge.allocation.director_ids):
            raise WorkflowError("Authorized Director required")

    def read(self, ticket_id: str) -> dict:
        if not isinstance(ticket_id, str) or not _ID.fullmatch(ticket_id):
            raise WorkflowError("Invalid ticket ID")
        with self.lock:
            row = self.db.execute(
                "SELECT * FROM studio_article_correction_tickets WHERE ticket_id=?", (ticket_id,)
            ).fetchone()
            if not row:
                raise WorkflowError("Unknown correction ticket")
            return {**dict(row), "publication_enabled": False,
                    "automatic_reapproval": False}

    def submit_revised_draft(self, *, ticket_id: str, revised_draft_id: str,
                             director_id: str) -> dict:
        """Records handoff for a *new* immutable draft; never closes approval gates."""
        self._check_director(director_id)
        with self.lock, self.db:
            ticket = self.read(ticket_id)
            if ticket["status"] != "open":
                raise WorkflowError("Correction already submitted")
            revised = self.intake.get(revised_draft_id)
            original = self.intake.get(ticket["draft_id"])
            if revised_draft_id == ticket["draft_id"] or (
                revised["project_id"] != original["project_id"]
                or revised["run_id"] != original["run_id"]
                or revised["body_sha256"] == original["body_sha256"]
            ):
                raise WorkflowError("Distinct revised draft on same project/run with changed content required")
            self.db.execute("""UPDATE studio_article_correction_tickets
                SET status='submitted_for_recheck',revised_draft_id=?,submitted_by=?,submitted_at=?
                WHERE ticket_id=? AND status='open'""",
                (revised_draft_id,director_id,datetime.now(timezone.utc).isoformat(),ticket_id))
            return self.read(ticket_id)

    def status(self, draft_id: str) -> dict:
        with self.lock:
            draft = self.intake.get(draft_id)
            tickets = self.db.execute("""SELECT ticket_id,status,department,issue_type
                FROM studio_article_correction_tickets WHERE draft_id=? ORDER BY created_at,ticket_id""",
                (draft_id,)).fetchall()
            return {
                "draft_id": draft_id, "body_sha256": draft["body_sha256"],
                "tickets": [dict(t) for t in tickets],
                "release_blocked": bool(tickets),
                "requires_new_version_and_fresh_review": bool(tickets),
                "publication_enabled": False,
            }
