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
