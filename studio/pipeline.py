"""Persistent Director-controlled project pipeline for the local studio."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import threading
from typing import Any
import uuid

from .observer import ObserverEngine, ObserverFinding, ObserverRunReport
from .registry import RegistryError, RoleRegistry


PROJECT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
LANGUAGE_PATTERN = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")


class ProjectError(ValueError):
    """Raised when a local project operation violates pipeline policy."""


class ProjectNotFound(ProjectError):
    """Raised when a project record does not exist."""


class ProjectStatus(StrEnum):
    DRAFT = "draft"
    QUEUED = "queued"
    ACTIVE = "active"
    PENDING_REVIEW = "pending_review"
    BLOCKED = "blocked"
    READY = "ready"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class ProjectLinkType(StrEnum):
    TASK = "task"
    ISSUE = "issue"
    OBSERVER_FINDING = "observer_finding"


class ProjectPriority(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


ALLOWED_TRANSITIONS: dict[ProjectStatus, frozenset[ProjectStatus]] = {
    ProjectStatus.DRAFT: frozenset({ProjectStatus.QUEUED, ProjectStatus.CANCELLED}),
    ProjectStatus.QUEUED: frozenset(
        {ProjectStatus.ACTIVE, ProjectStatus.BLOCKED, ProjectStatus.CANCELLED}
    ),
    ProjectStatus.ACTIVE: frozenset(
        {ProjectStatus.PENDING_REVIEW, ProjectStatus.BLOCKED, ProjectStatus.CANCELLED}
    ),
    ProjectStatus.PENDING_REVIEW: frozenset(
        {
            ProjectStatus.ACTIVE,
            ProjectStatus.BLOCKED,
            ProjectStatus.READY,
            ProjectStatus.CANCELLED,
        }
    ),
    ProjectStatus.BLOCKED: frozenset(
        {ProjectStatus.QUEUED, ProjectStatus.ACTIVE, ProjectStatus.CANCELLED}
    ),
    ProjectStatus.READY: frozenset(
        {ProjectStatus.ACTIVE, ProjectStatus.COMPLETED, ProjectStatus.CANCELLED}
    ),
    ProjectStatus.COMPLETED: frozenset(),
    ProjectStatus.CANCELLED: frozenset(),
}


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ProjectError("Timestamps must include a timezone")
    return value.astimezone(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _text(value: object, label: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        raise ProjectError(f"{label} must be non-empty and at most {limit} characters")
    return value.strip()


def _choice(value: object, enum: type[StrEnum], label: str) -> str:
    if not isinstance(value, str):
        raise ProjectError(f"Invalid {label}")
    try:
        return enum(value).value
    except ValueError as exc:
        raise ProjectError(f"Invalid {label}") from exc


class ProjectPipeline:
    """SQLite project registry using the local Studio role registry and IDs."""

    def __init__(
        self,
        database: str | Path,
        director_ids: set[str] | frozenset[str],
        registry: RoleRegistry,
    ) -> None:
        self.database = Path(database)
        self.director_ids = frozenset(director_ids)
        if not self.director_ids or any(
            not isinstance(director_id, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", director_id)
            for director_id in self.director_ids
        ):
            raise ProjectError("At least one valid local Director ID must be configured")
        self.registry = registry
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(
            str(self.database), timeout=10, check_same_thread=False
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._create_schema()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def _create_schema(self) -> None:
        with self._connection:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS studio_projects (
                    project_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    project_type TEXT NOT NULL,
                    department_id TEXT NOT NULL,
                    language TEXT NOT NULL,
                    priority TEXT NOT NULL,
                    status TEXT NOT NULL,
                    role_id TEXT NOT NULL,
                    is_demo INTEGER NOT NULL CHECK (is_demo IN (0, 1)),
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS studio_project_links (
                    project_id TEXT NOT NULL REFERENCES studio_projects(project_id),
                    link_type TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    linked_by TEXT NOT NULL,
                    linked_at TEXT NOT NULL,
                    PRIMARY KEY(project_id, link_type, target_id)
                );
                CREATE TABLE IF NOT EXISTS studio_project_observer_findings (
                    finding_id TEXT PRIMARY KEY,
                    observer_id TEXT NOT NULL,
                    code TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    evidence_refs_json TEXT NOT NULL,
                    generated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS studio_project_audit_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    event_json TEXT NOT NULL,
                    previous_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL
                );
                """
            )

    def _require_director(self, director_id: object) -> str:
        if not isinstance(director_id, str) or director_id not in self.director_ids:
            raise ProjectError("Only a configured local Director may manage projects")
        return director_id

    @staticmethod
    def _project_id(project_id: object) -> str:
        if not isinstance(project_id, str) or not PROJECT_ID_PATTERN.fullmatch(project_id):
            raise ProjectError("Project ID must be a simple local identifier")
        return project_id

    def _audit(
        self,
        event_type: str,
        project_id: str,
        actor_id: str,
        data: dict[str, Any],
        created_at: str,
    ) -> None:
        previous = self._connection.execute(
            "SELECT event_hash FROM studio_project_audit_events ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        previous_hash = previous["event_hash"] if previous else "0" * 64
        event_json = _json(data)
        material = _json(
            {
                "event_type": event_type,
                "project_id": project_id,
                "actor_id": actor_id,
                "event_json": event_json,
                "previous_hash": previous_hash,
                "created_at": created_at,
            }
        )
        event_hash = hashlib.sha256(material.encode("utf-8")).hexdigest()
        self._connection.execute(
            """
            INSERT INTO studio_project_audit_events
                (event_type, project_id, actor_id, event_json, previous_hash, event_hash, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (event_type, project_id, actor_id, event_json, previous_hash, event_hash, created_at),
        )

    def create_project(
        self,
        *,
        director_id: str,
        title: str,
        project_type: str,
        department_id: str,
        language: str,
        priority: str,
        role_id: str,
        project_id: str | None = None,
        is_demo: bool = False,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        actor = self._require_director(director_id)
        title = _text(title, "Project title", 200)
        project_type = _text(project_type, "Project type", 80)
        department_id = _text(department_id, "Department ID", 16)
        language = _text(language, "Language", 35)
        if not LANGUAGE_PATTERN.fullmatch(language):
            raise ProjectError("Language must be a BCP-47-style language tag")
        priority_value = _choice(priority, ProjectPriority, "project priority")
        project_id_value = (
            self._project_id(project_id)
            if project_id is not None
            else f"project-{uuid.uuid4().hex}"
        )
        if not isinstance(is_demo, bool):
            raise ProjectError("Synthetic demo marker must be a boolean")
        try:
            role = self.registry.get(role_id)
        except (RegistryError, TypeError) as exc:
            raise ProjectError("Project role must exist in the registered role catalog") from exc
        if role.department_id != department_id:
            raise ProjectError("Selected role does not belong to the selected department")
        created_at = _timestamp(now or datetime.now(timezone.utc))
        with self._lock, self._connection:
            try:
                self._connection.execute(
                    """
                    INSERT INTO studio_projects (
                        project_id, title, project_type, department_id, language, priority,
                        status, role_id, is_demo, created_by, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        project_id_value,
                        title,
                        project_type,
                        department_id,
                        language,
                        priority_value,
                        ProjectStatus.DRAFT.value,
                        role_id,
                        int(is_demo),
                        actor,
                        created_at,
                        created_at,
                    ),
                )
                self._audit(
                    "project_created",
                    project_id_value,
                    actor,
                    {
                        "status": ProjectStatus.DRAFT.value,
                        "department_id": department_id,
                        "role_id": role_id,
                        "is_demo": is_demo,
                    },
                    created_at,
                )
            except sqlite3.IntegrityError as exc:
                raise ProjectError(f"Project ID already exists: {project_id_value}") from exc
        return self.get_project(project_id_value)

    def get_project(self, project_id: str) -> dict[str, Any]:
        clean_id = self._project_id(project_id)
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM studio_projects WHERE project_id = ?", (clean_id,)
            ).fetchone()
            if row is None:
                raise ProjectNotFound("Project not found")
            result = dict(row)
            result["is_demo"] = bool(result["is_demo"])
            return result

    def list_projects(
        self,
        *,
        status: str | None = None,
        department_id: str | None = None,
        project_type: str | None = None,
    ) -> list[dict[str, Any]]:
        filters: list[str] = []
        parameters: list[str] = []
        if status is not None:
            filters.append("status = ?")
            parameters.append(_choice(status, ProjectStatus, "project status"))
        if department_id:
            filters.append("department_id = ?")
            parameters.append(_text(department_id, "Department filter", 16))
        if project_type:
            filters.append("project_type = ?")
            parameters.append(_text(project_type, "Project type filter", 80))
        where = f" WHERE {' AND '.join(filters)}" if filters else ""
        with self._lock:
            rows = self._connection.execute(
                f"SELECT * FROM studio_projects{where} ORDER BY created_at DESC, project_id",
                parameters,
            ).fetchall()
        results = []
        for row in rows:
            project = dict(row)
            project["is_demo"] = bool(project["is_demo"])
            results.append(project)
        return results

    def project_detail(self, project_id: str) -> dict[str, Any]:
        project = self.get_project(project_id)
        project["allowed_transitions"] = sorted(
            status.value for status in ALLOWED_TRANSITIONS[ProjectStatus(project["status"])]
        )
        with self._lock:
            links = [
                dict(row)
                for row in self._connection.execute(
                    """
                    SELECT link_type, target_id, linked_by, linked_at
                    FROM studio_project_links WHERE project_id = ?
                    ORDER BY linked_at, link_type, target_id
                    """,
                    (project_id,),
                )
            ]
            for link in links:
                link["details"] = self._link_details(
                    ProjectLinkType(link["link_type"]), link["target_id"]
                )
            project["links"] = links
            project["audit_history"] = list(self.audit_events(project_id))
            project["history"] = [
                event
                for event in project["audit_history"]
                if event["event_type"] in {"project_created", "project_status_changed"}
            ]
        return project

    def _link_details(self, link_type: ProjectLinkType, target_id: str) -> dict[str, Any]:
        if link_type is ProjectLinkType.TASK and self._table_exists("tasks"):
            row = self._connection.execute(
                "SELECT * FROM tasks WHERE task_id = ?", (target_id,)
            ).fetchone()
            if row is not None:
                task = dict(row)
                return {
                    key: task[key]
                    for key in ("title", "status", "department_id", "role_id")
                    if key in task
                }
        elif link_type is ProjectLinkType.ISSUE and self._table_exists("studio_issues"):
            row = self._connection.execute(
                """
                SELECT description, status, severity FROM studio_issues WHERE issue_id = ?
                """,
                (target_id,),
            ).fetchone()
            if row is not None:
                return dict(row)
        elif link_type is ProjectLinkType.OBSERVER_FINDING:
            row = self._connection.execute(
                """
                SELECT observer_id, code, severity, subject_id
                FROM studio_project_observer_findings WHERE finding_id = ?
                """,
                (target_id,),
            ).fetchone()
            if row is not None:
                return dict(row)
        return {}

    def transition_status(
        self,
        project_id: str,
        status: str,
        *,
        director_id: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        actor = self._require_director(director_id)
        clean_id = self._project_id(project_id)
        try:
            target = ProjectStatus(status)
        except (ValueError, TypeError) as exc:
            raise ProjectError("Invalid project status") from exc
        changed_at = _timestamp(now or datetime.now(timezone.utc))
        with self._lock, self._connection:
            project = self._connection.execute(
                "SELECT status FROM studio_projects WHERE project_id = ?", (clean_id,)
            ).fetchone()
            if project is None:
                raise ProjectNotFound("Project not found")
            current = ProjectStatus(project["status"])
            if target not in ALLOWED_TRANSITIONS[current]:
                raise ProjectError(
                    f"Project status cannot transition from {current.value} to {target.value}"
                )
            self._connection.execute(
                """
                UPDATE studio_projects SET status = ?, updated_at = ? WHERE project_id = ?
                """,
                (target.value, changed_at, clean_id),
            )
            self._audit(
                "project_status_changed",
                clean_id,
                actor,
                {"from": current.value, "to": target.value},
                changed_at,
            )
        return self.get_project(clean_id)

    def _link_exists(self, link_type: ProjectLinkType, target_id: str) -> bool:
        if link_type is ProjectLinkType.TASK:
            table = self._table_exists("tasks")
            if not table:
                return False
            return self._connection.execute(
                "SELECT 1 FROM tasks WHERE task_id = ?", (target_id,)
            ).fetchone() is not None
        if link_type is ProjectLinkType.ISSUE:
            if not self._table_exists("studio_issues"):
                return False
            return self._connection.execute(
                "SELECT 1 FROM studio_issues WHERE issue_id = ?", (target_id,)
            ).fetchone() is not None
        return self._connection.execute(
            "SELECT 1 FROM studio_project_observer_findings WHERE finding_id = ?",
            (target_id,),
        ).fetchone() is not None

    def _table_exists(self, table: str) -> bool:
        return self._connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
        ).fetchone() is not None

    def link_record(
        self,
        project_id: str,
        link_type: str,
        target_id: str,
        *,
        director_id: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        actor = self._require_director(director_id)
        clean_id = self._project_id(project_id)
        try:
            kind = ProjectLinkType(link_type)
        except (ValueError, TypeError) as exc:
            raise ProjectError("Invalid project link type") from exc
        target = _text(target_id, "Linked record ID", 256)
        linked_at = _timestamp(now or datetime.now(timezone.utc))
        with self._lock, self._connection:
            if not self._connection.execute(
                "SELECT 1 FROM studio_projects WHERE project_id = ?", (clean_id,)
            ).fetchone():
                raise ProjectNotFound("Project not found")
            if not self._link_exists(kind, target):
                raise ProjectError(f"{kind.value} record not found in the local ledger")
            try:
                self._connection.execute(
                    """
                    INSERT INTO studio_project_links
                        (project_id, link_type, target_id, linked_by, linked_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (clean_id, kind.value, target, actor, linked_at),
                )
            except sqlite3.IntegrityError as exc:
                raise ProjectError("This record is already linked to the project") from exc
            self._audit(
                "project_record_linked",
                clean_id,
                actor,
                {"link_type": kind.value, "target_id": target},
                linked_at,
            )
        return self.project_detail(clean_id)

    def record_observer_findings(
        self, report: ObserverRunReport
    ) -> tuple[str, ...]:
        if not isinstance(report, ObserverRunReport):
            raise ProjectError("Only a read-only ObserverRunReport can be recorded")
        generated_at = _timestamp(report.generated_at)
        identifiers: list[str] = []
        with self._lock, self._connection:
            for finding in report.findings:
                self._save_observer_finding(finding, generated_at)
                identifiers.append(finding.finding_id)
        return tuple(identifiers)

    def refresh_observer_findings(
        self, *, now: datetime | None = None
    ) -> tuple[dict[str, str], ...]:
        report = ObserverEngine(
            self.database, self.registry, self.director_ids
        ).inspect(now=now or datetime.now(timezone.utc))
        self.record_observer_findings(report)
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT finding_id, observer_id, code, severity, subject_id
                FROM studio_project_observer_findings
                WHERE generated_at = ?
                ORDER BY severity, observer_id, code, subject_id
                """,
                (_timestamp(report.generated_at),),
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def _save_observer_finding(
        self, finding: ObserverFinding, generated_at: str
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO studio_project_observer_findings (
                finding_id, observer_id, code, severity, subject_id,
                evidence_refs_json, generated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(finding_id) DO UPDATE SET
                observer_id = excluded.observer_id,
                code = excluded.code,
                severity = excluded.severity,
                subject_id = excluded.subject_id,
                evidence_refs_json = excluded.evidence_refs_json,
                generated_at = excluded.generated_at
            """,
            (
                finding.finding_id,
                finding.observer_id,
                finding.code,
                finding.severity.value,
                finding.subject_id,
                _json(finding.evidence_refs),
                generated_at,
            ),
        )

    def link_options(self) -> dict[str, list[dict[str, str]]]:
        with self._lock:
            tasks = (
                [
                    {"id": row["task_id"], "label": row["task_id"]}
                    for row in self._connection.execute(
                        "SELECT task_id FROM tasks ORDER BY task_id"
                    )
                ]
                if self._table_exists("tasks")
                else []
            )
            issues = (
                [
                    {"id": row["issue_id"], "label": row["description"]}
                    for row in self._connection.execute(
                        "SELECT issue_id, description FROM studio_issues ORDER BY created_at DESC"
                    )
                ]
                if self._table_exists("studio_issues")
                else []
            )
            findings = [
                {
                    "id": row["finding_id"],
                    "label": f"{row['code']} · {row['severity']} · {row['subject_id']}",
                }
                for row in self._connection.execute(
                    """
                    SELECT finding_id, code, severity, subject_id
                    FROM studio_project_observer_findings ORDER BY generated_at DESC
                    """
                )
            ]
        return {"task": tasks, "issue": issues, "observer_finding": findings}

    def audit_events(self, project_id: str | None = None) -> tuple[dict[str, Any], ...]:
        with self._lock:
            if project_id is None:
                rows = self._connection.execute(
                    "SELECT * FROM studio_project_audit_events ORDER BY sequence"
                ).fetchall()
            else:
                rows = self._connection.execute(
                    """
                    SELECT * FROM studio_project_audit_events
                    WHERE project_id = ? ORDER BY sequence
                    """,
                    (project_id,),
                ).fetchall()
            return tuple(dict(row) for row in rows)

    def verify_audit_chain(self) -> bool:
        previous_hash = "0" * 64
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM studio_project_audit_events ORDER BY sequence"
            ).fetchall()
        for row in rows:
            if row["previous_hash"] != previous_hash:
                return False
            material = _json(
                {
                    "event_type": row["event_type"],
                    "project_id": row["project_id"],
                    "actor_id": row["actor_id"],
                    "event_json": row["event_json"],
                    "previous_hash": row["previous_hash"],
                    "created_at": row["created_at"],
                }
            )
            expected = hashlib.sha256(material.encode("utf-8")).hexdigest()
            if expected != row["event_hash"]:
                return False
            previous_hash = expected
        return True

    def snapshot(
        self,
        *,
        status: str | None = None,
        department_id: str | None = None,
        project_type: str | None = None,
    ) -> dict[str, Any]:
        projects = self.list_projects(
            status=status, department_id=department_id, project_type=project_type
        )
        counts = {status.value: 0 for status in ProjectStatus}
        for project in self.list_projects():
            counts[project["status"]] += 1
        roles = [
            {
                "role_id": role.role_id,
                "title": role.title,
                "department_id": role.department_id,
            }
            for role in self.registry.all_roles()
        ]
        department_ids = sorted({role["department_id"] for role in roles})
        return {
            "mode": "Local project pipeline; synthetic records are explicitly marked",
            "counts": counts,
            "projects": projects,
            "departments": department_ids,
            "roles": roles,
        }
