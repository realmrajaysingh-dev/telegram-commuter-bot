"""Offline contracts for a future manually operated media worker."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from enum import StrEnum
import hashlib
import json
import re
from typing import Iterable


JOB_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
LANGUAGE_TAG_PATTERN = re.compile(r"^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")
DRIVE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{10,200}$")
MAX_LEASE_SECONDS = 900
MIN_LEASE_SECONDS = 30


class ContractError(ValueError):
    """Raised when a worker contract violates offline policy."""


class WorkloadKind(StrEnum):
    CPU_FFMPEG = "cpu_ffmpeg"
    GPU_INFERENCE = "gpu_inference"


class Accelerator(StrEnum):
    NONE = "none"
    NVIDIA_T4 = "nvidia_t4"


class StorageBackend(StrEnum):
    LOCAL = "local"
    GOOGLE_DRIVE = "google_drive"


class JobState(StrEnum):
    QUEUED = "queued"
    LEASED = "leased"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    CANCELLED = "cancelled"


class WorkerState(StrEnum):
    REGISTERED = "registered"
    AVAILABLE = "available"
    GPU_UNAVAILABLE = "gpu_unavailable"
    DISCONNECTED = "disconnected"


@dataclass(frozen=True)
class WorkerLimits:
    max_artifact_bytes: int = 2 * 1024**3
    max_total_input_bytes: int = 8 * 1024**3
    max_output_bytes: int = 4 * 1024**3
    max_storage_bytes: int = 10 * 1024**3
    max_cpu_runtime_seconds: int = 6 * 60 * 60
    max_gpu_runtime_seconds: int = 2 * 60 * 60
    max_concurrent_jobs: int = 1
    max_lease_seconds: int = MAX_LEASE_SECONDS
    min_lease_seconds: int = MIN_LEASE_SECONDS


DEFAULT_LIMITS = WorkerLimits()


@dataclass(frozen=True)
class ArtifactRef:
    artifact_id: str
    backend: StorageBackend
    key: str
    sha256: str
    size_bytes: int
    media_type: str


@dataclass(frozen=True)
class OutputDeclaration:
    artifact_id: str
    backend: StorageBackend
    key: str
    media_type: str
    max_bytes: int


@dataclass(frozen=True)
class ResourceRequirements:
    accelerator: Accelerator = Accelerator.NONE
    cpu_cores: int = 1
    memory_mb: int = 1024
    min_vram_mb: int = 0
    max_runtime_seconds: int = 3600
    max_output_bytes: int = 1024**3
    max_storage_bytes: int = 2 * 1024**3
    max_concurrent_jobs: int = 1


@dataclass(frozen=True)
class CapabilityRequirements:
    operations: tuple[str, ...] = ()
    language_tags: tuple[str, ...] = ()
    model_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class JobManifest:
    schema_version: int
    job_id: str
    idempotency_key: str
    project_id: str
    task_id: str
    workload: WorkloadKind
    input_artifacts: tuple[ArtifactRef, ...]
    output_artifacts: tuple[OutputDeclaration, ...]
    resources: ResourceRequirements
    max_attempts: int
    created_at: datetime
    deadline_at: datetime
    capabilities: CapabilityRequirements = CapabilityRequirements()


@dataclass(frozen=True)
class DirectorAuthorization:
    authorization_id: str
    director_id: str
    manifest_sha256: str
    authorized_at: datetime
    expires_at: datetime


@dataclass(frozen=True)
class WorkerRegistration:
    worker_id: str
    state: WorkerState
    workloads: tuple[WorkloadKind, ...]
    registered_at: datetime
    expires_at: datetime
    max_concurrent_jobs: int = 1
    vram_mb: int = 0
    operations: tuple[str, ...] = ()
    language_tags: tuple[str, ...] = ()
    model_ids: tuple[str, ...] = ()
    registration_method: str = "manual"


@dataclass(frozen=True)
class JobLease:
    lease_id: str
    worker_id: str
    attempt: int
    acquired_at: datetime
    expires_at: datetime
    heartbeat_at: datetime


@dataclass(frozen=True)
class AuditEvent:
    event_id: str
    event_type: str
    actor_id: str
    created_at: datetime
    details_json: str
    previous_hash: str
    event_hash: str


@dataclass(frozen=True)
class JobResult:
    result_id: str
    job_id: str
    lease_id: str
    worker_id: str
    attempt: int
    outputs: tuple[ArtifactRef, ...]
    completed_at: datetime


@dataclass(frozen=True)
class JobRecord:
    manifest: JobManifest
    state: JobState
    created_at: datetime
    updated_at: datetime
    attempt_count: int = 0
    authorization: DirectorAuthorization | None = None
    lease: JobLease | None = None
    result: JobResult | None = None
    last_error: str | None = None
    audit_events: tuple[AuditEvent, ...] = ()


def _aware(value: datetime, field: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ContractError(f"{field} must be a timezone-aware datetime")


def _identifier(value: object, field: str) -> None:
    if not isinstance(value, str) or not JOB_ID_PATTERN.fullmatch(value):
        raise ContractError(f"{field} must be a simple local identifier")


def _unique(values: Iterable[str], field: str) -> None:
    materialized = tuple(values)
    if len(set(materialized)) != len(materialized):
        raise ContractError(f"{field} must not contain duplicates")


def _require_monotonic(now: datetime, previous: datetime, field: str) -> None:
    _aware(now, "Current time")
    _aware(previous, field)
    if now < previous:
        raise ContractError(f"Current time cannot precede {field}")


def _safe_key(backend: StorageBackend, key: str) -> None:
    if not isinstance(key, str) or not key or len(key) > 512:
        raise ContractError("Artifact key must be a non-empty bounded string")
    if backend is StorageBackend.LOCAL:
        parts = key.split("/")
        if (
            key.startswith("/")
            or "\\" in key
            or ":" in key
            or any(part in {"", ".", ".."} for part in parts)
            or any(ord(char) < 32 for char in key)
        ):
            raise ContractError("Local artifact keys must be safe relative paths")
    elif backend is StorageBackend.GOOGLE_DRIVE:
        if not DRIVE_ID_PATTERN.fullmatch(key):
            raise ContractError("Drive references must use an opaque file ID, not a path or URL")
    else:
        raise ContractError("Unsupported artifact storage backend")


def validate_artifact_ref(
    artifact: ArtifactRef, *, limits: WorkerLimits = DEFAULT_LIMITS
) -> None:
    if not isinstance(artifact, ArtifactRef):
        raise ContractError("Artifact reference has an invalid schema")
    _identifier(artifact.artifact_id, "Artifact ID")
    if not isinstance(artifact.backend, StorageBackend):
        raise ContractError("Artifact storage backend is invalid")
    _safe_key(artifact.backend, artifact.key)
    if not isinstance(artifact.sha256, str) or not SHA256_PATTERN.fullmatch(artifact.sha256):
        raise ContractError("Artifact SHA-256 must be a lowercase 64-character digest")
    if type(artifact.size_bytes) is not int or not 0 < artifact.size_bytes <= limits.max_artifact_bytes:
        raise ContractError("Artifact size is outside the permitted range")
    if (
        not isinstance(artifact.media_type, str)
        or not re.fullmatch(r"[a-z0-9][a-z0-9.+-]*/[a-z0-9][a-z0-9.+-]*", artifact.media_type)
    ):
        raise ContractError("Artifact media type must be a normalized type/subtype")


def validate_worker_registration(
    worker: WorkerRegistration,
    *,
    now: datetime,
    limits: WorkerLimits = DEFAULT_LIMITS,
) -> None:
    if not isinstance(worker, WorkerRegistration):
        raise ContractError("Worker registration has an invalid schema")
    _identifier(worker.worker_id, "Worker ID")
    _aware(worker.registered_at, "Worker registration time")
    _aware(worker.expires_at, "Worker expiry")
    _aware(now, "Current time")
    if worker.registration_method != "manual":
        raise ContractError("Workers must be registered manually")
    if worker.registered_at > now:
        raise ContractError("Worker registration time cannot be in the future")
    if worker.expires_at <= worker.registered_at or worker.expires_at <= now:
        raise ContractError("Worker registration must be unexpired")
    if type(worker.max_concurrent_jobs) is not int or not 1 <= worker.max_concurrent_jobs <= limits.max_concurrent_jobs:
        raise ContractError("Worker concurrency exceeds the configured limit")
    if type(worker.vram_mb) is not int or worker.vram_mb < 0:
        raise ContractError("Worker VRAM must be a non-negative integer")
    if (
        not isinstance(worker.workloads, tuple)
        or not worker.workloads
        or any(not isinstance(item, WorkloadKind) for item in worker.workloads)
    ):
        raise ContractError("Worker must declare supported workload kinds")
    _unique((item.value for item in worker.workloads), "Worker workloads")
    _validate_capability_values(worker.operations, worker.language_tags, worker.model_ids)
    if not isinstance(worker.state, WorkerState):
        raise ContractError("Worker state is invalid")


def _validate_capability_values(
    operations: tuple[str, ...], language_tags: tuple[str, ...], model_ids: tuple[str, ...]
) -> None:
    for values, field in (
        (operations, "operations"),
        (model_ids, "model IDs"),
    ):
        if not isinstance(values, tuple) or any(
            not isinstance(value, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", value)
            for value in values
        ):
            raise ContractError(f"Worker {field} must be bounded identifiers")
        _unique(values, f"Worker {field}")
    if not isinstance(language_tags, tuple) or any(
        not isinstance(value, str) or not LANGUAGE_TAG_PATTERN.fullmatch(value)
        for value in language_tags
    ):
        raise ContractError("Language capabilities must use BCP 47-style language tags")
    _unique(language_tags, "Worker language tags")


def validate_manifest(
    manifest: JobManifest, *, limits: WorkerLimits = DEFAULT_LIMITS
) -> None:
    if not isinstance(manifest, JobManifest):
        raise ContractError("Job manifest has an invalid schema")
    if type(manifest.schema_version) is not int or manifest.schema_version != 1:
        raise ContractError("Unsupported job manifest schema version")
    for value, field in (
        (manifest.job_id, "Job ID"),
        (manifest.idempotency_key, "Idempotency key"),
        (manifest.project_id, "Project ID"),
        (manifest.task_id, "Task ID"),
    ):
        _identifier(value, field)
    if not isinstance(manifest.workload, WorkloadKind):
        raise ContractError("Workload kind is invalid")
    if not isinstance(manifest.resources, ResourceRequirements):
        raise ContractError("Resource requirements are invalid")
    if not isinstance(manifest.capabilities, CapabilityRequirements):
        raise ContractError("Capability requirements are invalid")
    _aware(manifest.created_at, "Manifest creation time")
    _aware(manifest.deadline_at, "Manifest deadline")
    if manifest.deadline_at <= manifest.created_at:
        raise ContractError("Job deadline must be after creation")
    if type(manifest.max_attempts) is not int or not 1 <= manifest.max_attempts <= 5:
        raise ContractError("Job attempts must be between one and five")
    resources = manifest.resources
    for value, field, maximum in (
        (resources.cpu_cores, "CPU cores", 64),
        (resources.memory_mb, "Memory", 256 * 1024),
        (resources.max_concurrent_jobs, "Job concurrency", limits.max_concurrent_jobs),
    ):
        if type(value) is not int or not 1 <= value <= maximum:
            raise ContractError(f"{field} requirement exceeds policy")
    if (
        type(resources.min_vram_mb) is not int
        or resources.min_vram_mb < 0
        or type(resources.max_output_bytes) is not int
        or not 0 < resources.max_output_bytes <= limits.max_output_bytes
        or type(resources.max_storage_bytes) is not int
        or not 0 < resources.max_storage_bytes <= limits.max_storage_bytes
    ):
        raise ContractError("Resource byte or VRAM limit is invalid")
    runtime_limit = (
        limits.max_gpu_runtime_seconds
        if manifest.workload is WorkloadKind.GPU_INFERENCE
        else limits.max_cpu_runtime_seconds
    )
    if (
        type(resources.max_runtime_seconds) is not int
        or not 1 <= resources.max_runtime_seconds <= runtime_limit
    ):
        raise ContractError("Execution time exceeds the workload limit")
    if manifest.workload is WorkloadKind.CPU_FFMPEG:
        if resources.accelerator is not Accelerator.NONE or resources.min_vram_mb:
            raise ContractError("FFmpeg CPU jobs cannot request GPU resources")
    elif resources.accelerator is not Accelerator.NVIDIA_T4:
        raise ContractError("GPU inference jobs must explicitly request an NVIDIA T4")

    if not isinstance(manifest.input_artifacts, tuple) or not isinstance(manifest.output_artifacts, tuple):
        raise ContractError("Manifest artifact collections must be tuples")
    if not manifest.input_artifacts or not manifest.output_artifacts:
        raise ContractError("A job requires at least one input and one declared output")
    for artifact in manifest.input_artifacts:
        validate_artifact_ref(artifact, limits=limits)
    input_ids = tuple(item.artifact_id for item in manifest.input_artifacts)
    _unique(input_ids, "Input artifact IDs")
    storage_keys = {(item.backend, item.key) for item in manifest.input_artifacts}
    if len(storage_keys) != len(manifest.input_artifacts):
        raise ContractError("Input artifact storage references must be unique")
    total_input = sum(item.size_bytes for item in manifest.input_artifacts)
    if total_input > limits.max_total_input_bytes:
        raise ContractError("Total input size exceeds the configured limit")
    if total_input > resources.max_storage_bytes:
        raise ContractError("Input artifacts exceed the job storage allocation")
    output_ids: list[str] = []
    output_bytes = 0
    for output in manifest.output_artifacts:
        if not isinstance(output, OutputDeclaration):
            raise ContractError("Output declaration has an invalid schema")
        _identifier(output.artifact_id, "Output artifact ID")
        if not isinstance(output.backend, StorageBackend):
            raise ContractError("Output storage backend is invalid")
        _safe_key(output.backend, output.key)
        if (
            not isinstance(output.media_type, str)
            or not re.fullmatch(r"[a-z0-9][a-z0-9.+-]*/[a-z0-9][a-z0-9.+-]*", output.media_type)
            or type(output.max_bytes) is not int
            or not 0 < output.max_bytes <= limits.max_artifact_bytes
        ):
            raise ContractError("Output declaration is invalid")
        output_ids.append(output.artifact_id)
        location = (output.backend, output.key)
        if location in storage_keys:
            raise ContractError("Output storage references must not overwrite an input")
        storage_keys.add(location)
        output_bytes += output.max_bytes
    _unique(output_ids, "Output artifact IDs")
    if set(input_ids) & set(output_ids):
        raise ContractError("Output artifact IDs must not reuse input IDs")
    if output_bytes > resources.max_output_bytes:
        raise ContractError("Declared outputs exceed the job output allocation")
    if output_bytes + total_input > resources.max_storage_bytes:
        raise ContractError("Job input and output allocations exceed storage limit")
    _validate_capability_values(
        manifest.capabilities.operations,
        manifest.capabilities.language_tags,
        manifest.capabilities.model_ids,
    )


def manifest_digest(manifest: JobManifest) -> str:
    validate_manifest(manifest)
    return hashlib.sha256(_canonical(manifest).encode("utf-8")).hexdigest()


def ensure_idempotent_enqueue(existing: JobManifest, incoming: JobManifest) -> JobManifest:
    validate_manifest(existing)
    validate_manifest(incoming)
    if existing.idempotency_key != incoming.idempotency_key:
        raise ContractError("Existing job does not match the idempotency key")
    if _canonical(_idempotency_payload(existing)) != _canonical(_idempotency_payload(incoming)):
        raise ContractError("Idempotency key is already bound to a different manifest")
    return existing


def _idempotency_payload(manifest: JobManifest) -> dict[str, object]:
    return {
        "idempotency_key": manifest.idempotency_key,
        "project_id": manifest.project_id,
        "task_id": manifest.task_id,
        "workload": manifest.workload,
        "input_artifacts": manifest.input_artifacts,
        "output_artifacts": manifest.output_artifacts,
        "resources": manifest.resources,
        "max_attempts": manifest.max_attempts,
        "deadline_at": manifest.deadline_at,
        "capabilities": manifest.capabilities,
    }


def create_job(manifest: JobManifest, *, now: datetime) -> JobRecord:
    validate_manifest(manifest)
    _aware(now, "Current time")
    if now < manifest.created_at or now >= manifest.deadline_at:
        raise ContractError("Job creation time is outside its manifest window")
    record = JobRecord(
        manifest=manifest,
        state=JobState.QUEUED,
        created_at=now,
        updated_at=now,
    )
    return _event(record, "job_queued", "local-control-plane", now, {})


def authorize_execution(
    record: JobRecord,
    authorization: DirectorAuthorization,
    *,
    director_ids: frozenset[str],
    now: datetime,
) -> JobRecord:
    _aware(now, "Current time")
    _require_monotonic(now, record.updated_at, "job update time")
    if not isinstance(authorization, DirectorAuthorization):
        raise ContractError("Director authorization has an invalid schema")
    if record.state is not JobState.QUEUED:
        raise ContractError("Only queued jobs can receive execution authorization")
    _validate_authorization(
        authorization,
        manifest=record.manifest,
        director_ids=director_ids,
        now=now,
    )
    updated = replace(record, authorization=authorization, updated_at=now)
    return _event(
        updated,
        "execution_authorized",
        authorization.director_id,
        now,
        {"authorization_id": authorization.authorization_id},
    )


def _validate_authorization(
    authorization: DirectorAuthorization,
    *,
    manifest: JobManifest,
    director_ids: frozenset[str],
    now: datetime,
) -> None:
    if not isinstance(authorization, DirectorAuthorization):
        raise ContractError("Director authorization has an invalid schema")
    _identifier(authorization.authorization_id, "Authorization ID")
    _identifier(authorization.director_id, "Director ID")
    if authorization.director_id not in director_ids:
        raise ContractError("Execution authorization requires a configured Director")
    if authorization.manifest_sha256 != manifest_digest(manifest):
        raise ContractError("Director authorization is not bound to this job manifest")
    _aware(authorization.authorized_at, "Authorization time")
    _aware(authorization.expires_at, "Authorization expiry")
    if authorization.authorized_at > now or authorization.expires_at <= now:
        raise ContractError("Director authorization is not currently valid")
    if authorization.expires_at <= authorization.authorized_at:
        raise ContractError("Director authorization expiry must follow authorization time")
    if authorization.expires_at > manifest.deadline_at:
        raise ContractError("Director authorization cannot outlive the job deadline")


def lease_job(
    record: JobRecord,
    worker: WorkerRegistration,
    *,
    director_ids: frozenset[str],
    now: datetime,
    lease_seconds: int = 300,
    active_jobs_for_worker: int = 0,
    limits: WorkerLimits = DEFAULT_LIMITS,
) -> JobRecord:
    _aware(now, "Current time")
    _require_monotonic(now, record.updated_at, "job update time")
    if record.state is not JobState.QUEUED:
        raise ContractError("Only queued jobs can be leased")
    if record.authorization is None:
        raise ContractError("Director authorization is required before job execution")
    authorization = record.authorization
    _validate_authorization(
        authorization,
        manifest=record.manifest,
        director_ids=director_ids,
        now=now,
    )
    if now >= record.manifest.deadline_at:
        raise ContractError("Job deadline has expired")
    validate_worker_registration(worker, now=now, limits=limits)
    if worker.state is not WorkerState.AVAILABLE:
        raise ContractError("Worker is unavailable; queued jobs remain unleased")
    if record.manifest.workload not in worker.workloads:
        raise ContractError("Worker does not support this workload")
    if type(active_jobs_for_worker) is not int or active_jobs_for_worker < 0:
        raise ContractError("Active worker job count is invalid")
    if active_jobs_for_worker >= min(
        worker.max_concurrent_jobs,
        limits.max_concurrent_jobs,
        record.manifest.resources.max_concurrent_jobs,
    ):
        raise ContractError("Worker concurrency limit is reached")
    if record.manifest.workload is WorkloadKind.GPU_INFERENCE:
        if worker.vram_mb < record.manifest.resources.min_vram_mb:
            raise ContractError("Worker GPU capacity is below the job requirement")
    requirements = record.manifest.capabilities
    if (
        not set(requirements.operations) <= set(worker.operations)
        or not set(requirements.language_tags) <= set(worker.language_tags)
        or not set(requirements.model_ids) <= set(worker.model_ids)
    ):
        raise ContractError("Worker lacks a requested language/model capability")
    if type(lease_seconds) is not int or not limits.min_lease_seconds <= lease_seconds <= limits.max_lease_seconds:
        raise ContractError("Lease duration is outside the configured bounds")
    if record.attempt_count >= record.manifest.max_attempts:
        raise ContractError("Job retry limit is exhausted")
    lease = JobLease(
        lease_id=f"lease-{record.manifest.job_id}-{record.attempt_count + 1}",
        worker_id=worker.worker_id,
        attempt=record.attempt_count + 1,
        acquired_at=now,
        expires_at=min(
            now + timedelta(seconds=lease_seconds),
            authorization.expires_at,
            record.manifest.deadline_at,
            now + timedelta(seconds=record.manifest.resources.max_runtime_seconds),
        ),
        heartbeat_at=now,
    )
    if lease.expires_at <= now:
        raise ContractError("Lease cannot extend beyond authorization or job deadline")
    updated = replace(
        record,
        state=JobState.LEASED,
        attempt_count=lease.attempt,
        lease=lease,
        updated_at=now,
    )
    return _event(
        updated, "job_leased", worker.worker_id, now,
        {"lease_id": lease.lease_id, "attempt": lease.attempt, "expires_at": _iso(lease.expires_at)},
    )


def start_job(record: JobRecord, *, worker_id: str, now: datetime) -> JobRecord:
    _validate_live_lease(record, worker_id=worker_id, now=now)
    _require_monotonic(now, record.updated_at, "job update time")
    if record.state is not JobState.LEASED:
        raise ContractError("Only leased jobs can start")
    updated = replace(record, state=JobState.RUNNING, updated_at=now)
    return _event(updated, "job_running", worker_id, now, {"attempt": record.attempt_count})


def heartbeat_job(
    record: JobRecord,
    *,
    worker_id: str,
    now: datetime,
    lease_seconds: int = 300,
    limits: WorkerLimits = DEFAULT_LIMITS,
) -> JobRecord:
    _validate_live_lease(record, worker_id=worker_id, now=now)
    _require_monotonic(now, record.updated_at, "job update time")
    assert record.lease is not None
    _require_monotonic(now, record.lease.heartbeat_at, "lease heartbeat time")
    if record.state not in {JobState.LEASED, JobState.RUNNING}:
        raise ContractError("Only leased or running jobs can heartbeat")
    if type(lease_seconds) is not int or not limits.min_lease_seconds <= lease_seconds <= limits.max_lease_seconds:
        raise ContractError("Heartbeat lease duration is outside the configured bounds")
    authorization = record.authorization
    if authorization is None:
        raise ContractError("Director authorization is required for lease renewal")
    expires_at = min(
        now + timedelta(seconds=lease_seconds),
        authorization.expires_at,
        record.manifest.deadline_at,
        record.lease.acquired_at
        + timedelta(seconds=record.manifest.resources.max_runtime_seconds),
    )
    if expires_at <= now:
        raise ContractError("Job runtime or authorization window has expired")
    lease = replace(record.lease, heartbeat_at=now, expires_at=expires_at)
    updated = replace(record, lease=lease, updated_at=now)
    return _event(
        updated, "job_heartbeat", worker_id, now,
        {"lease_id": lease.lease_id, "expires_at": _iso(lease.expires_at)},
    )


def expire_lease(record: JobRecord, *, now: datetime) -> JobRecord:
    _aware(now, "Current time")
    _require_monotonic(now, record.updated_at, "job update time")
    if record.state not in {JobState.LEASED, JobState.RUNNING} or record.lease is None:
        raise ContractError("Only a leased or running job can expire")
    if now < record.lease.expires_at:
        raise ContractError("Lease has not expired")
    updated = replace(
        record, state=JobState.INTERRUPTED, lease=None, updated_at=now,
        last_error="worker_lease_expired",
    )
    return _event(
        updated, "job_interrupted", "local-recovery",
        now, {"attempt": record.attempt_count, "reason": "worker_lease_expired"},
    )


def recover_interrupted_job(record: JobRecord, *, now: datetime) -> JobRecord:
    _aware(now, "Current time")
    _require_monotonic(now, record.updated_at, "job update time")
    if record.state is not JobState.INTERRUPTED:
        raise ContractError("Only interrupted jobs can be recovered")
    if now >= record.manifest.deadline_at:
        updated = replace(record, state=JobState.FAILED, updated_at=now, last_error="deadline_expired")
        event = "job_failed"
    elif record.attempt_count >= record.manifest.max_attempts:
        updated = replace(record, state=JobState.FAILED, updated_at=now, last_error="retry_limit_exhausted")
        event = "job_failed"
    else:
        updated = replace(
            record,
            state=JobState.QUEUED,
            authorization=None,
            updated_at=now,
        )
        event = "job_requeued"
    return _event(
        updated, event, "local-recovery", now,
        {"attempt": record.attempt_count, "last_error": updated.last_error},
    )


def accept_result(record: JobRecord, result: JobResult, *, now: datetime) -> JobRecord:
    if not isinstance(result, JobResult):
        raise ContractError("Job result has an invalid schema")
    if type(result.attempt) is not int or not isinstance(result.outputs, tuple):
        raise ContractError("Job result attempt and output collection have invalid types")
    _aware(now, "Current time")
    if record.state is JobState.SUCCEEDED:
        raise ContractError("A result has already been accepted for this job")
    _validate_live_lease(record, worker_id=result.worker_id, now=now)
    _require_monotonic(now, record.updated_at, "job update time")
    if record.state is not JobState.RUNNING or record.lease is None:
        raise ContractError("Results are accepted only for a running job")
    if (
        result.job_id != record.manifest.job_id
        or result.lease_id != record.lease.lease_id
        or result.attempt != record.lease.attempt
    ):
        raise ContractError("Result is stale or belongs to a different lease or attempt")
    _identifier(result.result_id, "Result ID")
    _aware(result.completed_at, "Result completion time")
    if (
        result.completed_at < record.lease.acquired_at
        or result.completed_at > now
        or result.completed_at > record.lease.expires_at
    ):
        raise ContractError("Result completion is outside the active lease")
    declarations = {item.artifact_id: item for item in record.manifest.output_artifacts}
    if not isinstance(result.outputs, tuple) or len(result.outputs) != len(declarations):
        raise ContractError("Result must include every declared output exactly once")
    seen: set[str] = set()
    total_bytes = 0
    for artifact in result.outputs:
        validate_artifact_ref(artifact)
        declaration = declarations.get(artifact.artifact_id)
        if declaration is None or artifact.artifact_id in seen:
            raise ContractError("Result includes an undeclared or duplicate output")
        if (
            artifact.backend is not declaration.backend
            or artifact.key != declaration.key
            or artifact.media_type != declaration.media_type
            or artifact.size_bytes > declaration.max_bytes
        ):
            raise ContractError("Result output does not match its declaration")
        seen.add(artifact.artifact_id)
        total_bytes += artifact.size_bytes
    if total_bytes > record.manifest.resources.max_output_bytes:
        raise ContractError("Result exceeds the job output byte limit")
    updated = replace(
        record, state=JobState.SUCCEEDED, result=result, lease=None, updated_at=now,
    )
    return _event(
        updated, "job_succeeded", result.worker_id, now,
        {"result_id": result.result_id, "attempt": result.attempt,
         "output_artifact_ids": sorted(seen)},
    )


def fail_job(
    record: JobRecord, *, worker_id: str, error_code: str, now: datetime
) -> JobRecord:
    _validate_live_lease(record, worker_id=worker_id, now=now)
    _require_monotonic(now, record.updated_at, "job update time")
    if record.state not in {JobState.LEASED, JobState.RUNNING}:
        raise ContractError("Only leased or running jobs can fail")
    _identifier(error_code, "Failure code")
    updated = replace(
        record, state=JobState.FAILED, lease=None,
        last_error=error_code, updated_at=now,
    )
    return _event(
        updated, "job_failed", worker_id, now,
        {"attempt": record.attempt_count, "error_code": error_code},
    )


def retry_failed_job(
    record: JobRecord,
    *,
    director_id: str,
    director_ids: frozenset[str],
    now: datetime,
) -> JobRecord:
    _aware(now, "Current time")
    _require_monotonic(now, record.updated_at, "job update time")
    if director_id not in director_ids:
        raise ContractError("Only a configured Director can retry a failed job")
    if record.state is not JobState.FAILED:
        raise ContractError("Only failed jobs can be retried")
    if record.attempt_count >= record.manifest.max_attempts:
        raise ContractError("Job retry limit is exhausted")
    if now >= record.manifest.deadline_at:
        raise ContractError("Job deadline has expired")
    updated = replace(
        record,
        state=JobState.QUEUED,
        authorization=None,
        lease=None,
        result=None,
        updated_at=now,
    )
    return _event(
        updated, "job_retry_requested", director_id, now,
        {"attempt_count": record.attempt_count, "requires_new_authorization": True},
    )


def cancel_job(
    record: JobRecord,
    *,
    actor_id: str,
    director_ids: frozenset[str],
    now: datetime,
) -> JobRecord:
    _aware(now, "Current time")
    _require_monotonic(now, record.updated_at, "job update time")
    if actor_id not in director_ids:
        raise ContractError("Only a configured Director can cancel a job")
    if record.state not in {JobState.QUEUED, JobState.INTERRUPTED}:
        raise ContractError("Only queued or interrupted jobs can be cancelled")
    updated = replace(record, state=JobState.CANCELLED, lease=None, updated_at=now)
    return _event(updated, "job_cancelled", actor_id, now, {})


def _validate_live_lease(record: JobRecord, *, worker_id: str, now: datetime) -> None:
    _aware(now, "Current time")
    if record.lease is None or record.lease.worker_id != worker_id:
        raise ContractError("Worker does not own the current job lease")
    if now >= record.lease.expires_at:
        raise ContractError("Job lease has expired")


def _event(
    record: JobRecord, event_type: str, actor_id: str, now: datetime, details: dict[str, object]
) -> JobRecord:
    previous_hash = record.audit_events[-1].event_hash if record.audit_events else "0" * 64
    event_id = f"event-{len(record.audit_events) + 1}"
    details_json = json.dumps(details, sort_keys=True, separators=(",", ":"))
    event_hash = hashlib.sha256(
        f"{previous_hash}|{event_id}|{event_type}|{actor_id}|{_iso(now)}|{details_json}".encode("utf-8")
    ).hexdigest()
    event = AuditEvent(
        event_id=event_id,
        event_type=event_type,
        actor_id=actor_id,
        created_at=now,
        details_json=details_json,
        previous_hash=previous_hash,
        event_hash=event_hash,
    )
    return replace(record, audit_events=record.audit_events + (event,))


def _canonical(value: object) -> str:
    def encode(item: object) -> object:
        if isinstance(item, datetime):
            return _iso(item)
        if isinstance(item, StrEnum):
            return item.value
        if hasattr(item, "__dataclass_fields__"):
            return {name: encode(getattr(item, name)) for name in item.__dataclass_fields__}
        if isinstance(item, dict):
            return {str(key): encode(part) for key, part in item.items()}
        if isinstance(item, (tuple, list)):
            return [encode(part) for part in item]
        return item

    return json.dumps(encode(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()
