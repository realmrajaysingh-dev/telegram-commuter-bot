from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from studio.dashboard import create_dashboard_server
from studio.issues import (
    IssueEngine,
    IssueError,
    IssueNotFound,
    IssueStatus,
    RepairAction,
    classify_ffmpeg_error,
)


NOW = datetime(2026, 10, 26, 6, 0, tzinfo=timezone.utc)
DIRECTOR_ID = "director-local"


class DisagreeingObserver:
    observer_id = "test-independent-observer"

    def review(self, issue, result):
        return False, "TEST_OBSERVER_DISAGREEMENT"


class IssueEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary_directory.name) / "studio.sqlite3"
        self.engine = IssueEngine(self.database, {DIRECTOR_ID})

    def tearDown(self) -> None:
        self.engine.close()
        self.temporary_directory.cleanup()

    def create_issue(self, **overrides):
        values = {
            "project": "Voocks Media",
            "department": "Video",
            "category": "task_failure",
            "severity": "medium",
            "description": "Synthetic render task failed",
            "now": NOW,
        }
        values.update(overrides)
        return self.engine.create_issue(**values)

    def authorize(self, issue_id: str, **overrides):
        values = {"director_id": DIRECTOR_ID, "now": NOW}
        values.update(overrides)
        return self.engine.authorize_repair(issue_id, **values)

    def test_repair_requires_director_authorization_and_records_command(self) -> None:
        issue = self.create_issue()
        with self.assertRaises(IssueNotFound):
            self.engine.execute_authorized("not-a-command", now=NOW)

        command = self.authorize(issue["issue_id"], instructions="Retry once")
        report = self.engine.execute_authorized(command["authorization_id"], now=NOW)

        self.assertEqual(report.status, IssueStatus.RESOLVED)
        events = self.engine.audit_events(issue["issue_id"])
        self.assertIn("director_repair_authorized", [event["event_type"] for event in events])
        self.assertIn("director_repair_started", [event["event_type"] for event in events])

    def test_duplicate_commands_and_repair_execution_are_rejected(self) -> None:
        issue = self.create_issue()
        command = self.authorize(issue["issue_id"])
        with self.assertRaisesRegex(IssueError, "already pending"):
            self.authorize(issue["issue_id"])
        self.engine.execute_authorized(command["authorization_id"], now=NOW)
        with self.assertRaisesRegex(IssueError, "already been consumed"):
            self.engine.execute_authorized(command["authorization_id"], now=NOW)

    def test_expired_authorization_is_rejected_and_recorded(self) -> None:
        issue = self.create_issue()
        command = self.authorize(issue["issue_id"])
        later = NOW + self.engine.authorization_ttl + timedelta(seconds=1)
        with self.assertRaisesRegex(IssueError, "expired"):
            self.engine.execute_authorized(command["authorization_id"], now=later)
        with self.assertRaisesRegex(IssueError, "already been consumed"):
            self.engine.execute_authorized(command["authorization_id"], now=later)
        history = self.engine.issue_detail(issue["issue_id"])["repair_history"]
        self.assertEqual(history[0]["status"], "expired")
        self.assertIn(
            "director_repair_authorization_expired",
            [event["event_type"] for event in self.engine.audit_events(issue["issue_id"])],
        )

    def test_invalid_issue_id_is_rejected(self) -> None:
        with self.assertRaises(IssueNotFound):
            self.engine.get_issue("invalid-issue")
        with self.assertRaises(IssueNotFound):
            self.authorize("invalid-issue")

    def test_successful_and_failed_simulated_repair_results(self) -> None:
        success = self.create_issue()
        success_report = self.engine.fix_issue(
            success["issue_id"], director_id=DIRECTOR_ID, now=NOW
        )
        self.assertEqual(success_report.status, IssueStatus.RESOLVED)
        self.assertEqual(success_report.repair_result["task_status"], "queued")

        failure = self.create_issue(
            category="stuck_task",
            suggested_repair_action=RepairAction.RECOVER_SIMULATED_TASK.value,
            simulation={"recovery_succeeds": False},
        )
        failure_report = self.engine.fix_issue(
            failure["issue_id"], director_id=DIRECTOR_ID, now=NOW
        )
        self.assertEqual(failure_report.status, IssueStatus.FAILED)
        self.assertEqual(failure_report.repair_result["task_status"], "stuck")
        self.assertTrue(failure_report.observer_review["agrees"])

    def test_retry_limit_is_enforced(self) -> None:
        issue = self.create_issue(
            simulation={"retry_succeeds": False},
            max_attempts=2,
        )
        for _ in range(2):
            report = self.engine.fix_issue(
                issue["issue_id"], director_id=DIRECTOR_ID, now=NOW
            )
            self.assertEqual(report.status, IssueStatus.FAILED)
        with self.assertRaisesRegex(IssueError, "attempt limit"):
            self.authorize(issue["issue_id"])
        self.assertEqual(len(self.engine.issue_detail(issue["issue_id"])["repair_history"]), 2)

    def test_audit_history_is_hash_chained_and_exposes_repair_attempts(self) -> None:
        issue = self.create_issue()
        self.engine.fix_issue(issue["issue_id"], director_id=DIRECTOR_ID, now=NOW)
        detail = self.engine.issue_detail(issue["issue_id"])
        event_types = [event["event_type"] for event in detail["audit_history"]]
        self.assertEqual(event_types[0], "issue_detected")
        self.assertIn("director_repair_authorized", event_types)
        self.assertEqual(len(detail["repair_history"]), 1)
        self.assertTrue(self.engine.verify_audit_chain())

    def test_observer_disagreement_blocks_repair_and_surfaces_review(self) -> None:
        self.engine.close()
        self.engine = IssueEngine(
            self.database, {DIRECTOR_ID}, observer=DisagreeingObserver()
        )
        issue = self.create_issue()
        report = self.engine.fix_issue(issue["issue_id"], director_id=DIRECTOR_ID, now=NOW)
        self.assertEqual(report.status, IssueStatus.BLOCKED)
        self.assertFalse(report.observer_review["agrees"])
        self.assertEqual(
            report.observer_review["finding"], "TEST_OBSERVER_DISAGREEMENT"
        )

    def test_unsafe_repair_commands_and_simulation_fields_are_rejected(self) -> None:
        with self.assertRaisesRegex(IssueError, "disabled"):
            self.create_issue(suggested_repair_action="ai_code_repair_disabled")
        with self.assertRaisesRegex(IssueError, "unsupported inputs or commands"):
            self.create_issue(simulation={"command": "rm -rf"})

    def test_unauthorized_director_cannot_issue_a_repair_command(self) -> None:
        issue = self.create_issue()
        with self.assertRaisesRegex(IssueError, "configured local Director"):
            self.authorize(issue["issue_id"], director_id="untrusted-user")
        self.assertEqual(self.engine.issue_detail(issue["issue_id"])["repair_history"], [])

    def test_artifact_references_are_validated_without_opening_paths(self) -> None:
        self.engine.register_artifact("local-artifact")
        issue = self.create_issue(
            category="artifact_reference",
            evidence_refs=["local-artifact", "missing-artifact", "../outside"],
        )
        report = self.engine.fix_issue(issue["issue_id"], director_id=DIRECTOR_ID, now=NOW)
        self.assertEqual(report.status, IssueStatus.FAILED)
        self.assertEqual(report.repair_result["valid_refs"], ["local-artifact"])
        self.assertEqual(report.repair_result["missing_refs"], ["missing-artifact"])
        self.assertEqual(report.repair_result["invalid_refs"], ["../outside"])
        with self.assertRaisesRegex(IssueError, "symbolic identifier"):
            self.engine.register_artifact("../outside")

    def test_ffmpeg_errors_are_classified_and_render_test_is_synthetic(self) -> None:
        self.assertEqual(
            classify_ffmpeg_error("Unknown encoder 'badcodec'"),
            "unsupported_encoder",
        )
        self.assertEqual(
            classify_ffmpeg_error("Invalid data found when processing input"),
            "invalid_media",
        )
        issue = self.create_issue(
            category="ffmpeg",
            diagnostic_logs="No such file or directory",
        )
        report = self.engine.fix_issue(issue["issue_id"], director_id=DIRECTOR_ID, now=NOW)
        self.assertEqual(report.status, IssueStatus.RESOLVED)
        self.assertEqual(report.repair_result["ffmpeg_classification"], "missing_input")
        self.assertTrue(report.repair_result["synthetic_render_passed"])
        self.assertEqual(
            report.repair_result["operation"],
            "synthetic_render_check_no_ffmpeg_invoked",
        )

    def test_logs_are_sanitized_and_issue_filters_are_applied(self) -> None:
        high = self.create_issue(
            severity="high",
            department="Editorial",
            diagnostic_logs="token=secret123\x1b[31m failure\x1b[0m",
        )
        self.create_issue(severity="low", department="Video")
        self.assertIn("[REDACTED]", high["diagnostic_logs"])
        self.assertNotIn("secret123", high["diagnostic_logs"])
        self.assertNotIn("\x1b", high["diagnostic_logs"])
        filtered = self.engine.list_issues(severity="high", department="Editorial")
        self.assertEqual([item["issue_id"] for item in filtered], [high["issue_id"]])

    def test_dashboard_pipeline_and_issue_status_counts_match_database(self) -> None:
        with self.engine._connection:
            self.engine._connection.execute(
                "CREATE TABLE tasks (task_id TEXT PRIMARY KEY, status TEXT NOT NULL)"
            )
            self.engine._connection.executemany(
                "INSERT INTO tasks (task_id, status) VALUES (?, ?)",
                [
                    ("a", "in_progress"),
                    ("b", "queued"),
                    ("c", "blocked"),
                    ("d", "ready_for_review"),
                    ("e", "completed"),
                ],
            )
        self.create_issue()
        self.create_issue(severity="high", needs_approval=True)
        snapshot = self.engine.dashboard_snapshot()
        self.assertEqual(
            snapshot["pipeline"],
            {"active": 1, "pending": 1, "blocked": 1, "ready": 1},
        )
        self.assertEqual(snapshot["issue_counts"]["pending"], 1)
        self.assertEqual(snapshot["issue_counts"]["needs_approval"], 1)
        self.assertIn("Demo Mode", snapshot["mode"])

    def test_dashboard_rejects_unsafe_commands_and_non_loopback_binding(self) -> None:
        issue = self.create_issue()
        with self.assertRaisesRegex(ValueError, "loopback"):
            create_dashboard_server(self.engine, host="0.0.0.0", port=0)
        server = create_dashboard_server(self.engine, host="127.0.0.1", port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            request = Request(
                f"http://127.0.0.1:{server.server_port}/api/issues/{issue['issue_id']}/fix",
                data=json.dumps(
                    {
                        "director_id": DIRECTOR_ID,
                        "instructions": "",
                        "command": "rm -rf",
                    }
                ).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with self.assertRaises(HTTPError) as error:
                urlopen(request, timeout=2)
            self.assertEqual(error.exception.code, 400)
            error.exception.close()
            self.assertEqual(len(self.engine.issue_detail(issue["issue_id"])["repair_history"]), 0)

            request = Request(
                f"http://127.0.0.1:{server.server_port}/api/issues/{issue['issue_id']}/fix",
                data=json.dumps({"director_id": DIRECTOR_ID}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request, timeout=2) as response:
                report = json.loads(response.read())
            self.assertEqual(report["status"], IssueStatus.RESOLVED.value)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
