"""Typed task, assignment, review, and observer records."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from enum import StrEnum
import json
import re


ASSIGNMENT_WINDOW = timedelta(days=7)
MAX_ASSIGNMENT_HORIZON = timedelta(days=14)
MAX_EXTENSIONS = 1
ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
HASH_PATTERN = re.compile(r"^[a-f0-9]{64}$")


class StudioError(ValueError):
    """Raised when a local workflow operation violates policy."""


class TaskStatus(StrEnum):
    QUEUED = "queued"
    IN_PROGRESS = "in_progress"
    FAILED = "failed"
    BLOCKED = "blocked"
    READY_FOR_REVIEW = "ready_for_review"
    APPROVED = "approved"
    COMPLETED = "completed"


class ReviewGate(StrEnum):
    SOURCE_FACTUAL = "source_factual"
    EDITORIAL_CREATIVE = "editorial_creative"
    TECHNICAL_VISUAL_LICENSING = "technical_visual_licensing"
    INDEPENDENT_FINAL = "independent_final"


class ReviewOutcome(StrEnum):
    PASS = "pass"
    REVISE = "revise"
    BLOCK = "block"


class ObserverAssessment(StrEnum):
    CLEAR = "clear"
    CONCERN = "concern"
    BLOCK = "block"


@dataclass(frozen=True)
class AssignmentExtension:
    approved_by: str
    approved_at: datetime
    previous_expiry: datetime
    new_expiry: datetime


@dataclass(frozen=True)
class AssignmentManifest:
    assignment_id: str
    title: str
    director_id: str
    starts_at: datetime
    expires_at: datetime
    editorial_policy_id: str
    authorized_spend_minor: int = 0
    extensions: tuple[AssignmentExtension, ...] = ()
    schema_version: int = 1

    def extend(
        self,
        *,
        approved_by: str,
        approved_at: datetime,
        new_expiry: datetime,
        director_ids: frozenset[str],
        now: datetime,
    ) -> "AssignmentManifest":
        if approved_by not in director_ids:
            raise StudioError("Only an authorized Director may extend an assignment")
        validate_assignment(self, now=now, director_ids=director_ids, check_expiry=True)
        if len(self.extensions) >= MAX_EXTENSIONS:
            raise StudioError("Assignment extension limit reached")
        if approved_at.tzinfo is None or approved_at.utcoffset() is None:
            raise StudioError("Extension approval time must include a timezone")
        if approved_at > now:
            raise StudioError("Extension approval cannot be in the future")
        if new_expiry.tzinfo is None or new_expiry.utcoffset() is None:
            raise StudioError("Extension expiry must include a timezone")
        if new_expiry <= self.expires_at:
            raise StudioError("Extension must move the expiry forward")
        if new_expiry - self.expires_at > ASSIGNMENT_WINDOW:
            raise StudioError("A single extension cannot exceed seven days")
        if new_expiry - self.starts_at > MAX_ASSIGNMENT_HORIZON:
            raise StudioError("Assignment cannot exceed the 14-day total horizon")
        if new_expiry - now > MAX_ASSIGNMENT_HORIZON:
            raise StudioError("Assignment exceeds the 14-day forward horizon")
        extension = AssignmentExtension(
            approved_by=approved_by,
            approved_at=approved_at,
            previous_expiry=self.expires_at,
            new_expiry=new_expiry,
        )
        return replace(
            self,
            expires_at=new_expiry,
            extensions=self.extensions + (extension,),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "assignment_id": self.assignment_id,
            "title": self.title,
            "director_id": self.director_id,
            "starts_at": self.starts_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "editorial_policy_id": self.editorial_policy_id,
            "authorized_spend_minor": self.authorized_spend_minor,
            "extensions": [
                {
                    "approved_by": extension.approved_by,
                    "approved_at": extension.approved_at.isoformat(),
                    "previous_expiry": extension.previous_expiry.isoformat(),
                    "new_expiry": extension.new_expiry.isoformat(),
                }
                for extension in self.extensions
            ],
        }

    @classmethod
    def from_json(cls, payload: str) -> "AssignmentManifest":
        try:
            value = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise StudioError("Assignment manifest is not valid JSON") from exc
        return cls.from_dict(value)

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "AssignmentManifest":
        if not isinstance(value, dict):
            raise StudioError("Assignment manifest must be an object")
        required = {
            "assignment_id",
            "title",
            "director_id",
            "starts_at",
            "expires_at",
            "editorial_policy_id",
        }
        allowed = required | {"authorized_spend_minor", "schema_version", "extensions"}
        if set(value) - allowed or required - set(value):
            raise StudioError("Assignment manifest has missing or unsupported fields")
        schema_version = value.get("schema_version", 1)
        if isinstance(schema_version, bool) or not isinstance(schema_version, int) or schema_version != 1:
            raise StudioError("Unsupported assignment manifest schema version")
        try:
            for field_name in ("assignment_id", "title", "director_id", "editorial_policy_id"):
                if not isinstance(value[field_name], str):
                    raise StudioError(f"{field_name} must be a string")
            spend = value.get("authorized_spend_minor", 0)
            if isinstance(spend, bool) or not isinstance(spend, int):
                raise StudioError("authorized_spend_minor must be an integer")
            extensions_value = value.get("extensions", [])
            if not isinstance(extensions_value, list):
                raise StudioError("extensions must be a list")
            extensions: list[AssignmentExtension] = []
            for extension in extensions_value:
                if not isinstance(extension, dict) or set(extension) != {
                    "approved_by",
                    "approved_at",
                    "previous_expiry",
                    "new_expiry",
                }:
                    raise StudioError("Invalid assignment extension record")
                if not isinstance(extension["approved_by"], str):
                    raise StudioError("Extension approver must be a string")
                extensions.append(
                    AssignmentExtension(
                        approved_by=extension["approved_by"],
                        approved_at=parse_datetime(extension["approved_at"]),
                        previous_expiry=parse_datetime(extension["previous_expiry"]),
                        new_expiry=parse_datetime(extension["new_expiry"]),
                    )
                )
            manifest = cls(
                assignment_id=value["assignment_id"],
                title=value["title"],
                director_id=value["director_id"],
                starts_at=parse_datetime(value["starts_at"]),
                expires_at=parse_datetime(value["expires_at"]),
                editorial_policy_id=value["editorial_policy_id"],
                authorized_spend_minor=spend,
                extensions=tuple(extensions),
                schema_version=1,
            )
        except (TypeError, ValueError) as exc:
            raise StudioError(f"Invalid assignment manifest: {exc}") from exc
        return manifest


@dataclass(frozen=True)
class TaskRequest:
    task_id: str
    assignment_id: str
    title: str
    department_id: str
    role_id: str
    dependencies: tuple[str, ...] = ()
    required_evidence: tuple[str, ...] = ("source_pack", "artifact")
    max_attempts: int = 3


@dataclass(frozen=True)
class ObserverReport:
    observer_id: str
    assessment: ObserverAssessment
    evidence_refs: tuple[str, ...]
    finding: str


def parse_datetime(value: object) -> datetime:
    if not isinstance(value, str):
        raise StudioError("Timestamps must be ISO-8601 strings")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise StudioError("Timestamps must include a timezone")
    return parsed.astimezone(timezone.utc)


def validate_assignment(
    manifest: AssignmentManifest,
    *,
    now: datetime,
    director_ids: frozenset[str],
    check_expiry: bool,
) -> None:
    if manifest.schema_version != 1:
        raise StudioError("Unsupported assignment manifest schema version")
    for field_name, value in (
        ("assignment_id", manifest.assignment_id),
        ("director_id", manifest.director_id),
        ("editorial_policy_id", manifest.editorial_policy_id),
    ):
        if not ID_PATTERN.fullmatch(value):
            raise StudioError(f"Invalid {field_name}")
    if not isinstance(manifest.title, str) or not manifest.title.strip():
        raise StudioError("Assignment title cannot be empty")
    if manifest.director_id not in director_ids:
        raise StudioError("Assignment was not issued by an authorized Director")
    for label, value in (("start", manifest.starts_at), ("expiry", manifest.expires_at), ("now", now)):
        if value.tzinfo is None or value.utcoffset() is None:
            raise StudioError(f"Assignment {label} time must include a timezone")
    if manifest.authorized_spend_minor != 0:
        raise StudioError("Paid spending is disabled in the local prototype")
    if manifest.expires_at <= manifest.starts_at:
        raise StudioError("Assignment expiry must be after its start")
    if not manifest.extensions and manifest.expires_at - manifest.starts_at > ASSIGNMENT_WINDOW:
        raise StudioError("Initial assignment window cannot exceed seven days")
    if len(manifest.extensions) > MAX_EXTENSIONS:
        raise StudioError("Assignment may only be extended once")
    if manifest.expires_at - manifest.starts_at > MAX_ASSIGNMENT_HORIZON:
        raise StudioError("Assignment cannot exceed the 14-day total horizon")
    if manifest.expires_at - now > MAX_ASSIGNMENT_HORIZON:
        raise StudioError("Assignment exceeds the 14-day forward horizon")
    if manifest.extensions:
        extension = manifest.extensions[0]
        if extension.approved_by not in director_ids:
            raise StudioError("Extension was not approved by an authorized Director")
        if extension.previous_expiry <= manifest.starts_at:
            raise StudioError("Extension does not continue the original assignment")
        if extension.previous_expiry - manifest.starts_at > ASSIGNMENT_WINDOW:
            raise StudioError("Initial assignment window cannot exceed seven days")
        if extension.new_expiry != manifest.expires_at:
            raise StudioError("Extension expiry does not match assignment expiry")
        if extension.new_expiry <= extension.previous_expiry:
            raise StudioError("Extension must move the expiry forward")
        if extension.new_expiry - extension.previous_expiry > ASSIGNMENT_WINDOW:
            raise StudioError("Extension cannot exceed seven days")
        if extension.approved_at.tzinfo is None or extension.approved_at.utcoffset() is None:
            raise StudioError("Extension approval time must include a timezone")
        if extension.approved_at > now:
            raise StudioError("Extension approval cannot be in the future")
    elif manifest.expires_at - manifest.starts_at > ASSIGNMENT_WINDOW:
        raise StudioError("Assignment exceeds seven days without an explicit extension")
    if check_expiry and now >= manifest.expires_at:
        raise StudioError("Assignment instruction has expired")


def normalize_hash(value: str) -> str:
    normalized = value.lower()
    if not HASH_PATTERN.fullmatch(normalized):
        raise StudioError("Artifact hash must be a 64-character SHA-256 hex digest")
    return normalized
