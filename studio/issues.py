"""Local issue inbox and Director-authorized deterministic repair prototype."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import threading
from typing import Any, Protocol
import uuid


AUTHORIZATION_TTL = timedelta(minutes=5)
MAX_REPAIR_ATTEMPTS = 3
REFERENCE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
SECRET_PATTERN = re.compile(
    r"(?i)\b(api[_-]?key|token|password|secret|authorization)\b"
    r"(\s*[:=]\s*|\s+)(?:bearer\s+)?[^\s,;]+"
)
ANSI_PATTERN = re.compile(r"\x1B(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1B\\))")


class IssueError(ValueError):
    """Raised when a local issue or repair operation violates policy."""


class IssueNotFound(IssueError):
    """Raised when an issue or authorization does not exist."""


class IssueCategory(StrEnum):
    TASK_FAILURE = "task_failure"
    STUCK_TASK = "stuck_task"
    ARTIFACT_REFERENCE = "artifact_reference"
    FFMPEG = "ffmpeg"


class IssueSeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class IssueStatus(StrEnum):
    PENDING = "pending"
    NEEDS_APPROVAL = "needs_approval"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    BLOCKED = "blocked"
    FAILED = "failed"


class RepairAction(StrEnum):
    RETRY_SIMULATED_TASK = "retry_simulated_task"
    RECOVER_SIMULATED_TASK = "recover_simulated_task"
    VALIDATE_ARTIFACT_REFS = "validate_artifact_refs"
    FFMPEG_SYNTHETIC_TEST = "ffmpeg_synthetic_test"
    AI_CODE_REPAIR_DISABLED = "ai_code_repair_disabled"


@dataclass(frozen=True)
class RepairReport:
    issue_id: str
    authorization_id: str
    attempt_no: int
    status: IssueStatus
    repair_result: dict[str, Any]
    observer_review: dict[str, Any]
    started_at: str
    completed_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "issue_id": self.issue_id,
            "authorization_id": self.authorization_id,
            "attempt_no": self.attempt_no,
            "status": self.status.value,
            "repair_result": self.repair_result,
            "observer_review": self.observer_review,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }


class RepairObserver(Protocol):
    observer_id: str

    def review(
        self, issue: dict[str, Any], result: dict[str, Any]
    ) -> tuple[bool, str]: ...


class LocalRepairObserver:
    """Independent deterministic check of a synthetic repair result."""

    observer_id = "local-repair-observer"

    def review(
        self, issue: dict[str, Any], result: dict[str, Any]
    ) -> tuple[bool, str]:
        expected = result.get("expected_success")
        if not isinstance(expected, bool) or result.get("success") is not expected:
            return False, "REPAIR_OUTCOME_DISAGREEMENT"
        action = issue["suggested_repair_action"]
        if action == RepairAction.RETRY_SIMULATED_TASK.value:
            valid = result.get("task_status") == ("queued" if expected else "failed")
        elif action == RepairAction.RECOVER_SIMULATED_TASK.value:
            valid = result.get("task_status") == ("recovered" if expected else "stuck")
        elif action == RepairAction.VALIDATE_ARTIFACT_REFS.value:
            valid = expected is (not result.get("invalid_refs") and not result.get("missing_refs"))
        elif action == RepairAction.FFMPEG_SYNTHETIC_TEST.value:
            valid = result.get("synthetic_render_passed") is expected
        else:
            return False, "REPAIR_ACTION_NOT_RECOGNIZED"
        if not valid:
            return False, "REPAIR_RESULT_SHAPE_DISAGREEMENT"
        return True, "INDEPENDENT_SYNTHETIC_CHECK_PASSED"


def sanitize_diagnostic_logs(value: str) -> str:
    """Strip terminal control sequences and redact common inline credentials."""
    clean = ANSI_PATTERN.sub("", value)
    clean = SECRET_PATTERN.sub(lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", clean)
    return clean[:20_000]


def classify_ffmpeg_error(log_text: str) -> str:
    """Classify common FFmpeg diagnostics without invoking FFmpeg."""
    normalized = log_text.casefold()
    classifiers = (
        (("unknown encoder", "encoder not found"), "unsupported_encoder"),
        (("invalid data found", "moov atom not found", "invalid argument"), "invalid_media"),
        (("no such file or directory", "input file not found"), "missing_input"),
        (("permission denied", "operation not permitted"), "permission_error"),
        (("out of memory", "cannot allocate memory"), "resource_limit"),
    )
    for needles, classification in classifiers:
        if any(needle in normalized for needle in needles):
            return classification
    return "unclassified"


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise IssueError("Timestamps must include a timezone")
    return value.astimezone(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _required_text(value: object, label: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        raise IssueError(f"{label} must be non-empty and at most {limit} characters")
    return value.strip()


def _enum_value(value: object, enum_type: type[StrEnum], label: str) -> str:
    if not isinstance(value, str):
        raise IssueError(f"Invalid {label}")
    try:
        return enum_type(value).value
    except ValueError as exc:
        raise IssueError(f"Invalid {label}") from exc


class IssueEngine:
    """Local SQLite issue registry with human-triggered, allowlisted repairs."""

    def __init__(
        self,
        database: str | Path,
        director_ids: set[str] | frozenset[str],
        *,
        observer: RepairObserver | None = None,
        authorization_ttl: timedelta = AUTHORIZATION_TTL,
    ) -> None:
        self.database = Path(database)
        self.director_ids = frozenset(director_ids)
        if not self.director_ids or any(
            not isinstance(item, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", item)
            for item in self.director_ids
        ):
            raise IssueError("At least one valid local Director ID must be configured")
        if authorization_ttl <= timedelta(0) or authorization_ttl > timedelta(hours=1):
            raise IssueError("Authorization expiry must be positive and no longer than one hour")
        self.authorization_ttl = authorization_ttl
        self.observer = observer or LocalRepairObserver()
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

    def __enter__(self) -> "IssueEngine":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def _create_schema(self) -> None:
        with self._connection:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS studio_issues (
                    issue_id TEXT PRIMARY KEY,
                    project TEXT NOT NULL,
                    department TEXT NOT NULL,
                    category TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    description TEXT NOT NULL,
                    diagnostic_logs TEXT NOT NULL,
                    evidence_refs_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    suggested_repair_action TEXT NOT NULL,
                    director_instructions TEXT NOT NULL DEFAULT '',
                    simulation_json TEXT NOT NULL,
                    max_attempts INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS studio_repair_attempts (
                    attempt_id TEXT PRIMARY KEY,
                    issue_id TEXT NOT NULL REFERENCES studio_issues(issue_id),
                    attempt_no INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    repair_result_json TEXT NOT NULL DEFAULT '{}',
                    observer_review_json TEXT NOT NULL DEFAULT '{}',
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    UNIQUE(issue_id, attempt_no)
                );
                CREATE TABLE IF NOT EXISTS studio_director_authorizations (
                    authorization_id TEXT PRIMARY KEY,
                    issue_id TEXT NOT NULL REFERENCES studio_issues(issue_id),
                    attempt_id TEXT NOT NULL UNIQUE REFERENCES studio_repair_attempts(attempt_id),
                    director_id TEXT NOT NULL,
                    instructions TEXT NOT NULL,
                    authorized_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    consumed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS studio_artifact_catalog (
                    artifact_ref TEXT PRIMARY KEY,
                    available INTEGER NOT NULL CHECK (available IN (0, 1))
                );
                CREATE TABLE IF NOT EXISTS studio_issue_audit_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    issue_id TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    event_json TEXT NOT NULL,
                    previous_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL
                );
                """
            )

    def _audit(
        self,
        event_type: str,
        issue_id: str,
        actor_id: str,
        data: dict[str, Any],
        created_at: str,
    ) -> None:
        previous = self._connection.execute(
            "SELECT event_hash FROM studio_issue_audit_events ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        previous_hash = previous["event_hash"] if previous else "0" * 64
        event_json = _json(data)
        material = _json(
            {
                "event_type": event_type,
                "issue_id": issue_id,
                "actor_id": actor_id,
                "event_json": event_json,
                "previous_hash": previous_hash,
                "created_at": created_at,
            }
        )
        event_hash = hashlib.sha256(material.encode("utf-8")).hexdigest()
        self._connection.execute(
            """
            INSERT INTO studio_issue_audit_events
                (event_type, issue_id, actor_id, event_json, previous_hash, event_hash, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (event_type, issue_id, actor_id, event_json, previous_hash, event_hash, created_at),
        )

    def create_issue(
        self,
        *,
        project: str,
        department: str,
        category: str,
        severity: str,
        description: str,
        diagnostic_logs: str = "",
        evidence_refs: tuple[str, ...] | list[str] = (),
        suggested_repair_action: str | None = None,
        director_instructions: str = "",
        needs_approval: bool = False,
        simulation: dict[str, Any] | None = None,
        max_attempts: int = MAX_REPAIR_ATTEMPTS,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        project = _required_text(project, "Project", 100)
        department = _required_text(department, "Department", 100)
        description = _required_text(description, "Description", 2_000)
        if not isinstance(diagnostic_logs, str) or not isinstance(director_instructions, str):
            raise IssueError("Diagnostic logs and Director instructions must be text")
        category_value = _enum_value(category, IssueCategory, "issue category")
        severity_value = _enum_value(severity, IssueSeverity, "issue severity")
        expected_action = {
            IssueCategory.TASK_FAILURE.value: RepairAction.RETRY_SIMULATED_TASK.value,
            IssueCategory.STUCK_TASK.value: RepairAction.RECOVER_SIMULATED_TASK.value,
            IssueCategory.ARTIFACT_REFERENCE.value: RepairAction.VALIDATE_ARTIFACT_REFS.value,
            IssueCategory.FFMPEG.value: RepairAction.FFMPEG_SYNTHETIC_TEST.value,
        }[category_value]
        action = suggested_repair_action or expected_action
        action_value = _enum_value(action, RepairAction, "suggested repair action")
        if action_value == RepairAction.AI_CODE_REPAIR_DISABLED.value:
            raise IssueError("AI-generated code repair is disabled")
        if action_value != expected_action:
            raise IssueError("Suggested repair action does not match the issue category")
        if not isinstance(needs_approval, bool):
            raise IssueError("needs_approval must be a boolean")
        if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or not 1 <= max_attempts <= MAX_REPAIR_ATTEMPTS:
            raise IssueError(f"max_attempts must be between 1 and {MAX_REPAIR_ATTEMPTS}")
        refs = self._validate_evidence_refs(evidence_refs)
        if director_instructions and len(director_instructions.strip()) > 1_000:
            raise IssueError("Director instructions must be at most 1,000 characters")
        instructions = sanitize_diagnostic_logs(director_instructions.strip())
        simulation_data = self._validate_simulation(
            action_value, {} if simulation is None else simulation
        )
        created_at = _timestamp(now or datetime.now(timezone.utc))
        issue_id = uuid.uuid4().hex
        status = IssueStatus.NEEDS_APPROVAL.value if needs_approval else IssueStatus.PENDING.value
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO studio_issues (
                    issue_id, project, department, category, severity, description,
                    diagnostic_logs, evidence_refs_json, status, suggested_repair_action,
                    director_instructions, simulation_json, max_attempts, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    issue_id,
                    project,
                    department,
                    category_value,
                    severity_value,
                    description,
                    sanitize_diagnostic_logs(diagnostic_logs),
                    _json(refs),
                    status,
                    action_value,
                    instructions,
                    _json(simulation_data),
                    max_attempts,
                    created_at,
                    created_at,
                ),
            )
            self._audit(
                "issue_detected",
                issue_id,
                "issue-detector",
                {"category": category_value, "severity": severity_value, "status": status},
                created_at,
            )
        return self.get_issue(issue_id)

    @staticmethod
    def _validate_evidence_refs(values: tuple[str, ...] | list[str]) -> list[str]:
        if not isinstance(values, (tuple, list)) or len(values) > 100:
            raise IssueError("Evidence references must be a list of at most 100 text values")
        if any(not isinstance(value, str) or len(value) > 256 for value in values):
            raise IssueError("Each evidence reference must be text of at most 256 characters")
        return list(values)

    @staticmethod
    def _validate_simulation(action: str, simulation: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            RepairAction.RETRY_SIMULATED_TASK.value: {"retry_succeeds"},
            RepairAction.RECOVER_SIMULATED_TASK.value: {"recovery_succeeds"},
            RepairAction.VALIDATE_ARTIFACT_REFS.value: set(),
            RepairAction.FFMPEG_SYNTHETIC_TEST.value: {"synthetic_render_succeeds"},
        }[action]
        if not isinstance(simulation, dict) or set(simulation) - allowed:
            raise IssueError("Simulation contains unsupported inputs or commands")
        if any(not isinstance(value, bool) for value in simulation.values()):
            raise IssueError("Simulation flags must be booleans")
        defaults = {
            RepairAction.RETRY_SIMULATED_TASK.value: {"retry_succeeds": True},
            RepairAction.RECOVER_SIMULATED_TASK.value: {"recovery_succeeds": True},
            RepairAction.VALIDATE_ARTIFACT_REFS.value: {},
            RepairAction.FFMPEG_SYNTHETIC_TEST.value: {"synthetic_render_succeeds": True},
        }
        return defaults[action] | simulation

    def register_artifact(self, artifact_ref: str, *, available: bool = True) -> None:
        if not isinstance(artifact_ref, str) or not REFERENCE_PATTERN.fullmatch(artifact_ref):
            raise IssueError("Artifact reference must be a local symbolic identifier")
        if not isinstance(available, bool):
            raise IssueError("Artifact availability must be a boolean")
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO studio_artifact_catalog (artifact_ref, available) VALUES (?, ?)
                ON CONFLICT(artifact_ref) DO UPDATE SET available = excluded.available
                """,
                (artifact_ref, int(available)),
            )

    def get_issue(self, issue_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM studio_issues WHERE issue_id = ?", (issue_id,)
            ).fetchone()
            if row is None:
                raise IssueNotFound("Issue not found")
            return self._decode_issue(row)

    @staticmethod
    def _decode_issue(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["evidence_refs"] = json.loads(result.pop("evidence_refs_json"))
        result["simulation"] = json.loads(result.pop("simulation_json"))
        return result

    def list_issues(
        self, *, severity: str | None = None, department: str | None = None
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[str] = []
        if severity:
            clauses.append("severity = ?")
            params.append(_enum_value(severity, IssueSeverity, "issue severity"))
        if department:
            clauses.append("department = ?")
            params.append(_required_text(department, "Department filter", 100))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            rows = self._connection.execute(
                f"SELECT * FROM studio_issues{where} ORDER BY created_at DESC, issue_id",
                params,
            ).fetchall()
            return [self._decode_issue(row) for row in rows]

    def issue_detail(self, issue_id: str) -> dict[str, Any]:
        issue = self.get_issue(issue_id)
        with self._lock:
            attempts = self._connection.execute(
                """
                SELECT a.*, d.authorization_id, d.director_id, d.instructions,
                    d.authorized_at, d.expires_at, d.consumed_at
                FROM studio_repair_attempts a
                LEFT JOIN studio_director_authorizations d ON d.attempt_id = a.attempt_id
                WHERE a.issue_id = ? ORDER BY a.attempt_no
                """,
                (issue_id,),
            ).fetchall()
            issue["repair_history"] = [
                {
                    **dict(row),
                    "repair_result": json.loads(row["repair_result_json"]),
                    "observer_review": json.loads(row["observer_review_json"]),
                }
                for row in attempts
            ]
            issue["audit_history"] = list(self.audit_events(issue_id))
            return issue

    def authorize_repair(
        self,
        issue_id: str,
        *,
        director_id: str,
        instructions: str = "",
        now: datetime | None = None,
    ) -> dict[str, Any]:
        if not isinstance(director_id, str) or director_id not in self.director_ids:
            raise IssueError("Only a configured local Director may authorize a repair")
        if not isinstance(instructions, str) or len(instructions) > 1_000:
            raise IssueError("Director instructions must be at most 1,000 characters")
        clean_instructions = sanitize_diagnostic_logs(instructions.strip())
        instant = now or datetime.now(timezone.utc)
        authorized_at = _timestamp(instant)
        expires_at = _timestamp(instant + self.authorization_ttl)
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                issue_row = self._connection.execute(
                    "SELECT * FROM studio_issues WHERE issue_id = ?", (issue_id,)
                ).fetchone()
                if issue_row is None:
                    raise IssueNotFound("Issue not found")
                issue = self._decode_issue(issue_row)
                if issue["status"] not in {
                    IssueStatus.PENDING.value,
                    IssueStatus.NEEDS_APPROVAL.value,
                    IssueStatus.FAILED.value,
                }:
                    raise IssueError("Issue is not eligible for a Director repair command")
                self._expire_outstanding(issue_id, instant)
                outstanding = self._connection.execute(
                    """
                    SELECT 1 FROM studio_director_authorizations
                    WHERE issue_id = ? AND consumed_at IS NULL AND expires_at > ?
                    LIMIT 1
                    """,
                    (issue_id, authorized_at),
                ).fetchone()
                if outstanding:
                    raise IssueError("A repair command is already pending for this issue")
                executions = self._connection.execute(
                    """
                    SELECT COUNT(*) FROM studio_repair_attempts
                    WHERE issue_id = ? AND status IN ('running', 'resolved', 'failed', 'blocked')
                    """,
                    (issue_id,),
                ).fetchone()[0]
                if executions >= issue["max_attempts"]:
                    raise IssueError("Repair attempt limit reached")
                latest = self._connection.execute(
                    "SELECT COALESCE(MAX(attempt_no), 0) FROM studio_repair_attempts WHERE issue_id = ?",
                    (issue_id,),
                ).fetchone()[0]
                authorization_id = uuid.uuid4().hex
                attempt_id = uuid.uuid4().hex
                attempt_no = latest + 1
                self._connection.execute(
                    """
                    INSERT INTO studio_repair_attempts
                        (attempt_id, issue_id, attempt_no, status, started_at)
                    VALUES (?, ?, ?, 'authorized', ?)
                    """,
                    (attempt_id, issue_id, attempt_no, authorized_at),
                )
                self._connection.execute(
                    """
                    INSERT INTO studio_director_authorizations (
                        authorization_id, issue_id, attempt_id, director_id,
                        instructions, authorized_at, expires_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        authorization_id,
                        issue_id,
                        attempt_id,
                        director_id,
                        clean_instructions,
                        authorized_at,
                        expires_at,
                    ),
                )
                self._connection.execute(
                    """
                    UPDATE studio_issues
                    SET director_instructions = ?, updated_at = ? WHERE issue_id = ?
                    """,
                    (clean_instructions, authorized_at, issue_id),
                )
                self._audit(
                    "director_repair_authorized",
                    issue_id,
                    director_id,
                    {
                        "authorization_id": authorization_id,
                        "attempt_id": attempt_id,
                        "attempt_no": attempt_no,
                        "expires_at": expires_at,
                    },
                    authorized_at,
                )
                self._connection.commit()
                return {
                    "authorization_id": authorization_id,
                    "issue_id": issue_id,
                    "attempt_no": attempt_no,
                    "director_id": director_id,
                    "authorized_at": authorized_at,
                    "expires_at": expires_at,
                }
            except Exception:
                self._connection.rollback()
                raise

    def _expire_outstanding(self, issue_id: str, now: datetime) -> None:
        now_text = _timestamp(now)
        expired = self._connection.execute(
            """
            SELECT authorization_id, attempt_id FROM studio_director_authorizations
            WHERE issue_id = ? AND consumed_at IS NULL AND expires_at <= ?
            """,
            (issue_id, now_text),
        ).fetchall()
        for authorization in expired:
            self._connection.execute(
                """
                UPDATE studio_director_authorizations SET consumed_at = ?
                WHERE authorization_id = ? AND consumed_at IS NULL
                """,
                (now_text, authorization["authorization_id"]),
            )
            self._connection.execute(
                "UPDATE studio_repair_attempts SET status = 'expired', completed_at = ? WHERE attempt_id = ?",
                (now_text, authorization["attempt_id"]),
            )
            self._audit(
                "director_repair_authorization_expired",
                issue_id,
                "issue-engine",
                {"authorization_id": authorization["authorization_id"]},
                now_text,
            )

    def execute_authorized(
        self, authorization_id: str, *, now: datetime | None = None
    ) -> RepairReport:
        instant = now or datetime.now(timezone.utc)
        executed_at = _timestamp(instant)
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                authorization = self._connection.execute(
                    """
                    SELECT d.*, a.attempt_no, a.attempt_id, i.*
                    FROM studio_director_authorizations d
                    JOIN studio_repair_attempts a ON a.attempt_id = d.attempt_id
                    JOIN studio_issues i ON i.issue_id = d.issue_id
                    WHERE d.authorization_id = ?
                    """,
                    (authorization_id,),
                ).fetchone()
                if authorization is None:
                    raise IssueNotFound("Repair authorization not found")
                if authorization["consumed_at"] is not None:
                    raise IssueError("Repair authorization has already been consumed")
                if authorization["expires_at"] <= executed_at:
                    self._expire_outstanding(authorization["issue_id"], instant)
                    self._connection.commit()
                    raise IssueError("Repair authorization has expired")
                issue = self._decode_issue(authorization)
                updated = self._connection.execute(
                    """
                    UPDATE studio_director_authorizations SET consumed_at = ?
                    WHERE authorization_id = ? AND consumed_at IS NULL AND expires_at > ?
                    """,
                    (executed_at, authorization_id, executed_at),
                ).rowcount
                if updated != 1:
                    raise IssueError("Repair authorization is invalid or already consumed")
                self._connection.execute(
                    "UPDATE studio_repair_attempts SET status = 'running' WHERE attempt_id = ?",
                    (authorization["attempt_id"],),
                )
                self._connection.execute(
                    """
                    UPDATE studio_issues SET status = ?, updated_at = ? WHERE issue_id = ?
                    """,
                    (IssueStatus.IN_PROGRESS.value, executed_at, issue["issue_id"]),
                )
                self._audit(
                    "director_repair_started",
                    issue["issue_id"],
                    authorization["director_id"],
                    {
                        "authorization_id": authorization_id,
                        "attempt_no": authorization["attempt_no"],
                    },
                    executed_at,
                )
                self._connection.commit()
            except Exception:
                if self._connection.in_transaction:
                    self._connection.rollback()
                raise

            result = self._run_repair(issue, authorization["attempt_no"])
            agrees, finding = self.observer.review(issue, result)
            completed_at = _timestamp(datetime.now(timezone.utc))

            if not agrees:
                final_status = IssueStatus.BLOCKED
            elif result.get("success") is True:
                final_status = IssueStatus.RESOLVED
            else:
                final_status = IssueStatus.FAILED
            observer_review = {
                "observer_id": self.observer.observer_id,
                "agrees": agrees,
                "finding": finding,
                "reviewed_at": completed_at,
            }
            with self._connection:
                self._connection.execute(
                    """
                    UPDATE studio_repair_attempts
                    SET status = ?, repair_result_json = ?, observer_review_json = ?, completed_at = ?
                    WHERE attempt_id = ?
                    """,
                    (
                        final_status.value,
                        _json(result),
                        _json(observer_review),
                        completed_at,
                        authorization["attempt_id"],
                    ),
                )
                self._connection.execute(
                    "UPDATE studio_issues SET status = ?, updated_at = ? WHERE issue_id = ?",
                    (final_status.value, completed_at, issue["issue_id"]),
                )
                self._audit(
                    "repair_reported",
                    issue["issue_id"],
                    self.observer.observer_id,
                    {
                        "authorization_id": authorization_id,
                        "attempt_no": authorization["attempt_no"],
                        "status": final_status.value,
                        "observer_review": observer_review,
                    },
                    completed_at,
                )
            return RepairReport(
                issue_id=issue["issue_id"],
                authorization_id=authorization_id,
                attempt_no=authorization["attempt_no"],
                status=final_status,
                repair_result=result,
                observer_review=observer_review,
                started_at=executed_at,
                completed_at=completed_at,
            )

    def fix_issue(
        self,
        issue_id: str,
        *,
        director_id: str,
        instructions: str = "",
        now: datetime | None = None,
    ) -> RepairReport:
        authorization = self.authorize_repair(
            issue_id,
            director_id=director_id,
            instructions=instructions,
            now=now,
        )
        return self.execute_authorized(authorization["authorization_id"], now=now)

    def _run_repair(self, issue: dict[str, Any], attempt_no: int) -> dict[str, Any]:
        action = issue["suggested_repair_action"]
        simulation = issue["simulation"]
        if action == RepairAction.RETRY_SIMULATED_TASK.value:
            succeeded = simulation["retry_succeeds"]
            return {
                "success": succeeded,
                "expected_success": succeeded,
                "task_status": "queued" if succeeded else "failed",
                "attempt": attempt_no,
                "operation": "controlled_simulated_retry",
            }
        if action == RepairAction.RECOVER_SIMULATED_TASK.value:
            succeeded = simulation["recovery_succeeds"]
            return {
                "success": succeeded,
                "expected_success": succeeded,
                "task_status": "recovered" if succeeded else "stuck",
                "operation": "safe_simulated_task_recovery",
            }
        if action == RepairAction.VALIDATE_ARTIFACT_REFS.value:
            refs = issue["evidence_refs"]
            invalid = [ref for ref in refs if not REFERENCE_PATTERN.fullmatch(ref)]
            local_refs = [ref for ref in refs if ref not in invalid]
            if local_refs:
                placeholders = ",".join("?" for _ in local_refs)
                rows = self._connection.execute(
                    f"""
                    SELECT artifact_ref FROM studio_artifact_catalog
                    WHERE available = 1 AND artifact_ref IN ({placeholders})
                    """,
                    local_refs,
                ).fetchall()
                available = {row["artifact_ref"] for row in rows}
            else:
                available = set()
            missing = [ref for ref in local_refs if ref not in available]
            if not refs:
                missing.append("<no references supplied>")
            succeeded = not invalid and not missing
            return {
                "success": succeeded,
                "expected_success": succeeded,
                "valid_refs": sorted(available),
                "invalid_refs": invalid,
                "missing_refs": missing,
                "operation": "local_artifact_reference_validation",
            }
        if action == RepairAction.FFMPEG_SYNTHETIC_TEST.value:
            classification = classify_ffmpeg_error(issue["diagnostic_logs"])
            succeeded = simulation["synthetic_render_succeeds"]
            return {
                "success": succeeded,
                "expected_success": succeeded,
                "ffmpeg_classification": classification,
                "synthetic_render_passed": succeeded,
                "operation": "synthetic_render_check_no_ffmpeg_invoked",
            }
        raise IssueError("Repair action is not in the deterministic allowlist")

    def audit_events(self, issue_id: str | None = None) -> tuple[dict[str, Any], ...]:
        with self._lock:
            if issue_id is None:
                rows = self._connection.execute(
                    "SELECT * FROM studio_issue_audit_events ORDER BY sequence"
                ).fetchall()
            else:
                rows = self._connection.execute(
                    "SELECT * FROM studio_issue_audit_events WHERE issue_id = ? ORDER BY sequence",
                    (issue_id,),
                ).fetchall()
            return tuple(dict(row) for row in rows)

    def verify_audit_chain(self) -> bool:
        previous_hash = "0" * 64
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM studio_issue_audit_events ORDER BY sequence"
            ).fetchall()
        for row in rows:
            if row["previous_hash"] != previous_hash:
                return False
            material = _json(
                {
                    "event_type": row["event_type"],
                    "issue_id": row["issue_id"],
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

    def pipeline_counts(self) -> dict[str, int]:
        counts = {"active": 0, "pending": 0, "blocked": 0, "ready": 0}
        with self._lock:
            exists = self._connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'tasks'"
            ).fetchone()
            if exists is None:
                return counts
            for row in self._connection.execute(
                "SELECT status, COUNT(*) AS count FROM tasks GROUP BY status"
            ):
                if row["status"] == "in_progress":
                    counts["active"] = row["count"]
                elif row["status"] == "queued":
                    counts["pending"] = row["count"]
                elif row["status"] == "blocked":
                    counts["blocked"] = row["count"]
                elif row["status"] == "ready_for_review":
                    counts["ready"] = row["count"]
        return counts

    def dashboard_snapshot(
        self, *, severity: str | None = None, department: str | None = None
    ) -> dict[str, Any]:
        issues = self.list_issues(severity=severity, department=department)
        counts = {status.value: 0 for status in IssueStatus}
        for issue in self.list_issues():
            counts[issue["status"]] += 1
        return {
            "mode": "Demo Mode — local-only prototype; repairs use synthetic deterministic data",
            "pipeline": self.pipeline_counts(),
            "issue_counts": counts,
            "issues": issues,
        }
