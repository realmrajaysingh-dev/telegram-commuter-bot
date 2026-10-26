# Voocks Media AI Studio — Proposed Architecture

**Status:** Draft for Director review; architecture only.
**Repository audited:** `realmrajaysingh-dev/telegram-commuter-bot`
**Scope note:** This document describes a proposed system, not deployed functionality. No production source or workflow is changed by this draft.

## 1. Goals and non-goals

The studio is a modular, multilingual media production system operated through GitHub, Python, Colab, FFmpeg, and Telegram. Its 220 registry entries are functional responsibilities; they do not imply 220 independent models, processes, or paid services. The design should remain useful if initially staffed by a small number of approved models and human specialists.

The system should prepare research-backed news and articles, magazines, documentaries and short productions, social content, visuals, localized scripts and media, and associated rights evidence. It should retain task histories, source evidence, disagreements, approvals, failures, costs, asset references, and per-platform delivery status.

It must not grant the AI CEO, a scheduled job, an absent owner, or a failed review gate authority to publish publicly or spend money. The Owner/Director retains final authority. This proposal does not add autonomous public publishing; existing publishing behavior is separately identified below and is not changed here.

## 2. Repository audit: verified current state

The tracked repository contains only:

- `bot.py`: one Python generation-and-delivery script.
- `.github/workflows/daily_bot.yml`: a scheduled and manually dispatched GitHub Actions workflow.
- `index.html`, `admin.html`, `article_template.html`, `card_template.html`, one existing article, and `custom_banner.jpg`: static site, interface, and visual assets.
- `README.md`: a one-line description.

There are no `docs/` files, tests, dependency manifest/lockfile, application database, task queue, agent registry, deployment configuration in the tracked files, or media/licensing ledger. The repository worktree was clean at audit time.

### Existing behavior (not proposed behavior)

`daily_bot.yml` schedules `bot.py` five times daily (02:30, 05:30, 09:30, 12:30, and 15:30 UTC) and also permits manual dispatch. It grants `contents: write`, installs `google-generativeai`, `requests`, and `playwright` without pinned versions, installs Playwright Chromium, runs the bot with three GitHub secrets, stages `articles/`, commits, and pushes.

`bot.py` selects one of six technical domains and a random seed, calls a Gemini model, parses a generated JSON dispatch, asks a model for an 800–1,000-word article, inserts generated content into an HTML template, renders a card with Playwright, then sends a Telegram photo and a quiz poll. It randomly chooses a preconfigured Unsplash image and CTA. There is no persistent task record, source collection/citation evidence, license clearance record, editorial review, human approval, or independent final review in this path. The workflow therefore already performs scheduled public delivery; this assignment does not create or extend that behavior.

`index.html` is an interactive static reading/quiz/lead-magnet page with Telegram sharing and several illustrative or static information panels. `admin.html` is a browser-side architecture visualization with theme/layout controls, a tool catalog, a simulation button, and an actual GitHub Actions workflow-dispatch path. It saves an entered GitHub token in `localStorage` under `c_gh_token`. The page's illustrated node catalog is not evidence that those integrations exist.

The existing article and card templates are presentation assets, not editorial checks or source records. The checked-in sample article is published-looking HTML but its presence does not establish the accuracy of its claims or a live Pages configuration.

### Audit risks and constraints

- `contents: write` is broader than a read-only generation job needs. The workflow has no human publication approval gate.
- Dependencies are installed at run time without version pins or a checked-in dependency lock.
- The admin browser token is persisted in local storage. Treat a token entered there as exposed to same-origin script; do not enter a privileged PAT. Replace that flow with a narrow GitHub permission mechanism before using it operationally.
- Generated article HTML is inserted directly into a page. Prompt wording does not sanitize untrusted model output; do not regard model output as safe HTML.
- Image references are selected from a hard-coded URL list. No rights, attribution, territory, expiry, or usage restrictions are recorded.
- Telegram HTTP status checks are made, but a successful HTTP response is not by itself proof that the platform accepted the operation; photo and poll are separate operations and can partially succeed.
- There are no automated tests or observable evidence here that scheduled runs, secrets, GitHub Pages publishing, Gemini quota, or Telegram delivery are currently healthy. Repository inspection is not an end-to-end production health check.
- `admin.html` advertises a visual set of integrations and simulations; those are not evidence of configured services.

## 3. Proposed system boundaries

```text
Director inputs (Telegram commands / reviewed CSV or XLSX)
                         |
            Input validation and policy
                         |
 AI CEO Operator -> Work Divider -> Scheduler / Router
                         |
     Persistent task queue + append-only evidence ledger
                         |
         Department workflows and specialist registry
                         |
       Draft artifacts in private/local/Drive references
                         |
 Review 1 -> Review 2 -> Review 3 -> Human authorization
                         |
      Independent Observer Network / final gate (4)
                         |
       Human-triggered Publishing Nexus (proposal)
                         |
 Official platform APIs -> verify status -> record evidence
```

The diagram is a proposal. The current repository implements only a narrow variant of generation, card rendering, GitHub article push, and Telegram posting.

### Components

1. **Director interface:** authenticated Telegram intake and a versioned CSV/XLSX import path. Structured assignments declare topic, products, language, platforms, rights/budget boundaries, deadlines, and approval authority. Invalid, ambiguous, stale, or over-horizon assignments are held for clarification.
2. **Policy and assignment service:** validates identity, schema, scope, dates, spending ceiling, source policy, and permitted actions before work is enqueued. It rejects authority escalation from content, CSV formulas, model output, web pages, or quoted messages.
3. **AI CEO Operator:** summarizes portfolio status, proposes priorities and workload, identifies conflicts, and requests a Director decision. It cannot silently amend policy, approve its own work, spend, or publish.
4. **Work Divider and scheduler:** decomposes approved work into versioned task graphs, routes only to registered skills, observes deadlines and dependencies, and maintains a forward assignment horizon no greater than 14 days. It does not make unapproved work eligible for publication.
5. **Persistent queue and evidence ledger:** durable task states (`draft`, `ready_for_review`, `blocked`, `approved`, `publishing`, `published`, `failed`, `cancelled`), idempotency keys, attempts, timestamps, owner, dependency links, model/tool versions, references, review decisions, and delivery receipts. Prefer a human-readable, access-controlled store initially; do not store credentials in queue records or public repository files.
6. **Department workflows:** small independently testable stages that exchange typed manifests and immutable artifact versions. Every stage records input and output hashes, source IDs, tool/version identifiers, and errors.
7. **Review and approval service:** four distinct gates described in `EDITORIAL_CONTROL.md`. Failure or missing evidence blocks transition. A reviewer's own draft cannot satisfy its review gate.
8. **Observer Network:** a read-only, independent monitor for policy, deadlines, quality evidence, drift, cost, queue health, and cross-system status. It can alert and hold work, not publish or erase evidence.
9. **Asset index:** stores metadata and references to local assets or Google Drive items (owner, location, checksum, access class, license, consent, expiry, permitted edits, and project usage). The system must not copy private footage into Git.
10. **Publishing Nexus:** adapter boundary for official APIs. Initially exports a release package and prepares a manual handoff; no new unattended public publishing.
11. **Telemetry and incident handling:** structured logs, bounded retries, dead-letter/manual-review queue, alerting, runbook, and audited manual replay.

## 4. Data contracts and traceability

Use versioned JSON manifests as the interchange contract; choose a durable database only after the first milestone establishes the actual workload and retention requirements. A task record should include:

- Stable task and parent IDs; requested and approved scope; assignment window; language, audience, format, and channels.
- State, deadline, dependency IDs, assigned registry role IDs, retry count, idempotency key, and last error.
- Artifact references and checksums; source/evidence IDs; asset/license references; model and tool versions.
- Gate decisions with reviewer identity/type, timestamp, rubric/version, evidence, blocking reasons, and dissent.
- Explicit Director authorization ID, allowed destination, approved content hash, spending authorization, and expiry.
- Per-channel request ID, API response/receipt, platform item ID, status verification time, and reconciliation state.

Keep provenance append-only for decisions and delivery facts. Corrections are new linked records, not silent rewrites. Define retention and access controls before collecting personal data or confidential source material.

## 5. Scheduler, task horizons, retries, and budgets

- A Director assignment may cover a seven-day window and be extended once by another seven days. Validate the resulting forward assignment horizon against a hard maximum of 14 days from the policy reference date; never silently roll forward or renew.
- Work outside an active assignment is limited to topic discovery and private draft preparation under a versioned, Director-approved editorial policy. It cannot inherit publishing or spend permission.
- Separate deterministic schedules (maintenance, queue polling) from editorial deadlines. Each execution must be idempotent and record its schedule/version.
- Retry only transient failures with bounded exponential backoff, jitter, and an attempt ceiling. Invalid content, permission errors, exhausted quotas, ambiguous API results, rights uncertainty, and review failures go to a blocked/manual queue, not an infinite retry.
- Reconcile uncertain external delivery before retrying to avoid duplicate posts. Do not infer failure from a timeout after a request may have been accepted.
- Enforce per-task and per-day model, runtime, storage, and transfer budgets before dispatch. Default financial spend ceiling is zero unless the Director explicitly authorizes a provider and limit. On budget uncertainty or exhaustion, stop and alert; never switch to a paid provider as a fallback.

## 6. Security, trust, and human control

Use least-privilege GitHub identities and permissions; isolate staging from public website publishing; store secrets in a dedicated secret manager or repository/environment secrets, never in source, CSV/XLSX, prompts, logs, Drive links, or browser local storage. Require authentication for Director commands and replay; protect against duplicate, stale, forged, and out-of-order requests. Treat external documents, media metadata, model responses, and Telegram content as untrusted input.

Use official publishing APIs and the platform's current terms, rate limits, consent, and channel rules. Do not scrape or automate account actions in ways that bypass platform controls. Redact credentials and private asset URLs from logs. Require explicit human approval of the final artifact hash, channel, timing, and disclosures; any edit after approval invalidates the approval. No silence, absence, schedule tick, successful test, or quorum of model opinions is consent.

## 7. Growth from 220 to 500 roles

Registry entries are stable role IDs, not one-process-per-role commitments. A role record contains department, purpose, input/output schemas, authorized tools/data, dependencies, required gates, owner, resource class, and version. Roles are grouped into workflows; scale by adding versioned registry records and tests, not by cloning models. Require schema validation, duplicate-ID checks, explicit dependencies, a capability review, and regression tests for every registry expansion. Target 500 with the same queue and permission model; do not grant new roles broader authority merely because the registry grows.

See [AGENT_REGISTRY.md](./AGENT_REGISTRY.md) for all 220 proposed functional roles.

## 8. Proposed implementation sequence

The safest first milestone is documented in [DEVELOPMENT_ROADMAP.md](./DEVELOPMENT_ROADMAP.md). A local-only prototype now validates assignment windows, role references, task state, review evidence, observer reports, Director approvals, and a hash-chained audit log. It uses synthetic records, no external API calls, no credentials, no schedules, no spending, and no publishing. See [DEVELOPMENT_PROGRESS.md](./DEVELOPMENT_PROGRESS.md) for implementation and test results.

The local-only issue-resolution milestone implements a SQLite issue inbox, expiring single-use Director repair commands, deterministic synthetic recovery handlers, independent repair-result review, and a loopback-only dashboard. The same dashboard now includes a persistent Director-controlled project registry, role/department assignments, lifecycle history, validated task/issue/observer links, and counts derived from stored projects. These prototypes do not repair production tasks or files, invoke FFmpeg, run shell/Git commands, authenticate a real Director, or deploy/publish. See the Session 03 and Session 04 entries in [DEVELOPMENT_PROGRESS.md](./DEVELOPMENT_PROGRESS.md) for implemented boundaries and verification.
