from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest

from studio.models import (
    AssignmentManifest,
    ObserverAssessment,
    ObserverReport,
    ReviewGate,
    ReviewOutcome,
    TaskRequest,
)
from studio.observer import (
    FindingSeverity,
    ObserverEngine,
    ObserverError,
)
from studio.registry import RoleRegistry
from studio.store import StudioStore


ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)
DIRECTORS = {"director-1"}
ARTIFACT_HASH = hashlib.sha256(b"observer synthetic artifact").hexdigest()


def make_manifest(
    assignment_id: str = "observer-assignment",
    starts_at: datetime = NOW - timedelta(days=1),
    expires_at: datetime = NOW + timedelta(days=6),
) -> AssignmentManifest:
    return AssignmentManifest(
        assignment_id=assignment_id,
        title="Synthetic observer assignment",
        director_id="director-1",
        starts_at=starts_at,
        expires_at=expires_at,
        editorial_policy_id="policy-v1",
    )


class ObserverEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary_directory.name) / "observer.sqlite3"
        self.registry = RoleRegistry.from_markdown(ROOT / "docs" / "AGENT_REGISTRY.md")
        self.store = StudioStore(self.database, self.registry, DIRECTORS)
        self.store.create_assignment(make_manifest(), now=NOW)
        self.engine = ObserverEngine(self.database, self.registry, DIRECTORS)

    def tearDown(self) -> None:
        self.store.close()
        self.temporary_directory.cleanup()

    def create_task(
        self,
        task_id: str = "observer-task",
        *,
        title: str = "Synthetic task",
        actor_id: str = "operator-test",
        role_id: str = "D03-R03",
    ) -> None:
        role = self.registry.get(role_id)
        self.store.create_task(
            TaskRequest(
                task_id=task_id,
                assignment_id="observer-assignment",
                title=title,
                department_id=role.department_id,
                role_id=role_id,
            ),
            actor_id=actor_id,
            now=NOW,
        )

    def submit_for_review(self, *, actor_id: str = "operator-test") -> None:
        self.create_task(actor_id=actor_id)
        self.store.start_task("observer-task", actor_id="worker-test", now=NOW)
        self.store.submit_for_review(
            "observer-task",
            actor_id="worker-test",
            artifact_hash=ARTIFACT_HASH,
            evidence_refs=("source_pack", "artifact"),
            now=NOW,
        )

    def report(self):
        return self.engine.inspect(now=NOW)

    def codes(self, report=None) -> set[str]:
        current = report or self.report()
        return {finding.code for finding in current.findings}

    def tamper_manifest(self, update) -> None:
        row = self.store._connection.execute(
            "SELECT manifest_json FROM assignments WHERE assignment_id = ?",
            ("observer-assignment",),
        ).fetchone()
        value = json.loads(row["manifest_json"])
        update(value)
        with self.store._connection:
            self.store._connection.execute(
                "UPDATE assignments SET manifest_json = ? WHERE assignment_id = ?",
                (json.dumps(value, sort_keys=True, separators=(",", ":")), "observer-assignment"),
            )

    def test_clear_ledger_is_read_only_and_reports_no_findings(self) -> None:
        self.create_task()
        before_audit = self.store.audit_events()
        before_task = self.store.get_task("observer-task")
        before_reports = self.store._connection.execute(
            "SELECT COUNT(*) FROM observer_reports"
        ).fetchone()[0]
        report = self.report()
        self.assertEqual(report.findings, ())
        self.assertTrue(report.is_clear)
        self.assertEqual(self.store.audit_events(), before_audit)
        self.assertEqual(self.store.get_task("observer-task"), before_task)
        self.assertEqual(
            self.store._connection.execute("SELECT COUNT(*) FROM observer_reports").fetchone()[0],
            before_reports,
        )

    def test_assignment_policy_observer_detects_invalid_director_or_spend(self) -> None:
        self.tamper_manifest(
            lambda value: value.update(
                {"director_id": "unknown-director", "authorized_spend_minor": 10}
            )
        )
        report = self.report()
        self.assertIn("ASSIGNMENT_POLICY_INVALID", self.codes(report))
        finding = report.by_code("ASSIGNMENT_POLICY_INVALID")[0]
        self.assertEqual(finding.observer_id, "observer-assignment-policy")
        self.assertEqual(finding.severity, FindingSeverity.BLOCK)

    def test_assignment_horizon_observer_checks_seven_and_fourteen_days(self) -> None:
        self.tamper_manifest(
            lambda value: value.update(
                {"expires_at": (NOW + timedelta(days=8)).isoformat()}
            )
        )
        codes = self.codes()
        self.assertIn("ASSIGNMENT_SEVEN_DAY_WINDOW_VIOLATION", codes)

        self.tamper_manifest(
            lambda value: value.update(
                {"expires_at": (NOW + timedelta(days=15)).isoformat()}
            )
        )
        self.assertIn("ASSIGNMENT_FOURTEEN_DAY_HORIZON_VIOLATION", self.codes())

        self.tamper_manifest(
            lambda value: value.update(
                {
                    "expires_at": (NOW + timedelta(days=14)).isoformat(),
                    "extensions": [
                        {
                            "approved_by": "director-1",
                            "approved_at": NOW.isoformat(),
                            "previous_expiry": (NOW + timedelta(days=6)).isoformat(),
                            "new_expiry": (NOW + timedelta(days=14)).isoformat(),
                        }
                    ],
                }
            )
        )
        self.assertIn("ASSIGNMENT_EXTENSION_WINDOW_VIOLATION", self.codes())

    def test_assignment_horizon_observer_reports_expired_assignment(self) -> None:
        self.tamper_manifest(
            lambda value: value.update(
                {
                    "starts_at": (NOW - timedelta(days=9)).isoformat(),
                    "expires_at": (NOW - timedelta(days=1)).isoformat(),
                }
            )
        )
        finding = self.report().by_code("ASSIGNMENT_EXPIRED")
        self.assertEqual(len(finding), 1)
        self.assertEqual(finding[0].severity, FindingSeverity.WARNING)

    def test_duplicate_task_observer_detects_duplicate_requests(self) -> None:
        self.create_task(title="  Same   synthetic task ")
        self.create_task("observer-task-copy", title="same synthetic task")
        finding = self.report().by_code("DUPLICATE_TASK_REQUEST")
        self.assertEqual(len(finding), 1)
        self.assertEqual(finding[0].evidence_refs, ("role:D03-R03", "task:observer-task", "task:observer-task-copy"))

    def test_assignment_policy_observer_detects_invalid_role_and_department(self) -> None:
        self.create_task()
        with self.store._connection:
            self.store._connection.execute(
                "UPDATE tasks SET role_id = ?, department_id = ? WHERE task_id = ?",
                ("D99-R99", "D04", "observer-task"),
            )
        codes = self.codes()
        self.assertIn("TASK_ROLE_UNKNOWN", codes)

        with self.store._connection:
            self.store._connection.execute(
                "UPDATE tasks SET role_id = ?, department_id = ? WHERE task_id = ?",
                ("D03-R03", "D04", "observer-task"),
            )
        self.assertIn("TASK_DEPARTMENT_MISMATCH", self.codes())

    def test_assignment_approval_observer_detects_missing_assignment_approval(self) -> None:
        with self.store._connection:
            self.store._connection.execute(
                "DELETE FROM director_approvals WHERE object_id = ?",
                ("observer-assignment",),
            )
        self.assertIn("ASSIGNMENT_APPROVAL_MISSING_OR_MISMATCHED", self.codes())

    def test_missing_task_release_approval_is_detected(self) -> None:
        self.create_task()
        with self.store._connection:
            self.store._connection.execute(
                "UPDATE tasks SET status = ?, artifact_hash = ? WHERE task_id = ?",
                ("approved", ARTIFACT_HASH, "observer-task"),
            )
        finding = self.report().by_code("TASK_RELEASE_APPROVAL_MISSING_OR_MISMATCHED")
        self.assertEqual(len(finding), 1)
        self.assertEqual(finding[0].severity, FindingSeverity.BLOCK)

    def test_unauthorized_publishing_state_and_event_are_detected(self) -> None:
        self.create_task()
        with self.store._connection:
            self.store._connection.execute(
                "UPDATE tasks SET status = ? WHERE task_id = ?",
                ("published", "observer-task"),
            )
        findings = self.report().by_code("UNAUTHORIZED_PUBLISH_STATE")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, FindingSeverity.BLOCK)

    def test_unauthorized_platform_delivery_event_is_detected(self) -> None:
        with self.store._connection:
            self.store._connection.execute(
                """
                INSERT INTO audit_events
                    (event_type, object_id, actor_id, event_json, previous_hash,
                     event_hash, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "telegram_send_photo",
                    "observer-assignment",
                    "operator-test",
                    "{}",
                    "0" * 64,
                    "1" * 64,
                    NOW.isoformat(),
                ),
            )
        self.assertIn("UNAUTHORIZED_PUBLISH_EVENT", self.codes())

    def test_audit_consistency_observer_detects_tampered_event(self) -> None:
        self.create_task()
        self.assertTrue(self.store.verify_audit_chain())
        with self.store._connection:
            self.store._connection.execute(
                "UPDATE audit_events SET event_json = ? WHERE sequence = 1",
                ('{"tampered":true}',),
            )
        report = self.report()
        self.assertIn("AUDIT_EVENT_HASH_MISMATCH", self.codes(report))
        self.assertIn("AUDIT_PREVIOUS_HASH_MISMATCH", self.codes(report))

    def test_failed_task_observer_reports_retryable_and_terminal_failures(self) -> None:
        self.create_task()
        self.store.start_task("observer-task", actor_id="worker-test", now=NOW)
        self.store.fail_task(
            "observer-task",
            actor_id="worker-test",
            error_code="TRANSIENT_IO",
            retryable=True,
            now=NOW,
        )
        self.assertIn("TASK_RETRYABLE_FAILURE", self.codes())

        with self.store._connection:
            self.store._connection.execute(
                "UPDATE tasks SET retryable = 0, attempts = max_attempts WHERE task_id = ?",
                ("observer-task",),
            )
        finding = self.report().by_code("TASK_FAILURE_REQUIRES_MANUAL_REVIEW")
        self.assertEqual(len(finding), 1)
        self.assertEqual(finding[0].severity, FindingSeverity.BLOCK)

    def test_disagreement_observer_reports_conflicting_assessments(self) -> None:
        self.submit_for_review()
        for observer_id, assessment, finding_code in (
            ("observer-one", ObserverAssessment.CLEAR, "CHECK_CLEAR"),
            ("observer-two", ObserverAssessment.CONCERN, "CHECK_CONCERN"),
        ):
            self.store.submit_observer_report(
                "observer-task",
                ObserverReport(
                    observer_id=observer_id,
                    assessment=assessment,
                    evidence_refs=(f"{observer_id}-evidence",),
                    finding=finding_code,
                ),
                now=NOW,
            )
        finding = self.report().by_code("OBSERVER_DISAGREEMENT")
        self.assertEqual(len(finding), 1)
        self.assertEqual(finding[0].severity, FindingSeverity.BLOCK)

    def test_disagreement_observer_marks_director_resolved_disagreement(self) -> None:
        self.submit_for_review()
        for observer_id, assessment, finding_code in (
            ("observer-one", ObserverAssessment.CLEAR, "CHECK_CLEAR"),
            ("observer-two", ObserverAssessment.CONCERN, "CHECK_CONCERN"),
        ):
            self.store.submit_observer_report(
                "observer-task",
                ObserverReport(
                    observer_id=observer_id,
                    assessment=assessment,
                    evidence_refs=(f"{observer_id}-evidence",),
                    finding=finding_code,
                ),
                now=NOW,
            )
        self.store.resolve_observer_disagreement(
            "observer-task",
            director_id="director-1",
            disposition="CONCERN_REVIEWED",
            evidence_refs=("director-evidence",),
            now=NOW,
        )
        report = self.report()
        finding = report.by_code("OBSERVER_DISAGREEMENT_RESOLVED")
        self.assertEqual(len(finding), 1)
        self.assertEqual(finding[0].severity, FindingSeverity.INFO)
        self.assertFalse(report.by_code("OBSERVER_DISAGREEMENT"))

    def test_disagreement_observer_rejects_unauthorized_resolution_record(self) -> None:
        self.submit_for_review()
        for observer_id, assessment, finding_code in (
            ("observer-one", ObserverAssessment.CLEAR, "CHECK_CLEAR"),
            ("observer-two", ObserverAssessment.CONCERN, "CHECK_CONCERN"),
        ):
            self.store.submit_observer_report(
                "observer-task",
                ObserverReport(
                    observer_id=observer_id,
                    assessment=assessment,
                    evidence_refs=(f"{observer_id}-evidence",),
                    finding=finding_code,
                ),
                now=NOW,
            )
        self.store.resolve_observer_disagreement(
            "observer-task",
            director_id="director-1",
            disposition="CONCERN_REVIEWED",
            evidence_refs=("director-evidence",),
            now=NOW,
        )
        with self.store._connection:
            self.store._connection.execute(
                "UPDATE observer_resolutions SET director_id = ? WHERE task_id = ?",
                ("not-a-director", "observer-task"),
            )
        report = self.report()
        self.assertIn("OBSERVER_RESOLUTION_INVALID", self.codes(report))
        self.assertIn("OBSERVER_DISAGREEMENT", self.codes(report))

    def test_blocking_observer_disagreement_cannot_be_marked_resolved(self) -> None:
        self.submit_for_review()
        for observer_id, assessment, finding_code in (
            ("observer-one", ObserverAssessment.CLEAR, "CHECK_CLEAR"),
            ("observer-two", ObserverAssessment.BLOCK, "CHECK_BLOCK"),
        ):
            self.store.submit_observer_report(
                "observer-task",
                ObserverReport(
                    observer_id=observer_id,
                    assessment=assessment,
                    evidence_refs=(f"{observer_id}-evidence",),
                    finding=finding_code,
                ),
                now=NOW,
            )
        with self.store._connection:
            self.store._connection.execute(
                """
                INSERT INTO observer_resolutions
                    (task_id, artifact_hash, director_id, disposition, evidence_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    "observer-task",
                    ARTIFACT_HASH,
                    "director-1",
                    "BLOCK_IGNORED",
                    '["director-evidence"]',
                    NOW.isoformat(),
                ),
            )
        report = self.report()
        finding = report.by_code("OBSERVER_DISAGREEMENT")
        self.assertEqual(len(finding), 1)
        self.assertEqual(finding[0].severity, FindingSeverity.BLOCK)
        self.assertFalse(report.by_code("OBSERVER_DISAGREEMENT_RESOLVED"))

    def test_independence_observer_detects_author_as_reviewer(self) -> None:
        self.submit_for_review(actor_id="reviewer-source")
        self.store.record_review(
            "observer-task",
            gate=ReviewGate.SOURCE_FACTUAL,
            outcome=ReviewOutcome.PASS,
            reviewer_id="reviewer-source",
            artifact_hash=ARTIFACT_HASH,
            evidence_refs=("source-check",),
            now=NOW,
        )
        self.assertIn("REVIEWER_OR_OBSERVER_NOT_INDEPENDENT", self.codes())

    def test_observer_requires_an_existing_complete_database(self) -> None:
        with self.assertRaises(ObserverError):
            ObserverEngine(
                Path(self.temporary_directory.name) / "missing.sqlite3",
                self.registry,
                DIRECTORS,
            )


if __name__ == "__main__":
    unittest.main()
