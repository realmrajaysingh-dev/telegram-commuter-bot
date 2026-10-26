from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from studio.dashboard import create_dashboard_server
from studio.issues import IssueEngine
from studio.observer import (
    FindingSeverity,
    ObserverFinding,
    ObserverRunReport,
)
from studio.pipeline import ProjectError, ProjectPipeline, ProjectStatus
from studio.registry import RoleRegistry


ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 26, 7, 0, tzinfo=timezone.utc)
DIRECTOR_ID = "director-local"


class ProjectPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary_directory.name) / "studio.sqlite3"
        self.registry = RoleRegistry.from_markdown(ROOT / "docs" / "AGENT_REGISTRY.md")
        self.issues = IssueEngine(self.database, {DIRECTOR_ID})
        self.pipeline = ProjectPipeline(
            self.database, {DIRECTOR_ID}, self.registry
        )

    def tearDown(self) -> None:
        self.pipeline.close()
        self.issues.close()
        self.temporary_directory.cleanup()

    def create_project(self, **overrides):
        values = {
            "director_id": DIRECTOR_ID,
            "title": "Synthetic studio project",
            "project_type": "video",
            "department_id": "D03",
            "language": "en-US",
            "priority": "normal",
            "role_id": "D03-R03",
            "project_id": "project-test",
            "is_demo": True,
            "now": NOW,
        }
        values.update(overrides)
        return self.pipeline.create_project(**values)

    def test_create_project_starts_in_draft_and_validates_role_assignment(self) -> None:
        project = self.create_project()
        self.assertEqual(project["project_id"], "project-test")
        self.assertEqual(project["status"], ProjectStatus.DRAFT.value)
        self.assertEqual(project["role_id"], "D03-R03")
        self.assertTrue(project["is_demo"])
        self.assertEqual(project["created_by"], DIRECTOR_ID)
        generated = self.create_project(project_id=None, title="Generated ID")
        self.assertTrue(generated["project_id"].startswith("project-"))

    def test_invalid_project_fields_and_department_role_mismatch_are_rejected(self) -> None:
        invalid_cases = (
            {"title": "  "},
            {"language": "not a language!"},
            {"priority": "emergency"},
            {"department_id": "D04"},
            {"role_id": "D99-R99"},
            {"is_demo": "true"},
            {"project_type": ""},
        )
        for values in invalid_cases:
            with self.subTest(values=values):
                with self.assertRaises(ProjectError):
                    self.create_project(project_id=None, **values)
        self.assertEqual(self.pipeline.list_projects(), [])

    def test_duplicate_project_ids_are_rejected(self) -> None:
        self.create_project()
        with self.assertRaisesRegex(ProjectError, "already exists"):
            self.create_project()
        self.assertEqual(len(self.pipeline.list_projects()), 1)

    def test_creation_requires_configured_director_authorization(self) -> None:
        with self.assertRaisesRegex(ProjectError, "configured local Director"):
            self.create_project(director_id="untrusted")
        self.assertEqual(self.pipeline.list_projects(), [])

    def test_only_allowed_director_controlled_status_transitions_succeed(self) -> None:
        project = self.create_project()
        with self.assertRaisesRegex(ProjectError, "cannot transition"):
            self.pipeline.transition_status(
                project["project_id"], "completed", director_id=DIRECTOR_ID, now=NOW
            )
        with self.assertRaisesRegex(ProjectError, "configured local Director"):
            self.pipeline.transition_status(
                project["project_id"], "queued", director_id="untrusted", now=NOW
            )
        for status in (
            "queued",
            "active",
            "pending_review",
            "ready",
            "completed",
        ):
            project = self.pipeline.transition_status(
                project["project_id"], status, director_id=DIRECTOR_ID, now=NOW
            )
        self.assertEqual(project["status"], "completed")
        with self.assertRaisesRegex(ProjectError, "cannot transition"):
            self.pipeline.transition_status(
                project["project_id"], "active", director_id=DIRECTOR_ID, now=NOW
            )

    def test_project_links_validate_existing_task_issue_and_observer_finding(self) -> None:
        with self.issues._connection:
            self.issues._connection.execute(
                "CREATE TABLE tasks (task_id TEXT PRIMARY KEY, title TEXT, status TEXT)"
            )
            self.issues._connection.execute(
                "INSERT INTO tasks (task_id, title, status) VALUES ('task-real-1', 'Synthetic task', 'queued')"
            )
        issue = self.issues.create_issue(
            project="Voocks",
            department="Video",
            category="task_failure",
            severity="low",
            description="Synthetic task issue",
            now=NOW,
        )
        report = ObserverRunReport(
            generated_at=NOW,
            observer_id="observer-engine",
            findings=(
                ObserverFinding(
                    finding_id="finding-real-1",
                    observer_id="observer-policy",
                    code="TASK_STATUS_INVALID",
                    severity=FindingSeverity.BLOCK,
                    subject_id="task-real-1",
                    evidence_refs=("task:task-real-1",),
                ),
            ),
        )
        self.pipeline.record_observer_findings(report)
        project = self.create_project()
        project_id = project["project_id"]
        for kind, target in (
            ("task", "task-real-1"),
            ("issue", issue["issue_id"]),
            ("observer_finding", "finding-real-1"),
        ):
            detail = self.pipeline.link_record(
                project_id, kind, target, director_id=DIRECTOR_ID, now=NOW
            )
            linked = next(
                item
                for item in detail["links"]
                if item["link_type"] == kind and item["target_id"] == target
            )
            self.assertEqual(linked["linked_by"], DIRECTOR_ID)
            self.assertEqual(linked["linked_at"], NOW.isoformat())
            self.assertTrue(linked["details"])
        with self.assertRaisesRegex(ProjectError, "record not found"):
            self.pipeline.link_record(
                project_id, "task", "missing-task", director_id=DIRECTOR_ID, now=NOW
            )
        with self.assertRaisesRegex(ProjectError, "already linked"):
            self.pipeline.link_record(
                project_id, "issue", issue["issue_id"], director_id=DIRECTOR_ID, now=NOW
            )
        self.assertEqual(len(self.pipeline.link_options()["observer_finding"]), 1)

    def test_project_history_and_hash_chained_audit_records(self) -> None:
        project = self.create_project()
        self.pipeline.transition_status(
            project["project_id"], "queued", director_id=DIRECTOR_ID, now=NOW
        )
        detail = self.pipeline.project_detail(project["project_id"])
        self.assertEqual(
            [event["event_type"] for event in detail["history"]],
            ["project_created", "project_status_changed"],
        )
        self.assertTrue(self.pipeline.verify_audit_chain())
        event_data = json.loads(detail["audit_history"][1]["event_json"])
        self.assertEqual(event_data, {"from": "draft", "to": "queued"})

    def test_dashboard_counts_and_filters_reflect_persisted_projects(self) -> None:
        first = self.create_project()
        self.create_project(project_id="project-second", is_demo=False, priority="high")
        self.pipeline.transition_status(
            first["project_id"], "queued", director_id=DIRECTOR_ID, now=NOW
        )
        snapshot = self.pipeline.snapshot()
        self.assertEqual(snapshot["counts"]["queued"], 1)
        self.assertEqual(snapshot["counts"]["draft"], 1)
        self.assertEqual(sum(snapshot["counts"].values()), 2)
        filtered = self.pipeline.snapshot(status="queued", department_id="D03")
        self.assertEqual([project["project_id"] for project in filtered["projects"]], ["project-test"])
        self.assertIn("D03-R03", [role["role_id"] for role in snapshot["roles"]])
        demo_project = next(
            project for project in snapshot["projects"]
            if project["project_id"] == "project-test"
        )
        self.assertTrue(demo_project["is_demo"])

    def test_dashboard_project_api_enforces_authorized_creation(self) -> None:
        server = create_dashboard_server(
            self.issues, host="127.0.0.1", port=0, pipeline=self.pipeline
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            with urlopen(f"{base}/api/projects") as response:
                snapshot = json.loads(response.read())
            self.assertEqual(snapshot["counts"]["draft"], 0)
            payload = {
                "director_id": "untrusted",
                "title": "Unauthorized",
                "project_type": "video",
                "department_id": "D03",
                "language": "en",
                "priority": "normal",
                "role_id": "D03-R03",
            }
            request = Request(
                f"{base}/api/projects",
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with self.assertRaises(HTTPError) as error:
                urlopen(request, timeout=2)
            self.assertEqual(error.exception.code, 400)
            error.exception.close()
            payload["director_id"] = DIRECTOR_ID
            request = Request(
                f"{base}/api/projects",
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request, timeout=2) as response:
                created = json.loads(response.read())
                self.assertEqual(response.status, 201)
            self.assertEqual(created["status"], "draft")
            with urlopen(f"{base}/api/projects/{created['project_id']}") as response:
                detail = json.loads(response.read())
            self.assertEqual(detail["created_by"], DIRECTOR_ID)
            status_request = Request(
                f"{base}/api/projects/{created['project_id']}/status",
                data=json.dumps(
                    {"director_id": DIRECTOR_ID, "status": "queued"}
                ).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(status_request, timeout=2) as response:
                updated = json.loads(response.read())
            self.assertEqual(updated["status"], "queued")
            issue = self.issues.create_issue(
                project="Voocks",
                department="Video",
                category="task_failure",
                severity="low",
                description="Issue linked through dashboard API",
                now=NOW,
            )
            link_request = Request(
                f"{base}/api/projects/{created['project_id']}/links",
                data=json.dumps(
                    {
                        "director_id": DIRECTOR_ID,
                        "link_type": "issue",
                        "target_id": issue["issue_id"],
                    }
                ).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(link_request, timeout=2) as response:
                linked = json.loads(response.read())
            self.assertEqual(linked["links"][0]["target_id"], issue["issue_id"])
            with urlopen(f"{base}/") as response:
                page = response.read().decode()
            self.assertIn("Project Pipeline", page)
            self.assertIn("Synthetic demo record", page)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
