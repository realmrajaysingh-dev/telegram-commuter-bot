"""Offline contracts for a future authenticated remote-worker transport.

These validators operate on claims produced by a real, separately trusted
authentication verifier. They do not authenticate identities or signatures.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
import hashlib
import hmac
import re
from typing import Protocol

from .worker_contracts import ContractError


REMOTE_TRUST_SCHEMA_VERSION = 1
MAX_CREDENTIAL_LIFETIME = timedelta(minutes=15)
MAX_REQUEST_LIFETIME = timedelta(seconds=60)
MAX_CLOCK_SKEW = timedelta(seconds=30)
MAX_REMOTE_REQUEST_BODY_BYTES = 16 * 1024 * 1024
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_DIGEST = re.compile(r"^[a-f0-9]{64}$")
_TEXT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")


class RemoteWorkerScope(StrEnum):
    JOB_READ = "job:read"
    INPUT_READ = "input:read"
    LEASE_HEARTBEAT = "lease:heartbeat"
    RESULT_SUBMIT = "result:submit"


class RemoteWorkerOperation(StrEnum):
    JOB_READ = "job"
    INPUT_READ = "inputs"
    LEASE_HEARTBEAT = "heartbeat"
    RESULT_SUBMIT = "result"


_OPERATION_RULES = {
    RemoteWorkerOperation.JOB_READ: ("GET", RemoteWorkerScope.JOB_READ),
    RemoteWorkerOperation.INPUT_READ: ("GET", RemoteWorkerScope.INPUT_READ),
    RemoteWorkerOperation.LEASE_HEARTBEAT: (
        "POST",
        RemoteWorkerScope.LEASE_HEARTBEAT,
    ),
    RemoteWorkerOperation.RESULT_SUBMIT: ("POST", RemoteWorkerScope.RESULT_SUBMIT),
}


@dataclass(frozen=True)
class RemoteWorkerCredentialClaims:
    """Parsed claims from a credential already verified by a trusted provider."""

    schema_version: int
    credential_id: str
    issuer: str
    subject: str
    audience: str
    worker_id: str
    job_id: str
    project_id: str
    task_id: str
    lease_id: str
    scopes: tuple[RemoteWorkerScope, ...]
    issued_at: datetime
    expires_at: datetime


@dataclass(frozen=True)
class RemoteWorkerRequest:
    """Request metadata bound to exact resource, operation, and body bytes."""

    schema_version: int
    credential_id: str
    request_id: str
    operation: RemoteWorkerOperation
    method: str
    path: str
    job_id: str
    lease_id: str
    issued_at: datetime
    expires_at: datetime
    body_sha256: str


class RemoteRequestReplayStore(Protocol):
    """Required durable nonce store; no production implementation is provided."""

    def consume_once(
        self, credential_id: str, request_id: str, *, expires_at: datetime
    ) -> bool:
        """Atomically reject a previously consumed credential/request pair."""
        ...


class RemoteCredentialStatusStore(Protocol):
    """Required provider-backed revocation lookup; no implementation is provided."""

    def is_revoked(self, credential_id: str) -> bool:
        """Return whether the credential or its signing key has been revoked."""
        ...


def _aware(value: datetime, field: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ContractError(f"{field} must be a timezone-aware datetime")


def _identifier(value: object, field: str) -> None:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ContractError(f"{field} must be a bounded identifier")


def validate_remote_worker_claims(
    claims: RemoteWorkerCredentialClaims,
    *,
    now: datetime,
    expected_issuer: str,
    expected_audience: str,
    expected_worker_id: str,
    expected_job_id: str,
    expected_project_id: str,
    expected_task_id: str,
    expected_lease_id: str,
    lease_expires_at: datetime,
) -> None:
    """Validate bounded, job-scoped claims after external credential verification."""
    if not isinstance(claims, RemoteWorkerCredentialClaims):
        raise ContractError("Remote worker claims have an invalid schema")
    if type(claims.schema_version) is not int or claims.schema_version != REMOTE_TRUST_SCHEMA_VERSION:
        raise ContractError("Unsupported remote worker trust schema version")
    for value, field in (
        (claims.credential_id, "Credential ID"),
        (claims.worker_id, "Worker ID"),
        (claims.job_id, "Job ID"),
        (claims.project_id, "Project ID"),
        (claims.task_id, "Task ID"),
        (claims.lease_id, "Lease ID"),
    ):
        _identifier(value, field)
    for value, field in (
        (claims.issuer, "Credential issuer"),
        (claims.subject, "Credential subject"),
        (claims.audience, "Credential audience"),
    ):
        if not isinstance(value, str) or not _TEXT.fullmatch(value):
            raise ContractError(f"{field} is invalid")
    for value, field in (
        (now, "Current time"),
        (claims.issued_at, "Credential issue time"),
        (claims.expires_at, "Credential expiry"),
        (lease_expires_at, "Lease expiry"),
    ):
        _aware(value, field)
    if claims.issued_at > now + MAX_CLOCK_SKEW:
        raise ContractError("Credential issue time is too far in the future")
    if (
        claims.expires_at <= now
        or claims.expires_at <= claims.issued_at
        or claims.expires_at - claims.issued_at > MAX_CREDENTIAL_LIFETIME
        or claims.expires_at > lease_expires_at
    ):
        raise ContractError("Credential expiry is invalid or exceeds its lease")
    if claims.audience != expected_audience:
        raise ContractError("Credential audience does not match this control plane")
    if claims.issuer != expected_issuer:
        raise ContractError("Credential issuer is not trusted")
    for actual, expected, field in (
        (claims.worker_id, expected_worker_id, "worker"),
        (claims.job_id, expected_job_id, "job"),
        (claims.project_id, expected_project_id, "project"),
        (claims.task_id, expected_task_id, "task"),
        (claims.lease_id, expected_lease_id, "lease"),
    ):
        if actual != expected:
            raise ContractError(f"Credential {field} binding does not match the active lease")
    if (
        not isinstance(claims.scopes, tuple)
        or not claims.scopes
        or any(not isinstance(scope, RemoteWorkerScope) for scope in claims.scopes)
        or len(set(claims.scopes)) != len(claims.scopes)
    ):
        raise ContractError("Credential scopes must be a non-empty unique allowlist")


def validate_remote_worker_request(
    request: RemoteWorkerRequest,
    *,
    claims: RemoteWorkerCredentialClaims,
    body: bytes,
    now: datetime,
    expected_issuer: str,
    expected_audience: str,
    expected_worker_id: str,
    expected_job_id: str,
    expected_project_id: str,
    expected_task_id: str,
    expected_lease_id: str,
    lease_expires_at: datetime,
) -> None:
    """Validate request binding and freshness; this does not verify a signature."""
    validate_remote_worker_claims(
        claims,
        now=now,
        expected_issuer=expected_issuer,
        expected_audience=expected_audience,
        expected_worker_id=expected_worker_id,
        expected_job_id=expected_job_id,
        expected_project_id=expected_project_id,
        expected_task_id=expected_task_id,
        expected_lease_id=expected_lease_id,
        lease_expires_at=lease_expires_at,
    )
    if not isinstance(request, RemoteWorkerRequest):
        raise ContractError("Remote worker request has an invalid schema")
    if type(request.schema_version) is not int or request.schema_version != REMOTE_TRUST_SCHEMA_VERSION:
        raise ContractError("Unsupported remote worker request schema version")
    _identifier(request.credential_id, "Credential ID")
    _identifier(request.request_id, "Request ID")
    _identifier(request.job_id, "Request job ID")
    _identifier(request.lease_id, "Request lease ID")
    if not isinstance(request.operation, RemoteWorkerOperation):
        raise ContractError("Remote worker operation is invalid")
    if not isinstance(body, bytes) or len(body) > MAX_REMOTE_REQUEST_BODY_BYTES:
        raise ContractError("Authenticated request body is outside the package size limit")
    for value, field in (
        (now, "Current time"),
        (request.issued_at, "Request issue time"),
        (request.expires_at, "Request expiry"),
    ):
        _aware(value, field)
    expected_method, required_scope = _OPERATION_RULES[request.operation]
    expected_path = f"/v1/worker/jobs/{request.job_id}/{request.operation.value}"
    if request.method != expected_method or request.path != expected_path:
        raise ContractError("Request method or path does not match its operation")
    if (
        request.expires_at <= now
        or request.issued_at > now + MAX_CLOCK_SKEW
        or request.issued_at < claims.issued_at - MAX_CLOCK_SKEW
        or request.expires_at <= request.issued_at
        or request.expires_at - request.issued_at > MAX_REQUEST_LIFETIME
        or request.expires_at > claims.expires_at
        or request.expires_at > lease_expires_at
    ):
        raise ContractError(
            "Remote worker request is expired or exceeds its credential/lease window"
        )
    if request.credential_id != claims.credential_id:
        raise ContractError("Request credential ID does not match verified claims")
    if request.job_id != expected_job_id or request.lease_id != expected_lease_id:
        raise ContractError("Request resource does not match the active lease")
    if required_scope not in claims.scopes:
        raise ContractError("Credential is not scoped for this operation")
    if not isinstance(request.body_sha256, str) or not _DIGEST.fullmatch(request.body_sha256):
        raise ContractError("Request body SHA-256 is invalid")
    actual_digest = hashlib.sha256(body).hexdigest()
    if not hmac.compare_digest(request.body_sha256, actual_digest):
        raise ContractError("Request body SHA-256 does not match its bytes")


def consume_remote_request_once(
    store: RemoteRequestReplayStore,
    request: RemoteWorkerRequest,
) -> None:
    """Apply the replay-store contract after request validation, before mutation."""
    if not isinstance(request, RemoteWorkerRequest):
        raise ContractError("Remote worker request has an invalid schema")
    _identifier(request.credential_id, "Credential ID")
    _identifier(request.request_id, "Request ID")
    _aware(request.expires_at, "Request expiry")
    if not store.consume_once(
        request.credential_id, request.request_id, expires_at=request.expires_at
    ):
        raise ContractError("Remote worker request was already consumed")
