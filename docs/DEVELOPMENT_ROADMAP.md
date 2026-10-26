# Development Roadmap

**Status:** Proposed plan for Director review; local-only prototype milestones are implemented and summarized in `DEVELOPMENT_PROGRESS.md`. No deployment or external integration is authorized by this document.

## Guiding constraints

- Preserve the existing public website and Telegram workflows until the Director explicitly approves a migration or change. This architecture draft does not alter them.
- No new autonomous public publishing. The existing scheduled publisher is documented as a current risk and remains untouched in this assignment.
- No paid providers or spending without explicit, bounded approval. No dependencies are added for the first milestone.
- Never store secrets, private footage, or privileged tokens in source, public assets, CSV/XLSX, logs, or browser local storage.
- Keep every module independently testable; use synthetic fixtures before external integrations.

## Milestone 0 — Architecture review (this deliverable)

Review repository audit, proposed boundaries, policies, the 220-role registry, horizons, gates, resource assumptions, and migration constraints. Resolve Director decisions on threat model, data retention, approval identity, and acceptable draft data before implementation. No source-code changes are part of this milestone.

## Milestone 1 — Local-only contracts and policy validator (implemented on development branch)

The offline prototype is implemented in `studio/` with standard-library tooling and synthetic tests. It validates versioned assignment manifests, trusted Director IDs supplied by the local harness, assignment expiry, a seven-day initial window, one explicit extension, the 14-day total/forward horizon, role and department references, task dependencies, status transitions, required evidence, four distinct review gates, observer disagreement, zero-spend default, Director approval, bounded transient retry, and audit-chain integrity. It also provides a persistent issue registry with human-triggered deterministic repairs, plus a Director-controlled project registry and dashboard with explicit lifecycle transitions and validated record links. A separate read-only observer engine checks assignment policy, horizon, duplicate tasks, approvals, publishing-boundary violations, audit consistency, failures, observer disagreement, and review independence. Registry metadata is loaded from `AGENT_REGISTRY.md`.

Automated tests cover these local policy checks, observer reports, and recovery paths. No AI observer models, network requests, credentials, schedules, production workflow edits, public writes, or new dependencies are used. The prototype is not an authentication system or production-ready queue.

## Milestone 2 — Durable task/evidence ledger hardening (next)

Harden the SQLite prototype with transactional schema migrations, export/import, retention and access policy, restart/replay coverage, and an independently anchored audit trail. Test using synthetic data. Introduce the retention/access policy before real assets or personal data.

## Milestone 3 — Review workflow and human authorization hardening (next)

The prototype records four distinct evidence-backed gate results and hash-bound Director approvals, but is not a production release-authorization service. Add an expiring, one-use authorization bound to final artifact, destination, timing, and spend; establish reviewer identity/independence; and test that any edit invalidates approval and an absent Director blocks release. No publishing adapter.

## Milestone 4 — Staging ingestion and one draft department

Only after explicit approval, add a limited source-ingestion and article-draft workflow using approved sources and private staging. Keep generated content escaped/sanitized and preserve claim-level source references. Add independent tests, review fixtures, budget controls, and an observer before handling real tasks. No public publication.

## Milestone 5 — Queue, scheduler, and operational controls

After the queue state machine is validated, add bounded retries, idempotency, dead-letter/manual review, watchdog alerts, resource ceilings, and observability. Start with manual triggers and synthetic tasks. Schedule work only after explicit operational approval; schedules cannot publish.

## Milestone 6 — Asset management and more departments

Add local/Drive references, license/consent records, checksum and expiry checks, then expand department workflows incrementally. Do not copy private footage into the repository. Every workflow needs schemas, tests, gate mapping, failure behavior, and owner.

## Milestone 7 — Human-controlled platform integration (separate approval)

Only if requested, evaluate a specific official API, permission model, current terms, costs, test account, and staging destination. Add per-platform adapter tests and manual reconciliation. Begin with human-initiated publication and hash-bound approval. Any unattended publishing would require a separate explicit design and Director approval; it is outside the current proposal.

## Milestone 8 — Expansion to 500 role functions

Add registry roles in validated increments after measuring actual throughput and overlap. Enforce unique stable IDs, dependency references, allowed capabilities, resource categories, required evidence, deprecation/version rules, and tests. Do not spawn or buy one model per role.

## Decisions required before coding beyond Milestone 1

1. Director identity and authentication mechanism; who may review/approve when the Director is unavailable.
2. Data classification, evidence/log retention, privacy, and permitted model-provider data.
3. Approved editorial policy, initial language/department scope, and source standards.
4. Spending ceiling and approved provider list (default remains zero).
5. Whether existing publisher remains untouched, is separately gated, or is intentionally retired in a future approved change.
6. Private staging and asset storage design, including Drive ownership/access and consent processes.
7. Platform-specific release procedures and correction/removal authority.
