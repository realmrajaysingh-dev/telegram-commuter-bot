"""Session 09.2: opt-in, local-only bridge to the EXISTING CEO task plan queue.

Never schedules an agent, approves a plan, publishes, or contacts a provider.
The Director supplies a pre-existing, explicitly routed Daily Desk project.
"""
from __future__ import annotations

from datetime import datetime, timezone
import sqlite3

from .allocation import TaskAllocationEngine
from .daily_draft_handoff import DailyDraftHandoff
from .workflows import WorkflowError


class DailyCEOQueueBridge:
    def __init__(self, handoffs: DailyDraftHandoff, allocation: TaskAllocationEngine):
        self.handoffs = handoffs
        self.allocation = allocation
        if handoffs.engine.database.resolve() != allocation.database.resolve():
            raise WorkflowError("Daily Desk and CEO queue must share the same local database")
        self.db = handoffs.connection
        with handoffs.engine._lock, self.db:
            self.db.execute("""CREATE TABLE IF NOT EXISTS studio_daily_ceo_links (
                run_id TEXT PRIMARY KEY REFERENCES studio_daily_draft_handoffs(run_id),
                project_id TEXT NOT NULL UNIQUE REFERENCES studio_projects(project_id),
                plan_id TEXT NOT NULL UNIQUE REFERENCES studio_task_plans(plan_id),
                created_at TEXT NOT NULL
            )""")

    def link_existing_project(self, run_id: str, project_id: str, *, director_id: str) -> dict:
        """Prepare (NOT approve/start) an existing CEO news_article task plan.

        Must be explicitly Director-invoked. No new project or autonomous identity.
        """
        if not isinstance(director_id, str) or director_id not in self.allocation.director_ids:
            raise WorkflowError("Director authorization required")
        if not isinstance(run_id, str) or not isinstance(project_id, str):
            raise WorkflowError("Invalid identifiers")
        with self.handoffs.engine._lock, self.db:
            record = self.db.execute(
                "SELECT handoff_json FROM studio_daily_draft_handoffs WHERE run_id=?", (run_id,)
            ).fetchone()
            if record is None:
                raise WorkflowError("Private Daily Desk handoff must exist first")
            existing = self.db.execute(
                "SELECT project_id,plan_id FROM studio_daily_ceo_links WHERE run_id=?", (run_id,)
            ).fetchone()
            if existing:
                if existing['project_id'] != project_id:
                    raise WorkflowError("Daily run already belongs to another project")
                return self._result(run_id, project_id, existing['plan_id'])
            if self.handoffs.engine.project_workflow(project_id) != 'ai_daily_desk':
                raise WorkflowError("Only an explicitly routed AI Daily Desk project is eligible")
            project = self.handoffs.engine.pipeline.get_project(project_id)
            if project['created_by'] != director_id:
                raise WorkflowError("Project must belong to the authorizing Director")
            if self.allocation.get_plan_for_project(project_id) is not None:
                raise WorkflowError("Project already has a CEO task plan")
            already = self.db.execute(
                "SELECT run_id FROM studio_daily_ceo_links WHERE project_id=?", (project_id,)
            ).fetchone()
            if already:
                raise WorkflowError("Project already linked to another Daily Desk run")
            # Existing allocation engine retains its own Director, horizon, roles,
            # dependencies, evidence, and observer checks.
            plan = self.allocation.generate_task_plan(
                project_id, director_id=director_id, template='news_article',
                director_instructions=f"Private Daily Desk handoff {run_id}; no publishing.",
                output_format='markdown',
            )
            plan_id = plan['plan_id']
            self.db.execute(
                "INSERT INTO studio_daily_ceo_links(run_id,project_id,plan_id,created_at) VALUES(?,?,?,?)",
                (run_id, project_id, plan_id, datetime.now(timezone.utc).isoformat()),
            )
            return self._result(run_id, project_id, plan_id)

    def _result(self, run_id: str, project_id: str, plan_id: str) -> dict:
        return {'run_id': run_id, 'project_id': project_id, 'plan_id': plan_id,
                'workflow_id': 'ai_daily_desk', 'status': 'draft_plan_requires_director_approval',
                'execution_performed': False, 'publication_enabled': False}
