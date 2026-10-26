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
the Studio project records. The initial planning window is at most seven days;
an approving Director may explicitly extend it once by seven days, subject to
the fourteen-day assignment horizon. Task retries are capped at three attempts.
The workflow only records and routes work; it does not generate content, render
media, publish, or call external services.

The dashboard remains loopback-only. The existing local launch can be used to
serve it on `127.0.0.1:8765`; the server reads the current `studio/dashboard.py`
when it starts. Use `python -m unittest discover -s tests -v` to run the full
test suite.
