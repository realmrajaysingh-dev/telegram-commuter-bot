"""Read-only Session 09.2 dual-lane verification facade.

This class deliberately does NOT run agents, schedules, approvals or publishing.
It reconciles the existing workflow, handoff and CEO queue records into
truthful independent status summaries for local inspection.
"""
from __future__ import annotations

from typing import Any

from .daily_ceo_queue_bridge import DailyCEOQueueBridge
from .workflows import WorkflowError


class DualLaneMonitor092:
    def __init__(self, bridge: DailyCEOQueueBridge):
        self.bridge = bridge

    def snapshot(self) -> dict[str, Any]:
        engine = self.bridge.handoffs.engine
        state = engine.snapshot()
        handoffs = self.bridge.handoffs.list_private_handoffs()
        links = self.bridge.db.execute(
            "SELECT run_id,project_id,plan_id FROM studio_daily_ceo_links ORDER BY run_id"
        ).fetchall()
        linked = []
        for row in links:
            plan = self.bridge.allocation.get_plan(row['plan_id'])
            linked.append({
                'run_id': row['run_id'], 'project_id': row['project_id'],
                'plan_id': row['plan_id'], 'plan_status': plan['status'],
                'execution_performed': False, 'publication_enabled': False,
            })
        return {
            'mode': 'local_only_read_only',
            'daily_desk': {
                'private_handoff_count': len(handoffs),
                'linked_ceo_plan_count': len(linked),
                'linked_plans': linked,
                'blocked_projects': state['workflows']['ai_daily_desk']['blocked'],
            },
            'creative_desk': {
                'blocked_projects': state['workflows']['human_creative_desk']['blocked'],
                'projects': state['workflows']['human_creative_desk']['projects'],
            },
            'independent_lanes': True,
            'execution_performed': False,
            'publication_enabled': False,
        }

    def creative_review(self, project_id: str) -> dict[str, Any]:
        """Check the already recorded human gate chain; never grant approval."""
        state = self.bridge.handoffs.engine.creative_gate_status(project_id)
        return {
            'project_id': project_id,
            'next_required_gate': state['next_required_gate'],
            'release_approved': state['release_approved'],
            'publication_enabled': False,
        }
