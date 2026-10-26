# Optional GPU Worker Architecture (Session 07.1)

## Scope and status

This document and `studio/worker_contracts.py` define offline contracts for a
future optional, manually started media worker. The existing local Dashboard
and SQLite Studio task allocation remain the intended control plane. A Colab
notebook is a disposable compute session, not an always-on service or source of
truth.

This batch does **not** add worker tables to the Studio database, a Colab
notebook, network transport, Drive authentication, a lease scheduler, or media
execution. The contract records are immutable Python values; they do not
persist across process restart. Offline checks can validate declared metadata
and legal transitions, but cannot inspect actual files or prove a checksum.
Any later adapter must persist state and artifacts before treating this
contract as operational.

## Data flow

```text
Director-approved Studio plan/task
              |
              | create versioned manifest; queue stays in local control plane
              v
     Queued worker job (SQLite in a future integration)
              |
              | explicit Director authorization bound to manifest digest
              v
      Manual worker registration / capability match
              |
              | bounded, expiring lease + heartbeat
              v
   CPU FFmpeg worker OR optional T4 inference worker
              |
              | declared output refs + checksums + lease/attempt identity
              v
 Validate result, deduplicate, and attach evidence to the Studio task
```

The worker must not approve Studio plans, mark Studio tasks complete, or
bypass the existing artifact validation, Observer, Director acceptance, or
audit rules. Result acceptance and Studio evidence ingestion are separate
future transactions. The Director remains responsible for the existing task
review/completion gate.

## Versioned manifest and artifact contracts

`JobManifest` is schema version 1 and binds a stable job/idempotency key to a
project and Studio task. It includes workload kind, checksummed input artifact
references, declared output artifact references and byte ceilings, resource
requirements, attempt limit, creation/deadline timestamps, and optional
capability metadata. Unknown schema versions are rejected.

Artifact references carry opaque IDs, storage backend, key, media type, byte
size, and lowercase SHA-256. Local keys are constrained relative paths; they
cannot be absolute, traverse with `..`, use backslashes, or include a URL.
Future Drive references use an opaque file ID only, never an arbitrary URL or
path. Credentials and access tokens must be supplied by a future, separate
secret-management integration and must never appear in manifests, logs, audit
events, or browser storage.

Output declarations reserve a bounded output ID, storage key, media type, and
maximum size before execution. A `JobResult` must provide exactly those
outputs and match the current job, attempt, lease, worker, media type, storage
key, and declared size. The offline validator checks checksum syntax and
metadata binding; a future storage adapter must read the bytes, recompute
SHA-256, and verify the controlled storage root before acceptance.

`ensure_idempotent_enqueue` treats an identical idempotency key and semantic
request as a replay of the original manifest. Reusing the key with changed
project/task, workload, inputs, outputs, resources, deadline, retries, or
capabilities is rejected. A later SQLite integration must enforce this with a
unique key and a transaction, not a process-local check.

## State, authorization, leasing, and recovery

Supported job states are `queued`, `leased`, `running`, `succeeded`, `failed`,
`interrupted`, and `cancelled`. Contracts allow:

```text
queued -> leased -> running -> succeeded
                         \-> failed
              lease expiry -> interrupted -> queued (retry) or failed
failed -> queued (explicit Director retry, if attempts/deadline remain)
queued or interrupted -> cancelled (Director)
```

Each lease is scoped to a worker and attempt and expires within configured
lease, Director authorization, job deadline, and execution-time bounds.
Heartbeats can renew only the current live lease. Expired leases become
`interrupted`; a separate recovery decision returns eligible jobs to `queued`
or exhausts them to `failed`. Retry requires a remaining attempt; explicit
retry after failure clears the old execution authorization, requiring a new
Director authorization. A late or replayed result cannot satisfy the current
lease/attempt and a succeeded job has no live lease, so another result is
rejected.

Execution authorization is an explicit record bound to the manifest digest,
issued by a Director ID in the local allowlist, and limited by expiry/deadline.
The allowlist check is a local prototype boundary, not authenticated identity.
The authorization contract does not create approval: a future control-plane
operation must record the Director action in SQLite and the existing audit
chain before a worker can lease the job.

Manual worker registration has an expiry, explicit availability state,
workload/capability declarations, VRAM and concurrency ceilings. `registered`,
`gpu_unavailable`, and `disconnected` workers cannot lease jobs; queued jobs
remain queued. The contract checks a requested worker's current active-job
count. A future multi-process queue must make lease acquisition atomic (for
example, a SQLite transaction with a conditional state update) and enforce the
global concurrency count transactionally.

Every contract transition appends a hash-linked audit event with actor,
timestamp, and structured details. A future durable ledger must preserve this
history, verify its chain, and commit the state change and event atomically.

## CPU/GPU separation and future capabilities

- `cpu_ffmpeg` requires CPU-only resources and is intended for deterministic
  media operations such as transcode, trim, mux, or format conversion.
- `gpu_inference` explicitly requests an NVIDIA T4. It is intended for optional
  future inference only; this batch performs no inference or model loading.
- Language tags use BCP 47-style values (for example `hi`, `ta-IN`, `bn-IN`,
  `te-IN`, `mr-IN`, `gu-IN`, `kn-IN`, `ml-IN`, `pa-IN`, `or-IN`, `as-IN`, or
  `en-IN`). Model IDs and operation names are capability metadata, not a model
  catalog, endorsement, language-quality guarantee, or execution instruction.
- Transcription, translation, voice generation, and video-model operations
  must be separately declared and approved. No supported capability implies
  an external API or automatic model download.

## Default resource ceilings

The standard-library contract defaults are intentionally conservative and
configurable by the future trusted control plane:

| Resource | Default ceiling |
|---|---:|
| One input or output artifact | 2 GiB |
| Total job input | 8 GiB |
| Declared output total | 4 GiB |
| Job storage allocation | 10 GiB |
| CPU FFmpeg execution | 6 hours |
| GPU inference execution | 2 hours |
| Concurrent jobs per worker | 1 |
| Lease / heartbeat extension | 30 seconds–15 minutes |
| Attempts | 5 maximum |

The current four-hour Studio assignment deadline may impose a stricter bound.
Colab quotas, GPU availability, idle shutdown, Drive quota, and data-transfer
limits are external and are not guaranteed by these defaults.

## Safe shared-storage boundary

The control plane should retain ownership of job state and approved artifact
references. Future Drive integration should:

1. use an explicitly selected, Director-approved folder and least-privilege
   credentials held outside the repository and browser;
2. stage only checksum-verified, size-bounded artifacts into a per-job
   directory with opaque names;
3. reject path traversal, symlinks, arbitrary URLs, unsupported media, and
   undeclared outputs;
4. write outputs to temporary per-job locations, verify size and content hash,
   then atomically publish the artifact reference to the local ledger;
5. never let a worker overwrite an input, another job's artifact, or a Studio
   task's accepted evidence in place; and
6. expire temporary files and leases under an explicit retention policy.

No Drive access or filesystem transfer is implemented in Session 07.1.

## Session 07.2 — Local persistent queue integration

`studio/worker_queue.py` now persists the version-1 contracts in additive
SQLite tables beside the existing Studio project/task ledger:

- `studio_worker_jobs` stores the manifest, project/task foreign keys,
  idempotency key, current state, authorization, lease, result metadata,
  attempt count, deadline, errors, and output-verification status.
- `studio_worker_registrations` stores explicitly manual, synthetic worker
  capability declarations and availability/expiry.
- `studio_worker_job_events` stores immutable per-job hash-linked lifecycle
  events; `studio_worker_job_outputs` stores immutable output metadata marked
  `unverified_metadata`.

Queue mutations run under SQLite `BEGIN IMMEDIATE` transactions. The
idempotency key is unique; matching requests return the existing job, while
different requests using that key fail. A job may be queued only for an active
task in a Director-approved plan, and its input references must match the
task's current verified local evidence IDs, sizes, and hashes. The Director
who approved the plan must separately authorize execution against the
manifest digest. A manually registered available worker must match workload,
VRAM, and capability constraints before lease.

The dashboard exposes persisted job counts/details, manual synthetic worker
registration and availability, job creation from verified active task
evidence, separate Director authorization, manual lease/start/heartbeat/failure
actions, retry/cancel controls, and explicit expired-lease recovery. Starting a
job only records the local simulated `running` state; it invokes no worker,
FFmpeg, model, or external service. An accepted result moves the queue job to
`succeeded`, but its output reference remains unverified metadata only and is
never copied into Studio evidence or treated as a file. The existing evidence,
Observer, and Director task-completion gates remain authoritative.

Expired leases are changed to `interrupted` and then recovered to `queued` or
`failed` in a transaction, with both audit events retained. Recovery clears
the previous execution authorization, so another explicit Director action is
required before re-leasing. Dashboard startup performs this recovery pass;
the dashboard also offers a manual recovery action.

This is still a local simulation, not an execution service. Output bytes and
checksums are not read or recomputed; worker identity and Director identity
are not authenticated; the queue has no network protocol, Drive adapter,
cross-process worker, or production deployment. SQLite writes are transactional
locally, but future operational integration still needs real artifact
verification, credential management, authenticated worker handshakes, and
additional operational safeguards.

## Verification and remaining gates

Synthetic tests exercise schema and checksum/path validation, CPU/GPU
separation, regional-language capability metadata, idempotency, Director
authorization, bounded leasing and heartbeat, unavailable/disconnected
workers, lease expiry/recovery, stale and duplicate result rejection, output
binding, and audit-chain linkage.

Session 07.2 tests additionally cover approved-task eligibility, verified
evidence association, Director authorization, idempotent persistence, worker
availability, lease and heartbeat ownership, retry and audit behavior,
restart/expiry recovery, duplicate/stale results, output metadata isolation,
and dashboard endpoints.

Before operational integration, still required: formal schema migration and
backup policy, real Director authentication, worker identity/registration
handshake, Colab disconnect behavior in an actual runner, safe Drive
credential/storage integration, byte-level verification at a real-worker
adapter boundary, cleanup/retention, cancellation acknowledgement for running workers,
operational monitoring, and privacy/licensing/consent controls.

## Session 07.3 — Worker output integrity and recovery

The Session 07.2 metadata-only result path has been replaced by byte-verified
local result acceptance. A result must include every declared local output and
the supplied bytes. The queue checks job/task/project ownership, declared
artifact ID, backend/key/media type, non-empty content, exact size, SHA-256
syntax and digest, per-output and aggregate output limits, and the job storage
budget. The server writes bytes to a private (`0700`) database-adjacent worker
artifact directory using a unique staging file (`0600`), reads the stored file
back, verifies its size and digest, and publishes it without overwriting an
existing path. Only then, in the same SQLite transaction as verified-output
metadata, immutable checks, and audit events, can the lifecycle contract mark
the job succeeded.

New `studio_worker_verified_outputs` rows bind the immutable path, digest,
size, attempt, media type, task, and project to the exact job. Append-only
`studio_worker_output_checks` retain awaiting, verified, and failed checks;
hash-linked job events record output hashes/sizes on acceptance and verification
failure reasons without recording content. Failures do not mark a job
succeeded. The running job can be corrected with a valid result on the same
lease or reported failed through the existing lifecycle; retries continue to
require Director control and a new authorization.

On startup and before dashboard job/snapshot reads, the queue verifies the
latest accepted artifact bytes against their persisted digest, size, ownership,
and declaration. Unreferenced staging files and generated orphan files are
removed from the private artifact directory. A missing or tampered accepted
output appends an immutable integrity event and changes the persisted job state
from succeeded to failed, retaining the original success and result events.
Historical Session 07.2 metadata-only rows and successes are not backfilled or
promoted: the dashboard reports their outputs as awaiting verification.

The local dashboard provides a clearly labeled synthetic output-fixture action.
Its local JSON endpoint requires `synthetic: true`, accepts strict base64, caps
the fixture at 1 MiB, and does not execute, render, or publish the bytes. CPU
and GPU test fixtures use the same output-integrity checks. Verified worker
outputs remain separate from allocation evidence; they do not satisfy task
review, Observer, or Director completion gates.

This is not a real worker protocol. Director and worker IDs remain local
assertions, and dashboard fixtures are synthetic. No Colab/Drive, external
network, model, FFmpeg, or publishing integration exists. Local file and
SQLite consistency is checked on reads/restart, but external backup/retention,
authenticated worker identity, and cross-process operational recovery remain
future work.

## Session 07.4 — Local synthetic lifecycle simulation

The dashboard's **Run One Synthetic Job · DEMO** control invokes
`WorkerJobQueue.run_synthetic_job` for one persisted job. It accepts only the
fixed `success`, `failure`, or `interruption` scenarios, and only for a queued
job with unexpired persisted Director authorization, a still-active task in an
approved plan, verified task inputs, an explicitly demo-labeled project, and
an available manually registered worker that passes the existing worker
registration and capability contracts.

The simulator uses the existing queue transitions for lease, start, heartbeat,
failure, expired-lease recovery, Director retry, and result acceptance. Its
success fixture is deterministic JSON bytes containing only job/task/project
IDs, workload, attempt, and a fixed synthetic marker. The bytes are passed to
the Session 07.3 controlled storage and read-back SHA-256 verifier; the
simulator never interprets a job payload as code and calls no shell, FFmpeg,
model, GPU, Colab, Drive, or external API. Success remains a worker-job state,
not Studio task completion or human/Observer review. Audit history includes
the ordinary lifecycle events plus a synthetic-scenario event.

## Session 07.5 — Offline worker connector and capability matching

`studio/worker_connector.py` defines a version-1 provider-independent
`WorkerAdapter` protocol and immutable `WorkerCapabilityProfile`. The contract
surface includes capability discovery, Director-controlled online/offline
state, queue-issued claim/lease metadata, heartbeat, and result submission.
It is an interface only: no Colab, Drive, network, subprocess, FFmpeg, or GPU
adapter is implemented.

Manual registration now persists declared CPU cores, memory MiB, supported
processing types, workload kinds, accelerator/VRAM, and the existing operation,
language, and model capabilities in the same SQLite worker-registration
records. Existing databases gain additive columns in place; legacy rows have
unknown CPU/RAM capacities (`0`) and are therefore visible but cannot be
matched or leased until a new adequately declared worker registration is
provided. Existing project, task, job, evidence, and audit records are not
rewritten.

One deterministic matcher checks workload, CPU, RAM, accelerator, VRAM,
processing operation/type, language, model, manual availability, and
registration expiry. The same matcher is applied by queue leasing,
Session 07.4 synthetic execution, persisted dashboard queue-compatibility
data, and worker-selection controls. Hardware compatibility is reported
separately from current availability; an incompatible or offline job remains
queued. A Director authorization is still required independently of a
compatibility match, and outputs continue through the existing byte/hash and
job/task/project checks.

The dashboard reports each worker's declared profile and each queued job's
compatible and currently available workers. It also displays the
`offline-mock-colab-t4` version-1 profile and per-job compatibility result.
That fixture is synthetic metadata only, is never stored as a registered or
available worker, and is never leaseable. The default profile declares a
T4-like resource shape for compatibility checks, not a promise of Colab
availability or supported model quality.

### Future Colab notebook handoff contract

A future notebook adapter must remain manually started and treat SQLite/local
Studio storage as the source of truth. The safe handoff sequence is:

1. A Director explicitly authorizes an approved queued job; the control plane
   binds authorization to the manifest digest.
2. A worker presents a versioned capability profile and manually transitions
   online. The control plane matches requirements and issues one bounded,
   worker-owned lease; a notebook must not select or self-authorize jobs.
3. Only the current lease owner may heartbeat or submit a result. Job ID,
   lease ID, attempt, worker ID, declared artifact references, and checksums
   must agree with persisted records.
4. The control plane reads staged output bytes in controlled storage and
   verifies ownership, size, and SHA-256 before accepting success. Worker
   metadata alone is not proof of output.
5. Expired/disconnected leases use the existing interruption/recovery/retry
   policy. Stale or duplicate results remain rejected. Worker success never
   completes a Studio task or bypasses evidence, Observer, or Director review.

No endpoint, authentication, notebook, network protocol, shared storage
transfer, or real worker heartbeat exists yet. The local Director ID and
worker IDs remain assertions rather than authenticated identities, SQLite is
not a distributed queue, and declared resource capacity is not independently
measured. The T4 fixture and manually registered workers must not be presented
as live Colab workers or genuine external compute.

**Verification:** Full repository suite: **134 tests passed**. Regression
coverage includes capability and resource matching, offline/unavailable
worker non-leasability, additive legacy-registration migration without
inventing capacity, persisted worker profile data, and dashboard matching
visibility. No warning or test failure was reported.

## Session 07.6 — Offline package handoff preparation

`studio/worker_handoff.py` defines schema-version-1 portable JSON job and
result packages, bounded file read/write helpers, and worker-side validators.
Job exports can be created only for an already Director-authorized queued job,
by that authorizing Director, and for a compatible, manually registered
available worker. Export creates the existing queue lease and running
transition; the package binds the complete versioned manifest digest,
project/task/job IDs, Director authorization, lease ID/attempt/expiry, worker
identity, and the declared provider/capability profile. Packages carry
checksummed artifact references and declarations, not arbitrary paths, URLs,
credentials, or task evidence bytes.

The local dashboard adds an explicit export/download action and a bounded
result-package file import action. Imported packages are validated for exact
schema fields, identifiers, manifest digest, authorization and lease identity,
attempt, declared artifacts, strict base64 bytes, exact sizes, and SHA-256.
Import first runs the existing expired-lease recovery; current SQLite state
must still show the same authorized running job, worker, and lease before
result acceptance. Successful bytes pass through the existing private
artifact store and read-back verifier. Duplicate, stale, replayed,
unauthorized, mismatched, or malformed results are rejected. A failure or
interruption result records a queue failure; only the existing Director retry
action can authorize another attempt.

The handoff currently accepts only explicitly synthetic, manually registered
workers. The `.ipynb` scaffold under `notebooks/` uses the standard library
only to inspect and validate an exported package, package already staged
fixture bytes, or report an interruption. It executes no job code and invokes no hardware, model,
rendering, network, Colab, or Drive API. Its T4 entry is profile metadata,
not evidence that a T4 exists or is authenticated. `provider_id` metadata
allows future adapters to identify local, Colab-mock, or other provider
profiles without changing the current queue semantics.

Inputs are not embedded in handoff packages: local evidence paths and storage
are not transferable to Colab, and Session 07.6 adds no shared artifact
transfer mechanism. Packages are capped at 16 MiB and output bytes at 8 MiB
for this manual JSON flow. This cap is intentionally conservative; larger
media needs a separately reviewed, authenticated, bounded storage adapter.
Imported bytes can be cryptographically verified as persisted bytes, but
their source and compute environment are not authenticated. All this
milestone's accepted worker output remains labeled synthetic; output
verification does not mean real GPU execution, task completion, or Director
acceptance.

**Future manual procedure:** Director approves/authorizes and exports one job
from the local dashboard; the operator transfers the JSON file manually to
the offline notebook environment; the notebook validates it and packages
only explicitly staged fixture bytes; the operator transfers the result JSON
back and imports it through the dashboard. Lease expiry and the existing
recovery flow are the safe fallback if transfer or processing is interrupted.
No Colab session or external account is launched by this procedure.

Failure produces a persisted failed job and follows the existing Director
retry/new-authorization gates. Interruption advances to lease expiry without
waiting, exercises recovery, returns the job to queued (or failure at the
retry limit), and clears execution authorization. Restart uses the same
persisted expiry and artifact checks. Synthetic workers, simulator controls,
output records, and dashboard labels remain explicitly marked as demo.

**Verification:** `PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -v`
completed with **138 tests passing** and no failures. Notebook JSON/code-cell
syntax validation and Python module compilation passed. Session 07.6 readiness:
**PASS for the offline handoff contract and synthetic-only workflow**; real
Colab/Drive integration and authenticated workers remain out of scope.

## Session 07.7 — Verified offline handoff integration

The portable job and result package contract is now version 2. Before the
existing queue issues a lease for export, it reads every referenced task
evidence artifact through the existing allocation evidence store and verifies
the actual bytes against the persisted task association, declared size, and
SHA-256. The package embeds those bytes as canonical base64 alongside their
exact manifest references; total raw input is capped at 8 MiB and the encoded
package at 16 MiB. Version-1 handoff files are rejected rather than treated as
containing verified inputs.

The notebook validates exact package fields, schema versions, manifest
digest, IDs, capability and resource bounds, authorization and lease windows,
safe artifact keys, input-reference equality, and actual input bytes/checksums.
File reads are limited to regular, non-symlink direct children of the notebook
working directory. No archive format is used or extracted. The notebook
generates deterministic inert JSON result bytes from job metadata and input
checksums; it does not process inputs, run declared instructions, or access
hardware. New result files are bounded, created with exclusive/no-follow
semantics, and explicitly labeled `DEMO / UNAUTHENTICATED`.

Dashboard result import requires the configured Director who authorized that
job. The current persisted running lease and worker assertion are matched
before package acceptance; output bytes, declaration association, size, and
SHA-256 are validated before the existing queue artifact store accepts them.
Successful and failed package imports are recorded as immutable worker audit
events with Director ID assertion and unauthenticated-worker provenance.
Dashboard success history is persisted; failures display generic, sanitized
messages. Existing retry, lease-expiry recovery, evidence, Observer, and task
completion controls remain authoritative. This protocol is local/manual and
does not authenticate the Director ID or worker identity.

**Verification:** `PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -v`
completed with **141 tests passing** and no failures or reported warnings.
Python module compilation, notebook JSON/code-cell syntax validation, and
`git diff --check` passed. Session 07.7 readiness: **PASS** for the local,
manual, synthetic-only handoff; actual Colab/Drive integration and
authenticated workers remain out of scope.

## Session 07.8 — Offline end-to-end lifecycle and diagnostic

The Dashboard-to-notebook-to-Dashboard regression now exercises the complete
manual flow against the existing SQLite queue: export a Director-authorized
job package, run the offline notebook's actual validator and deterministic
synthetic result generator, reject an unauthorized import, and accept the
result through the existing Director-controlled import route. The queue
persists the accepted output only after its existing declaration, lease,
ownership, actual-byte, size, and SHA-256 checks. The integration test
confirms a successful worker job leaves its linked Studio task active rather
than bypassing task evidence, review, Observer disagreement, or Director
completion gates.

The local diagnostic is generated from persisted queue, linked plan/task,
output-verification, and hash-chain state. Its versioned JSON reports only
whitelisted identifiers and enums, counts for lifecycle stages, fixed warning
codes, explicit unauthenticated/synthetic provenance, and completion-gate
limitations. It excludes raw failure messages, evidence contents, result
bytes, and audit payloads. The Dashboard displays the report on demand; no
external transport or diagnostic persistence was added.

Lease interruption and requeue events are surfaced as a lifecycle stage, and
attempt counts plus current failure/recovery status remain visible. Existing
state transitions and retry limits are unchanged. These checks demonstrate
offline integration only: the asserted worker and Director IDs are not
cryptographically authenticated, and no Colab, Drive, GPU inference, FFmpeg,
or real media workflow is connected.

**Verification:** the final command
`PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -v` completed
with **141 tests passing**, no failures, and no reported warnings. The
focused worker queue/handoff module passed all **33 tests**. Session 07.8
readiness: **PASS** for the local, synthetic-only, Director-controlled
lifecycle; authenticated remote workers and real Colab/Drive integration
remain blockers for a future milestone.

## Session 07.9 — Remote-worker trust and secure transport contract

### Current trust boundary

Sessions 07.1–07.8 provide the versioned job and worker capability contracts,
Director-authorized SQLite queue, leases/heartbeats/retries, hash-linked audit,
manual offline handoff, controlled artifact storage, byte/hash verification,
and task/Observer completion gates. They do not authenticate a worker or
Director, establish a network transport, or verify remote hardware. The local
IDs and `DEMO / UNAUTHENTICATED` notebook assertions remain non-trustworthy
metadata. No behavior in this section activates a remote worker.

`studio/worker_trust.py` defines schema-version-1, in-memory value contracts
for already-verified credential claims and request metadata. It validates
short credential/request lifetimes, audience, unique allowlisted operation
scopes, exact worker/job/project/task/lease bindings, route/method, body
SHA-256, and bounded IDs/body size. Claim validation is deliberately not
signature validation. Callers must first authenticate the credential using a
trusted, allowlisted issuer and check its live revocation status. The
provider's immutable `(issuer, subject)` identity must map server-side to the
manually approved `worker_id`; the worker ID is never accepted as
self-authentication. `RemoteRequestReplayStore` and
`RemoteCredentialStatusStore` are interfaces only; the test replay fixture is
not durable or suitable for production.

### Future authentication and request contract

Before any network operation, a separately approved service must validate
worker identity using an established mechanism (for example, a managed
OIDC/OAuth issuer with strict issuer/audience/algorithm/key-rotation
validation, optionally reinforced with mTLS). Do not implement JWT, TLS,
signature, or key-management cryptography in this repository. A credential
must be minted only after the existing Director authorization and active
queue lease are persisted. Its claims bind one worker identity, audience,
credential ID, exact job/project/task/lease, explicit scopes, issue time, and
expiry. Wildcards and cross-job scopes are prohibited. The offline v1
contract caps credential lifetime at 15 minutes and never beyond the lease.

Every request must use HTTPS with valid server-certificate verification
(TLS 1.3 preferred; a documented TLS 1.2 floor only if provider support
requires it), and authenticate the worker at the application boundary.
Redirects must not forward credentials. The authenticated request proof must
bind method, canonical path, exact job and lease, request ID, issue/expiry
times, and SHA-256 of the exact body bytes. The offline contract permits only
the fixed `GET job`, `GET inputs`, `POST heartbeat`, and `POST result`
operation paths, caps request freshness at 60 seconds, and checks the body
digest; it does not itself verify a token or request signature.

After cryptographic authentication, validate the claims against the current
SQLite authorization, task/project association, worker registration,
unexpired lease, declared operation scope, and actual artifact declaration.
The control plane—not the worker—selects files and output destinations.
Existing queue idempotency and result/lease checks remain authoritative.
Request IDs must be unique per credential. Production replay protection must
be durable and atomic with the associated queue mutation/idempotency record;
persisting only a nonce before a non-transactional side effect can lose a
legitimate retry after a crash.

### Rotation, revocation, artifact movement, and recovery

Keep signing keys and worker credentials in a managed secret store or
provider-supported protected runtime injection. Never place credentials in
notebooks, manifests, job/result packages, SQLite audit payloads, browser
storage, source control, or logs. Persist credential IDs and status metadata,
not bearer values. Use short-lived credentials, overlap key rotation only
within a defined window, and support immediate issuer/key/worker/job
revocation. Re-check status on every request. Credential expiry or revocation
must stop new operations; the existing lease expiry/recovery path then marks
work interrupted and requires the current retry and Director reauthorization
rules.

Future artifact transfer must be job-scoped, size-bounded, and time-limited.
Use an authenticated transfer service or short-lived, single-object
capabilities issued by the control plane; never accept worker-provided
arbitrary URLs or paths. Stream into a per-job quarantine area, enforce byte
limits while streaming, recompute SHA-256 from received bytes, verify
declaration/lease ownership, and atomically promote only validated outputs
using the existing controlled artifact verifier. A transport interruption
does not imply success: resume only with a fresh authorized request and
idempotency key, otherwise allow lease recovery and reject stale results.

Capability declarations remain claims, not hardware attestation. A worker
must be manually registered and explicitly marked available. Its provider
session, GPU, memory, and capabilities should be rechecked by a trusted
runtime when available; unavailable, disconnected, expired, or unsupported
workers leave jobs queued or trigger existing lease recovery. Remote worker
success never marks Studio tasks complete and cannot bypass evidence,
Observer disagreement, review, or Director completion.

### Threat model

| Threat | Required control |
|---|---|
| Stolen/forged credential or spoofed worker ID | Provider-backed authentication, strict issuer/audience/algorithm validation, short expiry, revocation, no ID-only trust |
| Replay or duplicate submit | Signed/bound request ID and timestamps; durable unique nonce/idempotency record atomically committed with queue mutation |
| Cross-job/project artifact access (IDOR) | Credential and every request bound to the exact worker, job, project, task, and lease; authorize each artifact against current queue state |
| Body substitution or corrupted transfer | Bind exact body digest; stream under limits; hash actual bytes and verify declaration before controlled storage promotion |
| Stale worker or delayed result | Check current lease, attempt, deadline, credential expiry, and queue state at acceptance; recover expired leases through existing transitions |
| Compromised/expired Colab runtime | Treat session as untrusted and ephemeral; no long-lived secrets or authority; revoke/expire credentials and recover lease |
| Credential leakage in browser, package, logs, or notebook | Keep secrets out of those surfaces; use provider-managed secret injection, redaction, rotation, and audit credential IDs only |
| False capability/GPU declaration | Treat capability metadata as unverified; do not claim T4 availability without runtime verification; retain synthetic/mock labels |
| Control-plane or replay-store crash | Durable transactionally consistent queue, nonce/idempotency state, audit event, and result acceptance; backup/restore and integrity checks |
| Malicious or oversized artifact/path | Fixed job-scoped object references, stream limits, quarantine, no archive extraction, safe names, byte-level verification |

The control plane, identity issuer, TLS termination, durable replay/status
store, and artifact store become trusted components in any future deployment.
An authenticated remote worker is still untrusted with respect to other jobs,
Studio approvals, arbitrary code execution, and task completion.

### Zero-cost-first feasibility

**Google Colab:** a manually launched notebook is a disposable, user-session
runtime, not an always-on worker. Free T4 availability, quotas, idle
disconnection, maximum session duration, storage, and outbound transfer are
not guaranteed and may change. Runtime files and process memory are
ephemeral. It is reasonable only for explicitly authorized, manually
transferred, small test inputs with no durable secrets and a short lease; it
cannot provide reliable heartbeat, continuous queue consumption, durable
artifact storage, or an availability promise. A session expiry should be
treated as interruption and handled by existing lease recovery. A mock T4
profile is not evidence of real hardware.

**Hostinger Business shared hosting:** do not assume it can host a persistent
authenticated worker API or queue service. Shared plans are not GPU compute;
long-running background process, inbound listener, Python runtime, outbound
TLS behavior, execution limits, certificate configuration, and database
locking/backup capabilities are plan- and policy-dependent. Verify the
current plan and terms directly before considering even a control-plane
endpoint. The present loopback dashboard and SQLite database are not remotely
reachable or designed as a distributed service. Do not expose them with a
public tunnel. A reliable public control plane, durable object transfer,
secret management, and monitored always-on worker may require a separately
approved hosted service and incur cost; zero-cost-first does not mean
availability or security is free.

No service, network endpoint, credential, secret store, transport, Colab or
Hostinger connection, external dependency, workload execution, or database
migration was added in Session 07.9. The contract tests use synthetic
identifiers and a test-only in-memory replay fixture. Readiness is limited to
offline contract validation; production remote connectivity remains blocked.

**Verification:** `PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -v`
completed with **149 tests passing**, no failures, and no reported warnings.
The focused trust and worker-contract tests passed (**25 tests**). Session
07.9 readiness: **PASS** for the requested offline architecture and
contract-validation scope; deployment and remote worker connectivity remain
**BLOCKED** pending the prerequisites above.
