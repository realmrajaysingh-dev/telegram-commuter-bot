# telegram-commuter-bot
Automated daily quizzes and news brief for Telegram

## Local Studio task allocation

The local Studio dashboard includes a Director-controlled work-allocation
prototype. Move a project from Draft to Queued or Active using the existing
Director action, select it in CEO Work Allocation, and generate one of the four
deterministic task templates. Generation saves a draft only. A configured
Director must explicitly approve the plan before its dependency-free tasks
become ready.

Plans, assigned registered roles, evidence requirements, verification gates,
task transitions, retry/failure records, Observer findings, approvals, and
hash-chained audit events are persisted in the same local SQLite database as
the Studio project records. Before a task can enter Review or Completed, each
required evidence type must have a non-empty UTF-8 text artifact stored in a
restricted local directory beside the database. The task record references the
artifact; its task association, type, file, size, and SHA-256 hash are rechecked
at both transitions. The dashboard accepts artifact text only (not paths,
uploads, or external URLs), with a 256 KiB per-artifact limit. Existing
label-only evidence is not migrated or treated as proof; a task in Review must
receive real evidence and be submitted for review again.

Artifact integrity does not verify factual accuracy, rights, licensing, or
consent of the submitted text. The initial planning window is at most seven days;
an approving Director may explicitly extend it once by seven days, subject to
the fourteen-day assignment horizon. Task retries are capped at three attempts.
The workflow only records and routes work; it does not generate content, render
media, publish, or call external services.

The dashboard remains loopback-only. The existing local launch can be used to
serve it on `127.0.0.1:8765`; the server reads the current `studio/dashboard.py`
when it starts. Use `python -m unittest discover -s tests -v` to run the full
test suite.
