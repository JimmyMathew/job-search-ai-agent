# Job Search Agent

Finds new LinkedIn jobs that match **your resume**, scores each one with AI, and adds them to
**your own Google Sheet** exactly once. Mark jobs "Interested", "Applied" or "Not for me" in the
sheet and the agent learns what you do and don't want. It runs only when you ask it to.

It works anywhere LinkedIn has jobs; nothing is tied to a country or industry.

## What you need

- A computer (Windows, Mac or Linux) on your normal home internet connection
- [VS Code](https://code.visualstudio.com/) with the [Claude Code](https://claude.com/claude-code)
  extension, and a Claude subscription (it uses your own usage)
- A Google account
- About 10 minutes for the first setup

## Quickstart

1. **Download** this project (green **Code** button on GitHub, then **Download ZIP**, and unzip
   it somewhere you will find it again).
2. **Put your resume** (PDF, Word or text, any file name) in the `resume` folder inside it.
3. **Open the folder in VS Code**, open Claude Code, and say: **set me up**.

Claude does the rest and asks you a couple of plain questions along the way: which city you want
jobs in, and whether the roles it suggests from your resume look right. It installs what it needs
(the free tool [uv](https://docs.astral.sh/uv/)), creates your Google Sheet, and writes a profile
from your resume.

### What you will see at Google sign-in

A browser tab opens once. Pick your Google account and click **Continue** (or **Allow**). The app
asks only for permission to see and edit **files it creates itself**, which is the one sheet it
makes for you. It cannot see anything else in your Google Drive. When the tab says "Signed in",
close it and go back to VS Code. Your sheet is created in your own Drive and its address is shown
to you.

### Then, every time you want fresh jobs

Say `/job-run your-name` in Claude Code. It finds new jobs, shows you how many it found, and asks
how many to score (to protect your usage). Then it fills your sheet. Only new jobs are ever added.

*Advanced:* already have a Google Sheet you want to use, or would rather not sign in through the
browser? Set `sheet:` in `profiles/your-name/config.yaml` and follow
[docs/service-account-setup.md](docs/service-account-setup.md).

## Using your sheet

| Tab | What it is |
| --- | --- |
| **Jobs** | Every new job, sorted by match score, with why it matches and any gaps. **You edit Status and Reason**. |
| **Preferences** | Optional questions (minimum salary, work setup, industries to avoid). Fill in whatever you like; blank means no preference. |
| AI_Rules, AI_MetaData | The agent's own notes. You can ignore these. |

A low score is only a sort order, and nothing is hidden from you. Use **Status** (Interested,
Applied, Not for me, ...) and write a short **Reason** when something is not for you. After 2 or
more similar "Not for me" rows, the agent adds a rule so similar jobs rank lower or are skipped.
You can edit or turn off any rule in the AI_Rules tab.

## Costs and limits

- The AI scoring step uses your Claude subscription. Before scoring, the run tells you how many
  jobs it found and a rough usage estimate, and asks how many to score. Your first run suggests
  30, later runs 60 (`first_run_max_jobs_to_score` and `max_jobs_to_score_per_run` in
  `config.yaml`). You can also pass a number: `/job-run your-name 20`. Every new job still lands in
  your sheet, scored or not.
- Nothing is lost if a run stops (a crash, closing the laptop, hitting your Claude usage limit).
  Each batch of scores is saved to your sheet as soon as it is done, and the next
  `/job-run` finishes any unscored rows first.
- Searching LinkedIn is free but rate-limited. If it reports being blocked, wait a few hours.

## Several people, one computer

Each person gets their own folder under `profiles/` and their own sheet, resume and Google
sign-in; nothing is shared. Run `/job-run <name>` for whichever profile you want.

## Troubleshooting

Run `uv run python scripts/doctor.py --profile your-name` first; it explains most problems.

| Problem | What to do |
| --- | --- |
| "Google sign-in needed" | Say "set me up" again, or run `uv run python scripts/sheet.py --profile your-name connect`. It is safe to repeat and never makes a second sheet |
| Sheet errors (404, 403) with your own sheet | [docs/service-account-setup.md](docs/service-account-setup.md), troubleshooting table |
| "Blocked" / zero jobs found | LinkedIn rate-limited you. Wait a few hours; avoid VPNs and work networks |
| Rows stuck on "Pending" | A run was interrupted. Just run `/job-run your-name` again; it finishes the Pending rows first |
| Missing packages | `uv sync` (or `pip install -r requirements.txt`) |

## Your data and privacy

Your resume and profile stay on your computer, and your sheet lives in your own Google account.
The Google sign-in is saved on your computer only, in `secrets/tokens/`. You can remove the app's
access any time at <https://myaccount.google.com/permissions>. To score jobs,
the text of your profile and of each job posting is processed by Claude (through Claude Code).
Everything under `profiles/` (except the `_example` template) and `secrets/` is git-ignored, so
it will not be uploaded if you use git.

## Disclaimer

This project is not affiliated with LinkedIn. LinkedIn's terms restrict automated access. This
tool makes low-volume, logged-out searches for personal use, but you use it at your own risk (for
example, your connection may be temporarily blocked). Never run it logged in, from a shared or
work network, or on a server. Provided as-is under the [MIT License](LICENSE).

## For developers

```
uv sync                                  # installs dependencies plus pytest and ruff
uv run pytest                            # unit tests (no network, no Google account needed)
uv run ruff check scripts tests          # lint
uv run ruff format --check scripts tests
```

Code map: `scripts/config.py` (profiles and config), `scripts/sheet_schema.py` (sheet layout and
pure logic), `scripts/sheet.py` (Google Sheets I/O), `scripts/fetch.py` (LinkedIn search),
`scripts/google_auth.py` (Google sign-in), `scripts/prefilter.py` (rule-based filtering),
`scripts/doctor.py` (setup checks). Design notes and conventions are in `CLAUDE.md`. Maintainers:
the one-time Google Cloud setup behind the sign-in is in
[docs/maintainer-oauth-client.md](docs/maintainer-oauth-client.md).
