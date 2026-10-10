"""The layout of a person's Google Sheet, plus the pure (no network) logic around it.

Jobs and Preferences are the job seeker's tabs; the AI_ prefix marks the tabs the agent
owns (rules it learned, run bookkeeping, a record of which feedback it has already read).
See CLAUDE.md's "Sheet design" section for why there is exactly one Jobs tab.
"""

from __future__ import annotations

from typing import Any

JOBS_TAB = "Jobs"
RULES_TAB = "AI_Rules"
META_TAB = "AI_MetaData"
STATUS_SYNC_TAB = "AI_StatusSync"
PREFERENCES_TAB = "Preferences"

JOBS_HEADERS = [
    "Date added",
    "Score",
    "Job ID",
    "Job title",
    "Company",
    "Location",
    "Posted",
    "Link",
    "Why it matches",
    "Gaps",
    "Flags",
    "Role type",
    "Status",
    "Reason",
]
RULES_HEADERS = ["Rule", "Type", "Keywords", "Based on", "Added by", "Active"]
META_HEADERS = ["Key", "Value"]
STATUS_SYNC_HEADERS = ["Job ID", "Status", "Reason"]
PREFERENCES_HEADERS = ["Criterion", "Value", "Example"]

TAB_HEADERS = {
    JOBS_TAB: JOBS_HEADERS,
    PREFERENCES_TAB: PREFERENCES_HEADERS,
    RULES_TAB: RULES_HEADERS,
    META_TAB: META_HEADERS,
    STATUS_SYNC_TAB: STATUS_SYNC_HEADERS,
}

STATUS_NEW = "New"
STATUS_VALUES = [
    STATUS_NEW,
    "Interested",
    "Applied",
    "Not for me",
    "Rejected by company",
    "Interview",
    "Offer",
]

# Legacy: older sheets kept the feedback snapshot as one JSON blob in AI_MetaData
# ({job_id: "status||reason"}). Read once to seed AI_StatusSync, never written again.
LEGACY_STATUS_SNAPSHOT_KEY = "status_sync_snapshot"

# Seeded once when the Preferences tab is created. A blank Value means "no preference
# stated" and the scorer falls back to profile.md / general judgment for that row.
PREFERENCES_SEED_ROWS = [
    (
        "What are you hoping this next role looks like?",
        "",
        'e.g. "Open to a team-lead role if the opportunity is right" or '
        '"Prefer to stay hands-on, not managing people yet"',
    ),
    (
        "Minimum acceptable salary (include currency and period)",
        "",
        'e.g. "60,000 per year" or "Not fixed, depends on the role"',
    ),
    (
        "Certifications or training in progress or planned",
        "",
        'e.g. "Studying for a PMP, finishing 2027" or "None currently"',
    ),
    (
        "Contract type",
        "",
        'e.g. "Permanent only" or "Open to fixed-term if the pay is good"',
    ),
    (
        "Work setup (on-site, hybrid, remote)",
        "",
        'e.g. "Hybrid or remote only" or "Happy to be on-site"',
    ),
    (
        "Work authorization or relocation notes",
        "",
        'e.g. "Need visa sponsorship" or "Can relocate within the country"',
    ),
    (
        "Industries or company types preferred",
        "",
        'e.g. "Healthcare, logistics" or "No preference"',
    ),
    (
        "Industries or company types to avoid",
        "",
        'e.g. "Gambling" or "None"',
    ),
    (
        "Tools or software you use, beyond what's on your resume",
        "",
        'e.g. "Salesforce" or "None"',
    ),
]

# Where each Jobs column's value comes from in a job dict. A callable gets the dict.
# "Why it matches" falls back to the prefilter's block reason, so blocked jobs explain
# themselves in the same column.
_JOB_FIELD_FOR_HEADER = {
    "Score": "score",
    "Job ID": "job_id",
    "Job title": "title",
    "Company": "company",
    "Location": "location",
    "Posted": "posted_date",
    "Link": "link",
    "Gaps": "gaps",
    "Role type": "role_type",
}

# The columns update_jobs fills in once scoring finishes (never Status or Reason).
SCORING_HEADERS = ["Score", "Why it matches", "Gaps", "Flags", "Role type"]


def _flags_text(flags: Any) -> str:
    return ", ".join(flags) if isinstance(flags, list) else (flags or "")


def job_cell(job: dict[str, Any], header: str, today: str) -> Any:
    """The value a job contributes to one Jobs column ("" for columns we don't write)."""
    if header == "Date added":
        return job.get("date_added", today)
    if header == "Why it matches":
        return job.get("why") or job.get("filtered_reason", "")
    if header == "Flags":
        return _flags_text(job.get("flags"))
    if header == "Status":
        return STATUS_NEW
    if header in _JOB_FIELD_FOR_HEADER:
        return job.get(_JOB_FIELD_FOR_HEADER[header], "")
    return ""


def job_to_row(job: dict[str, Any], headers: list[str], today: str) -> list[Any]:
    """A job as a sheet row laid out by the sheet's *actual* header order, so a sheet that
    has gained, lost or reordered columns is never written into the wrong ones."""
    return [job_cell(job, header, today) for header in headers]


def _feedback(status: str, reason: str) -> str:
    return f"{status}||{reason}"


def find_changed_status_rows(
    rows: list[dict[str, Any]], snapshot: dict[str, str]
) -> list[dict[str, str]]:
    """Rows whose Status/Reason differ from what the learning step last read.

    `snapshot` maps job ID to the "status||reason" last read. A job never read before is
    assumed to be at its initial state ("New", no reason), so freshly written rows are not
    feedback, but a row the person set to something else, or set back to New, is."""
    initial = _feedback(STATUS_NEW, "")
    changed = []
    for row in rows:
        job_id = str(row.get("Job ID", "")).strip()
        status = str(row.get("Status", "")).strip()
        reason = str(row.get("Reason", "")).strip()
        if not job_id or not status:
            continue
        if _feedback(status, reason) != snapshot.get(job_id, initial):
            changed.append(
                {
                    "job_id": job_id,
                    "title": row.get("Job title", ""),
                    "company": row.get("Company", ""),
                    "status": status,
                    "reason": reason,
                }
            )
    return changed


def snapshot_entry(status: str, reason: str) -> str:
    return _feedback(status.strip(), reason.strip())


# ---- Row states in "Why it matches" -------------------------------------------------
# A blank Score can mean several things; the text in "Why it matches" says which. Code
# decides what to do with a row by these prefixes, so they live here, in one place.

WHY_PENDING = "Pending — awaiting description fetch and scoring"
WHY_DESCRIPTION_UNAVAILABLE = "Pending — description unavailable (LinkedIn blocked), will retry"
WHY_SCORING_FAILED = "Scoring failed — will retry next run"
WHY_OVER_CAP = "Not scored this run (over the per-run limit)"
_PENDING_PREFIX = "Pending"
_RETRY_PREFIXES = (_PENDING_PREFIX, "Scoring failed")


def needs_scoring(score: Any, why: str) -> bool:
    """True for a row the agent still owes a score: no score yet, and marked pending or failed.
    Over-cap and blocked rows also have no score, but they are in their final state."""
    return str(score).strip() == "" and str(why).strip().startswith(_RETRY_PREFIXES)


VALID_ROLE_TYPES = ("target", "adjacent")


def parse_scores(text: str, expected_ids: set[str]) -> tuple[list[dict[str, Any]], list[str]]:
    """Validate a scorer's output.

    Accepts JSON lines, a JSON array, or either inside a code fence (scorers drift from the
    requested format). Returns (valid_results, problems). Results for IDs outside the batch,
    out-of-range scores, or an empty `why` are dropped and reported rather than written."""
    import json
    import re

    cleaned = re.sub(r"^\s*```(?:json|jsonl)?\s*$", "", text, flags=re.MULTILINE).strip()
    items: list[Any] = []
    try:
        loaded = json.loads(cleaned)
        items = loaded if isinstance(loaded, list) else [loaded]
    except ValueError:
        for line in cleaned.splitlines():
            line = line.strip().lstrip(",").rstrip(",")
            if not line:
                continue
            try:
                items.append(json.loads(line))
            except ValueError:
                items.append({"_unparseable": line[:80]})

    valid: list[dict[str, Any]] = []
    problems: list[str] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict) or "_unparseable" in item:
            problems.append(f"unreadable line: {str(item)[:80]}")
            continue
        job_id = str(item.get("job_id", "")).strip()
        score = item.get("score")
        if job_id not in expected_ids:
            problems.append(f"unknown job_id {job_id!r}")
        elif job_id in seen:
            problems.append(f"duplicate result for {job_id}")
        elif isinstance(score, bool) or not isinstance(score, int | float) or not 0 <= score <= 100:
            problems.append(f"{job_id}: score {score!r} is not 0-100")
        elif not str(item.get("why", "")).strip():
            problems.append(f"{job_id}: empty 'why'")
        else:
            seen.add(job_id)
            role_type = str(item.get("role_type", "")).strip().lower()
            valid.append(
                {
                    **item,
                    "job_id": job_id,
                    "score": int(round(score)),
                    "role_type": role_type if role_type in VALID_ROLE_TYPES else "adjacent",
                }
            )
    return valid, problems
