"""Persistent local job queue that reuses the Studio allocation control plane."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import base64
import binascii
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import tempfile
import threading
from typing import Any, Iterator
import uuid

from .allocation import AllocationError, TaskAllocationEngine
from .models import parse_datetime
from .worker_contracts import (
    Accelerator,
    ArtifactRef,
    AuditEvent,
    CapabilityRequirements,
    ContractError,
    DirectorAuthorization,
    JobLease,
    JobManifest,
    JobRecord,
    JobResult,
    JobState,
    OutputDeclaration,
    ResourceRequirements,
    StorageBackend,
    WorkerRegistration,
    WorkerState,
    WorkloadKind,
    accept_result,
    authorize_execution,
    cancel_job as contract_cancel_job,
    create_job,
    ensure_idempotent_enqueue,
    expire_lease,
    fail_job,
    heartbeat_job,
    lease_job,
    manifest_digest,
    recover_interrupted_job,
    retry_failed_job,
    start_job,
    validate_manifest,
    validate_worker_registration,
)


class WorkerQueueError(ValueError):
    """Raised when a local worker-queue operation violates policy."""


class WorkerJobQueue:
    """Persist offline worker contracts alongside the existing Studio ledger."""

    def __init__(
        self,
        allocation: TaskAllocationEngine,
        director_ids: set[str] | frozenset[str],
        *,
        now: datetime | None = None,
    ) -> None:
        self.allocation = allocation
        self.database = Path(allocation.database)
        self.director_ids = frozenset(director_ids)
        if not self.director_ids:
            raise WorkerQueueError("At least one local Director ID must be configured")
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(
            str(self.database), timeout=10, check_same_thread=False
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._create_schema()
        self._recover_artifact_storage(now=now or datetime.now(timezone.utc))
        self.recover_expired_leases(now=now)

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                yield
                self._connection.commit()
            except BaseException:
                self._connection.rollback()
                raise

    def _create_schema(self) -> None:
        with self._connection:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS studio_worker_registrations (
                    worker_id TEXT PRIMARY KEY,
                    state TEXT NOT NULL CHECK (state IN (
                        'registered', 'available', 'gpu_unavailable', 'disconnected'
                    )),
                    workloads_json TEXT NOT NULL,
                    registered_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    max_concurrent_jobs INTEGER NOT NULL CHECK (max_concurrent_jobs = 1),
                    vram_mb INTEGER NOT NULL CHECK (vram_mb >= 0),
                    operations_json TEXT NOT NULL,
                    language_tags_json TEXT NOT NULL,
                    model_ids_json TEXT NOT NULL,
                    registration_method TEXT NOT NULL CHECK (registration_method = 'manual'),
                    is_simulation INTEGER NOT NULL DEFAULT 1 CHECK (is_simulation = 1),
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS studio_worker_jobs (
                    job_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    schema_version INTEGER NOT NULL CHECK (schema_version = 1),
                    project_id TEXT NOT NULL REFERENCES studio_projects(project_id),
                    task_id TEXT NOT NULL REFERENCES studio_allocation_tasks(task_id),
                    manifest_json TEXT NOT NULL,
                    state TEXT NOT NULL CHECK (state IN (
                        'queued', 'leased', 'running', 'succeeded', 'failed',
                        'interrupted', 'cancelled'
                    )),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
                    max_attempts INTEGER NOT NULL CHECK (max_attempts BETWEEN 1 AND 5),
                    authorization_json TEXT,
                    lease_json TEXT,
                    result_json TEXT,
                    result_id TEXT UNIQUE,
                    last_error TEXT,
                    output_verification TEXT NOT NULL DEFAULT 'none'
                        CHECK (output_verification IN ('none', 'unverified_metadata')),
                    UNIQUE(job_id, task_id)
                );
                CREATE TABLE IF NOT EXISTS studio_worker_job_outputs (
                    job_id TEXT NOT NULL REFERENCES studio_worker_jobs(job_id),
                    artifact_id TEXT NOT NULL,
                    artifact_json TEXT NOT NULL,
                    verification_status TEXT NOT NULL
                        CHECK (verification_status = 'unverified_metadata'),
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(job_id, artifact_id)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS studio_worker_jobs_job_task_project
                    ON studio_worker_jobs(job_id, task_id, project_id);
                CREATE TABLE IF NOT EXISTS studio_worker_output_checks (
                    check_id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    artifact_id TEXT NOT NULL,
                    attempt INTEGER NOT NULL CHECK (attempt BETWEEN 0 AND 5),
                    status TEXT NOT NULL CHECK (status IN (
                        'awaiting_verification', 'verified', 'failed'
                    )),
                    expected_sha256 TEXT,
                    actual_sha256 TEXT,
                    expected_size_bytes INTEGER,
                    actual_size_bytes INTEGER,
                    error_code TEXT,
                    checked_at TEXT NOT NULL,
                    is_synthetic INTEGER NOT NULL DEFAULT 1 CHECK (is_synthetic = 1),
                    FOREIGN KEY (job_id, task_id, project_id)
                        REFERENCES studio_worker_jobs(job_id, task_id, project_id)
                );
                CREATE TABLE IF NOT EXISTS studio_worker_verified_outputs (
                    job_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    artifact_id TEXT NOT NULL,
                    storage_name TEXT NOT NULL UNIQUE,
                    attempt INTEGER NOT NULL CHECK (attempt BETWEEN 1 AND 5),
                    sha256 TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL CHECK (size_bytes > 0),
                    media_type TEXT NOT NULL,
                    verified_at TEXT NOT NULL,
                    is_synthetic INTEGER NOT NULL DEFAULT 1 CHECK (is_synthetic = 1),
                    PRIMARY KEY(job_id, artifact_id, attempt),
                    FOREIGN KEY (job_id, task_id, project_id)
                        REFERENCES studio_worker_jobs(job_id, task_id, project_id)
                );
                CREATE TRIGGER IF NOT EXISTS studio_worker_output_checks_no_update
                BEFORE UPDATE ON studio_worker_output_checks
                BEGIN
                    SELECT RAISE(ABORT, 'worker output checks are immutable');
                END;
                CREATE TRIGGER IF NOT EXISTS studio_worker_output_checks_no_delete
                BEFORE DELETE ON studio_worker_output_checks
                BEGIN
                    SELECT RAISE(ABORT, 'worker output checks are immutable');
                END;
                CREATE TRIGGER IF NOT EXISTS studio_worker_verified_outputs_no_update
                BEFORE UPDATE ON studio_worker_verified_outputs
                BEGIN
                    SELECT RAISE(ABORT, 'verified worker outputs are immutable');
                END;
                CREATE TRIGGER IF NOT EXISTS studio_worker_verified_outputs_no_delete
                BEFORE DELETE ON studio_worker_verified_outputs
                BEGIN
                    SELECT RAISE(ABORT, 'verified worker outputs are immutable');
                END;
                CREATE TABLE IF NOT EXISTS studio_worker_job_events (
                    job_id TEXT NOT NULL REFERENCES studio_worker_jobs(job_id),
                    sequence INTEGER NOT NULL,
                    event_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    details_json TEXT NOT NULL,
                    previous_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL,
                    PRIMARY KEY(job_id, sequence),
                    UNIQUE(job_id, event_id),
                    UNIQUE(job_id, event_hash)
                );
                CREATE TRIGGER IF NOT EXISTS studio_worker_job_events_no_update
                BEFORE UPDATE ON studio_worker_job_events
                BEGIN
                    SELECT RAISE(ABORT, 'worker job events are immutable');
                END;
                CREATE TRIGGER IF NOT EXISTS studio_worker_job_events_no_delete
                BEFORE DELETE ON studio_worker_job_events
                BEGIN
                    SELECT RAISE(ABORT, 'worker job events are immutable');
                END;
                CREATE TRIGGER IF NOT EXISTS studio_worker_job_outputs_no_update
                BEFORE UPDATE ON studio_worker_job_outputs
                BEGIN
                    SELECT RAISE(ABORT, 'worker job output references are immutable');
                END;
                CREATE TRIGGER IF NOT EXISTS studio_worker_job_outputs_no_delete
                BEFORE DELETE ON studio_worker_job_outputs
                BEGIN
                    SELECT RAISE(ABORT, 'worker job output references are immutable');
                END;
                """
            )
            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS studio_worker_jobs_state_created
                ON studio_worker_jobs(state, created_at)
                """
            )
            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS studio_worker_jobs_task
                ON studio_worker_jobs(task_id, created_at)
                """
            )

    def _artifact_directory(self) -> Path:
        database_parent = self.database.parent.resolve()
        root = database_parent / f".{self.database.name}.worker-artifacts"
        try:
            root.mkdir(mode=0o700, exist_ok=True)
            root_stat = root.lstat()
        except OSError as exc:
            raise WorkerQueueError("Local worker artifact directory is unavailable") from exc
        if not stat.S_ISDIR(root_stat.st_mode) or stat.S_ISLNK(root_stat.st_mode):
            raise WorkerQueueError("Worker artifact directory must be a real directory")
        try:
            os.chmod(root, 0o700, follow_symlinks=False)
        except OSError as exc:
            raise WorkerQueueError("Worker artifact directory permissions could not be secured") from exc
        resolved = root.resolve(strict=True)
        if resolved.parent != database_parent:
            raise WorkerQueueError("Worker artifact directory escaped its database directory")
        return resolved

    def _append_integrity_event(
        self, record: JobRecord, *, event_type: str, actor_id: str,
        now: datetime, details: dict[str, Any],
    ) -> None:
        previous_hash = (
            record.audit_events[-1].event_hash if record.audit_events else "0" * 64
        )
        event_id = f"event-{len(record.audit_events) + 1}"
        details_json = self._json(details)
        created_at = self._timestamp(now)
        material = (
            f"{previous_hash}|{event_id}|{event_type}|{actor_id}|"
            f"{created_at}|{details_json}"
        )
        event_hash = hashlib.sha256(material.encode("utf-8")).hexdigest()
        self._connection.execute(
            """
            INSERT INTO studio_worker_job_events
                (job_id, sequence, event_id, event_type, actor_id, created_at,
                 details_json, previous_hash, event_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.manifest.job_id,
                len(record.audit_events) + 1,
                event_id,
                event_type,
                actor_id,
                created_at,
                details_json,
                previous_hash,
                event_hash,
            ),
        )

    def _record_output_check(
        self,
        record: JobRecord,
        *,
        artifact_id: str,
        attempt: int,
        status: str,
        now: datetime,
        expected_sha256: str | None = None,
        actual_sha256: str | None = None,
        expected_size_bytes: int | None = None,
        actual_size_bytes: int | None = None,
        error_code: str | None = None,
    ) -> None:
        check_attempt = 0 if status == "awaiting_verification" else attempt
        if type(check_attempt) is not int or not 0 <= check_attempt <= 5:
            raise WorkerQueueError("Worker output verification attempt is invalid")
        self._connection.execute(
            """
            INSERT INTO studio_worker_output_checks
                (check_id, job_id, task_id, project_id, artifact_id, attempt,
                 status, expected_sha256, actual_sha256, expected_size_bytes,
                 actual_size_bytes, error_code, checked_at, is_synthetic)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                f"check-{uuid.uuid4().hex}",
                record.manifest.job_id,
                record.manifest.task_id,
                record.manifest.project_id,
                artifact_id,
                check_attempt,
                status,
                expected_sha256,
                actual_sha256,
                expected_size_bytes,
                actual_size_bytes,
                error_code,
                self._timestamp(now),
            ),
        )

    @staticmethod
    def _output_error_code(message: str) -> str:
        normalized = message.lower()
        if "checksum" in normalized or "hash" in normalized:
            return "output_checksum_mismatch"
        if "empty" in normalized:
            return "output_empty"
        if "oversized" in normalized or "size" in normalized or "exceeds" in normalized:
            return "output_size_invalid"
        if "missing" in normalized:
            return "output_missing"
        if "duplicate" in normalized:
            return "output_duplicate"
        if "declared" in normalized or "ownership" in normalized or "association" in normalized:
            return "output_association_invalid"
        return "output_verification_failed"

    @staticmethod
    def _artifact_storage_name(job_id: str, artifact_id: str, attempt: int) -> str:
        if (
            not isinstance(job_id, str)
            or not isinstance(artifact_id, str)
            or type(attempt) is not int
            or not 1 <= attempt <= 5
        ):
            raise WorkerQueueError("Worker output ownership reference is invalid")
        return f"{job_id}.{artifact_id}.{attempt}.bin"

    def _write_verified_output(
        self,
        *,
        record: JobRecord,
        artifact_id: str,
        attempt: int,
        content: bytes,
        expected_sha256: str,
        expected_size_bytes: int,
        media_type: str,
    ) -> tuple[str, str, int]:
        declarations = {
            item.artifact_id: item for item in record.manifest.output_artifacts
        }
        declaration = declarations.get(artifact_id)
        if declaration is None:
            raise WorkerQueueError("Worker output is not declared by this job")
        if (
            declaration.backend is not StorageBackend.LOCAL
            or not isinstance(content, bytes)
            or not isinstance(expected_sha256, str)
            or not isinstance(expected_size_bytes, int)
            or isinstance(expected_size_bytes, bool)
        ):
            raise WorkerQueueError("Worker output payload or metadata has an invalid type")
        if (
            not hmac.compare_digest(declaration.media_type, media_type)
            or expected_size_bytes <= 0
            or expected_size_bytes > declaration.max_bytes
            or len(content) > declaration.max_bytes
        ):
            raise WorkerQueueError("Worker output is empty, oversized, or mismatches its declaration")
        if len(content) != expected_size_bytes:
            raise WorkerQueueError("Worker output size does not match its declared size")
        used_output_bytes = self._connection.execute(
            """
            SELECT COALESCE(SUM(size_bytes), 0)
            FROM studio_worker_verified_outputs WHERE job_id = ?
            """,
            (record.manifest.job_id,),
        ).fetchone()[0]
        input_bytes = sum(item.size_bytes for item in record.manifest.input_artifacts)
        if (
            used_output_bytes + len(content) + input_bytes
            > record.manifest.resources.max_storage_bytes
        ):
            raise WorkerQueueError("Worker output exceeds the remaining job storage limit")
        if not re.fullmatch(r"[a-f0-9]{64}", expected_sha256):
            raise WorkerQueueError("Worker output checksum must be a lowercase SHA-256 digest")

        actual_sha256 = hashlib.sha256(content).hexdigest()
        if not hmac.compare_digest(actual_sha256, expected_sha256):
            raise WorkerQueueError("Worker output checksum does not match its actual bytes")

        root = self._artifact_directory()
        storage_name = self._artifact_storage_name(
            record.manifest.job_id, artifact_id, attempt
        )
        final_path = root / storage_name
        descriptor, temporary_name = tempfile.mkstemp(
            prefix="worker-output-staging-", dir=root
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb", closefd=True) as artifact_file:
                artifact_file.write(content)
                artifact_file.flush()
                os.fsync(artifact_file.fileno())
            verification_descriptor = os.open(
                temporary_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            )
            try:
                file_stat = os.fstat(verification_descriptor)
                if not stat.S_ISREG(file_stat.st_mode):
                    raise WorkerQueueError("Stored worker output is not a regular file")
                if file_stat.st_size <= 0 or file_stat.st_size > declaration.max_bytes:
                    raise WorkerQueueError("Stored worker output size is outside the permitted range")
                digest = hashlib.sha256()
                actual_size = 0
                while True:
                    chunk = os.read(verification_descriptor, 65_536)
                    if not chunk:
                        break
                    digest.update(chunk)
                    actual_size += len(chunk)
                    if actual_size > declaration.max_bytes:
                        raise WorkerQueueError("Stored worker output exceeds its size limit")
            finally:
                os.close(verification_descriptor)
            verified_digest = digest.hexdigest()
            if (
                actual_size != expected_size_bytes
                or not hmac.compare_digest(verified_digest, expected_sha256)
            ):
                raise WorkerQueueError("Stored worker output failed byte-level verification")
            os.link(temporary_path, final_path, follow_symlinks=False)
            temporary_path.unlink()
            directory_descriptor = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
            return storage_name, verified_digest, actual_size
        except BaseException:
            temporary_path.unlink(missing_ok=True)
            raise

    def _read_verified_output(
        self, storage_name: str, *, expected_size_bytes: int, max_bytes: int
    ) -> tuple[str, int]:
        if (
            not isinstance(storage_name, str)
            or not re.fullmatch(
                r"job-[A-Za-z0-9._-]{1,60}\.output-[A-Za-z0-9._-]{1,60}\.[1-5]\.bin",
                storage_name,
            )
        ):
            raise WorkerQueueError("Persisted worker artifact path is invalid")
        root = self._artifact_directory()
        path = root / storage_name
        try:
            path_stat = path.lstat()
            if not stat.S_ISREG(path_stat.st_mode) or stat.S_ISLNK(path_stat.st_mode):
                raise WorkerQueueError("Persisted worker artifact is not a regular file")
            if path.resolve(strict=True).parent != root:
                raise WorkerQueueError("Persisted worker artifact escaped its storage directory")
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        except FileNotFoundError as exc:
            raise WorkerQueueError("Persisted worker artifact is missing") from exc
        except OSError as exc:
            raise WorkerQueueError("Persisted worker artifact is unavailable") from exc
        try:
            file_stat = os.fstat(descriptor)
            if (
                not stat.S_ISREG(file_stat.st_mode)
                or file_stat.st_size <= 0
                or file_stat.st_size > max_bytes
            ):
                raise WorkerQueueError("Persisted worker artifact size is invalid")
            digest = hashlib.sha256()
            total = 0
            while True:
                chunk = os.read(descriptor, min(65_536, max_bytes + 1 - total))
                if not chunk:
                    break
                digest.update(chunk)
                total += len(chunk)
                if total > max_bytes:
                    raise WorkerQueueError("Persisted worker artifact exceeds its size limit")
        finally:
            os.close(descriptor)
        if total != expected_size_bytes:
            raise WorkerQueueError("Persisted worker artifact size has changed")
        return digest.hexdigest(), total

    def _recover_artifact_storage(self, *, now: datetime) -> None:
        root = self._artifact_directory()
        with self._transaction():
            referenced = {
                row["storage_name"]: row
                for row in self._connection.execute(
                    """
                    SELECT output.*
                    FROM studio_worker_verified_outputs AS output
                    WHERE output.attempt = (
                        SELECT MAX(latest.attempt)
                        FROM studio_worker_verified_outputs AS latest
                        WHERE latest.job_id = output.job_id
                          AND latest.artifact_id = output.artifact_id
                    )
                    """
                )
            }
            for entry in os.scandir(root):
                if entry.name.startswith("worker-output-staging-"):
                    try:
                        if entry.is_file(follow_symlinks=False):
                            Path(entry.path).unlink(missing_ok=True)
                    except OSError as exc:
                        raise WorkerQueueError(
                            "Interrupted worker artifact could not be cleaned safely"
                        ) from exc
                    continue
                if (
                    re.fullmatch(
                        r"job-[A-Za-z0-9._-]{1,60}\.output-[A-Za-z0-9._-]{1,60}\.[1-5]\.bin",
                        entry.name,
                    )
                    and entry.name not in referenced
                    and entry.is_file(follow_symlinks=False)
                ):
                    try:
                        Path(entry.path).unlink(missing_ok=True)
                    except OSError as exc:
                        raise WorkerQueueError(
                            "Uncommitted worker artifact could not be cleaned safely"
                        ) from exc
            for storage_name, output in referenced.items():
                record = self._record(output["job_id"])
                declarations = {
                    item.artifact_id: item
                    for item in record.manifest.output_artifacts
                }
                declaration = declarations.get(output["artifact_id"])
                error_code = None
                actual_digest = None
                actual_size = None
                if (
                    declaration is None
                    or output["task_id"] != record.manifest.task_id
                    or output["project_id"] != record.manifest.project_id
                    or output["storage_name"] != self._artifact_storage_name(
                        record.manifest.job_id, output["artifact_id"], output["attempt"]
                    )
                    or output["media_type"] != declaration.media_type
                    or output["size_bytes"] > declaration.max_bytes
                ):
                    error_code = "persisted_output_association_invalid"
                else:
                    try:
                        actual_digest, actual_size = self._read_verified_output(
                            storage_name,
                            expected_size_bytes=output["size_bytes"],
                            max_bytes=declaration.max_bytes,
                        )
                        if not hmac.compare_digest(actual_digest, output["sha256"]):
                            error_code = "persisted_output_hash_mismatch"
                    except WorkerQueueError as exc:
                        error_code = (
                            "persisted_output_missing"
                            if "missing" in str(exc).lower()
                            else "persisted_output_invalid"
                        )
                if error_code is None:
                    continue
                latest_check = self._connection.execute(
                    """
                    SELECT status FROM studio_worker_output_checks
                    WHERE job_id = ? AND artifact_id = ?
                    ORDER BY rowid DESC LIMIT 1
                    """,
                    (output["job_id"], output["artifact_id"]),
                ).fetchone()
                if latest_check is not None and latest_check["status"] == "failed":
                    continue
                self._record_output_check(
                    record,
                    artifact_id=output["artifact_id"],
                    attempt=max(1, record.attempt_count),
                    status="failed",
                    now=now,
                    expected_sha256=output["sha256"],
                    actual_sha256=actual_digest,
                    expected_size_bytes=output["size_bytes"],
                    actual_size_bytes=actual_size,
                    error_code=error_code,
                )
                self._append_integrity_event(
                    record,
                    event_type="output_integrity_failed",
                    actor_id="local-integrity-check",
                    now=now,
                    details={
                        "artifact_id": output["artifact_id"],
                        "error_code": error_code,
                    },
                )
                if record.state is JobState.SUCCEEDED:
                    invalidated = JobRecord(
                        manifest=record.manifest,
                        state=JobState.FAILED,
                        created_at=record.created_at,
                        updated_at=now,
                        attempt_count=record.attempt_count,
                        authorization=record.authorization,
                        lease=None,
                        result=record.result,
                        last_error=error_code,
                        audit_events=record.audit_events,
                    )
                    self._save_transition(invalidated, len(record.audit_events))
    @staticmethod
    def _timestamp(value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat()

    @staticmethod
    def _json(value: object) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)

    @classmethod
    def _artifact_json(cls, artifact: ArtifactRef) -> dict[str, Any]:
        return {
            "artifact_id": artifact.artifact_id,
            "backend": artifact.backend.value,
            "key": artifact.key,
            "sha256": artifact.sha256,
            "size_bytes": artifact.size_bytes,
            "media_type": artifact.media_type,
        }

    @classmethod
    def _manifest_dict(cls, manifest: JobManifest) -> dict[str, Any]:
        return {
            "schema_version": manifest.schema_version,
            "job_id": manifest.job_id,
            "idempotency_key": manifest.idempotency_key,
            "project_id": manifest.project_id,
            "task_id": manifest.task_id,
            "workload": manifest.workload.value,
            "input_artifacts": [cls._artifact_json(item) for item in manifest.input_artifacts],
            "output_artifacts": [
                {
                    "artifact_id": item.artifact_id,
                    "backend": item.backend.value,
                    "key": item.key,
                    "media_type": item.media_type,
                    "max_bytes": item.max_bytes,
                }
                for item in manifest.output_artifacts
            ],
            "resources": {
                "accelerator": manifest.resources.accelerator.value,
                "cpu_cores": manifest.resources.cpu_cores,
                "memory_mb": manifest.resources.memory_mb,
                "min_vram_mb": manifest.resources.min_vram_mb,
                "max_runtime_seconds": manifest.resources.max_runtime_seconds,
                "max_output_bytes": manifest.resources.max_output_bytes,
                "max_storage_bytes": manifest.resources.max_storage_bytes,
                "max_concurrent_jobs": manifest.resources.max_concurrent_jobs,
            },
            "max_attempts": manifest.max_attempts,
            "created_at": cls._timestamp(manifest.created_at),
            "deadline_at": cls._timestamp(manifest.deadline_at),
            "capabilities": {
                "operations": list(manifest.capabilities.operations),
                "language_tags": list(manifest.capabilities.language_tags),
                "model_ids": list(manifest.capabilities.model_ids),
            },
        }

    @classmethod
    def _parse_manifest(cls, value: str) -> JobManifest:
        data = json.loads(value)
        return JobManifest(
            schema_version=data["schema_version"],
            job_id=data["job_id"],
            idempotency_key=data["idempotency_key"],
            project_id=data["project_id"],
            task_id=data["task_id"],
            workload=WorkloadKind(data["workload"]),
            input_artifacts=tuple(
                ArtifactRef(
                    artifact_id=item["artifact_id"],
                    backend=StorageBackend(item["backend"]),
                    key=item["key"],
                    sha256=item["sha256"],
                    size_bytes=item["size_bytes"],
                    media_type=item["media_type"],
                )
                for item in data["input_artifacts"]
            ),
            output_artifacts=tuple(
                OutputDeclaration(
                    artifact_id=item["artifact_id"],
                    backend=StorageBackend(item["backend"]),
                    key=item["key"],
                    media_type=item["media_type"],
                    max_bytes=item["max_bytes"],
                )
                for item in data["output_artifacts"]
            ),
            resources=ResourceRequirements(
                accelerator=Accelerator(data["resources"]["accelerator"]),
                cpu_cores=data["resources"]["cpu_cores"],
                memory_mb=data["resources"]["memory_mb"],
                min_vram_mb=data["resources"]["min_vram_mb"],
                max_runtime_seconds=data["resources"]["max_runtime_seconds"],
                max_output_bytes=data["resources"]["max_output_bytes"],
                max_storage_bytes=data["resources"]["max_storage_bytes"],
                max_concurrent_jobs=data["resources"]["max_concurrent_jobs"],
            ),
            max_attempts=data["max_attempts"],
            created_at=parse_datetime(data["created_at"]),
            deadline_at=parse_datetime(data["deadline_at"]),
            capabilities=CapabilityRequirements(
                operations=tuple(data["capabilities"]["operations"]),
                language_tags=tuple(data["capabilities"]["language_tags"]),
                model_ids=tuple(data["capabilities"]["model_ids"]),
            ),
        )

    @classmethod
    def _auth_dict(cls, value: DirectorAuthorization | None) -> dict[str, Any] | None:
        if value is None:
            return None
        return {
            "authorization_id": value.authorization_id,
            "director_id": value.director_id,
            "manifest_sha256": value.manifest_sha256,
            "authorized_at": cls._timestamp(value.authorized_at),
            "expires_at": cls._timestamp(value.expires_at),
        }

    @staticmethod
    def _parse_auth(value: str | None) -> DirectorAuthorization | None:
        if value is None:
            return None
        data = json.loads(value)
        return DirectorAuthorization(
            authorization_id=data["authorization_id"],
            director_id=data["director_id"],
            manifest_sha256=data["manifest_sha256"],
            authorized_at=parse_datetime(data["authorized_at"]),
            expires_at=parse_datetime(data["expires_at"]),
        )

    @classmethod
    def _lease_dict(cls, value: JobLease | None) -> dict[str, Any] | None:
        if value is None:
            return None
        return {
            "lease_id": value.lease_id,
            "worker_id": value.worker_id,
            "attempt": value.attempt,
            "acquired_at": cls._timestamp(value.acquired_at),
            "expires_at": cls._timestamp(value.expires_at),
            "heartbeat_at": cls._timestamp(value.heartbeat_at),
        }

    @staticmethod
    def _parse_lease(value: str | None) -> JobLease | None:
        if value is None:
            return None
        data = json.loads(value)
        return JobLease(
            lease_id=data["lease_id"],
            worker_id=data["worker_id"],
            attempt=data["attempt"],
            acquired_at=parse_datetime(data["acquired_at"]),
            expires_at=parse_datetime(data["expires_at"]),
            heartbeat_at=parse_datetime(data["heartbeat_at"]),
        )

    @classmethod
    def _result_dict(cls, value: JobResult | None) -> dict[str, Any] | None:
        if value is None:
            return None
        return {
            "result_id": value.result_id,
            "job_id": value.job_id,
            "lease_id": value.lease_id,
            "worker_id": value.worker_id,
            "attempt": value.attempt,
            "outputs": [cls._artifact_json(item) for item in value.outputs],
            "completed_at": cls._timestamp(value.completed_at),
        }

    @staticmethod
    def _parse_result(value: str | None) -> JobResult | None:
        if value is None:
            return None
        data = json.loads(value)
        return JobResult(
            result_id=data["result_id"],
            job_id=data["job_id"],
            lease_id=data["lease_id"],
            worker_id=data["worker_id"],
            attempt=data["attempt"],
            outputs=tuple(
                ArtifactRef(
                    artifact_id=item["artifact_id"],
                    backend=StorageBackend(item["backend"]),
                    key=item["key"],
                    sha256=item["sha256"],
                    size_bytes=item["size_bytes"],
                    media_type=item["media_type"],
                )
                for item in data["outputs"]
            ),
            completed_at=parse_datetime(data["completed_at"]),
        )

    def _record(self, job_id: str) -> JobRecord:
        row = self._connection.execute(
            "SELECT * FROM studio_worker_jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        if row is None:
            raise WorkerQueueError("Worker job not found")
        events = tuple(
            AuditEvent(
                event_id=item["event_id"],
                event_type=item["event_type"],
                actor_id=item["actor_id"],
                created_at=parse_datetime(item["created_at"]),
                details_json=item["details_json"],
                previous_hash=item["previous_hash"],
                event_hash=item["event_hash"],
            )
            for item in self._connection.execute(
                """
                SELECT * FROM studio_worker_job_events
                WHERE job_id = ? ORDER BY sequence
                """,
                (job_id,),
            )
        )
        return JobRecord(
            manifest=self._parse_manifest(row["manifest_json"]),
            state=JobState(row["state"]),
            created_at=parse_datetime(row["created_at"]),
            updated_at=parse_datetime(row["updated_at"]),
            attempt_count=row["attempt_count"],
            authorization=self._parse_auth(row["authorization_json"]),
            lease=self._parse_lease(row["lease_json"]),
            result=self._parse_result(row["result_json"]),
            last_error=row["last_error"],
            audit_events=events,
        )

    def _append_events(self, record: JobRecord, start: int) -> None:
        for sequence, event in enumerate(record.audit_events[start:], start=start + 1):
            self._connection.execute(
                """
                INSERT INTO studio_worker_job_events
                    (job_id, sequence, event_id, event_type, actor_id, created_at,
                     details_json, previous_hash, event_hash)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.manifest.job_id,
                    sequence,
                    event.event_id,
                    event.event_type,
                    event.actor_id,
                    self._timestamp(event.created_at),
                    event.details_json,
                    event.previous_hash,
                    event.event_hash,
                ),
            )

    def _save_transition(self, record: JobRecord, previous_event_count: int) -> None:
        self._connection.execute(
            """
            UPDATE studio_worker_jobs
            SET state = ?, updated_at = ?, attempt_count = ?,
                authorization_json = ?, lease_json = ?, result_json = ?,
                result_id = ?, last_error = ?
            WHERE job_id = ?
            """,
            (
                record.state.value,
                self._timestamp(record.updated_at),
                record.attempt_count,
                self._json(self._auth_dict(record.authorization))
                if record.authorization
                else None,
                self._json(self._lease_dict(record.lease)) if record.lease else None,
                self._json(self._result_dict(record.result)) if record.result else None,
                record.result.result_id if record.result else None,
                record.last_error,
                record.manifest.job_id,
            ),
        )
        self._append_events(record, previous_event_count)

    def _validate_task(
        self, project_id: str, task_id: str, *, require_active: bool = True
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        try:
            plan = self.allocation.get_plan_for_project(project_id)
        except AllocationError as exc:
            raise WorkerQueueError(str(exc)) from exc
        if plan is None or plan["project_id"] != project_id:
            raise WorkerQueueError("Project has no persisted task plan")
        if plan["status"] != "approved" or not plan["approved_by"]:
            raise WorkerQueueError("Worker jobs require a Director-approved task plan")
        task = next((item for item in plan["tasks"] if item["task_id"] == task_id), None)
        if task is None or task["parent_project_id"] != project_id:
            raise WorkerQueueError("Task does not belong to the selected project")
        if require_active and task["status"] != "active":
            raise WorkerQueueError("The linked Studio task is no longer active")
        if require_active and not task["evidence_verified"]:
            raise WorkerQueueError("Worker job inputs require verified task evidence")
        return plan, task

    def _validate_job_task(self, manifest: JobManifest) -> None:
        plan, task = self._validate_task(manifest.project_id, manifest.task_id)
        if task["status"] != "active":
            raise WorkerQueueError("The linked Studio task is no longer active")
        if plan["deadline_at"] < self._timestamp(manifest.deadline_at):
            raise WorkerQueueError("Worker job deadline exceeds its Studio plan deadline")
        verified = {
            artifact["artifact_id"]: artifact
            for artifact in task["evidence_artifacts"]
            if artifact["verified"]
        }
        for reference in manifest.input_artifacts:
            item = verified.get(reference.artifact_id)
            if (
                item is None
                or item["sha256"] != reference.sha256
                or item["size_bytes"] != reference.size_bytes
                or reference.backend is not StorageBackend.LOCAL
            ):
                raise WorkerQueueError("Worker inputs must reference current verified evidence for this task")

    def _new_manifest(
        self,
        *,
        plan: dict[str, Any],
        task: dict[str, Any],
        workload: WorkloadKind,
        idempotency_key: str,
        now: datetime,
    ) -> JobManifest:
        stable = hashlib.sha256(
            f"{task['task_id']}:{idempotency_key}".encode("utf-8")
        ).hexdigest()[:24]
        job_id = f"job-{stable}"
        inputs = tuple(
            ArtifactRef(
                artifact_id=item["artifact_id"],
                backend=StorageBackend.LOCAL,
                key=f"studio-evidence/{item['artifact_id']}.txt",
                sha256=item["sha256"],
                size_bytes=item["size_bytes"],
                media_type="text/plain",
            )
            for item in task["evidence_artifacts"]
            if item["verified"]
        )
        if not inputs:
            raise WorkerQueueError("A worker job requires verified task evidence as input")
        max_input = sum(item.size_bytes for item in inputs)
        output_limit = min(512 * 1024**2, 2 * 1024**3)
        output_id = f"output-{stable}"
        resources = (
            ResourceRequirements(
                accelerator=Accelerator.NONE,
                cpu_cores=2,
                memory_mb=4096,
                max_runtime_seconds=6 * 60 * 60,
                max_output_bytes=output_limit,
                max_storage_bytes=max_input + output_limit,
                max_concurrent_jobs=1,
            )
            if workload is WorkloadKind.CPU_FFMPEG
            else ResourceRequirements(
                accelerator=Accelerator.NVIDIA_T4,
                cpu_cores=2,
                memory_mb=8192,
                min_vram_mb=8192,
                max_runtime_seconds=2 * 60 * 60,
                max_output_bytes=output_limit,
                max_storage_bytes=max_input + output_limit,
                max_concurrent_jobs=1,
            )
        )
        manifest = JobManifest(
            schema_version=1,
            job_id=job_id,
            idempotency_key=idempotency_key,
            project_id=plan["project_id"],
            task_id=task["task_id"],
            workload=workload,
            input_artifacts=inputs,
            output_artifacts=(
                OutputDeclaration(
                    artifact_id=output_id,
                    backend=StorageBackend.LOCAL,
                    key=f"worker-jobs/{job_id}/output.bin",
                    media_type="application/octet-stream",
                    max_bytes=output_limit,
                ),
            ),
            resources=resources,
            max_attempts=min(task["max_attempts"], 3),
            created_at=now,
            deadline_at=parse_datetime(plan["deadline_at"]),
        )
        validate_manifest(manifest)
        return manifest

    def enqueue_task(
        self,
        task_id: str,
        *,
        director_id: str,
        workload: WorkloadKind | str,
        idempotency_key: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        current = now or datetime.now(timezone.utc)
        if director_id not in self.director_ids:
            raise WorkerQueueError("Only a configured Director may queue a worker job")
        try:
            kind = WorkloadKind(workload)
        except (TypeError, ValueError) as exc:
            raise WorkerQueueError("Worker workload must be cpu_ffmpeg or gpu_inference") from exc
        with self._transaction():
            task_row = self._connection.execute(
                """
                SELECT parent_project_id FROM studio_allocation_tasks
                WHERE task_id = ?
                """,
                (task_id,),
            ).fetchone()
            if task_row is None:
                raise WorkerQueueError("Studio task not found")
            project_id = task_row["parent_project_id"]
            plan, task = self._validate_task(project_id, task_id)
            if director_id != plan["approved_by"]:
                raise WorkerQueueError("Only the approving Director may queue work for this plan")
            if not isinstance(idempotency_key, str) or not idempotency_key:
                idempotency_key = "request-" + hashlib.sha256(
                    self._json(
                        [
                            task_id,
                            kind.value,
                            [
                                [item["artifact_id"], item["sha256"]]
                                for item in task["evidence_artifacts"]
                                if item["verified"]
                            ],
                        ]
                    ).encode("utf-8")
                ).hexdigest()[:32]
            manifest = self._new_manifest(
                plan=plan,
                task=task,
                workload=kind,
                idempotency_key=idempotency_key,
                now=current,
            )
            self._validate_job_task(manifest)
            existing = self._connection.execute(
                "SELECT job_id FROM studio_worker_jobs WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if existing:
                try:
                    ensure_idempotent_enqueue(
                        self._record(existing["job_id"]).manifest, manifest
                    )
                except ContractError as exc:
                    raise WorkerQueueError(str(exc)) from exc
                return self._job_dict(self._record(existing["job_id"]))
            record = create_job(manifest, now=current)
            self._connection.execute(
                """
                INSERT INTO studio_worker_jobs
                    (job_id, idempotency_key, schema_version, project_id, task_id,
                     manifest_json, state, created_at, updated_at, attempt_count,
                     max_attempts, output_verification)
                VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, 0, ?, 'none')
                """,
                (
                    manifest.job_id,
                    manifest.idempotency_key,
                    manifest.project_id,
                    manifest.task_id,
                    self._json(self._manifest_dict(manifest)),
                    record.state.value,
                    self._timestamp(record.created_at),
                    self._timestamp(record.updated_at),
                    manifest.max_attempts,
                ),
            )
            self._append_events(record, 0)
            for declaration in manifest.output_artifacts:
                self._record_output_check(
                    record,
                    artifact_id=declaration.artifact_id,
                    attempt=0,
                    status="awaiting_verification",
                    now=current,
                )
            return self._job_dict(record)

    def _get_director_for_task(self, manifest: JobManifest) -> str:
        plan, _ = self._validate_task(
            manifest.project_id, manifest.task_id, require_active=False
        )
        return plan["approved_by"]

    def authorize(self, job_id: str, *, director_id: str, now: datetime | None = None) -> dict[str, Any]:
        current = now or datetime.now(timezone.utc)
        with self._transaction():
            record = self._record(job_id)
            self._validate_job_task(record.manifest)
            approved_by = self._get_director_for_task(record.manifest)
            if director_id != approved_by:
                raise WorkerQueueError("Only the approving Director may authorize execution")
            authorization = DirectorAuthorization(
                authorization_id=f"auth-{uuid.uuid4().hex}",
                director_id=director_id,
                manifest_sha256=manifest_digest(record.manifest),
                authorized_at=current,
                expires_at=min(record.manifest.deadline_at, current + timedelta(hours=1)),
            )
            try:
                updated = authorize_execution(
                    record, authorization, director_ids=self.director_ids, now=current
                )
            except ContractError as exc:
                raise WorkerQueueError(str(exc)) from exc
            self._save_transition(updated, len(record.audit_events))
            return self._job_dict(updated)

    def register_worker(
        self,
        *,
        director_id: str,
        worker_id: str,
        workloads: tuple[str, ...],
        state: str,
        expires_at: datetime,
        vram_mb: int = 0,
        operations: tuple[str, ...] = (),
        language_tags: tuple[str, ...] = (),
        model_ids: tuple[str, ...] = (),
        now: datetime | None = None,
    ) -> dict[str, Any]:
        current = now or datetime.now(timezone.utc)
        if director_id not in self.director_ids:
            raise WorkerQueueError("Only a configured Director may register a worker")
        try:
            worker = WorkerRegistration(
                worker_id=worker_id,
                state=WorkerState(state),
                workloads=tuple(WorkloadKind(item) for item in workloads),
                registered_at=current,
                expires_at=expires_at,
                vram_mb=vram_mb,
                operations=operations,
                language_tags=language_tags,
                model_ids=model_ids,
            )
            validate_worker_registration(worker, now=current)
            if expires_at > current + timedelta(hours=12):
                raise WorkerQueueError("Manual worker registration cannot exceed 12 hours")
        except (ContractError, TypeError, ValueError) as exc:
            raise WorkerQueueError(str(exc)) from exc
        with self._transaction():
            try:
                self._connection.execute(
                    """
                    INSERT INTO studio_worker_registrations
                        (worker_id, state, workloads_json, registered_at, expires_at,
                         max_concurrent_jobs, vram_mb, operations_json,
                         language_tags_json, model_ids_json, registration_method,
                         is_simulation, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'manual', 1, ?)
                    """,
                    (
                        worker.worker_id,
                        worker.state.value,
                        self._json([item.value for item in worker.workloads]),
                        self._timestamp(worker.registered_at),
                        self._timestamp(worker.expires_at),
                        worker.max_concurrent_jobs,
                        worker.vram_mb,
                        self._json(worker.operations),
                        self._json(worker.language_tags),
                        self._json(worker.model_ids),
                        self._timestamp(current),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise WorkerQueueError("Worker ID is already registered") from exc
        return self._worker_dict(worker)

    def set_worker_state(
        self, worker_id: str, *, director_id: str, state: str, now: datetime | None = None
    ) -> dict[str, Any]:
        current = now or datetime.now(timezone.utc)
        if director_id not in self.director_ids:
            raise WorkerQueueError("Only a configured Director may update worker availability")
        try:
            target = WorkerState(state)
        except (TypeError, ValueError) as exc:
            raise WorkerQueueError("Worker state is invalid") from exc
        with self._transaction():
            worker = self._worker(worker_id)
            if current < worker.registered_at:
                raise WorkerQueueError("Worker update time precedes registration")
            updated = WorkerRegistration(
                **{**asdict(worker), "state": target}
            )
            try:
                validate_worker_registration(updated, now=current)
            except ContractError as exc:
                raise WorkerQueueError(str(exc)) from exc
            self._connection.execute(
                "UPDATE studio_worker_registrations SET state = ?, updated_at = ? WHERE worker_id = ?",
                (target.value, self._timestamp(current), worker_id),
            )
        return self._worker_dict(updated)

    def _worker(self, worker_id: str) -> WorkerRegistration:
        row = self._connection.execute(
            "SELECT * FROM studio_worker_registrations WHERE worker_id = ?",
            (worker_id,),
        ).fetchone()
        if row is None:
            raise WorkerQueueError("Registered worker not found")
        return WorkerRegistration(
            worker_id=row["worker_id"],
            state=WorkerState(row["state"]),
            workloads=tuple(WorkloadKind(item) for item in json.loads(row["workloads_json"])),
            registered_at=parse_datetime(row["registered_at"]),
            expires_at=parse_datetime(row["expires_at"]),
            max_concurrent_jobs=row["max_concurrent_jobs"],
            vram_mb=row["vram_mb"],
            operations=tuple(json.loads(row["operations_json"])),
            language_tags=tuple(json.loads(row["language_tags_json"])),
            model_ids=tuple(json.loads(row["model_ids_json"])),
            registration_method=row["registration_method"],
        )

    @staticmethod
    def _worker_dict(worker: WorkerRegistration) -> dict[str, Any]:
        return {
            "worker_id": worker.worker_id,
            "state": worker.state.value,
            "workloads": [item.value for item in worker.workloads],
            "registered_at": worker.registered_at.isoformat(),
            "expires_at": worker.expires_at.isoformat(),
            "max_concurrent_jobs": worker.max_concurrent_jobs,
            "vram_mb": worker.vram_mb,
            "operations": list(worker.operations),
            "language_tags": list(worker.language_tags),
            "model_ids": list(worker.model_ids),
            "registration_method": worker.registration_method,
            "is_simulation": True,
        }

    def lease(
        self,
        job_id: str,
        *,
        worker_id: str,
        director_ids: set[str] | frozenset[str] | None = None,
        lease_seconds: int = 300,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        current = now or datetime.now(timezone.utc)
        with self._transaction():
            record = self._record(job_id)
            self._validate_job_task(record.manifest)
            if record.authorization is None:
                raise WorkerQueueError(
                    "Director execution authorization is required before leasing"
                )
            worker = self._worker(worker_id)
            active = self._connection.execute(
                """
                SELECT COUNT(*) FROM studio_worker_jobs
                WHERE state IN ('leased', 'running') AND json_extract(lease_json, '$.worker_id') = ?
                """,
                (worker_id,),
            ).fetchone()[0]
            try:
                updated = lease_job(
                    record,
                    worker,
                    director_ids=self.director_ids,
                    now=current,
                    lease_seconds=lease_seconds,
                    active_jobs_for_worker=active,
                )
            except ContractError as exc:
                raise WorkerQueueError(str(exc)) from exc
            self._save_transition(updated, len(record.audit_events))
            return self._job_dict(updated)

    def start(self, job_id: str, *, worker_id: str, now: datetime | None = None) -> dict[str, Any]:
        return self._transition_contract(
            job_id, lambda record, current: start_job(record, worker_id=worker_id, now=current), now
        )

    def heartbeat(
        self, job_id: str, *, worker_id: str, lease_seconds: int = 300,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        current = now or datetime.now(timezone.utc)
        with self._lock:
            worker = self._worker(worker_id)
        try:
            validate_worker_registration(worker, now=current)
        except ContractError as exc:
            raise WorkerQueueError(str(exc)) from exc
        if worker.state is not WorkerState.AVAILABLE:
            raise WorkerQueueError("Unavailable workers cannot heartbeat")
        return self._transition_contract(
            job_id,
            lambda record, current: heartbeat_job(
                record, worker_id=worker_id, now=current, lease_seconds=lease_seconds
            ),
            current,
        )

    def fail(
        self, job_id: str, *, worker_id: str, error_code: str, now: datetime | None = None
    ) -> dict[str, Any]:
        return self._transition_contract(
            job_id,
            lambda record, current: fail_job(
                record, worker_id=worker_id, error_code=error_code, now=current
            ),
            now,
        )

    def submit_result(
        self, job_id: str, *, worker_id: str, lease_id: str, attempt: int,
        outputs: tuple[dict[str, Any], ...], result_id: str | None = None,
        completed_at: datetime | None = None, now: datetime | None = None,
    ) -> dict[str, Any]:
        current = now or datetime.now(timezone.utc)
        timestamp = completed_at or current
        if type(attempt) is not int or not isinstance(outputs, tuple):
            raise WorkerQueueError("Worker result attempt and output collection have invalid types")

        created_paths: list[Path] = []
        verification_error: str | None = None
        accepted_record: JobRecord | None = None
        try:
            with self._transaction():
                record = self._record(job_id)
                self._validate_job_task(record.manifest)
                if record.state is JobState.SUCCEEDED:
                    raise WorkerQueueError("A result was already accepted for this job")
                lease = record.lease
                if (
                    record.state is not JobState.RUNNING
                    or lease is None
                    or worker_id != lease.worker_id
                    or lease_id != lease.lease_id
                    or attempt != lease.attempt
                    or current >= lease.expires_at
                ):
                    raise WorkerQueueError("Worker result is stale or does not own the active lease")
                if (
                    not isinstance(timestamp, datetime)
                    or timestamp.tzinfo is None
                    or timestamp.utcoffset() is None
                    or timestamp < lease.acquired_at
                    or timestamp > current
                    or timestamp > lease.expires_at
                ):
                    raise WorkerQueueError("Worker result completion is outside the active lease")

                declarations = {
                    item.artifact_id: item for item in record.manifest.output_artifacts
                }
                output_by_id: dict[str, dict[str, Any]] = {}
                try:
                    for item in outputs:
                        required = {
                            "artifact_id", "backend", "key", "sha256",
                            "size_bytes", "media_type", "content",
                        }
                        if not isinstance(item, dict) or set(item) != required:
                            raise WorkerQueueError(
                                "Each worker output must include declared metadata and actual bytes"
                            )
                        artifact_id = item["artifact_id"]
                        if not isinstance(artifact_id, str) or artifact_id not in declarations:
                            raise WorkerQueueError("Worker output is not declared by this job")
                        if artifact_id in output_by_id:
                            raise WorkerQueueError("Worker result contains a duplicate output")
                        declaration = declarations[artifact_id]
                        if (
                            item["backend"] != declaration.backend.value
                            or item["key"] != declaration.key
                            or item["media_type"] != declaration.media_type
                        ):
                            raise WorkerQueueError(
                                "Worker output ownership or type does not match its declaration"
                            )
                        output_by_id[artifact_id] = item
                    if set(output_by_id) != set(declarations):
                        raise WorkerQueueError("Worker result is missing one or more declared output artifacts")
                    if sum(len(item["content"]) for item in output_by_id.values()) > (
                        record.manifest.resources.max_output_bytes
                    ):
                        raise WorkerQueueError("Worker result exceeds the aggregate output size limit")

                    verified_refs: list[ArtifactRef] = []
                    verified_material: list[tuple[dict[str, Any], OutputDeclaration, str, str, int]] = []
                    for artifact_id, declaration in declarations.items():
                        item = output_by_id[artifact_id]
                        storage_name, digest, size_bytes = self._write_verified_output(
                            record=record,
                            artifact_id=artifact_id,
                            attempt=attempt,
                            content=item["content"],
                            expected_sha256=item["sha256"],
                            expected_size_bytes=item["size_bytes"],
                            media_type=item["media_type"],
                        )
                        output_path = self._artifact_directory() / storage_name
                        created_paths.append(output_path)
                        verified_ref = ArtifactRef(
                            artifact_id=artifact_id,
                            backend=declaration.backend,
                            key=declaration.key,
                            sha256=digest,
                            size_bytes=size_bytes,
                            media_type=declaration.media_type,
                        )
                        verified_material.append(
                            (item, declaration, storage_name, digest, size_bytes)
                        )
                        verified_refs.append(verified_ref)
                    result = JobResult(
                        result_id=result_id or f"result-{uuid.uuid4().hex}",
                        job_id=job_id,
                        lease_id=lease_id,
                        worker_id=worker_id,
                        attempt=attempt,
                        outputs=tuple(verified_refs),
                        completed_at=timestamp,
                    )
                    updated = accept_result(record, result, now=current)
                    for item, declaration, storage_name, digest, size_bytes in verified_material:
                        self._connection.execute(
                            """
                            INSERT INTO studio_worker_verified_outputs
                                (job_id, task_id, project_id, artifact_id, storage_name,
                                 attempt, sha256, size_bytes, media_type, verified_at,
                                 is_synthetic)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                            """,
                            (
                                job_id,
                                record.manifest.task_id,
                                record.manifest.project_id,
                                declaration.artifact_id,
                                storage_name,
                                attempt,
                                digest,
                                size_bytes,
                                declaration.media_type,
                                self._timestamp(current),
                            ),
                        )
                        self._record_output_check(
                            record,
                            artifact_id=declaration.artifact_id,
                            attempt=attempt,
                            status="verified",
                            now=current,
                            expected_sha256=item["sha256"],
                            actual_sha256=digest,
                            expected_size_bytes=item["size_bytes"],
                            actual_size_bytes=size_bytes,
                        )
                    self._save_transition(updated, len(record.audit_events))
                    self._append_integrity_event(
                        updated,
                        event_type="outputs_verified",
                        actor_id=worker_id,
                        now=current,
                        details={
                            "attempt": attempt,
                            "outputs": [
                                {
                                    "artifact_id": declaration.artifact_id,
                                    "sha256": digest,
                                    "size_bytes": size_bytes,
                                    "storage_name": storage_name,
                                }
                                for _, declaration, storage_name, digest, size_bytes
                                in verified_material
                            ],
                        },
                    )
                    accepted_record = self._record(job_id)
                except (WorkerQueueError, ContractError, TypeError, ValueError, KeyError, OSError) as exc:
                    for output_path in created_paths:
                        output_path.unlink(missing_ok=True)
                    created_paths.clear()
                    verification_error = str(exc)
                    record = self._record(job_id)
                    error_code = self._output_error_code(verification_error)
                    failed_outputs: list[dict[str, Any]] = []
                    for artifact_id in declarations:
                        supplied = output_by_id.get(artifact_id, {})
                        expected_hash = supplied.get("sha256")
                        if (
                            not isinstance(expected_hash, str)
                            or not re.fullmatch(r"[a-f0-9]{64}", expected_hash)
                        ):
                            expected_hash = None
                        expected_size = supplied.get("size_bytes")
                        if type(expected_size) is not int or expected_size < 0:
                            expected_size = None
                        content = supplied.get("content")
                        actual_hash = None
                        actual_size = None
                        if isinstance(content, bytes):
                            actual_size = len(content)
                            if actual_size <= declarations[artifact_id].max_bytes:
                                actual_hash = hashlib.sha256(content).hexdigest()
                        self._record_output_check(
                            record,
                            artifact_id=artifact_id,
                            attempt=attempt,
                            status="failed",
                            now=current,
                            expected_sha256=expected_hash,
                            actual_sha256=actual_hash,
                            expected_size_bytes=expected_size,
                            actual_size_bytes=actual_size,
                            error_code=error_code,
                        )
                        failed_outputs.append(
                            {
                                "artifact_id": artifact_id,
                                "expected_sha256": expected_hash,
                                "actual_sha256": actual_hash,
                                "expected_size_bytes": expected_size,
                                "actual_size_bytes": actual_size,
                            }
                        )
                    self._append_integrity_event(
                        record,
                        event_type="output_verification_failed",
                        actor_id=worker_id if isinstance(worker_id, str) else "unknown-worker",
                        now=current,
                        details={
                            "attempt": attempt,
                            "error_code": error_code,
                            "reason": verification_error[:256],
                            "outputs": failed_outputs,
                        },
                    )
        except BaseException:
            for output_path in created_paths:
                output_path.unlink(missing_ok=True)
            raise
        if verification_error is not None:
            raise WorkerQueueError(verification_error)
        if accepted_record is None:
            raise WorkerQueueError("Worker result was not accepted")
        return self._job_dict(accepted_record)

    def retry(self, job_id: str, *, director_id: str, now: datetime | None = None) -> dict[str, Any]:
        current = now or datetime.now(timezone.utc)
        with self._transaction():
            record = self._record(job_id)
            if self._get_director_for_task(record.manifest) != director_id:
                raise WorkerQueueError("Only the approving Director may retry this job")
            try:
                updated = retry_failed_job(
                    record, director_id=director_id,
                    director_ids=self.director_ids, now=current,
                )
            except ContractError as exc:
                raise WorkerQueueError(str(exc)) from exc
            self._save_transition(updated, len(record.audit_events))
            return self._job_dict(updated)

    def cancel(
        self, job_id: str, *, director_id: str, now: datetime | None = None
    ) -> dict[str, Any]:
        current = now or datetime.now(timezone.utc)
        with self._transaction():
            record = self._record(job_id)
            if self._get_director_for_task(record.manifest) != director_id:
                raise WorkerQueueError("Only the approving Director may cancel this job")
            try:
                updated = contract_cancel_job(
                    record, actor_id=director_id,
                    director_ids=self.director_ids, now=current,
                )
            except ContractError as exc:
                raise WorkerQueueError(str(exc)) from exc
            self._save_transition(updated, len(record.audit_events))
            return self._job_dict(updated)

    def recover_expired_leases(self, *, now: datetime | None = None) -> list[dict[str, Any]]:
        current = now or datetime.now(timezone.utc)
        recovered: list[dict[str, Any]] = []
        with self._transaction():
            rows = self._connection.execute(
                """
                SELECT job_id FROM studio_worker_jobs
                WHERE state IN ('leased', 'running', 'interrupted')
                ORDER BY created_at, job_id
                """
            ).fetchall()
            for row in rows:
                record = self._record(row["job_id"])
                if record.lease and current >= record.lease.expires_at:
                    try:
                        interrupted = expire_lease(record, now=current)
                        recovered_record = recover_interrupted_job(interrupted, now=current)
                    except ContractError as exc:
                        raise WorkerQueueError(str(exc)) from exc
                    self._save_transition(recovered_record, len(record.audit_events))
                    recovered.append(self._job_dict(recovered_record))
                elif record.state is JobState.INTERRUPTED:
                    try:
                        updated = recover_interrupted_job(record, now=current)
                    except ContractError as exc:
                        raise WorkerQueueError(str(exc)) from exc
                    self._save_transition(updated, len(record.audit_events))
                    recovered.append(self._job_dict(updated))
        return recovered

    def _transition_contract(self, job_id: str, transition, now: datetime | None) -> dict[str, Any]:
        current = now or datetime.now(timezone.utc)
        with self._transaction():
            record = self._record(job_id)
            self._validate_job_task(record.manifest)
            try:
                updated = transition(record, current)
            except ContractError as exc:
                raise WorkerQueueError(str(exc)) from exc
            self._save_transition(updated, len(record.audit_events))
            return self._job_dict(updated)

    def get_record(self, job_id: str) -> JobRecord:
        with self._lock:
            return self._record(job_id)

    def _job_dict(self, record: JobRecord) -> dict[str, Any]:
        plan = self.allocation.get_plan_for_project(record.manifest.project_id)
        if plan is None:
            raise WorkerQueueError("Linked Studio project plan no longer exists")
        task = next(
            (item for item in plan["tasks"] if item["task_id"] == record.manifest.task_id),
            None,
        )
        if task is None:
            raise WorkerQueueError("Linked Studio task no longer exists")
        output_rows = self._connection.execute(
            """
            SELECT artifact_json, verification_status, created_at
            FROM studio_worker_job_outputs WHERE job_id = ? ORDER BY artifact_id
            """,
            (record.manifest.job_id,),
        ).fetchall()
        verified_rows = self._connection.execute(
            """
            SELECT output.artifact_id, output.storage_name, output.sha256,
                   output.size_bytes, output.media_type, output.verified_at,
                   output.attempt, output.is_synthetic
            FROM studio_worker_verified_outputs AS output
            WHERE output.job_id = ?
              AND output.attempt = (
                  SELECT MAX(latest.attempt)
                  FROM studio_worker_verified_outputs AS latest
                  WHERE latest.job_id = output.job_id
                    AND latest.artifact_id = output.artifact_id
              )
            ORDER BY output.artifact_id
            """,
            (record.manifest.job_id,),
        ).fetchall()
        check_rows = self._connection.execute(
            """
            SELECT artifact_id, attempt, status, expected_sha256, actual_sha256,
                   expected_size_bytes, actual_size_bytes, error_code, checked_at,
                   is_synthetic
            FROM studio_worker_output_checks
            WHERE job_id = ? ORDER BY rowid
            """,
            (record.manifest.job_id,),
        ).fetchall()
        latest_checks: dict[str, sqlite3.Row] = {}
        for check in check_rows:
            latest_checks[check["artifact_id"]] = check
        output_statuses = {
            declaration.artifact_id: (
                latest_checks[declaration.artifact_id]["status"]
                if declaration.artifact_id in latest_checks
                else "awaiting_verification"
            )
            for declaration in record.manifest.output_artifacts
        }
        if any(status == "failed" for status in output_statuses.values()):
            output_verification = "failed"
        elif output_statuses and all(status == "verified" for status in output_statuses.values()):
            output_verification = "verified"
        else:
            output_verification = "awaiting_verification"
        output_details = [
            {
                "artifact_id": item["artifact_id"],
                "storage_name": item["storage_name"],
                "sha256": item["sha256"],
                "size_bytes": item["size_bytes"],
                "media_type": item["media_type"],
                "attempt": item["attempt"],
                "verification_status": "verified",
                "created_at": item["verified_at"],
                "is_synthetic": bool(item["is_synthetic"]),
            }
            for item in verified_rows
        ]
        output_details.extend(
            {
                **json.loads(item["artifact_json"]),
                "verification_status": item["verification_status"],
                "created_at": item["created_at"],
                "is_synthetic": True,
            }
            for item in output_rows
            if item["artifact_id"] not in {
                output["artifact_id"] for output in verified_rows
            }
        )
        return {
            "job_id": record.manifest.job_id,
            "idempotency_key": record.manifest.idempotency_key,
            "schema_version": record.manifest.schema_version,
            "project_id": record.manifest.project_id,
            "project_title": plan["project"]["title"],
            "project_is_demo": plan["project"]["is_demo"],
            "task_id": record.manifest.task_id,
            "task_title": task["title"],
            "task_status": task["status"],
            "workload": record.manifest.workload.value,
            "state": record.state.value,
            "created_at": self._timestamp(record.created_at),
            "updated_at": self._timestamp(record.updated_at),
            "attempt_count": record.attempt_count,
            "max_attempts": record.manifest.max_attempts,
            "deadline_at": self._timestamp(record.manifest.deadline_at),
            "authorization": self._auth_dict(record.authorization),
            "lease": self._lease_dict(record.lease),
            "last_error": record.last_error,
            "resource_requirements": {
                "accelerator": record.manifest.resources.accelerator.value,
                "cpu_cores": record.manifest.resources.cpu_cores,
                "memory_mb": record.manifest.resources.memory_mb,
                "min_vram_mb": record.manifest.resources.min_vram_mb,
                "max_runtime_seconds": record.manifest.resources.max_runtime_seconds,
                "max_output_bytes": record.manifest.resources.max_output_bytes,
                "max_storage_bytes": record.manifest.resources.max_storage_bytes,
            },
            "input_artifacts": [
                self._artifact_json(item) for item in record.manifest.input_artifacts
            ],
            "output_declarations": [
                {
                    "artifact_id": item.artifact_id,
                    "backend": item.backend.value,
                    "key": item.key,
                    "media_type": item.media_type,
                    "max_bytes": item.max_bytes,
                }
                for item in record.manifest.output_artifacts
            ],
            "outputs": output_details,
            "output_checks": [
                {
                    "artifact_id": item["artifact_id"],
                    "attempt": item["attempt"],
                    "status": item["status"],
                    "expected_sha256": item["expected_sha256"],
                    "actual_sha256": item["actual_sha256"],
                    "expected_size_bytes": item["expected_size_bytes"],
                    "actual_size_bytes": item["actual_size_bytes"],
                    "error_code": item["error_code"],
                    "checked_at": item["checked_at"],
                    "is_synthetic": bool(item["is_synthetic"]),
                }
                for item in check_rows
            ],
            "output_verification": output_verification,
            "result": self._result_dict(record.result),
            "audit_events": [
                {
                    "sequence": index + 1,
                    **asdict(event),
                    "created_at": self._timestamp(event.created_at),
                }
                for index, event in enumerate(record.audit_events)
            ],
        }

    def get_job(self, job_id: str) -> dict[str, Any]:
        self._recover_artifact_storage(now=datetime.now(timezone.utc))
        with self._lock:
            return self._job_dict(self._record(job_id))

    def snapshot(self) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        self._recover_artifact_storage(now=now)
        with self._lock:
            jobs = [
                self._job_dict(self._record(row["job_id"]))
                for row in self._connection.execute(
                    "SELECT job_id FROM studio_worker_jobs ORDER BY created_at DESC, job_id"
                )
            ]
            workers = [
                self._worker_dict(self._worker(row["worker_id"]))
                for row in self._connection.execute(
                    "SELECT worker_id FROM studio_worker_registrations ORDER BY registered_at DESC"
                )
            ]
        counts = {state.value: 0 for state in JobState}
        for job in jobs:
            counts[job["state"]] += 1
        counts["total"] = len(jobs)
        return {
            "mode": "local_only",
            "workers": workers,
            "jobs": jobs,
            "counts": counts,
            "available_worker_count": sum(
                worker["state"] == WorkerState.AVAILABLE.value
                and parse_datetime(worker["expires_at"]) > now
                for worker in workers
            ),
            "output_notice": (
                "Only locally stored byte-verified outputs are verified. Synthetic "
                "fixtures are not rendered content or Studio evidence; legacy "
                "metadata-only results remain awaiting verification."
            ),
        }

    def verify_audit_chain(self, job_id: str | None = None) -> bool:
        with self._lock:
            if job_id is None:
                ids = [
                    row["job_id"]
                    for row in self._connection.execute(
                        "SELECT job_id FROM studio_worker_jobs ORDER BY job_id"
                    )
                ]
            else:
                ids = [job_id]
            for current_id in ids:
                previous = "0" * 64
                events = self._connection.execute(
                    """
                    SELECT * FROM studio_worker_job_events
                    WHERE job_id = ? ORDER BY sequence
                    """,
                    (current_id,),
                ).fetchall()
                for expected, event in enumerate(events, start=1):
                    if (
                        event["sequence"] != expected
                        or event["previous_hash"] != previous
                    ):
                        return False
                    material = (
                        f"{previous}|{event['event_id']}|{event['event_type']}|"
                        f"{event['actor_id']}|{event['created_at']}|{event['details_json']}"
                    )
                    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
                    if digest != event["event_hash"]:
                        return False
                    previous = digest
            return True
