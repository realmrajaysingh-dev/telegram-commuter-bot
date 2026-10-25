# Publishing Nexus

**Status:** Proposed distribution boundary; no new publishing automation is authorized.

## Principle

Publishing is a privileged side effect, separate from production. Use official platform APIs and comply with current platform policies, rate limits, account permissions, and audience consent. A successful generation or scheduled run is never publication authorization. Owner/Director approval of the immutable release bundle and target is mandatory.

## Current repository behavior

The existing GitHub Actions workflow runs the bot five times daily and can be manually dispatched. The bot creates article HTML, renders a card, sends a Telegram photo and quiz poll, and the workflow pushes article files to the repository using `contents: write`. This is existing automated public delivery, not a proposed feature. Do not infer from this draft that it has been disabled. The present assignment must not modify it.

## Proposed release package

For each channel, prepare a reviewable release manifest containing:

- Task/assignment and approval IDs; final content and asset hashes; language, format, disclosure, and intended audience.
- Exact destination/account, requested publication time, platform-specific text and media, alt text/subtitles, URL/link checks, and rights/attribution evidence.
- Gate decisions and unresolved-risk disposition; hash-bound Director authorization and expiry.
- Adapter/API version, request idempotency key, and expected platform result shape.

The initial Nexus exports a package or preview only. A human uses the platform's normal interface or explicitly authorized official API call. No new scheduled or unattended public release path is introduced in this architecture task.

## Adapter lifecycle (future, gated)

Each platform adapter must have a narrow permission scope, explicit allowlist, content/format validation, dry-run preview, idempotency and reconciliation strategy, rate-limit handling, and independent test fixtures. It may submit only a Director-approved package to its specified destination. An ambiguous timeout requires status reconciliation; do not blindly repeat a request. Record API response, platform ID, canonical URL where available, observed status, and verification time. A request accepted by an API is not proof that the item remains live or is visible as intended.

No credentials for platforms not explicitly approved. No scraping as a substitute for official APIs. No platform adapter may override review, licensing, budget, or human authorization.

## Delivery states

`not_prepared` → `prepared` → `reviewed` → `director_approved` → `submitted` → `platform_confirmed` / `platform_rejected` / `status_unknown` → `reconciled`.

`status_unknown` is a hold state, not permission to retry. Partial multi-channel delivery is recorded per channel; do not claim all-channel success if any target failed or remains uncertain. Corrections/removals are separately authorized and logged.

## Platform expansion

Treat Telegram as the only current platform API integration evidenced in `bot.py`. Other channels shown in `admin.html` are interface catalog entries and are not verified integrations. Evaluate each future platform separately for API eligibility, free-tier limits, privacy, account permissions, rate limits, and Director approval before design or implementation.
