from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from studio.allocation import (
    AllocationError,
    TaskAllocationEngine,
    TaskDefinition,
    topological_task_order,
)
from studio.dashboard import create_dashboard_server
from studio.issues import IssueEngine
from studio.pipeline import ProjectPipeline
from studio.registry import RoleRegistry


ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 26, 8, 0, tzinfo=timezone.utc)
DIRECTOR_ID = "director-local"


class TaskAllocationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary_directory.name) / "studio.sqlite3"
        self.registry = RoleRegistry.from_markdown(ROOT / "docs" / "AGENT_REGISTRY.md")
        self.issues = IssueEngine(self.database, {DIRECTOR_ID})
        self.pipeline = ProjectPipeline(self.database, {DIRECTOR_ID}, self.registry)
        self.allocation = TaskAllocationEngine(
            self.database, self.registry, {DIRECTOR_ID}
        )
        self.project_number = 0

    def tearDown(self) -> None:
        self.allocation.close()
        self.pipeline.close()
        self.issues.close()
        self.temporary_directory.cleanup()

    def create_project(self, *, status: str = "queued", **overrides) -> dict:
        self.project_number += 1
        values = {
            "director_id": DIRECTOR_ID,
            "title": "Synthetic article project",
            "project_type": "news article",
            "department_id": "D03",
            "language": "en",
            "priority": "normal",
            "role_id": "D03-R03",
            "project_id": f"project-{self.project_number}",
            "is_demo": True,
            "now": NOW,
        }
        values.update(overrides)
        project = self.pipeline.create_project(**values)
        if status != "draft":
            self.pipeline.transition_status(
                project["project_id"], status, director_id=DIRECTOR_ID, now=NOW
            )
        return self.pipeline.get_project(project["project_id"])

    def create_plan(self, *, project: dict | None = None, **overrides) -> dict:
        project = project or self.create_project()
        values = {
            "director_id": DIRECTOR_ID,
            "template": "news_article",
            "director_instructions": "Use only sourced claims.",
            "output_format": "markdown",
            "language": "en-US",
            "priority": "high",
            "deadline_at": NOW + timedelta(days=5),
            "now": NOW,
        }
        values.update(overrides)
        return self.allocation.generate_task_plan(project["project_id"], **values)

    def approve(self, plan: dict, *, now: datetime = NOW) -> dict:
        return self.allocation.approve_plan(
            plan["plan_id"], director_id=DIRECTOR_ID, now=now
        )

    @staticmethod
    def first_task(plan: dict, key: str | None = None) -> dict:
        return next(
            task for task in plan["tasks"]
            if key is None or task["task_key"] == key
        )

    def test_valid_project_plan_uses_registered_roles_and_persists_requirements(self) -> None:
        project = self.create_project()
        plan = self.create_plan(project=project)
        self.assertEqual(plan["status"], "draft")
        self.assertEqual(plan["project_id"], project["project_id"])
        self.assertEqual(plan["project"]["title"], project["title"])
        self.assertEqual(plan["output_format"], "markdown")
        self.assertEqual(plan["language"], "en-US")
        self.assertEqual(plan["priority"], "high")
        self.assertEqual(plan["counts"]["pending"], 5)
        self.assertEqual(plan["counts"]["ready"], 0)
        for task in plan["tasks"]:
            role = self.registry.get(task["role_id"])
            self.assertEqual(task["parent_project_id"], project["project_id"])
            self.assertEqual(task["department_id"], role.department_id)
            self.assertTrue(task["description"])
            self.assertTrue(task["evidence_requirements"])
            self.assertTrue(task["verification_gates"])
            self.assertEqual(task["status"], "pending")
            self.assertEqual(task["created_at"], task["updated_at"])
        self.assertTrue(self.allocation.verify_audit_chain())
        self.assertEqual(plan["observer"]["observer_id"], "observer-task-allocation")
        self.assertTrue(plan["observer"]["is_clear"])

    def test_all_four_templates_produce_registered_relevant_tasks(self) -> None:
        for template in self.allocation.template_names():
            with self.subTest(template=template):
                project = self.create_project()
                plan = self.create_plan(
                    project=project,
                    template=template,
                    director_instructions="",
                )
                self.assertGreaterEqual(len(plan["tasks"]), 4)
                self.assertEqual(plan["template"], template)
                for task in plan["tasks"]:
                    self.assertEqual(
                        self.registry.get(task["role_id"]).department_id,
                        task["department_id"],
                    )

    def test_invalid_and_unapproved_project_ids_are_rejected(self) -> None:
        with self.assertRaisesRegex(AllocationError, "Project ID"):
            self.allocation.generate_task_plan(
                "bad/project", director_id=DIRECTOR_ID, template="news_article", now=NOW
            )
        with self.assertRaisesRegex(AllocationError, "not found"):
            self.allocation.generate_task_plan(
                "missing-project", director_id=DIRECTOR_ID, template="news_article", now=NOW
            )
        draft_project = self.create_project(status="draft")
        with self.assertRaisesRegex(AllocationError, "explicitly approved"):
            self.create_plan(project=draft_project)

    def test_duplicate_plan_generation_is_idempotent_and_conflicts_are_rejected(self) -> None:
        project = self.create_project()
        first = self.create_plan(project=project)
        self.pipeline.transition_status(
            project["project_id"], "active", director_id=DIRECTOR_ID, now=NOW
        )
        repeated = self.create_plan(project=project)
        self.assertEqual(first["plan_id"], repeated["plan_id"])
        self.assertEqual(len(repeated["tasks"]), 5)
        with self.assertRaisesRegex(AllocationError, "different task plan"):
            self.create_plan(project=project, template="youtube_video")
        self.assertEqual(
            self.allocation._connection.execute(
                "SELECT COUNT(*) FROM studio_task_plans"
            ).fetchone()[0],
            1,
        )

    def test_plan_requires_explicit_director_approval_before_activation(self) -> None:
        plan = self.create_plan()
        task = self.first_task(plan)
        with self.assertRaisesRegex(AllocationError, "explicit Director approval"):
            self.allocation.start_task(task["task_id"], actor_id=task["role_id"], now=NOW)
        with self.assertRaisesRegex(AllocationError, "configured local Director"):
            self.allocation.approve_plan(
                plan["plan_id"], director_id="untrusted", now=NOW
            )
        approved = self.approve(plan)
        self.assertEqual(approved["status"], "approved")
        self.assertEqual(approved["approved_by"], DIRECTOR_ID)
        self.assertEqual(approved["counts"]["ready"], 1)
        self.assertEqual(approved["counts"]["pending"], 4)
        self.assertEqual(len(approved["approvals"]), 1)

    def test_assignment_window_extension_and_fourteen_day_horizon(self) -> None:
        project = self.create_project()
        plan = self.create_plan(
            project=project, deadline_at=NOW + timedelta(days=14)
        )
        self.assertEqual(
            datetime.fromisoformat(plan["manifest"]["expires_at"]),
            NOW + timedelta(days=7),
        )
        approved = self.approve(plan)
        extended = self.allocation.extend_plan(
            plan["plan_id"],
            director_id=DIRECTOR_ID,
            now=NOW + timedelta(days=1),
        )
        self.assertEqual(
            datetime.fromisoformat(extended["manifest"]["expires_at"]),
            NOW + timedelta(days=14),
        )
        self.assertEqual(len(extended["manifest"]["extensions"]), 1)
        with self.assertRaisesRegex(AllocationError, "extension limit"):
            self.allocation.extend_plan(
                plan["plan_id"],
                director_id=DIRECTOR_ID,
                now=NOW + timedelta(days=2),
            )
        self.assertEqual(approved["status"], "approved")
        too_late_project = self.create_project()
        with self.assertRaisesRegex(AllocationError, "14-day"):
            self.create_plan(
                project=too_late_project,
                deadline_at=NOW + timedelta(days=15),
            )

    def test_deadline_and_horizon_reject_naive_or_past_values(self) -> None:
        project = self.create_project()
        with self.assertRaisesRegex(AllocationError, "timezone"):
            self.create_plan(project=project, deadline_at=datetime(2026, 10, 30))
        with self.assertRaisesRegex(AllocationError, "future"):
            self.create_plan(project=project, deadline_at=NOW)

    def test_dependency_graph_rejects_unknown_and_cyclic_dependencies(self) -> None:
        first = TaskDefinition(
            "first", "First", "D02", "D02-R01", "First task", ("missing",), ("brief",)
        )
        with self.assertRaisesRegex(AllocationError, "unknown dependencies"):
            topological_task_order((first,))
        cyclic = (
            TaskDefinition("first", "First", "D02", "D02-R01", "First", ("second",), ("brief",)),
            TaskDefinition("second", "Second", "D02", "D02-R03", "Second", ("first",), ("sources",)),
        )
        with self.assertRaisesRegex(AllocationError, "cycle"):
            topological_task_order(cyclic)

    def test_tasks_activate_in_dependency_order_and_require_evidence_for_review(self) -> None:
        plan = self.approve(self.create_plan())
        first = self.first_task(plan, "research_scope")
        second = self.first_task(plan, "source_collection")
        with self.assertRaisesRegex(AllocationError, "Only ready tasks"):
            self.allocation.start_task(
                second["task_id"], actor_id=second["role_id"], now=NOW
            )
        active = self.allocation.start_task(
            first["task_id"], actor_id=first["role_id"], now=NOW
        )
        self.assertEqual(self.first_task(active, "research_scope")["status"], "active")
        with self.assertRaisesRegex(AllocationError, "evidence is missing"):
            self.allocation.submit_task_for_review(
                first["task_id"], actor_id=first["role_id"],
                evidence_refs=("other",), now=NOW,
            )
        review = self.allocation.submit_task_for_review(
            first["task_id"], actor_id=first["role_id"],
            evidence_refs=("research_brief",), now=NOW,
        )
        self.assertEqual(self.first_task(review, "research_scope")["status"], "review")
        completed = self.allocation.complete_task(
            first["task_id"], actor_id=first["role_id"], now=NOW
        )
        self.assertEqual(self.first_task(completed, "research_scope")["status"], "completed")
        self.assertEqual(self.first_task(completed, "source_collection")["status"], "ready")
        self.assertEqual(self.first_task(completed, "source_collection")["dependencies"][0]["status"], "completed")

    def test_role_department_mismatch_is_reported_by_persisted_observer(self) -> None:
        plan = self.create_plan()
        first = self.first_task(plan)
        with self.allocation._connection:
            self.allocation._connection.execute(
                "UPDATE studio_allocation_tasks SET department_id = ? WHERE task_id = ?",
                ("D01", first["task_id"]),
            )
        report = self.allocation.refresh_observer_findings(plan["plan_id"], now=NOW)
        self.assertFalse(report["is_clear"])
        self.assertIn(
            "TASK_DEPARTMENT_MISMATCH",
            {finding["code"] for finding in report["findings"]},
        )
        stored = self.allocation.get_plan(plan["plan_id"])["observer"]
        self.assertEqual(stored["run_id"], report["run_id"])

    def test_cancelled_project_blocks_approved_tasks_and_is_reported(self) -> None:
        project = self.create_project()
        plan = self.approve(self.create_plan(project=project))
        self.pipeline.transition_status(
            project["project_id"], "cancelled", director_id=DIRECTOR_ID, now=NOW
        )
        task = self.first_task(plan)
        with self.assertRaisesRegex(AllocationError, "Project approval is no longer active"):
            self.allocation.start_task(
                task["task_id"], actor_id=task["role_id"], now=NOW
            )
        report = self.allocation.refresh_observer_findings(plan["plan_id"], now=NOW)
        self.assertIn(
            "PROJECT_APPROVAL_NOT_ACTIVE",
            {finding["code"] for finding in report["findings"]},
        )

    def test_retryable_and_terminal_task_failures_obey_attempt_limit(self) -> None:
        plan = self.approve(self.create_plan())
        task = self.first_task(plan)
        task_id, role_id = task["task_id"], task["role_id"]
        for attempt in range(1, 4):
            plan = self.allocation.start_task(task_id, actor_id=role_id, now=NOW)
            current = self.first_task(plan)
            self.assertEqual(current["attempts"], attempt)
            plan = self.allocation.fail_task(
                task_id, actor_id=role_id, error_code="TRANSIENT_FAILURE",
                retryable=True, now=NOW,
            )
            self.assertEqual(self.first_task(plan)["status"], "blocked")
            if attempt < 3:
                plan = self.allocation.retry_task(task_id, actor_id=role_id, now=NOW)
                self.assertEqual(self.first_task(plan)["status"], "ready")
        with self.assertRaisesRegex(AllocationError, "retry limit"):
            self.allocation.retry_task(task_id, actor_id=role_id, now=NOW)
        self.assertEqual(len(plan["failures"]), 3)
        with self.assertRaisesRegex(AllocationError, "uppercase error code"):
            self.allocation.fail_task(
                task_id, actor_id=role_id, error_code="sensitive details",
                retryable=False, now=NOW,
            )

    def test_task_actions_require_the_registered_assigned_role(self) -> None:
        plan = self.approve(self.create_plan())
        task = self.first_task(plan)
        with self.assertRaisesRegex(AllocationError, "assigned registered role"):
            self.allocation.start_task(task["task_id"], actor_id="other-role", now=NOW)

    def test_dashboard_uses_persisted_plan_data_and_preserves_issue_resolution(self) -> None:
        project = self.create_project()
        issue = self.issues.create_issue(
            project="Synthetic allocation test",
            department="Editorial",
            category="task_failure",
            severity="low",
            description="Existing issue remains visible",
            now=NOW,
        )
        server = create_dashboard_server(
            self.issues,
            host="127.0.0.1",
            port=0,
            pipeline=self.pipeline,
            allocation=self.allocation,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            with urlopen(f"{base}/") as response:
                page = response.read().decode()
            self.assertIn("CEO Work Allocation", page)
            self.assertIn("Generate Task Plan", page)
            self.assertIn("Issue Inbox", page)
            self.assertIn("Issue details", page)

            body = {
                "project_id": project["project_id"],
                "director_id": DIRECTOR_ID,
                "template": "news_article",
                "director_instructions": "Keep claims sourced.",
                "output_format": "markdown",
                "language": "en",
                "priority": "normal",
                "deadline_at": (NOW + timedelta(days=5)).isoformat(),
            }
            request = Request(
                f"{base}/api/task-plans",
                data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request, timeout=3) as response:
                draft = json.loads(response.read())
                self.assertEqual(response.status, 201)
            self.assertEqual(draft["status"], "draft")
            self.assertEqual(draft["counts"]["pending"], 5)
            with urlopen(f"{base}/api/task-plans?project_id={project['project_id']}") as response:
                persisted = json.loads(response.read())["plan"]
            self.assertEqual(persisted["plan_id"], draft["plan_id"])
            task = persisted["tasks"][0]
            start_request = Request(
                f"{base}/api/allocation-tasks/{task['task_id']}/start",
                data=json.dumps({"actor_id": task["role_id"]}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with self.assertRaises(HTTPError) as error:
                urlopen(start_request, timeout=3)
            self.assertEqual(error.exception.code, 400)
            error.exception.close()

            approve_request = Request(
                f"{base}/api/task-plans/{draft['plan_id']}/approve",
                data=json.dumps({"director_id": DIRECTOR_ID}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(approve_request, timeout=3) as response:
                approved = json.loads(response.read())
            self.assertEqual(approved["status"], "approved")
            with urlopen(f"{base}/api/dashboard") as response:
                issue_snapshot = json.loads(response.read())
            self.assertIn(issue["issue_id"], {item["issue_id"] for item in issue_snapshot["issues"]})
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
