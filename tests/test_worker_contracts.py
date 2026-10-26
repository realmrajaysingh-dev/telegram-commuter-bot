from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import unittest

from studio.worker_contracts import (
    Accelerator,
    ArtifactRef,
    CapabilityRequirements,
    ContractError,
    DirectorAuthorization,
    JobManifest,
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
    cancel_job,
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
)


NOW = datetime(2026, 10, 26, 8, 0, tzinfo=timezone.utc)
DIRECTOR_IDS = frozenset({"director-local"})
INPUT = ArtifactRef(
    artifact_id="input-video",
    backend=StorageBackend.LOCAL,
    key="projects/demo/input.mp4",
    sha256=hashlib.sha256(b"synthetic input").hexdigest(),
    size_bytes=1024,
    media_type="video/mp4",
)
OUTPUT_DECLARATION = OutputDeclaration(
    artifact_id="output-video",
    backend=StorageBackend.LOCAL,
    key="projects/demo/output.mp4",
    media_type="video/mp4",
    max_bytes=1024,
)


def manifest(**overrides) -> JobManifest:
    values = {
        "schema_version": 1,
        "job_id": "job-001",
        "idempotency_key": "request-001",
        "project_id": "project-001",
        "task_id": "task-001",
        "workload": WorkloadKind.GPU_INFERENCE,
        "input_artifacts": (INPUT,),
        "output_artifacts": (OUTPUT_DECLARATION,),
        "resources": ResourceRequirements(
            accelerator=Accelerator.NVIDIA_T4,
            cpu_cores=2,
            memory_mb=4096,
            min_vram_mb=8192,
            max_runtime_seconds=600,
            max_output_bytes=1024,
            max_storage_bytes=4096,
        ),
        "max_attempts": 2,
        "created_at": NOW,
        "deadline_at": NOW + timedelta(hours=4),
        "capabilities": CapabilityRequirements(
            operations=("transcribe",),
            language_tags=("hi", "ta-IN"),
            model_ids=("local-model-v1",),
        ),
    }
    values.update(overrides)
    return JobManifest(**values)


def worker(**overrides) -> WorkerRegistration:
    values = {
        "worker_id": "colab-session-01",
        "state": WorkerState.AVAILABLE,
        "workloads": (WorkloadKind.GPU_INFERENCE,),
        "registered_at": NOW - timedelta(minutes=1),
        "expires_at": NOW + timedelta(hours=4),
        "max_concurrent_jobs": 1,
        "vram_mb": 15_000,
        "operations": ("transcribe", "translate"),
        "language_tags": ("hi", "ta-IN", "en-IN"),
        "model_ids": ("local-model-v1",),
    }
    values.update(overrides)
    return WorkerRegistration(**values)


def authorized_record(job_manifest: JobManifest | None = None):
    item = job_manifest or manifest()
    record = create_job(item, now=NOW)
    authorization = DirectorAuthorization(
        authorization_id="auth-001",
        director_id="director-local",
        manifest_sha256=manifest_digest(item),
        authorized_at=NOW,
        expires_at=NOW + timedelta(hours=2),
    )
    return authorize_execution(
        record, authorization, director_ids=DIRECTOR_IDS, now=NOW
    )


def output_artifact() -> ArtifactRef:
    return ArtifactRef(
        artifact_id="output-video",
        backend=StorageBackend.LOCAL,
        key="projects/demo/output.mp4",
        sha256=hashlib.sha256(b"synthetic output").hexdigest(),
        size_bytes=800,
        media_type="video/mp4",
    )


class WorkerContractTests(unittest.TestCase):
    def test_manifest_accepts_versioned_t4_and_regional_language_metadata(self) -> None:
        value = manifest()
        validate_manifest(value)
        self.assertEqual(value.schema_version, 1)
        self.assertEqual(value.capabilities.language_tags, ("hi", "ta-IN"))
        self.assertEqual(value.workload, WorkloadKind.GPU_INFERENCE)

    def test_manifest_rejects_unsupported_version_and_invalid_checksums(self) -> None:
        with self.assertRaisesRegex(ContractError, "schema version"):
            validate_manifest(manifest(schema_version=2))
        with self.assertRaisesRegex(ContractError, "SHA-256"):
            validate_manifest(manifest(input_artifacts=(replace(INPUT, sha256="bad"),)))
        with self.assertRaisesRegex(ContractError, "Artifact reference"):
            validate_manifest(manifest(input_artifacts=(object(),)))

    def test_manifest_rejects_unsafe_storage_keys_and_remote_urls(self) -> None:
        for key in ("../secret.mp4", "/tmp/video.mp4", r"folder\\video.mp4", "https://example.test/a"):
            with self.subTest(key=key):
                bad_input = replace(INPUT, key=key)
                with self.assertRaises(ContractError):
                    validate_manifest(manifest(input_artifacts=(bad_input,)))
        with self.assertRaisesRegex(ContractError, "opaque file ID"):
            validate_manifest(
                manifest(
                    input_artifacts=(
                        replace(INPUT, backend=StorageBackend.GOOGLE_DRIVE, key="https://drive.google.com/file"),
                    )
                )
            )

    def test_manifest_prevents_output_from_overwriting_an_input(self) -> None:
        output = replace(
            OUTPUT_DECLARATION,
            artifact_id="output-copy",
            key=INPUT.key,
        )
        with self.assertRaisesRegex(ContractError, "overwrite an input"):
            validate_manifest(manifest(output_artifacts=(output,)))

    def test_cpu_ffmpeg_and_gpu_inference_require_distinct_resources(self) -> None:
        with self.assertRaisesRegex(ContractError, "cannot request GPU"):
            validate_manifest(manifest(workload=WorkloadKind.CPU_FFMPEG))
        cpu = manifest(
            workload=WorkloadKind.CPU_FFMPEG,
            resources=replace(
                manifest().resources,
                accelerator=Accelerator.NONE,
                min_vram_mb=0,
            ),
            capabilities=CapabilityRequirements(operations=("ffmpeg_transcode",)),
        )
        validate_manifest(cpu)

    def test_manifest_enforces_file_time_storage_and_retry_limits(self) -> None:
        with self.assertRaisesRegex(ContractError, "Execution time"):
            validate_manifest(
                manifest(resources=replace(manifest().resources, max_runtime_seconds=10_000))
            )
        with self.assertRaisesRegex(ContractError, "attempts"):
            validate_manifest(manifest(max_attempts=6))
        with self.assertRaisesRegex(ContractError, "output allocation"):
            validate_manifest(
                manifest(
                    output_artifacts=(
                        replace(OUTPUT_DECLARATION, max_bytes=2_000),
                    )
                )
            )

    def test_idempotent_enqueue_reuses_same_request_and_rejects_key_reuse(self) -> None:
        first = manifest()
        replay = manifest(job_id="job-002", created_at=NOW + timedelta(seconds=5))
        self.assertIs(ensure_idempotent_enqueue(first, replay), first)
        changed = manifest(task_id="task-999")
        with self.assertRaisesRegex(ContractError, "different manifest"):
            ensure_idempotent_enqueue(first, changed)

    def test_director_authorization_is_required_and_bound_to_manifest(self) -> None:
        queued = create_job(manifest(), now=NOW)
        with self.assertRaisesRegex(ContractError, "authorization"):
            lease_job(queued, worker(), director_ids=DIRECTOR_IDS, now=NOW)
        wrong_director = DirectorAuthorization(
            "auth-001", "not-a-director", manifest_digest(manifest()), NOW,
            NOW + timedelta(hours=1),
        )
        with self.assertRaisesRegex(ContractError, "configured Director"):
            authorize_execution(
                queued, wrong_director, director_ids=DIRECTOR_IDS, now=NOW
            )
        wrong_manifest = DirectorAuthorization(
            "auth-001", "director-local", "0" * 64, NOW,
            NOW + timedelta(hours=1),
        )
        with self.assertRaisesRegex(ContractError, "not bound"):
            authorize_execution(
                queued, wrong_manifest, director_ids=DIRECTOR_IDS, now=NOW
            )

    def test_gpu_unavailable_or_disconnected_worker_leaves_job_queued(self) -> None:
        authorized = authorized_record()
        for state in (WorkerState.GPU_UNAVAILABLE, WorkerState.DISCONNECTED):
            with self.subTest(state=state):
                unavailable = worker(state=state)
                with self.assertRaisesRegex(ContractError, "unavailable"):
                    lease_job(
                        authorized, unavailable, director_ids=DIRECTOR_IDS, now=NOW
                    )
                self.assertEqual(authorized.state, JobState.QUEUED)

    def test_worker_leasing_checks_capability_vram_and_concurrency(self) -> None:
        authorized = authorized_record()
        with self.assertRaisesRegex(ContractError, "capability"):
            lease_job(
                authorized,
                worker(model_ids=()),
                director_ids=DIRECTOR_IDS,
                now=NOW,
            )
        with self.assertRaisesRegex(ContractError, "GPU capacity"):
            lease_job(
                authorized,
                worker(vram_mb=4096),
                director_ids=DIRECTOR_IDS,
                now=NOW,
            )
        with self.assertRaisesRegex(ContractError, "concurrency"):
            lease_job(
                authorized,
                worker(),
                director_ids=DIRECTOR_IDS,
                now=NOW,
                active_jobs_for_worker=1,
            )

    def test_lease_heartbeat_expiry_and_retry_recovery_are_bounded(self) -> None:
        record = authorized_record()
        leased = lease_job(record, worker(), director_ids=DIRECTOR_IDS, now=NOW)
        self.assertEqual(leased.state, JobState.LEASED)
        running = start_job(leased, worker_id="colab-session-01", now=NOW + timedelta(seconds=1))
        renewed = heartbeat_job(
            running, worker_id="colab-session-01", now=NOW + timedelta(seconds=60)
        )
        self.assertGreater(renewed.lease.expires_at, running.lease.expires_at)
        interrupted = expire_lease(
            renewed, now=renewed.lease.expires_at + timedelta(seconds=1)
        )
        self.assertEqual(interrupted.state, JobState.INTERRUPTED)
        recovered = recover_interrupted_job(
            interrupted, now=interrupted.updated_at + timedelta(seconds=1)
        )
        self.assertEqual(recovered.state, JobState.QUEUED)
        self.assertEqual(recovered.attempt_count, 1)
        self.assertEqual(recovered.audit_events[-2].event_type, "job_interrupted")

    def test_interrupted_job_fails_after_retry_limit(self) -> None:
        record = authorized_record()
        for attempt in range(2):
            leased = lease_job(
                record, worker(), director_ids=DIRECTOR_IDS, now=record.updated_at
            )
            interrupted = expire_lease(
                leased,
                now=leased.lease.expires_at + timedelta(seconds=1),
            )
            record = recover_interrupted_job(
                interrupted, now=interrupted.updated_at + timedelta(seconds=1)
            )
            if attempt == 0:
                self.assertEqual(record.state, JobState.QUEUED)
                record = authorize_execution(
                    record,
                    DirectorAuthorization(
                        authorization_id="auth-002",
                        director_id="director-local",
                        manifest_sha256=manifest_digest(record.manifest),
                        authorized_at=record.updated_at,
                        expires_at=NOW + timedelta(hours=3),
                    ),
                    director_ids=DIRECTOR_IDS,
                    now=record.updated_at,
                )
        self.assertEqual(record.state, JobState.FAILED)
        self.assertEqual(record.last_error, "retry_limit_exhausted")

    def test_result_acceptance_rejects_stale_attempt_and_duplicate_result(self) -> None:
        record = authorized_record()
        first_lease = lease_job(record, worker(), director_ids=DIRECTOR_IDS, now=NOW)
        first_running = start_job(
            first_lease, worker_id="colab-session-01", now=NOW + timedelta(seconds=1)
        )
        interrupted = expire_lease(
            first_running, now=first_lease.lease.expires_at + timedelta(seconds=1)
        )
        recovered = recover_interrupted_job(
            interrupted, now=interrupted.updated_at + timedelta(seconds=1)
        )
        new_auth = DirectorAuthorization(
            authorization_id="auth-002",
            director_id="director-local",
            manifest_sha256=manifest_digest(recovered.manifest),
            authorized_at=recovered.updated_at,
            expires_at=NOW + timedelta(hours=3),
        )
        recovered = authorize_execution(
            recovered, new_auth, director_ids=DIRECTOR_IDS, now=recovered.updated_at
        )
        second_lease = lease_job(
            recovered,
            worker(),
            director_ids=DIRECTOR_IDS,
            now=recovered.updated_at,
        )
        running = start_job(
            second_lease,
            worker_id="colab-session-01",
            now=second_lease.updated_at + timedelta(seconds=1),
        )
        stale = JobResult(
            "result-old", "job-001", first_lease.lease.lease_id,
            "colab-session-01", 1, (output_artifact(),), running.updated_at,
        )
        with self.assertRaisesRegex(ContractError, "stale"):
            accept_result(running, stale, now=running.updated_at)
        early = replace(
            stale,
            result_id="result-early",
            lease_id=second_lease.lease.lease_id,
            attempt=2,
            completed_at=second_lease.lease.acquired_at - timedelta(seconds=1),
        )
        with self.assertRaisesRegex(ContractError, "outside the active lease"):
            accept_result(running, early, now=running.updated_at)
        valid = replace(
            stale,
            result_id="result-new",
            lease_id=second_lease.lease.lease_id,
            attempt=2,
            completed_at=running.updated_at,
        )
        succeeded = accept_result(running, valid, now=running.updated_at)
        self.assertEqual(succeeded.state, JobState.SUCCEEDED)
        with self.assertRaisesRegex(ContractError, "already been accepted"):
            accept_result(succeeded, valid, now=valid.completed_at)

    def test_result_must_match_declared_output_and_integrity_metadata(self) -> None:
        record = authorized_record()
        leased = lease_job(record, worker(), director_ids=DIRECTOR_IDS, now=NOW)
        running = start_job(
            leased, worker_id="colab-session-01", now=NOW + timedelta(seconds=1)
        )
        result = JobResult(
            "result-001", "job-001", leased.lease.lease_id, "colab-session-01",
            1, (replace(output_artifact(), key="other/path.mp4"),), running.updated_at,
        )
        with self.assertRaisesRegex(ContractError, "does not match"):
            accept_result(running, result, now=running.updated_at)

    def test_audit_events_are_append_only_hash_linked_contract_records(self) -> None:
        record = authorized_record()
        leased = lease_job(record, worker(), director_ids=DIRECTOR_IDS, now=NOW)
        self.assertEqual(
            leased.audit_events[-1].previous_hash,
            leased.audit_events[-2].event_hash,
        )
        self.assertEqual(len(leased.audit_events), len(record.audit_events) + 1)
        self.assertNotEqual(leased.audit_events[-1].event_hash, "0" * 64)

    def test_failed_job_retry_requires_director_and_remaining_attempt(self) -> None:
        record = authorized_record()
        leased = lease_job(record, worker(), director_ids=DIRECTOR_IDS, now=NOW)
        failed = fail_job(
            leased,
            worker_id="colab-session-01",
            error_code="worker_crash",
            now=NOW + timedelta(seconds=1),
        )
        with self.assertRaisesRegex(ContractError, "configured Director"):
            retry_failed_job(
                failed, director_id="other", director_ids=DIRECTOR_IDS,
                now=NOW + timedelta(seconds=2),
            )
        retried = retry_failed_job(
            failed, director_id="director-local", director_ids=DIRECTOR_IDS,
            now=NOW + timedelta(seconds=2),
        )
        self.assertEqual(retried.state, JobState.QUEUED)
        self.assertIsNone(retried.authorization)
        self.assertEqual(retried.audit_events[-1].event_type, "job_retry_requested")

    def test_only_director_can_cancel_queued_jobs(self) -> None:
        queued = create_job(manifest(), now=NOW)
        with self.assertRaisesRegex(ContractError, "configured Director"):
            cancel_job(
                queued,
                actor_id="worker",
                director_ids=DIRECTOR_IDS,
                now=NOW + timedelta(seconds=1),
            )
        cancelled = cancel_job(
            queued,
            actor_id="director-local",
            director_ids=DIRECTOR_IDS,
            now=NOW + timedelta(seconds=1),
        )
        self.assertEqual(cancelled.state, JobState.CANCELLED)
        self.assertEqual(cancelled.audit_events[-1].event_type, "job_cancelled")


if __name__ == "__main__":
    unittest.main()
