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

## Verification and remaining gates

Synthetic tests exercise schema and checksum/path validation, CPU/GPU
separation, regional-language capability metadata, idempotency, Director
authorization, bounded leasing and heartbeat, unavailable/disconnected
workers, lease expiry/recovery, stale and duplicate result rejection, output
binding, and audit-chain linkage.

Before operational integration, still required: durable SQLite schema and
migrations, atomic leasing and result acceptance, real Director authentication,
worker identity/registration handshake, Colab disconnect behavior in the
actual runner, safe Drive credential/storage integration, byte-level artifact
verification, cleanup/retention, cancellation acknowledgement for running
workers, operational monitoring, and privacy/licensing/consent controls.
