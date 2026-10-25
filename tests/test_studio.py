from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import tempfile
import unittest

from studio.models import (
    AssignmentManifest,
    ObserverAssessment,
    ObserverReport,
    ReviewGate,
    ReviewOutcome,
    StudioError,
    TaskRequest,
    TaskStatus,
)
from studio.registry import RegistryError, RoleRegistry
from studio.store import StudioStore


ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)
DIRECTOR_IDS = {"director-1"}
ARTIFACT_HASH = hashlib.sha256(b"synthetic test artifact").hexdigest()


def registry() -> RoleRegistry:
    return RoleRegistry.from_markdown(ROOT / "docs" / "AGENT_REGISTRY.md")


def manifest(
    *,
    assignment_id: str = "assignment-1",
    starts_at: datetime = NOW - timedelta(days=1),
    expires_at: datetime = NOW + timedelta(days=6),
    director_id: str = "director-1",
    spend: int = 0,
) -> AssignmentManifest:
    return AssignmentManifest(
        assignment_id=assignment_id,
        title="Synthetic local assignment",
        director_id=director_id,
        starts_at=starts_at,
        expires_at=expires_at,
        editorial_policy_id="policy-v1",
        authorized_spend_minor=spend,
    )


class StudioTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.store = StudioStore(
            Path(self.temporary_directory.name) / "studio.sqlite3",
            registry(),
            DIRECTOR_IDS,
        )
        self.store.create_assignment(manifest(), now=NOW)

    def tearDown(self) -> None:
        self.store.close()
        self.temporary_directory.cleanup()

    def create_task(self, task_id: str = "task-1", role_id: str = "D03-R03") -> None:
        role = self.store.registry.get(role_id)
        self.store.create_task(
            TaskRequest(
                task_id=task_id,
                assignment_id="assignment-1",
                title="Prepare synthetic draft",
                department_id=role.department_id,
                role_id=role_id,
            ),
            actor_id="operator-test",
            now=NOW,
        )

    def submit_task_for_review(self, task_id: str = "task-1") -> None:
        self.create_task(task_id)
        self.store.start_task(task_id, actor_id="worker-test", now=NOW)
        self.store.submit_for_review(
            task_id,
            actor_id="worker-test",
            artifact_hash=ARTIFACT_HASH,
            evidence_refs=("source_pack", "artifact"),
            now=NOW,
        )

    def add_passing_reviews(self, task_id: str = "task-1") -> None:
        reviewers = ("reviewer-source", "reviewer-editorial", "reviewer-technical", "reviewer-final")
        for gate, reviewer_id in zip(ReviewGate, reviewers, strict=True):
            self.store.record_review(
                task_id,
                gate=gate,
                outcome=ReviewOutcome.PASS,
                reviewer_id=reviewer_id,
                artifact_hash=ARTIFACT_HASH,
                evidence_refs=(f"{gate.value}-evidence",),
                now=NOW,
            )

    def add_clear_observers(self, task_id: str = "task-1") -> None:
        for observer_id in ("observer-policy", "observer-evidence"):
            self.store.submit_observer_report(
                task_id,
                ObserverReport(
                    observer_id=observer_id,
                    assessment=ObserverAssessment.CLEAR,
                    evidence_refs=(f"{observer_id}-check",),
                    finding="NO_BLOCKING_ISSUE",
                ),
                now=NOW,
            )


class RegistryTests(unittest.TestCase):
    def test_documented_registry_has_220_unique_roles_and_valid_dependencies(self) -> None:
        loaded = registry()
        self.assertEqual(len(loaded), 220)
        self.assertEqual(len(loaded.roles_for_department("D03")), 20)
        with self.assertRaises(RegistryError):
            loaded.get("D99-R99")


class AssignmentTests(StudioTestCase):
    def test_rejects_invalid_assignment_window_and_spend(self) -> None:
        with self.assertRaisesRegex(StudioError, "seven days"):
            self.store.create_assignment(
                manifest(
                    assignment_id="too-long",
                    expires_at=NOW + timedelta(days=7),
                ),
                now=NOW,
            )
        with self.assertRaisesRegex(StudioError, "Paid spending"):
            self.store.create_assignment(
                manifest(assignment_id="paid", spend=100),
                now=NOW,
            )
        with self.assertRaisesRegex(StudioError, "authorized Director"):
            self.store.create_assignment(
                manifest(assignment_id="unauthorized", director_id="untrusted"),
                now=NOW,
            )

    def test_rejects_expired_instruction(self) -> None:
        expired = manifest(
            assignment_id="expired",
            starts_at=NOW - timedelta(days=8),
            expires_at=NOW - timedelta(days=1),
        )
        with self.assertRaisesRegex(StudioError, "expired"):
            self.store.create_assignment(expired, now=NOW)

    def test_explicit_extension_is_limited_to_one_and_14_days(self) -> None:
        extended = self.store.extend_assignment(
            "assignment-1",
            director_id="director-1",
            new_expiry=NOW + timedelta(days=13),
            approved_at=NOW,
            now=NOW,
        )

    def test_assignment_manifest_round_trips_extension_record(self) -> None:
        extended = self.store.extend_assignment(
        "assignment-1",
        director_id="director-1",
        new_expiry=NOW + timedelta(days=13),
        approved_at=NOW,
        now=NOW,
        )
        restored = AssignmentManifest.from_json(json.dumps(extended.to_dict()))
        self.assertEqual(restored, extended)
        self.assertEqual(len(extended.extensions), 1)
        self.assertEqual(len(self.store.approvals("assignment-1")), 2)
        with self.assertRaisesRegex(StudioError, "extension limit"):
            self.store.extend_assignment(
                "assignment-1",
                director_id="director-1",
                new_expiry=NOW + timedelta(days=14),
                approved_at=NOW,
                now=NOW,
            )

    def test_rejects_assignment_beyond_forward_horizon(self) -> None:
        future_start = NOW + timedelta(days=9)
        with self.assertRaisesRegex(StudioError, "14-day forward horizon"):
            self.store.create_assignment(
                manifest(
                    assignment_id="too-far",
                    starts_at=future_start,
                    expires_at=future_start + timedelta(days=6),
                ),
                now=NOW,
            )

    def test_extension_cannot_exceed_forward_horizon(self) -> None:
        future_start = NOW + timedelta(days=1)
        self.store.create_assignment(
            manifest(
                assignment_id="future-assignment",
                starts_at=future_start,
                expires_at=future_start + timedelta(days=7),
            ),
            now=NOW,
        )
        with self.assertRaisesRegex(StudioError, "14-day forward horizon"):
            self.store.extend_assignment(
                "future-assignment",
                director_id="director-1",
                new_expiry=future_start + timedelta(days=14),
                approved_at=NOW,
                now=NOW,
            )


class TaskTests(StudioTestCase):
    def test_rejects_duplicate_task_ids(self) -> None:
        self.create_task()
        with self.assertRaisesRegex(StudioError, "Duplicate task ID"):
            self.create_task()

    def test_rejects_invalid_role_reference_and_department_mismatch(self) -> None:
        with self.assertRaisesRegex(StudioError, "Unknown role ID"):
            self.store.create_task(
                TaskRequest(
                    task_id="unknown-role",
                    assignment_id="assignment-1",
                    title="Bad role",
                    department_id="D03",
                    role_id="D99-R99",
                ),
                actor_id="operator-test",
                now=NOW,
            )
        with self.assertRaisesRegex(StudioError, "belongs to"):
            self.store.create_task(
                TaskRequest(
                    task_id="wrong-department",
                    assignment_id="assignment-1",
                    title="Wrong department",
                    department_id="D04",
                    role_id="D03-R03",
                ),
                actor_id="operator-test",
                now=NOW,
            )

    def test_routes_department_to_registered_roles(self) -> None:
        roles = self.store.route_by_department("D03")
        self.assertEqual(len(roles), 20)
        self.assertIn("D03-R03", roles)
        with self.assertRaisesRegex(StudioError, "Unknown department"):
            self.store.route_by_department("D99")

    def test_missing_evidence_blocks_review_submission(self) -> None:
        self.create_task()
        self.store.start_task("task-1", actor_id="worker-test", now=NOW)
        with self.assertRaisesRegex(StudioError, "Missing required evidence"):
            self.store.submit_for_review(
                "task-1",
                actor_id="worker-test",
                artifact_hash=ARTIFACT_HASH,
                evidence_refs=("artifact",),
                now=NOW,
            )

    def test_expired_instruction_cannot_route_or_approve_work(self) -> None:
        self.create_task()
        with self.assertRaisesRegex(StudioError, "expired"):
            self.store.start_task(
                "task-1",
                actor_id="worker-test",
                now=NOW + timedelta(days=7),
            )

    def test_approval_requires_assignment_to_remain_active(self) -> None:
        self.submit_task_for_review()
        self.add_passing_reviews()
        self.add_clear_observers()
        with self.assertRaisesRegex(StudioError, "expired"):
            self.store.approve_task(
                "task-1",
                director_id="director-1",
                artifact_hash=ARTIFACT_HASH,
                approved_at=NOW + timedelta(days=7),
            )

    def test_unauthorized_director_cannot_approve_task(self) -> None:
        self.submit_task_for_review()
        self.add_passing_reviews()
        self.add_clear_observers()
        with self.assertRaisesRegex(StudioError, "authorized Director"):
            self.store.approve_task(
                "task-1",
                director_id="not-the-director",
                artifact_hash=ARTIFACT_HASH,
                approved_at=NOW,
            )

    def test_four_independent_gates_and_director_approval_are_audited(self) -> None:
        self.submit_task_for_review()
        self.add_passing_reviews()
        self.add_clear_observers()
        self.store.approve_task(
            "task-1",
            director_id="director-1",
            artifact_hash=ARTIFACT_HASH,
            approved_at=NOW,
        )
        self.assertEqual(self.store.get_task("task-1")["status"], TaskStatus.APPROVED.value)
        self.assertEqual(len(self.store.approvals("task-1")), 1)
        self.assertTrue(self.store.verify_audit_chain())
        self.store.mark_complete("task-1", actor_id="operator-test", now=NOW)
        self.assertEqual(self.store.get_task("task-1")["status"], TaskStatus.COMPLETED.value)

    def test_observer_disagreement_blocks_until_director_resolution(self) -> None:
        self.submit_task_for_review()
        self.add_passing_reviews()
        reports = (
            ObserverReport(
                observer_id="observer-a",
                assessment=ObserverAssessment.CLEAR,
                evidence_refs=("check-a",),
                finding="EVIDENCE_MATCH",
            ),
            ObserverReport(
                observer_id="observer-b",
                assessment=ObserverAssessment.CONCERN,
                evidence_refs=("check-b",),
                finding="ASSIGNMENT_METADATA_UNCONFIRMED",
            ),
        )
        for report in reports:
            self.store.submit_observer_report("task-1", report, now=NOW)
        summary = self.store.observer_summary("task-1")
        self.assertTrue(summary["disagreement"])
        self.assertTrue(summary["blocked"])
        with self.assertRaisesRegex(StudioError, "not ready"):
            self.store.approve_task(
                "task-1",
                director_id="director-1",
                artifact_hash=ARTIFACT_HASH,
                approved_at=NOW,
            )
        with self.assertRaisesRegex(StudioError, "authorized Director"):
            self.store.resolve_observer_disagreement(
                "task-1",
                director_id="untrusted",
                disposition="ACCEPT_CONCERN",
                evidence_refs=("director-check",),
                now=NOW,
            )
        self.store.resolve_observer_disagreement(
            "task-1",
            director_id="director-1",
            disposition="METADATA_CONFIRMED",
            evidence_refs=("director-check",),
            now=NOW,
        )
        self.assertFalse(self.store.observer_summary("task-1")["blocked"])
        self.store.approve_task(
            "task-1",
            director_id="director-1",
            artifact_hash=ARTIFACT_HASH,
            approved_at=NOW,
        )
        self.assertEqual(self.store.get_task("task-1")["status"], TaskStatus.APPROVED.value)

    def test_blocking_observer_report_cannot_be_overridden(self) -> None:
        self.submit_task_for_review()
        self.store.submit_observer_report(
            "task-1",
            ObserverReport(
                observer_id="observer-block",
                assessment=ObserverAssessment.BLOCK,
                evidence_refs=("policy-violation",),
                finding="POLICY_VIOLATION",
            ),
            now=NOW,
        )
        with self.assertRaisesRegex(StudioError, "cannot be overridden"):
            self.store.resolve_observer_disagreement(
                "task-1",
                director_id="director-1",
                disposition="IGNORE_BLOCK",
                evidence_refs=("not-acceptable",),
                now=NOW,
            )

    def test_blocking_review_gate_cannot_be_overridden_by_observer_resolution(self) -> None:
        self.submit_task_for_review()
        self.store.record_review(
            "task-1",
            gate=ReviewGate.SOURCE_FACTUAL,
            outcome=ReviewOutcome.BLOCK,
            reviewer_id="reviewer-source",
            artifact_hash=ARTIFACT_HASH,
            evidence_refs=("unsupported-claim",),
            now=NOW,
        )
        for observer_id, assessment in (
            ("observer-a", ObserverAssessment.CLEAR),
            ("observer-b", ObserverAssessment.CONCERN),
        ):
            self.store.submit_observer_report(
                "task-1",
                ObserverReport(
                    observer_id=observer_id,
                    assessment=assessment,
                    evidence_refs=(f"{observer_id}-check",),
                    finding="REVIEW_BLOCK_REMAINS",
                ),
                now=NOW,
            )
        with self.assertRaisesRegex(StudioError, "review gate cannot be overridden"):
            self.store.resolve_observer_disagreement(
                "task-1",
                director_id="director-1",
                disposition="ACCEPT_CONCERN",
                evidence_refs=("director-check",),
                now=NOW,
            )

    def test_transient_failure_can_be_retried_with_audit_history(self) -> None:
        self.create_task()
        self.store.start_task("task-1", actor_id="worker-test", now=NOW)
        self.store.fail_task(
            "task-1",
            actor_id="worker-test",
            error_code="TRANSIENT_IO",
            retryable=True,
            now=NOW,
        )
        self.assertEqual(self.store.get_task("task-1")["status"], TaskStatus.FAILED.value)
        self.store.retry_task("task-1", actor_id="operator-test", now=NOW)
        task = self.store.get_task("task-1")
        self.assertEqual(task["status"], TaskStatus.QUEUED.value)
        self.assertEqual(task["attempts"], 1)
        self.assertIsNone(task["last_error_code"])
        self.store.start_task("task-1", actor_id="worker-test", now=NOW)
        self.assertTrue(self.store.verify_audit_chain())


if __name__ == "__main__":
    unittest.main()
