# Free / Existing Infrastructure Options

**Status:** Planning inventory only. “Free” is not guaranteed, and no service or paid plan is approved by this document.

## Verified from this repository

- **GitHub repository and Actions:** repository contains a workflow configured for Ubuntu runners, manual dispatch, and five cron triggers per day. Actions usage quotas and repository settings determine availability; they are not verified here. The workflow installs dependencies on each run.
- **GitHub-hosted static files:** the repository contains static HTML, a generated article, templates, and an image. The repository references a GitHub Pages URL, but no Pages configuration or live reachability was validated in this audit.
- **Telegram Bot API:** `bot.py` calls `sendPhoto` and `sendPoll` over HTTPS using environment-provided secrets. A configured bot/channel and successful live delivery were not validated.
- **Gemini API:** `bot.py` uses `google-generativeai` and `GEMINI_API_KEY`. API access, quota, current free allowance, terms, and data handling were not validated. “Free tier” is not a spending guarantee or an authorization to send data.
- **Local Python/FFmpeg/Colab:** Python exists in the current workflow via setup-python; there is no FFmpeg or Colab configuration evidenced in tracked repository files.

## Candidate low-cost prototype components (require review)

| Need | Candidate starting point | Guardrail |
|---|---|---|
| Durable code and review | Existing GitHub repository and pull requests | Keep credentials and private footage out of commits; tighten workflow permissions. |
| Static preview | Local HTML preview or a private test artifact | Do not assume repository Pages is private; only publish content cleared for public access. |
| Local orchestration tests | Python standard library and synthetic fixtures | Keep the first milestone offline and avoid new dependencies. |
| Workflow experiments | Manual GitHub Actions dispatch after security review | No production dispatch or schedule changes in this assignment; verify quota first. |
| Media processing | Local FFmpeg only after availability, license, and resource review | Do not upload private footage to a third-party service without permission. |
| Director input | Versioned CSV/XLSX template imported locally | Validate schema and formulas; reject external links/macros; never put secrets in a sheet. |
| Asset references | Local path or Google Drive reference with metadata | Drive is not evidenced as configured; avoid public links and preserve access/rights restrictions. |
| Telegram control | Existing bot integration only after auth and command allowlisting review | Current bot is outbound-oriented; do not treat it as an authenticated Director console. |
| Model-assisted drafts | Existing Gemini integration only after quota, privacy, safety, and billing approval | No automatic paid fallback; zero spend unless the Director authorizes a bounded amount. |

## Cost and privacy controls

Default to zero authorized paid spend. Before any external call, disclose provider, expected cost or quota use, data sent, retention terms, and whether the provider can train on inputs. A free tier may change, be exhausted, require a payment method, or impose terms that are unsuitable. If a quota or cost status cannot be verified, stop the call and ask the Director. Do not automatically substitute a paid model, stock asset, storage tier, or transcoding service.

Use local/synthetic data for early testing. Store only references and minimum metadata for private assets. Apply access control, retention, deletion, and consent requirements before using Drive, Colab, external renderers, or shared runners with confidential media.

## Dependencies and supply chain

The current workflow installs three Python packages without version pins and installs the Playwright browser at runtime. No dependency manifest, lockfile, test command, or automated dependency review is tracked. A later implementation milestone should first record the actual runtime dependency set and supported Python version, then pin and review packages; do not add dependencies for the architecture draft. Pin GitHub Actions by immutable commit SHA as part of a separately reviewed hardening change.
