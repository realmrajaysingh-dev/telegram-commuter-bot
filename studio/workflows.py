"""Local-only workflow routing and governance layered on Studio's existing ledger."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
import sqlite3
import threading
from pathlib import Path
from typing import Any
import uuid

from .models import (
    ASSIGNMENT_WINDOW,
    AssignmentManifest,
    AssignmentExtension,
    StudioError,
    parse_datetime,
    validate_assignment,
)
from .pipeline import ProjectPipeline, ProjectError, PROJECT_ID_PATTERN


WORKFLOWS = frozenset({"ai_daily_desk", "human_creative_desk"})
_HASH = re.compile(r"^[a-f0-9]{64}$")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_POLICY_LIST_LIMIT = 100
_RELEASE_CHECKS = frozenset(
    {
        "quality_verified",
        "sources_verified",
        "copyright_verified",
        "duplicate_checked",
        "risk_cleared",
        "evidence_complete",
    }
)
_EVERGREEN_CHECKS = frozenset(
    {"source_verified", "rights_verified", "fact_checked", "duplicate_checked", "risk_cleared"}
)
_CREATIVE_GATES = ("scope_script", "rough_cut", "final_release")


class WorkflowError(ValueError):
    """Raised when local workflow governance requirements are not met."""


def _now(value: datetime | None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise WorkflowError("Workflow timestamps must include a timezone")
    return current.astimezone(timezone.utc)


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _text(value: object, label: str, limit: int = 200) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        raise WorkflowError(f"{label} must be non-empty and at most {limit} characters")
    return value.strip()


def _unique_values(value: object, label: str, *, max_items: int = _POLICY_LIST_LIMIT) -> tuple[str, ...]:
    if (
        not isinstance(value, (list, tuple))
        or not value
        or len(value) > max_items
        or any(not isinstance(item, str) or not item.strip() or len(item.strip()) > 128 for item in value)
    ):
        raise WorkflowError(f"{label} must contain bounded non-empty values")
    result = tuple(item.strip() for item in value)
    if len(set(result)) != len(result):
        raise WorkflowError(f"{label} must not contain duplicates")
    return result


class WorkflowEngine:
    """Additive workflow-policy records sharing the existing Studio SQLite file.

    Task plans, task evidence, worker jobs, and their audit records continue to
    use their existing services. This module owns routing, standing policy,
    schedule intents, fallback verification, and creative human-gate decisions.
    """

    def __init__(self, pipeline: ProjectPipeline) -> None:
        self.pipeline = pipeline
        self.database = Path(pipeline.database)
        self.director_ids = pipeline.director_ids
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(
            str(self.database), timeout=10, check_same_thread=False
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._create_schema()
        self._ensure_project_routes()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def _create_schema(self) -> None:
        with self._connection:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS studio_workflow_routes (
                    project_id TEXT PRIMARY KEY REFERENCES studio_projects(project_id),
                    workflow_id TEXT NOT NULL CHECK (workflow_id IN (
                        'ai_daily_desk', 'human_creative_desk', 'unassigned'
                    )),
                    assigned_by TEXT NOT NULL,
                    assigned_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS studio_workflow_audit_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    workflow_id TEXT NOT NULL,
                    object_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    event_json TEXT NOT NULL,
                    previous_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS studio_editorial_policies (
                    policy_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('draft', 'approved', 'superseded')),
                    policy_json TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    approved_by TEXT,
                    created_at TEXT NOT NULL,
                    approved_at TEXT,
                    PRIMARY KEY(policy_id, version)
                );
                CREATE TABLE IF NOT EXISTS studio_workflow_assignments (
                    assignment_id TEXT PRIMARY KEY,
                    workflow_id TEXT NOT NULL CHECK (workflow_id IN ('ai_daily_desk', 'human_creative_desk')),
                    project_id TEXT REFERENCES studio_projects(project_id),
                    manifest_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS studio_daily_schedules (
                    schedule_id TEXT PRIMARY KEY,
                    policy_id TEXT NOT NULL,
                    category TEXT NOT NULL,
                    content_type TEXT NOT NULL,
                    recurrence TEXT NOT NULL CHECK (recurrence IN ('daily', 'weekly')),
                    next_due_at TEXT NOT NULL,
                    enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS studio_workflow_budgets (
                    workflow_id TEXT PRIMARY KEY CHECK (workflow_id IN ('ai_daily_desk', 'human_creative_desk')),
                    max_active_projects INTEGER NOT NULL CHECK (max_active_projects BETWEEN 1 AND 1000),
                    max_daily_items INTEGER NOT NULL CHECK (max_daily_items BETWEEN 1 AND 10000),
                    max_runtime_seconds INTEGER NOT NULL CHECK (max_runtime_seconds BETWEEN 1 AND 86400),
                    max_storage_bytes INTEGER NOT NULL CHECK (max_storage_bytes BETWEEN 1 AND 10737418240),
                    authorized_spend_minor INTEGER NOT NULL DEFAULT 0 CHECK (authorized_spend_minor = 0),
                    updated_by TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS studio_daily_runs (
                    run_id TEXT PRIMARY KEY,
                    schedule_id TEXT NOT NULL REFERENCES studio_daily_schedules(schedule_id),
                    occurrence_key TEXT NOT NULL,
                    policy_id TEXT NOT NULL,
                    category TEXT NOT NULL,
                    content_type TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('planned', 'blocked', 'missed')),
                    fallback_candidate_id TEXT,
                    reason_code TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(schedule_id, occurrence_key)
                );
                CREATE TABLE IF NOT EXISTS studio_evergreen_candidates (
                    candidate_id TEXT PRIMARY KEY,
                    policy_id TEXT NOT NULL,
                    category TEXT NOT NULL,
                    content_type TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    evidence_refs_json TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('pending', 'verified', 'rejected')),
                    expires_at TEXT NOT NULL,
                    verified_by TEXT,
                    verified_at TEXT,
                    last_used_at TEXT,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS studio_creative_gate_events (
                    event_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES studio_projects(project_id),
                    gate_id TEXT NOT NULL CHECK (gate_id IN ('scope_script', 'rough_cut', 'final_release')),
                    outcome TEXT NOT NULL CHECK (outcome IN ('pass', 'revise', 'block')),
                    artifact_sha256 TEXT NOT NULL,
                    evidence_refs_json TEXT NOT NULL,
                    predecessor_event_id TEXT,
                    reviewer_id TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            now = datetime.now(timezone.utc).isoformat()
            for workflow_id, items, runtime, storage in (
                ("ai_daily_desk", 20, 3600, 256 * 1024 * 1024),
                ("human_creative_desk", 5, 4 * 3600, 1024 * 1024 * 1024),
            ):
                self._connection.execute(
                    """
                    INSERT OR IGNORE INTO studio_workflow_budgets
                        (workflow_id,max_active_projects,max_daily_items,max_runtime_seconds,
                         max_storage_bytes,authorized_spend_minor,updated_by,updated_at)
                    VALUES (?, ?, ?, ?, ?, 0, 'local-policy-default', ?)
                    """,
                    (workflow_id, 100, items, runtime, storage, now),
                )

    def _actor(self, director_id: object) -> str:
        if not isinstance(director_id, str) or director_id not in self.director_ids:
            raise WorkflowError("Only a configured local Director may change workflow governance")
        return director_id

    def _audit(
        self,
        workflow_id: str,
        object_id: str,
        event_type: str,
        actor_id: str,
        data: dict[str, Any],
        created_at: datetime,
    ) -> None:
        previous = self._connection.execute(
            "SELECT event_hash FROM studio_workflow_audit_events ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        previous_hash = previous["event_hash"] if previous else "0" * 64
        event_json = _json(data)
        event_hash = hashlib.sha256(
            _json(
                {
                    "workflow_id": workflow_id,
                    "object_id": object_id,
                    "event_type": event_type,
                    "actor_id": actor_id,
                    "event_json": event_json,
                    "previous_hash": previous_hash,
                    "created_at": created_at.isoformat(),
                }
            ).encode("utf-8")
        ).hexdigest()
        self._connection.execute(
            """
            INSERT INTO studio_workflow_audit_events
                (workflow_id, object_id, event_type, actor_id, event_json,
                 previous_hash, event_hash, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                workflow_id, object_id, event_type, actor_id, event_json,
                previous_hash, event_hash, created_at.isoformat(),
            ),
        )

    @staticmethod
    def _default_workflow(project_type: str) -> str:
        normalized = re.sub(r"[^a-z0-9]+", "_", project_type.lower()).strip("_")
        if normalized in {
            "news", "news_article", "article", "seo", "social", "social_post",
            "social_media_campaign", "daily_news",
        }:
            return "ai_daily_desk"
        if normalized in {
            "documentary", "research_documentary", "video", "youtube_video",
            "creative_video", "original_video",
        }:
            return "human_creative_desk"
        return "unassigned"

    def _ensure_project_routes(self) -> None:
        with self._connection:
            projects = self._connection.execute(
                """
                SELECT project_id, project_type, created_by, created_at
                FROM studio_projects
                """
            ).fetchall()
            for project in projects:
                self._connection.execute(
                    """
                    INSERT OR IGNORE INTO studio_workflow_routes
                        (project_id, workflow_id, assigned_by, assigned_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        project["project_id"],
                        self._default_workflow(project["project_type"]),
                        project["created_by"],
                        project["created_at"],
                    ),
                )

    def route_project(
        self,
        project_id: str,
        workflow_id: str,
        *,
        director_id: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        actor, current = self._actor(director_id), _now(now)
        if not isinstance(project_id, str) or not PROJECT_ID_PATTERN.fullmatch(project_id):
            raise WorkflowError("Project ID is invalid")
        if workflow_id not in WORKFLOWS:
            raise WorkflowError("Workflow must be ai_daily_desk or human_creative_desk")
        try:
            self.pipeline.get_project(project_id)
        except ProjectError as exc:
            raise WorkflowError("Project does not exist") from exc
        budget = self._connection.execute(
            "SELECT max_active_projects FROM studio_workflow_budgets WHERE workflow_id=?",
            (workflow_id,),
        ).fetchone()
        active_count = sum(
            project["status"] in {"queued", "active", "pending_review", "ready"}
            and self._connection.execute(
                "SELECT workflow_id FROM studio_workflow_routes WHERE project_id=?",
                (project["project_id"],),
            ).fetchone()["workflow_id"] == workflow_id
            for project in self.pipeline.list_projects()
        )
        current_route = self._connection.execute(
            "SELECT workflow_id FROM studio_workflow_routes WHERE project_id=?",
            (project_id,),
        ).fetchone()
        if (
            (current_route is None or current_route["workflow_id"] != workflow_id)
            and active_count >= budget["max_active_projects"]
        ):
            raise WorkflowError("Workflow active-project budget is exhausted")
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO studio_workflow_routes
                    (project_id, workflow_id, assigned_by, assigned_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(project_id) DO UPDATE SET
                    workflow_id=excluded.workflow_id,
                    assigned_by=excluded.assigned_by,
                    assigned_at=excluded.assigned_at
                """,
                (project_id, workflow_id, actor, current.isoformat()),
            )
            self._audit(
                workflow_id, project_id, "project_workflow_routed", actor,
                {"workflow_id": workflow_id}, current,
            )
        return {"project_id": project_id, "workflow_id": workflow_id, "assigned_by": actor}

    def set_workflow_budget(
        self,
        workflow_id: str,
        *,
        director_id: str,
        max_active_projects: int,
        max_daily_items: int,
        max_runtime_seconds: int,
        max_storage_bytes: int,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        actor, current = self._actor(director_id), _now(now)
        values = {
            "max_active_projects": (max_active_projects, 1, 1000),
            "max_daily_items": (max_daily_items, 1, 10000),
            "max_runtime_seconds": (max_runtime_seconds, 1, 86400),
            "max_storage_bytes": (max_storage_bytes, 1, 10 * 1024**3),
        }
        if workflow_id not in WORKFLOWS:
            raise WorkflowError("Workflow budget target is invalid")
        for label, (value, low, high) in values.items():
            if type(value) is not int or not low <= value <= high:
                raise WorkflowError(f"{label} is outside local policy limits")
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO studio_workflow_budgets
                    (workflow_id,max_active_projects,max_daily_items,max_runtime_seconds,
                     max_storage_bytes,authorized_spend_minor,updated_by,updated_at)
                VALUES (?, ?, ?, ?, ?, 0, ?, ?)
                ON CONFLICT(workflow_id) DO UPDATE SET
                    max_active_projects=excluded.max_active_projects,
                    max_daily_items=excluded.max_daily_items,
                    max_runtime_seconds=excluded.max_runtime_seconds,
                    max_storage_bytes=excluded.max_storage_bytes,
                    updated_by=excluded.updated_by,updated_at=excluded.updated_at
                """,
                (
                    workflow_id, max_active_projects, max_daily_items,
                    max_runtime_seconds, max_storage_bytes, actor, current.isoformat(),
                ),
            )
            self._audit(
                workflow_id, workflow_id, "workflow_budget_updated", actor,
                {key: value[0] for key, value in values.items()}, current,
            )
        return self._budget(workflow_id)

    def _budget(self, workflow_id: str) -> dict[str, Any]:
        row = self._connection.execute(
            "SELECT * FROM studio_workflow_budgets WHERE workflow_id=?",
            (workflow_id,),
        ).fetchone()
        return dict(row) if row else {}

    def create_policy(
        self,
        *,
        director_id: str,
        policy_id: str,
        categories: tuple[str, ...] | list[str],
        content_types: tuple[str, ...] | list[str],
        source_policies: tuple[str, ...] | list[str],
        allowed_channels: tuple[str, ...] | list[str],
        prohibited_topics: tuple[str, ...] | list[str],
        routine_publish_rules: list[dict[str, Any]] | tuple[dict[str, Any], ...],
        now: datetime | None = None,
    ) -> dict[str, Any]:
        actor, current = self._actor(director_id), _now(now)
        if not isinstance(policy_id, str) or not _ID.fullmatch(policy_id):
            raise WorkflowError("Policy ID is invalid")
        policy = {
            "schema_version": 1,
            "categories": _unique_values(categories, "Categories"),
            "content_types": _unique_values(content_types, "Content types"),
            "source_policies": _unique_values(source_policies, "Source policies"),
            "allowed_channels": _unique_values(allowed_channels, "Allowed channels"),
            "prohibited_topics": _unique_values(prohibited_topics, "Prohibited topics"),
        }
        if not isinstance(routine_publish_rules, (list, tuple)) or len(routine_publish_rules) > 100:
            raise WorkflowError("Routine publish rules must be a bounded list")
        normalized_rules = []
        for rule in routine_publish_rules:
            if not isinstance(rule, dict) or set(rule) != {"category", "content_type", "channel"}:
                raise WorkflowError("Routine publish rule fields are invalid")
            normalized = {
                key: _text(value, f"Rule {key}", 128)
                for key, value in rule.items()
            }
            if (
                normalized["category"] not in policy["categories"]
                or normalized["content_type"] not in policy["content_types"]
                or normalized["channel"] not in policy["allowed_channels"]
            ):
                raise WorkflowError("Routine publish rule exceeds policy categories, content types, or channels")
            normalized_rules.append(normalized)
        policy["routine_publish_rules"] = normalized_rules
        with self._connection:
            row = self._connection.execute(
                "SELECT COALESCE(MAX(version), 0) AS version FROM studio_editorial_policies WHERE policy_id=?",
                (policy_id,),
            ).fetchone()
            version = row["version"] + 1
            self._connection.execute(
                """
                INSERT INTO studio_editorial_policies
                    (policy_id, version, status, policy_json, created_by, created_at)
                VALUES (?, ?, 'draft', ?, ?, ?)
                """,
                (policy_id, version, _json(policy), actor, current.isoformat()),
            )
            self._audit(
                "ai_daily_desk", f"{policy_id}:{version}", "editorial_policy_drafted",
                actor, {"version": version}, current,
            )
        return {"policy_id": policy_id, "version": version, "status": "draft", **policy}

    def approve_policy(
        self, policy_id: str, version: int, *, director_id: str, now: datetime | None = None
    ) -> dict[str, Any]:
        actor, current = self._actor(director_id), _now(now)
        with self._connection:
            row = self._connection.execute(
                "SELECT * FROM studio_editorial_policies WHERE policy_id=? AND version=?",
                (policy_id, version),
            ).fetchone()
            if row is None or row["status"] == "superseded":
                raise WorkflowError("Editorial policy version not found")
            self._connection.execute(
                "UPDATE studio_editorial_policies SET status='superseded' WHERE status='approved'"
            )
            self._connection.execute(
                """
                UPDATE studio_editorial_policies
                SET status='approved', approved_by=?, approved_at=?
                WHERE policy_id=? AND version=?
                """,
                (actor, current.isoformat(), policy_id, version),
            )
            self._audit(
                "ai_daily_desk", f"{policy_id}:{version}", "editorial_policy_approved",
                actor, {"version": version}, current,
            )
        return self.get_policy(policy_id, version)

    def get_policy(self, policy_id: str, version: int | None = None) -> dict[str, Any]:
        query = "SELECT * FROM studio_editorial_policies WHERE policy_id=?"
        args: tuple[Any, ...] = (policy_id,)
        if version is not None:
            query += " AND version=?"
            args = (policy_id, version)
        query += " ORDER BY version DESC LIMIT 1"
        row = self._connection.execute(query, args).fetchone()
        if row is None:
            raise WorkflowError("Editorial policy not found")
        return {
            "policy_id": row["policy_id"],
            "version": row["version"],
            "status": row["status"],
            **json.loads(row["policy_json"]),
            "created_by": row["created_by"],
            "approved_by": row["approved_by"],
            "created_at": row["created_at"],
            "approved_at": row["approved_at"],
        }

    def _approved_policy(self, policy_id: str) -> dict[str, Any]:
        row = self._connection.execute(
            """
            SELECT * FROM studio_editorial_policies
            WHERE policy_id=? AND status='approved'
            ORDER BY version DESC LIMIT 1
            """,
            (policy_id,),
        ).fetchone()
        if row is None:
            raise WorkflowError("A Director-approved standing editorial policy is required")
        return {
            "policy_id": row["policy_id"],
            "version": row["version"],
            **json.loads(row["policy_json"]),
        }

    def create_assignment(
        self,
        *,
        director_id: str,
        workflow_id: str,
        title: str,
        starts_at: datetime,
        expires_at: datetime,
        policy_id: str,
        project_id: str | None = None,
        assignment_id: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        actor, current = self._actor(director_id), _now(now)
        if workflow_id not in WORKFLOWS:
            raise WorkflowError("Assignment workflow is invalid")
        self._approved_policy(policy_id)
        assignment_id = assignment_id or f"assignment-{uuid.uuid4().hex[:16]}"
        manifest = AssignmentManifest(
            assignment_id=assignment_id,
            title=_text(title, "Assignment title"),
            director_id=actor,
            starts_at=starts_at,
            expires_at=expires_at,
            editorial_policy_id=policy_id,
        )
        try:
            validate_assignment(manifest, now=current, director_ids=self.director_ids)
        except StudioError as exc:
            raise WorkflowError(str(exc)) from exc
        if project_id is not None:
            if not isinstance(project_id, str) or not PROJECT_ID_PATTERN.fullmatch(project_id):
                raise WorkflowError("Project ID is invalid")
            route = self._route(project_id)
            if route != workflow_id:
                raise WorkflowError("Assignment workflow does not match the routed project")
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO studio_workflow_assignments
                    (assignment_id, workflow_id, project_id, manifest_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    assignment_id, workflow_id, project_id, _json(manifest.to_dict()),
                    current.isoformat(), current.isoformat(),
                ),
            )
            self._audit(
                workflow_id, assignment_id, "assignment_created", actor,
                {"project_id": project_id, "expires_at": expires_at.isoformat()}, current,
            )
        return manifest.to_dict() | {"workflow_id": workflow_id, "project_id": project_id}

    def extend_assignment(
        self,
        assignment_id: str,
        *,
        director_id: str,
        new_expiry: datetime,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        actor, current = self._actor(director_id), _now(now)
        row = self._connection.execute(
            "SELECT * FROM studio_workflow_assignments WHERE assignment_id=?",
            (assignment_id,),
        ).fetchone()
        if row is None:
            raise WorkflowError("Assignment not found")
        manifest = AssignmentManifest.from_json(row["manifest_json"])
        try:
            extended = manifest.extend(
                approved_by=actor,
                approved_at=current,
                new_expiry=new_expiry,
                director_ids=self.director_ids,
                now=current,
            )
        except StudioError as exc:
            raise WorkflowError(str(exc)) from exc
        with self._connection:
            self._connection.execute(
                "UPDATE studio_workflow_assignments SET manifest_json=?, updated_at=? WHERE assignment_id=?",
                (_json(extended.to_dict()), current.isoformat(), assignment_id),
            )
            self._audit(
                row["workflow_id"], assignment_id, "assignment_extended", actor,
                {"new_expiry": new_expiry.isoformat()}, current,
            )
        return extended.to_dict()

    def create_daily_schedule(
        self,
        *,
        director_id: str,
        schedule_id: str,
        policy_id: str,
        category: str,
        content_type: str,
        recurrence: str,
        next_due_at: datetime,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        actor, current = self._actor(director_id), _now(now)
        policy = self._approved_policy(policy_id)
        if not isinstance(schedule_id, str) or not _ID.fullmatch(schedule_id):
            raise WorkflowError("Schedule ID is invalid")
        if category not in policy["categories"] or content_type not in policy["content_types"]:
            raise WorkflowError("Schedule category or content type is outside the approved policy")
        if recurrence not in {"daily", "weekly"}:
            raise WorkflowError("Schedule recurrence must be daily or weekly")
        due = _now(next_due_at)
        if due < current:
            raise WorkflowError("Schedule due time cannot be in the past")
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO studio_daily_schedules
                    (schedule_id, policy_id, category, content_type, recurrence,
                     next_due_at, enabled, created_by, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (schedule_id, policy_id, category, content_type, recurrence, due.isoformat(), actor, current.isoformat()),
            )
            self._audit(
                "ai_daily_desk", schedule_id, "daily_schedule_created", actor,
                {"category": category, "content_type": content_type, "recurrence": recurrence},
                current,
            )
        return {"schedule_id": schedule_id, "status": "enabled", "next_due_at": due.isoformat()}

    def run_due_schedules(
        self,
        *,
        now: datetime | None = None,
        current_story_available: bool = True,
    ) -> list[dict[str, Any]]:
        """Create bounded private planning intents; never fetch, generate, or publish content."""
        current = _now(now)
        if type(current_story_available) is not bool:
            raise WorkflowError("Current-story availability must be explicit")
        results: list[dict[str, Any]] = []
        rows = self._connection.execute(
            "SELECT * FROM studio_daily_schedules WHERE enabled=1 AND next_due_at<=? ORDER BY next_due_at,schedule_id",
            (current.isoformat(),),
        ).fetchall()
        for row in rows:
            due = parse_datetime(row["next_due_at"])
            interval = timedelta(days=1) if row["recurrence"] == "daily" else timedelta(days=7)
            budget = self._budget("ai_daily_desk")
            while due <= current:
                occurrence = due.isoformat()
                status, reason, fallback_id = "planned", "private_discovery_only", None
                if current - due >= interval:
                    status, reason = "missed", "schedule_occurrence_missed"
                else:
                    try:
                        policy = self._approved_policy(row["policy_id"])
                        run_count = self._connection.execute(
                            "SELECT COUNT(*) FROM studio_daily_runs WHERE created_at>=?",
                            (current.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(),),
                        ).fetchone()[0]
                        if run_count >= budget["max_daily_items"]:
                            status, reason = "blocked", "daily_resource_budget_exhausted"
                        elif current_story_available is False:
                            fallback = self._find_evergreen(
                                policy_id=row["policy_id"],
                                category=row["category"],
                                content_type=row["content_type"],
                                now=current,
                            )
                            if fallback is None:
                                status, reason = "blocked", "no_verified_evergreen_fallback"
                            else:
                                fallback_id = fallback["candidate_id"]
                                reason = "verified_evergreen_fallback_private_draft"
                    except WorkflowError:
                        status, reason = "blocked", "approved_policy_unavailable"
                run_id = f"daily-run-{uuid.uuid4().hex[:16]}"
                with self._connection:
                    self._connection.execute(
                        """
                        INSERT OR IGNORE INTO studio_daily_runs
                            (run_id, schedule_id, occurrence_key, policy_id, category,
                             content_type, status, fallback_candidate_id, reason_code, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            run_id, row["schedule_id"], occurrence, row["policy_id"],
                            row["category"], row["content_type"], status, fallback_id,
                            reason, current.isoformat(),
                        ),
                    )
                    if fallback_id and status == "planned":
                        self._connection.execute(
                            "UPDATE studio_evergreen_candidates SET last_used_at=? WHERE candidate_id=?",
                            (current.isoformat(), fallback_id),
                        )
                    self._audit(
                        "ai_daily_desk", row["schedule_id"], "daily_schedule_tick",
                        "local-scheduler",
                        {"run_id": run_id, "status": status, "reason_code": reason},
                        current,
                    )
                results.append(
                    {
                        "run_id": run_id,
                        "schedule_id": row["schedule_id"],
                        "status": status,
                        "reason_code": reason,
                        "fallback_candidate_id": fallback_id,
                        "publication_enabled": False,
                    }
                )
                due += interval
            with self._connection:
                self._connection.execute(
                    "UPDATE studio_daily_schedules SET next_due_at=? WHERE schedule_id=?",
                    (due.isoformat(), row["schedule_id"]),
                )
        return results

    def create_evergreen_candidate(
        self,
        *,
        director_id: str,
        candidate_id: str,
        policy_id: str,
        category: str,
        content_type: str,
        content_sha256: str,
        evidence_refs: tuple[str, ...] | list[str],
        expires_at: datetime,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        actor, current = self._actor(director_id), _now(now)
        policy = self._approved_policy(policy_id)
        if not isinstance(candidate_id, str) or not _ID.fullmatch(candidate_id):
            raise WorkflowError("Evergreen candidate ID is invalid")
        if category not in policy["categories"] or content_type not in policy["content_types"]:
            raise WorkflowError("Evergreen candidate is outside the approved policy")
        if not isinstance(content_sha256, str) or not _HASH.fullmatch(content_sha256):
            raise WorkflowError("Evergreen candidate requires a content SHA-256")
        refs = _unique_values(evidence_refs, "Evergreen evidence references")
        expiry = _now(expires_at)
        if expiry <= current:
            raise WorkflowError("Evergreen candidate expiry must be in the future")
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO studio_evergreen_candidates
                    (candidate_id, policy_id, category, content_type, content_sha256,
                     evidence_refs_json, status, expires_at, created_by, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)
                """,
                (
                    candidate_id, policy_id, category, content_type, content_sha256,
                    _json(refs), expiry.isoformat(), actor, current.isoformat(),
                ),
            )
            self._audit(
                "ai_daily_desk", candidate_id, "evergreen_candidate_submitted",
                actor, {"content_sha256": content_sha256}, current,
            )
        return {"candidate_id": candidate_id, "status": "pending"}

    def verify_evergreen_candidate(
        self,
        candidate_id: str,
        *,
        director_id: str,
        checks: dict[str, bool],
        now: datetime | None = None,
    ) -> dict[str, Any]:
        actor, current = self._actor(director_id), _now(now)
        if not isinstance(checks, dict) or set(checks) != _EVERGREEN_CHECKS or any(
            type(value) is not bool for value in checks.values()
        ):
            raise WorkflowError("Evergreen verification requires every explicit policy check")
        row = self._connection.execute(
            "SELECT * FROM studio_evergreen_candidates WHERE candidate_id=?",
            (candidate_id,),
        ).fetchone()
        if row is None:
            raise WorkflowError("Evergreen candidate not found")
        passed = all(checks.values()) and bool(json.loads(row["evidence_refs_json"]))
        status = "verified" if passed and parse_datetime(row["expires_at"]) > current else "rejected"
        with self._connection:
            self._connection.execute(
                """
                UPDATE studio_evergreen_candidates SET status=?, verified_by=?, verified_at=?
                WHERE candidate_id=?
                """,
                (status, actor, current.isoformat(), candidate_id),
            )
            self._audit(
                "ai_daily_desk", candidate_id, "evergreen_candidate_verified",
                actor, {"status": status, "checks": checks}, current,
            )
        return {"candidate_id": candidate_id, "status": status}

    def _find_evergreen(
        self, *, policy_id: str, category: str, content_type: str, now: datetime
    ) -> dict[str, Any] | None:
        row = self._connection.execute(
            """
            SELECT * FROM studio_evergreen_candidates
            WHERE policy_id=? AND category=? AND content_type=? AND status='verified'
              AND last_used_at IS NULL AND expires_at>?
            ORDER BY verified_at, candidate_id LIMIT 1
            """,
            (policy_id, category, content_type, now.isoformat()),
        ).fetchone()
        return dict(row) if row else None

    def evaluate_routine_release(
        self,
        *,
        policy_id: str,
        category: str,
        content_type: str,
        channel: str,
        artifact_sha256: str,
        checks: dict[str, bool],
        evidence_refs: tuple[str, ...] | list[str],
    ) -> dict[str, Any]:
        policy = self._approved_policy(policy_id)
        if not isinstance(artifact_sha256, str) or not _HASH.fullmatch(artifact_sha256):
            raise WorkflowError("Release candidate requires a SHA-256")
        refs = _unique_values(evidence_refs, "Release evidence references")
        if not isinstance(checks, dict) or set(checks) != _RELEASE_CHECKS or any(
            type(value) is not bool for value in checks.values()
        ):
            raise WorkflowError("Release evaluation requires every explicit quality and risk check")
        rule = {
            "category": category,
            "content_type": content_type,
            "channel": channel,
        }
        listed = rule in policy["routine_publish_rules"]
        eligible = listed and all(checks.values()) and bool(refs)
        return {
            "eligible_under_policy_contract": eligible,
            "publication_enabled": False,
            "policy_id": policy_id,
            "policy_version": policy["version"],
            "artifact_sha256": artifact_sha256,
            "reason_code": (
                "eligible_for_separate_release_authorization"
                if eligible else
                "policy_rule_missing_or_verification_failed"
            ),
        }

    def dispatch_publish(self, **_kwargs: Any) -> None:
        raise WorkflowError("Publishing is disabled in this local milestone")

    def record_creative_gate(
        self,
        project_id: str,
        gate_id: str,
        *,
        director_id: str,
        outcome: str,
        artifact_sha256: str,
        evidence_refs: tuple[str, ...] | list[str],
        now: datetime | None = None,
    ) -> dict[str, Any]:
        actor, current = self._actor(director_id), _now(now)
        if gate_id not in _CREATIVE_GATES or outcome not in {"pass", "revise", "block"}:
            raise WorkflowError("Creative gate or outcome is invalid")
        if not isinstance(artifact_sha256, str) or not _HASH.fullmatch(artifact_sha256):
            raise WorkflowError("Human gate requires the reviewed artifact SHA-256")
        refs = _unique_values(evidence_refs, "Human gate evidence references")
        if self._route(project_id) != "human_creative_desk":
            raise WorkflowError("Human creative gates apply only to routed creative projects")
        previous_gate = None
        if gate_id == "rough_cut":
            previous_gate = self._latest_gate(project_id, "scope_script")
            if previous_gate is None or previous_gate["outcome"] != "pass":
                raise WorkflowError("Gate 1 scope/script approval is required before Gate 2")
        elif gate_id == "final_release":
            previous_gate = self._latest_gate(project_id, "rough_cut")
            if previous_gate is None or previous_gate["outcome"] != "pass":
                raise WorkflowError("Gate 2 rough-cut approval is required before Gate 3")
            scope_gate = self._latest_gate(project_id, "scope_script")
            if scope_gate is None or scope_gate["outcome"] != "pass":
                raise WorkflowError("Gate 1 scope/script approval is no longer current")
        event_id = f"creative-gate-{uuid.uuid4().hex[:16]}"
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO studio_creative_gate_events
                    (event_id, project_id, gate_id, outcome, artifact_sha256,
                     evidence_refs_json, predecessor_event_id, reviewer_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id, project_id, gate_id, outcome, artifact_sha256,
                    _json(refs), previous_gate["event_id"] if previous_gate else None,
                    actor, current.isoformat(),
                ),
            )
            self._audit(
                "human_creative_desk", project_id, "creative_human_gate_recorded",
                actor,
                {
                    "event_id": event_id, "gate_id": gate_id, "outcome": outcome,
                    "artifact_sha256": artifact_sha256,
                    "predecessor_event_id": previous_gate["event_id"] if previous_gate else None,
                },
                current,
            )
        return {
            "event_id": event_id,
            "project_id": project_id,
            "gate_id": gate_id,
            "outcome": outcome,
            "artifact_sha256": artifact_sha256,
            "predecessor_event_id": previous_gate["event_id"] if previous_gate else None,
        }

    def _latest_gate(self, project_id: str, gate_id: str) -> dict[str, Any] | None:
        row = self._connection.execute(
            """
            SELECT * FROM studio_creative_gate_events
            WHERE project_id=? AND gate_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1
            """,
            (project_id, gate_id),
        ).fetchone()
        return dict(row) if row else None

    def creative_gate_status(self, project_id: str) -> dict[str, Any]:
        if self._route(project_id) != "human_creative_desk":
            raise WorkflowError("Creative gate status is available only for creative projects")
        gates = {gate_id: self._latest_gate(project_id, gate_id) for gate_id in _CREATIVE_GATES}
        scope, rough, final = (gates[gate_id] for gate_id in _CREATIVE_GATES)
        gate2_valid = bool(
            scope and scope["outcome"] == "pass" and rough
            and rough["outcome"] == "pass"
            and rough["predecessor_event_id"] == scope["event_id"]
        )
        gate3_valid = bool(
            gate2_valid and final and final["outcome"] == "pass"
            and final["predecessor_event_id"] == rough["event_id"]
        )
        return {
            "project_id": project_id,
            "gates": gates,
            "next_required_gate": (
                "complete" if gate3_valid else
                "final_release" if gate2_valid else
                "rough_cut" if scope and scope["outcome"] == "pass" else
                "scope_script"
            ),
            "release_approved": gate3_valid,
            "publication_enabled": False,
        }

    def _route(self, project_id: str) -> str:
        if not isinstance(project_id, str) or not PROJECT_ID_PATTERN.fullmatch(project_id):
            raise WorkflowError("Project ID is invalid")
        row = self._connection.execute(
            "SELECT workflow_id FROM studio_workflow_routes WHERE project_id=?",
            (project_id,),
        ).fetchone()
        if row is None:
            raise WorkflowError("Project has not been routed to a workflow")
        if row["workflow_id"] == "unassigned":
            raise WorkflowError("Project requires explicit Director workflow routing")
        return row["workflow_id"]

    def project_workflow(self, project_id: str) -> str:
        if not isinstance(project_id, str) or not PROJECT_ID_PATTERN.fullmatch(project_id):
            raise WorkflowError("Project ID is invalid")
        row = self._connection.execute(
            "SELECT workflow_id FROM studio_workflow_routes WHERE project_id=?",
            (project_id,),
        ).fetchone()
        if row is None:
            raise WorkflowError("Project has not been routed to a workflow")
        return row["workflow_id"]

    def snapshot(self, *, now: datetime | None = None) -> dict[str, Any]:
        current = _now(now)
        projects = self.pipeline.list_projects()
        routes = {
            row["project_id"]: row["workflow_id"]
            for row in self._connection.execute(
                "SELECT project_id, workflow_id FROM studio_workflow_routes"
            )
        }
        policies = [
            {
                "policy_id": row["policy_id"],
                "version": row["version"],
                "status": row["status"],
                "approved_by": row["approved_by"],
            }
            for row in self._connection.execute(
                "SELECT policy_id, version, status, approved_by FROM studio_editorial_policies ORDER BY policy_id,version"
            )
        ]
        runs = [
            dict(row)
            for row in self._connection.execute(
                "SELECT run_id,schedule_id,status,reason_code,created_at FROM studio_daily_runs ORDER BY created_at DESC LIMIT 50"
            )
        ]
        schedules = [
            dict(row)
            for row in self._connection.execute(
                "SELECT schedule_id,recurrence,next_due_at,enabled FROM studio_daily_schedules ORDER BY next_due_at"
            )
        ]
        lane_counts = {
            workflow: {
                "projects": sum(routes.get(project["project_id"]) == workflow for project in projects),
                "active": sum(
                    routes.get(project["project_id"]) == workflow
                    and project["status"] == "active"
                    for project in projects
                ),
                "blocked": sum(
                    routes.get(project["project_id"]) == workflow
                    and project["status"] == "blocked"
                    for project in projects
                ),
            }
            budgets = {
                row["workflow_id"]: dict(row)
                for row in self._connection.execute(
                    "SELECT * FROM studio_workflow_budgets"
                )
            }
            for workflow in WORKFLOWS
        }
        latest_approved = self._connection.execute(
            "SELECT policy_id,version FROM studio_editorial_policies WHERE status='approved' ORDER BY approved_at DESC LIMIT 1"
        ).fetchone()
        return {
            "mode": "local_only",
            "publication_enabled": False,
            "workflows": {
                "ai_daily_desk": {
                    **lane_counts["ai_daily_desk"],
                    "standing_policy": (
                        f"{latest_approved['policy_id']} v{latest_approved['version']}"
                        if latest_approved else "none · Director approval required"
                    ),
                    "schedule_count": len(schedules),
                    "budget": budgets["ai_daily_desk"],
                    "blocked_runs": sum(run["status"] == "blocked" for run in runs),
                    "missed_runs": sum(run["status"] == "missed" for run in runs),
                    "runs": runs,
                    "schedules": schedules,
                    "notice": "Discovery/planning only; no external source access, generation, or publishing.",
                },
                "human_creative_desk": {
                    **lane_counts["human_creative_desk"],
                    "human_gates": list(_CREATIVE_GATES),
                    "budget": budgets["human_creative_desk"],
                    "notice": "Gate 1 scope/script, Gate 2 rough cut, Gate 3 final release; all require explicit human approval.",
                },
            },
            "unassigned_projects": [
                project["project_id"] for project in projects
                if routes.get(project["project_id"]) == "unassigned"
            ],
            "policies": policies,
            "director_assignment_count": self._connection.execute(
                "SELECT COUNT(*) FROM studio_workflow_assignments"
            ).fetchone()[0],
            "audit_chain_valid": self.verify_audit_chain(),
            "now": current.isoformat(),
        }

    def verify_audit_chain(self) -> bool:
        previous = "0" * 64
        rows = self._connection.execute(
            "SELECT * FROM studio_workflow_audit_events ORDER BY sequence"
        ).fetchall()
        for row in rows:
            material = _json(
                {
                    "workflow_id": row["workflow_id"],
                    "object_id": row["object_id"],
                    "event_type": row["event_type"],
                    "actor_id": row["actor_id"],
                    "event_json": row["event_json"],
                    "previous_hash": previous,
                    "created_at": row["created_at"],
                }
            )
            if row["previous_hash"] != previous or not hmac_compare(
                hashlib.sha256(material.encode("utf-8")).hexdigest(),
                row["event_hash"],
            ):
                return False
            previous = row["event_hash"]
        return True


def hmac_compare(left: str, right: str) -> bool:
    import hmac

    return hmac.compare_digest(left, right)
