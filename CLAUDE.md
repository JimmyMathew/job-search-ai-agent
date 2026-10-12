# LinkedIn Job Search Agent

Finds every new LinkedIn job matching a person's resume, adds it to their Excel job list once, and
learns from their feedback. Manual trigger only (`/job-run <profile>`) — it never runs on a
timer, so it never competes with other Claude usage. This file is the operational reference
for actually running and extending it; end-user setup is in `README.md`.

## Setup requests

When the user asks to set up, get started, install, or "set me up", run the `/job-setup` command
(`.claude/commands/job-setup.md`). It is one idempotent start-to-finish flow: install uv and the
packages, find the resume (`resume/` drop folder), create the profile, ask for locations, create
the Excel job list, check with the doctor, write `profile.md`. Do not improvise your own setup
steps, and never read anything under `secrets/`.

The people using this are not technical. Their only actions are: put a resume in `resume/`, say
"set me up", name a city, approve the roles. Anything that adds a step (an account, a sign-in, a
key, a console) is a defect.

**Where the results live:** a local Excel workbook, `profiles/<name>/Job Search - <name>.xlsx` by
default (`workbook:` in `config.yaml` overrides, see `config.workbook_path`). `python
scripts/sheet.py --profile <name> init` creates it and is idempotent: if the file exists it only
adds what is missing (tabs, headers, the Status dropdown), never touching what the person typed.
There is no Google Sheets, OAuth or service-account code any more; it was removed because those
routes were a security and support burden for non-technical users. `config.yaml` keys from that
era (`sheet`, `sheet_id`, `auth`, `credentials`) are ignored without error and reported by the
doctor (`config['deprecated_keys']`).

**State:** `profiles/<name>/agent-state.json` (git-ignored) records the workbook path and creation
time written by `init`. If the state records a workbook but the file is gone, every command
raises `WorkbookMissing` with a plain explanation (where it should be, where the backups are) and
`init` refuses to create a new empty one: that would silently lose the seen-jobs history that
dedupe depends on. Deleting `agent-state.json` is the explicit "start a brand-new list" action.

**Profiles from the Google era** (legacy `sheet`/`sheet_id`/`auth`/`credentials` in `config.yaml`, or
`sheet_id` in the state file) and no workbook yet: `init` raises `OldGoogleListError` unless
`--fresh` is passed, and every command and the doctor print the old sheet's address with the steps
(open it, File > Download > Microsoft Excel, save at the workbook path, then `init`). Never run
`init --fresh` without the person's explicit yes. `init` also carries the old `status_sync_snapshot`
key of AI_MetaData into AI_StatusSync and hides the bookkeeping tabs of an imported file.

**The file is shared with the person's Excel.** All writes go through `scripts/workbook.py`, which
refuses to write while the file is open (lock file next to it, or on Windows a failed write-open
probe) and raises `WorkbookLocked`, whose message starts `WORKBOOK_OPEN:`. `/job-run` shows that
message verbatim, waits for the person, and re-runs the same command (all writes are idempotent).
`sheet.py ready` is the pre-flight (file loads, closed, one backup). Backups go to
`profiles/<name>/backups/` (newest 10); `restore-backup` puts the newest back and first keeps the
replaced file there as `<stem>-before-restore-<time>.xlsx` (never pruned or restored automatically).
A read-only file raises `WorkbookReadOnly` (a separate message, not `WORKBOOK_OPEN:`).

## Who does what

| Component | Tool | Why |
| --- | --- | --- |
| Orchestrator | Claude Code running `/job-run` (set to Sonnet) | Runs commands and the learning step; never sees job text |
| Scorer | `.claude/agents/job-scorer.md`, Sonnet subagent | Reads a batch file, writes a scores file |
| Fetch / dedupe / prefilter / batching / ingest / workbook I/O | `scripts/*.py` | Mechanical, zero tokens |

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
  config.py                # profiles + config.yaml: locate, load, default, validate; workbook_path
  util.py                  # JSON/time helpers, job ID parsing, repost logic, run_cli
  workbook.py              # storage layer: one .xlsx, safe read/write, lock detection, backups
  sheet_schema.py          # job list layout (tabs, headers, seed rows) + pure row/diff logic
  sheet.py                 # SheetClient: jobs, rules, feedback on top of workbook.py + CLI (init, ready, ...)
  fetch.py                 # JobSpy LinkedIn search, one profile's keywords × locations
  pipeline.py              # plan, batch, ingest scores, recover after a crash (the middle of /job-run)
  prefilter.py             # applies Block rules as keyword checks, no model involved
  new_profile.py           # creates profiles/<name>/ from the profiles/_example template
  doctor.py                # readiness checklist: packages, resume, config, job list
profiles/
  _example/                # tracked template (fictional); everything else here is git-ignored
  <name>/
    <any>.pdf/.docx/.txt   # the resume, any file name; git-ignored — personal, stays local only
    config.yaml            # locations, roles, limits — the person's decisions (optional workbook:)
    Job Search - <name>.xlsx  # the person's job list, created by `sheet.py init`; git-ignored
    backups/               # automatic copies of the job list (newest 10); git-ignored
    agent-state.json       # written by the app: the workbook path; git-ignored
    profile.md             # written by /job-setup from the resume — what the scorer actually reads
resume/                    # drop folder for the resume (contents git-ignored; README.txt tracked)
secrets/                   # git-ignored; nothing in the app needs it any more
tests/                     # pytest suite (real .xlsx files in tmp dirs; no network)
runs/
  <profile>/<timestamp>/   # intermediate JSON per run (raw/deduped/passed/blocked/scored) — git-ignored
```

There is one tab for job listings — **Jobs** — not split by score or by whether a rule
blocked it. Every job that survives dedupe lands there, so nothing is hidden from the person just
because the AI rated it low; score is a sort order and a one-line explanation, not a gate. See
"Workbook design" below.

## Running it

```
/job-run <profile>
```

Runs the full evening cycle end to end, stopping at a checkpoint right after dedupe (before any
description fetching) so the person running it can cap how many jobs get scored. The exact steps
are spelled out in `.claude/commands/job-run.md` — read that file rather than this summary if
you're about to run or debug it, since it has the actual script invocations.

In short: doctor → `sheet.py ready` (loads, closed, backup) → read the job list → learn from any new feedback (only if there is any) → fetch new
postings for every keyword in `profile.md` (listing only, cheap) → dedupe against everything
already seen → checkpoint (caps how many get *scored*, not how many get written) → **write every
deduped job to the workbook immediately, as "Pending" for the capped subset and its final state for
the rest** → fetch full descriptions for just the capped subset (the expensive, blockable request)
→ prefilter with Block rules → score the survivors in batches of 10 via the Sonnet subagent,
saving each batch's results as soon as it is scored (`pipeline.py ingest`, which
updates the Pending rows in place) → summarize → open the workbook for the person. A run that is
interrupted for any reason leaves only Pending rows behind, and the next `/job-run` finishes them
first.

Jobs get written to the workbook *before* the slow description/scoring steps, not after — an
earlier version wrote everything in one batch at the very end, which meant a crash or interruption
during a 30+ minute run lost all visibility into work already done, even though nothing was
actually lost on disk. Writing "Pending" rows immediately and updating them in place
(`update_jobs()`, matched by Job ID at write time, so a list the person has sorted is safe) means
the worst case is "some rows still say Pending," never "nothing in the file at all." Fixed
2026-09-28, after exactly that scenario came up on a 484-job run.

Fetching is deliberately two-phase — `fetch.py search` then `fetch.py descriptions` — because
LinkedIn only returns full descriptions via a separate per-job request. Fetching them for every
raw search result instead of just the deduped survivors was an early bug here (each keyword
search took 90-140s instead of ~20s); see `scripts/fetch.py`'s module docstring.

If a run is interrupted, nothing is lost and nothing is double-counted: `last_run_iso` is set to
the moment the search *started* (so jobs posted during a long run are not skipped), and only
after the Pending rows are written; a blocked search does not advance it. Re-appending jobs
already in the workbook is a no-op. If the workbook is open in Excel when a write is due, the
command stops with `WORKBOOK_OPEN:` before changing anything (`pipeline.py ingest` then leaves the
batch `todo`); the person closes the file and the same command is run again.

Onboarding a new person or refreshing an existing profile's `profile.md`:

```
/job-setup <profile-name>
```

## Setup (maintainer view)

End users just say "set me up" (see "Setup requests"). Underneath it is:

1. `uv sync` (or Python 3.10+ and `pip install -r requirements.txt`).
2. `python scripts/new_profile.py <name> --resume <file>`, then set `locations` in the profile's
   `config.yaml`.
3. `python scripts/sheet.py --profile <name> init`: creates the workbook (or adds what is missing
   to an existing one) and records it in `agent-state.json`. `init-tabs` is an alias.
4. `python scripts/doctor.py --profile <name>` must show no FIX; then `/job-setup` writes
   `profile.md` from the resume.

`init` creates Jobs / Preferences / AI_Rules / AI_MetaData / AI_StatusSync with headers, seeds the
Preferences tab with example rows only when it creates that tab (a job seeker's filled-in answers
are never overwritten), applies the Status dropdown (New / Interested / Applied / Not for me /
Rejected by company / Interview / Offer) to the Jobs tab, freezes the header row, and hides the two
bookkeeping tabs. A header a newer release adds is appended at the end of an existing tab's header
row. Re-run `python scripts/sheet.py --profile <name> apply-status-validation` on its own if the
dropdown is ever missing or ends up on the wrong column (it replaces any validation on the tab).

## Workbook design

One workbook per job seeker. The job seeker edits **Status** and **Reason** in Jobs, and
**Value** in Preferences — nothing else is theirs to touch. `workbook.py` knows files, tabs,
headers and cells and nothing about jobs; `sheet_schema.py` holds the layout and pure logic;
`sheet.py` ties them together.

- **Jobs** — every job that survives dedupe, whatever its score: scored high, scored low, or
  blocked by a rule (score left blank, `Why it matches` holds the block/skip reason instead).
  There is deliberately no second "Filtered" tab and no score threshold gating what gets written —
  an earlier version of this design split jobs across two tabs by score, and hid low scorers in a
  tab the job seeker might never check. That's a real risk for someone job-hunting: the AI's score
  is a judgment call, not a guarantee, and a job scored 45 might still be worth applying to. Score
  is now a sort order and a one-line explanation, never a gate. Columns, in sheet order: **Date
  added, Score, Job ID, Job title, Company, Location, Posted, Link, Why it matches, Gaps, Flags,
  Role type, Status, Reason.** Status is a dropdown; everything else is agent-written. Score is a
  number (so Excel sorts it), Job ID is text (a long number would show as 3.9E+09), dates are
  `YYYY-MM-DD` text, Link is a clickable hyperlink (only for a valid http(s) address under 2000
  characters; the cell text stays the URL).
- **AI_Rules** (visible) — plain-language learning rules (Rule, Type: Block/Lower/Boost, Keywords,
  Based on, Added by, Active). The job seeker can edit or deactivate any rule by hand, so it stays
  visible. The "AI_" prefix marks it and the two hidden tabs as the agent's, distinct from Jobs and
  Preferences which are the job seeker's. Rules are de-duplicated by their text when added.
- **Preferences** — things the job seeker states directly about what they want, as simple
  Criterion / Value / Example rows (e.g. minimum salary, career direction, industries to avoid).
  Seeded once with example rows and a blank Value column (only when the tab is created); a blank
  Value means no preference stated, and the scorer falls back to profile.md / general judgment for
  that row. See `PREFERENCES_SEED_ROWS` in `scripts/sheet_schema.py` for the exact starter questions.
- **AI_MetaData** (hidden) — key/value: `last_run_iso`, `jobs_seen_last_run`,
  `jobs_added_last_run`, `reposts_skipped_last_run`, `linkedin_status` (ok/partial/blocked, as
  `/job-run` writes it). Not for
  the job seeker; it's run bookkeeping. Read/write via `scripts/sheet.py meta` / `update-meta`.
- **AI_StatusSync** (hidden) — Job ID, Status, Reason: what the learning step last read.

**Why there's no second "Status changed" column next to Status in Jobs:** the learning step needs
to know which rows' Status/Reason changed since it last looked, but that tracking doesn't have to
live in the tab the person edits — a second status-shaped column there reads as confusing (which
one do I edit?). `AI_StatusSync` holds the last-seen Status+Reason per job ID;
`get_changed_status_rows()` diffs live Jobs rows against it, and `mark_status_synced()` updates it
after the learning step processes them. Jobs keeps exactly one status-shaped column.

**Safety rules in `workbook.py` (don't weaken them):**
- Never write while the file is open elsewhere; check before doing any work (`WorkbookLocked`,
  message starts `WORKBOOK_OPEN:`). Reading is always allowed.
- Save to `.<stem>.saving.xlsx` in the same folder (never a `~$` name), then `os.replace` with
  retries (antivirus/sync tools); if that keeps failing, leave the original untouched and raise
  `WorkbookLocked`.
- A file that cannot be loaded raises `WorkbookCorrupt`; a fresh workbook is never created over it.
- Rows are placed and matched by header name / key at write time, never by remembered position.
- Cell values: text starting `=` stays text (`cell.data_type = "s"`), illegal characters are
  stripped, text is cut at 32,000 characters.
- Several operations in one command share one load and one save (`session()`), and the whole
  session holds an inter-process lock file (`.<stem>.agent-lock`, PID + time, stale after 10 min or
  a dead PID): two commands started together take turns, and one that can't get it in 30 s raises
  `WorkbookBusy`. Temp files have unique names (`.<stem>.<random>.saving.xlsx`).
- The file's size and mtime are recorded at load and re-checked just before the replace; if the
  person saved meanwhile the update is abandoned with a `WORKBOOK_OPEN:` message (the commands are
  idempotent, so they are simply run again).
- New rows go below the last row with *anything* in it (a hand-typed row has no Job ID); upserts
  write only the headers they carry. Excel tables (Format as Table) are extended and get no
  sheet-level autofilter. The Status dropdown replaces only a validation covering that column.
- openpyxl drops pictures, charts, threaded comments and cross-sheet dropdowns on save; the
  README tells people so. Renaming the Status header or deleting Job ID breaks the learning loop.

Exact column layouts are defined in `scripts/sheet_schema.py` (`JOBS_HEADERS`, `RULES_HEADERS`,
`PREFERENCES_HEADERS`) — that file is the source of truth if this doc and the code ever disagree.

## Learning loop — guardrails that matter

- A **Block** rule needs at least 2 similar "Not for me" rejections behind it — one bad day
  shouldn't remove a whole category. Block rules are the only ones `prefilter.py` acts on
  mechanically (keyword match on title + description); Lower/Boost are ranking hints the scorer
  subagent applies with judgment.
- The agent writes rules with `Added by: AI`. Rules with any other `Added by` value were written by a human and are never changed or removed by the agent.
- Every run's summary lists rules added or changed, so a bad lesson is easy to spot and undo.

## Token budget rules — don't relearn these, just follow them

- Manual trigger only — never add a scheduled/cron run without being asked.
- Fetch, dedupe, prefilter, and workbook I/O are Python; never do them by reasoning over data in
  the main context.
- Skip the learning step entirely when `changed-status` comes back empty.
- Always stop at the checkpoint (after dedupe, before any description is fetched) and cap how many
  jobs get scored. The first-ever run defaults to a smaller cap (`first_run_max_jobs_to_score`).
- Job text never passes through the orchestrator: `pipeline.py make-batches` writes batch files,
  the `job-scorer` subagent reads one and writes a scores file, `pipeline.py ingest` validates it
  and saves it to the workbook. Prompts to the scorer are file paths only.
- Descriptions are trimmed to `description_max_words` (default 1500; benefits sections can hold
  the salary, so don't cut deeper without measuring). Batch 10 jobs per scorer call.
- Save as you go: each batch is written to the workbook as soon as it is scored, descriptions are
  cached as they are fetched, and the next run finishes any "Pending" rows before searching.
- `/job-run` is set to Sonnet, but a command's model setting lasts only for the current turn, so
  after the checkpoint reply the session may run on the user's own model. That is why the
  orchestrator must stay terse and keep job text out of its context.

## Current status

Generalized for sharing (branch `feature/shareable-app`): any person is a new folder under
`profiles/` created by `scripts/new_profile.py` from the `profiles/_example/` template; only
`locations` is required in `config.yaml` (`roles` optional, AI-suggested at `/job-setup` when
empty). `scripts/doctor.py` is the readiness checklist for onboarding and debugging; it checks the
workbook exists, loads, has every tab, and warns when it is open, in a OneDrive/Dropbox/Google Drive
folder, or has conflict-copy siblings. User-facing docs: `README.md`. Each person's results are an
Excel workbook in their profile folder (no cloud, no sign-in); the Google Sheets/OAuth/service
account routes were removed. Feedback tracking lives in the hidden agent-owned `AI_StatusSync` tab.
Rows are written by header name, so added columns never misalign. Development: `uv sync`, then
`uv run pytest` and `uv run ruff check scripts tests`. Known gaps: only LinkedIn is searched; other
sources would plug in beside `fetch.py`. Apple Numbers cannot open the job list (Excel or
LibreOffice are needed on a Mac). The Windows "file is open" check relies on lock files and a
write-open probe; on other systems only lock files are checked.
