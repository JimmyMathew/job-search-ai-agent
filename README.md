# Job Search Agent

Finds new LinkedIn jobs that match **your resume**, scores each one with AI, and adds them to
**your own Google Sheet** exactly once. Mark jobs "Interested", "Applied" or "Not for me" in the
sheet and the agent learns what you do and don't want. It runs only when you ask it to.

It works anywhere LinkedIn has jobs; nothing is tied to a country or industry.

## What you need

- A computer (Windows, Mac or Linux) on your normal home internet connection
- [Claude Code](https://claude.com/claude-code) with a Claude subscription (it uses your own usage)
- A Google account
- About 15 minutes for the first setup

## Quickstart

1. **Install** [uv](https://docs.astral.sh/uv/) (installs Python and the packages for you), then in
   this folder run: `uv sync`
   (Prefer plain Python 3.10+? Run `pip install -r requirements.txt` instead.)
2. **Create your folder:** `uv run scripts/new_profile.py your-name`
3. **Drop your resume** (PDF, DOCX or TXT, any file name) into `profiles/your-name/`.
4. **Edit `profiles/your-name/config.yaml`.** You only need to set:
   - `locations`: where to search, e.g. `"Berlin, Germany"`, `"Austin, TX"`, `"Remote"`
   - `roles` *(optional)*: job titles you want. Leave it empty and the AI will suggest roles from
     your resume for you to approve.
5. **Set up Google** by following [docs/google-sheet-setup.md](docs/google-sheet-setup.md), then
   paste your sheet's address into `config.yaml` as `sheet:`.
6. **Check it all:** `uv run scripts/doctor.py --profile your-name`. It tells you exactly what to
   fix.
7. **Open Claude Code in this folder** and run `/job-setup your-name`. The AI reads your resume,
   proposes your roles and search keywords, and asks you to approve them.
8. **Run it:** `/job-run your-name`. It finds new jobs, shows you how many it found, and asks how
   many to score (to protect your usage). Then it fills your sheet.

Run `/job-run your-name` whenever you want fresh jobs. Only new jobs are ever added.

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

Each person gets their own folder under `profiles/` and their own sheet, resume and Google key;
nothing is shared. Run `/job-run <name>` for whichever profile you want.

## Troubleshooting

Run `uv run scripts/doctor.py --profile your-name` first; it explains most problems.

| Problem | What to do |
| --- | --- |
| Sheet errors (404, 403) | [docs/google-sheet-setup.md](docs/google-sheet-setup.md), troubleshooting table |
| "Blocked" / zero jobs found | LinkedIn rate-limited you. Wait a few hours; avoid VPNs and work networks |
| Rows stuck on "Pending" | A run was interrupted. Just run `/job-run your-name` again; it finishes the Pending rows first |
| Missing packages | `uv sync` (or `pip install -r requirements.txt`) |

## Your data and privacy

Your resume, profile and sheet stay on your computer and in your Google account. To score jobs,
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
```

Code map: `scripts/config.py` (profiles and config), `scripts/sheet_schema.py` (sheet layout and
pure logic), `scripts/sheet.py` (Google Sheets I/O), `scripts/fetch.py` (LinkedIn search),
`scripts/prefilter.py` (rule-based filtering), `scripts/doctor.py` (setup checks). Design notes
and conventions are in `CLAUDE.md`.
