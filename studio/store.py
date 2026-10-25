"""SQLite-backed local task, approval, observer, and audit ledger."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import (
    AssignmentManifest,
    ObserverAssessment,
    ObserverReport,
    ReviewGate,
    ReviewOutcome,
    StudioError,
    TaskRequest,
    TaskStatus,
    ID_PATTERN,
    normalize_hash,
    validate_assignment,
)
from .registry import RegistryError, RoleRegistry


REQUIRED_GATES = tuple(ReviewGate)
ERROR_CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
FINDING_CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise StudioError("Timestamps must include a timezone")
    return value.astimezone(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _manifest_dict(manifest: AssignmentManifest) -> dict[str, Any]:
    return manifest.to_dict()


def _validate_identifier(value: str, label: str) -> None:
    if not isinstance(value, str) or not ID_PATTERN.fullmatch(value):
        raise StudioError(f"{label} must be a simple local identifier, not a URL or free-form text")


def _validate_references(values: tuple[str, ...], label: str) -> None:
    if not values or len(set(values)) != len(values):
        raise StudioError(f"{label} must contain unique identifiers")
    for value in values:
        _validate_identifier(value, label)


class StudioStore:
    """A local-only prototype store. It has no network or publishing adapters."""

    def __init__(
        self,
        database: str | Path,
        registry: RoleRegistry,
        director_ids: set[str] | frozenset[str],
    ) -> None:
        self.registry = registry
        self.director_ids = frozenset(director_ids)
        if not self.director_ids:
            raise StudioError("At least one trusted Director ID must be configured")
        for director_id in self.director_ids:
            _validate_identifier(director_id, "Director ID")
        self._connection = sqlite3.connect(str(database))
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._create_schema()

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "StudioStore":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def _create_schema(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS assignments (
                assignment_id TEXT PRIMARY KEY,
                manifest_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY,
                assignment_id TEXT NOT NULL REFERENCES assignments(assignment_id),
                title TEXT NOT NULL,
                department_id TEXT NOT NULL,
                role_id TEXT NOT NULL,
                dependencies_json TEXT NOT NULL,
                required_evidence_json TEXT NOT NULL,
                evidence_json TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL,
                artifact_hash TEXT,
                attempts INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL,
                retryable INTEGER NOT NULL DEFAULT 0,
                last_error_code TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS reviews (
                task_id TEXT NOT NULL REFERENCES tasks(task_id),
                gate TEXT NOT NULL,
                outcome TEXT NOT NULL,
                reviewer_id TEXT NOT NULL,
                artifact_hash TEXT NOT NULL,
                evidence_json TEXT NOT NULL,
                dissent TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(task_id, gate, artifact_hash)
            );
            CREATE TABLE IF NOT EXISTS observer_reports (
                task_id TEXT NOT NULL REFERENCES tasks(task_id),
                observer_id TEXT NOT NULL,
                artifact_hash TEXT NOT NULL,
                assessment TEXT NOT NULL,
                evidence_json TEXT NOT NULL,
                finding TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(task_id, observer_id, artifact_hash)
            );
            CREATE TABLE IF NOT EXISTS observer_resolutions (
                task_id TEXT NOT NULL REFERENCES tasks(task_id),
                artifact_hash TEXT NOT NULL,
                director_id TEXT NOT NULL,
                disposition TEXT NOT NULL,
                evidence_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(task_id, artifact_hash)
            );
            CREATE TABLE IF NOT EXISTS director_approvals (
                approval_id INTEGER PRIMARY KEY AUTOINCREMENT,
                approval_type TEXT NOT NULL,
                object_id TEXT NOT NULL,
                approver_id TEXT NOT NULL,
                artifact_hash TEXT NOT NULL,
                approved_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS audit_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                object_id TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                event_json TEXT NOT NULL,
                previous_hash TEXT NOT NULL,
                event_hash TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL
            );
            """
        )
        self._connection.commit()

    def _audit(self, event_type: str, object_id: str, actor_id: str, data: dict[str, Any], at: datetime) -> None:
        _validate_identifier(object_id, "Audit object ID")
        _validate_identifier(actor_id, "Audit actor ID")
        previous = self._connection.execute(
            "SELECT event_hash FROM audit_events ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        previous_hash = previous["event_hash"] if previous else "0" * 64
        event_json = _json(data)
        created_at = _timestamp(at)
        material = _json(
            {
                "event_type": event_type,
                "object_id": object_id,
                "actor_id": actor_id,
                "event_json": event_json,
                "previous_hash": previous_hash,
                "created_at": created_at,
            }
        )
        event_hash = hashlib.sha256(material.encode("utf-8")).hexdigest()
        self._connection.execute(
            """
            INSERT INTO audit_events
                (event_type, object_id, actor_id, event_json, previous_hash, event_hash, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (event_type, object_id, actor_id, event_json, previous_hash, event_hash, created_at),
        )

    def create_assignment(self, manifest: AssignmentManifest, *, now: datetime) -> None:
        if manifest.extensions:
            raise StudioError("Create the base assignment first, then record an explicit extension")
        validate_assignment(
            manifest,
            now=now,
            director_ids=self.director_ids,
            check_expiry=True,
        )
        value = _manifest_dict(manifest)
        manifest_json = _json(value)
        manifest_hash = hashlib.sha256(manifest_json.encode("utf-8")).hexdigest()
        try:
            with self._connection:
                self._connection.execute(
                    "INSERT INTO assignments (assignment_id, manifest_json, created_at) VALUES (?, ?, ?)",
                    (manifest.assignment_id, manifest_json, _timestamp(now)),
                )
                self._connection.execute(
                    """
                    INSERT INTO director_approvals
                        (approval_type, object_id, approver_id, artifact_hash, approved_at)
                    VALUES ('assignment', ?, ?, ?, ?)
                    """,
                    (manifest.assignment_id, manifest.director_id, manifest_hash, _timestamp(now)),
                )
                self._audit(
                    "assignment_created",
                    manifest.assignment_id,
                    manifest.director_id,
                    {"manifest_hash": manifest_hash, "schema_version": manifest.schema_version},
                    now,
                )
        except sqlite3.IntegrityError as exc:
            raise StudioError(f"Duplicate assignment ID: {manifest.assignment_id}") from exc

    def extend_assignment(
        self,
        assignment_id: str,
        *,
        director_id: str,
        new_expiry: datetime,
        approved_at: datetime,
        now: datetime,
    ) -> AssignmentManifest:
        if director_id not in self.director_ids:
            raise StudioError("Only an authorized Director may extend an assignment")
        current = self.get_assignment(assignment_id)
        extended = current.extend(
            approved_by=director_id,
            approved_at=approved_at,
            new_expiry=new_expiry,
            director_ids=self.director_ids,
            now=now,
        )
        manifest_json = _json(_manifest_dict(extended))
        manifest_hash = hashlib.sha256(manifest_json.encode("utf-8")).hexdigest()
        with self._connection:
            self._connection.execute(
                "UPDATE assignments SET manifest_json = ? WHERE assignment_id = ?",
                (manifest_json, assignment_id),
            )
            self._connection.execute(
                """
                INSERT INTO director_approvals
                    (approval_type, object_id, approver_id, artifact_hash, approved_at)
                VALUES ('assignment_extension', ?, ?, ?, ?)
                """,
                (assignment_id, director_id, manifest_hash, _timestamp(approved_at)),
            )
            self._audit(
                "assignment_extended",
                assignment_id,
                director_id,
                {"new_expiry": _timestamp(new_expiry), "manifest_hash": manifest_hash},
                approved_at,
            )
        return extended

    def get_assignment(self, assignment_id: str) -> AssignmentManifest:
        row = self._connection.execute(
            "SELECT manifest_json FROM assignments WHERE assignment_id = ?",
            (assignment_id,),
        ).fetchone()
        if row is None:
            raise StudioError(f"Unknown assignment ID: {assignment_id}")
        return AssignmentManifest.from_json(row["manifest_json"])

    def create_task(self, request: TaskRequest, *, actor_id: str, now: datetime) -> None:
        _validate_identifier(request.task_id, "Task ID")
        _validate_identifier(actor_id, "Actor ID")
        assignment = self.get_assignment(request.assignment_id)
        validate_assignment(
            assignment,
            now=now,
            director_ids=self.director_ids,
            check_expiry=True,
        )
        if request.task_id in self._task_ids():
            raise StudioError(f"Duplicate task ID: {request.task_id}")
        try:
            role = self.registry.get(request.role_id)
        except RegistryError as exc:
            raise StudioError(str(exc)) from exc
        if role.department_id != request.department_id:
            raise StudioError(
                f"Role {request.role_id} belongs to {role.department_id}, not {request.department_id}"
            )
        if not request.title.strip():
            raise StudioError("Task title cannot be empty")
        if request.max_attempts < 1:
            raise StudioError("Task max_attempts must be positive")
        if len(set(request.required_evidence)) != len(request.required_evidence):
            raise StudioError("Required evidence references must be unique")
        for reference in request.required_evidence:
            _validate_identifier(reference, "Required evidence reference")
        for dependency_id in request.dependencies:
            _validate_identifier(dependency_id, "Task dependency ID")
        if set(request.dependencies) - self._task_ids():
            raise StudioError("Task dependencies must reference existing tasks")
        for dependency_id in request.dependencies:
            dependency = self.get_task(dependency_id)
            if dependency["assignment_id"] != request.assignment_id:
                raise StudioError("Task dependencies must belong to the same assignment")

        try:
            with self._connection:
                self._connection.execute(
                    """
                    INSERT INTO tasks
                        (task_id, assignment_id, title, department_id, role_id,
                         dependencies_json, required_evidence_json, status,
                         max_attempts, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        request.task_id,
                        request.assignment_id,
                        request.title,
                        request.department_id,
                        request.role_id,
                        _json(request.dependencies),
                        _json(request.required_evidence),
                        TaskStatus.QUEUED.value,
                        request.max_attempts,
                        _timestamp(now),
                        _timestamp(now),
                    ),
                )
                self._audit(
                    "task_routed",
                    request.task_id,
                    actor_id,
                    {
                        "assignment_id": request.assignment_id,
                        "department_id": request.department_id,
                        "role_id": role.role_id,
                    },
                    now,
                )
        except sqlite3.IntegrityError as exc:
            raise StudioError(f"Duplicate task ID: {request.task_id}") from exc

    def _task_ids(self) -> set[str]:
        return {
            row["task_id"]
            for row in self._connection.execute("SELECT task_id FROM tasks")
        }

    def get_task(self, task_id: str) -> dict[str, Any]:
        row = self._connection.execute(
            "SELECT * FROM tasks WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        if row is None:
            raise StudioError(f"Unknown task ID: {task_id}")
        result = dict(row)
        for key in ("dependencies_json", "required_evidence_json", "evidence_json"):
            result[key.removesuffix("_json")] = json.loads(result.pop(key))
        return result

    def route_by_department(self, department_id: str) -> tuple[str, ...]:
        roles = self.registry.roles_for_department(department_id)
        if not roles:
            raise StudioError(f"Unknown department ID: {department_id}")
        return tuple(role.role_id for role in roles)

    def start_task(self, task_id: str, *, actor_id: str, now: datetime) -> None:
        _validate_identifier(actor_id, "Actor ID")
        task = self.get_task(task_id)
        if task["status"] != TaskStatus.QUEUED.value:
            raise StudioError("Only queued tasks can start")
        assignment = self.get_assignment(task["assignment_id"])
        validate_assignment(
            assignment,
            now=now,
            director_ids=self.director_ids,
            check_expiry=True,
        )
        for dependency_id in task["dependencies"]:
            if self.get_task(dependency_id)["status"] not in {
                TaskStatus.APPROVED.value,
                TaskStatus.COMPLETED.value,
            }:
                raise StudioError(f"Task dependency {dependency_id} is not complete")
        self._set_status(task_id, TaskStatus.IN_PROGRESS, actor_id, now, "task_started")

    def submit_for_review(
        self,
        task_id: str,
        *,
        actor_id: str,
        artifact_hash: str,
        evidence_refs: tuple[str, ...],
        now: datetime,
    ) -> None:
        _validate_identifier(actor_id, "Actor ID")
        task = self.get_task(task_id)
        if task["status"] != TaskStatus.IN_PROGRESS.value:
            raise StudioError("Only in-progress tasks can be submitted for review")
        missing = set(task["required_evidence"]) - set(evidence_refs)
        if missing:
            raise StudioError(f"Missing required evidence: {', '.join(sorted(missing))}")
        clean_hash = normalize_hash(artifact_hash)
        _validate_references(evidence_refs, "Evidence references")
        with self._connection:
            self._connection.execute(
                """
                UPDATE tasks SET status = ?, artifact_hash = ?, evidence_json = ?, updated_at = ?
                WHERE task_id = ?
                """,
                (
                    TaskStatus.READY_FOR_REVIEW.value,
                    clean_hash,
                    _json(evidence_refs),
                    _timestamp(now),
                    task_id,
                ),
            )
            self._audit(
                "task_submitted_for_review",
                task_id,
                actor_id,
                {"artifact_hash": clean_hash, "evidence_refs": list(evidence_refs)},
                now,
            )

    def record_review(
        self,
        task_id: str,
        *,
        gate: ReviewGate,
        outcome: ReviewOutcome,
        reviewer_id: str,
        artifact_hash: str,
        evidence_refs: tuple[str, ...],
        dissent: str = "",
        now: datetime,
    ) -> None:
        _validate_identifier(reviewer_id, "Reviewer ID")
        task = self.get_task(task_id)
        if task["status"] != TaskStatus.READY_FOR_REVIEW.value:
            raise StudioError("Reviews require a task that is ready for review")
        clean_hash = normalize_hash(artifact_hash)
        if clean_hash != task["artifact_hash"]:
            raise StudioError("Review artifact hash does not match the submitted artifact")
        if not reviewer_id.strip():
            raise StudioError("Reviewer identity cannot be empty")
        _validate_references(evidence_refs, "Review evidence references")
        if dissent and not FINDING_CODE_PATTERN.fullmatch(dissent):
            raise StudioError("Review dissent must use a non-sensitive uppercase code")
        existing = self._connection.execute(
            """
            SELECT reviewer_id FROM reviews
            WHERE task_id = ? AND artifact_hash = ?
            """,
            (task_id, clean_hash),
        ).fetchall()
        reviewer_ids = {row["reviewer_id"] for row in existing}
        if reviewer_id == task["role_id"] or reviewer_id in reviewer_ids:
            raise StudioError("Reviewers must be independent and distinct from the author role")
        try:
            with self._connection:
                self._connection.execute(
                    """
                    INSERT INTO reviews
                        (task_id, gate, outcome, reviewer_id, artifact_hash,
                         evidence_json, dissent, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        task_id,
                        gate.value,
                        outcome.value,
                        reviewer_id,
                        clean_hash,
                        _json(evidence_refs),
                        dissent,
                        _timestamp(now),
                    ),
                )
                if outcome is ReviewOutcome.BLOCK:
                    self._connection.execute(
                        "UPDATE tasks SET status = ?, updated_at = ? WHERE task_id = ?",
                        (TaskStatus.BLOCKED.value, _timestamp(now), task_id),
                    )
                elif outcome is ReviewOutcome.REVISE:
                    self._connection.execute(
                        "UPDATE tasks SET status = ?, updated_at = ? WHERE task_id = ?",
                        (TaskStatus.IN_PROGRESS.value, _timestamp(now), task_id),
                    )
                self._audit(
                    "review_recorded",
                    task_id,
                    reviewer_id,
                    {
                        "gate": gate.value,
                        "outcome": outcome.value,
                        "artifact_hash": clean_hash,
                        "evidence_refs": list(evidence_refs),
                        "dissent": dissent,
                    },
                    now,
                )
        except sqlite3.IntegrityError as exc:
            raise StudioError(f"Review already exists for gate {gate.value}") from exc

    def submit_observer_report(self, task_id: str, report: ObserverReport, *, now: datetime) -> None:
        task = self.get_task(task_id)
        if task["status"] not in {
            TaskStatus.READY_FOR_REVIEW.value,
            TaskStatus.BLOCKED.value,
        }:
            raise StudioError("Observer reports require a review-ready task")
        if report.observer_id == task["role_id"]:
            raise StudioError("Observers must be independent of the task author role")
        _validate_identifier(report.observer_id, "Observer ID")
        if not FINDING_CODE_PATTERN.fullmatch(report.finding):
            raise StudioError("Observer identity and finding are required")
        _validate_references(report.evidence_refs, "Observer evidence references")
        try:
            with self._connection:
                self._connection.execute(
                    """
                    INSERT INTO observer_reports
                        (task_id, observer_id, artifact_hash, assessment,
                         evidence_json, finding, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        task_id,
                        report.observer_id,
                        task["artifact_hash"],
                        report.assessment.value,
                        _json(report.evidence_refs),
                        report.finding,
                        _timestamp(now),
                    ),
                )
                summary = self.observer_summary(task_id)
                if summary["blocked"]:
                    self._connection.execute(
                        "UPDATE tasks SET status = ?, updated_at = ? WHERE task_id = ?",
                        (TaskStatus.BLOCKED.value, _timestamp(now), task_id),
                    )
                self._audit(
                    "observer_report_recorded",
                    task_id,
                    report.observer_id,
                    {
                        "assessment": report.assessment.value,
                        "finding": report.finding,
                        "evidence_refs": list(report.evidence_refs),
                        "disagreement": summary["disagreement"],
                    },
                    now,
                )
        except sqlite3.IntegrityError as exc:
            raise StudioError(f"Observer {report.observer_id} already reported on {task_id}") from exc

    def observer_summary(self, task_id: str) -> dict[str, Any]:
        task = self.get_task(task_id)
        reports = self._connection.execute(
            """
            SELECT observer_id, assessment, finding FROM observer_reports
            WHERE task_id = ? AND artifact_hash = ?
            """,
            (task_id, task["artifact_hash"]),
        ).fetchall()
        assessments = {row["assessment"] for row in reports}
        disagreement = len(assessments) > 1
        has_block = ObserverAssessment.BLOCK.value in assessments
        has_concern = ObserverAssessment.CONCERN.value in assessments
        resolution = self._connection.execute(
            """
            SELECT director_id, disposition FROM observer_resolutions
            WHERE task_id = ? AND artifact_hash = ?
            """,
            (task_id, task["artifact_hash"]),
        ).fetchone()
        resolved = resolution is not None
        return {
            "report_count": len(reports),
            "assessments": sorted(assessments),
            "disagreement": disagreement,
            "blocked": has_block or ((disagreement or has_concern) and not resolved),
            "resolved": resolved,
            "resolution": dict(resolution) if resolution else None,
        }

    def resolve_observer_disagreement(
        self,
        task_id: str,
        *,
        director_id: str,
        disposition: str,
        evidence_refs: tuple[str, ...],
        now: datetime,
    ) -> None:
        if director_id not in self.director_ids:
            raise StudioError("Only an authorized Director may resolve observer disagreement")
        summary = self.observer_summary(task_id)
        if ObserverAssessment.BLOCK.value in summary["assessments"]:
            raise StudioError("A blocking observer finding cannot be overridden")
        if not summary["disagreement"] and ObserverAssessment.CONCERN.value not in summary["assessments"]:
            raise StudioError("No observer disagreement or concern requires resolution")
        if not FINDING_CODE_PATTERN.fullmatch(disposition) or not evidence_refs:
            raise StudioError("Resolution requires a non-sensitive disposition code and evidence")
        _validate_references(evidence_refs, "Resolution evidence references")
        task = self.get_task(task_id)
        if task["status"] != TaskStatus.BLOCKED.value:
            raise StudioError("Only blocked tasks can receive an observer resolution")
        review_block = self._connection.execute(
            """
            SELECT 1 FROM reviews
            WHERE task_id = ? AND artifact_hash = ? AND outcome = ?
            LIMIT 1
            """,
            (task_id, task["artifact_hash"], ReviewOutcome.BLOCK.value),
        ).fetchone()
        if review_block:
            raise StudioError("A blocking review gate cannot be overridden by observer resolution")
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO observer_resolutions
                    (task_id, artifact_hash, director_id, disposition, evidence_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    task["artifact_hash"],
                    director_id,
                    disposition,
                    _json(evidence_refs),
                    _timestamp(now),
                ),
            )
            self._connection.execute(
                "UPDATE tasks SET status = ?, updated_at = ? WHERE task_id = ?",
                (TaskStatus.READY_FOR_REVIEW.value, _timestamp(now), task_id),
            )
            self._audit(
                "observer_disagreement_resolved",
                task_id,
                director_id,
                {"disposition": disposition, "evidence_refs": list(evidence_refs)},
                now,
            )

    def approve_task(
        self,
        task_id: str,
        *,
        director_id: str,
        artifact_hash: str,
        approved_at: datetime,
    ) -> None:
        if director_id not in self.director_ids:
            raise StudioError("Only an authorized Director may approve a task")
        task = self.get_task(task_id)
        if task["status"] != TaskStatus.READY_FOR_REVIEW.value:
            raise StudioError("Task is not ready for Director approval")
        assignment = self.get_assignment(task["assignment_id"])
        validate_assignment(
            assignment,
            now=approved_at,
            director_ids=self.director_ids,
            check_expiry=True,
        )
        clean_hash = normalize_hash(artifact_hash)
        if clean_hash != task["artifact_hash"]:
            raise StudioError("Approval artifact hash does not match the submitted artifact")
        reviews = self._connection.execute(
            "SELECT gate, outcome, reviewer_id, evidence_json FROM reviews WHERE task_id = ? AND artifact_hash = ?",
            (task_id, clean_hash),
        ).fetchall()
        review_by_gate = {row["gate"]: row for row in reviews}
        if set(review_by_gate) != {gate.value for gate in REQUIRED_GATES}:
            raise StudioError("All four independent review gates are required")
        if any(row["outcome"] != ReviewOutcome.PASS.value for row in review_by_gate.values()):
            raise StudioError("Every review gate must pass")
        reviewer_ids = [row["reviewer_id"] for row in reviews]
        if len(set(reviewer_ids)) != len(REQUIRED_GATES) or task["role_id"] in reviewer_ids:
            raise StudioError("Review gates must have distinct reviewers independent of the author role")
        if any(not json.loads(row["evidence_json"]) for row in reviews):
            raise StudioError("Every review gate must include evidence")

        observer_state = self.observer_summary(task_id)
        if observer_state["report_count"] < 2:
            raise StudioError("At least two independent observer reports are required")
        observer_ids = {
            row["observer_id"]
            for row in self._connection.execute(
                """
                SELECT observer_id FROM observer_reports
                WHERE task_id = ? AND artifact_hash = ?
                """,
                (task_id, clean_hash),
            )
        }
        if observer_ids & set(reviewer_ids):
            raise StudioError("Observer identities must be independent of gate reviewers")
        if observer_state["blocked"]:
            raise StudioError("Observer findings are unresolved")
        if ObserverAssessment.BLOCK.value in observer_state["assessments"]:
            raise StudioError("A blocking observer finding prevents approval")

        now_text = _timestamp(approved_at)
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO director_approvals
                    (approval_type, object_id, approver_id, artifact_hash, approved_at)
                VALUES ('task_release', ?, ?, ?, ?)
                """,
                (task_id, director_id, clean_hash, now_text),
            )
            self._connection.execute(
                "UPDATE tasks SET status = ?, updated_at = ? WHERE task_id = ?",
                (TaskStatus.APPROVED.value, now_text, task_id),
            )
            self._audit(
                "director_task_approved",
                task_id,
                director_id,
                {"artifact_hash": clean_hash, "observer_state": observer_state},
                approved_at,
            )

    def mark_complete(self, task_id: str, *, actor_id: str, now: datetime) -> None:
        task = self.get_task(task_id)
        if task["status"] != TaskStatus.APPROVED.value:
            raise StudioError("Only Director-approved tasks can be marked complete")
        self._set_status(task_id, TaskStatus.COMPLETED, actor_id, now, "task_completed")

    def fail_task(
        self,
        task_id: str,
        *,
        actor_id: str,
        error_code: str,
        retryable: bool,
        now: datetime,
    ) -> None:
        _validate_identifier(actor_id, "Actor ID")
        task = self.get_task(task_id)
        if task["status"] != TaskStatus.IN_PROGRESS.value:
            raise StudioError("Only in-progress tasks can fail")
        if not ERROR_CODE_PATTERN.fullmatch(error_code):
            raise StudioError("Failure must use a non-sensitive uppercase error code")
        with self._connection:
            self._connection.execute(
                """
                UPDATE tasks SET status = ?, retryable = ?, last_error_code = ?, updated_at = ?
                WHERE task_id = ?
                """,
                (
                    TaskStatus.FAILED.value,
                    int(retryable),
                    error_code,
                    _timestamp(now),
                    task_id,
                ),
            )
            self._audit(
                "task_failed",
                task_id,
                actor_id,
                {"error_code": error_code, "retryable": retryable},
                now,
            )

    def retry_task(self, task_id: str, *, actor_id: str, now: datetime) -> None:
        task = self.get_task(task_id)
        if task["status"] != TaskStatus.FAILED.value or not task["retryable"]:
            raise StudioError("Only retryable failed tasks can be retried")
        if task["attempts"] + 1 >= task["max_attempts"]:
            raise StudioError("Task retry limit reached")
        with self._connection:
            self._connection.execute(
                """
                UPDATE tasks SET status = ?, attempts = attempts + 1,
                    retryable = 0, last_error_code = NULL, updated_at = ?
                WHERE task_id = ?
                """,
                (TaskStatus.QUEUED.value, _timestamp(now), task_id),
            )
            self._audit(
                "task_retry_queued",
                task_id,
                actor_id,
                {"attempt": task["attempts"] + 1, "max_attempts": task["max_attempts"]},
                now,
            )

    def verify_audit_chain(self) -> bool:
        previous_hash = "0" * 64
        rows = self._connection.execute("SELECT * FROM audit_events ORDER BY sequence").fetchall()
        for row in rows:
            if row["previous_hash"] != previous_hash:
                return False
            material = _json(
                {
                    "event_type": row["event_type"],
                    "object_id": row["object_id"],
                    "actor_id": row["actor_id"],
                    "event_json": row["event_json"],
                    "previous_hash": row["previous_hash"],
                    "created_at": row["created_at"],
                }
            )
            event_hash = hashlib.sha256(material.encode("utf-8")).hexdigest()
            if event_hash != row["event_hash"]:
                return False
            previous_hash = event_hash
        return True

    def audit_events(self) -> tuple[dict[str, Any], ...]:
        rows = self._connection.execute("SELECT * FROM audit_events ORDER BY sequence").fetchall()
        return tuple(dict(row) for row in rows)

    def approvals(self, object_id: str) -> tuple[dict[str, Any], ...]:
        rows = self._connection.execute(
            "SELECT * FROM director_approvals WHERE object_id = ? ORDER BY approval_id",
            (object_id,),
        ).fetchall()
        return tuple(dict(row) for row in rows)

    def _set_status(
        self,
        task_id: str,
        status: TaskStatus,
        actor_id: str,
        now: datetime,
        event_type: str,
    ) -> None:
        with self._connection:
            self._connection.execute(
                "UPDATE tasks SET status = ?, updated_at = ? WHERE task_id = ?",
                (status.value, _timestamp(now), task_id),
            )
            self._audit(event_type, task_id, actor_id, {"status": status.value}, now)
