# AI CEO Operator, Work Divider, Scheduler, and Watchdog

**Status:** Proposed operating charter; not implemented.

## Authority

The AI CEO Operator is a coordination and decision-support function, not the chief authority. The Owner/Director retains final authority over editorial policy, assignments, exceptional access, spending, approval, and publication. The Operator may recommend; it may not approve its own recommendation, expand a mandate, create spending authority, or publish.

## Operating loop

1. **Ingest:** accept an authenticated Director instruction or an approved policy-triggered discovery task. Preserve the original input, actor, timestamp, and checksum. Quarantine unsupported file formats, ambiguous instructions, formula-bearing spreadsheet cells, and untrusted attachments for safe parsing.
2. **Validate:** validate assignment schema, identity, scope, content policy, dates, requested channels, resource ceilings, and rights constraints. Reject an assignment extending beyond its permitted period; never infer additional authority.
3. **Plan:** propose deliverables and task dependencies. Assign registry role IDs and resource estimates. A human can inspect and amend the proposed graph before execution where the task is novel or high-impact.
4. **Execute:** dispatch eligible, non-publishing tasks to registered workflows. Record inputs, outputs, tool/model versions, task state, and bounded retry attempts.
5. **Review:** hold deliverables at four independent review gates. The Operator cannot count its own work or another model's agreement as independent factual evidence.
6. **Report:** provide the Director a concise portfolio view: ready, blocked, failed, pending review, expiring assignments, costs, unresolved factual disputes, and publishing readiness.
7. **Stop safely:** pause affected work on policy ambiguity, approval expiry, rights uncertainty, lost credentials, budget exhaustion, inconsistent status, repeated failure, or suspected security incident.

## Assignment window

A Director-defined assignment covers at most seven days. The Director may explicitly extend that assignment once for at most another seven days. The validated forward horizon is never more than 14 days. An extension is a new signed/audited decision, not an automatic renewal. Persist the start/end and extension count; refuse to schedule beyond the policy horizon.

When no active Director instruction exists, an approved editorial policy may permit topic discovery, source collection, and private drafts. Those outputs remain drafts. They do not authorize public publishing, paid services, asset licensing, outreach, or new data collection beyond the policy.

## Work Divider

Turn an assignment into small, typed tasks with acceptance criteria, dependencies, responsible role IDs, required evidence, and resource ceiling. Maintain provenance links from each output to its inputs and parent assignment. Do not create tasks to evade a denied approval, an exhausted budget, a rights restriction, or a blocked review.

## Scheduler and queue

- Persist job intent and state before attempting work; use idempotency keys to prevent duplicate effects.
- Dispatch only when dependencies, policy checks, and resource limits pass.
- Bound retries to transient failures and send terminal/ambiguous failures to a manual queue with useful diagnostic evidence.
- For API timeouts, reconcile platform state before retrying; avoid duplicate posts.
- Prioritize deadlines only among authorized tasks; never let urgency bypass a quality gate.
- A scheduled trigger is a wake-up signal, not publication authorization.

## Watchdog

Monitor queue age, stuck tasks, retry storms, service quota, resource ceilings, stale approvals, expiring assignments, workflow failures, repeated content, missing evidence, delivery receipts, and unauthorized state transitions. It may pause work, create a clearly labeled incident, and notify the Director. It cannot delete logs, self-heal production code, rotate privileges without a human, or turn alerts into approvals.

## Decision and escalation rules

Escalate to the Director when instructions conflict; factual evidence is disputed; the next action changes an approved artifact; a new service or cost is needed; licensing, consent, privacy, or platform rules are unclear; the assignment horizon would be exceeded; an external result is uncertain; or a release is ready for review. Include the options, evidence, consequence, and requested decision. In the absence of a reply, hold the action.

## Audit requirements

Record who/what proposed, validated, executed, reviewed, approved, and changed every task. Save decision policy/rubric versions and dissent. Logs must not contain secret values or unrestricted private media. Corrections append new records and reference the superseded record. The Director can inspect and export the audit trail.
