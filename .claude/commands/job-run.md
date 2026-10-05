---
description: Run one evening's job search + learning cycle for a profile
argument-hint: <profile-name>
---

Run the full cycle for profile `$ARGUMENTS`. If no argument was given, run
`python scripts/new_profile.py --list`: if exactly one profile exists use it, otherwise ask which.
Confirm the profile folder (`profiles/<name>/`) exists, then run
`python scripts/doctor.py --profile <name>`; if it reports anything to FIX, stop and help fix it
(see docs/google-sheet-setup.md) instead of guessing. Use the Python from `.venv` if the project
has one (`.venv/Scripts/python` on Windows, `.venv/bin/python` elsewhere), or `uv run python`.

Work through these steps in order. Every script call is a `Bash` call to a script under
`scripts/`, run from the repo root with `python scripts/<name>.py ...`. Nothing here should cost
tokens except step 2 (learning) and step 9 (scoring) — the rest is mechanical, so just run the
commands and read their small JSON/text output, don't re-derive what they already computed.

1. **Read the sheet.**
   `python scripts/sheet.py --profile $ARGUMENTS meta` → last run time (`last_run_iso`), health
   fields. `python scripts/sheet.py --profile $ARGUMENTS changed-status --out <run_dir>/changed.json`
   → rows whose Status/Reason changed since last run.

2. **Learn, only if `changed.json` is non-empty.** Read `profiles/$ARGUMENTS/profile.md` and the
   current AI_Rules tab (`python scripts/sheet.py --profile $ARGUMENTS rules --out <run_dir>/rules.json`).
   For each changed row, work out what it teaches (see CLAUDE.md's "Learning loop" section for
   the Status → signal mapping and guardrails — 2+ similar rejections before a Block rule, never
   touch a rule whose "Added by" is anything other than "AI", since those are human-written). Write any new/changed rules (with "Added by" set to `AI`) onto the AI_Rules
   tab with a short inline gspread script, or ask if you're unsure — this is the one step
   worth being careful and deliberate about, since bad rules quietly hide good jobs later. Then
   `python scripts/sheet.py --profile $ARGUMENTS mark-status-synced --job-ids <ids>` so these
   rows aren't treated as "changed" again next run. Skip this whole step entirely if
   `changed.json` was empty — don't spend tokens reasoning about nothing.

3. **Fetch (listing only).** Work out `hours_old` from `meta.last_run_iso` (or
   `first_run_hours_old` from `config.yaml` if there's no last run yet). `python scripts/fetch.py
   search --profile $ARGUMENTS --hours-old <n> --out <run_dir>/raw.json`. This is cheap (one
   request per page of ~10 results, no descriptions yet). If it exits with code 2, LinkedIn
   blocked partway — keep going with what was collected, and say so in the final summary.

4. **Dedupe.** `python scripts/sheet.py --profile $ARGUMENTS dedupe --in <run_dir>/raw.json --out
   <run_dir>/deduped.json`. Note the repost count for the summary.

5. **Checkpoint — before fetching any descriptions.** Tell the user the deduped count (e.g. "283
   new, 0 reposts. Fetch descriptions and score how many?") and wait for an answer (suggest `max_jobs_to_score_per_run` from config.yaml as the default cap): yes/all, a
   number ("top 40", highest-posted-date first), or stop. This has to come before description
   fetching, not after — descriptions are the expensive, blockable, per-job-request step, and
   showing counts after already paying that cost defeats the point of asking. (An earlier version
   of this file put the checkpoint after prefiltering, which meant descriptions got fetched for
   every deduped job regardless of the cap — a real bug, fixed 2026-09-28.) If they say stop, this
   run adds nothing — the deduped jobs haven't been written anywhere yet at this point.

6. **Write pending rows immediately — before the slow steps, not after.** Split
   `deduped.json`'s `"new"` array into the capped subset (to be scored) and the rest. Write both
   to the sheet right now: the capped subset with `score` blank and `why`/`filtered_reason` set to
   "Pending — awaiting description fetch and scoring"; the rest with `why` set to "not scored this
   run (over the per-run limit)" (this second group is already in its final state — no update
   needed later). Both via `python scripts/sheet.py --profile $ARGUMENTS append-jobs --in
   <file>.json`. This is the fix for a real problem: description-fetching and scoring can take
   30+ minutes on a large batch, and until 2026-09-28 nothing reached the sheet until the very
   end — a crash or interruption partway lost all visibility into work already done, even though
   the person could see nothing had been saved. Writing immediately means the worst case is
   "some rows still say Pending," never "nothing happened."

7. **Fetch descriptions — only for the capped subset.** `python scripts/fetch.py descriptions
   --in <run_dir>/deduped_new.json --out <run_dir>/with_descriptions.json`. If it exits with code
   2, LinkedIn blocked partway through descriptions — keep whatever came back (some jobs will have
   `description: ""`) and note it in the summary.

8. **Prefilter.** `python scripts/prefilter.py --in <run_dir>/with_descriptions.json --rules
   <run_dir>/rules.json --out-passed <run_dir>/passed.json --out-blocked <run_dir>/blocked.json`
   (re-fetch rules first if step 2 changed them). Blocked jobs already have a "Pending" row from
   step 6 — hold `blocked.json` to update (not append) at step 10, with `score` left blank and
   `why` set to the block reason.

9. **Score.** `python scripts/sheet.py --profile $ARGUMENTS preferences --out
   <run_dir>/preferences.json` — only rows the job seeker actually filled in come back; if empty,
   just tell the scorer there are no stated preferences this run. Split the jobs to score into
   batches of 10. For each batch, use the `Agent` tool with `subagent_type: job-scorer`, passing
   it the full text of `profiles/$ARGUMENTS/profile.md`, the active Lower/Boost rules from
   `rules.json`, `preferences.json`'s rows, and the batch (title, company, location, posted_date,
   link, description trimmed to ~1500 words — see `trim_description` in `scripts/util.py`, or trim
   inline). Collect the JSON-lines results.

10. **Update the pending rows with real results.** Merge each scored job's score/why/gaps/
    role_type/flags, combine with the blocked jobs from step 8, and write with
    `python scripts/sheet.py --profile $ARGUMENTS update-jobs --in <run_dir>/results.json` — this
    updates the existing "Pending" rows in place by Job ID, it does not append new rows (the
    over-the-cap rows from step 6 are already in their final state and don't need this). Then
    `python scripts/sheet.py --profile $ARGUMENTS update-meta --set last_run_iso=<now, only after
    the update succeeds> --set jobs_seen_last_run=<n> --set jobs_added_last_run=<n> --set
    reposts_skipped_last_run=<n> --set linkedin_status=<ok|blocked|partial>`.

11. **Summarize** in a few lines: new jobs added, top 3 by score (title, company, score, link),
    rules added or changed this run, reposts skipped, and any LinkedIn trouble. If AI_MetaData
    shows zero jobs found two runs in a row, say that plainly — it likely means LinkedIn is blocking, not
    that there are no jobs.

If anything fails after step 6, the sheet already has every job the run found — worst case some
rows are stuck at "Pending" (safe to re-run `update-jobs` once descriptions/scoring are re-done
for just those Job IDs). If it fails before step 6, nothing has been written yet, and the next
run picks up the same window again.

**Timing note:** each keyword/location search takes roughly 15-40s (paginated, 3-7s pause
between pages), and each description fetch takes a couple seconds plus a 2-5s pause. A profile
with N keywords × M locations runs N×M searches — e.g. 19 keywords × 3 locations = 57 —
so a full listing search alone can run several minutes; that's expected, not stuck. Run
fetch/description steps as background Bash calls so the session isn't blocked waiting on them.
