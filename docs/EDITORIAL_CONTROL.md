# Editorial Control, Evidence, and Approval

**Status:** Proposed editorial governance; not implemented.

## Editorial policy

The Director owns a versioned editorial policy specifying covered topics, audience, languages, formats, source standards, corrections, prohibited content, disclosures, privacy boundaries, allowed asset sources, and review expectations. Unassigned discovery is permitted only inside the currently approved policy and produces private drafts. A policy change is explicit and auditable; a draft, source, model response, or CSV cell cannot change policy.

## Four required and distinct review gates

All gates are required for any release candidate. Each returns `pass`, `revise`, or `block`, with reviewer identity/type, timestamp, rubric version, artifact hash, supporting evidence, reasons, and dissent. `revise` creates a new artifact version and invalidates later approvals. A gate is not passed by silence, elapsed time, a model confidence score, or a quorum.

### Gate 1 — Source and factual verification

Check every material factual claim against identified, accessible evidence. Preserve source title/creator, URL or restricted reference, publication and access times, relevant excerpt/page/timecode, claim-to-source mapping, reliability limitations, and contradictory evidence. Prefer primary records and separately obtained corroboration for high-impact claims. Mark uncertainty and distinguish fact, analysis, forecast, and opinion. A language model may help extract claims or summarize evidence, but multiple outputs from models are not independent factual verification.

If a claim cannot be substantiated, remove it, qualify it, or block the item. Do not fabricate references, quotes, dates, benchmarks, or source access. The reviewer must be independent of the claim's authoring task and disclose source/model conflicts.

### Gate 2 — Editorial and creative verification

Assess relevance to assignment and audience, structure, clarity, originality, tone, multilingual consistency, attribution, disclosure, harmful stereotypes, privacy, corrections context, and whether headlines/visual framing exaggerate evidence. Preserve edit notes and disagreements. This gate cannot waive factual or rights checks.

### Gate 3 — Technical, visual, and licensing verification

Validate artifact integrity and rendering for intended format, required dimensions/codec/subtitles/accessibility, factual consistency between variants, safe HTML/media handling, links/metadata, and platform constraints. Verify each visual/audio/video asset's owner/source, license, attribution, permitted uses/edits, territory, duration/expiry, consent/releases where relevant, and evidence. A URL or public availability is not a license. Unclear rights block use.

### Gate 4 — Independent final quality review

A reviewer independent from drafting and prior gate decisions checks the final immutable artifact bundle, all prior evidence/decisions, unresolved dissent, the exact intended destination, metadata/disclosures, and remaining risks. This review is not a substitute for Gates 1–3. It yields a release recommendation and a hash-bound decision.

## Human authorization

After all gates pass, the Owner/Director separately approves the exact artifact hash, channel(s), timing, disclosure, and any authorized spend. The system then creates a one-use, expiring release authorization. Any content, asset, destination, or timing change invalidates or requires renewed approval. Current repository automation does not implement these gates or this authorization model.

## Evidence, disagreement, and correction

Keep source manifests, claim matrices, asset rights records, reviewer evidence, rubric versions, model/tool provenance, failures, disagreements, and approval decisions linked to artifact hashes. Preserve contrary evidence rather than averaging it away. Record a named disposition for each material disagreement. Published correction requests enter a human-owned workflow; do not silently rewrite history.

## Existing baseline

The current bot generates a JSON dispatch and long-form HTML from Gemini prompts and uses a fixed set of image URLs. The audited code has no claim-to-source evidence map or independent reviews. Its generated HTML is placed into templates without an HTML sanitizer. Its generated quiz supplies an asserted answer. These are current implementation facts, not claims that its content is verified.
