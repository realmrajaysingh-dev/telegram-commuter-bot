from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
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
        self.assertEqual(queued["output_verification"], "none")

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

    def test_result_metadata_succeeds_but_never_becomes_studio_evidence(self) -> None:
        job = self.enqueue()
        self.authorize(job)
        self.register_worker()
        self.queue.lease(job["job_id"], worker_id="simulated-worker", now=NOW)
        running = self.queue.start(
            job["job_id"], worker_id="simulated-worker",
            now=NOW + timedelta(seconds=1),
        )
        declaration = running["output_declarations"][0]
        output = {
            "artifact_id": declaration["artifact_id"],
            "backend": declaration["backend"],
            "key": declaration["key"],
            "sha256": hashlib.sha256(b"synthetic output metadata").hexdigest(),
            "size_bytes": 128,
            "media_type": declaration["media_type"],
        }
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
        self.assertEqual(completed["output_verification"], "unverified_metadata")
        self.assertEqual(completed["outputs"][0]["verification_status"], "unverified_metadata")
        with self.assertRaises(sqlite3.IntegrityError):
            self.queue._connection.execute(
                "UPDATE studio_worker_job_outputs SET verification_status='verified' "
                "WHERE job_id=?",
                (job["job_id"],),
            )
        task = next(
            item for item in self.allocation.get_plan_for_project(self.project["project_id"])["tasks"]
            if item["task_id"] == self.task["task_id"]
        )
        self.assertEqual(len(task["evidence_artifacts"]), 1)
        self.assertEqual(task["status"], "active")
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
        declaration = running["output_declarations"][0]
        output = {
            "artifact_id": declaration["artifact_id"],
            "backend": declaration["backend"],
            "key": "different/path.bin",
            "sha256": hashlib.sha256(b"bad output").hexdigest(),
            "size_bytes": 128,
            "media_type": declaration["media_type"],
        }
        with self.assertRaisesRegex(WorkerQueueError, "does not match"):
            self.queue.submit_result(
                job["job_id"], worker_id="simulated-worker",
                lease_id=running["lease"]["lease_id"], attempt=1,
                outputs=(output,), now=NOW + timedelta(seconds=2),
            )
        with self.assertRaisesRegex(WorkerQueueError, "stale"):
            self.queue.submit_result(
                job["job_id"], worker_id="simulated-worker",
                lease_id="old-lease", attempt=0,
                outputs=(output,), now=NOW + timedelta(seconds=2),
            )
        self.assertEqual(self.queue.get_job(job["job_id"])["state"], "running")

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
            self.assertIn("unverified metadata only", page)
            worker_payload = json.dumps(
                {
                    "director_id": DIRECTOR_ID,
                    "worker_id": "dashboard-worker",
                    "workloads": ["gpu_inference"],
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
            with urlopen(f"{base}/api/worker-jobs") as response:
                snapshot = json.loads(response.read())
            self.assertEqual(snapshot["counts"]["queued"], 1)
            self.assertEqual(snapshot["jobs"][0]["job_id"], queued["job_id"])
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
