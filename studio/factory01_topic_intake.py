"""VM-F01-002: persistent, private, manually submitted topic/source candidates.

Not a trends scraper, source verifier, model executor, or publishing engine.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import ipaddress
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlsplit

from .factory01_editorial_plan import DEFAULT_FACTORY01_PLAN

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")


class TopicIntakeError(ValueError):
    pass


def _url(value: object) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > 2048:
        raise TopicIntakeError("Source must be a bounded HTTPS URL")
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower()
        try:
            address = ipaddress.ip_address(host)
            public_host = address.is_global
        except ValueError:
            public_host = True
        valid = (public_host and parsed.scheme == "https" and host and parsed.username is None
                 and parsed.password is None and parsed.port in (None, 443)
                 and not any(ord(c) < 33 for c in value)
                 and host not in {"localhost", "127.0.0.1", "::1"}
                 and not host.endswith((".local", ".localhost", ".internal"))
                 and not re.fullmatch(r"(?:10|127|192\.168)\.\d+\.\d+\.\d+", host)
                 and not re.fullmatch(r"172\.(?:1[6-9]|2\d|3[01])\.\d+\.\d+", host))
    except ValueError:
        valid = False
    if not valid:
        raise TopicIntakeError("Source must be a public HTTPS reference URL")
    return value


class Factory01TopicIntake:
    """Append-only topic proposals in the existing Studio database file."""

    def __init__(self, database: str | Path):
        DEFAULT_FACTORY01_PLAN.validate()
        self.db = sqlite3.connect(str(database), timeout=10)
        self.db.row_factory = sqlite3.Row
        with self.db:
            self.db.execute("""CREATE TABLE IF NOT EXISTS studio_factory01_topics (
                candidate_id TEXT PRIMARY KEY,
                category TEXT NOT NULL,
                headline TEXT NOT NULL,
                source_urls_json TEXT NOT NULL,
                submitted_by TEXT NOT NULL,
                submitted_at TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status='private_unverified'),
                origin TEXT NOT NULL CHECK(origin='manual_submission')
            )""")

    def close(self) -> None:
        self.db.close()

    def submit(self, *, candidate_id: str, category: str, headline: str,
               source_urls: list[str], submitted_by: str) -> dict:
        if not isinstance(candidate_id, str) or not _ID.fullmatch(candidate_id):
            raise TopicIntakeError("Invalid candidate ID")
        if category not in DEFAULT_FACTORY01_PLAN.categories:
            raise TopicIntakeError("Category outside Factory 01 policy")
        if (not isinstance(headline, str) or not headline.strip()
                or len(headline.strip()) > 240):
            raise TopicIntakeError("Headline must contain 1-240 characters")
        if (not isinstance(submitted_by, str) or not submitted_by.strip()
                or len(submitted_by) > 120):
            raise TopicIntakeError("Named submitter is required")
        if not isinstance(source_urls, list) or not 1 <= len(source_urls) <= 12:
            raise TopicIntakeError("Provide 1-12 source references")
        urls = [_url(url) for url in source_urls]
        if len(set(urls)) != len(urls):
            raise TopicIntakeError("Duplicate source references")
        with self.db:
            existing = self.db.execute("SELECT * FROM studio_factory01_topics WHERE candidate_id=?",
                                       (candidate_id,)).fetchone()
            if existing:
                if (existing['category'] != category or existing['headline'] != headline.strip()
                        or json.loads(existing['source_urls_json']) != urls
                        or existing['submitted_by'] != submitted_by.strip()):
                    raise TopicIntakeError("Candidate ID already belongs to a different proposal")
                return self._record(existing)
            timestamp = datetime.now(timezone.utc).isoformat()
            self.db.execute("""INSERT INTO studio_factory01_topics
                (candidate_id,category,headline,source_urls_json,submitted_by,submitted_at,status,origin)
                VALUES (?,?,?,?,?,?,'private_unverified','manual_submission')""",
                (candidate_id,category,headline.strip(),json.dumps(urls),submitted_by.strip(),timestamp))
        return self.get(candidate_id)

    @staticmethod
    def _record(row: sqlite3.Row) -> dict:
        return dict(candidate_id=row['candidate_id'],category=row['category'],
                    headline=row['headline'],source_urls=json.loads(row['source_urls_json']),
                    submitted_by=row['submitted_by'],submitted_at=row['submitted_at'],
                    status=row['status'],origin=row['origin'],
                    trending_rank=None,trend_verified=False,sources_verified=False,
                    research_performed=False,ai_generation_performed=False,
                    publishing_enabled=False)

    def get(self, candidate_id: str) -> dict:
        if not isinstance(candidate_id, str) or not _ID.fullmatch(candidate_id):
            raise TopicIntakeError("Invalid candidate ID")
        row = self.db.execute("SELECT * FROM studio_factory01_topics WHERE candidate_id=?",
                              (candidate_id,)).fetchone()
        if row is None:
            raise TopicIntakeError("Unknown candidate")
        return self._record(row)

    def list_private(self, category: str | None = None) -> list[dict]:
        if category is not None and category not in DEFAULT_FACTORY01_PLAN.categories:
            raise TopicIntakeError("Invalid category")
        if category is None:
            rows = self.db.execute("SELECT * FROM studio_factory01_topics ORDER BY submitted_at,candidate_id").fetchall()
        else:
            rows = self.db.execute("SELECT * FROM studio_factory01_topics WHERE category=? ORDER BY submitted_at,candidate_id",(category,)).fetchall()
        return [self._record(row) for row in rows]
