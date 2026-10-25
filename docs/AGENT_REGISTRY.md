# Voocks Media AI Studio — Functional Agent Registry

**Status:** Proposed role inventory; none of these roles are implemented by this document.
**Count:** 220 functional role definitions across 11 departments. Roles are responsibilities, not commitments to create 220 models, workers, accounts, or concurrent agents. A person, deterministic tool, or approved model may cover multiple roles if access and review independence are preserved.

## Registry conventions

- IDs `D01-R01` through `D11-R20` are stable proposed identifiers. A deployed registry would add owner, version, input/output schema, tools, data scope, resource limit, and enabled state.
- **Dependencies** name prerequisite roles. `—` means no registry prerequisite. Dependencies express workflow prerequisites, not authority to approve a result.
- **Checks** specify role-level acceptance evidence. For output with factual claims, applicable source and factual verification (V1) is mandatory. For editorial/creative assets V2 applies. For media, technical, visual, and rights clearance V3 applies. V4 is the independent final review of every release candidate. `Ops` means deterministic test, source record, or audit evidence; it is not a publication gate.
- **V1 — Source/factual:** claim-to-evidence mapping, primary/source review, corroboration where appropriate, dates, uncertainty, contradiction handling. Model agreement is not independent evidence.
- **V2 — Editorial/creative:** assignment fit, clarity, originality, audience, language, attribution, disclosure, and creative brief.
- **V3 — Technical/visual/licensing:** integrity, render/accessibility/format checks, source/license/consent/expiry, platform constraints, and asset provenance.
- **V4 — Independent final:** reviewer not responsible for drafting or earlier gates verifies the final hash, all prior evidence, dissent, destination, and release readiness. V4 recommends; Director authorization remains separate.
- Role completion never grants spending, account, legal, or publication authority. An absent Director cannot be replaced by an agent.

## D01 — Governance and executive operations (20)

| ID | Functional role and responsibility | Depends on | Verification requirement |
|---|---|---|---|
| D01-R01 | AI CEO Operator — summarize portfolio and propose priorities | D01-R02, D01-R03, D01-R16 | Ops: recommendations link to tasks, scope, and evidence; no self-approval |
| D01-R02 | Director Intake Steward — capture authenticated assignments and decisions | — | Ops: actor, timestamp, original input hash, schema result |
| D01-R03 | Editorial Policy Custodian — version policy and permitted-topic rules | D01-R02 | Ops: approved policy version and change history; Director approval recorded |
| D01-R04 | Work Divider — decompose assignments into task graphs and criteria | D01-R02, D01-R03 | Ops: dependency graph valid; acceptance criteria and output owner present |
| D01-R05 | Assignment Horizon Controller — enforce 7-day period, one extension, 14-day maximum | D01-R02 | Ops: boundary-date and extension-count tests pass |
| D01-R06 | Scheduler — trigger only eligible, authorized internal work | D01-R04, D01-R05 | Ops: schedules never create publish or spend permission |
| D01-R07 | Queue Steward — maintain durable state and dependency transitions | D01-R04 | Ops: restart/replay, state-transition, and idempotency tests |
| D01-R08 | Resource Controller — enforce per-task and portfolio ceilings | D01-R03, D01-R07 | Ops: zero-spend default; budget exhaustion blocks and alerts |
| D01-R09 | Watchdog — detect stuck, stale, repeated, or unsafe states | D01-R07, D01-R08 | Ops: synthetic alert fixtures and fail-closed behavior |
| D01-R10 | Incident Coordinator — record, contain, and escalate incidents | D01-R09, D01-R12 | Ops: timeline, owner, containment, and disposition recorded |
| D01-R11 | Change-Control Steward — review schema, policy, workflow, and role changes | D01-R03, D01-R12 | Ops: approved diff, compatibility evidence, rollback path |
| D01-R12 | Audit Ledger Steward — preserve append-only task and approval history | D01-R07 | Ops: linked hashes, actor/time, retention and export test |
| D01-R13 | Privacy Steward — enforce data minimization, retention, and access rules | D01-R03 | Ops: data-classification and retention evidence; no secret in records |
| D01-R14 | Access-Control Steward — least-privilege identities and credential handling | D01-R03, D01-R13 | Ops: permission inventory; secret scanning and rotation evidence |
| D01-R15 | Policy Compliance Steward — map platform, privacy, and editorial constraints | D01-R03 | Ops: versioned checklist; unresolved compliance issue blocks release |
| D01-R16 | Portfolio Metrics Analyst — report status, quality, throughput, and cost | D01-R07, D01-R08, D01-R12 | Ops: metrics trace to records; distinguish estimate from measured value |
| D01-R17 | Provider Review Steward — assess external providers, terms, and costs | D01-R08, D01-R13 | Ops: provider, data, price/quota, and approval evidence |
| D01-R18 | Knowledge Curator — maintain approved playbooks and reusable lessons | D01-R11, D01-R12 | Ops: version, provenance, and review date |
| D01-R19 | Capacity Planner — forecast role/workload capacity and bottlenecks | D01-R16 | Ops: assumptions, horizon, and resource estimate recorded |
| D01-R20 | Continuity Coordinator — prepare recovery and manual operating procedures | D01-R09, D01-R10 | Ops: recovery exercise and manual fallback record |

## D02 — Research and source intelligence (20)

| ID | Functional role and responsibility | Depends on | Verification requirement |
|---|---|---|---|
| D02-R01 | Assignment Research Planner — translate brief into research questions | D01-R04, D01-R03 | Ops: scope and unanswered questions approved |
| D02-R02 | Topic Discovery Analyst — propose topics inside editorial policy | D02-R01, D01-R03 | V1: discovery signals linked; no automatic factual assertion |
| D02-R03 | Primary-Source Finder — locate original records and statements | D02-R01 | V1: source identity, URL/reference, access date, relevant passage |
| D02-R04 | Academic Literature Researcher — locate scholarly evidence | D02-R01 | V1: bibliographic record, version, methods and limitations |
| D02-R05 | Public-Records Researcher — locate official filings, datasets, and notices | D02-R01 | V1: originating agency, record ID, date, and extraction notes |
| D02-R06 | Expert Interview Researcher — prepare interview targets and questions | D02-R01, D01-R13 | V1: identity/consent and quote provenance; no invented interview |
| D02-R07 | Source Reliability Analyst — document source track record and constraints | D02-R03 | V1: reliability rationale and conflict disclosure |
| D02-R08 | Claim Extractor — identify verifiable claims in drafts and sources | D02-R03 | V1: claim ledger coverage and source links |
| D02-R09 | Claim-to-Source Mapper — map each material claim to evidence | D02-R08, D02-R03 | V1: every material claim has traceable support or is flagged |
| D02-R10 | Independent Corroboration Researcher — seek distinct supporting evidence | D02-R09, D02-R03 | V1: independent source lineage and conflicting findings retained |
| D02-R11 | Contradiction Analyst — surface conflict, uncertainty, and counterevidence | D02-R09, D02-R10 | V1: dispute log and resolution/qualification recommendation |
| D02-R12 | Quantitative Research Analyst — inspect numeric claims and calculations | D02-R03 | V1: reproducible calculation, units, population, period, and source |
| D02-R13 | Data Provenance Analyst — retain dataset origin and transformation lineage | D02-R12 | V1: source/license, version, transformations, and checksum |
| D02-R14 | Timeline Researcher — validate chronology and event dates | D02-R03, D02-R05 | V1: date references and timezone/sequence assumptions |
| D02-R15 | Quote and Attribution Verifier — check exact wording and attribution | D02-R03, D02-R06 | V1: quote text, context, source locator, permission where needed |
| D02-R16 | Research Librarian — organize source dossiers and reuse permissions | D02-R03, D01-R12 | Ops: indexed references, access class, retention and rights flags |
| D02-R17 | Research Update Monitor — identify material changes to cited evidence | D02-R09, D01-R06 | V1: update timestamp and impact assessment; no silent replacement |
| D02-R18 | Search Bias Reviewer — test for omitted perspectives and selection bias | D02-R09, D02-R11 | V1: search scope, missing cohorts, and limitations recorded |
| D02-R19 | Field Research Coordinator — plan safe, consent-based field research | D02-R01, D01-R13 | Ops: human plan, consent, safety, and data-minimization checks |
| D02-R20 | Evidence Packager — assemble source dossier for Gate 1 | D02-R09, D02-R11, D02-R13 | V1: complete indexed evidence pack; reviewer independence recorded |

## D03 — News and research-based articles (20)

| ID | Functional role and responsibility | Depends on | Verification requirement |
|---|---|---|---|
| D03-R01 | News Desk Editor — assign and shape an approved news brief | D02-R01, D01-R03 | V2: assignment fit, angle, deadline, and audience |
| D03-R02 | News Reporter — draft a sourced news story | D03-R01, D02-R20 | V1+V2: claim ledger and attribution before handoff |
| D03-R03 | Longform Article Writer — develop a research-based article | D03-R01, D02-R20 | V1+V2: claims map to dossier; analysis labeled |
| D03-R04 | Explainer Writer — explain complex topics for a defined audience | D03-R01, D02-R20 | V1+V2: technical definitions and audience rubric |
| D03-R05 | Interview Feature Writer — shape consented interview into feature | D02-R06, D02-R15, D03-R01 | V1+V2: quote-context audit and consent record |
| D03-R06 | Headline and Deck Editor — create accurate headlines and summaries | D03-R02, D03-R03 | V1+V2: no claim stronger than story evidence |
| D03-R07 | Copy Editor — correct grammar, style, and consistency | D03-R02 | V2: tracked edit and style checklist |
| D03-R08 | Structural Editor — improve narrative and evidence flow | D03-R03, D02-R20 | V1+V2: all material edits returned for factual recheck |
| D03-R09 | Fact-Check Editor — coordinate first-gate claim verification | D02-R20, D03-R02 | V1: independent claim coverage, evidence, disputes, sign-off |
| D03-R10 | Attribution Editor — ensure source and quotation credit | D02-R15, D03-R02 | V1+V2: attribution and permissions audit |
| D03-R11 | Data Visualization Brief Writer — specify evidence-led visual | D02-R12, D03-R03 | V1+V2: figures and labels trace to source data |
| D03-R12 | Article Metadata Editor — prepare dates, descriptions, and schema | D03-R06, D03-R02 | V1+V3: metadata matches approved content and date |
| D03-R13 | Article HTML Producer — produce safe template-bound output | D03-R03, D03-R12 | V3: sanitizer, escaping, valid structure and link checks |
| D03-R14 | Article Accessibility Editor — assess structure, alt text, readability | D03-R13, D07-R13 | V2+V3: keyboard/semantic/accessibility checklist |
| D03-R15 | Corrections Editor — record and route correction requests | D01-R12, D03-R09 | Ops+V1: request, evidence, decision, and public correction link |
| D03-R16 | News Brief Producer — condense verified story into short brief | D03-R02, D03-R09 | V1+V2: every retained claim has evidence |
| D03-R17 | Technical Review Writer — explain engineering claims without overstatement | D02-R04, D02-R12, D03-R03 | V1+V2: specialist terminology and uncertainty review |
| D03-R18 | Article Localization Handoff Editor — prepare translation-ready source | D03-R09, D08-R01 | V1+V2: locked facts, terms, names, and translatable strings |
| D03-R19 | News Package Producer — assemble article, metadata, assets, evidence | D03-R13, D05-R20, D09-R20 | V1+V2+V3: manifest completeness and hash |
| D03-R20 | Independent Article Quality Reviewer — final independent review | D03-R19 | V4: artifact hash, all gates, dissent and destination readiness |

## D04 — Monthly digital magazine (20)

| ID | Functional role and responsibility | Depends on | Verification requirement |
|---|---|---|---|
| D04-R01 | Magazine Editor-in-Chief — set approved issue theme and lineup | D01-R03, D01-R04 | V2: lineup and issue scope trace to assignment |
| D04-R02 | Issue Planner — sequence formats, contributors, and deadlines | D04-R01, D01-R05 | Ops+V2: dependency/date plan within horizon |
| D04-R03 | Section Editor — commission and edit section packages | D04-R02, D03-R01 | V2: section brief and acceptance criteria |
| D04-R04 | Feature Commissioning Editor — prepare long-form commissions | D04-R01, D02-R01 | V2: approved question, source approach, scope |
| D04-R05 | Magazine Reporter — produce assigned reported feature | D04-R04, D02-R20 | V1+V2: evidence dossier and edit history |
| D04-R06 | Review Essay Editor — manage evidence-based criticism/reviews | D04-R03, D02-R20 | V1+V2: fact/opinion distinction and conflict disclosure |
| D04-R07 | Interview Editor — prepare and edit magazine interviews | D02-R06, D02-R15, D04-R03 | V1+V2: consent, quote verification, context |
| D04-R08 | Columns Editor — commission recurring commentary | D04-R03, D01-R03 | V1+V2: claims checked; opinion clearly labeled |
| D04-R09 | Issue Copy Editor — unify style and consistency | D04-R05, D04-R06 | V2: issue style and tracked changes |
| D04-R10 | Issue Fact-Check Coordinator — coordinate issue claim checks | D02-R20, D04-R05 | V1: section-level evidence coverage and disputes |
| D04-R11 | Issue Art Director — define issue visual system | D04-R01, D07-R01 | V2+V3: approved design brief and asset constraints |
| D04-R12 | Layout Producer — assemble digital pages and contents | D04-R09, D07-R11 | V3: pagination, links, responsive/print preview |
| D04-R13 | Issue Cover Designer — design issue cover and thumbnail | D04-R11, D07-R04 | V2+V3: brief fit, rights, final-size check |
| D04-R14 | Issue Accessibility Editor — check navigability and accessible export | D04-R12, D07-R13 | V3: semantic, contrast, reading-order, alt-text test |
| D04-R15 | Magazine Localization Editor — manage translated issue variants | D04-R09, D08-R02 | V1+V2: facts and voice compared across versions |
| D04-R16 | Issue Rights Coordinator — collect contributor and artwork permissions | D09-R01, D09-R08, D04-R11 | V3: signed/traceable permissions and usage scope |
| D04-R17 | Issue Metadata Editor — manage issue identifiers and metadata | D04-R12 | V3: manifest consistency and metadata validation |
| D04-R18 | Magazine Production Tester — inspect final bundle across devices | D04-R12, D04-R14 | V3: reproducible render/device checklist |
| D04-R19 | Issue Release Packager — hash and bundle approved issue files | D04-R17, D04-R18, D04-R16 | V3: file inventory, checksums, no missing evidence |
| D04-R20 | Independent Issue Quality Reviewer — inspect complete issue | D04-R19 | V4: independent issue-wide review and hash-bound recommendation |

## D05 — Documentary and short-form production (20)

| ID | Functional role and responsibility | Depends on | Verification requirement |
|---|---|---|---|
| D05-R01 | Documentary Development Producer — shape approved subject and scope | D01-R03, D02-R01 | V2: scope, access and risk brief |
| D05-R02 | Documentary Researcher — build production research dossier | D02-R20, D05-R01 | V1: claims, source access, and open questions |
| D05-R03 | Story Producer — shape evidence-led narrative outline | D05-R02, D05-R01 | V1+V2: beat-to-evidence trace |
| D05-R04 | Documentary Scriptwriter — draft narration and scenes | D05-R03, D02-R20 | V1+V2: line-level evidence and disclosure notes |
| D05-R05 | Interview Producer — arrange interviews and releases | D02-R06, D01-R13 | Ops+V3: consent, scope, release, and secure contact record |
| D05-R06 | Interview Transcript Editor — prepare timecoded transcript | D05-R05, D02-R15 | V1: audio/timecode comparison and quote accuracy |
| D05-R07 | Field Production Planner — define locations, crew, and safe capture | D05-R03, D09-R01 | Ops+V3: safety, permissions and asset plan |
| D05-R08 | Shot-List Planner — translate scenes into required footage | D05-R03, D05-R04 | V2+V3: coverage-to-outline map |
| D05-R09 | Storyboard Artist — visualize shot and sequence plan | D05-R08, D07-R02 | V2+V3: scene alignment and rights status |
| D05-R10 | Footage Researcher — locate usable archival/reference footage | D05-R02, D09-R02 | V1+V3: provenance, timecode, license status |
| D05-R11 | Edit Decision List Editor — create source-linked edit plan | D05-R06, D05-R10 | V1+V3: source/timecode map and version |
| D05-R12 | Video Editor — assemble approved footage and audio | D05-R11, D09-R20 | V3: project file, media links, playback and export check |
| D05-R13 | Motion Graphics Designer — produce titles and visual explainers | D05-R03, D07-R06 | V1+V2+V3: data/source labels and design review |
| D05-R14 | Sound Editor — edit dialogue, effects, and mix | D05-R12, D09-R09 | V3: loudness, intelligibility, rights and export tests |
| D05-R15 | Color and Picture Finisher — complete picture finishing | D05-R12 | V3: color/render QC and source-preservation check |
| D05-R16 | Short-Form Cutter — adapt approved long-form into short edits | D05-R12, D05-R20 | V1+V2+V3: clip context, claim integrity, aspect ratios |
| D05-R17 | Documentary Fact-Check Editor — audit narrative against evidence | D05-R04, D02-R20, D05-R11 | V1: claim/timecode/source and fairness record |
| D05-R18 | Documentary Accessibility Producer — prepare captions and audio description | D05-R12, D08-R05 | V3: synchronization, completeness, accessibility sample |
| D05-R19 | Video Delivery Tester — check codecs, dimensions, playback and metadata | D05-R14, D05-R15, D05-R18 | V3: machine checks plus human playback record |
| D05-R20 | Independent Film Quality Reviewer — inspect locked picture and evidence | D05-R17, D05-R19, D09-R20 | V4: hash-bound final review, no author self-review |

## D06 — Social media content and marketing (20)

| ID | Functional role and responsibility | Depends on | Verification requirement |
|---|---|---|---|
| D06-R01 | Social Strategy Planner — propose channel-specific campaign goals | D01-R03, D01-R04 | V2: audience, objective, and allowed channel |
| D06-R02 | Campaign Calendar Planner — schedule draft deliverables | D06-R01, D01-R05 | Ops: only authorized internal deadlines; no publish trigger |
| D06-R03 | Social Copywriter — draft platform-specific copy | D03-R19, D06-R01 | V1+V2: source and claim trace; character/format checks |
| D06-R04 | Short-Form Scriptwriter — write short video scripts | D05-R20, D06-R01 | V1+V2: script claims and context preservation |
| D06-R05 | Thread and Carousel Editor — package sequential explainer content | D03-R19, D07-R05 | V1+V2+V3: sequence, claims, visual legibility |
| D06-R06 | Social Headline Editor — create channel-appropriate hooks | D06-R03 | V1+V2: no sensational claim beyond evidence |
| D06-R07 | Caption and Alt-Text Writer — produce descriptive accessibility text | D07-R05, D07-R13 | V2+V3: image/video checked against description |
| D06-R08 | Community Conversation Analyst — summarize public feedback safely | D06-R01, D01-R13 | Ops+V2: aggregate only; privacy and harassment safeguards |
| D06-R09 | Community Response Drafter — prepare human-reviewed responses | D06-R08, D01-R03 | V1+V2: no unsolicited personal data or unsupported claims |
| D06-R10 | Campaign Fact-Check Liaison — map promotional claims to evidence | D02-R20, D06-R03 | V1: claim map and prohibited guarantee check |
| D06-R11 | Brand Voice Editor — check approved voice and terminology | D01-R03, D06-R03 | V2: voice guide and change notes |
| D06-R12 | CTA and Offer Editor — validate calls-to-action and offer terms | D06-R01, D01-R15 | V2+Ops: destination and offer terms verified |
| D06-R13 | Social Asset Producer — package image/video variants | D07-R19, D09-R20 | V3: hashes, sizes, crops, license references |
| D06-R14 | Campaign Localization Coordinator — plan localized variants | D06-R01 | V2: language, market, deadline and reviewer assigned |
| D06-R15 | Marketing Disclosure Reviewer — check sponsorship and promotional disclosure | D01-R15, D06-R12 | V2+Ops: disclosure present and correctly placed |
| D06-R16 | Campaign Performance Analyst — report measured campaign outcomes | D06-R02, D11-R17 | Ops: metric source, attribution limits, privacy aggregation |
| D06-R17 | Social Listening Researcher — categorize relevant public signals | D06-R08, D01-R13 | Ops: source, time window, privacy minimization |
| D06-R18 | Campaign Rights Reviewer — check talent, music and brand asset rights | D09-R08, D09-R09, D06-R13 | V3: rights scope for each channel and territory |
| D06-R19 | Campaign Release Packager — assemble copy, variants and approvals | D06-R10, D06-R13, D06-R15 | V1+V2+V3: manifest and artifact hashes |
| D06-R20 | Independent Campaign Reviewer — inspect final campaign bundle | D06-R19, D03-R20 | V4: destination-specific, hash-bound recommendation |

## D07 — Graphic design, characters, costumes, and storyboarding (20)

| ID | Functional role and responsibility | Depends on | Verification requirement |
|---|---|---|---|
| D07-R01 | Creative Director — establish approved visual concept and constraints | D01-R03, D01-R04 | V2: concept brief and prohibited elements |
| D07-R02 | Storyboard Designer — develop visual sequence frames | D05-R03, D07-R01 | V2+V3: narrative mapping, provenance, review notes |
| D07-R03 | Character Designer — develop original character concepts | D07-R01 | V2+V3: brief fit, originality review, asset provenance |
| D07-R04 | Costume Designer — design production costume concepts | D07-R03, D07-R01 | V2+V3: character continuity, materials and rights |
| D07-R05 | Graphic Designer — create static editorial graphics | D07-R01, D02-R12 | V1+V2+V3: data source, legibility, export and rights |
| D07-R06 | Motion Designer — design motion systems and explainers | D07-R01 | V1+V2+V3: timing, evidence, export tests |
| D07-R07 | Infographic Designer — translate verified data to visuals | D02-R12, D07-R05 | V1+V3: source, scale, units, labels, accessibility |
| D07-R08 | Cover and Thumbnail Designer — create discoverable packaging | D07-R01, D03-R06 | V1+V2+V3: no misleading imagery/headline, crop checks |
| D07-R09 | Brand Systems Designer — maintain reusable identity assets | D01-R03, D07-R01 | V2+V3: versioned tokens, license and use guide |
| D07-R10 | Layout Designer — arrange editorial pages and cards | D04-R11, D07-R05 | V3: dimensions, reading order, responsive output |
| D07-R11 | Publication Artworker — prepare final production artwork | D07-R10 | V3: preflight, fonts, embedded/linked asset records |
| D07-R12 | Visual Researcher — source visual references and context | D02-R01, D09-R01 | V1+V3: source provenance and no implied license |
| D07-R13 | Accessibility Visual Reviewer — review contrast and visual access | D07-R05, D07-R10 | V3: contrast, text alternatives, non-color cues |
| D07-R14 | Visual Continuity Supervisor — track characters/props across scenes | D07-R03, D07-R04, D05-R09 | V2+V3: continuity sheet and approved changes |
| D07-R15 | Environment and Set Concept Artist — design production environments | D07-R01, D05-R07 | V2+V3: brief, location/asset rights, safety notes |
| D07-R16 | Design QA Specialist — inspect final visual artifacts | D07-R11, D07-R13 | V3: independent render checklist and hash |
| D07-R17 | Image Integrity Reviewer — detect misleading edits or synthetic imagery risks | D07-R05, D09-R20 | V1+V3: edit provenance, label/disclosure decision |
| D07-R18 | Typography and Localization Designer — validate script/font coverage | D08-R02, D07-R10 | V3: glyph coverage, line breaks, legibility per locale |
| D07-R19 | Multi-Format Asset Adapter — export approved crops and aspect ratios | D07-R16 | V3: dimensions, crop safety, source hash mapping |
| D07-R20 | Independent Visual Quality Reviewer — inspect final visual package | D07-R16, D09-R20 | V4: final hash, rights evidence, destination fit |

## D08 — Multilingual scripts, dubbing, subtitles, and accessibility (20)

| ID | Functional role and responsibility | Depends on | Verification requirement |
|---|---|---|---|
| D08-R01 | Localization Program Planner — scope locales and review capacity | D01-R04 | Ops+V2: locale, audience, reviewer, source version |
| D08-R02 | Translator — translate approved source material | D03-R18, D08-R01 | V1+V2: source alignment, terminology, translator record |
| D08-R03 | Translation Editor — review fluency and meaning | D08-R02 | V1+V2: bilingual review and change log |
| D08-R04 | Localization Fact Checker — compare translated claims to evidence | D02-R20, D08-R03 | V1: names, numbers, dates, qualifiers preserved |
| D08-R05 | Subtitle Writer — create timed, readable captions | D05-R12, D08-R03 | V1+V3: transcript accuracy, timing, line-length checks |
| D08-R06 | Subtitle Synchronizer — align captions to final audio | D08-R05, D05-R19 | V3: timecode and sync sample |
| D08-R07 | Dubbing Script Adapter — adapt text for spoken duration | D08-R03, D05-R04 | V1+V2: no altered factual meaning; timing record |
| D08-R08 | Voice Casting Coordinator — propose voice talent and consent | D08-R01, D09-R09 | V3: consent, rights, attribution, usage term |
| D08-R09 | Dubbing Producer — coordinate approved recording session | D08-R07, D08-R08 | V3: session and release records |
| D08-R10 | Voice Director — review performance against intent | D08-R09, D05-R03 | V2: direction notes and approved take ID |
| D08-R11 | Audio Description Writer — describe meaningful visual information | D05-R12, D08-R03 | V2+V3: scene/timecode mapping and accessibility review |
| D08-R12 | Pronunciation and Terminology Editor — maintain locale glossaries | D08-R03, D02-R17 | V1+V2: approved term and pronunciation references |
| D08-R13 | Script Continuity Editor — sync narration and scene references | D08-R07, D05-R11 | V1+V2: script-to-edit alignment |
| D08-R14 | Multilingual Quality Reviewer — review complete locale bundle | D08-R04, D08-R06, D08-R11 | V2+V3: locale checklist and final version match |
| D08-R15 | Voice Recording Editor — edit authorized dubbing assets | D08-R09, D09-R09 | V3: source release, edit log, audio QC |
| D08-R16 | Cultural Context Reviewer — flag locale-specific ambiguity or harm | D08-R03, D01-R15 | V2: reviewed concerns and human disposition |
| D08-R17 | Language Metadata Editor — maintain locale tags and encoding | D08-R14, D05-R19 | V3: language codes, encoding, platform metadata |
| D08-R18 | Accessible Transcript Editor — produce corrected transcript formats | D05-R19, D08-R05 | V1+V3: completeness and semantic structure |
| D08-R19 | Localization Package Producer — bundle matched scripts/audio/subtitles | D08-R14, D08-R17 | V3: hash and language/version consistency |
| D08-R20 | Independent Localization Reviewer — review final multilingual assets | D08-R19, D03-R20 | V4: independent locale and factual consistency review |

## D09 — Research footage, asset discovery, and rights management (20)

| ID | Functional role and responsibility | Depends on | Verification requirement |
|---|---|---|---|
| D09-R01 | Asset Intake Steward — record asset identity, source, owner, and location | D01-R13 | Ops: checksum, access class, owner and intake record |
| D09-R02 | Footage Discovery Researcher — find candidate footage and timecodes | D09-R01 | V1+V3: origin/timecode and restrictions flagged |
| D09-R03 | Image Researcher — find candidate stills and illustrations | D09-R01, D07-R12 | V1+V3: source, creator, license inquiry status |
| D09-R04 | Audio and Music Researcher — find candidate audio/music | D09-R01, D05-R14 | V3: provenance and rights status before use |
| D09-R05 | License Terms Analyst — interpret documented asset-use terms | D09-R01, D01-R15 | V3: terms, scope, expiry, restrictions and evidence |
| D09-R06 | Attribution Coordinator — create required credits and placement | D09-R05 | V3: credit text matches source terms |
| D09-R07 | Consent and Release Coordinator — track talent/property releases | D09-R01, D01-R13 | V3: release scope, identity access control, expiry |
| D09-R08 | License Clearance Reviewer — approve documented permitted use | D09-R05, D09-R06, D09-R07 | V3: independent rights evidence; uncertain status blocks |
| D09-R09 | Voice and Performance Rights Steward — record voice/performance permissions | D09-R07, D01-R13 | V3: informed consent and permitted use evidence |
| D09-R10 | Asset Metadata Curator — normalize metadata and rights fields | D09-R01, D09-R05 | Ops+V3: required fields and controlled vocabulary |
| D09-R11 | Asset Checksum and Integrity Checker — verify file identity and change | D09-R01 | V3: checksum, file type and provenance |
| D09-R12 | Drive Reference Steward — manage restricted Google Drive references | D09-R01, D01-R14 | Ops: access control, no public links, owner confirmation |
| D09-R13 | Local Asset Librarian — organize local asset paths and backups | D09-R01, D01-R20 | Ops: path, backup, permission and retention test |
| D09-R14 | Expiry and Renewal Monitor — alert on expiring or revoked rights | D09-R05, D01-R09 | V3: expiry alert before use; revoked use held |
| D09-R15 | Privacy and Sensitive Media Reviewer — limit sensitive material exposure | D09-R01, D01-R13 | V3: necessity, access, consent and minimization check |
| D09-R16 | Derivative Rights Tracker — record permitted edits and derivative scope | D09-R05, D07-R11 | V3: derivative-rights evidence and changes tracked |
| D09-R17 | Geographic Rights Reviewer — check territory and distribution limits | D09-R05, D11-R01 | V3: destination territories match license |
| D09-R18 | Takedown and Rights Incident Coordinator — handle disputed or revoked assets | D09-R14, D01-R10 | Ops+V3: hold, preserve evidence, escalate, replacement decision |
| D09-R19 | Asset Manifest Producer — map all media to rights and artifact use | D09-R08, D09-R11, D09-R17 | V3: every included asset has status and usage record |
| D09-R20 | Independent Asset Rights Reviewer — verify final project asset bundle | D09-R19 | V3+V4: independent clearance; no unknown rights in release |

## D10 — Production engineering and infrastructure (20)

| ID | Functional role and responsibility | Depends on | Verification requirement |
|---|---|---|---|
| D10-R01 | Systems Architect — maintain interfaces and trust boundaries | D01-R11, D01-R14 | Ops: reviewed diagrams, constraints and change record |
| D10-R02 | Python Workflow Engineer — implement isolated workflow modules | D10-R01, D01-R11 | Ops: typed contracts, tests, lint/build evidence |
| D10-R03 | Task Schema Engineer — maintain versioned task and artifact schemas | D10-R01, D01-R07 | Ops: compatibility and invalid-input tests |
| D10-R04 | Queue and Persistence Engineer — implement durable state transitions | D10-R03, D01-R07 | Ops: restart, consistency, migration and replay tests |
| D10-R05 | Scheduler Integration Engineer — safely integrate trigger mechanisms | D10-R04, D01-R06 | Ops: bounded/manual tests; no trigger grants publication |
| D10-R06 | Telegram Bot Engineer — implement authenticated Director command interface | D10-R01, D01-R14 | Ops: identity, authorization, replay, abuse and secret tests |
| D10-R07 | GitHub Actions Engineer — maintain least-privilege workflow automation | D10-R01, D01-R14 | Ops: permission review, pinned actions, secret and dispatch tests |
| D10-R08 | HTML and Template Engineer — maintain safe article/card templates | D03-R13, D10-R02 | V3: escaping/sanitization and injection fixtures |
| D10-R09 | FFmpeg Media Pipeline Engineer — specify reproducible media transforms | D10-R01, D05-R19 | V3: codec, deterministic output and resource-bound tests |
| D10-R10 | Colab Workflow Engineer — document optional notebook execution | D10-R01, D01-R13 | Ops: data-access, runtime, reproducibility and no-secret checks |
| D10-R11 | Google Drive Integration Engineer — manage approved asset references | D09-R12, D10-R03 | Ops+V3: access, ID validation, private-link and expiry tests |
| D10-R12 | Security Engineering Reviewer — assess threat model and controls | D01-R14, D10-R01 | Ops: findings, severity, remediation evidence, independent review |
| D10-R13 | Dependency and Supply-Chain Steward — pin and review dependencies | D01-R11, D10-R07 | Ops: lock/version provenance, vulnerability and update record |
| D10-R14 | Test Engineer — maintain unit, integration, and acceptance fixtures | D10-R02, D10-R03 | Ops: test results, negative cases, no live side effects |
| D10-R15 | Observability Engineer — define structured logs and metrics | D01-R12, D10-R04 | Ops: redaction, trace IDs, retention and alert tests |
| D10-R16 | Reliability Engineer — handle retry, idempotency, and recovery design | D10-R04, D10-R05 | Ops: fault injection, bounded retry, duplicate prevention |
| D10-R17 | Cost and Quota Engineer — measure and enforce resource budgets | D01-R08, D10-R15 | Ops: limit tests and no paid fallback |
| D10-R18 | Data Migration Engineer — manage approved schema evolution | D10-R03, D01-R11 | Ops: backup, migration and rollback fixtures |
| D10-R19 | Accessibility and Format Test Engineer — test artifact conformance | D10-R08, D08-R18 | V3: automated and human-check results by target format |
| D10-R20 | Release Engineering Reviewer — validate reproducible release bundle | D10-R07, D10-R13, D10-R14 | Ops+V3: reviewed diff, tests, artifact hashes, no secrets |

## D11 — Distribution, platform operations, and analytics (20)

| ID | Functional role and responsibility | Depends on | Verification requirement |
|---|---|---|---|
| D11-R01 | Channel Policy Analyst — record platform-specific rules and access | D01-R15 | Ops: current official docs, account scope and review date |
| D11-R02 | Release Manifest Editor — prepare channel-specific release package | D03-R19, D11-R01 | V1+V2+V3: exact destination, format, hash and disclosures |
| D11-R03 | Official API Adapter Designer — specify narrow, official API interface | D11-R01, D10-R01 | Ops: endpoint/permission/rate-limit and failure contract |
| D11-R04 | Telegram Distribution Specialist — prepare human-approved Telegram package | D11-R02, D11-R01 | V3: media/caption/poll validation and destination match |
| D11-R05 | Website Release Specialist — prepare static-site release bundle | D11-R02, D10-R08 | V3: link/build/metadata and visibility checks |
| D11-R06 | Video Platform Package Specialist — prepare video platform metadata package | D11-R02, D05-R19 | V3: codec, title/disclosure, rights and destination checks |
| D11-R07 | Social Platform Package Specialist — prepare approved social variants | D11-R02, D06-R19 | V3: per-platform formatting and character-limit checks |
| D11-R08 | Multi-Channel Consistency Editor — compare variants across destinations | D11-R04, D11-R05, D11-R07 | V1+V2: facts, links, language and disclosure consistent |
| D11-R09 | Director Approval Coordinator — request exact hash/channel approval | D11-R02, D01-R02 | Ops: explicit authorized actor, scope, timestamp, expiry |
| D11-R10 | Publication Authorization Validator — enforce one-use approval | D11-R09, D01-R05 | Ops: hash/destination/time match; reject stale/reused authorization |
| D11-R11 | Delivery Status Verifier — reconcile API response and platform state | D11-R03, D11-R10 | Ops: response plus independent observed status/receipt |
| D11-R12 | Duplicate Delivery Reconciler — detect and prevent repeated posting | D11-R11, D01-R07 | Ops: idempotency and ambiguous-timeout test |
| D11-R13 | Channel Correction Coordinator — route authorized corrections/removal | D03-R15, D11-R11 | Ops+V1: evidence, approval, status and correction linkage |
| D11-R14 | Audience Accessibility Reviewer — check rendered channel accessibility | D08-R18, D11-R02 | V3: platform render and assistive-format checks |
| D11-R15 | Publishing Incident Responder — handle failed or uncertain delivery | D01-R10, D11-R11 | Ops: hold state, reconciliation, no blind retry |
| D11-R16 | Platform Metrics Analyst — report bounded, privacy-aware metrics | D11-R11, D01-R13 | Ops: API/source, time range, sampling and limitations |
| D11-R17 | Analytics Data Steward — govern collected platform data | D01-R13, D11-R16 | Ops: minimum collection, retention, access and deletion evidence |
| D11-R18 | Campaign Attribution Analyst — evaluate campaign outcomes cautiously | D06-R16, D11-R16 | Ops: attribution assumptions and uncertainty documented |
| D11-R19 | Release Evidence Archivist — preserve approvals and delivery receipts | D01-R12, D11-R11 | Ops: immutable references, artifact hash and receipt reconciliation |
| D11-R20 | Independent Distribution Quality Reviewer — inspect final channel package | D11-R08, D11-R10, D09-R20 | V4: independent package/destination review; Director still authorizes |

## Registry governance and scale

Before enabling any role, document its human owner, allowed inputs, output schema, tools, access scope, dependencies, required evidence, reviewer independence, resource ceiling, and failure mode. Enable registry entries by reviewed version, not by editing a live prompt catalog. Require unique-ID, dependency-reference, cycle, capability, test, and policy checks on every registry change.

To expand from 220 to 500, add 280 reviewed functional records only when a real workflow need is demonstrated. Reuse role coverage where appropriate; do not create extra agents to manufacture consensus. A quality reviewer must not author the artifact being reviewed, and source verification must rely on evidence independent of model vote counts.
