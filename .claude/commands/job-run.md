---
description: Run one job search + learning cycle for a profile
argument-hint: <profile-name> [max-jobs-to-score]
model: sonnet
---

Run the full cycle for profile `$ARGUMENTS`. The first word is the profile name; an optional
second word is the most jobs to score this run. If no profile was given, run
`uv run python scripts/new_profile.py --list`: use the only profile if there is one, otherwise ask.

Run `uv run python scripts/doctor.py --profile <name>` first. If it reports anything to FIX, stop and
help fix it. If it says Google sign-in is needed, run
`uv run python scripts/sheet.py --profile <name> connect` (it opens a browser tab; tell the person
to pick their account and click Continue or Allow, and run it in the background so you can read its
output), then re-run the doctor. Every script below runs with `uv run python`; if the person has no
uv, use `python` (or `.venv/Scripts/python` on Windows, `.venv/bin/python` elsewhere) instead.
`<name>` is the profile and `RUN` is the run folder from step 1.

**Principles that keep this cheap and safe. Do not break them:**
- Never read or retype job descriptions yourself. Scripts write batch files; the `job-scorer`
  subagent reads them. You only run commands and read their one-line output.
- Everything is saved as it happens. If anything fails or a usage limit stops the run, nothing
  finished is lost; tell the person to run `/job-run <name>` again and it picks up where it
  stopped.

1. **Start.** `uv run python scripts/pipeline.py new-run --profile <name>` prints the run folder (`RUN`).
   Then `uv run python scripts/pipeline.py recover --profile <name>`: it saves scores an interrupted
   earlier run produced but never wrote to the sheet.

2. **Read the sheet.** `uv run python scripts/sheet.py --profile <name> meta` gives `last_run_iso`.
   `uv run python scripts/sheet.py --profile <name> changed-status --out RUN/changed.json` gives rows whose
   Status/Reason changed since last time.

3. **Learn, only if `changed.json` is non-empty.** Read `profiles/<name>/profile.md` and
   `uv run python scripts/sheet.py --profile <name> rules --out RUN/rules.json`. For each changed row work
   out what it teaches (see CLAUDE.md's "Learning loop": a Block rule needs 2+ similar rejections;
   never touch a rule whose "Added by" is not `AI`). Write new rules as a JSON list of `rule`,
   `type` (Block/Lower/Boost), `keywords` (`;`-separated), `based_on` to `RUN/new_rules.json`, then
   `uv run python scripts/sheet.py --profile <name> add-rules --in RUN/new_rules.json`. Then
   `uv run python scripts/sheet.py --profile <name> mark-status-synced --job-ids <ids>`. Skip this whole
   step if `changed.json` is empty.

4. **Backlog first.** `uv run python scripts/sheet.py --profile <name> pending --out RUN/pending.json`.
   If it holds jobs, an earlier run was interrupted: tell the person "N jobs from an earlier run
   still need scores; finishing those first", then run
   export `rules` and `preferences` to `RUN/rules.json` and `RUN/prefs.json` (the commands in step
   9), then run
   `uv run python scripts/pipeline.py resume-plan --pending RUN/pending.json --cap <cap> --run-dir RUN`
   (the cap is the command's second argument if given, otherwise ask as described in step 8,
   suggesting `max_jobs_to_score_per_run` from the profile's config.yaml) and jump to step 10. When the backlog is done, tell them to run `/job-run`
   again for new jobs. If nothing is pending, continue.

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
   "Don't score, just add the jobs". Explain that every new job lands in their sheet either way;
   the cap only limits how many get AI scores. If they choose none, use cap 0.

9. **Write every job to the sheet now, before any slow step.**
   `uv run python scripts/sheet.py --profile <name> rules --out RUN/rules.json`, then
   `uv run python scripts/sheet.py --profile <name> preferences --out RUN/prefs.json`, then
   `uv run python scripts/pipeline.py plan --deduped RUN/deduped.json --rules RUN/rules.json --cap <cap> --run-dir RUN`
   and `uv run python scripts/sheet.py --profile <name> append-jobs --in RUN/append.json`. Only now
   advance the search window, unless step 5 was blocked:
   `uv run python scripts/sheet.py --profile <name> update-meta --set last_run_iso=<started_at from
   RUN/raw.json> --set jobs_seen_last_run=<n> --set jobs_added_last_run=<n> --set
   reposts_skipped_last_run=<n> --set linkedin_status=<ok|blocked>`.
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
      scores, writes them to the sheet right away, and queues one retry for any job the scorer
      skipped. Report its one-line result.
    Stop and explain plainly if the scorer errors or a usage limit is hit; everything scored so far
    is already in the sheet.

13. **Summarize** in a few lines: new jobs added, how many scored, the top 3 from
    `uv run python scripts/pipeline.py summary --run-dir RUN --top 3` (title, company, score, link), rules
    added or changed, reposts skipped, any LinkedIn trouble, and any jobs left Pending (they are
    finished by the next `/job-run`). If AI_MetaData shows zero jobs found two runs in a row, say
    plainly that LinkedIn is probably blocking, not that there are no jobs. Remind them not to sort
    or delete rows in the sheet while a run is writing.

**Timing note:** each keyword/location search takes roughly 15-40s and each description fetch a
few seconds, so large runs take a long time; that is expected, not stuck. Run the search and
description commands in the background so the session isn't blocked waiting.
