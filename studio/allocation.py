"""Director-controlled, local-only task planning and queue persistence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import stat
import threading
from pathlib import Path
from typing import Any, Sequence
import uuid

from .models import (
    AssignmentManifest,
    MAX_ASSIGNMENT_HORIZON,
    StudioError,
    parse_datetime,
    validate_assignment,
)
from .pipeline import LANGUAGE_PATTERN, PROJECT_ID_PATTERN
from .registry import RegistryError, RoleRegistry


MAX_TASK_ATTEMPTS = 3
MAX_EVIDENCE_BYTES = 256 * 1024
ALLOCATION_OBSERVER_ID = "observer-task-allocation"
BASE_AUDIT_HASH = "0" * 64
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_ERROR_CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_PRIORITIES = frozenset({"low", "normal", "high", "urgent"})
_OUTPUT_FORMATS = frozenset({"markdown", "html", "script", "platform_package"})


class AllocationError(ValueError):
    """Raised when a task plan or queue operation violates local policy."""


class PlanStatus(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"


class AllocationTaskStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    ACTIVE = "active"
    BLOCKED = "blocked"
    REVIEW = "review"
    COMPLETED = "completed"


@dataclass(frozen=True)
class TaskDefinition:
    key: str
    title: str
    department_id: str
    role_id: str
    description: str
    dependencies: tuple[str, ...]
    evidence_requirements: tuple[str, ...]


@dataclass(frozen=True)
class ObserverFinding:
    code: str
    severity: str
    subject_id: str
    evidence_refs: tuple[str, ...]


_TEMPLATES: dict[str, tuple[TaskDefinition, ...]] = {
    "news_article": (
        TaskDefinition(
            "research_scope", "Define research scope", "D02", "D02-R01",
            "Prepare the research questions and source scope.", (), ("research_brief",),
        ),
        TaskDefinition(
            "source_collection", "Collect primary sources", "D02", "D02-R03",
            "Identify and document primary sources for the approved scope.",
            ("research_scope",), ("source_list",),
        ),
        TaskDefinition(
            "article_draft", "Prepare article draft", "D03", "D03-R03",
            "Prepare a draft from the approved source list; do not publish it.",
            ("source_collection",), ("draft_artifact",),
        ),
        TaskDefinition(
            "fact_check", "Check material claims", "D03", "D03-R09",
            "Record claim coverage, supporting evidence, and unresolved disputes.",
            ("article_draft",), ("claim_ledger",),
        ),
        TaskDefinition(
            "copy_review", "Review copy and attribution", "D03", "D03-R07",
            "Review language, style, and consistency without changing verified facts.",
            ("fact_check",), ("edit_checklist",),
        ),
    ),
    "research_documentary": (
        TaskDefinition(
            "research_scope", "Define documentary research", "D02", "D02-R01",
            "Prepare research questions and a bounded documentary source scope.",
            (), ("research_brief",),
        ),
        TaskDefinition(
            "source_dossier", "Prepare source dossier", "D05", "D05-R02",
            "Document source records, limitations, and unresolved questions.",
            ("research_scope",), ("source_dossier",),
        ),
        TaskDefinition(
            "story_outline", "Prepare documentary outline", "D05", "D05-R03",
            "Create an evidence-led story outline and mark unsupported claims.",
            ("source_dossier",), ("story_outline",),
        ),
        TaskDefinition(
            "script_draft", "Prepare documentary script draft", "D05", "D05-R04",
            "Prepare a draft script from the approved outline; no recording or rendering.",
            ("story_outline",), ("script_draft",),
        ),
        TaskDefinition(
            "production_plan", "Prepare production plan", "D05", "D05-R07",
            "List proposed production steps and required rights checks; do not execute.",
            ("script_draft",), ("production_plan",),
        ),
    ),
    "youtube_video": (
        TaskDefinition(
            "video_brief", "Define video brief", "D05", "D05-R01",
            "Record the approved audience, purpose, format, and production boundaries.",
            (), ("video_brief",),
        ),
        TaskDefinition(
            "research_pack", "Prepare video research pack", "D02", "D02-R20",
            "Package traceable sources and unresolved evidence questions.",
            ("video_brief",), ("source_pack",),
        ),
        TaskDefinition(
            "script_draft", "Prepare video script draft", "D05", "D05-R04",
            "Prepare a script draft based on the research pack; no recording.",
            ("research_pack",), ("script_draft",),
        ),
        TaskDefinition(
            "storyboard", "Prepare storyboard plan", "D05", "D05-R09",
            "Describe proposed scenes and visuals with source and rights references.",
            ("script_draft",), ("storyboard",),
        ),
        TaskDefinition(
            "delivery_review", "Review accessibility and delivery requirements",
            "D05", "D05-R19",
            "Check the draft package against accessibility and technical delivery gates.",
            ("storyboard",), ("delivery_checklist",),
        ),
    ),
    "social_media_campaign": (
        TaskDefinition(
            "campaign_strategy", "Define campaign strategy", "D06", "D06-R01",
            "Record the approved campaign objective, audience, and channel constraints.",
            (), ("campaign_brief",),
        ),
        TaskDefinition(
            "campaign_calendar", "Prepare campaign calendar", "D06", "D06-R02",
            "Propose an internal schedule; do not schedule publication or delivery.",
            ("campaign_strategy",), ("campaign_calendar",),
        ),
        TaskDefinition(
            "copy_drafts", "Prepare social copy drafts", "D06", "D06-R03",
            "Prepare draft copy for review; do not post or send it.",
            ("campaign_calendar",), ("copy_package",),
        ),
        TaskDefinition(
            "fact_check", "Review campaign claims", "D06", "D06-R10",
            "Check claims against project evidence and flag unsupported wording.",
            ("copy_drafts",), ("claim_check",),
        ),
        TaskDefinition(
            "release_package", "Prepare internal release package", "D06", "D06-R19",
            "Package approved drafts and review records for Director inspection only.",
            ("fact_check",), ("release_package",),
        ),
    ),
}


def topological_task_order(
    definitions: Sequence[TaskDefinition],
) -> tuple[TaskDefinition, ...]:
    """Validate task keys and dependencies, then return stable dependency order."""
    by_key: dict[str, TaskDefinition] = {}
    for definition in definitions:
        if not isinstance(definition, TaskDefinition) or not _ID_PATTERN.fullmatch(definition.key):
            raise AllocationError("Task keys must be unique simple identifiers")
        if definition.key in by_key:
            raise AllocationError(f"Duplicate task key: {definition.key}")
        by_key[definition.key] = definition
    for definition in definitions:
        if len(set(definition.dependencies)) != len(definition.dependencies):
            raise AllocationError(f"Duplicate dependency in task {definition.key}")
        missing = set(definition.dependencies) - by_key.keys()
        if missing:
            raise AllocationError(
                f"Task {definition.key} references unknown dependencies: {', '.join(sorted(missing))}"
            )
        if definition.key in definition.dependencies:
            raise AllocationError(f"Task {definition.key} cannot depend on itself")

    result: list[TaskDefinition] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(key: str) -> None:
        if key in visiting:
            raise AllocationError(f"Task dependency cycle includes {key}")
        if key in visited:
            return
        visiting.add(key)
        for dependency in by_key[key].dependencies:
            visit(dependency)
        visiting.remove(key)
        visited.add(key)
        result.append(by_key[key])

    for definition in definitions:
        visit(definition.key)
    return tuple(result)


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise AllocationError("Timestamps must include a timezone")
    return value.astimezone(timezone.utc).isoformat()


def _allocation_plan_hash(
    plan: dict[str, Any], tasks: Sequence[dict[str, Any]]
) -> str:
    material = {
        key: plan[key]
        for key in (
            "plan_id", "project_id", "template", "director_instructions",
            "output_format", "language", "priority", "deadline_at", "request_hash",
        )
    }
    material["tasks"] = [
        {
            key: task[key]
            for key in (
                "task_id", "parent_project_id", "task_key", "title",
                "department_id", "role_id", "description", "priority",
                "dependencies_json", "evidence_requirements_json",
                "verification_gates_json",
            )
        }
        for task in tasks
    ]
    return hashlib.sha256(_json(material).encode("utf-8")).hexdigest()


def _datetime(value: object, label: str) -> datetime:
    try:
        if isinstance(value, datetime):
            parsed = value
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                raise ValueError
            return parsed.astimezone(timezone.utc)
        return parse_datetime(value)
    except (StudioError, TypeError, ValueError) as exc:
        raise AllocationError(f"{label} must be an ISO-8601 timestamp with timezone") from exc


class TaskAllocationObserver:
    """Independent deterministic policy checks over persisted allocation records."""

    observer_id = ALLOCATION_OBSERVER_ID
    _statuses = {status.value for status in AllocationTaskStatus}

    def __init__(self, registry: RoleRegistry, director_ids: frozenset[str]) -> None:
        self.registry = registry
        self.director_ids = director_ids

    def inspect(
        self,
        plan: dict[str, Any],
        tasks: Sequence[dict[str, Any]],
        approvals: Sequence[dict[str, Any]],
        *,
        now: datetime,
    ) -> tuple[ObserverFinding, ...]:
        findings: list[ObserverFinding] = []

        def finding(code: str, subject: str, *evidence: str) -> None:
            findings.append(
                ObserverFinding(
                    code=code,
                    severity="block",
                    subject_id=subject,
                    evidence_refs=tuple(sorted(set(evidence))),
                )
            )

        plan_id = plan["plan_id"]
        if plan.get("project_status") not in {"queued", "active"}:
            finding("PROJECT_APPROVAL_NOT_ACTIVE", plan_id, f"project:{plan['project_id']}")
        if plan.get("project_created_by") not in self.director_ids:
            finding("PROJECT_DIRECTOR_INVALID", plan_id, f"project:{plan['project_id']}")
        try:
            manifest = AssignmentManifest.from_json(plan["manifest_json"])
            validate_assignment(
                manifest,
                now=now,
                director_ids=self.director_ids,
                check_expiry=False,
            )
        except (StudioError, TypeError, ValueError):
            finding("ALLOCATION_HORIZON_INVALID", plan_id, f"plan:{plan_id}")

        approved = plan["status"] == PlanStatus.APPROVED.value
        if approved:
            expected_hash = _allocation_plan_hash(plan, tasks)
            matching_approval = any(
                approval["approval_type"] == "plan"
                and approval["approver_id"] == plan["approved_by"]
                and approval["approver_id"] in self.director_ids
                and approval["plan_hash"] == expected_hash
                for approval in approvals
            )
            if not matching_approval:
                finding("ALLOCATION_APPROVAL_MISSING_OR_MISMATCHED", plan_id, f"plan:{plan_id}")
        if not approved and any(task["status"] != AllocationTaskStatus.PENDING.value for task in tasks):
            finding("TASK_ACTIVE_WITHOUT_PLAN_APPROVAL", plan_id, f"plan:{plan_id}")

        by_id = {task["task_id"]: task for task in tasks}
        graph: list[TaskDefinition] = []
        for task in tasks:
            task_id = task["task_id"]
            if task["parent_project_id"] != plan["project_id"]:
                finding("TASK_PARENT_PROJECT_MISMATCH", task_id, f"task:{task_id}")
            if task["status"] not in self._statuses:
                finding("TASK_STATUS_INVALID", task_id, f"task:{task_id}")
            if task["priority"] not in _PRIORITIES:
                finding("TASK_PRIORITY_INVALID", task_id, f"task:{task_id}")
            try:
                role = self.registry.get(task["role_id"])
            except (RegistryError, KeyError, TypeError):
                finding("TASK_ROLE_UNKNOWN", task_id, f"task:{task_id}", f"role:{task['role_id']}")
            else:
                if role.department_id != task["department_id"]:
                    finding(
                        "TASK_DEPARTMENT_MISMATCH",
                        task_id,
                        f"task:{task_id}",
                        f"role:{task['role_id']}",
                    )
            try:
                dependency_values = json.loads(task["dependencies_json"])
                if not isinstance(dependency_values, list) or any(
                    not isinstance(value, str) or not _ID_PATTERN.fullmatch(value)
                    for value in dependency_values
                ):
                    raise ValueError
                dependencies = tuple(dependency_values)
            except (json.JSONDecodeError, TypeError, ValueError):
                finding("TASK_DEPENDENCIES_INVALID", task_id, f"task:{task_id}")
                dependencies = ()
            for dependency_id in dependencies:
                if dependency_id not in by_id:
                    finding(
                        "TASK_DEPENDENCY_UNKNOWN",
                        task_id,
                        f"task:{task_id}",
                        f"dependency:{dependency_id}",
                    )
            try:
                evidence_requirements = json.loads(task["evidence_requirements_json"])
                evidence_values = json.loads(task["evidence_json"])
                if (
                    not isinstance(evidence_requirements, list)
                    or not isinstance(evidence_values, list)
                    or any(not isinstance(value, str) for value in evidence_requirements + evidence_values)
                ):
                    raise ValueError
            except (json.JSONDecodeError, TypeError, ValueError):
                finding("TASK_EVIDENCE_INVALID", task_id, f"task:{task_id}")
                evidence_requirements, evidence_values = [], []
            graph.append(
                TaskDefinition(
                    key=task_id,
                    title=task["title"],
                    department_id=task["department_id"],
                    role_id=task["role_id"],
                    description=task["description"],
                    dependencies=dependencies,
                    evidence_requirements=tuple(evidence_requirements),
                )
            )
            if task["attempts"] > task["max_attempts"]:
                finding("TASK_RETRY_LIMIT_EXCEEDED", task_id, f"task:{task_id}")
            if (
                task["status"] in {
                    AllocationTaskStatus.REVIEW.value,
                    AllocationTaskStatus.COMPLETED.value,
                }
            ):
                validation = task.get("_evidence_validation")
                if not isinstance(validation, dict) or not validation.get("is_valid"):
                    finding(
                        "TASK_EVIDENCE_ARTIFACT_INVALID",
                        task_id,
                        f"task:{task_id}",
                        *(
                            tuple(validation.get("errors", ()))
                            if isinstance(validation, dict)
                            else ("evidence-validation-unavailable",)
                        ),
                    )
            for dependency_id in dependencies:
                dependency = by_id.get(dependency_id)
                if dependency and dependency["status"] != AllocationTaskStatus.COMPLETED.value:
                    if task["status"] in {
                        AllocationTaskStatus.READY.value,
                        AllocationTaskStatus.ACTIVE.value,
                        AllocationTaskStatus.REVIEW.value,
                        AllocationTaskStatus.COMPLETED.value,
                    }:
                        finding(
                            "TASK_DEPENDENCY_ORDER_VIOLATION",
                            task_id,
                            f"task:{task_id}",
                            f"dependency:{dependency_id}",
                        )
        try:
            topological_task_order(graph)
        except AllocationError:
            finding("TASK_DEPENDENCY_GRAPH_INVALID", plan_id, f"plan:{plan_id}")

        unique_findings: dict[tuple[str, str], ObserverFinding] = {}
        for item in findings:
            unique_findings[(item.code, item.subject_id)] = item
        return tuple(unique_findings.values())


class TaskAllocationEngine:
    """Create draft task plans and manage the Director-controlled local queue."""

    def __init__(
        self,
        database: str | Path,
        registry: RoleRegistry,
        director_ids: set[str] | frozenset[str],
    ) -> None:
        self.database = Path(database)
        self.registry = registry
        self.director_ids = frozenset(director_ids)
        if not self.director_ids or any(
            not isinstance(item, str) or not _ID_PATTERN.fullmatch(item)
            for item in self.director_ids
        ):
            raise AllocationError("At least one valid local Director ID must be configured")
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(
            str(self.database), timeout=10, check_same_thread=False
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._create_schema()
        self.observer = TaskAllocationObserver(self.registry, self.director_ids)

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def _create_schema(self) -> None:
        with self._connection:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS studio_task_plans (
                    plan_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL UNIQUE REFERENCES studio_projects(project_id),
                    template TEXT NOT NULL,
                    director_instructions TEXT NOT NULL,
                    output_format TEXT NOT NULL,
                    language TEXT NOT NULL,
                    priority TEXT NOT NULL,
                    deadline_at TEXT NOT NULL,
                    manifest_json TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('draft', 'approved')),
                    created_by TEXT NOT NULL,
                    approved_by TEXT,
                    is_demo INTEGER NOT NULL CHECK (is_demo IN (0, 1)),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS studio_allocation_tasks (
                    task_id TEXT PRIMARY KEY,
                    plan_id TEXT NOT NULL REFERENCES studio_task_plans(plan_id),
                    parent_project_id TEXT NOT NULL REFERENCES studio_projects(project_id),
                    task_key TEXT NOT NULL,
                    title TEXT NOT NULL,
                    department_id TEXT NOT NULL,
                    role_id TEXT NOT NULL,
                    description TEXT NOT NULL,
                    priority TEXT NOT NULL,
                    dependencies_json TEXT NOT NULL,
                    evidence_requirements_json TEXT NOT NULL,
                    verification_gates_json TEXT NOT NULL,
                    evidence_json TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL CHECK (status IN ('pending', 'ready', 'active', 'blocked', 'review', 'completed')),
                    attempts INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL,
                    last_error_code TEXT,
                    retryable INTEGER NOT NULL DEFAULT 0 CHECK (retryable IN (0, 1)),
                    sort_order INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    UNIQUE(plan_id, task_key)
                );
                CREATE TABLE IF NOT EXISTS studio_evidence_artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL REFERENCES studio_allocation_tasks(task_id),
                    evidence_type TEXT NOT NULL,
                    storage_name TEXT NOT NULL UNIQUE,
                    sha256 TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    submitted_by TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS studio_plan_approvals (
                    approval_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    plan_id TEXT NOT NULL REFERENCES studio_task_plans(plan_id),
                    approver_id TEXT NOT NULL,
                    approval_type TEXT NOT NULL CHECK (approval_type IN ('plan', 'extension')),
                    plan_hash TEXT NOT NULL,
                    approved_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS studio_allocation_failures (
                    failure_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL REFERENCES studio_allocation_tasks(task_id),
                    attempt_no INTEGER NOT NULL,
                    error_code TEXT NOT NULL,
                    retryable INTEGER NOT NULL CHECK (retryable IN (0, 1)),
                    failed_at TEXT NOT NULL,
                    UNIQUE(task_id, attempt_no)
                );
                CREATE TABLE IF NOT EXISTS studio_allocation_observer_runs (
                    run_id TEXT PRIMARY KEY,
                    plan_id TEXT NOT NULL REFERENCES studio_task_plans(plan_id),
                    observer_id TEXT NOT NULL,
                    is_clear INTEGER NOT NULL CHECK (is_clear IN (0, 1)),
                    generated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS studio_allocation_observer_findings (
                    finding_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES studio_allocation_observer_runs(run_id),
                    code TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    evidence_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS studio_allocation_audit_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    plan_id TEXT NOT NULL REFERENCES studio_task_plans(plan_id),
                    object_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
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
            raise AllocationError("Only a configured local Director may manage task plans")
        return director_id

    @staticmethod
    def template_names() -> tuple[str, ...]:
        return tuple(_TEMPLATES)

    def _audit(
        self,
        plan_id: str,
        object_id: str,
        event_type: str,
        actor_id: str,
        data: dict[str, Any],
        at: datetime,
    ) -> None:
        previous = self._connection.execute(
            "SELECT event_hash FROM studio_allocation_audit_events ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        previous_hash = previous["event_hash"] if previous else BASE_AUDIT_HASH
        event_json = _json(data)
        created_at = _timestamp(at)
        material = _json(
            {
                "plan_id": plan_id,
                "object_id": object_id,
                "event_type": event_type,
                "actor_id": actor_id,
                "event_json": event_json,
                "previous_hash": previous_hash,
                "created_at": created_at,
            }
        )
        event_hash = hashlib.sha256(material.encode("utf-8")).hexdigest()
        self._connection.execute(
            """
            INSERT INTO studio_allocation_audit_events
                (plan_id, object_id, event_type, actor_id, event_json,
                 previous_hash, event_hash, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                plan_id, object_id, event_type, actor_id, event_json,
                previous_hash, event_hash, created_at,
            ),
        )

    def _project(self, project_id: object) -> dict[str, Any]:
        if not isinstance(project_id, str) or not PROJECT_ID_PATTERN.fullmatch(project_id):
            raise AllocationError("Project ID must be a simple local identifier")
        row = self._connection.execute(
            "SELECT * FROM studio_projects WHERE project_id = ?", (project_id,)
        ).fetchone()
        if row is None:
            raise AllocationError("Project not found")
        project = dict(row)
        if project["created_by"] not in self.director_ids:
            raise AllocationError("Project has no verified Director authorization")
        if project["status"] not in {"queued", "active"}:
            raise AllocationError(
                "Project must be explicitly approved by moving it to Queued or Active"
            )
        return project

    @staticmethod
    def _definition_payload(definition: TaskDefinition) -> dict[str, Any]:
        return {
            "key": definition.key,
            "title": definition.title,
            "department_id": definition.department_id,
            "role_id": definition.role_id,
            "description": definition.description,
            "dependencies": list(definition.dependencies),
            "evidence_requirements": list(definition.evidence_requirements),
        }

    def generate_task_plan(
        self,
        project_id: str,
        *,
        director_id: str,
        template: str,
        director_instructions: str = "",
        output_format: str = "markdown",
        language: str | None = None,
        priority: str | None = None,
        deadline_at: datetime | str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        actor = self._require_director(director_id)
        current = now or datetime.now(timezone.utc)
        current_text = _timestamp(current)
        project = self._project(project_id)
        if not isinstance(template, str) or template not in _TEMPLATES:
            raise AllocationError("Unknown task planning template")
        if not isinstance(director_instructions, str) or len(director_instructions) > 2000:
            raise AllocationError("Director instructions must be text of at most 2000 characters")
        instructions = director_instructions.strip()
        if not isinstance(output_format, str) or output_format not in _OUTPUT_FORMATS:
            raise AllocationError("Unsupported desired output format")
        plan_language = language if language is not None else project["language"]
        if not isinstance(plan_language, str) or not LANGUAGE_PATTERN.fullmatch(plan_language):
            raise AllocationError("Language must be a BCP-47-style language tag")
        plan_priority = priority if priority is not None else project["priority"]
        if not isinstance(plan_priority, str) or plan_priority not in _PRIORITIES:
            raise AllocationError("Invalid task plan priority")
        due = _datetime(deadline_at, "Deadline") if deadline_at is not None else current + timedelta(days=7)
        if due <= current:
            raise AllocationError("Deadline must be in the future")
        if due - current > MAX_ASSIGNMENT_HORIZON:
            raise AllocationError("Deadline exceeds the 14-day Director assignment horizon")

        definitions = topological_task_order(_TEMPLATES[template])
        for definition in definitions:
            try:
                role = self.registry.get(definition.role_id)
            except RegistryError as exc:
                raise AllocationError(str(exc)) from exc
            if role.department_id != definition.department_id:
                raise AllocationError(
                    f"Role {role.role_id} does not belong to department {definition.department_id}"
                )

        request_data = {
            "project_id": project_id,
            "template": template,
            "director_instructions": instructions,
            "output_format": output_format,
            "language": plan_language,
            "priority": plan_priority,
            "deadline_at": _timestamp(due),
            "project": {
                key: project[key]
                for key in (
                    "title", "project_type", "department_id", "language",
                    "priority", "created_by", "is_demo",
                )
            },
            "tasks": [self._definition_payload(definition) for definition in definitions],
        }
        request_hash = hashlib.sha256(_json(request_data).encode("utf-8")).hexdigest()
        plan_id = "plan-" + hashlib.sha256(project_id.encode("utf-8")).hexdigest()[:24]

        with self._lock, self._connection:
            existing = self._connection.execute(
                "SELECT plan_id, request_hash FROM studio_task_plans WHERE project_id = ?",
                (project_id,),
            ).fetchone()
            if existing:
                if existing["request_hash"] != request_hash:
                    raise AllocationError("A different task plan already exists for this project")
                return self.get_plan(existing["plan_id"])

            window_expiry = min(due, current + timedelta(days=7))
            manifest = AssignmentManifest(
                assignment_id=plan_id,
                title=project["title"],
                director_id=actor,
                starts_at=current,
                expires_at=window_expiry,
                editorial_policy_id="local-task-plan-v1",
            )
            try:
                validate_assignment(
                    manifest,
                    now=current,
                    director_ids=self.director_ids,
                    check_expiry=True,
                )
            except StudioError as exc:
                raise AllocationError(str(exc)) from exc

            self._connection.execute(
                """
                INSERT INTO studio_task_plans
                    (plan_id, project_id, template, director_instructions, output_format,
                     language, priority, deadline_at, manifest_json, request_hash, status,
                     created_by, is_demo, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    plan_id, project_id, template, instructions, output_format,
                    plan_language, plan_priority, _timestamp(due),
                    _json(manifest.to_dict()), request_hash, PlanStatus.DRAFT.value,
                    actor, int(project["is_demo"]), current_text, current_text,
                ),
            )
            task_ids = {
                definition.key: "task-" + hashlib.sha256(
                    f"{project_id}:{template}:{definition.key}".encode("utf-8")
                ).hexdigest()[:24]
                for definition in definitions
            }
            for order, definition in enumerate(definitions):
                role = self.registry.get(definition.role_id)
                task_description = (
                    f"{definition.description} Project: {project['title']}. "
                    f"Desired format: {output_format}; language: {plan_language}."
                )
                dependencies = [task_ids[key] for key in definition.dependencies]
                self._connection.execute(
                    """
                    INSERT INTO studio_allocation_tasks
                        (task_id, plan_id, parent_project_id, task_key, title,
                         department_id, role_id, description, priority,
                         dependencies_json, evidence_requirements_json,
                         verification_gates_json, status, max_attempts,
                         sort_order, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        task_ids[definition.key], plan_id, project_id,
                        definition.key, definition.title, definition.department_id,
                        definition.role_id, task_description, plan_priority,
                        _json(dependencies), _json(definition.evidence_requirements),
                        _json([role.verification, "independent_observer_check"]),
                        AllocationTaskStatus.PENDING.value, MAX_TASK_ATTEMPTS,
                        order, current_text, current_text,
                    ),
                )
            self._audit(
                plan_id,
                plan_id,
                "task_plan_drafted",
                actor,
                {
                    "project_id": project_id,
                    "template": template,
                    "request_hash": request_hash,
                    "task_count": len(definitions),
                },
                current,
            )
            self._refresh_observer(plan_id, now=current)
        return self.get_plan(plan_id)

    def _plan_row(self, plan_id: str) -> dict[str, Any]:
        if not isinstance(plan_id, str) or not _ID_PATTERN.fullmatch(plan_id):
            raise AllocationError("Invalid task plan ID")
        row = self._connection.execute(
            "SELECT * FROM studio_task_plans WHERE plan_id = ?", (plan_id,)
        ).fetchone()
        if row is None:
            raise AllocationError("Task plan not found")
        return dict(row)

    def _task_rows(self, plan_id: str) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self._connection.execute(
                "SELECT * FROM studio_allocation_tasks WHERE plan_id = ? ORDER BY sort_order, task_id",
                (plan_id,),
            )
        ]

    def _approval_rows(self, plan_id: str) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self._connection.execute(
                "SELECT * FROM studio_plan_approvals WHERE plan_id = ? ORDER BY approval_id",
                (plan_id,),
            )
        ]

    def _evidence_directory(self) -> Path:
        database_parent = self.database.parent.resolve()
        root = database_parent / f".{self.database.name}.evidence"
        try:
            root.mkdir(mode=0o700, exist_ok=True)
            root_stat = root.lstat()
        except OSError as exc:
            raise AllocationError("Local evidence directory is unavailable") from exc
        if not stat.S_ISDIR(root_stat.st_mode) or stat.S_ISLNK(root_stat.st_mode):
            raise AllocationError("Local evidence directory must be a real directory")
        try:
            os.chmod(root, 0o700, follow_symlinks=False)
        except OSError as exc:
            raise AllocationError("Local evidence directory permissions could not be secured") from exc
        resolved = root.resolve(strict=True)
        if resolved.parent != database_parent:
            raise AllocationError("Local evidence directory escaped its database directory")
        return resolved

    def _verify_artifact_file(
        self, artifact: dict[str, Any], task_id: str
    ) -> tuple[bool, str | None]:
        if artifact["task_id"] != task_id:
            return False, "artifact-task-mismatch"
        artifact_id = artifact["artifact_id"]
        if (
            not isinstance(artifact_id, str)
            or not re.fullmatch(r"[a-f0-9]{32}", artifact_id)
            or artifact["storage_name"] != f"{artifact_id}.txt"
        ):
            return False, "artifact-storage-reference-invalid"
        if (
            not isinstance(artifact["size_bytes"], int)
            or artifact["size_bytes"] <= 0
            or artifact["size_bytes"] > MAX_EVIDENCE_BYTES
            or not isinstance(artifact["sha256"], str)
            or not re.fullmatch(r"[a-f0-9]{64}", artifact["sha256"])
        ):
            return False, "artifact-metadata-invalid"
        try:
            root = self._evidence_directory()
            path = root / artifact["storage_name"]
            path_stat = path.lstat()
            if not stat.S_ISREG(path_stat.st_mode) or stat.S_ISLNK(path_stat.st_mode):
                return False, "artifact-file-invalid"
            if path.resolve(strict=True).parent != root:
                return False, "artifact-path-invalid"
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(path, flags)
            try:
                opened_stat = os.fstat(descriptor)
                if not stat.S_ISREG(opened_stat.st_mode):
                    return False, "artifact-file-invalid"
                if opened_stat.st_size <= 0:
                    return False, "artifact-empty"
                if opened_stat.st_size > MAX_EVIDENCE_BYTES:
                    return False, "artifact-too-large"
                chunks: list[bytes] = []
                total = 0
                while True:
                    chunk = os.read(descriptor, min(65_536, MAX_EVIDENCE_BYTES + 1 - total))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    total += len(chunk)
                    if total > MAX_EVIDENCE_BYTES:
                        return False, "artifact-too-large"
                content = b"".join(chunks)
            finally:
                os.close(descriptor)
        except FileNotFoundError:
            return False, "artifact-file-missing"
        except (OSError, AllocationError):
            return False, "artifact-file-unavailable"
        if len(content) != artifact["size_bytes"]:
            return False, "artifact-size-mismatch"
        try:
            if not content.decode("utf-8").strip():
                return False, "artifact-empty"
        except UnicodeDecodeError:
            return False, "artifact-content-invalid"
        digest = hashlib.sha256(content).hexdigest()
        if not hmac.compare_digest(digest, artifact["sha256"]):
            return False, "artifact-hash-mismatch"
        return True, None

    def _validate_task_evidence(self, task: dict[str, Any]) -> dict[str, Any]:
        errors: list[str] = []
        try:
            required = json.loads(task["evidence_requirements_json"])
            references = json.loads(task["evidence_json"])
            if (
                not isinstance(required, list)
                or not required
                or any(not isinstance(value, str) or not _ID_PATTERN.fullmatch(value) for value in required)
                or len(required) != len(set(required))
                or not isinstance(references, list)
                or any(not isinstance(value, str) or not _ID_PATTERN.fullmatch(value) for value in references)
                or len(references) != len(set(references))
            ):
                raise ValueError
        except (json.JSONDecodeError, TypeError, ValueError):
            return {
                "is_valid": False,
                "errors": ["evidence-reference-data-invalid"],
                "artifacts": [],
            }

        verified_types: set[str] = set()
        artifacts: list[dict[str, Any]] = []
        for artifact_id in references:
            row = self._connection.execute(
                "SELECT * FROM studio_evidence_artifacts WHERE artifact_id = ?",
                (artifact_id,),
            ).fetchone()
            if row is None:
                errors.append(f"artifact-record-missing:{artifact_id}")
                artifacts.append(
                    {
                        "artifact_id": artifact_id,
                        "evidence_type": None,
                        "verified": False,
                        "error": "artifact-record-missing",
                    }
                )
                continue
            artifact = dict(row)
            valid, error = self._verify_artifact_file(artifact, task["task_id"])
            if artifact["evidence_type"] not in required:
                valid, error = False, "artifact-type-not-required"
            if valid and artifact["evidence_type"] in verified_types:
                valid, error = False, "duplicate-artifact-type"
            if valid:
                verified_types.add(artifact["evidence_type"])
            else:
                errors.append(f"{error}:{artifact_id}")
            artifacts.append(
                {
                    "artifact_id": artifact_id,
                    "evidence_type": artifact["evidence_type"],
                    "sha256": artifact["sha256"],
                    "size_bytes": artifact["size_bytes"],
                    "created_at": artifact["created_at"],
                    "verified": valid,
                    "error": error,
                }
            )
        for evidence_type in sorted(set(required) - verified_types):
            errors.append(f"required-artifact-missing:{evidence_type}")
        return {
            "is_valid": not errors,
            "errors": errors,
            "artifacts": artifacts,
        }

    def submit_evidence(
        self,
        task_id: str,
        *,
        actor_id: str,
        evidence_type: str,
        content: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        current = now or datetime.now(timezone.utc)
        if not isinstance(content, str):
            raise AllocationError("Evidence content must be text")
        encoded = content.encode("utf-8")
        if not content.strip():
            raise AllocationError("Evidence artifact cannot be empty")
        if len(encoded) > MAX_EVIDENCE_BYTES:
            raise AllocationError(
                f"Evidence artifact exceeds the {MAX_EVIDENCE_BYTES}-byte limit"
            )

        artifact_path: Path | None = None
        file_descriptor: int | None = None
        try:
            with self._lock, self._connection:
                task = self._task_row(task_id)
                actor = self._require_task_actor(task, actor_id)
                plan = self._plan_row(task["plan_id"])
                self._require_active_plan(plan, now=current)
                if task["status"] not in {
                    AllocationTaskStatus.ACTIVE.value,
                    AllocationTaskStatus.REVIEW.value,
                }:
                    raise AllocationError("Evidence can only be submitted for active or review tasks")
                requirements = json.loads(task["evidence_requirements_json"])
                if not isinstance(evidence_type, str) or evidence_type not in requirements:
                    raise AllocationError("Evidence type must match a requirement for this task")

                existing_refs = json.loads(task["evidence_json"])
                retained_refs: list[str] = []
                for reference in existing_refs:
                    if not isinstance(reference, str):
                        continue
                    row = self._connection.execute(
                        "SELECT * FROM studio_evidence_artifacts WHERE artifact_id = ?",
                        (reference,),
                    ).fetchone()
                    if row is None:
                        continue
                    artifact = dict(row)
                    valid, _ = self._verify_artifact_file(artifact, task_id)
                    if valid and artifact["evidence_type"] in requirements:
                        retained_refs.append(reference)
                        if artifact["evidence_type"] == evidence_type:
                            raise AllocationError(
                                f"Evidence type {evidence_type} has already been submitted"
                            )

                prior_artifacts = self._connection.execute(
                    """
                    SELECT * FROM studio_evidence_artifacts
                    WHERE task_id = ? AND evidence_type = ?
                    """,
                    (task_id, evidence_type),
                ).fetchall()
                for row in prior_artifacts:
                    valid, _ = self._verify_artifact_file(dict(row), task_id)
                    if valid:
                        raise AllocationError(
                            f"Evidence type {evidence_type} has already been submitted"
                        )

                evidence_root = self._evidence_directory()
                artifact_id = secrets.token_hex(16)
                storage_name = f"{artifact_id}.txt"
                artifact_path = evidence_root / storage_name
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
                file_descriptor = os.open(artifact_path, flags, 0o600)
                with os.fdopen(file_descriptor, "wb", closefd=True) as artifact_file:
                    file_descriptor = None
                    artifact_file.write(encoded)
                    artifact_file.flush()
                    os.fsync(artifact_file.fileno())

                digest = hashlib.sha256(encoded).hexdigest()
                self._connection.execute(
                    """
                    INSERT INTO studio_evidence_artifacts
                        (artifact_id, task_id, evidence_type, storage_name,
                         sha256, size_bytes, submitted_by, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        artifact_id, task_id, evidence_type, storage_name,
                        digest, len(encoded), actor, _timestamp(current),
                    ),
                )
                references = sorted(set(retained_refs + [artifact_id]))
                next_status = (
                    AllocationTaskStatus.ACTIVE.value
                    if task["status"] == AllocationTaskStatus.REVIEW.value
                    else task["status"]
                )
                self._connection.execute(
                    """
                    UPDATE studio_allocation_tasks
                    SET evidence_json = ?, status = ?, updated_at = ?
                    WHERE task_id = ?
                    """,
                    (_json(references), next_status, _timestamp(current), task_id),
                )
                self._audit(
                    plan["plan_id"], task_id, "evidence_artifact_submitted", actor,
                    {
                        "artifact_id": artifact_id,
                        "evidence_type": evidence_type,
                        "sha256": digest,
                        "size_bytes": len(encoded),
                    },
                    current,
                )
                validation = self._validate_task_evidence(self._task_row(task_id))
                if not validation["is_valid"] and len(requirements) == len(references):
                    raise AllocationError("Submitted evidence failed verification")
                self._refresh_observer(plan["plan_id"], now=current)
        except BaseException:
            if file_descriptor is not None:
                os.close(file_descriptor)
            if artifact_path is not None:
                try:
                    artifact_path.unlink(missing_ok=True)
                except OSError:
                    pass
            raise
        return self.get_plan(plan["plan_id"])

    def _refresh_observer(self, plan_id: str, *, now: datetime) -> dict[str, Any]:
        plan = self._plan_row(plan_id)
        project = self._connection.execute(
            "SELECT status, created_by FROM studio_projects WHERE project_id = ?",
            (plan["project_id"],),
        ).fetchone()
        plan["project_status"] = project["status"] if project else None
        plan["project_created_by"] = project["created_by"] if project else None
        tasks = self._task_rows(plan_id)
        for task in tasks:
            if task["status"] in {
                AllocationTaskStatus.REVIEW.value,
                AllocationTaskStatus.COMPLETED.value,
            }:
                task["_evidence_validation"] = self._validate_task_evidence(task)
        approvals = self._approval_rows(plan_id)
        findings = self.observer.inspect(plan, tasks, approvals, now=now)
        run_id = "obs-" + uuid.uuid4().hex[:24]
        self._connection.execute(
            """
            INSERT OR IGNORE INTO studio_allocation_observer_runs
                (run_id, plan_id, observer_id, is_clear, generated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (run_id, plan_id, ALLOCATION_OBSERVER_ID, int(not findings), _timestamp(now)),
        )
        for finding in findings:
            finding_id = hashlib.sha256(
                _json(
                    {
                        "run_id": run_id,
                        "code": finding.code,
                        "subject_id": finding.subject_id,
                        "evidence_refs": finding.evidence_refs,
                    }
                ).encode("utf-8")
            ).hexdigest()[:24]
            self._connection.execute(
                """
                INSERT OR IGNORE INTO studio_allocation_observer_findings
                    (finding_id, run_id, code, severity, subject_id, evidence_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    finding_id, run_id, finding.code, finding.severity,
                    finding.subject_id, _json(finding.evidence_refs),
                ),
            )
        return {
            "run_id": run_id,
            "observer_id": ALLOCATION_OBSERVER_ID,
            "is_clear": not findings,
            "generated_at": _timestamp(now),
            "findings": [
                {
                    "code": item.code,
                    "severity": item.severity,
                    "subject_id": item.subject_id,
                    "evidence_refs": list(item.evidence_refs),
                }
                for item in findings
            ],
        }

    def refresh_observer_findings(
        self, plan_id: str, *, now: datetime | None = None
    ) -> dict[str, Any]:
        current = now or datetime.now(timezone.utc)
        with self._lock, self._connection:
            return self._refresh_observer(plan_id, now=current)

    def _latest_observer(self, plan_id: str) -> dict[str, Any] | None:
        row = self._connection.execute(
            """
            SELECT * FROM studio_allocation_observer_runs
            WHERE plan_id = ? ORDER BY generated_at DESC, rowid DESC LIMIT 1
            """,
            (plan_id,),
        ).fetchone()
        if row is None:
            return None
        findings = [
            {
                "code": finding["code"],
                "severity": finding["severity"],
                "subject_id": finding["subject_id"],
                "evidence_refs": json.loads(finding["evidence_json"]),
            }
            for finding in self._connection.execute(
                "SELECT * FROM studio_allocation_observer_findings WHERE run_id = ? ORDER BY code, subject_id",
                (row["run_id"],),
            )
        ]
        return {
            "run_id": row["run_id"],
            "observer_id": row["observer_id"],
            "is_clear": bool(row["is_clear"]),
            "generated_at": row["generated_at"],
            "findings": findings,
        }

    def get_plan(self, plan_id: str) -> dict[str, Any]:
        with self._lock:
            plan = self._plan_row(plan_id)
            manifest = AssignmentManifest.from_json(plan.pop("manifest_json"))
            tasks = self._task_rows(plan_id)
            by_id = {task["task_id"]: task for task in tasks}
            for task in tasks:
                validation = self._validate_task_evidence(task)
                dependencies = json.loads(task.pop("dependencies_json"))
                try:
                    task["role_title"] = self.registry.get(task["role_id"]).title
                except RegistryError:
                    task["role_title"] = "Unknown registered role"
                task["dependencies"] = [
                    {
                        "task_id": dependency_id,
                        "status": by_id[dependency_id]["status"] if dependency_id in by_id else "missing",
                    }
                    for dependency_id in dependencies
                ]
                task["evidence_requirements"] = json.loads(
                    task.pop("evidence_requirements_json")
                )
                task["verification_gates"] = json.loads(
                    task.pop("verification_gates_json")
                )
                task["evidence"] = json.loads(task.pop("evidence_json"))
                task["evidence_artifacts"] = validation["artifacts"]
                task["evidence_errors"] = validation["errors"]
                task["evidence_verified"] = validation["is_valid"]
                task["retryable"] = bool(task["retryable"])
            counts = {status.value: 0 for status in AllocationTaskStatus}
            for task in tasks:
                if task["status"] in counts:
                    counts[task["status"]] += 1
            project_row = self._connection.execute(
                "SELECT title, project_type, status, is_demo FROM studio_projects WHERE project_id = ?",
                (plan["project_id"],),
            ).fetchone()
            project = dict(project_row) if project_row else {}
            if project:
                project["is_demo"] = bool(project["is_demo"])
            plan["is_demo"] = bool(plan["is_demo"])
            plan["manifest"] = manifest.to_dict()
            plan["project"] = project
            plan["tasks"] = tasks
            plan["counts"] = counts
            plan["observer"] = self._latest_observer(plan_id)
            plan["approvals"] = self._approval_rows(plan_id)
            plan["failures"] = [
                dict(row)
                for row in self._connection.execute(
                    """
                    SELECT f.* FROM studio_allocation_failures f
                    JOIN studio_allocation_tasks t ON t.task_id = f.task_id
                    WHERE t.plan_id = ? ORDER BY f.failed_at, f.failure_id
                    """,
                    (plan_id,),
                )
            ]
            plan["audit_history"] = self.audit_events(plan_id)
            return plan

    def get_plan_for_project(self, project_id: str) -> dict[str, Any] | None:
        if not isinstance(project_id, str) or not PROJECT_ID_PATTERN.fullmatch(project_id):
            raise AllocationError("Project ID must be a simple local identifier")
        with self._lock:
            row = self._connection.execute(
                "SELECT plan_id FROM studio_task_plans WHERE project_id = ?", (project_id,)
            ).fetchone()
        return self.get_plan(row["plan_id"]) if row else None

    @staticmethod
    def _plan_hash(plan: dict[str, Any], tasks: Sequence[dict[str, Any]]) -> str:
        return _allocation_plan_hash(plan, tasks)

    def approve_plan(
        self,
        plan_id: str,
        *,
        director_id: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        actor = self._require_director(director_id)
        current = now or datetime.now(timezone.utc)
        with self._lock, self._connection:
            plan = self._plan_row(plan_id)
            if plan["status"] == PlanStatus.APPROVED.value:
                return self.get_plan(plan_id)
            project = self._project(plan["project_id"])
            if project["created_by"] != actor:
                raise AllocationError("Only the Director who approved the project may approve its task plan")
            manifest = AssignmentManifest.from_json(plan["manifest_json"])
            try:
                validate_assignment(
                    manifest,
                    now=current,
                    director_ids=self.director_ids,
                    check_expiry=True,
                )
            except StudioError as exc:
                raise AllocationError(str(exc)) from exc
            observer_report = self._refresh_observer(plan_id, now=current)
            if not observer_report["is_clear"]:
                raise AllocationError("Observer policy checks block plan approval")
            tasks = self._task_rows(plan_id)
            plan_hash = self._plan_hash(plan, tasks)
            self._connection.execute(
                """
                INSERT INTO studio_plan_approvals
                    (plan_id, approver_id, approval_type, plan_hash, approved_at)
                VALUES (?, ?, 'plan', ?, ?)
                """,
                (plan_id, actor, plan_hash, _timestamp(current)),
            )
            self._connection.execute(
                "UPDATE studio_task_plans SET status = ?, approved_by = ?, updated_at = ? WHERE plan_id = ?",
                (PlanStatus.APPROVED.value, actor, _timestamp(current), plan_id),
            )
            self._connection.execute(
                """
                UPDATE studio_allocation_tasks SET status = ?, updated_at = ?
                WHERE plan_id = ? AND dependencies_json = '[]'
                """,
                (AllocationTaskStatus.READY.value, _timestamp(current), plan_id),
            )
            self._audit(
                plan_id, plan_id, "task_plan_approved", actor,
                {"plan_hash": plan_hash, "ready_task_count": sum(
                    not json.loads(task["dependencies_json"]) for task in tasks
                )},
                current,
            )
            self._refresh_observer(plan_id, now=current)
        return self.get_plan(plan_id)

    def extend_plan(
        self,
        plan_id: str,
        *,
        director_id: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        actor = self._require_director(director_id)
        current = now or datetime.now(timezone.utc)
        with self._lock, self._connection:
            plan = self._plan_row(plan_id)
            if plan["status"] != PlanStatus.APPROVED.value:
                raise AllocationError("Only an approved plan may be extended")
            if plan["approved_by"] != actor:
                raise AllocationError("Only the approving Director may extend this task plan")
            self._project(plan["project_id"])
            manifest = AssignmentManifest.from_json(plan["manifest_json"])
            try:
                extended = manifest.extend(
                    approved_by=actor,
                    approved_at=current,
                    new_expiry=manifest.expires_at + timedelta(days=7),
                    director_ids=self.director_ids,
                    now=current,
                )
            except StudioError as exc:
                raise AllocationError(str(exc)) from exc
            manifest_json = _json(extended.to_dict())
            manifest_hash = hashlib.sha256(manifest_json.encode("utf-8")).hexdigest()
            self._connection.execute(
                "UPDATE studio_task_plans SET manifest_json = ?, updated_at = ? WHERE plan_id = ?",
                (manifest_json, _timestamp(current), plan_id),
            )
            self._connection.execute(
                """
                INSERT INTO studio_plan_approvals
                    (plan_id, approver_id, approval_type, plan_hash, approved_at)
                VALUES (?, ?, 'extension', ?, ?)
                """,
                (plan_id, actor, manifest_hash, _timestamp(current)),
            )
            self._audit(
                plan_id, plan_id, "task_plan_extended", actor,
                {"expires_at": _timestamp(extended.expires_at), "manifest_hash": manifest_hash},
                current,
            )
            self._refresh_observer(plan_id, now=current)
        return self.get_plan(plan_id)

    def _require_active_plan(self, plan: dict[str, Any], *, now: datetime) -> None:
        if plan["status"] != PlanStatus.APPROVED.value:
            raise AllocationError("Task plan must receive explicit Director approval before activation")
        project = self._connection.execute(
            "SELECT status, created_by FROM studio_projects WHERE project_id = ?",
            (plan["project_id"],),
        ).fetchone()
        if (
            project is None
            or project["status"] not in {"queued", "active"}
            or project["created_by"] not in self.director_ids
        ):
            raise AllocationError("Project approval is no longer active")
        manifest = AssignmentManifest.from_json(plan["manifest_json"])
        try:
            validate_assignment(
                manifest, now=now, director_ids=self.director_ids, check_expiry=True
            )
        except StudioError as exc:
            raise AllocationError(str(exc)) from exc

    def _task_row(self, task_id: str) -> dict[str, Any]:
        if not isinstance(task_id, str) or not _ID_PATTERN.fullmatch(task_id):
            raise AllocationError("Invalid task ID")
        row = self._connection.execute(
            "SELECT * FROM studio_allocation_tasks WHERE task_id = ?", (task_id,)
        ).fetchone()
        if row is None:
            raise AllocationError("Task not found")
        return dict(row)

    @staticmethod
    def _require_task_actor(task: dict[str, Any], actor_id: object) -> str:
        if actor_id != task["role_id"]:
            raise AllocationError("Only the task's assigned registered role may update it")
        return str(actor_id)

    def start_task(
        self,
        task_id: str,
        *,
        actor_id: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        current = now or datetime.now(timezone.utc)
        with self._lock, self._connection:
            task = self._task_row(task_id)
            actor = self._require_task_actor(task, actor_id)
            plan = self._plan_row(task["plan_id"])
            self._require_active_plan(plan, now=current)
            if task["status"] != AllocationTaskStatus.READY.value:
                raise AllocationError("Only ready tasks can be activated")
            if task["attempts"] >= task["max_attempts"]:
                raise AllocationError("Task retry limit reached")
            for dependency_id in json.loads(task["dependencies_json"]):
                dependency = self._task_row(dependency_id)
                if dependency["status"] != AllocationTaskStatus.COMPLETED.value:
                    raise AllocationError(f"Task dependency {dependency_id} is not complete")
            self._connection.execute(
                """
                UPDATE studio_allocation_tasks
                SET status = ?, attempts = attempts + 1, started_at = ?, updated_at = ?
                WHERE task_id = ?
                """,
                (
                    AllocationTaskStatus.ACTIVE.value, _timestamp(current),
                    _timestamp(current), task_id,
                ),
            )
            self._audit(
                plan["plan_id"], task_id, "allocation_task_activated", actor,
                {"attempt": task["attempts"] + 1}, current,
            )
        return self.get_plan(plan["plan_id"])

    def submit_task_for_review(
        self,
        task_id: str,
        *,
        actor_id: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        current = now or datetime.now(timezone.utc)
        with self._lock, self._connection:
            task = self._task_row(task_id)
            actor = self._require_task_actor(task, actor_id)
            plan = self._plan_row(task["plan_id"])
            self._require_active_plan(plan, now=current)
            if task["status"] != AllocationTaskStatus.ACTIVE.value:
                raise AllocationError("Only active tasks can be submitted for review")
            validation = self._validate_task_evidence(task)
            if not validation["is_valid"]:
                raise AllocationError(
                    "Genuine task evidence is missing or invalid: "
                    + ", ".join(validation["errors"])
                )
            evidence_refs = task["evidence_json"]
            persisted_artifact_ids = json.loads(evidence_refs)
            self._connection.execute(
                "UPDATE studio_allocation_tasks SET status = ?, updated_at = ? WHERE task_id = ?",
                (AllocationTaskStatus.REVIEW.value, _timestamp(current), task_id),
            )
            self._audit(
                plan["plan_id"], task_id, "allocation_task_submitted_for_review",
                actor, {"artifact_ids": persisted_artifact_ids}, current,
            )
            report = self._refresh_observer(plan["plan_id"], now=current)
            if not report["is_clear"]:
                self._connection.execute(
                    "UPDATE studio_allocation_tasks SET status = ?, updated_at = ? WHERE task_id = ?",
                    (AllocationTaskStatus.BLOCKED.value, _timestamp(current), task_id),
                )
                self._audit(
                    plan["plan_id"], task_id, "allocation_task_observer_blocked",
                    ALLOCATION_OBSERVER_ID,
                    {"findings": [item["code"] for item in report["findings"]]}, current,
                )
        return self.get_plan(plan["plan_id"])

    def complete_task(
        self,
        task_id: str,
        *,
        actor_id: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        current = now or datetime.now(timezone.utc)
        with self._lock, self._connection:
            task = self._task_row(task_id)
            actor = self._require_task_actor(task, actor_id)
            plan = self._plan_row(task["plan_id"])
            self._require_active_plan(plan, now=current)
            if task["status"] != AllocationTaskStatus.REVIEW.value:
                raise AllocationError("Only review tasks can be completed")
            validation = self._validate_task_evidence(task)
            if not validation["is_valid"]:
                raise AllocationError(
                    "Task cannot be completed without verified evidence artifacts: "
                    + ", ".join(validation["errors"])
                )
            observer_report = self._refresh_observer(plan["plan_id"], now=current)
            if not observer_report["is_clear"]:
                raise AllocationError("Observer policy checks block task completion")
            self._connection.execute(
                """
                UPDATE studio_allocation_tasks
                SET status = ?, completed_at = ?, updated_at = ?
                WHERE task_id = ?
                """,
                (
                    AllocationTaskStatus.COMPLETED.value, _timestamp(current),
                    _timestamp(current), task_id,
                ),
            )
            self._audit(
                plan["plan_id"], task_id, "allocation_task_completed", actor,
                {"evidence_refs": json.loads(task["evidence_json"])}, current,
            )
            for pending in self._connection.execute(
                """
                SELECT task_id, dependencies_json FROM studio_allocation_tasks
                WHERE plan_id = ? AND status = ?
                """,
                (plan["plan_id"], AllocationTaskStatus.PENDING.value),
            ).fetchall():
                dependencies = json.loads(pending["dependencies_json"])
                if all(
                    self._task_row(dependency_id)["status"] == AllocationTaskStatus.COMPLETED.value
                    for dependency_id in dependencies
                ):
                    self._connection.execute(
                        "UPDATE studio_allocation_tasks SET status = ?, updated_at = ? WHERE task_id = ?",
                        (AllocationTaskStatus.READY.value, _timestamp(current), pending["task_id"]),
                    )
            self._refresh_observer(plan["plan_id"], now=current)
        return self.get_plan(plan["plan_id"])

    def fail_task(
        self,
        task_id: str,
        *,
        actor_id: str,
        error_code: str,
        retryable: bool,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        if not isinstance(error_code, str) or not _ERROR_CODE_PATTERN.fullmatch(error_code):
            raise AllocationError("Failure must use a non-sensitive uppercase error code")
        if not isinstance(retryable, bool):
            raise AllocationError("Retryable must be a boolean")
        current = now or datetime.now(timezone.utc)
        with self._lock, self._connection:
            task = self._task_row(task_id)
            actor = self._require_task_actor(task, actor_id)
            plan = self._plan_row(task["plan_id"])
            self._require_active_plan(plan, now=current)
            if task["status"] != AllocationTaskStatus.ACTIVE.value:
                raise AllocationError("Only active tasks can fail")
            self._connection.execute(
                """
                INSERT INTO studio_allocation_failures
                    (task_id, attempt_no, error_code, retryable, failed_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    task_id, task["attempts"], error_code, int(retryable),
                    _timestamp(current),
                ),
            )
            self._connection.execute(
                """
                UPDATE studio_allocation_tasks
                SET status = ?, last_error_code = ?, retryable = ?, updated_at = ?
                WHERE task_id = ?
                """,
                (
                    AllocationTaskStatus.BLOCKED.value, error_code, int(retryable),
                    _timestamp(current), task_id,
                ),
            )
            self._audit(
                plan["plan_id"], task_id, "allocation_task_failed", actor,
                {
                    "attempt": task["attempts"],
                    "error_code": error_code,
                    "retryable": retryable,
                },
                current,
            )
        return self.get_plan(plan["plan_id"])

    def retry_task(
        self,
        task_id: str,
        *,
        actor_id: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        current = now or datetime.now(timezone.utc)
        with self._lock, self._connection:
            task = self._task_row(task_id)
            actor = self._require_task_actor(task, actor_id)
            plan = self._plan_row(task["plan_id"])
            self._require_active_plan(plan, now=current)
            if task["status"] != AllocationTaskStatus.BLOCKED.value or not task["retryable"]:
                raise AllocationError("Only retryable blocked tasks can be retried")
            if task["attempts"] >= task["max_attempts"]:
                raise AllocationError("Task retry limit reached")
            dependencies = json.loads(task["dependencies_json"])
            next_status = (
                AllocationTaskStatus.READY.value
                if all(
                    self._task_row(dependency_id)["status"] == AllocationTaskStatus.COMPLETED.value
                    for dependency_id in dependencies
                )
                else AllocationTaskStatus.PENDING.value
            )
            self._connection.execute(
                "UPDATE studio_allocation_tasks SET status = ?, retryable = 0, updated_at = ? WHERE task_id = ?",
                (next_status, _timestamp(current), task_id),
            )
            self._audit(
                plan["plan_id"], task_id, "allocation_task_retry_queued", actor,
                {"attempts_used": task["attempts"], "max_attempts": task["max_attempts"]},
                current,
            )
        return self.get_plan(plan["plan_id"])

    def audit_events(self, plan_id: str | None = None) -> tuple[dict[str, Any], ...]:
        if plan_id is None:
            rows = self._connection.execute(
                "SELECT * FROM studio_allocation_audit_events ORDER BY sequence"
            ).fetchall()
        else:
            rows = self._connection.execute(
                "SELECT * FROM studio_allocation_audit_events WHERE plan_id = ? ORDER BY sequence",
                (plan_id,),
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def verify_audit_chain(self) -> bool:
        previous_hash = BASE_AUDIT_HASH
        for row in self._connection.execute(
            "SELECT * FROM studio_allocation_audit_events ORDER BY sequence"
        ):
            material = _json(
                {
                    "plan_id": row["plan_id"],
                    "object_id": row["object_id"],
                    "event_type": row["event_type"],
                    "actor_id": row["actor_id"],
                    "event_json": row["event_json"],
                    "previous_hash": row["previous_hash"],
                    "created_at": row["created_at"],
                }
            )
            event_hash = hashlib.sha256(material.encode("utf-8")).hexdigest()
            if row["previous_hash"] != previous_hash or row["event_hash"] != event_hash:
                return False
            previous_hash = event_hash
        return True
