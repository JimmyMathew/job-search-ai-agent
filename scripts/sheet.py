"""All Google Sheet reads/writes for one profile's job sheet.

CLI (each subcommand prints JSON to stdout, or reads/writes files):
    python scripts/sheet.py init-tabs      --profile <name>
    python scripts/sheet.py test-write     --profile <name>
    python scripts/sheet.py meta           --profile <name>
    python scripts/sheet.py rules          --profile <name> --out rules.json
    python scripts/sheet.py preferences    --profile <name> --out prefs.json
    python scripts/sheet.py changed-status --profile <name> --out changed.json
    python scripts/sheet.py dedupe         --profile <name> --in raw.json --out deduped.json
    python scripts/sheet.py append-jobs    --profile <name> --in jobs.json
    python scripts/sheet.py update-meta    --profile <name> --set last_run_iso=... --set ...

One tab holds every job the agent has ever seen for this profile — scored high,
scored low, or blocked by a rule — sorted by score, nothing hidden in a second
tab. The person only ever edits Status and Reason; everything else is agent-
written. See CLAUDE.md's "Sheet design" section for the reasoning.

Auth: a Google service account key (default secrets/service-account.json, override with
`credentials:` in config.yaml) with the sheet shared to its client_email as Editor.
See docs/google-sheet-setup.md for setup.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from util import (
    is_repost,
    load_config,
    load_json,
    now_iso,
    save_json,
    credentials_path,
)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

JOBS_TAB = "Jobs"
RULES_TAB = "AI_Rules"
META_TAB = "AI_MetaData"
PREFERENCES_TAB = "Preferences"

# Jobs and Preferences are the job seeker's tabs; the "AI_" prefix on the other two
# marks them as agent-owned bookkeeping, not something the person needs to open.

JOBS_HEADERS = [
    "Date added", "Score", "Job ID", "Job title", "Company", "Location", "Posted",
    "Link", "Why it matches", "Gaps", "Flags", "Role type",
    "Status", "Reason",
]
RULES_HEADERS = ["Rule", "Type", "Keywords", "Based on", "Added by", "Active"]
META_HEADERS = ["Key", "Value"]
PREFERENCES_HEADERS = ["Criterion", "Value", "Example"]

STATUS_VALUES = [
    "New", "Interested", "Applied", "Not for me",
    "Rejected by company", "Interview", "Offer",
]

# Seeded once when the tab is created. Value stays blank until the job seeker fills
# it in directly in Sheets — a blank Value means "no preference stated," and the
# scorer falls back to profile.md / general judgment for that criterion.
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

# Meta key holding a {job_id: "status||reason"} JSON blob — the last Status/Reason
# the learning step has already read for each job. Keeping this in Meta (instead of
# a second column next to Status in Jobs) keeps Jobs to exactly one status-shaped
# column for the person to edit.
STATUS_SYNC_META_KEY = "status_sync_snapshot"

REPOST_WINDOW_DAYS = 30


class SheetClient:
    def __init__(self, profile: str):
        self.profile = profile
        self.config = load_config(profile)
        self._spreadsheet = None

    @property
    def spreadsheet(self):
        if self._spreadsheet is None:
            import gspread
            from google.oauth2.service_account import Credentials

            key_path = credentials_path(self.config)
            if not key_path.exists():
                raise RuntimeError(
                    f"Missing {key_path}. Create a Google service account, download its "
                    f"JSON key there, and share the sheet with its client_email as Editor. "
                    f"Step-by-step: docs/google-sheet-setup.md"
                )
            creds = Credentials.from_service_account_file(str(key_path), scopes=SCOPES)
            client = gspread.authorize(creds)
            self._spreadsheet = client.open_by_key(self.config["sheet_id"])
        return self._spreadsheet

    def _worksheet(self, title: str, headers: list[str]):
        import gspread

        try:
            return self.spreadsheet.worksheet(title)
        except gspread.WorksheetNotFound:
            ws = self.spreadsheet.add_worksheet(title=title, rows=1000, cols=len(headers))
            ws.append_row(headers)
            ws.freeze(rows=1)
            return ws

    def init_tabs(self) -> None:
        self._worksheet(JOBS_TAB, JOBS_HEADERS)
        self._worksheet(RULES_TAB, RULES_HEADERS)
        self._worksheet(META_TAB, META_HEADERS)
        self.ensure_preferences_seeded()
        self.apply_status_validation()

    def ensure_preferences_seeded(self) -> None:
        """Create the Preferences tab with example rows if it doesn't exist yet.
        Never touches it if it already exists — the job seeker's filled-in Values
        must never be overwritten by re-running setup."""
        import gspread

        try:
            self.spreadsheet.worksheet(PREFERENCES_TAB)
            return  # already exists — leave whatever the job seeker has entered alone
        except gspread.WorksheetNotFound:
            pass
        ws = self.spreadsheet.add_worksheet(
            title=PREFERENCES_TAB, rows=50, cols=len(PREFERENCES_HEADERS)
        )
        ws.append_row(PREFERENCES_HEADERS)
        ws.append_rows([list(row) for row in PREFERENCES_SEED_ROWS])
        ws.freeze(rows=1)

    def apply_status_validation(self) -> None:
        """Add the Status dropdown (STATUS_VALUES) to the Jobs tab, and only there.

        Clears validation across the whole Jobs data range first. Without that, a
        rule set on the Status column under an older header order (e.g. before a
        column reorder) lingers on whatever column now sits at that old position —
        which is exactly how Role type ended up showing the Status dropdown after
        Status moved from column L to column M."""
        from gspread.utils import ValidationConditionType

        ws = self._worksheet(JOBS_TAB, JOBS_HEADERS)
        self.spreadsheet.batch_update(
            {
                "requests": [
                    {
                        "setDataValidation": {
                            "range": {
                                "sheetId": ws.id,
                                "startRowIndex": 1,
                                "startColumnIndex": 0,
                                "endColumnIndex": len(JOBS_HEADERS),
                            }
                        }
                    }
                ]
            }
        )
        col_letter = chr(ord("A") + JOBS_HEADERS.index("Status"))
        ws.add_validation(
            f"{col_letter}2:{col_letter}",
            ValidationConditionType.one_of_list,
            STATUS_VALUES,
            showCustomUi=True,
        )

    def test_write(self) -> None:
        ws = self._worksheet(META_TAB, META_HEADERS)
        ws.append_row(["test_write", now_iso()])

    # ---- Meta -----------------------------------------------------------

    def get_meta(self) -> dict[str, str]:
        ws = self._worksheet(META_TAB, META_HEADERS)
        rows = ws.get_all_records()
        return {str(r["Key"]): str(r["Value"]) for r in rows if r.get("Key")}

    def update_meta(self, updates: dict[str, str]) -> None:
        ws = self._worksheet(META_TAB, META_HEADERS)
        rows = ws.get_all_values()[1:]  # skip header
        existing_keys = {row[0]: idx for idx, row in enumerate(rows, start=2) if row}
        for key, value in updates.items():
            if key in existing_keys:
                ws.update(f"A{existing_keys[key]}:B{existing_keys[key]}", [[key, value]])
            else:
                ws.append_row([key, value])

    # ---- Rules ------------------------------------------------------------

    def get_rules(self) -> list[dict[str, Any]]:
        ws = self._worksheet(RULES_TAB, RULES_HEADERS)
        return [
            {
                "rule": r.get("Rule", ""),
                "type": r.get("Type", ""),
                "keywords": r.get("Keywords", ""),
                "based_on": r.get("Based on", ""),
                "added_by": r.get("Added by", ""),
                "active": r.get("Active", ""),
            }
            for r in ws.get_all_records()
        ]

    # ---- Preferences --------------------------------------------------------

    def get_preferences(self) -> list[dict[str, str]]:
        """Only rows the job seeker actually filled in — blank Value means no
        preference stated, so the scorer should ignore that row entirely."""
        ws = self._worksheet(PREFERENCES_TAB, PREFERENCES_HEADERS)
        return [
            {"criterion": r.get("Criterion", ""), "value": r.get("Value", "")}
            for r in ws.get_all_records()
            if str(r.get("Value", "")).strip()
        ]

    # ---- Dedupe -----------------------------------------------------------

    def get_known_job_ids(self) -> set[str]:
        ws = self._worksheet(JOBS_TAB, JOBS_HEADERS)
        return {
            str(row.get("Job ID", "")).strip()
            for row in ws.get_all_records()
            if str(row.get("Job ID", "")).strip()
        }

    def get_recent_jobs_for_repost_check(self) -> list[dict[str, Any]]:
        ws = self._worksheet(JOBS_TAB, JOBS_HEADERS)
        return [
            {
                "title": row.get("Job title", ""),
                "company": row.get("Company", ""),
                "posted_date": row.get("Posted") or None,
            }
            for row in ws.get_all_records()
        ]

    def dedupe(self, jobs: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        known_ids = self.get_known_job_ids()
        recent = self.get_recent_jobs_for_repost_check()

        new_jobs, reposts = [], []
        for job in jobs:
            if job["job_id"] in known_ids:
                continue
            if is_repost(
                job.get("title", ""),
                job.get("company", ""),
                job.get("posted_date"),
                recent,
                window_days=REPOST_WINDOW_DAYS,
            ):
                reposts.append(job)
                continue
            new_jobs.append(job)
        return {"new": new_jobs, "reposts": reposts}

    # ---- Status changes (feedback for the learning step) -----------------

    def _get_status_sync_snapshot(self) -> dict[str, str]:
        raw = self.get_meta().get(STATUS_SYNC_META_KEY, "")
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    def get_changed_status_rows(self) -> list[dict[str, Any]]:
        ws = self._worksheet(JOBS_TAB, JOBS_HEADERS)
        snapshot = self._get_status_sync_snapshot()
        changed = []
        for row in ws.get_all_records():
            job_id = str(row.get("Job ID", ""))
            status = str(row.get("Status", "")).strip()
            reason = str(row.get("Reason", "")).strip()
            current = f"{status}||{reason}"
            # A freshly written row says "New" with no reason; that isn't feedback.
            if status == "New" and not reason and job_id not in snapshot:
                continue
            if status and current != snapshot.get(job_id, ""):
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

    def mark_status_synced(self, job_ids: list[str]) -> None:
        """After the learning step reads feedback, snapshot Status+Reason (in Meta,
        not a sheet column) so the same row isn't treated as 'changed' again."""
        ws = self._worksheet(JOBS_TAB, JOBS_HEADERS)
        snapshot = self._get_status_sync_snapshot()
        wanted = set(job_ids)
        for row in ws.get_all_records():
            job_id = str(row.get("Job ID", ""))
            if job_id in wanted:
                status = str(row.get("Status", "")).strip()
                reason = str(row.get("Reason", "")).strip()
                snapshot[job_id] = f"{status}||{reason}"
        self.update_meta({STATUS_SYNC_META_KEY: json.dumps(snapshot, ensure_ascii=False)})

    # ---- Writing rows -------------------------------------------------------

    def append_jobs(self, jobs: list[dict[str, Any]]) -> None:
        """Write jobs to the single Jobs tab — scored, low-scored, or blocked by
        a rule all go here; only the Why/Gaps/Score fields differ. Sort jobs by
        score (descending, blank/unscored last) before calling this so related
        jobs land near each other."""
        ws = self._worksheet(JOBS_TAB, JOBS_HEADERS)
        rows = [
            [
                job.get("date_added", now_iso()[:10]),
                job.get("score", ""),
                job.get("job_id", ""),
                job.get("title", ""),
                job.get("company", ""),
                job.get("location", ""),
                job.get("posted_date", ""),
                job.get("link", ""),
                job.get("why") or job.get("filtered_reason", ""),
                job.get("gaps", ""),
                ", ".join(job.get("flags", [])) if isinstance(job.get("flags"), list) else job.get("flags", ""),
                job.get("role_type", ""),
                "New",
                "",
            ]
            for job in jobs
        ]
        if rows:
            ws.append_rows(rows)

    def update_jobs(self, jobs: list[dict[str, Any]]) -> int:
        """Fill in Score/Why/Gaps/Flags/Role type on rows that are already in the
        sheet (matched by Job ID) — doesn't touch Status/Reason or append new rows.

        This is the second half of a "write early, fill in later" pattern: write
        raw listings to the sheet immediately after dedupe (as "pending", via
        append_jobs), so a crash during the slow description-fetch/scoring steps
        never loses visibility into what was already found. Once scoring finishes,
        call this to update those same rows in place instead of appending
        duplicates. Rows whose Job ID isn't found are silently skipped — this
        method only updates, it never creates. Returns how many rows were updated.
        """
        ws = self._worksheet(JOBS_TAB, JOBS_HEADERS)
        rows = ws.get_all_values()
        header = rows[0]
        job_id_col = header.index("Job ID")
        score_col = header.index("Score")
        why_col = header.index("Why it matches")
        gaps_col = header.index("Gaps")
        flags_col = header.index("Flags")
        role_type_col = header.index("Role type")

        row_by_job_id = {
            row[job_id_col]: idx
            for idx, row in enumerate(rows[1:], start=2)
            if len(row) > job_id_col
        }

        updates = []
        updated_count = 0
        for job in jobs:
            row_idx = row_by_job_id.get(str(job.get("job_id", "")))
            if row_idx is None:
                continue
            flags = job.get("flags", "")
            flags_str = ", ".join(flags) if isinstance(flags, list) else flags
            values = {
                score_col: job.get("score", ""),
                why_col: job.get("why") or job.get("filtered_reason", ""),
                gaps_col: job.get("gaps", ""),
                flags_col: flags_str,
                role_type_col: job.get("role_type", ""),
            }
            for col, value in values.items():
                col_letter = chr(ord("A") + col)
                updates.append({"range": f"{col_letter}{row_idx}", "values": [[value]]})
            updated_count += 1

        if updates:
            ws.batch_update(updates)
        return updated_count


# ---- CLI -------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-tabs")
    sub.add_parser("test-write")
    sub.add_parser("meta")
    sub.add_parser("apply-status-validation")

    rules_p = sub.add_parser("rules")
    rules_p.add_argument("--out", type=Path, required=True)

    prefs_p = sub.add_parser("preferences")
    prefs_p.add_argument("--out", type=Path, required=True)

    changed_p = sub.add_parser("changed-status")
    changed_p.add_argument("--out", type=Path, required=True)

    dedupe_p = sub.add_parser("dedupe")
    dedupe_p.add_argument("--in", dest="in_path", type=Path, required=True)
    dedupe_p.add_argument("--out", type=Path, required=True)

    append_jobs_p = sub.add_parser("append-jobs")
    append_jobs_p.add_argument("--in", dest="in_path", type=Path, required=True)

    update_jobs_p = sub.add_parser("update-jobs")
    update_jobs_p.add_argument("--in", dest="in_path", type=Path, required=True)

    update_meta_p = sub.add_parser("update-meta")
    update_meta_p.add_argument("--set", action="append", default=[], metavar="KEY=VALUE")

    sync_p = sub.add_parser("mark-status-synced")
    sync_p.add_argument("--job-ids", nargs="+", required=True)

    args = parser.parse_args()
    client = SheetClient(args.profile)

    if args.command == "init-tabs":
        client.init_tabs()
        print("Tabs ready: Jobs, Preferences, AI_Rules, AI_MetaData")
    elif args.command == "test-write":
        client.test_write()
        print("Wrote a test row to Meta.")
    elif args.command == "apply-status-validation":
        client.apply_status_validation()
        print("Status dropdown applied to Jobs.")
    elif args.command == "meta":
        print(json.dumps(client.get_meta(), indent=2))
    elif args.command == "rules":
        save_json(args.out, client.get_rules())
        print(f"Wrote rules to {args.out}")
    elif args.command == "preferences":
        save_json(args.out, client.get_preferences())
        print(f"Wrote preferences to {args.out}")
    elif args.command == "changed-status":
        save_json(args.out, client.get_changed_status_rows())
        print(f"Wrote changed-status rows to {args.out}")
    elif args.command == "dedupe":
        jobs = load_json(args.in_path)
        jobs = jobs["jobs"] if isinstance(jobs, dict) else jobs
        result = client.dedupe(jobs)
        save_json(args.out, result)
        print(f"{len(result['new'])} new, {len(result['reposts'])} reposts skipped")
    elif args.command == "append-jobs":
        jobs = load_json(args.in_path)
        jobs = jobs["jobs"] if isinstance(jobs, dict) else jobs
        client.append_jobs(jobs)
        print(f"Appended {len(jobs)} rows to Jobs")
    elif args.command == "update-jobs":
        jobs = load_json(args.in_path)
        jobs = jobs["jobs"] if isinstance(jobs, dict) else jobs
        updated = client.update_jobs(jobs)
        print(f"Updated {updated} of {len(jobs)} rows in Jobs (matched by Job ID)")
    elif args.command == "update-meta":
        updates = dict(item.split("=", 1) for item in args.set)
        client.update_meta(updates)
        print(f"Updated Meta: {updates}")
    elif args.command == "mark-status-synced":
        client.mark_status_synced(args.job_ids)
        print(f"Synced status for {len(args.job_ids)} jobs")


if __name__ == "__main__":
    main()
