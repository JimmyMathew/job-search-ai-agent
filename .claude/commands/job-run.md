---
description: Run one job search + learning cycle for a profile
argument-hint: <profile-name> [max-jobs-to-score]
model: sonnet
---

Run the full cycle for profile `$ARGUMENTS`. The first word is the profile name; an optional
second word is the most jobs to score this run. If no profile was given, run
`uv run python scripts/new_profile.py --list`: use the only profile if there is one, otherwise ask.

Run `uv run python scripts/doctor.py --profile <name>` first. If it reports anything to FIX, stop and
help fix it (a missing job list is fixed by `uv run python scripts/sheet.py --profile <name> init`).
**Exception:** if the doctor (or `init`) mentions an **old Google sheet**, STOP. Show the person
that message, explain that their history of jobs is in that sheet and has to be exported to Excel
first (File > Download > Microsoft Excel), and wait. Never run `init --fresh` unless they
explicitly say yes to starting an empty list: it makes them see every job again.
Then run `uv run python scripts/sheet.py --profile <name> ready`: it checks the person's Excel
job list can be read and is not open, and saves a backup copy of it. Every script below runs with
`uv run python`; if the person has no uv, use `python` (or `.venv/Scripts/python` on Windows,
`.venv/bin/python` elsewhere) instead. `<name>` is the profile and `RUN` is the run folder from
step 1.

**Principles that keep this cheap and safe. Do not break them:**
- Never read or retype job descriptions yourself. Scripts write batch files; the `job-scorer`
  subagent reads them. You only run commands and read their one-line output.
- Everything is saved as it happens. If anything fails or a usage limit stops the run, nothing
  finished is lost; tell the person to run `/job-run <name>` again and it picks up where it
  stopped. The run is always safe to abandon.
- **If the job list is open in Excel,** any `sheet.py` or `pipeline.py` command that writes to it
  stops without changing anything and prints a message starting `WORKBOOK_OPEN:`. When you see
  that from any command: show the message to the person exactly as it is, wait for them to say
  the file is closed, then run the **same command again** (every write is safe to repeat).
  If it still says it is open after two retries, ask whether they are sure Excel is closed; if
  they are, offer `uv run python scripts/sheet.py --profile <name> clear-lock` (it removes a
  leftover lock file) and then retry. Never work around it by writing elsewhere.
- If a command instead says **another update of the job list is still running** (or the file was
  "changed while I was updating it"), nothing was lost: wait a few seconds, or for the person
  to close Excel as the message says, and run the same command again. Prefer running
  `sheet.py` and `pipeline.py` commands one after another rather than at the same time.
- If it says the job list is **read-only**, show the message: the person has to untick
  Read-only in the file's Properties.

1. **Start.** `uv run python scripts/pipeline.py new-run --profile <name>` prints the run folder (`RUN`).
   Then `uv run python scripts/pipeline.py recover --profile <name>`: it saves scores an interrupted
   earlier run produced but never wrote to the job list.

2. **Read the job list.** `uv run python scripts/sheet.py --profile <name> meta` gives `last_run_iso`.
   `uv run python scripts/sheet.py --profile <name> changed-status --out RUN/changed.json` gives rows whose
   Status/Reason changed since last time.

3. **Learn, only if `changed.json` is non-empty.** Read `profiles/<name>/profile.md` and
   `uv run python scripts/sheet.py --profile <name> rules --out RUN/rules.json`. For each changed row work
   out what it teaches (see CLAUDE.md's "Learning loop": a Block rule needs 2+ similar rejections;
   never touch a rule whose "Added by" is not `AI`). Write new rules as a JSON list of `rule`,
   `type` (Block/Lower/Boost), `keywords` (`;`-separated), `based_on` to `RUN/new_rules.json`, then
   `uv run python scripts/sheet.py --profile <name> add-rules --in RUN/new_rules.json`. Then
   `uv run python scripts/sheet.py --profile <name> mark-status-synced --in RUN/changed.json`
   (it records exactly what `changed.json` reported, so an edit the person makes meanwhile is
   learned from next time). Skip this whole
   step if `changed.json` is empty.

4. **Backlog first.** `uv run python scripts/sheet.py --profile <name> pending --out RUN/pending.json`.
   If it holds no jobs, continue with step 5. Otherwise an earlier run was interrupted: tell the
   person "N jobs from an earlier run still need scores; finishing those first", then:
   - Export the rules and preferences as in step 9 (`rules --out RUN/rules.json`, then
     `preferences --out RUN/prefs.json`).
   - Settle the cap: the command's second argument if given, otherwise ask as described in step 8,
     suggesting `max_jobs_to_score_per_run` from the profile's config.yaml.
   - Run
     `uv run python scripts/pipeline.py resume-plan --pending RUN/pending.json --cap <cap> --run-dir RUN`
     and jump to step 10.
   When the backlog is done, tell them to run `/job-run` again for new jobs.

5. **Search (listing only, cheap).** `hours_old` comes from `last_run_iso`, or
   `first_run_hours_old` in `profiles/<name>/config.yaml` if there is none.
   `uv run python scripts/fetch.py search --profile <name> --hours-old <n> --out RUN/raw.json` (run it as a
   background command; it takes minutes). Progress is saved after every search. Exit code 2 means
   LinkedIn blocked partway: continue with what was collected and say so at the end.

6. **Dedupe.**
   `uv run python scripts/sheet.py --profile <name> dedupe --in RUN/raw.json --out RUN/deduped.json`.

7. **Checkpoint.**
   `uv run python scripts/pipeline.py checkpoint --profile <name> --deduped RUN/deduped.json` (add
   `--first-run` if `last_run_iso` was empty). Show its `message` to the person.

8. **Decide the cap.** Use the number from the command's second argument if given. Otherwise ask
   with the AskUserQuestion tool, offering `suggested_cap`, a smaller number (about half), and
   "Don't score, just add the jobs". Explain that every new job lands in their job list either way;
   the cap only limits how many get AI scores. If they choose none, use cap 0.

9. **Write every job to the job list now, before any slow step.**
   `uv run python scripts/sheet.py --profile <name> rules --out RUN/rules.json`, then
   `uv run python scripts/sheet.py --profile <name> preferences --out RUN/prefs.json`, then
   `uv run python scripts/pipeline.py plan --deduped RUN/deduped.json --rules RUN/rules.json --cap <cap> --run-dir RUN`
   and `uv run python scripts/sheet.py --profile <name> append-jobs --in RUN/append.json`. Only now
   advance the search window, unless step 5 was blocked:
   `uv run python scripts/sheet.py --profile <name> update-meta --set last_run_iso=<started_at from
   RUN/raw.json> --set jobs_seen_last_run=<n> --set jobs_added_last_run=<n> --set
   reposts_skipped_last_run=<n> --set linkedin_status=<ok|partial|blocked>`.
   If the cap is 0 you are done: summarize.

10. **Fetch descriptions, only for the jobs to score.**
    `uv run python scripts/fetch.py descriptions --in RUN/to_score.json --out RUN/with_descriptions.json`
    (background). Every description is saved as it arrives, so if it exits with code 2 (blocked) or
    is interrupted, running the same command again continues without refetching. Jobs whose
    description could not be fetched stay Pending and are retried next run.

11. **Prefilter and batch.**
    `uv run python scripts/prefilter.py --in RUN/with_descriptions.json --rules RUN/rules.json --out-passed
    RUN/passed.json --out-blocked RUN/blocked.json`, then
    `uv run python scripts/sheet.py --profile <name> update-jobs --in RUN/blocked.json` (blocked rows get
    their reason), then
    `uv run python scripts/pipeline.py make-batches --profile <name> --run-dir RUN --jobs RUN/passed.json
    --rules RUN/rules.json --prefs RUN/prefs.json`.

12. **Score, one batch at a time.** Repeat until done:
    - `uv run python scripts/pipeline.py next --run-dir RUN` prints either `{"done": true}` or a batch with
      `batch_file`, `context_file` and `scores_file` paths.
    - Call the `job-scorer` subagent (`model: sonnet`) with a prompt that is only the four paths:
      the absolute path of `profiles/<name>/profile.md`, the `context_file`, the `batch_file`, and
      the `scores_file`. Don't paste any file content into the prompt.
    - `uv run python scripts/pipeline.py ingest --profile <name> --run-dir RUN --batch <n>` validates the
      scores, writes them to the job list right away, and queues one retry for any job the scorer
      skipped. Report its one-line result. If it prints `WORKBOOK_OPEN:` nothing was lost: the batch
      stays to do, so handle it as described above and run the same `ingest` command again.
    Stop and explain plainly if the scorer errors or a usage limit is hit; everything scored so far
    is already in the job list.

13. **Summarize** in a few lines: new jobs added, how many scored, the top 3 from
    `uv run python scripts/pipeline.py summary --run-dir RUN --top 3` (title, company, score, link), rules
    added or changed, reposts skipped, any LinkedIn trouble, and any jobs left Pending (they are
    finished by the next `/job-run`). If `sheet.py meta` shows zero jobs found two runs in a row, say
    plainly that LinkedIn is probably blocking, not that there are no jobs.

14. **Show them the list.** Run `uv run python scripts/sheet.py --profile <name> open` (if it
    cannot open the file, give the person the path that `sheet.py --profile <name> where` prints).
    Then tell them, briefly: sort by **Score** to see the best matches first; set **Status** and a
    short **Reason** on the jobs they have a view on (that is how the agent learns); save with
    Ctrl+S and **close the file before the next `/job-run`**; and don't add pictures or charts to
    the file, they can be lost the next time the agent saves it.

**Timing note:** each keyword/location search takes roughly 15-40s and each description fetch a
few seconds, so large runs take a long time; that is expected, not stuck. Run the search and
description commands in the background so the session isn't blocked waiting.
