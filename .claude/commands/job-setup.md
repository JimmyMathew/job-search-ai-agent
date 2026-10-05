---
description: Onboard a new job seeker profile, or regenerate an existing one's profile.md
argument-hint: <profile-name>
---

Set up profile `$ARGUMENTS`. This is the only command that reads a resume; everything else
reads `profile.md` instead, so get this right. Keep your questions to the person short and
non-technical; many users have never touched a terminal before.

1. **Create the folder if it doesn't exist:** `python scripts/new_profile.py $ARGUMENTS`
   (it copies the template). If no name was given, ask for a short one (lowercase, dashes).

2. **Run the checklist:** `python scripts/doctor.py --profile $ARGUMENTS`. Whatever it marks FIX
   (missing resume, missing locations or sheet address in config.yaml, Google key, sheet not
   shared), fix with the person using its instructions and `docs/google-sheet-setup.md`, then
   re-run until it passes. Don't proceed past a FIX.

3. **Read the resume.** It's whichever PDF, DOCX or TXT is in `profiles/$ARGUMENTS/`
   (`python -c "import sys; sys.path.insert(0,'scripts'); import util; print(util.find_resume('$ARGUMENTS'))"`
   prints its path). If it is a DOCX, extract the text with Python's zipfile (word/document.xml)
   rather than failing.

4. **Decide the target roles.** If `roles` in config.yaml is non-empty, those are the person's
   chosen roles. Otherwise propose 2-4 target roles from the resume (current/most recent role
   first, then natural next steps). Don't ask about seniority; derive it from years of experience
   and role scope. Honor `explore_adjacent_roles` (default true).

5. **Write `profiles/$ARGUMENTS/profile.md`** covering: a short summary, experience (employer,
   dates, location, what they actually did), education, core skills grouped sensibly, domains
   they've worked in, languages, a seniority judgment with a rough years-of-experience band, any
   work-authorization, relocation or location constraints that matter for how jobs should be
   flagged (only what the resume or the person says; ask if unclear, since getting this wrong
   either hides good jobs or wastes scoring on jobs they can't take), and a numbered
   "## Search keywords" list of 8-15 terms (target roles first, then adjacent titles;
   `scripts/fetch.py` parses this exact numbered list, so keep the heading and format).
   Nothing in profile.md should assume a country or industry; describe this person's situation
   in their own terms.

6. **Show the roles/keyword list and a profile summary and get the person's okay** before moving
   on. This is the input every later step depends on, so a human check now beats a bad run later.
   If they changed the roles, update `roles` in config.yaml too.

7. **Create the sheet tabs if needed.** `python scripts/doctor.py --profile $ARGUMENTS` already
   does this once the sheet is shared; it creates Jobs and Preferences (the person's tabs) plus
   AI_Rules and AI_MetaData (the agent's), seeds Preferences with questions and adds the Status
   dropdown. Tell the person they can fill in the Preferences tab (all optional) anytime.

8. **Smoke test:** `python scripts/doctor.py --profile $ARGUMENTS --online` should show a working
   LinkedIn search. Then tell them setup is done and to run `/job-run $ARGUMENTS`.
