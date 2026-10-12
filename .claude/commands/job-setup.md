---
description: Set up everything from scratch (install, resume, Excel job list), or regenerate a profile.md
argument-hint: [profile-name]
---

Set up the job search agent for the person you are talking to. `$ARGUMENTS` is an optional
profile name. This is the only command that reads a resume; everything else reads `profile.md`
instead, so get that right.

**How to behave.** The person is probably not technical. Keep every question short and plain, one
at a time, and say what you are about to do before a step that opens a window or installs
something. Never show raw errors without explaining them.

**This command is idempotent.** It runs start to finish, but before each step check whether that
step is already done and skip it if so. Running it again on a half-finished or finished setup must
just pick up where things stand, never redo or duplicate work. Run one command per tool call; never
chain commands with `&&` (the person may be on Windows PowerShell 5.1, which does not support it).
`uv run python` below means that exact command; if you fell back to plain Python in step 1, use
`python` (or `py` on Windows) in its place. `<name>` is the profile name.

## 1. Runtime

1. Run `uv --version`.
2. If it fails, install uv:
   - Windows: `winget install --id=astral-sh.uv -e`. If winget is missing or fails:
     `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`
   - Mac or Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`
3. A fresh install is not on PATH in this session yet. Call uv by its full path until the person
   restarts the terminal: Windows `%USERPROFILE%\.local\bin\uv.exe` or
   `%LOCALAPPDATA%\Microsoft\WinGet\Links\uv.exe`; Mac/Linux `~/.local/bin/uv`. (Use whichever
   exists; in PowerShell call it with `&`.)
4. Run `uv sync`. It installs Python and all packages.
5. Fallback if uv cannot be installed: if `python --version` shows 3.10 or newer, run
   `pip install -r requirements.txt` and use `python` instead of `uv run python`. Otherwise tell
   the person to install Python from python.org and stop.

`uv sync` is fast when nothing changed, so it is fine to run it every time.

## 2. Find the resume

Look in this order and stop at the first hit:

1. A profile named in `$ARGUMENTS`, or the only one in `uv run python scripts/new_profile.py --list`,
   that already has a resume (the doctor in step 7 reports it; or look in `profiles/<name>/`).
2. The `resume/` folder at the top of this project (one PDF, DOCX or TXT in it).
3. A file path the person already gave you in the chat.

If none of those exist, ask once: "Please put your resume (PDF, Word or text) into the `resume`
folder in this project, or paste its file path here." Wait for the answer. Never read anything
under `secrets/`.

## 3. Create the profile

Skip if the profile folder already exists.

1. Propose a name from the resume (lowercase, dashes, e.g. `jane-doe`) and confirm it, unless
   `$ARGUMENTS` gave one.
2. `uv run python scripts/new_profile.py <name> --resume "<path to the resume>"` copies the
   resume into `profiles/<name>/` and writes a starter `config.yaml`. (If the resume is already in
   the profile folder, omit `--resume`.)

## 4. Locations (required)

Read `profiles/<name>/config.yaml`. If `locations:` still has the placeholder (`YOUR CITY, COUNTRY`)
ask: "Which city or country should I search for jobs in? You can give more than one, and
'Remote' works too." Then edit the `locations:` block **in place** with the Edit tool, keeping
every comment and the rest of the file as it is. Never delete or touch `roles`, `workbook` or other
lines the person already set.

## 5. Roles (optional)

Do not ask yet. If `roles:` in config.yaml is empty, you will propose roles from the resume in
step 8. If it already lists roles, those are the person's choices.

## 6. Create your job list

Skip this step if `profiles/<name>/agent-state.json` already exists (the job list was created
earlier). Running it again is harmless anyway: it only adds what is missing.

0. **Old Google sheet check.** If `init` (or the doctor) says this profile **kept its job list in a
   Google sheet before**, STOP here. Show the person that message (it has the sheet's address),
   explain in plain words that their history of jobs they have already seen is in that sheet, and
   ask them to open it, choose File > Download > Microsoft Excel (.xlsx), and save the file at the
   path the message gives. When they say it is done, run `init` again: it picks the file up and
   keeps their history. Never run `init --fresh` unless they explicitly say they want an empty
   list and will see every job again.
1. Run `uv run python scripts/sheet.py --profile <name> init`. It prints `WORKBOOK: <path>`.
2. Tell the person: "Your job list is an Excel file on your computer, here: <path>. Nothing is
   sent to the cloud, and no sign-in is needed." On a Mac they need Excel or the free LibreOffice
   to open it; Apple Numbers will not work. On any computer without such a program, suggest
   installing LibreOffice.
3. If it says `WORKBOOK_OPEN:`, the file is open in Excel: show the message, wait for them to
   close it, and run the same command again.
4. If it says the job list file is missing but was created earlier, show that message as it is
   and do not delete `agent-state.json` on your own: that would make the agent forget every job
   it has already shown them. Ask what they want.

## 7. Check

Run `uv run python scripts/doctor.py --profile <name>`. It must show no `FIX`. Fix what it names
(each FIX says how) and re-run until it passes. A `WARN` about `profile.md` is expected until
step 8. If it says the job list is missing, go back to step 6.

## 8. Write profile.md

Skip if `profiles/<name>/profile.md` exists and the person did not ask to regenerate it.

Read the resume (the doctor printed its path; `config.find_resume('<name>')` also returns it). If
it is a DOCX, extract the text with Python's `zipfile` (word/document.xml) rather than failing.

Decide the target roles: if `roles` in config.yaml is non-empty use those; otherwise propose 2-4
target roles from the resume (current or most recent role first, then natural next steps). Don't
ask about seniority; derive it from years of experience and role scope. Honor
`explore_adjacent_roles` (default true).

Write `profiles/<name>/profile.md` covering: a short summary, experience (employer, dates,
location, what they actually did), education, core skills grouped sensibly, domains they have
worked in, languages, a seniority judgment with a rough years-of-experience band, any
work-authorization, relocation or location constraints that matter for how jobs should be flagged
(only what the resume or the person says; ask if unclear, since getting this wrong either hides
good jobs or wastes scoring on jobs they can't take), and a numbered `## Search keywords` list of
8-15 terms (target roles first, then adjacent titles). `scripts/fetch.py` parses that exact
numbered-list format, so keep the heading and the `1.`, `2.`, ... lines. Nothing in profile.md
should assume a country or industry; describe this person's situation in their own terms.

## 9. Approval

Show the roles, the keyword list and a short profile summary, and get the person's okay before
moving on. This is the input every later step depends on, so a human check now beats a bad run
later. If they changed the roles, update `roles:` in config.yaml (and profile.md's keywords) to
match.

## 10. Smoke test and wrap up

1. Run `uv run python scripts/doctor.py --profile <name> --online`. It should show a working
   LinkedIn search. (A quiet or blocked search is a `WARN`, not a failure; explain it.)
2. Open their job list for them with `uv run python scripts/sheet.py --profile <name> open`
   (if it says it cannot, give them the path from step 6 and the LibreOffice hint). Tell them
   setup is done and explain the list in a few plain lines:
   - Each new job is a row. Click the filter arrow on the **Score** column, or use Data > Sort,
     to put the best matches first.
   - They only fill in **Status** (a dropdown: Interested, Applied, Not for me...) and a short
     **Reason**. The agent learns from those, especially "Not for me" with a reason.
   - Save (Ctrl+S) and close the file before the next `/job-run`; the agent cannot update a file
     that is open.
   - The **Preferences** tab holds optional questions (salary, work setup, industries to avoid);
     blank means no preference, and they can fill them in any time.
3. Offer to run a small first search: `/job-run <name> 10` scores only 10 jobs, so it is a cheap
   way to see results.
