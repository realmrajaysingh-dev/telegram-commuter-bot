# Independent Observer Network

**Status:** Proposed monitoring and challenge layer; not implemented.

## Purpose and independence

Observers look for failures, policy drift, missing evidence, suspicious state transitions, resource overruns, and discrepancies between intended and actual delivery. An observer is not a fifth voting model and does not turn agreement into proof. Fact claims still need traceable sources and an appropriately independent verification process.

The network is operationally separate from the authoring path: read-only access to task manifests, source/evidence references, gate outcomes, artifact hashes, budget summaries, and platform receipts. It cannot edit content, approve gates, spend, or publish. Observer findings are append-only alerts; a named human resolves or accepts residual risk.

## Observer lanes

1. **Assignment and policy observer:** checks identity, scope, active assignment dates, extension count, allowed channels, editorial policy version, and Director authority.
2. **Queue and scheduler observer:** checks dependency integrity, task age, duplicate idempotency keys, retry bounds, orphan tasks, stale approvals, and forward horizon.
3. **Source and claim observer:** checks source provenance, dates, quote attribution, conflicting evidence, unsupported numbers, and whether independent source evidence exists. It flags; it cannot certify facts solely from model outputs.
4. **Editorial observer:** checks rubric completion, audience/language requirements, disclosure, corrections, and unresolved creative/editorial dissent.
5. **Technical and rights observer:** checks artifact hashes, media renders, metadata, accessibility, license/consent/expiry records, and private-asset boundary.
6. **Budget and dependency observer:** checks model/tool allowlists, estimated vs. actual resources, external provider changes, pinned dependency policy, and unauthorized paid fallback.
7. **Publication observer:** compares Director approval hash/channel/time with the release candidate and reconciles API receipts with platform status. It cannot initiate a post.
8. **Cross-run observer:** detects repeated content, recurring unsupported assertions, changed templates, drift in outputs, and recurring failures across projects.

Observers can be rules, humans, or agents with scoped capabilities. For a meaningful independent check, separate the authoring identity and evidence path, retain observer inputs, and disclose common model/provider dependencies. Repeating a prompt across several instances of one model is correlated opinion, not independent verification.

## Alerts and severity

- **Block:** missing/expired approval, wrong artifact hash or destination, policy conflict, unverified rights/consent, missing required evidence, assignment outside the 14-day horizon, or any attempt to publish without Director authorization.
- **Urgent:** suspected credential exposure, private media leak, unauthorized spend, potentially harmful or materially false content, repeated delivery, or conflicting platform state.
- **Warning:** approaching deadline/budget, retry threshold, aging queue, inconsistent metadata, or unresolved non-blocking dissent.
- **Informational:** successful transitions and reconciliations with evidence links.

Every alert has a stable ID, event time, affected task/artifact, observed facts, rule/rubric version, severity, source evidence, recommended safe action, acknowledgment, disposition, and append-only history. Alert resolution does not erase the original signal.

## Fail-safe behavior

If observer data is unavailable or inconsistent, do not infer a pass. Hold affected publishing and spending transitions and alert the Director. If a monitor itself fails, report the failure through an independent channel where possible; never silently report a healthy status. A human can resume after inspecting the specific evidence and recording the decision.

## MVP

Begin with deterministic local checks on synthetic task manifests: valid dates/horizon, valid dependencies, required gate evidence, budget ceiling, approval/artifact hash match, and transition legality. No production credentials, remote telemetry, or autonomous remediation are needed for the first prototype.
