"""Session 09.3-E: fresh verification for corrected article drafts.

Local-only checks; no external fact checking, public release, or AI execution.
"""
from __future__ import annotations

from .article_recheck_routing_093 import ArticleRecheckRouter
from .workflows import WorkflowError, _ID


class ArticleCorrectionEligibility:
    def __init__(self, router: ArticleRecheckRouter):
        self.router = router
        self.release = router.release
        self.gate = router.release.gate
        self.intake = router.intake

    def evaluate(self, ticket_id: str) -> dict:
        ticket = self.router.read(ticket_id)
        revised_id = ticket["revised_draft_id"]
        if ticket["status"] != "submitted_for_recheck" or not revised_id:
            return self._status(ticket, "awaiting_revised_draft", False)
        original = self.intake.get(ticket["draft_id"])
        revised = self.intake.get(revised_id)
        if (revised["project_id"] != original["project_id"]
                or revised["run_id"] != original["run_id"]
                or revised["body_sha256"] == ticket["draft_sha256"]):
            return self._status(ticket, "invalid_revised_draft", False)
        review = self.gate.evaluate(revised_id)
        if not review["ready_for_editorial_review"]:
            return self._status(ticket, "fresh_source_review_required", False)
        return self._status(ticket, "ready_for_separate_private_editorial_decision", True)

    @staticmethod
    def _status(ticket: dict, state: str, ready: bool) -> dict:
        return {
            "ticket_id": ticket["ticket_id"],
            "original_draft_id": ticket["draft_id"],
            "revised_draft_id": ticket["revised_draft_id"],
            "department": ticket["department"],
            "status": state,
            "eligible_for_private_editorial_decision": ready,
            "automatic_reapproval": False,
            "public_publishing_enabled": False,
        }

    def prepare_revised_candidate(self, *, ticket_id: str, reviewer_id: str,
                                  editorial_note: str) -> dict:
        """Director decisions are explicit; old draft approval never transfers."""
        with self.router.lock:
            state = self.evaluate(ticket_id)
            if not state["eligible_for_private_editorial_decision"]:
                raise WorkflowError("Corrected draft requires fresh source verification")
            return self.release.prepare(
                draft_id=state["revised_draft_id"],
                reviewer_id=reviewer_id,
                editorial_note=editorial_note,
            )
