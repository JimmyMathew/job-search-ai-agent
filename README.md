# Job Search Agent

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)

An AI-assisted job search that runs on your own machine. It searches LinkedIn for roles that
match your resume, scores each new posting against your profile, and writes the results to an
Excel file on your computer, one row per job, never twice. Mark jobs in the file and it learns
what you do and don't want.

It is driven by [Claude Code](https://claude.com/claude-code) and runs only when you ask. There
is no server, no scheduler, no account to create besides Claude, and your job list never leaves
your computer.

## Quick start (no technical background needed)

You don't have to know git or Python. Claude Code sets everything up for you and asks plain
questions along the way.

1. **Install** [VS Code](https://code.visualstudio.com/) and its **Claude Code** extension, and
   sign in with your Claude subscription. You also need a program that opens Excel files:
   Microsoft Excel, or the free [LibreOffice](https://www.libreoffice.org/).
2. **Get the project.** If you know git: `git clone https://github.com/JimmyMathew/job-search-ai-agent.git`.
   If not, open the repository page on GitHub, choose **Code → Download ZIP**, and unzip it
   somewhere you'll find again.
3. **Add your resume** (PDF, Word or text, any file name) to the `resume` folder inside the project.
4. **Open the project folder in VS Code**, open Claude Code, and type: **set me up**.

Claude installs what's missing, asks which city you want to search in, creates your job list
(an Excel file in your profile folder), and shows you the roles it found in your resume to
approve. That's the whole setup, about ten minutes.

From then on, type `/job-run` in Claude Code whenever you want fresh jobs. Your list opens in
Excel at the end of each run: sort by **Score**, set a **Status** on the jobs you have a view on,
then save and close the file before the next run. Details for everyone else follow below.

## Contents

- [Quick start (no technical background needed)](#quick-start-no-technical-background-needed)
- [Features](#features)
- [How it works](#how-it-works)
- [Requirements](#requirements)
- [Installation](#installation)
- [Getting started](#getting-started)
- [Usage](#usage)
- [Configuration](#configuration)
- [The Excel job list](#the-excel-job-list)
- [Cost and usage controls](#cost-and-usage-controls)
- [Privacy and security](#privacy-and-security)
- [Troubleshooting](#troubleshooting)
- [Project structure](#project-structure)
- [Development](#development)
- [Contributing](#contributing)
- [Disclaimer](#disclaimer)
- [License](#license)

## Features

- **Resume-driven.** Search terms, seniority and a candidate profile are derived from your
  resume. Nothing assumes a country, industry or language.
- **Each job exactly once.** New postings are deduplicated against everything already in your
  job list, including reposts of the same role by the same company.
- **Nothing is hidden.** Every new job lands in the list. The AI score is a sort order with a
  one-line explanation, never a filter.
- **Learns from you.** Set a row's *Status* and *Reason*; repeated "Not for me" patterns become
  rules that rank similar jobs lower or skip them. Rules are plain text and editable in the file.
- **Resumable.** Scores are saved to the file batch by batch and descriptions are cached as they
  arrive. A crash, a closed laptop or a usage limit loses at most one batch, and the next run
  finishes what is left.
- **Safe with your file.** The agent never writes while the file is open in Excel, never
  overwrites a save you made while it was working, saves through a temporary file so a failed
  save leaves your file as it was, and keeps the last 10 backups.
- **Cost-aware.** Job text never passes through the orchestrating session, runs are capped, and
  you see a usage estimate before anything is scored.
- **Private by design.** Your resume and job list stay on your computer. Each person has an
  isolated profile folder.

## How it works

```mermaid
flowchart LR
    R[Resume] -->|/job-setup| P[profile.md<br/>search keywords]
    P --> S[Search LinkedIn<br/>fetch.py]
    S --> D[Deduplicate<br/>against your job list]
    D --> C{Checkpoint<br/>how many to score?}
    C --> W[Write every job to the list<br/>as Pending]
    W --> F[Fetch descriptions<br/>cached]
    F --> B[Block rules<br/>prefilter.py]
    B --> Q[Batches of 10<br/>pipeline.py]
    Q --> A[job-scorer subagent<br/>reads batch, writes scores]
    A --> I[Validate and save<br/>each batch to the list]
    G[(Excel workbook<br/>on your computer)] <--> D
    G <--> I
    G -->|Status and Reason| L[Learning step<br/>new rules]
    L --> B
```

Only two steps involve a language model: the **learning** step (when you have changed a row's
Status or Reason) and **scoring**. Fetching, deduplication, rule matching, batching, validation
and all workbook I/O are deterministic Python in [`scripts/`](scripts), so they cost no tokens and
are covered by tests. The scorer is a Sonnet subagent that reads a batch file and writes a scores
file; the orchestrating session only ever sees one-line status messages.

## Requirements

- **Claude Code** with a Claude subscription, in [VS Code](https://code.visualstudio.com/) or a
  terminal. Scoring uses your own usage.
- **A program that opens `.xlsx` files:** Microsoft Excel or [LibreOffice](https://www.libreoffice.org/)
  (free). On a Mac, Apple Numbers does not work for this; use Excel or LibreOffice.
- **[uv](https://docs.astral.sh/uv/)**, which provisions Python 3.12 and the dependencies.
  `/job-setup` installs it for you if it is missing. Plain Python 3.10+ with `pip` also works.
- A normal home internet connection. LinkedIn may rate-limit shared, VPN or corporate networks.

Developed and tested on Windows. macOS and Linux are expected to work but are less exercised.

## Installation

```bash
git clone https://github.com/JimmyMathew/job-search-ai-agent.git
cd job-search-ai-agent
```

Open the folder in VS Code and start Claude Code. Dependencies are installed by the setup step
below, or by hand:

```bash
uv sync
```

## Getting started

1. Put your resume (PDF, Word or text, any file name) in the [`resume/`](resume) folder.
2. In Claude Code, run `/job-setup` (or just say *set me up*, as in the quick start above).

Setup is a single idempotent command; run it again at any point and it resumes where it left
off. It will:

1. Install `uv` and the dependencies if needed.
2. Create your profile in `profiles/<name>/` from your resume.
3. Ask which locations to search, and optionally which roles.
4. Create your job list, `profiles/<name>/Job Search - <name>.xlsx`. No sign-in is involved.
5. Write `profile.md` (skills, seniority, search keywords) and ask you to approve it.
6. Run a connectivity check, open your job list, and offer a small first run.

Prefer to drive it by hand?

```bash
uv run python scripts/new_profile.py jane-doe --resume path/to/resume.pdf
uv run python scripts/sheet.py --profile jane-doe init        # creates the Excel job list
uv run python scripts/doctor.py --profile jane-doe            # readiness checklist
```

Then edit `profiles/jane-doe/config.yaml` and run `/job-setup jane-doe` to generate the profile.

## Usage

```text
/job-run <profile> [max-jobs-to-score]
```

A run searches for postings since the last run, deduplicates them, shows how many are new with a
usage estimate, and asks how many to score. Passing the number up front skips the question:

```text
/job-run jane-doe 20
```

If an earlier run was interrupted, the next one finishes the unscored rows first. At the end the
job list opens in Excel. **Save and close it before the next run**: while it is open the agent
cannot write to it, and tells you so instead of failing.

| Command | Purpose |
| --- | --- |
| `/job-setup [profile]` | Install, create a profile and job list, generate `profile.md`. Safe to re-run. |
| `/job-run <profile> [cap]` | Search, deduplicate, score and write new jobs; learn from your feedback. |

Run `uv run python scripts/doctor.py --profile <name>` at any time for a checklist with the exact
fix for anything that is wrong. Other helpers (all take `--profile <name>`):

| Command | Purpose |
| --- | --- |
| `uv run python scripts/sheet.py ... where` | Print where the job list is. |
| `uv run python scripts/sheet.py ... open` | Open it in your spreadsheet program. |
| `uv run python scripts/sheet.py ... restore-backup` | Put the newest automatic backup back (for a damaged file). The file it replaces is kept in `backups/` as `...-before-restore-...xlsx`. |
| `uv run python scripts/sheet.py ... clear-lock` | Remove leftover lock files and half-written temporary files after Excel or an update crashed. |
| `uv run python scripts/sheet.py ... init` | Create the job list, or add what an existing one lacks. Never overwrites your data. |

## Configuration

Each profile has a `profiles/<name>/config.yaml`, created from
[`profiles/_example/config.yaml`](profiles/_example/config.yaml). Only `locations` is required.

| Key | Default | Description |
| --- | --- | --- |
| `locations` | *(required)* | One or more places to search, e.g. `"Berlin, Germany"`, `"Austin, TX"`, `"Remote"`. |
| `roles` | `[]` | Job titles to search for. If empty, roles are proposed from your resume at setup. |
| `explore_adjacent_roles` | `true` | Also consider related titles when proposing search keywords. |
| `workbook` | `profiles/<name>/Job Search - <name>.xlsx` | Where the Excel job list lives. An absolute path, a path starting with `~`, or one relative to the profile folder; it must end in `.xlsx`. |
| `max_jobs_to_score_per_run` | `60` | Suggested cap on jobs scored per run. You are asked before each run. |
| `first_run_max_jobs_to_score` | `30` | Smaller suggested cap for the very first run. |
| `first_run_hours_old` | `72` | How far back the first search looks, in hours. |
| `results_per_keyword` | `100` | Upper bound on results per keyword and location. |
| `description_max_words` | `1500` | Job descriptions are trimmed to this many words before scoring. |

Per-profile state (where the job list was created and when) is kept in
`profiles/<name>/agent-state.json`. Keep the job list in a normal folder rather than one that
syncs to the cloud (OneDrive, Dropbox, Google Drive): sync programs can create duplicate
"conflict copies" of a file that is being rewritten, and the doctor warns if it sees that.

Earlier versions kept the list in a Google Sheet and used the settings `sheet`, `sheet_id`, `auth`
and `credentials`. Google is no longer used. Old config files still work: those lines are ignored
and the doctor says so.

**If you used the Google version, do this first.** Your history of jobs you have already seen is in
that sheet. Open it in Google Sheets, choose **File → Download → Microsoft Excel (.xlsx)**, save the
file as the path `sheet.py ... where` prints, and only then run `sheet.py ... init` (or `/job-setup`).
To protect that history, `init` refuses to create an empty list for a profile that used a Google
sheet, and the doctor shows the old sheet's address. (`init --fresh` starts an empty list on
purpose; you would see every job again.) An imported list keeps what the learning step had already
read, and its two bookkeeping tabs are hidden.

## The Excel job list

One workbook per person. Hidden tabs are the agent's own bookkeeping.

| Tab | Owner | Contents |
| --- | --- | --- |
| **Jobs** | You | Every new job: date, score, title, company, location, link, why it matches, gaps, flags, role type. You edit **Status** (dropdown) and **Reason**. |
| **Preferences** | You | Optional questions: minimum salary, work setup, work authorization, industries to prefer or avoid. A blank value means no preference. |
| `AI_Rules` | Agent | Learned rules (Block / Lower / Boost) with their evidence. Editable; rules you write yourself are never changed by the agent. |
| `AI_MetaData` (hidden) | Agent | Run bookkeeping: last run time, counts, LinkedIn status. |
| `AI_StatusSync` (hidden) | Agent | The Status and Reason the learning step last read, so each change is learned from once. |

A **Block** rule needs at least two similar "Not for me" rows behind it, so one bad day cannot
remove a whole category. Lower and Boost rules are ranking hints.

Sorting and filtering the Jobs tab is fine whenever the file is closed to the agent: rows are
matched by Job ID, not by position, and rows you type yourself are never overwritten. Excel's
**Format as Table** works too (the table grows with the data).

Two things the agent needs: **don't rename the Status header or delete the Job ID column**, or the
learning loop and updates stop working. And because the agent re-saves the
file with a library that doesn't know every Excel feature, **pictures, charts, modern "threaded"
comments, and dropdowns that point at another sheet are lost** the next time it saves. Plain
notes, colours, filters and ordinary dropdowns are kept.

**How your file is protected.** The agent checks that the file is not open before it writes
anything, and if it is, stops with a plain message. Each save goes to a temporary file in the same
folder that replaces the original in one step, so a failure leaves your file as it was. A file
that can't be read is reported, never replaced. If you save the file in Excel while the agent is
working on it, the agent gives up that update (and asks you to close the file) instead of
overwriting your save. Two commands never write at the same time. Before each run a copy goes to
`profiles/<name>/backups/`, and the newest 10 are kept.

## Cost and usage controls

- Before scoring, the run reports how many new jobs it found and a rough usage estimate, then
  asks for a cap. Every new job is still added to the job list whether or not it is scored.
- Block rules are applied to titles before descriptions are fetched, so capped slots are not
  spent on jobs you have already rejected.
- Descriptions are fetched only for jobs that will be scored, and each batch of 10 is scored in a
  fresh subagent call that reads its own input file.
- Searching LinkedIn costs no tokens but is rate-limited. If it reports being blocked, wait a few
  hours.

## Privacy and security

- Your resume, profile and job list never leave your computer, apart from the text sent to
  Claude to be scored and the searches sent to LinkedIn. There is no Google account, sign-in
  token or cloud storage involved.
- `profiles/` (except the `_example` template), `resume/`, `runs/` and all `.xlsx` files are
  git-ignored, so personal data is not committed by accident.
- `.claude/settings.json` pre-approves only this project's own scripts and denies Claude read
  access to `secrets/` and `.env`.
- Job titles and descriptions come from the internet. Text in a cell that begins with `=` is
  stored as plain text, never as a formula, so a hostile posting cannot plant a formula in your file.

## Troubleshooting

Start with `uv run python scripts/doctor.py --profile <name>`; each failure comes with its fix.

| Symptom | Resolution |
| --- | --- |
| `WORKBOOK_OPEN: ... is open in another program` | Save and close the job list in Excel, then run the same command (or `/job-run`) again. Nothing was lost. If you are sure it is closed, `uv run python scripts/sheet.py --profile <name> clear-lock` removes a leftover lock file. |
| "can't be opened: it is damaged or password-protected" | Remove the password if there is one. Otherwise run `uv run python scripts/sheet.py --profile <name> restore-backup` to put back the newest automatic copy (made before your last run). The agent never changes the unreadable file by itself; `restore-backup` keeps it in `backups/` as `...-before-restore-...xlsx`. |
| "was changed while I was updating it" | You saved in Excel while the agent was writing. Nothing was overwritten. Close the file and run the same command again. |
| "Another update ... is still running" | Two commands were started at once. Wait a few seconds and run it again. |
| "is marked read-only" | Right-click the file, Properties, untick Read-only (Mac: Get Info, untick Locked). |
| "used a Google sheet before" | Export that sheet to Excel first (see Configuration), then run `init`. Don't use `init --fresh` unless you want an empty list. |
| Doctor: "half-written temporary files" | An update was cut off. `uv run python scripts/sheet.py --profile <name> clear-lock` deletes them; your list is not affected. |
| "Your job list file is missing" | Move it back to the path shown, or set `workbook:` in `config.yaml` to where it is. Or run `restore-backup`. Don't delete `agent-state.json` unless you want a brand-new empty list: the agent would forget every job it has already shown you. |
| Warning about a synced folder or "copies of your job list" | The file is in OneDrive/Dropbox/Google Drive, or a sync program made duplicates. Keep only the right file, and move the list to a normal folder (`workbook:` in `config.yaml`). |
| Mac: the file won't open or looks broken | Use Microsoft Excel or LibreOffice; Apple Numbers is not supported. |
| "Couldn't open ... install a spreadsheet program" | Install Microsoft Excel or the free LibreOffice, then open the file from the folder shown by `sheet.py ... where`. |
| "Blocked" or zero jobs found | LinkedIn rate-limited the connection. Wait a few hours; avoid VPNs and shared networks. |
| Rows stuck on *Pending* | A run was interrupted. Run `/job-run <name>` again; it finishes them first. |
| Missing packages | `uv sync`, or `pip install -r requirements.txt`. |

## Project structure

```text
.claude/
  commands/job-setup.md    # /job-setup: install, profile, job list
  commands/job-run.md      # /job-run: the search-score-learn cycle
  agents/job-scorer.md     # Sonnet subagent: reads a batch file, writes a scores file
  settings.json            # scoped permissions for this project's scripts
scripts/
  config.py                # profiles, config.yaml loading, validation, per-profile state
  workbook.py              # the Excel file: safe reads and writes, locking, backups
  sheet.py                 # the job list's contents (jobs, rules, feedback) and its CLI
  sheet_schema.py          # job list layout and the pure logic around it
  fetch.py                 # LinkedIn search and descriptions (JobSpy), checkpointed
  prefilter.py             # Block rules as whole-word keyword checks
  pipeline.py              # planning, batching, ingesting scores, crash recovery
  doctor.py                # readiness checklist
  new_profile.py           # create a profile from the template
  util.py                  # JSON and time helpers, job IDs, repost detection
profiles/_example/         # tracked template; real profiles are git-ignored
resume/                    # drop your resume here (git-ignored)
tests/                     # pytest suite; no network needed
```

[`CLAUDE.md`](CLAUDE.md) documents the design decisions and conventions in more depth.

## Development

```bash
uv sync                                   # dependencies, plus pytest and ruff
uv run pytest                             # unit tests
uv run ruff check scripts tests           # lint
uv run ruff format --check scripts tests  # formatting
```

The tests cover config validation, the workbook layer (real `.xlsx` files: safe saving, lock
detection, corrupt files, backups, cell safety), job list reads and writes, feedback diffing, the
pipeline's planning, batching and retry logic, score validation and the prefilter.

## Contributing

Issues and pull requests are welcome. Please run the tests and linters above before opening a
pull request, and keep person-, country- and industry-specific assumptions out of code and
prompts. Ideas not yet built: additional job sources beside LinkedIn, and a scoring step usable
from other agent CLIs.

## Disclaimer

This project is not affiliated with LinkedIn. LinkedIn's terms restrict automated access. The
tool makes low-volume, logged-out searches for personal use, but you use it at your own risk, for
example your connection may be temporarily blocked. Do not run it while logged in, from a shared
or work network, or on a server.

## License

[MIT](LICENSE)
