# Development Roadmap

**Status:** Proposed plan for Director review. No milestone in this document is implemented or authorized for deployment.

## Guiding constraints

- Preserve the existing public website and Telegram workflows until the Director explicitly approves a migration or change. This architecture draft does not alter them.
- No new autonomous public publishing. The existing scheduled publisher is documented as a current risk and remains untouched in this assignment.
- No paid providers or spending without explicit, bounded approval. No dependencies are added for the first milestone.
- Never store secrets, private footage, or privileged tokens in source, public assets, CSV/XLSX, logs, or browser local storage.
- Keep every module independently testable; use synthetic fixtures before external integrations.

## Milestone 0 — Architecture review (this deliverable)

Review repository audit, proposed boundaries, policies, the 220-role registry, horizons, gates, resource assumptions, and migration constraints. Resolve Director decisions on threat model, data retention, approval identity, and acceptable draft data before implementation. No source-code changes are part of this milestone.

## Milestone 1 — Local-only contracts and policy validator (safest first implementation)

Create a small, offline prototype for versioned assignment/task manifests and deterministic validation. Use standard-library tooling and synthetic input fixtures. Validate identity fields as supplied by a trusted test harness, assignment start/end, seven-day default, one explicit extension, hard 14-day horizon, dependencies, states, role IDs, gate-evidence presence, zero-spend default, and prohibition of publish actions. CSV/XLSX import can be a separate reviewed parser; initially define schema and fixtures without accepting untrusted workbooks.

Acceptance: unit tests cover valid/invalid assignments, boundary dates, extension count, dependencies/cycles, missing gates, and non-authorized publish/spend transitions. No network requests, credentials, schedules, production workflow edits, public writes, or new dependencies. Produce a test report and ask for approval before moving on.

## Milestone 2 — Read-only task/evidence ledger

Choose a persistence format based on tested requirements; add versioned task records, append-only decision/evidence entries, artifact hashes, typed state transitions, and export. Test restart/replay and audit trail integrity using synthetic data. Introduce retention/access policy before real assets or personal data.

## Milestone 3 — Review workflow with human-only release authorization

Implement four independent gates and human approval binding to final artifact hash, destination, timing, and spend. Add a local preview package. Require tests proving edits invalidate approval, gate failures block, and absent owner does not authorize release. No publishing adapter.

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
