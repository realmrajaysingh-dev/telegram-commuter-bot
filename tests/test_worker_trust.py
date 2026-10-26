from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import unittest

from studio.worker_contracts import ContractError
from studio.worker_trust import (
    MAX_CREDENTIAL_LIFETIME,
    RemoteWorkerCredentialClaims,
    RemoteWorkerOperation,
    RemoteWorkerRequest,
    RemoteWorkerScope,
    consume_remote_request_once,
    validate_remote_worker_claims,
    validate_remote_worker_request,
)


NOW = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)
BODY = b'{"status":"synthetic-demo"}'
EXPECTED = {
    "expected_issuer": "test-issuer",
    "expected_audience": "voocks-local-control-plane",
    "expected_worker_id": "worker-demo",
    "expected_job_id": "job-demo",
    "expected_project_id": "project-demo",
    "expected_task_id": "task-demo",
    "expected_lease_id": "lease-demo",
    "lease_expires_at": NOW + timedelta(minutes=10),
}


def claims(**changes) -> RemoteWorkerCredentialClaims:
    value = RemoteWorkerCredentialClaims(
        schema_version=1,
        credential_id="credential-demo",
        issuer="test-issuer",
        subject="worker-subject",
        audience="voocks-local-control-plane",
        worker_id="worker-demo",
        job_id="job-demo",
        project_id="project-demo",
        task_id="task-demo",
        lease_id="lease-demo",
        scopes=(RemoteWorkerScope.RESULT_SUBMIT,),
        issued_at=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(minutes=5),
    )
    return replace(value, **changes)


def request(**changes) -> RemoteWorkerRequest:
    value = RemoteWorkerRequest(
        schema_version=1,
        credential_id="credential-demo",
        request_id="request-demo-001",
        operation=RemoteWorkerOperation.RESULT_SUBMIT,
        method="POST",
        path="/v1/worker/jobs/job-demo/result",
        job_id="job-demo",
        lease_id="lease-demo",
        issued_at=NOW,
        expires_at=NOW + timedelta(seconds=30),
        body_sha256=hashlib.sha256(BODY).hexdigest(),
    )
    return replace(value, **changes)


def validate_request(
    candidate: RemoteWorkerRequest | None = None,
    credential: RemoteWorkerCredentialClaims | None = None,
    body: bytes = BODY,
) -> None:
    validate_remote_worker_request(
        candidate or request(),
        claims=credential or claims(),
        body=body,
        now=NOW,
        **EXPECTED,
    )


class _ReplayStore:
    """Test-only store; production replay state must be transactionally durable."""

    def __init__(self) -> None:
        self.consumed: set[tuple[str, str]] = set()

    def consume_once(
        self, credential_id: str, request_id: str, *, expires_at: datetime
    ) -> bool:
        key = (credential_id, request_id)
        if key in self.consumed:
            return False
        self.consumed.add(key)
        return True


class RemoteWorkerTrustContractTests(unittest.TestCase):
    def test_valid_job_scoped_claims_and_request_are_accepted(self) -> None:
        validate_remote_worker_claims(claims(), now=NOW, **EXPECTED)
        validate_request()

    def test_credential_schema_expiry_and_lease_ceiling_are_enforced(self) -> None:
        with self.assertRaisesRegex(ContractError, "schema version"):
            validate_remote_worker_claims(
                claims(schema_version=2), now=NOW, **EXPECTED
            )
        with self.assertRaisesRegex(ContractError, "expiry"):
            validate_remote_worker_claims(
                claims(expires_at=NOW + MAX_CREDENTIAL_LIFETIME + timedelta(seconds=1)),
                now=NOW,
                **EXPECTED,
            )
        with self.assertRaisesRegex(ContractError, "lease"):
            validate_remote_worker_claims(
                claims(expires_at=EXPECTED["lease_expires_at"] + timedelta(seconds=1)),
                now=NOW,
                **EXPECTED,
            )

    def test_credentials_cannot_cross_worker_job_project_task_or_lease(self) -> None:
        for field, other in (
            ("worker_id", "another-worker"),
            ("job_id", "another-job"),
            ("project_id", "another-project"),
            ("task_id", "another-task"),
            ("lease_id", "another-lease"),
        ):
            with self.subTest(field=field):
                with self.assertRaisesRegex(ContractError, "binding"):
                    validate_remote_worker_claims(
                        claims(**{field: other}), now=NOW, **EXPECTED
                    )

    def test_credential_audience_scopes_and_claim_time_are_enforced(self) -> None:
        with self.assertRaisesRegex(ContractError, "audience"):
            validate_remote_worker_claims(
                claims(audience="other-service"), now=NOW, **EXPECTED
            )
        with self.assertRaisesRegex(ContractError, "issuer"):
            validate_remote_worker_claims(
                claims(issuer="untrusted-issuer"), now=NOW, **EXPECTED
            )
        with self.assertRaisesRegex(ContractError, "scope"):
            validate_request(credential=claims(scopes=(RemoteWorkerScope.JOB_READ,)))
        with self.assertRaisesRegex(ContractError, "future"):
            validate_remote_worker_claims(
                claims(issued_at=NOW + timedelta(minutes=1)),
                now=NOW,
                **EXPECTED,
            )

    def test_request_method_path_digest_and_resource_binding_are_enforced(self) -> None:
        for changed in (
            request(method="GET"),
            request(path="/v1/worker/jobs/another-job/result"),
            request(job_id="another-job"),
            request(lease_id="another-lease"),
            request(body_sha256="0" * 64),
        ):
            with self.subTest(request=changed):
                with self.assertRaises(ContractError):
                    validate_request(changed)
        with self.assertRaisesRegex(ContractError, "does not match its bytes"):
            validate_request(body=b"altered body")

    def test_request_expiry_future_skew_and_credential_expiry_are_enforced(self) -> None:
        with self.assertRaisesRegex(ContractError, "expired"):
            validate_request(request(expires_at=NOW))
        with self.assertRaisesRegex(ContractError, "window"):
            validate_request(
                request(expires_at=NOW + timedelta(seconds=61))
            )
        with self.assertRaisesRegex(ContractError, "window"):
            validate_request(
                request(issued_at=NOW + timedelta(minutes=1))
            )
        with self.assertRaisesRegex(ContractError, "window"):
            validate_request(
                request(expires_at=EXPECTED["lease_expires_at"] + timedelta(seconds=1))
            )
        with self.assertRaisesRegex(ContractError, "credential/lease window"):
            validate_request(
                request(expires_at=NOW + timedelta(seconds=30)),
                credential=claims(expires_at=NOW + timedelta(seconds=20)),
            )

    def test_replay_store_contract_rejects_duplicate_request_id(self) -> None:
        store = _ReplayStore()
        consume_remote_request_once(store, request())
        with self.assertRaisesRegex(ContractError, "already consumed"):
            consume_remote_request_once(store, request())
        with self.assertRaisesRegex(ContractError, "bounded identifier"):
            consume_remote_request_once(store, request(request_id="../bad"))

    def test_request_schema_and_body_size_are_bounded(self) -> None:
        with self.assertRaisesRegex(ContractError, "schema version"):
            validate_request(request(schema_version=2))
        with self.assertRaisesRegex(ContractError, "size limit"):
            validate_request(body=b"x" * (16 * 1024 * 1024 + 1))


if __name__ == "__main__":
    unittest.main()
