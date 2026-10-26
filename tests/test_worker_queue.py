from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import stat
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from studio.allocation import TaskAllocationEngine
from studio.dashboard import create_dashboard_server
from studio.issues import IssueEngine
from studio.pipeline import ProjectPipeline
from studio.registry import RoleRegistry
from studio.worker_contracts import JobState
from studio.worker_queue import WorkerJobQueue, WorkerQueueError


ROOT = Path(__file__).resolve().parents[1]
NOW = datetime.now(timezone.utc).replace(microsecond=0)
DIRECTOR_ID = "director-local"


class WorkerJobQueueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary_directory.name) / "studio.sqlite3"
        self.registry = RoleRegistry.from_markdown(ROOT / "docs" / "AGENT_REGISTRY.md")
        self.issues = IssueEngine(self.database, {DIRECTOR_ID})
        self.pipeline = ProjectPipeline(self.database, {DIRECTOR_ID}, self.registry)
        self.allocation = TaskAllocationEngine(
            self.database, self.registry, {DIRECTOR_ID}
        )
        self.project = self.pipeline.create_project(
            director_id=DIRECTOR_ID,
            title="Synthetic worker project",
            project_type="documentary",
            department_id="D03",
            language="hi-IN",
            priority="normal",
            role_id="D03-R03",
            project_id="worker-project",
            is_demo=True,
            now=NOW,
        )
        self.pipeline.transition_status(
            self.project["project_id"], "queued",
            director_id=DIRECTOR_ID, now=NOW,
        )
        plan = self.allocation.generate_task_plan(
            project_id=self.project["project_id"],
            director_id=DIRECTOR_ID,
            template="research_documentary",
            director_instructions="Synthetic-only worker test.",
            output_format="markdown",
            language="hi-IN",
            priority="normal",
            deadline_at=NOW + timedelta(days=2),
            now=NOW,
        )
        plan = self.allocation.approve_plan(
            plan["plan_id"], director_id=DIRECTOR_ID, now=NOW
        )
        self.task = next(
            item for item in plan["tasks"] if item["task_key"] == "research_scope"
        )
        self.allocation.start_task(
            self.task["task_id"], actor_id=self.task["role_id"], now=NOW
        )
        evidence = self.allocation.submit_evidence(
            self.task["task_id"],
            actor_id=self.task["role_id"],
            evidence_type=self.task["evidence_requirements"][0],
            content="Synthetic but persisted task evidence for queue tests.",
            now=NOW,
        )
        self.task = next(item for item in evidence["tasks"] if item["task_id"] == self.task["task_id"])
        self.queue = WorkerJobQueue(self.allocation, {DIRECTOR_ID})

    def tearDown(self) -> None:
        self.queue.close()
        self.allocation.close()
        self.pipeline.close()
        self.issues.close()
        self.temporary_directory.cleanup()

    def enqueue(self, workload: str = "gpu_inference", *, key: str = "queue-request"):
        return self.queue.enqueue_task(
            self.task["task_id"],
            director_id=DIRECTOR_ID,
            workload=workload,
            idempotency_key=key,
            now=NOW,
        )

    def register_worker(self, state: str = "available"):
        return self.queue.register_worker(
            director_id=DIRECTOR_ID,
            worker_id="simulated-worker",
            workloads=("cpu_ffmpeg", "gpu_inference"),
            state=state,
            expires_at=NOW + timedelta(hours=2),
            vram_mb=16000,
            operations=("transcribe",),
            language_tags=("hi-IN",),
            model_ids=("local-model-v1",),
            now=NOW,
        )

    def authorize(self, job):
        return self.queue.authorize(
            job["job_id"], director_id=DIRECTOR_ID, now=NOW
        )

    def output_payload(
        self,
        job,
        content: bytes = b"Synthetic persisted output fixture.",
        *,
        claimed_content: bytes | None = None,
    ):
        declaration = job["output_declarations"][0]
        claimed = content if claimed_content is None else claimed_content
        return {
            "artifact_id": declaration["artifact_id"],
            "backend": declaration["backend"],
            "key": declaration["key"],
            "sha256": hashlib.sha256(claimed).hexdigest(),
            "size_bytes": len(claimed),
            "media_type": declaration["media_type"],
            "content": content,
        }

    def test_enqueue_requires_active_task_approved_plan_and_valid_evidence(self) -> None:
        with self.assertRaisesRegex(WorkerQueueError, "configured Director"):
            self.queue.enqueue_task(
                self.task["task_id"],
                director_id="intruder",
                workload="gpu_inference",
                idempotency_key="unauthorized",
                now=NOW,
            )
        self.assertEqual(self.queue.snapshot()["counts"]["total"], 0)
        queued = self.enqueue()
        self.assertEqual(queued["state"], "queued")
        self.assertEqual(queued["project_id"], self.project["project_id"])
        self.assertEqual(queued["task_id"], self.task["task_id"])
        self.assertEqual(queued["workload"], "gpu_inference")
        self.assertEqual(queued["input_artifacts"][0]["sha256"], self.task["evidence_artifacts"][0]["sha256"])
        self.assertEqual(queued["output_verification"], "awaiting_verification")

    def test_queue_idempotency_and_conflicting_key_reuse(self) -> None:
        first = self.enqueue(key="same-request")
        replay = self.enqueue(key="same-request")
        self.assertEqual(first["job_id"], replay["job_id"])
        self.assertEqual(self.queue.snapshot()["counts"]["total"], 1)
        with self.assertRaisesRegex(WorkerQueueError, "different manifest"):
            self.enqueue("cpu_ffmpeg", key="same-request")

    def test_cpu_and_gpu_jobs_persist_separate_resource_profiles(self) -> None:
        cpu = self.enqueue("cpu_ffmpeg", key="cpu-profile")
        gpu = self.enqueue("gpu_inference", key="gpu-profile")
        self.assertEqual(cpu["resource_requirements"]["accelerator"], "none")
        self.assertEqual(cpu["resource_requirements"]["max_runtime_seconds"], 6 * 60 * 60)
        self.assertEqual(gpu["resource_requirements"]["accelerator"], "nvidia_t4")
        self.assertEqual(gpu["resource_requirements"]["min_vram_mb"], 8192)

    def test_job_and_worker_states_persist_after_queue_restart(self) -> None:
        job = self.enqueue()
        worker = self.register_worker()
        self.assertTrue(worker["is_simulation"])
        self.queue.close()
        self.queue = WorkerJobQueue(self.allocation, {DIRECTOR_ID})
        persisted = self.queue.get_job(job["job_id"])
        snapshot = self.queue.snapshot()
        self.assertEqual(persisted["state"], "queued")
        self.assertEqual(snapshot["workers"][0]["worker_id"], "simulated-worker")
        self.assertEqual(snapshot["available_worker_count"], 1)
        self.assertEqual(len(persisted["audit_events"]), 1)
        self.assertTrue(self.queue.verify_audit_chain(job["job_id"]))

    def test_authorization_and_manual_worker_lease_are_gated(self) -> None:
        job = self.enqueue()
        with self.assertRaisesRegex(WorkerQueueError, "authorization"):
            self.queue.lease(job["job_id"], worker_id="simulated-worker", now=NOW)
        with self.assertRaisesRegex(WorkerQueueError, "approving Director"):
            self.queue.authorize(job["job_id"], director_id="someone-else", now=NOW)
        self.authorize(job)
        self.register_worker("gpu_unavailable")
        with self.assertRaisesRegex(WorkerQueueError, "unavailable"):
            self.queue.lease(job["job_id"], worker_id="simulated-worker", now=NOW)
        self.queue.set_worker_state(
            "simulated-worker", director_id=DIRECTOR_ID, state="available", now=NOW
        )
        leased = self.queue.lease(
            job["job_id"], worker_id="simulated-worker", now=NOW
        )
        self.assertEqual(leased["state"], "leased")
        self.assertEqual(leased["lease"]["attempt"], 1)
        with self.assertRaisesRegex(WorkerQueueError, "Only queued"):
            self.queue.lease(job["job_id"], worker_id="simulated-worker", now=NOW)

    def test_heartbeat_failure_retry_and_new_authorization_are_audited(self) -> None:
        job = self.enqueue()
        self.authorize(job)
        self.register_worker()
        leased = self.queue.lease(
            job["job_id"], worker_id="simulated-worker", now=NOW
        )
        running = self.queue.start(
            job["job_id"], worker_id="simulated-worker",
            now=NOW + timedelta(seconds=1),
        )
        heartbeat = self.queue.heartbeat(
            job["job_id"], worker_id="simulated-worker",
            now=NOW + timedelta(seconds=30),
        )
        self.assertGreater(
            datetime.fromisoformat(heartbeat["lease"]["expires_at"]),
            datetime.fromisoformat(running["lease"]["expires_at"]),
        )
        failed = self.queue.fail(
            job["job_id"], worker_id="simulated-worker",
            error_code="synthetic_worker_error",
            now=NOW + timedelta(seconds=31),
        )
        self.assertEqual(failed["state"], "failed")
        self.assertEqual(failed["last_error"], "synthetic_worker_error")
        with self.assertRaisesRegex(WorkerQueueError, "approving Director"):
            self.queue.retry(job["job_id"], director_id="intruder", now=NOW + timedelta(seconds=32))
        retried = self.queue.retry(
            job["job_id"], director_id=DIRECTOR_ID, now=NOW + timedelta(seconds=32)
        )
        self.assertEqual(retried["state"], "queued")
        self.assertIsNone(retried["authorization"])
        self.assertEqual(retried["attempt_count"], 1)
        self.assertTrue(self.queue.verify_audit_chain(job["job_id"]))
        self.assertNotEqual(leased["lease"]["lease_id"], "")

    def test_expired_lease_is_recovered_without_losing_audit_history(self) -> None:
        job = self.enqueue()
        self.authorize(job)
        self.register_worker()
        leased = self.queue.lease(
            job["job_id"], worker_id="simulated-worker", now=NOW
        )
        expired_at = datetime.fromisoformat(leased["lease"]["expires_at"]) + timedelta(seconds=1)
        recovered = self.queue.recover_expired_leases(now=expired_at)
        self.assertEqual(len(recovered), 1)
        self.assertEqual(recovered[0]["state"], "queued")
        event_types = [item["event_type"] for item in recovered[0]["audit_events"]]
        self.assertEqual(event_types[-2:], ["job_interrupted", "job_requeued"])
        self.assertTrue(self.queue.verify_audit_chain(job["job_id"]))

    def test_dashboard_queue_restart_recovers_expired_lease_and_reauthorizes(self) -> None:
        job = self.enqueue()
        self.authorize(job)
        self.register_worker()
        leased = self.queue.lease(
            job["job_id"], worker_id="simulated-worker", now=NOW
        )
        restart_time = (
            datetime.fromisoformat(leased["lease"]["expires_at"])
            + timedelta(seconds=1)
        )
        self.queue.close()
        self.queue = WorkerJobQueue(
            self.allocation, {DIRECTOR_ID}, now=restart_time
        )
        recovered = self.queue.get_job(job["job_id"])
        self.assertEqual(recovered["state"], "queued")
        self.assertIsNone(recovered["lease"])
        self.assertIsNone(recovered["authorization"])
        self.assertEqual(
            [item["event_type"] for item in recovered["audit_events"]][-2:],
            ["job_interrupted", "job_requeued"],
        )
        self.assertTrue(self.queue.verify_audit_chain(job["job_id"]))

    def test_verified_output_succeeds_but_never_becomes_studio_evidence(self) -> None:
        job = self.enqueue()
        self.authorize(job)
        self.register_worker()
        self.queue.lease(job["job_id"], worker_id="simulated-worker", now=NOW)
        running = self.queue.start(
            job["job_id"], worker_id="simulated-worker",
            now=NOW + timedelta(seconds=1),
        )
        output = self.output_payload(job)
        completed = self.queue.submit_result(
            job["job_id"],
            worker_id="simulated-worker",
            lease_id=running["lease"]["lease_id"],
            attempt=1,
            outputs=(output,),
            result_id="result-synthetic",
            now=NOW + timedelta(seconds=2),
        )
        self.assertEqual(completed["state"], "succeeded")
        self.assertEqual(completed["output_verification"], "verified")
        self.assertEqual(completed["outputs"][0]["verification_status"], "verified")
        self.assertTrue(completed["outputs"][0]["is_synthetic"])
        self.assertEqual(completed["outputs"][0]["sha256"], output["sha256"])
        self.assertEqual(completed["audit_events"][-1]["event_type"], "outputs_verified")
        self.assertIn(output["sha256"], completed["audit_events"][-1]["details_json"])
        stored_file = (
            self.database.parent
            / f".{self.database.name}.worker-artifacts"
            / completed["outputs"][0]["storage_name"]
        )
        self.assertEqual(stored_file.read_bytes(), output["content"])
        self.assertEqual(stat.S_IMODE(stored_file.parent.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(stored_file.stat().st_mode), 0o600)
        with self.assertRaises(sqlite3.IntegrityError):
            self.queue._connection.execute(
                "UPDATE studio_worker_verified_outputs SET sha256='tampered' "
                "WHERE job_id=?",
                (job["job_id"],),
            )
        self.queue._connection.rollback()
        with self.assertRaises(sqlite3.IntegrityError):
            self.queue._connection.execute(
                "DELETE FROM studio_worker_output_checks WHERE job_id=?",
                (job["job_id"],),
            )
        self.queue._connection.rollback()
        task = next(
            item for item in self.allocation.get_plan_for_project(self.project["project_id"])["tasks"]
            if item["task_id"] == self.task["task_id"]
        )
        self.assertEqual(len(task["evidence_artifacts"]), 1)
        self.assertEqual(task["status"], "active")
        self.queue.close()
        self.queue = WorkerJobQueue(self.allocation, {DIRECTOR_ID})
        persisted = self.queue.get_job(job["job_id"])
        self.assertEqual(persisted["state"], "succeeded")
        self.assertEqual(persisted["output_verification"], "verified")
        self.assertTrue(self.queue.verify_audit_chain(job["job_id"]))
        with self.assertRaisesRegex(WorkerQueueError, "already accepted"):
            self.queue.submit_result(
                job["job_id"],
                worker_id="simulated-worker",
                lease_id=running["lease"]["lease_id"],
                attempt=1,
                outputs=(output,),
                result_id="duplicate-result",
                now=NOW + timedelta(seconds=3),
            )

    def test_stale_worker_result_and_wrong_output_are_rejected(self) -> None:
        job = self.enqueue()
        self.authorize(job)
        self.register_worker()
        self.queue.lease(job["job_id"], worker_id="simulated-worker", now=NOW)
        running = self.queue.start(
            job["job_id"], worker_id="simulated-worker",
            now=NOW + timedelta(seconds=1),
        )
        output = self.output_payload(job)
        output["key"] = "different/path.bin"
        with self.assertRaisesRegex(WorkerQueueError, "does not match"):
            self.queue.submit_result(
                job["job_id"], worker_id="simulated-worker",
                lease_id=running["lease"]["lease_id"], attempt=1,
                outputs=(output,), now=NOW + timedelta(seconds=2),
            )
        rejected = self.queue.get_job(job["job_id"])
        self.assertEqual(rejected["state"], "running")
        self.assertEqual(rejected["output_verification"], "failed")
        self.assertEqual(rejected["audit_events"][-1]["event_type"], "output_verification_failed")
        with self.assertRaisesRegex(WorkerQueueError, "stale"):
            self.queue.submit_result(
                job["job_id"], worker_id="simulated-worker",
                lease_id="old-lease", attempt=0,
                outputs=(output,), now=NOW + timedelta(seconds=2),
            )
        self.assertEqual(self.queue.get_job(job["job_id"])["state"], "running")

    def test_missing_output_is_rejected_then_valid_fixture_can_complete(self) -> None:
        job = self.enqueue()
        self.authorize(job)
        self.register_worker()
        self.queue.lease(job["job_id"], worker_id="simulated-worker", now=NOW)
        running = self.queue.start(
            job["job_id"], worker_id="simulated-worker",
            now=NOW + timedelta(seconds=1),
        )
        with self.assertRaisesRegex(WorkerQueueError, "missing"):
            self.queue.submit_result(
                job["job_id"], worker_id="simulated-worker",
                lease_id=running["lease"]["lease_id"], attempt=1,
                outputs=(), now=NOW + timedelta(seconds=2),
            )
        failed = self.queue.get_job(job["job_id"])
        self.assertEqual(failed["state"], "running")
        self.assertEqual(failed["output_verification"], "failed")
        self.assertEqual(failed["output_checks"][-1]["error_code"], "output_missing")
        completed = self.queue.submit_result(
            job["job_id"], worker_id="simulated-worker",
            lease_id=running["lease"]["lease_id"], attempt=1,
            outputs=(self.output_payload(job),), now=NOW + timedelta(seconds=3),
        )
        self.assertEqual(completed["state"], "succeeded")
        self.assertEqual(completed["output_verification"], "verified")
        self.assertTrue(self.queue.verify_audit_chain(job["job_id"]))

    def test_empty_oversized_and_checksum_tampered_outputs_are_rejected(self) -> None:
        job = self.enqueue()
        self.authorize(job)
        self.register_worker()
        self.queue.lease(job["job_id"], worker_id="simulated-worker", now=NOW)
        running = self.queue.start(
            job["job_id"], worker_id="simulated-worker",
            now=NOW + timedelta(seconds=1),
        )
        with self.assertRaisesRegex(WorkerQueueError, "empty"):
            self.queue.submit_result(
                job["job_id"], worker_id="simulated-worker",
                lease_id=running["lease"]["lease_id"], attempt=1,
                outputs=(self.output_payload(job, b""),), now=NOW + timedelta(seconds=2),
            )
        with self.assertRaisesRegex(WorkerQueueError, "checksum"):
            self.queue.submit_result(
                job["job_id"], worker_id="simulated-worker",
                lease_id=running["lease"]["lease_id"], attempt=1,
                outputs=(
                    self.output_payload(
                        job, b"tampered bytes", claimed_content=b"expected bytes"
                    ),
                ),
                now=NOW + timedelta(seconds=3),
            )
        declaration = job["output_declarations"][0]
        oversized = self.output_payload(job, b"x")
        oversized["size_bytes"] = declaration["max_bytes"] + 1
        with self.assertRaisesRegex(WorkerQueueError, "oversized"):
            self.queue.submit_result(
                job["job_id"], worker_id="simulated-worker",
                lease_id=running["lease"]["lease_id"], attempt=1,
                outputs=(oversized,), now=NOW + timedelta(seconds=4),
            )
        current = self.queue.get_job(job["job_id"])
        self.assertEqual(current["state"], "running")
        self.assertEqual(current["output_verification"], "failed")
        self.assertEqual(
            [check["status"] for check in current["output_checks"][-3:]],
            ["failed", "failed", "failed"],
        )
        self.assertTrue(self.queue.verify_audit_chain(job["job_id"]))

    def test_output_from_another_job_cannot_be_accepted(self) -> None:
        job = self.enqueue(key="owner-job")
        another = self.enqueue(key="other-owner-job")
        self.authorize(job)
        self.register_worker()
        self.queue.lease(job["job_id"], worker_id="simulated-worker", now=NOW)
        running = self.queue.start(
            job["job_id"], worker_id="simulated-worker",
            now=NOW + timedelta(seconds=1),
        )
        wrong = self.output_payload(another)
        with self.assertRaisesRegex(WorkerQueueError, "not declared"):
            self.queue.submit_result(
                job["job_id"], worker_id="simulated-worker",
                lease_id=running["lease"]["lease_id"], attempt=1,
                outputs=(wrong,), now=NOW + timedelta(seconds=2),
            )
        self.assertEqual(self.queue.get_job(job["job_id"])["state"], "running")
        self.assertEqual(self.queue.get_job(job["job_id"])["output_verification"], "failed")

    def test_verified_output_is_rechecked_after_restart_and_tampering(self) -> None:
        job = self.enqueue()
        self.authorize(job)
        self.register_worker()
        self.queue.lease(job["job_id"], worker_id="simulated-worker", now=NOW)
        running = self.queue.start(
            job["job_id"], worker_id="simulated-worker",
            now=NOW + timedelta(seconds=1),
        )
        completed = self.queue.submit_result(
            job["job_id"], worker_id="simulated-worker",
            lease_id=running["lease"]["lease_id"], attempt=1,
            outputs=(self.output_payload(job),), now=NOW + timedelta(seconds=2),
        )
        artifact_path = (
            self.database.parent
            / f".{self.database.name}.worker-artifacts"
            / completed["outputs"][0]["storage_name"]
        )
        orphan_staging = artifact_path.parent / "worker-output-staging-interrupted"
        orphan_result = artifact_path.parent / "job-orphan.output-orphan.1.bin"
        orphan_staging.write_bytes(b"partial synthetic bytes")
        orphan_result.write_bytes(b"uncommitted fixture")
        valid_content = self.output_payload(job)["content"]
        artifact_path.write_bytes(b"X" + valid_content[1:])
        self.queue.close()
        self.queue = WorkerJobQueue(
            self.allocation, {DIRECTOR_ID}, now=NOW + timedelta(seconds=3)
        )
        recovered = self.queue.get_job(job["job_id"])
        self.assertEqual(recovered["state"], "failed")
        self.assertEqual(recovered["output_verification"], "failed")
        self.assertEqual(recovered["last_error"], "persisted_output_hash_mismatch")
        self.assertFalse(orphan_staging.exists())
        self.assertFalse(orphan_result.exists())
        self.assertIn(
            "output_integrity_failed",
            [event["event_type"] for event in recovered["audit_events"]],
        )
        self.assertTrue(self.queue.verify_audit_chain(job["job_id"]))

    def test_verified_output_symlink_is_rejected_on_restart(self) -> None:
        job = self.enqueue()
        self.authorize(job)
        self.register_worker()
        self.queue.lease(job["job_id"], worker_id="simulated-worker", now=NOW)
        running = self.queue.start(
            job["job_id"], worker_id="simulated-worker",
            now=NOW + timedelta(seconds=1),
        )
        completed = self.queue.submit_result(
            job["job_id"], worker_id="simulated-worker",
            lease_id=running["lease"]["lease_id"], attempt=1,
            outputs=(self.output_payload(job),), now=NOW + timedelta(seconds=2),
        )
        artifact_directory = (
            self.database.parent / f".{self.database.name}.worker-artifacts"
        )
        artifact_path = artifact_directory / completed["outputs"][0]["storage_name"]
        external_path = self.database.parent / "outside-worker-artifact.txt"
        external_path.write_bytes(b"external file must remain untouched")
        artifact_path.unlink()
        artifact_path.symlink_to(external_path)
        self.queue.close()
        self.queue = WorkerJobQueue(
            self.allocation, {DIRECTOR_ID}, now=NOW + timedelta(seconds=3)
        )
        rejected = self.queue.get_job(job["job_id"])
        self.assertEqual(rejected["state"], "failed")
        self.assertEqual(rejected["output_verification"], "failed")
        self.assertEqual(external_path.read_bytes(), b"external file must remain untouched")

    def test_synthetic_worker_requires_authorization_and_runs_one_verified_job(self) -> None:
        job = self.enqueue()
        with self.assertRaisesRegex(WorkerQueueError, "Director authorization"):
            self.queue.run_synthetic_job(job["job_id"], now=NOW)
        self.assertEqual(self.queue.get_job(job["job_id"])["state"], "queued")
        with self.assertRaisesRegex(WorkerQueueError, "scenario"):
            self.queue.run_synthetic_job(
                job["job_id"], scenario="execute-payload", now=NOW
            )
        self.authorize(job)
        with self.assertRaisesRegex(WorkerQueueError, "No available"):
            self.queue.run_synthetic_job(job["job_id"], now=NOW)
        self.register_worker()
        completed = self.queue.run_synthetic_job(job["job_id"], now=NOW)
        self.assertEqual(completed["state"], "succeeded")
        self.assertEqual(completed["output_verification"], "verified")
        self.assertTrue(completed["outputs"][0]["is_synthetic"])
        self.assertEqual(completed["audit_events"][-1]["event_type"], "synthetic_worker_run")
        self.assertIn('"scenario":"success"', completed["audit_events"][-1]["details_json"])
        plan = self.allocation.get_plan_for_project(self.project["project_id"])
        task = next(item for item in plan["tasks"] if item["task_id"] == self.task["task_id"])
        self.assertEqual(task["status"], "active")
        with self.assertRaisesRegex(WorkerQueueError, "already accepted"):
            self.queue.run_synthetic_job(job["job_id"], now=NOW + timedelta(seconds=10))

    def test_synthetic_failure_and_director_retry_are_persisted(self) -> None:
        job = self.enqueue()
        self.authorize(job)
        self.register_worker()
        failed = self.queue.run_synthetic_job(
            job["job_id"], scenario="failure", now=NOW
        )
        self.assertEqual(failed["state"], "failed")
        self.assertEqual(failed["last_error"], "synthetic_worker_failure")
        self.assertEqual(failed["output_verification"], "awaiting_verification")
        self.assertEqual(failed["audit_events"][-1]["event_type"], "synthetic_worker_run")
        retry_at = NOW + timedelta(seconds=5)
        retried = self.queue.retry(
            job["job_id"], director_id=DIRECTOR_ID, now=retry_at
        )
        self.assertEqual(retried["state"], "queued")
        self.assertIsNone(retried["authorization"])
        reauthorized = self.queue.authorize(
            job["job_id"], director_id=DIRECTOR_ID,
            now=retry_at + timedelta(seconds=1),
        )
        self.assertIsNotNone(reauthorized["authorization"])
        completed = self.queue.run_synthetic_job(
            job["job_id"], now=retry_at + timedelta(seconds=2)
        )
        self.assertEqual(completed["state"], "succeeded")
        self.assertEqual(completed["attempt_count"], 2)
        self.assertEqual(completed["output_verification"], "verified")
        self.assertTrue(self.queue.verify_audit_chain(job["job_id"]))

    def test_synthetic_worker_cannot_run_against_non_demo_project(self) -> None:
        job = self.enqueue()
        self.authorize(job)
        self.register_worker()
        self.pipeline._connection.execute(
            "UPDATE studio_projects SET is_demo=0 WHERE project_id=?",
            (self.project["project_id"],),
        )
        self.pipeline._connection.commit()
        try:
            with self.assertRaisesRegex(WorkerQueueError, "demo projects"):
                self.queue.run_synthetic_job(job["job_id"], now=NOW)
            self.assertEqual(self.queue.get_job(job["job_id"])["state"], "queued")
        finally:
            self.pipeline._connection.execute(
                "UPDATE studio_projects SET is_demo=1 WHERE project_id=?",
                (self.project["project_id"],),
            )
            self.pipeline._connection.commit()

    def test_synthetic_interruption_recovers_after_expiry_and_restart(self) -> None:
        job = self.enqueue()
        self.authorize(job)
        self.register_worker()
        interrupted = self.queue.run_synthetic_job(
            job["job_id"], scenario="interruption", now=NOW
        )
        self.assertEqual(interrupted["state"], "queued")
        self.assertEqual(interrupted["attempt_count"], 1)
        self.assertIsNone(interrupted["authorization"])
        self.assertIsNone(interrupted["lease"])
        event_types = [event["event_type"] for event in interrupted["audit_events"]]
        self.assertIn("job_interrupted", event_types)
        self.assertIn("job_requeued", event_types)
        self.assertEqual(event_types[-1], "synthetic_worker_run")
        self.queue.close()
        self.queue = WorkerJobQueue(self.allocation, {DIRECTOR_ID})
        recovered = self.queue.get_job(job["job_id"])
        self.assertEqual(recovered["state"], "queued")
        self.assertIsNone(recovered["authorization"])
        next_run = datetime.fromisoformat(recovered["updated_at"]) + timedelta(seconds=1)
        self.queue.authorize(
            job["job_id"], director_id=DIRECTOR_ID, now=next_run
        )
        completed = self.queue.run_synthetic_job(
            job["job_id"], now=next_run + timedelta(seconds=1)
        )
        self.assertEqual(completed["state"], "succeeded")
        self.assertEqual(completed["attempt_count"], 2)
        self.assertEqual(completed["output_verification"], "verified")
        self.assertTrue(self.queue.verify_audit_chain(job["job_id"]))

    def test_cpu_and_gpu_synthetic_outputs_are_persisted_and_verified(self) -> None:
        self.register_worker()
        for index, workload in enumerate(("cpu_ffmpeg", "gpu_inference")):
            with self.subTest(workload=workload):
                job = self.enqueue(workload, key=f"synthetic-{workload}")
                self.authorize(job)
                start = NOW + timedelta(seconds=index * 20)
                self.queue.lease(
                    job["job_id"], worker_id="simulated-worker", now=start
                )
                running = self.queue.start(
                    job["job_id"], worker_id="simulated-worker",
                    now=start + timedelta(seconds=1),
                )
                verified = self.queue.submit_result(
                    job["job_id"], worker_id="simulated-worker",
                    lease_id=running["lease"]["lease_id"], attempt=1,
                    outputs=(self.output_payload(job, f"{workload} fixture".encode()),),
                    now=start + timedelta(seconds=2),
                )
                self.assertEqual(verified["output_verification"], "verified")
                self.assertTrue(verified["outputs"][0]["is_synthetic"])

    def test_task_evidence_and_observer_states_are_not_bypassed(self) -> None:
        job = self.enqueue()
        self.allocation.submit_task_for_review(
            self.task["task_id"], actor_id=self.task["role_id"], now=NOW
        )
        with self.assertRaisesRegex(WorkerQueueError, "no longer active"):
            self.authorize(job)
        self.assertEqual(self.queue.get_job(job["job_id"])["state"], "queued")

    def test_job_audit_and_output_reference_records_are_immutable(self) -> None:
        job = self.enqueue()
        with self.assertRaises(sqlite3.IntegrityError):
            self.queue._connection.execute(
                "UPDATE studio_worker_job_events SET event_type='tampered' WHERE job_id=?",
                (job["job_id"],),
            )

    def test_dashboard_exposes_persisted_jobs_and_manual_worker_simulation(self) -> None:
        server = create_dashboard_server(
            self.issues, host="127.0.0.1", port=0,
            pipeline=self.pipeline, allocation=self.allocation,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            with urlopen(f"{base}/") as response:
                page = response.read().decode("utf-8")
            self.assertIn("Persistent Worker Queue", page)
            self.assertIn("Submit Synthetic Output Fixture", page)
            self.assertIn("Run One Synthetic Job", page)
            self.assertIn("Output verification", page)
            worker_payload = json.dumps(
                {
                    "director_id": DIRECTOR_ID,
                    "worker_id": "dashboard-worker",
                    "workloads": ["gpu_inference", "cpu_ffmpeg"],
                    "state": "available",
                    "vram_mb": 12000,
                    "operations": [],
                    "language_tags": [],
                    "model_ids": [],
                    "ttl_seconds": 3600,
                }
            ).encode()
            request = Request(
                f"{base}/api/workers",
                data=worker_payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request) as response:
                worker_data = json.loads(response.read())
            self.assertTrue(worker_data["is_simulation"])
            queue_payload = json.dumps(
                {
                    "director_id": DIRECTOR_ID,
                    "workload": "gpu_inference",
                }
            ).encode()
            request = Request(
                f"{base}/api/worker-jobs/queue/{self.task['task_id']}",
                data=queue_payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request) as response:
                queued = json.loads(response.read())

            def post(path: str, payload: dict):
                request = Request(
                    f"{base}{path}",
                    data=json.dumps(payload).encode(),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request) as response:
                    return json.loads(response.read())

            job_path = f"/api/worker-jobs/{queued['job_id']}"
            post(
                job_path + "/authorize",
                {"director_id": DIRECTOR_ID},
            )
            post(
                job_path + "/lease",
                {"worker_id": "dashboard-worker", "lease_seconds": 300},
            )
            running = post(
                job_path + "/start",
                {"worker_id": "dashboard-worker"},
            )
            fixture = b"Synthetic dashboard GPU result fixture."
            declaration = running["output_declarations"][0]
            invalid_result_request = Request(
                f"{base}{job_path}/result",
                data=json.dumps(
                    {
                        "synthetic": True,
                        "worker_id": "dashboard-worker",
                        "lease_id": running["lease"]["lease_id"],
                        "attempt": running["lease"]["attempt"],
                        "outputs": [
                            {
                                "artifact_id": declaration["artifact_id"],
                                "backend": declaration["backend"],
                                "key": declaration["key"],
                                "sha256": hashlib.sha256(fixture).hexdigest(),
                                "size_bytes": len(fixture),
                                "media_type": declaration["media_type"],
                                "content_base64": "%%%invalid%%%",
                            }
                        ],
                    }
                ).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with self.assertRaises(HTTPError) as error:
                urlopen(invalid_result_request)
            self.assertEqual(error.exception.code, 400)
            error.exception.close()
            post(
                job_path + "/result",
                {
                    "synthetic": True,
                    "worker_id": "dashboard-worker",
                    "lease_id": running["lease"]["lease_id"],
                    "attempt": running["lease"]["attempt"],
                    "outputs": [
                        {
                            "artifact_id": declaration["artifact_id"],
                            "backend": declaration["backend"],
                            "key": declaration["key"],
                            "sha256": hashlib.sha256(fixture).hexdigest(),
                            "size_bytes": len(fixture),
                            "media_type": declaration["media_type"],
                            "content_base64": base64.b64encode(fixture).decode("ascii"),
                        }
                    ],
                },
            )
            with urlopen(f"{base}{job_path}") as response:
                verified_job = json.loads(response.read())
            self.assertEqual(verified_job["state"], "succeeded")
            self.assertEqual(verified_job["output_verification"], "verified")
            cpu_queue_payload = json.dumps(
                {
                    "director_id": DIRECTOR_ID,
                    "workload": "cpu_ffmpeg",
                }
            ).encode()
            request = Request(
                f"{base}/api/worker-jobs/queue/{self.task['task_id']}",
                data=cpu_queue_payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request) as response:
                cpu_job = json.loads(response.read())
            cpu_path = f"/api/worker-jobs/{cpu_job['job_id']}"
            post(cpu_path + "/authorize", {"director_id": DIRECTOR_ID})
            with self.assertRaises(HTTPError) as error:
                post(cpu_path + "/simulate", {"scenario": "arbitrary-payload"})
            self.assertEqual(error.exception.code, 400)
            error.exception.close()
            simulated = post(cpu_path + "/simulate", {"scenario": "success"})
            self.assertEqual(simulated["state"], "succeeded")
            self.assertEqual(simulated["output_verification"], "verified")
            self.assertEqual(simulated["audit_events"][-1]["event_type"], "synthetic_worker_run")
            self.assertTrue(simulated["outputs"][0]["is_synthetic"])

            with urlopen(f"{base}/api/worker-jobs") as response:
                snapshot = json.loads(response.read())
            self.assertEqual(snapshot["counts"]["succeeded"], 2)
            self.assertIn(
                queued["job_id"], [job["job_id"] for job in snapshot["jobs"]]
            )
            self.assertEqual(snapshot["workers"][0]["worker_id"], "dashboard-worker")
            with self.assertRaises(HTTPError) as error:
                request = Request(
                    f"{base}/api/worker-jobs/queue/{self.task['task_id']}",
                    data=b'{"director_id":"intruder","workload":"gpu_inference"}',
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                urlopen(request)
            self.assertEqual(error.exception.code, 400)
            error.exception.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
