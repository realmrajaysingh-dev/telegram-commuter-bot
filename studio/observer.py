"""Independent read-only checks over the local SQLite workflow ledger."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Protocol

from .models import (
    AssignmentManifest,
    ObserverAssessment,
    StudioError,
    TaskStatus,
    validate_assignment,
)
from .registry import RegistryError, RoleRegistry
from .store import _json


OBSERVER_ENGINE_ID = "observer-engine"
BASE_AUDIT_HASH = "0" * 64
KNOWN_TASK_STATUSES = {status.value for status in TaskStatus}
PUBLISH_STATES = {
    "publish",
    "publishing",
    "published",
    "publication_submitted",
    "platform_confirmed",
}
PUBLISH_EVENT_MARKERS = (
    "publish",
    "publication",
    "sendphoto",
    "sendpoll",
    "telegram_send",
    "platform_submit",
    "platform_submission",
    "release_submitted",
)
PUBLISH_ACTIONS = PUBLISH_STATES | {
    "send_photo",
    "send_poll",
    "submit_release",
    "submit_platform",
}


class ObserverError(RuntimeError):
    """Raised when the observer cannot safely read a complete ledger snapshot."""


class FindingSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    BLOCK = "block"


@dataclass(frozen=True)
class ObserverFinding:
    finding_id: str
    observer_id: str
    code: str
    severity: FindingSeverity
    subject_id: str
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True)
class ObserverRunReport:
    generated_at: datetime
    observer_id: str
    findings: tuple[ObserverFinding, ...]

    def by_code(self, code: str) -> tuple[ObserverFinding, ...]:
        return tuple(finding for finding in self.findings if finding.code == code)

    @property
    def is_clear(self) -> bool:
        return not any(
            finding.severity in {FindingSeverity.WARNING, FindingSeverity.BLOCK}
            for finding in self.findings
        )


@dataclass(frozen=True)
class LedgerSnapshot:
    assignments: tuple[dict[str, Any], ...]
    tasks: tuple[dict[str, Any], ...]
    reviews: tuple[dict[str, Any], ...]
    observer_reports: tuple[dict[str, Any], ...]
    observer_resolutions: tuple[dict[str, Any], ...]
    approvals: tuple[dict[str, Any], ...]
    audit_events: tuple[dict[str, Any], ...]


class Observer(Protocol):
    observer_id: str

    def inspect(self, snapshot: LedgerSnapshot, now: datetime) -> list[ObserverFinding]: ...


def _stable_finding(
    observer_id: str,
    code: str,
    severity: FindingSeverity,
    subject_id: str,
    evidence_refs: tuple[str, ...],
) -> ObserverFinding:
    fingerprint = _json(
        {
            "observer_id": observer_id,
            "code": code,
            "subject_id": subject_id,
            "evidence_refs": sorted(evidence_refs),
        }
    )
    finding_id = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:24]
    return ObserverFinding(
        finding_id=finding_id,
        observer_id=observer_id,
        code=code,
        severity=severity,
        subject_id=subject_id,
        evidence_refs=tuple(sorted(set(evidence_refs))),
    )


class AssignmentPolicyObserver:
    observer_id = "observer-assignment-policy"

    def __init__(self, registry: RoleRegistry, director_ids: frozenset[str]) -> None:
        self.registry = registry
        self.director_ids = director_ids

    def inspect(self, snapshot: LedgerSnapshot, now: datetime) -> list[ObserverFinding]:
        findings: list[ObserverFinding] = []
        approvals_by_assignment: dict[str, list[dict[str, Any]]] = {}
        for approval in snapshot.approvals:
            if approval["approval_type"] in {"assignment", "assignment_extension"}:
                approvals_by_assignment.setdefault(approval["object_id"], []).append(approval)

        manifest_by_id: dict[str, AssignmentManifest] = {}
        for row in snapshot.assignments:
            assignment_id = row["assignment_id"]
            evidence = (f"assignment:{assignment_id}",)
            try:
                manifest = AssignmentManifest.from_json(row["manifest_json"])
                validate_assignment(
                    manifest,
                    now=now,
                    director_ids=self.director_ids,
                    check_expiry=False,
                )
            except (StudioError, TypeError, ValueError):
                findings.append(
                    _stable_finding(
                        self.observer_id,
                        "ASSIGNMENT_POLICY_INVALID",
                        FindingSeverity.BLOCK,
                        assignment_id,
                        evidence,
                    )
                )
                continue

            manifest_by_id[assignment_id] = manifest
            if manifest.assignment_id != assignment_id:
                findings.append(
                    _stable_finding(
                        self.observer_id,
                        "ASSIGNMENT_ID_MISMATCH",
                        FindingSeverity.BLOCK,
                        assignment_id,
                        evidence,
                    )
                )
                continue

            current_hash = hashlib.sha256(
                _json(manifest.to_dict()).encode("utf-8")
            ).hexdigest()
            required_type = "assignment_extension" if manifest.extensions else "assignment"
            matching_approval = any(
                item["approval_type"] == required_type
                and item["approver_id"] == manifest.director_id
                and item["artifact_hash"] == current_hash
                for item in approvals_by_assignment.get(assignment_id, ())
            )
            if not matching_approval:
                findings.append(
                    _stable_finding(
                        self.observer_id,
                        "ASSIGNMENT_APPROVAL_MISSING_OR_MISMATCHED",
                        FindingSeverity.BLOCK,
                        assignment_id,
                        evidence + ("director-approval",),
                    )
                )

        audit_creators = {
            row["object_id"]: row["actor_id"]
            for row in snapshot.audit_events
            if row["event_type"] == "task_routed"
        }
        for task in snapshot.tasks:
            task_id = task["task_id"]
            evidence = (f"task:{task_id}",)
            if task["status"] not in KNOWN_TASK_STATUSES:
                findings.append(
                    _stable_finding(
                        self.observer_id,
                        "TASK_STATUS_INVALID",
                        FindingSeverity.BLOCK,
                        task_id,
                        evidence + ("task-status",),
                    )
                )
            manifest = manifest_by_id.get(task["assignment_id"])
            if manifest is None:
                findings.append(
                    _stable_finding(
                        self.observer_id,
                        "TASK_ASSIGNMENT_MISSING_OR_INVALID",
                        FindingSeverity.BLOCK,
                        task_id,
                        evidence + (f"assignment:{task['assignment_id']}",),
                    )
                )
            try:
                role = self.registry.get(task["role_id"])
            except (RegistryError, KeyError):
                findings.append(
                    _stable_finding(
                        self.observer_id,
                        "TASK_ROLE_UNKNOWN",
                        FindingSeverity.BLOCK,
                        task_id,
                        evidence + (f"role:{task['role_id']}",),
                    )
                )
            else:
                if role.department_id != task["department_id"]:
                    findings.append(
                        _stable_finding(
                            self.observer_id,
                            "TASK_DEPARTMENT_MISMATCH",
                            FindingSeverity.BLOCK,
                            task_id,
                            evidence + (f"role:{task['role_id']}",),
                        )
                    )
            creator = audit_creators.get(task_id)
            if creator is None:
                findings.append(
                    _stable_finding(
                        self.observer_id,
                        "TASK_ROUTING_AUDIT_MISSING",
                        FindingSeverity.BLOCK,
                        task_id,
                        evidence + ("audit:task_routed",),
                    )
                )
            elif creator == task["role_id"]:
                findings.append(
                    _stable_finding(
                        self.observer_id,
                        "TASK_AUTHOR_IDENTITY_CONFLICT",
                        FindingSeverity.BLOCK,
                        task_id,
                        evidence + (f"role:{task['role_id']}",),
                    )
                )
            try:
                dependencies = json.loads(task["dependencies_json"])
                valid_dependencies = isinstance(dependencies, list) and all(
                    isinstance(dependency, str) for dependency in dependencies
                )
            except (TypeError, json.JSONDecodeError):
                valid_dependencies = False
                dependencies = []
            if not valid_dependencies or len(dependencies) != len(set(dependencies)):
                findings.append(
                    _stable_finding(
                        self.observer_id,
                        "TASK_DEPENDENCIES_INVALID",
                        FindingSeverity.BLOCK,
                        task_id,
                        evidence + ("task-dependencies",),
                    )
                )
                continue
            for dependency in dependencies:
                parent = next((candidate for candidate in snapshot.tasks if candidate["task_id"] == dependency), None)
                if parent is None or parent["assignment_id"] != task["assignment_id"]:
                    findings.append(
                        _stable_finding(
                            self.observer_id,
                            "TASK_DEPENDENCY_MISSING_OR_CROSS_ASSIGNMENT",
                            FindingSeverity.BLOCK,
                            task_id,
                            evidence + (f"task:{dependency}",),
                        )
                    )
        return findings


class AssignmentHorizonObserver:
    observer_id = "observer-assignment-horizon"

    def inspect(self, snapshot: LedgerSnapshot, now: datetime) -> list[ObserverFinding]:
        findings: list[ObserverFinding] = []
        for row in snapshot.assignments:
            assignment_id = row["assignment_id"]
            evidence = (f"assignment:{assignment_id}",)
            try:
                manifest = AssignmentManifest.from_json(row["manifest_json"])
            except (StudioError, TypeError, ValueError):
                continue
            initial_duration = (
                manifest.extensions[0].previous_expiry - manifest.starts_at
                if manifest.extensions
                else manifest.expires_at - manifest.starts_at
            )
            if initial_duration.total_seconds() <= 0 or initial_duration.total_seconds() > 7 * 24 * 60 * 60:
                findings.append(
                    _stable_finding(
                        self.observer_id,
                        "ASSIGNMENT_SEVEN_DAY_WINDOW_VIOLATION",
                        FindingSeverity.BLOCK,
                        assignment_id,
                        evidence,
                    )
                )
            extension_duration = (
                manifest.extensions[0].new_expiry - manifest.extensions[0].previous_expiry
                if manifest.extensions
                else None
            )
            if extension_duration is not None and (
                extension_duration.total_seconds() <= 0
                or extension_duration.total_seconds() > 7 * 24 * 60 * 60
            ):
                findings.append(
                    _stable_finding(
                        self.observer_id,
                        "ASSIGNMENT_EXTENSION_WINDOW_VIOLATION",
                        FindingSeverity.BLOCK,
                        assignment_id,
                        evidence,
                    )
                )
            total_duration = manifest.expires_at - manifest.starts_at
            forward_duration = manifest.expires_at - now
            if total_duration.total_seconds() > 14 * 24 * 60 * 60 or forward_duration.total_seconds() > 14 * 24 * 60 * 60:
                findings.append(
                    _stable_finding(
                        self.observer_id,
                        "ASSIGNMENT_FOURTEEN_DAY_HORIZON_VIOLATION",
                        FindingSeverity.BLOCK,
                        assignment_id,
                        evidence,
                    )
                )
            if now >= manifest.expires_at:
                findings.append(
                    _stable_finding(
                        self.observer_id,
                        "ASSIGNMENT_EXPIRED",
                        FindingSeverity.WARNING,
                        assignment_id,
                        evidence,
                    )
                )
        return findings


class DuplicateTaskObserver:
    observer_id = "observer-duplicate-task"

    def inspect(self, snapshot: LedgerSnapshot, now: datetime) -> list[ObserverFinding]:
        groups: dict[tuple[str, str, str], list[str]] = {}
        findings: list[ObserverFinding] = []
        task_id_counts: dict[str, int] = {}
        for task in snapshot.tasks:
            task_id = task["task_id"]
            task_id_counts[task_id] = task_id_counts.get(task_id, 0) + 1
            try:
                dependencies = tuple(sorted(json.loads(task["dependencies_json"])))
            except (TypeError, json.JSONDecodeError):
                dependencies = ()
            normalized_title = " ".join(task["title"].casefold().split())
            signature = (task["assignment_id"], task["role_id"], f"{normalized_title}|{dependencies}")
            groups.setdefault(signature, []).append(task_id)
        for task_id, count in task_id_counts.items():
            if count > 1:
                findings.append(
                    _stable_finding(
                        self.observer_id,
                        "DUPLICATE_TASK_ID",
                        FindingSeverity.BLOCK,
                        task_id,
                        tuple(f"task:{task_id}" for _ in range(count)),
                    )
                )
        for (assignment_id, role_id, _), task_ids in groups.items():
            if len(task_ids) < 2:
                continue
            evidence = tuple(f"task:{task_id}" for task_id in sorted(task_ids))
            findings.append(
                _stable_finding(
                    self.observer_id,
                    "DUPLICATE_TASK_REQUEST",
                    FindingSeverity.WARNING,
                    assignment_id,
                    evidence + (f"role:{role_id}",),
                )
            )
        return findings


class ApprovalObserver:
    observer_id = "observer-director-approval"

    def __init__(self, director_ids: frozenset[str]) -> None:
        self.director_ids = director_ids

    def inspect(self, snapshot: LedgerSnapshot, now: datetime) -> list[ObserverFinding]:
        findings: list[ObserverFinding] = []
        for task in snapshot.tasks:
            if task["status"] not in {TaskStatus.APPROVED.value, TaskStatus.COMPLETED.value}:
                continue
            task_id = task["task_id"]
            matching = [
                approval
                for approval in snapshot.approvals
                if approval["approval_type"] == "task_release"
                and approval["object_id"] == task_id
                and approval["artifact_hash"] == task["artifact_hash"]
                and approval["approver_id"] in self.director_ids
            ]
            if not task["artifact_hash"] or not matching:
                findings.append(
                    _stable_finding(
                        self.observer_id,
                        "TASK_RELEASE_APPROVAL_MISSING_OR_MISMATCHED",
                        FindingSeverity.BLOCK,
                        task_id,
                        (f"task:{task_id}", "director-approval"),
                    )
                )
        return findings


class PublishingObserver:
    observer_id = "observer-publishing-boundary"

    def inspect(self, snapshot: LedgerSnapshot, now: datetime) -> list[ObserverFinding]:
        findings: list[ObserverFinding] = []
        for task in snapshot.tasks:
            state = str(task["status"]).casefold()
            if state in PUBLISH_STATES or state.startswith("published_"):
                findings.append(
                    _stable_finding(
                        self.observer_id,
                        "UNAUTHORIZED_PUBLISH_STATE",
                        FindingSeverity.BLOCK,
                        task["task_id"],
                        (f"task:{task['task_id']}", "publishing-boundary"),
                    )
                )
        for event in snapshot.audit_events:
            event_type = str(event["event_type"]).casefold()
            if any(marker in event_type for marker in PUBLISH_EVENT_MARKERS):
                findings.append(
                    _stable_finding(
                        self.observer_id,
                        "UNAUTHORIZED_PUBLISH_EVENT",
                        FindingSeverity.BLOCK,
                        event["object_id"],
                        (f"audit-sequence:{event['sequence']}", "publishing-boundary"),
                    )
                )
                continue
            try:
                payload = json.loads(event["event_json"])
            except (TypeError, json.JSONDecodeError):
                continue
            if self._contains_publish_action(payload):
                findings.append(
                    _stable_finding(
                        self.observer_id,
                        "UNAUTHORIZED_PUBLISH_EVENT",
                        FindingSeverity.BLOCK,
                        event["object_id"],
                        (f"audit-sequence:{event['sequence']}", "publishing-boundary"),
                    )
                )
        return findings

    @classmethod
    def _contains_publish_action(cls, value: Any, key: str = "") -> bool:
        if isinstance(value, dict):
            return any(cls._contains_publish_action(item, str(item_key).casefold()) for item_key, item in value.items())
        if isinstance(value, list):
            return any(cls._contains_publish_action(item, key) for item in value)
        return (
            key in {"action", "operation", "state", "status"}
            and isinstance(value, str)
            and (
                value.casefold() in PUBLISH_ACTIONS
                or value.casefold().replace("_", "").replace("-", "") in {
                    "sendphoto",
                    "sendpoll",
                    "submitrelease",
                    "submitplatform",
                }
            )
        )


class AuditConsistencyObserver:
    observer_id = "observer-audit-consistency"

    def inspect(self, snapshot: LedgerSnapshot, now: datetime) -> list[ObserverFinding]:
        previous_hash = BASE_AUDIT_HASH
        findings: list[ObserverFinding] = []
        for expected_sequence, event in enumerate(snapshot.audit_events, 1):
            subject_id = f"sequence-{event['sequence']}"
            evidence = (f"audit-sequence:{event['sequence']}",)
            if event["sequence"] != expected_sequence:
                findings.append(
                    _stable_finding(
                        self.observer_id,
                        "AUDIT_SEQUENCE_GAP",
                        FindingSeverity.BLOCK,
                        subject_id,
                        evidence,
                    )
                )
            if event["previous_hash"] != previous_hash:
                findings.append(
                    _stable_finding(
                        self.observer_id,
                        "AUDIT_PREVIOUS_HASH_MISMATCH",
                        FindingSeverity.BLOCK,
                        subject_id,
                        evidence,
                    )
                )
            try:
                json.loads(event["event_json"])
                material = _json(
                    {
                        "event_type": event["event_type"],
                        "object_id": event["object_id"],
                        "actor_id": event["actor_id"],
                        "event_json": event["event_json"],
                        "previous_hash": event["previous_hash"],
                        "created_at": event["created_at"],
                    }
                )
                actual_hash = hashlib.sha256(material.encode("utf-8")).hexdigest()
            except (TypeError, json.JSONDecodeError, ValueError):
                actual_hash = ""
            if actual_hash != event["event_hash"]:
                findings.append(
                    _stable_finding(
                        self.observer_id,
                        "AUDIT_EVENT_HASH_MISMATCH",
                        FindingSeverity.BLOCK,
                        subject_id,
                        evidence,
                    )
                )
            previous_hash = actual_hash or event["event_hash"]
        return findings


class FailedTaskObserver:
    observer_id = "observer-failed-task"

    def inspect(self, snapshot: LedgerSnapshot, now: datetime) -> list[ObserverFinding]:
        findings: list[ObserverFinding] = []
        for task in snapshot.tasks:
            if task["status"] != TaskStatus.FAILED.value:
                continue
            exhausted = task["attempts"] + 1 >= task["max_attempts"]
            severity = FindingSeverity.BLOCK if exhausted or not task["retryable"] else FindingSeverity.WARNING
            code = "TASK_FAILURE_REQUIRES_MANUAL_REVIEW" if severity is FindingSeverity.BLOCK else "TASK_RETRYABLE_FAILURE"
            findings.append(
                _stable_finding(
                    self.observer_id,
                    code,
                    severity,
                    task["task_id"],
                    (f"task:{task['task_id']}", "task-failure"),
                )
            )
        return findings


class ObserverDisagreementObserver:
    observer_id = "observer-disagreement"

    def __init__(self, director_ids: frozenset[str]) -> None:
        self.director_ids = director_ids

    def inspect(self, snapshot: LedgerSnapshot, now: datetime) -> list[ObserverFinding]:
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for report in snapshot.observer_reports:
            grouped.setdefault((report["task_id"], report["artifact_hash"]), []).append(report)
        resolution_by_artifact = {
            (resolution["task_id"], resolution["artifact_hash"]): resolution
            for resolution in snapshot.observer_resolutions
        }
        findings: list[ObserverFinding] = []
        for (task_id, artifact_hash), reports in grouped.items():
            assessments = {report["assessment"] for report in reports}
            if len(assessments) < 2:
                continue
            evidence = (f"task:{task_id}", f"artifact:{artifact_hash}")
            resolution = resolution_by_artifact.get((task_id, artifact_hash))
            resolution_evidence: Any = []
            if resolution is not None:
                try:
                    resolution_evidence = json.loads(resolution["evidence_json"])
                except (TypeError, json.JSONDecodeError):
                    resolution_evidence = []
            is_resolved = bool(
                resolution is not None
                and ObserverAssessment.BLOCK.value not in assessments
                and resolution["director_id"] in self.director_ids
                and resolution["disposition"]
                and isinstance(resolution_evidence, list)
                and resolution_evidence
            )
            if resolution is not None and not is_resolved:
                findings.append(
                    _stable_finding(
                        self.observer_id,
                        "OBSERVER_RESOLUTION_INVALID",
                        FindingSeverity.BLOCK,
                        task_id,
                        evidence + ("director-resolution",),
                    )
                )
            findings.append(
                _stable_finding(
                    self.observer_id,
                    "OBSERVER_DISAGREEMENT_RESOLVED" if is_resolved else "OBSERVER_DISAGREEMENT",
                    FindingSeverity.INFO if is_resolved else FindingSeverity.BLOCK,
                    task_id,
                    evidence,
                )
            )
        return findings


class ObserverIndependenceObserver:
    observer_id = "observer-independence"

    def inspect(self, snapshot: LedgerSnapshot, now: datetime) -> list[ObserverFinding]:
        creators = {
            event["object_id"]: event["actor_id"]
            for event in snapshot.audit_events
            if event["event_type"] == "task_routed"
        }
        reviewers_by_task: dict[tuple[str, str], set[str]] = {}
        observers_by_task: dict[tuple[str, str], set[str]] = {}
        for review in snapshot.reviews:
            reviewers_by_task.setdefault((review["task_id"], review["artifact_hash"]), set()).add(review["reviewer_id"])
        for report in snapshot.observer_reports:
            observers_by_task.setdefault((report["task_id"], report["artifact_hash"]), set()).add(report["observer_id"])

        findings: list[ObserverFinding] = []
        for task in snapshot.tasks:
            task_id = task["task_id"]
            creator = creators.get(task_id)
            for artifact_hash in {
                record["artifact_hash"]
                for record in snapshot.reviews + snapshot.observer_reports
                if record["task_id"] == task_id
            }:
                reviewers = reviewers_by_task.get((task_id, artifact_hash), set())
                observers = observers_by_task.get((task_id, artifact_hash), set())
                if creator and (creator in reviewers or creator in observers):
                    findings.append(
                        _stable_finding(
                            self.observer_id,
                            "REVIEWER_OR_OBSERVER_NOT_INDEPENDENT",
                            FindingSeverity.BLOCK,
                            task_id,
                            (f"task:{task_id}", f"artifact:{artifact_hash}", "review-independence"),
                        )
                    )
                if reviewers & observers:
                    findings.append(
                        _stable_finding(
                            self.observer_id,
                            "OBSERVER_REUSES_REVIEWER_IDENTITY",
                            FindingSeverity.BLOCK,
                            task_id,
                            (f"task:{task_id}", f"artifact:{artifact_hash}", "review-independence"),
                        )
                    )
        return findings


class ObserverEngine:
    """Coordinates deterministic independent observers without writing to the ledger."""

    def __init__(
        self,
        database: str | Path,
        registry: RoleRegistry,
        director_ids: set[str] | frozenset[str],
    ) -> None:
        self.database = Path(database).resolve()
        if not self.database.is_file():
            raise ObserverError("Observer database must be an existing local SQLite file")
        self.director_ids = frozenset(director_ids)
        if not self.director_ids:
            raise ObserverError("Observer requires configured Director IDs")
        self.observers: tuple[Observer, ...] = (
            AssignmentPolicyObserver(registry, self.director_ids),
            AssignmentHorizonObserver(),
            DuplicateTaskObserver(),
            ApprovalObserver(self.director_ids),
            PublishingObserver(),
            AuditConsistencyObserver(),
            FailedTaskObserver(),
            ObserverDisagreementObserver(self.director_ids),
            ObserverIndependenceObserver(),
        )

    def inspect(self, *, now: datetime) -> ObserverRunReport:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ObserverError("Observer timestamp must include a timezone")
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(f"{self.database.as_uri()}?mode=ro", uri=True)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only = ON")
            connection.execute("BEGIN")
            snapshot = self._load_snapshot(connection)
            findings = [
                finding
                for observer in self.observers
                for finding in observer.inspect(snapshot, now.astimezone(timezone.utc))
            ]
            connection.rollback()
        except sqlite3.Error as exc:
            raise ObserverError("Observer could not read a consistent SQLite snapshot") from exc
        finally:
            if connection is not None:
                connection.close()
        unique = {finding.finding_id: finding for finding in findings}
        ordered = tuple(
            sorted(
                unique.values(),
                key=lambda finding: (
                    finding.severity.value,
                    finding.observer_id,
                    finding.code,
                    finding.subject_id,
                ),
            )
        )
        return ObserverRunReport(
            generated_at=now.astimezone(timezone.utc),
            observer_id=OBSERVER_ENGINE_ID,
            findings=ordered,
        )

    @staticmethod
    def _load_snapshot(connection: sqlite3.Connection) -> LedgerSnapshot:
        expected_tables = {
            "assignments",
            "tasks",
            "reviews",
            "observer_reports",
            "observer_resolutions",
            "director_approvals",
            "audit_events",
        }
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
        available_tables = {row["name"] for row in rows}
        missing = expected_tables - available_tables
        if missing:
            raise ObserverError(f"Observer ledger tables are missing: {', '.join(sorted(missing))}")

        def read(table: str) -> tuple[dict[str, Any], ...]:
            return tuple(dict(row) for row in connection.execute(f"SELECT * FROM {table}"))

        return LedgerSnapshot(
            assignments=read("assignments"),
            tasks=read("tasks"),
            reviews=read("reviews"),
            observer_reports=read("observer_reports"),
            observer_resolutions=read("observer_resolutions"),
            approvals=read("director_approvals"),
            audit_events=read("audit_events"),
        )
