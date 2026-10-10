# LinkedIn Job Search Agent

Finds every new LinkedIn job matching a person's resume, adds it to their Google Sheet once, and
learns from their feedback. Manual trigger only (`/job-run <profile>`) — it never runs on a
timer, so it never competes with other Claude usage. This file is the operational reference
for actually running and extending it; end-user setup is in `README.md`.

## Setup requests

When the user asks to set up, get started, install, or "set me up", run the `/job-setup` command
(`.claude/commands/job-setup.md`). It is one idempotent start-to-finish flow: install uv and the
packages, find the resume (`resume/` drop folder), create the profile, ask for locations, sign in
to Google and create the sheet, check with the doctor, write `profile.md`. Do not improvise your
own setup steps, and never read anything under `secrets/`.

**Auth modes** (`config.choose_auth`, per profile; `auth:` in `config.yaml` overrides):
- `oauth` (default): the person signs in once in a browser (scope `drive.file`, so the app only
  sees files it created). `python scripts/sheet.py --profile <name> connect` creates the sheet in
  their own Drive and is idempotent. The app's OAuth client lives in
  `scripts/google_oauth_client.json` (owner setup: `docs/maintainer-oauth-client.md`).
- `service_account`: for an existing hand-made sheet (`drive.file` cannot open those). Chosen
  automatically when `config.yaml` itself sets `sheet:` and `secrets/service-account.json` exists;
  see `docs/service-account-setup.md`. Existing users such as `dubai-bil` stay on this.

**State:** `profiles/<name>/agent-state.json` (git-ignored) remembers the sheet the app created
(`sheet_id`, `sheet_url`, ...). A `sheet:` in `config.yaml` wins over it. If the file is lost,
`connect` re-finds the sheet by its title `Job Search - <name>`. The Google sign-in token is
`secrets/tokens/<name>.json`; only `google_auth.py login` and `sheet.py connect` ever open a
browser, every other script fails with "Google sign-in needed" instead.

## Who does what

| Component | Tool | Why |
| --- | --- | --- |
| Orchestrator | Claude Code running `/job-run` (set to Sonnet) | Runs commands and the learning step; never sees job text |
| Scorer | `.claude/agents/job-scorer.md`, Sonnet subagent | Reads a batch file, writes a scores file |
| Fetch / dedupe / prefilter / batching / ingest / sheet I/O | `scripts/*.py` | Mechanical, zero tokens |

Nothing person-specific lives in code. A new job seeker is a new folder under `profiles/`, not a
code change.

## Folder structure

```
CLAUDE.md
.claude/
  commands/job-run.md      # /job-run <profile> — the evening cycle
  commands/job-setup.md    # /job-setup [profile] — whole setup, start to finish (also "set me up")
  settings.json            # pre-approved script commands; denies reading secrets/
  agents/job-scorer.md     # Sonnet subagent used by job-run's scoring step
scripts/
  config.py                # profiles + config.yaml: locate, load, default, validate
  util.py                  # JSON/time helpers, job ID parsing, repost logic
  fetch.py                 # JobSpy LinkedIn search, one profile's keywords × locations
  pipeline.py              # plan, batch, ingest scores, recover after a crash (the middle of /job-run)
  sheet_schema.py          # sheet layout (tabs, headers, seed rows) + pure row/diff logic
  sheet.py                 # all Google Sheet reads/writes (gspread) + CLI; `connect` creates the sheet
  google_auth.py           # Google OAuth sign-in (drive.file), token refresh; login/status/logout CLI
  prefilter.py             # applies Block rules as keyword checks, no model involved
  new_profile.py           # creates profiles/<name>/ from the profiles/_example template
  doctor.py                # readiness checklist: packages, resume, config, Google sign-in, sheet
  google_oauth_client.json # the app's OAuth desktop client (owner adds it; see docs/maintainer-oauth-client.md)
profiles/
  _example/                # tracked template (fictional); everything else here is git-ignored
  <name>/
    <any>.pdf/.docx/.txt   # the resume, any file name; git-ignored — personal, stays local only
    config.yaml            # locations, roles, limits — the person's decisions (sheet: only for existing sheets)
    agent-state.json       # written by the app: the sheet it created; git-ignored
    profile.md             # written by /job-setup from the resume — what the scorer actually reads
resume/                    # drop folder for the resume (contents git-ignored; README.txt tracked)
secrets/
  tokens/<profile>.json    # Google sign-in tokens — git-ignored
  service-account.json     # only for the advanced existing-sheet option — git-ignored
docs/                      # service-account-setup.md (advanced), maintainer-oauth-client.md (owner)
tests/                     # pytest suite for the pure logic (no network)
runs/
  <profile>/<timestamp>/   # intermediate JSON per run (raw/deduped/passed/blocked/scored) — git-ignored
```

There is one sheet tab for job listings — **Jobs** — not split by score or by whether a rule
blocked it. Every job that survives dedupe lands there, sorted by score, so nothing is hidden
from the person just because the AI rated it low; score is a sort order and a one-line
explanation, not a gate. See "Sheet design" below.

## Running it

```
/job-run <profile>
```

Runs the full evening cycle end to end, stopping at a checkpoint right after dedupe (before any
description fetching) so the person running it can cap how many jobs get scored. The exact steps
are spelled out in `.claude/commands/job-run.md` — read that file rather than this summary if
you're about to run or debug it, since it has the actual script invocations.

In short: read the sheet → learn from any new feedback (only if there is any) → fetch new
postings for every keyword in `profile.md` (listing only, cheap) → dedupe against everything
already seen → checkpoint (caps how many get *scored*, not how many get written) → **write every
deduped job to the sheet immediately, as "Pending" for the capped subset and its final state for
the rest** → fetch full descriptions for just the capped subset (the expensive, blockable request)
→ prefilter with Block rules → score the survivors in batches of 10 via the Sonnet subagent,
saving each batch's results to the sheet as soon as it is scored (`pipeline.py ingest`, which
updates the Pending rows in place) → summarize. A run that is interrupted for any reason leaves
only Pending rows behind, and the next `/job-run` finishes them first.

Jobs get written to the sheet *before* the slow description/scoring steps, not after — an earlier
version wrote everything in one batch at the very end, which meant a crash or interruption during
a 30+ minute run lost all visibility into work already done, even though nothing was actually
lost on disk. Writing "Pending" rows immediately and updating them in place (`update_jobs()`,
matched by Job ID) means the worst case is now "some rows still say Pending," never "nothing in
the sheet at all." Fixed 2026-09-28, after exactly that scenario came up on a 484-job run.

Fetching is deliberately two-phase — `fetch.py search` then `fetch.py descriptions` — because
LinkedIn only returns full descriptions via a separate per-job request. Fetching them for every
raw search result instead of just the deduped survivors was an early bug here (each keyword
search took 90-140s instead of ~20s); see `scripts/fetch.py`'s module docstring.

If a run is interrupted, nothing is lost and nothing is double-counted: `last_run_iso` is set to
the moment the search *started* (so jobs posted during a long run are not skipped), and only
after the Pending rows are written; a blocked search does not advance it. Re-appending jobs
already in the sheet is a no-op.

Onboarding a new person or refreshing an existing profile's `profile.md`:

```
/job-setup <profile-name>
```

## Setup (maintainer view)

End users just say "set me up" (see "Setup requests"). Underneath it is:

1. `uv sync` (or Python 3.10+ and `pip install -r requirements.txt`).
2. `python scripts/new_profile.py <name> --resume <file>`, then set `locations` in the profile's
   `config.yaml`.
3. `python scripts/sheet.py --profile <name> connect`: Google sign-in, creates or re-finds the
   sheet, then `init-tabs` (below) and a test write. Existing-sheet users instead follow
   `docs/service-account-setup.md` and run `init-tabs` (the doctor also creates missing tabs).
4. `python scripts/doctor.py --profile <name>` must show no FIX; then `/job-setup` writes
   `profile.md` from the resume.

`init-tabs` creates Jobs / AI_Rules / AI_MetaData
with headers if they don't exist yet, seeds the Preferences tab with example rows if it doesn't
exist yet (never touches it if it does — a job seeker's filled-in answers must never be
overwritten), and applies the Status dropdown (New / Interested / Applied / Not for me /
Rejected by company / Interview / Offer) to the Jobs tab via `apply_status_validation()`. The
"AI_" prefix on those two tabs is deliberate — Jobs and Preferences are the job seeker's tabs,
AI_Rules and AI_MetaData are the agent's own bookkeeping, and the prefix keeps that obvious at
a glance. Re-run `python scripts/sheet.py --profile <name> apply-status-validation` on its own
if the dropdown is ever missing or ends up on the wrong column (it clears stale validation
across the whole Jobs range first, so it's always safe to re-run after any column reorder).

## Sheet design

Four tabs, one sheet per job seeker. The job seeker edits **Status** and **Reason** in Jobs, and
**Value** in Preferences — nothing else is theirs to touch.

- **Jobs** — every job that survives dedupe, whatever its score: scored high, scored low, or
  blocked by a rule (score left blank, `Why it matches` holds the block/skip reason instead).
  There is deliberately no second "Filtered" tab and no score threshold gating what gets written —
  an earlier version of this design split jobs across two tabs by score, and hid low scorers in a
  tab the job seeker might never check. That's a real risk for someone job-hunting: the AI's score
  is a judgment call, not a guarantee, and a job scored 45 might still be worth applying to. Score
  is now a sort order and a one-line explanation, never a gate. Columns, in sheet order: **Date
  added, Score, Job ID, Job title, Company, Location, Posted, Link, Why it matches, Gaps, Flags,
  Role type, Status, Reason.** Status is a dropdown (`apply_status_validation()`); everything else
  is agent-written.
- **AI_Rules** — plain-language learning rules (Rule, Type: Block/Lower/Boost, Keywords, Based on,
  Added by, Active). The job seeker can edit or deactivate any rule by hand. The "AI_" prefix
  marks this and AI_MetaData as the agent's tabs, distinct from Jobs and
  Preferences which are the job seeker's.
- **Preferences** — things the job seeker states directly about what they want, as simple
  Criterion / Value / Example rows (e.g. minimum salary, career direction, industries to avoid).
  Seeded once with example rows and a blank Value column (`ensure_preferences_seeded()`, never
  re-run over existing data); a blank Value means no preference stated, and the scorer falls back
  to profile.md / general judgment for that row. This exists instead of profile.md prose for
  preference data specifically because it's the one thing meant to be edited directly by the job
  seeker in Sheets, with zero technical vocabulary (no "type: hard requirement" style jargon —
  just a question, a blank cell, and an example of what to write). See `PREFERENCES_SEED_ROWS` in
  `scripts/sheet.py` for the exact starter questions.
- **AI_MetaData** — key/value: `last_run_iso`, `jobs_seen_last_run`, `jobs_added_last_run`,
  `reposts_skipped_last_run`, `linkedin_status` (ok/blocked/partial), and
  `status_sync_snapshot` (a `{job_id: "status||reason"}` JSON blob — see below). Not for the job
  seeker to read; it's run bookkeeping. Read/write via `scripts/sheet.py meta` / `update-meta`.

**Why there's no second "Status changed" column next to Status in Jobs:** the learning step needs
to know which rows' Status/Reason changed since it last looked, but that tracking doesn't have to
live in the same tab the person edits — a second status-shaped column there reads as confusing
(which one do I edit?). Instead `status_sync_snapshot` in AI_MetaData holds the last-seen
Status+Reason per job ID; `get_changed_status_rows()` diffs live Jobs rows against that snapshot, and
`mark_status_synced()` updates it after the learning step processes them. Jobs keeps exactly one
status-shaped column.

Exact column layouts are defined in `scripts/sheet.py` (`JOBS_HEADERS`, `RULES_HEADERS`,
`PREFERENCES_HEADERS`) — that
file is the source of truth if this doc and the code ever disagree.

## Learning loop — guardrails that matter

- A **Block** rule needs at least 2 similar "Not for me" rejections behind it — one bad day
  shouldn't remove a whole category. Block rules are the only ones `prefilter.py` acts on
  mechanically (keyword match on title + description); Lower/Boost are ranking hints the scorer
  subagent applies with judgment.
- The agent writes rules with `Added by: AI`. Rules with any other `Added by` value were written by a human and are never changed or removed by the agent.
- Every run's summary lists rules added or changed, so a bad lesson is easy to spot and undo.

## Token budget rules — don't relearn these, just follow them

- Manual trigger only — never add a scheduled/cron run without being asked.
- Fetch, dedupe, prefilter, and sheet I/O are Python; never do them by reasoning over data in the
  main context.
- Skip the learning step entirely when `changed-status` comes back empty.
- Always stop at the checkpoint (after dedupe, before any description is fetched) and cap how many
  jobs get scored. The first-ever run defaults to a smaller cap (`first_run_max_jobs_to_score`).
- Job text never passes through the orchestrator: `pipeline.py make-batches` writes batch files,
  the `job-scorer` subagent reads one and writes a scores file, `pipeline.py ingest` validates it
  and saves it to the sheet. Prompts to the scorer are file paths only.
- Descriptions are trimmed to `description_max_words` (default 1500; benefits sections can hold
  the salary, so don't cut deeper without measuring). Batch 10 jobs per scorer call.
- Save as you go: each batch is written to the sheet as soon as it is scored, descriptions are
  cached as they are fetched, and the next run finishes any "Pending" rows before searching.
- `/job-run` is set to Sonnet, but a command's model setting lasts only for the current turn, so
  after the checkpoint reply the session may run on the user's own model. That is why the
  orchestrator must stay terse and keep job text out of its context.

## Current status

Generalized for sharing (branch `feature/shareable-app`): any person is a new folder under
`profiles/` created by `scripts/new_profile.py` from the `profiles/_example/` template; only
`locations` and `sheet` are required in `config.yaml` (`roles` optional, AI-suggested at
`/job-setup` when empty). `scripts/doctor.py` is the readiness checklist for onboarding and
debugging. User-facing docs: `README.md` (and `docs/service-account-setup.md` for the advanced
existing-sheet option). Each user signs in with their own Google account and the app creates the
sheet in their own Drive (OAuth, `drive.file`), so sheets are isolated; service-account keys
(`credentials:` in config) remain for existing sheets. Nothing in code or prompts may assume a country,
industry or person. Feedback tracking lives in the agent-owned `AI_StatusSync` tab (Job ID, Status, Reason: what the
learning step last read); older sheets' single-cell `status_sync_snapshot` is migrated into it
once. Jobs rows are written by header name, so added columns never misalign. Development:
`uv sync`, then `uv run pytest` and `uv run ruff check scripts tests`. Known gap: only LinkedIn
is searched; other sources would plug in beside `fetch.py`.
