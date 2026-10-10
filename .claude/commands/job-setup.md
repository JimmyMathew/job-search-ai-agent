---
description: Set up everything from scratch (install, resume, Google sheet), or regenerate a profile.md
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
every comment and the rest of the file as it is. Never delete or touch `roles`, `auth` or other
lines the person already set.

## 5. Roles (optional)

Do not ask yet. If `roles:` in config.yaml is empty, you will propose roles from the resume in
step 8. If it already lists roles, those are the person's choices.

## 6. Google sign-in and sheet

Skip this step if `profiles/<name>/agent-state.json` already exists (the sheet was created
earlier) or `config.yaml` sets `sheet:` (an existing sheet, advanced option). Step 7 will say if
signing in again is needed; never run `connect` for those people unless the doctor says to.

1. Tell the person: "A browser tab will open now. Pick your Google account and click Continue or
   Allow. The app can only see the sheet it creates, nothing else in your Google Drive."
2. Run `uv run python scripts/sheet.py --profile <name> connect` **in the background** (it
   waits up to 5 minutes for the sign-in), then read its output as it appears. If it prints a link
   ("If no browser opened, open this link"), show the link to the person.
3. When it finishes it prints `SHEET_URL: ...`. Show that address as the person's new sheet.
4. If it fails because `google_oauth_client.json` is missing, the project owner has not finished
   their one-time setup (`docs/maintainer-oauth-client.md`). Tell the person plainly that this
   copy of the app cannot sign in yet and who to contact; offer the advanced option
   (`docs/service-account-setup.md`) only if they say they want it.
5. If the person closes the tab or it times out, just run the same command again. It is safe to
   repeat and never creates a second sheet.

## 7. Check

Run `uv run python scripts/doctor.py --profile <name>`. It must show no `FIX`. Fix what it names
(each FIX says how) and re-run until it passes. A `WARN` about `profile.md` is expected until
step 8. If it says sign-in is needed, go back to step 6.

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
2. Tell the person setup is done, and show their sheet address again. Mention the **Preferences**
   tab in their sheet: optional questions (salary, work setup, industries to avoid); blank means
   no preference, and they can fill them in any time.
3. Offer to run a small first search: `/job-run <name> 10` scores only 10 jobs, so it is a cheap
   way to see results.
