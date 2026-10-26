"""Portable, offline job/result packages for manually operated worker handoff."""

from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat
from typing import Any

from .models import parse_datetime
from .worker_contracts import (
    Accelerator,
    JobState,
    JobRecord,
    WorkloadKind,
    manifest_digest,
    validate_manifest,
)
from .worker_queue import WorkerJobQueue
from .worker_connector import (
    WorkerCapabilityProfile,
    match_job_to_worker,
)


HANDOFF_SCHEMA_VERSION = 1
MAX_PACKAGE_BYTES = 16 * 1024 * 1024
MAX_PACKAGE_OUTPUT_BYTES = 8 * 1024 * 1024
_PACKAGE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_ERROR_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_CAPABILITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_LANGUAGE = re.compile(r"^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")
_EXECUTION_NOTICE = (
    "Offline handoff only. No hardware, worker identity, or execution "
    "environment is authenticated by this package."
)


class WorkerHandoffError(ValueError):
    """Raised when an offline worker package is invalid or unauthorized."""


def _aware_now(now: datetime | None) -> datetime:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise WorkerHandoffError("Handoff time must include a timezone")
    return current


def _require_id(value: object, label: str) -> str:
    if not isinstance(value, str) or not _PACKAGE_ID.fullmatch(value):
        raise WorkerHandoffError(f"{label} is invalid")
    return value


def _json_bytes(value: object) -> bytes:
    try:
        encoded = json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise WorkerHandoffError("Package cannot be encoded as portable JSON") from exc
    if len(encoded) > MAX_PACKAGE_BYTES:
        raise WorkerHandoffError("Package exceeds the local handoff size limit")
    return encoded


def _reject_json_constant(value: str) -> None:
    raise WorkerHandoffError(f"Invalid JSON constant: {value}")


def encode_package(package: dict[str, Any]) -> bytes:
    return _json_bytes(package)


def decode_package(content: bytes) -> dict[str, Any]:
    if not isinstance(content, bytes) or not content or len(content) > MAX_PACKAGE_BYTES:
        raise WorkerHandoffError("Package must be non-empty and within the size limit")
    try:
        package = json.loads(
            content.decode("utf-8"),
            object_pairs_hook=_unique_object_pairs,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WorkerHandoffError("Package must contain valid UTF-8 JSON") from exc
    if not isinstance(package, dict):
        raise WorkerHandoffError("Package root must be a JSON object")
    return package


def _unique_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise WorkerHandoffError(f"Duplicate JSON field: {key}")
        value[key] = item
    return value


def write_package_file(path: str | Path, package: dict[str, Any]) -> Path:
    target = Path(path)
    if not target.name or target.name in {".", ".."}:
        raise WorkerHandoffError("Package file path must name a file")
    parent = target.parent.resolve(strict=True)
    if not parent.is_dir():
        raise WorkerHandoffError("Package parent path must be a directory")
    destination = parent / target.name
    content = encode_package(package)
    descriptor: int | None = None
    created = False
    try:
        descriptor = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        created = True
        with os.fdopen(descriptor, "wb") as output:
            descriptor = None
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
    except FileExistsError as exc:
        raise WorkerHandoffError("Package destination already exists") from exc
    except OSError as exc:
        if created:
            destination.unlink(missing_ok=True)
        raise WorkerHandoffError("Package file could not be written safely") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    return destination


def read_package_file(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    try:
        descriptor = os.open(target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as source:
            metadata = os.fstat(source.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_PACKAGE_BYTES:
                raise WorkerHandoffError("Package path must be a bounded regular file")
            return decode_package(source.read(MAX_PACKAGE_BYTES + 1))
    except WorkerHandoffError:
        raise
    except OSError as exc:
        raise WorkerHandoffError("Package file could not be read safely") from exc


def _profile_dict(profile: WorkerCapabilityProfile) -> dict[str, Any]:
    return {
        "profile_version": profile.profile_version,
        "worker_id": profile.worker_id,
        "availability": profile.availability,
        "workloads": [item.value for item in profile.workloads],
        "cpu_cores": profile.cpu_cores,
        "memory_mb": profile.memory_mb,
        "accelerators": [item.value for item in profile.accelerators],
        "vram_mb": profile.vram_mb,
        "processing_types": list(profile.processing_types),
        "operations": list(profile.operations),
        "language_tags": list(profile.language_tags),
        "model_ids": list(profile.model_ids),
        "expires_at": profile.expires_at.astimezone(timezone.utc).isoformat(),
        "is_synthetic": profile.is_synthetic,
        "provider_id": profile.provider_id,
    }


def _parse_profile(data: object) -> WorkerCapabilityProfile:
    fields = {
        "profile_version", "worker_id", "availability", "workloads",
        "cpu_cores", "memory_mb", "accelerators", "vram_mb",
        "processing_types", "operations", "language_tags", "model_ids",
        "expires_at", "is_synthetic", "provider_id",
    }
    if not isinstance(data, dict) or set(data) != fields:
        raise WorkerHandoffError("Worker capability profile fields are invalid")
    if type(data["profile_version"]) is not int or data["profile_version"] != 1:
        raise WorkerHandoffError("Unsupported worker capability profile version")
    worker_id = _require_id(data["worker_id"], "Worker ID")
    provider_id = data["provider_id"]
    if not isinstance(provider_id, str) or not _CAPABILITY.fullmatch(provider_id):
        raise WorkerHandoffError("Worker provider identifier is invalid")
    if not isinstance(data["availability"], str) or data["availability"] not in {
        "available", "registered", "gpu_unavailable", "disconnected"
    }:
        raise WorkerHandoffError("Worker availability value is invalid")
    list_fields = (
        "workloads", "accelerators", "processing_types", "operations",
        "language_tags", "model_ids",
    )
    if any(
        not isinstance(data[field], list)
        or any(not isinstance(item, str) for item in data[field])
        for field in list_fields
    ):
        raise WorkerHandoffError("Worker capability values must be string lists")
    if any(
        type(data[field]) is not int or data[field] < 0
        for field in ("cpu_cores", "memory_mb", "vram_mb")
    ):
        raise WorkerHandoffError("Worker resource declarations must be non-negative integers")
    if not 1 <= data["cpu_cores"] <= 64 or not 1 <= data["memory_mb"] <= 262144:
        raise WorkerHandoffError("Worker CPU or memory declaration is outside policy")
    if data["vram_mb"] > 262144 or type(data["is_synthetic"]) is not bool:
        raise WorkerHandoffError("Worker VRAM or provenance declaration is invalid")
    try:
        profile = WorkerCapabilityProfile(
            profile_version=1,
            worker_id=worker_id,
            availability=data["availability"],
            workloads=tuple(WorkloadKind(item) for item in data["workloads"]),
            cpu_cores=data["cpu_cores"],
            memory_mb=data["memory_mb"],
            accelerators=tuple(Accelerator(item) for item in data["accelerators"]),
            vram_mb=data["vram_mb"],
            processing_types=tuple(data["processing_types"]),
            operations=tuple(data["operations"]),
            language_tags=tuple(data["language_tags"]),
            model_ids=tuple(data["model_ids"]),
            expires_at=parse_datetime(data["expires_at"]),
            is_synthetic=data["is_synthetic"],
            provider_id=provider_id,
        )
    except (TypeError, ValueError, KeyError) as exc:
        raise WorkerHandoffError("Worker capability profile contains invalid values") from exc
    if (
        len(set(profile.workloads)) != len(profile.workloads)
        or len(set(profile.accelerators)) != len(profile.accelerators)
        or any(len(set(getattr(profile, field))) != len(getattr(profile, field))
               for field in ("processing_types", "operations", "language_tags", "model_ids"))
    ):
        raise WorkerHandoffError("Worker capability values must not contain duplicates")
    if not profile.workloads:
        raise WorkerHandoffError("Worker profile must declare at least one workload")
    if any(
        not _CAPABILITY.fullmatch(value)
        for values in (profile.processing_types, profile.operations, profile.model_ids)
        for value in values
    ) or any(not _LANGUAGE.fullmatch(value) for value in profile.language_tags):
        raise WorkerHandoffError("Worker profile contains malformed capability identifiers")
    return profile


def _package_from_record(
    queue: WorkerJobQueue,
    record: JobRecord,
    profile: WorkerCapabilityProfile,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    if record.authorization is None or record.lease is None:
        raise WorkerHandoffError("A persisted Director authorization and lease are required")
    return {
        "package_type": "voocks.worker.job",
        "schema_version": HANDOFF_SCHEMA_VERSION,
        "manifest": queue._manifest_dict(record.manifest),
        "manifest_sha256": manifest_digest(record.manifest),
        "authorization": queue._auth_dict(record.authorization),
        "lease": queue._lease_dict(record.lease),
        "worker_profile": _profile_dict(profile),
        "exported_at": (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(),
        "execution_notice": _EXECUTION_NOTICE,
    }


def validate_job_package(
    package: dict[str, Any],
    *,
    now: datetime | None = None,
    require_available: bool = True,
) -> dict[str, Any]:
    current = _aware_now(now)
    required = {
        "package_type", "schema_version", "manifest", "manifest_sha256",
        "authorization", "lease", "worker_profile", "exported_at",
        "execution_notice",
    }
    if not isinstance(package, dict) or set(package) != required:
        raise WorkerHandoffError("Job package fields are missing or unsupported")
    if package["package_type"] != "voocks.worker.job":
        raise WorkerHandoffError("Package is not a worker job export")
    if type(package["schema_version"]) is not int or package["schema_version"] != HANDOFF_SCHEMA_VERSION:
        raise WorkerHandoffError("Unsupported worker job package version")
    try:
        manifest = WorkerJobQueue._parse_manifest(json.dumps(package["manifest"]))
        validate_manifest(manifest)
    except (AttributeError, TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        raise WorkerHandoffError(f"Job manifest is invalid: {exc}") from exc
    if WorkerJobQueue._manifest_dict(manifest) != package["manifest"]:
        raise WorkerHandoffError("Job manifest contains unsupported or non-canonical fields")
    digest = manifest_digest(manifest)
    if (
        not isinstance(package["manifest_sha256"], str)
        or not _SHA256.fullmatch(package["manifest_sha256"])
        or not hmac.compare_digest(digest, package["manifest_sha256"])
    ):
        raise WorkerHandoffError("Job manifest checksum does not match its content")
    auth = package["authorization"]
    if not isinstance(auth, dict) or set(auth) != {
        "authorization_id", "director_id", "manifest_sha256", "authorized_at", "expires_at",
    }:
        raise WorkerHandoffError("Director authorization fields are invalid")
    _require_id(auth["authorization_id"], "Authorization ID")
    _require_id(auth["director_id"], "Director ID")
    try:
        authorized_at = parse_datetime(auth["authorized_at"])
        auth_expires = parse_datetime(auth["expires_at"])
    except (TypeError, ValueError) as exc:
        raise WorkerHandoffError("Director authorization timestamps are invalid") from exc
    if (
        auth["manifest_sha256"] != digest
        or authorized_at > current
        or auth_expires <= current
        or auth_expires <= authorized_at
        or authorized_at < manifest.created_at
        or auth_expires > manifest.deadline_at
    ):
        raise WorkerHandoffError("Director authorization is invalid or expired")
    lease = package["lease"]
    if not isinstance(lease, dict) or set(lease) != {
        "lease_id", "worker_id", "attempt", "acquired_at", "expires_at", "heartbeat_at",
    }:
        raise WorkerHandoffError("Worker lease fields are invalid")
    _require_id(lease["lease_id"], "Lease ID")
    worker_id = _require_id(lease["worker_id"], "Lease worker ID")
    if type(lease["attempt"]) is not int or not 1 <= lease["attempt"] <= manifest.max_attempts:
        raise WorkerHandoffError("Lease attempt is outside the manifest retry limit")
    try:
        acquired_at = parse_datetime(lease["acquired_at"])
        lease_expires = parse_datetime(lease["expires_at"])
        heartbeat_at = parse_datetime(lease["heartbeat_at"])
        exported_at = parse_datetime(package["exported_at"])
    except (TypeError, ValueError) as exc:
        raise WorkerHandoffError("Job package timestamps are invalid") from exc
    profile = _parse_profile(package["worker_profile"])
    if (
        acquired_at > current
        or heartbeat_at < acquired_at
        or heartbeat_at > current
        or heartbeat_at > lease_expires
        or acquired_at < authorized_at
        or lease_expires <= current
        or lease_expires <= acquired_at
        or lease_expires > manifest.deadline_at
        or lease_expires > auth_expires
        or lease_expires > profile.expires_at
        or exported_at < acquired_at
        or exported_at > current
    ):
        raise WorkerHandoffError("Job package lease or export window is invalid")
    if profile.worker_id != worker_id:
        raise WorkerHandoffError("Worker profile does not match the leased worker identity")
    if require_available and (
        profile.availability != "available" or current >= profile.expires_at
    ):
        raise WorkerHandoffError("Worker profile is unavailable or expired")
    match = match_job_to_worker(manifest, profile, now=current)
    if not match.hardware_compatible or (require_available and not match.available_for_lease):
        raise WorkerHandoffError(
            "Worker capability profile does not satisfy the job: "
            + ", ".join(match.reasons)
        )
    if package["execution_notice"] != _EXECUTION_NOTICE:
        raise WorkerHandoffError("Job package execution notice is invalid")
    return {"manifest": manifest, "profile": profile, "match": match}


def export_job_package(
    queue: WorkerJobQueue,
    job_id: str,
    *,
    worker_id: str,
    director_id: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = _aware_now(now)
    _require_id(job_id, "Job ID")
    _require_id(worker_id, "Worker ID")
    _require_id(director_id, "Director ID")
    record = queue.get_record(job_id)
    if record.state is not JobState.QUEUED or record.authorization is None:
        raise WorkerHandoffError("Only an authorized queued job can be exported")
    if record.authorization.director_id != director_id:
        raise WorkerHandoffError("Only the Director who authorized execution may export")
    profile = queue._worker_profile(worker_id)
    match = match_job_to_worker(record.manifest, profile, now=current)
    if not match.available_for_lease:
        raise WorkerHandoffError(
            "Selected worker is incompatible or unavailable: " + ", ".join(match.reasons)
        )
    queue.lease(job_id, worker_id=worker_id, now=current)
    queue.start(job_id, worker_id=worker_id, now=current)
    record = queue.get_record(job_id)
    package = _package_from_record(queue, record, profile, now=current)
    validate_job_package(package, now=current)
    encode_package(package)
    return package


def _result_context(package: dict[str, Any], *, now: datetime) -> dict[str, Any]:
    job = validate_job_package(package, now=now, require_available=False)
    manifest = job["manifest"]
    authorization = package["authorization"]
    lease = package["lease"]
    return {
        "job_id": manifest.job_id,
        "task_id": manifest.task_id,
        "project_id": manifest.project_id,
        "manifest_sha256": package["manifest_sha256"],
        "authorization_id": authorization["authorization_id"],
        "worker_id": lease["worker_id"],
        "lease_id": lease["lease_id"],
        "attempt": lease["attempt"],
        "manifest": manifest,
        "lease": lease,
        "profile": job["profile"],
    }


def create_result_package(
    job_package: dict[str, Any],
    outputs: tuple[dict[str, Any], ...] = (),
    *,
    status: str = "succeeded",
    error_code: str | None = None,
    is_synthetic: bool = True,
    completed_at: datetime | None = None,
    result_id: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = _aware_now(now)
    validate_job_package(job_package, now=current)
    context = _result_context(job_package, now=current)
    manifest = context["manifest"]
    timestamp = completed_at or current
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise WorkerHandoffError("Result completion time must include a timezone")
    if timestamp < parse_datetime(context["lease"]["acquired_at"]) or timestamp > current:
        raise WorkerHandoffError("Result completion time is outside the handoff window")
    if not isinstance(status, str) or status not in {"succeeded", "failed", "interrupted"}:
        raise WorkerHandoffError("Unsupported handoff result state")
    if type(is_synthetic) is not bool or is_synthetic != context["profile"].is_synthetic:
        raise WorkerHandoffError("Result provenance must match the worker profile")
    normalized_outputs: list[dict[str, Any]] = []
    if status == "succeeded":
        if error_code is not None:
            raise WorkerHandoffError("Successful results cannot include a failure code")
        declarations = {item.artifact_id: item for item in manifest.output_artifacts}
        if not isinstance(outputs, tuple) or len(outputs) != len(declarations):
            raise WorkerHandoffError("Successful result must contain all declared outputs")
        total_bytes = 0
        for output in outputs:
            if not isinstance(output, dict) or set(output) != {
                "artifact_id", "content",
            }:
                raise WorkerHandoffError("Output inputs require only artifact ID and bytes")
            declaration = declarations.get(output["artifact_id"])
            content = output["content"]
            if declaration is None or not isinstance(content, bytes) or not content:
                raise WorkerHandoffError("Output is missing, empty, or not declared")
            if len(content) > declaration.max_bytes or len(content) > MAX_PACKAGE_OUTPUT_BYTES:
                raise WorkerHandoffError("Output exceeds the handoff artifact size limit")
            total_bytes += len(content)
            digest = hashlib.sha256(content).hexdigest()
            normalized_outputs.append(
                {
                    "artifact_id": declaration.artifact_id,
                    "backend": declaration.backend.value,
                    "key": declaration.key,
                    "sha256": digest,
                    "size_bytes": len(content),
                    "media_type": declaration.media_type,
                    "content_base64": base64.b64encode(content).decode("ascii"),
                }
            )
        if total_bytes > min(manifest.resources.max_output_bytes, MAX_PACKAGE_OUTPUT_BYTES):
            raise WorkerHandoffError("Result exceeds the aggregate handoff size limit")
    else:
        if outputs or not isinstance(error_code, str) or not _ERROR_CODE.fullmatch(error_code):
            raise WorkerHandoffError("Failure result requires a bounded error code and no outputs")
    package = {
        "package_type": "voocks.worker.result",
        "schema_version": HANDOFF_SCHEMA_VERSION,
        "status": status,
        "job_id": context["job_id"],
        "task_id": context["task_id"],
        "project_id": context["project_id"],
        "manifest_sha256": context["manifest_sha256"],
        "authorization_id": context["authorization_id"],
        "worker_id": context["worker_id"],
        "lease_id": context["lease_id"],
        "attempt": context["attempt"],
        "completed_at": timestamp.astimezone(timezone.utc).isoformat(),
        "result_id": result_id or f"handoff-result-{hashlib.sha256(os.urandom(32)).hexdigest()[:32]}",
        "is_synthetic": is_synthetic,
        "error_code": error_code,
        "outputs": normalized_outputs,
    }
    validate_result_package(package, job_package=job_package, now=current)
    encode_package(package)
    return package


def validate_result_package(
    package: dict[str, Any],
    *,
    job_package: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    current = _aware_now(now)
    context = _result_context(job_package, now=current)
    required = {
        "package_type", "schema_version", "status", "job_id", "task_id",
        "project_id", "manifest_sha256", "authorization_id", "worker_id",
        "lease_id", "attempt", "completed_at", "result_id", "is_synthetic",
        "error_code", "outputs",
    }
    if not isinstance(package, dict) or set(package) != required:
        raise WorkerHandoffError("Result package fields are missing or unsupported")
    if package["package_type"] != "voocks.worker.result":
        raise WorkerHandoffError("Package is not a worker result")
    if type(package["schema_version"]) is not int or package["schema_version"] != HANDOFF_SCHEMA_VERSION:
        raise WorkerHandoffError("Unsupported worker result package version")
    for field in ("job_id", "task_id", "project_id", "worker_id", "lease_id", "result_id"):
        _require_id(package[field], field.replace("_", " ").title())
    for field in ("job_id", "task_id", "project_id", "manifest_sha256", "authorization_id", "worker_id", "lease_id", "attempt"):
        expected = context[field]
        if package[field] != expected:
            raise WorkerHandoffError(f"Result {field.replace('_', ' ')} does not match active job lease")
    if not _SHA256.fullmatch(package["manifest_sha256"]):
        raise WorkerHandoffError("Result manifest checksum is invalid")
    if type(package["attempt"]) is not int:
        raise WorkerHandoffError("Result attempt must be an integer")
    if type(package["is_synthetic"]) is not bool or package["is_synthetic"] != context["profile"].is_synthetic:
        raise WorkerHandoffError("Result provenance does not match the worker profile")
    try:
        completed_at = parse_datetime(package["completed_at"])
    except (TypeError, ValueError) as exc:
        raise WorkerHandoffError("Result completion timestamp is invalid") from exc
    if (
        completed_at < parse_datetime(context["lease"]["acquired_at"])
        or completed_at > current
        or completed_at > parse_datetime(context["lease"]["expires_at"])
    ):
        raise WorkerHandoffError("Result is stale or completed outside its lease")
    status = package["status"]
    if status not in {"succeeded", "failed", "interrupted"}:
        raise WorkerHandoffError("Unsupported worker result status")
    if status in {"failed", "interrupted"}:
        if package["outputs"] != [] or not isinstance(package["error_code"], str) or not _ERROR_CODE.fullmatch(package["error_code"]):
            raise WorkerHandoffError("Failure result has invalid outputs or error code")
        return {"status": status, "outputs": (), "completed_at": completed_at}
    if package["error_code"] is not None or not isinstance(package["outputs"], list):
        raise WorkerHandoffError("Successful result contains invalid failure metadata or outputs")
    declarations = {item.artifact_id: item for item in context["manifest"].output_artifacts}
    if len(package["outputs"]) != len(declarations):
        raise WorkerHandoffError("Result must contain every declared output exactly once")
    output_ids: set[str] = set()
    output_bytes: list[dict[str, Any]] = []
    total = 0
    for item in package["outputs"]:
        if not isinstance(item, dict) or set(item) != {
            "artifact_id", "backend", "key", "sha256", "size_bytes",
            "media_type", "content_base64",
        }:
            raise WorkerHandoffError("Result output fields are invalid")
        artifact_id = _require_id(item["artifact_id"], "Output artifact ID")
        declaration = declarations.get(artifact_id)
        if declaration is None or artifact_id in output_ids:
            raise WorkerHandoffError("Result output is undeclared or duplicated")
        output_ids.add(artifact_id)
        if (
            item["backend"] != declaration.backend.value
            or item["key"] != declaration.key
            or item["media_type"] != declaration.media_type
            or not isinstance(item["sha256"], str)
            or not _SHA256.fullmatch(item["sha256"])
            or type(item["size_bytes"]) is not int
            or not 0 < item["size_bytes"] <= min(declaration.max_bytes, MAX_PACKAGE_OUTPUT_BYTES)
            or not isinstance(item["content_base64"], str)
        ):
            raise WorkerHandoffError("Result artifact reference or checksum metadata is invalid")
        try:
            content = base64.b64decode(item["content_base64"], validate=True)
        except (ValueError, base64.binascii.Error) as exc:
            raise WorkerHandoffError("Result artifact bytes are not valid base64") from exc
        digest = hashlib.sha256(content).hexdigest()
        if (
            not content
            or len(content) != item["size_bytes"]
            or base64.b64encode(content).decode("ascii") != item["content_base64"]
            or not hmac.compare_digest(digest, item["sha256"])
        ):
            raise WorkerHandoffError("Result artifact size or SHA-256 does not match its bytes")
        total += len(content)
        output_bytes.append(
            {
                "artifact_id": artifact_id,
                "backend": item["backend"],
                "key": item["key"],
                "sha256": item["sha256"],
                "size_bytes": item["size_bytes"],
                "media_type": item["media_type"],
                "content": content,
            }
        )
    if total > min(context["manifest"].resources.max_output_bytes, MAX_PACKAGE_OUTPUT_BYTES):
        raise WorkerHandoffError("Result exceeds the aggregate handoff size limit")
    return {
        "status": "succeeded",
        "outputs": tuple(output_bytes),
        "completed_at": completed_at,
    }


def import_result_package(
    queue: WorkerJobQueue,
    package: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = _aware_now(now)
    encode_package(package)
    if not isinstance(package, dict) or not isinstance(package.get("job_id"), str):
        raise WorkerHandoffError("Result package must identify a worker job")
    _require_id(package["job_id"], "Job ID")
    queue.recover_expired_leases(now=current)
    record = queue.get_record(package["job_id"])
    if record.state is not JobState.RUNNING or record.authorization is None or record.lease is None:
        raise WorkerHandoffError("Result package requires an active authorized running job")
    profile = queue._worker_profile(record.lease.worker_id)
    if not profile.is_synthetic:
        raise WorkerHandoffError("Current local handoff accepts only labeled synthetic workers")
    trusted_job_package = _package_from_record(queue, record, profile, now=current)
    result = validate_result_package(
        package, job_package=trusted_job_package, now=current
    )
    if result["status"] == "succeeded":
        return queue.submit_result(
            record.manifest.job_id,
            worker_id=record.lease.worker_id,
            lease_id=record.lease.lease_id,
            attempt=record.lease.attempt,
            outputs=result["outputs"],
            result_id=package["result_id"],
            completed_at=result["completed_at"],
            now=current,
        )
    return queue.fail(
        record.manifest.job_id,
        worker_id=record.lease.worker_id,
        error_code=package["error_code"],
        now=current,
    )


def export_job_package_file(
    queue: WorkerJobQueue,
    path: str | Path,
    job_id: str,
    *,
    worker_id: str,
    director_id: str,
    now: datetime | None = None,
) -> Path:
    package = export_job_package(
        queue, job_id, worker_id=worker_id, director_id=director_id, now=now
    )
    try:
        return write_package_file(path, package)
    except WorkerHandoffError as exc:
        raise WorkerHandoffError(
            f"Job lease was created but its package could not be saved; allow lease expiry recovery: {exc}"
        ) from exc


def import_result_package_file(
    queue: WorkerJobQueue,
    path: str | Path,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    return import_result_package(queue, read_package_file(path), now=now)
