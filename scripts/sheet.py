"""All Google Sheet reads and writes for one profile's sheet.

CLI (each subcommand prints a short result, or reads/writes JSON files):
    python scripts/sheet.py --profile <name> connect        # sign in, create/find the sheet
    python scripts/sheet.py --profile <name> init-tabs
    python scripts/sheet.py --profile <name> test-write
    python scripts/sheet.py --profile <name> meta
    python scripts/sheet.py --profile <name> rules          --out rules.json
    python scripts/sheet.py --profile <name> add-rules      --in new_rules.json
    python scripts/sheet.py --profile <name> preferences    --out prefs.json
    python scripts/sheet.py --profile <name> changed-status --out changed.json
    python scripts/sheet.py --profile <name> pending        --out pending.json
    python scripts/sheet.py --profile <name> mark-status-synced --job-ids 123 456
    python scripts/sheet.py --profile <name> dedupe         --in raw.json --out deduped.json
    python scripts/sheet.py --profile <name> append-jobs    --in jobs.json
    python scripts/sheet.py --profile <name> update-jobs    --in results.json
    python scripts/sheet.py --profile <name> update-meta    --set last_run_iso=... --set ...
    python scripts/sheet.py --profile <name> apply-status-validation

One tab holds every job the agent has ever seen for this profile: scored high, scored
low, or blocked by a rule. Nothing is hidden in a second tab. The person only edits
Status and Reason; everything else is agent-written. The layout and pure logic live in
sheet_schema.py.

Two ways to authenticate (config.choose_auth picks one per profile):
  oauth            the person signs in to Google once; `connect` creates their sheet in their own
                   Drive (drive.file scope) and remembers it in profiles/<name>/agent-state.json.
  service_account  an existing sheet shared with a service-account key (advanced; see
                   docs/service-account-setup.md). The original behaviour, unchanged.
`connect` is idempotent and the only command here that may open a browser.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from config import (
    ProfileError,
    choose_auth,
    credentials_path,
    load_config,
    read_state,
    write_state,
)
from google_auth import get_credentials
from sheet_schema import (
    JOBS_TAB,
    LEGACY_STATUS_SNAPSHOT_KEY,
    META_TAB,
    PREFERENCES_SEED_ROWS,
    PREFERENCES_TAB,
    RULES_TAB,
    SCORING_HEADERS,
    STATUS_SYNC_TAB,
    STATUS_VALUES,
    TAB_HEADERS,
    default_tab_to_adopt,
    find_changed_status_rows,
    job_to_row,
    needs_scoring,
    sheet_title,
    snapshot_entry,
)
from util import is_repost, load_json, now_iso, run_cli, save_json, unwrap_jobs

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
SHEET_URL = "https://docs.google.com/spreadsheets/d/{sheet_id}/edit"
REPOST_WINDOW_DAYS = 30
AGENT_RULE_AUTHOR = "AI"  # rules with any other "Added by" were written by a human


class SheetClient:
    def __init__(self, profile: str):
        self.profile = profile
        self.config = load_config(profile)
        self._spreadsheet = None
        self._gc = None

    @property
    def auth_mode(self) -> str:
        return choose_auth(self.config, credentials_path(self.config).exists())

    def _gspread_client(self, interactive: bool = False):
        """The gspread client for this profile's auth mode. Only `interactive` may open a
        browser for Google sign-in."""
        if self._gc is None:
            import gspread

            if self.auth_mode == "service_account":
                from google.oauth2.service_account import Credentials

                key_path = credentials_path(self.config)
                if not key_path.exists():
                    raise ProfileError(
                        f"Missing {key_path}. Create a Google service account, download its "
                        f"JSON key there, and share the sheet with its client_email as Editor. "
                        f"Step-by-step: docs/service-account-setup.md"
                    )
                creds = Credentials.from_service_account_file(str(key_path), scopes=SCOPES)
            else:
                if self.config["auth"] == "auto" and self.config.get("sheet_from_yaml"):
                    raise ProfileError(
                        "config.yaml has a `sheet:` address, but without a Google service-account "
                        "key the app can only open sheets it created itself. Either delete the "
                        "`sheet:` line (setup will then create a new sheet for you), or add a "
                        "key: docs/service-account-setup.md"
                    )
                creds = get_credentials(self.profile, interactive=interactive)
            self._gc = gspread.authorize(creds)
        return self._gc

    @property
    def spreadsheet(self):
        if self._spreadsheet is None:
            if not self.config.get("sheet_id"):
                raise ProfileError(
                    "No sheet yet: run "
                    f"`uv run python scripts/sheet.py --profile {self.profile} connect`"
                )
            gc = self._gspread_client()
            self._spreadsheet = gc.open_by_key(self.config["sheet_id"])
        return self._spreadsheet

    # ---- Connecting ---------------------------------------------------------

    def connect(self, interactive: bool = True) -> str:
        """Make sure this profile has a working sheet with all its tabs; return its URL.

        Safe to run any number of times. With OAuth it signs in if needed, then reuses the
        sheet already known, or adopts one it created earlier (a crash between creating the
        sheet and saving its ID would otherwise leave an orphan), or creates a new one."""
        gc = self._gspread_client(interactive)
        fresh = False
        if not self.config.get("sheet_id"):
            if self.auth_mode == "service_account":
                raise ProfileError(
                    "`auth: service_account` needs your sheet's address as `sheet:` in "
                    "config.yaml (docs/service-account-setup.md)."
                )
            self._adopt_or_create_sheet(gc)
            fresh = True
        if self.auth_mode == "oauth":
            self._adopt_default_tab()
        self.init_tabs()
        if fresh:
            self.test_write()
        return SHEET_URL.format(sheet_id=self.config["sheet_id"])

    def _adopt_or_create_sheet(self, gc) -> None:
        """Find or create this profile's sheet and remember it."""
        title = sheet_title(self.profile)
        own = self._find_own_sheet(gc, title)
        if own is not None:
            self._spreadsheet = own
        else:
            self._spreadsheet = gc.create(title)
        sheet_id = self._spreadsheet.id
        # Record the ID before doing anything else with the sheet. Merge, so nothing else
        # kept in the state file is lost, and keep the original creation time if we have it.
        previous = read_state(self.profile)
        write_state(
            self.profile,
            {
                **previous,
                "sheet_id": sheet_id,
                "sheet_url": SHEET_URL.format(sheet_id=sheet_id),
                "sheet_title": title,
                "created_at": previous.get("created_at") or now_iso(),
                "auth": "oauth",
            },
        )
        self.config["sheet_id"] = sheet_id

    def _find_own_sheet(self, gc, title: str):
        """A sheet this app made earlier (its state file was lost), or None.

        Same title is not enough: Drive's listing includes trashed files and can include an
        unrelated sheet someone happened to name the same. A candidate has to be out of the
        trash and look like ours (has our Jobs tab, or is still the untouched default tab of
        a sheet whose setup was interrupted). The most recently modified such sheet wins."""
        matches = sorted(
            gc.list_spreadsheet_files(title=title),
            key=lambda f: f.get("modifiedTime", ""),
            reverse=True,
        )
        for match in matches:
            if self._is_trashed(gc, match["id"]):
                continue
            candidate = gc.open_by_key(match["id"])
            worksheets = candidate.worksheets()
            first_empty = len(worksheets) == 1 and not any(
                cell for row in worksheets[0].get_all_values() for cell in row
            )
            if JOBS_TAB in {ws.title for ws in worksheets} or default_tab_to_adopt(
                [ws.title for ws in worksheets], first_empty
            ):
                return candidate
        return None

    @staticmethod
    def _is_trashed(gc, file_id: str) -> bool:
        """Whether Drive has this file in the trash (False if that can't be determined)."""
        try:
            response = gc.http_client.request(
                "get",
                f"https://www.googleapis.com/drive/v3/files/{file_id}",
                params={"fields": "trashed"},
            )
            return bool(response.json().get("trashed"))
        except Exception:  # a failed lookup must not stop setup; treat as not trashed
            return False

    def _adopt_default_tab(self) -> None:
        """Turn Google's empty default tab into Jobs, so no stray empty tab is left behind."""
        sheet = self.spreadsheet
        worksheets = sheet.worksheets()
        if len(worksheets) != 1:
            return
        first = worksheets[0]
        empty = not any(cell for row in first.get_all_values() for cell in row)
        if default_tab_to_adopt([ws.title for ws in worksheets], empty):
            first.update_title(JOBS_TAB)
            first.append_row(TAB_HEADERS[JOBS_TAB])
            first.freeze(rows=1)

    # ---- Tabs ---------------------------------------------------------------

    def _open_or_create(self, title: str):
        """Returns (worksheet, created). A new tab gets its header row and a frozen top row."""
        import gspread

        try:
            return self.spreadsheet.worksheet(title), False
        except gspread.WorksheetNotFound:
            headers = TAB_HEADERS[title]
            ws = self.spreadsheet.add_worksheet(title=title, rows=1000, cols=len(headers))
            ws.append_row(headers)
            ws.freeze(rows=1)
            return ws, True

    def _worksheet(self, title: str):
        return self._open_or_create(title)[0]

    def init_tabs(self) -> None:
        """Create any missing tab and header, seed Preferences, apply the Status dropdown.
        Safe to re-run: it only ever adds, never overwrites what the person has entered."""
        for title in (JOBS_TAB, RULES_TAB, META_TAB):
            self._worksheet(title)
        self._ensure_missing_headers(JOBS_TAB)
        self._ensure_preferences_seeded()
        self._status_sync_worksheet()
        self.apply_status_validation()

    def _ensure_missing_headers(self, title: str) -> None:
        """Append headers an older sheet lacks (e.g. after a release adds a column)."""
        import gspread

        ws = self._worksheet(title)
        existing = ws.row_values(1)
        missing = [h for h in TAB_HEADERS[title] if h not in existing]
        if missing:
            ws.update_cells(
                [gspread.Cell(1, len(existing) + i + 1, h) for i, h in enumerate(missing)]
            )

    def _ensure_preferences_seeded(self) -> None:
        """Create Preferences with example rows if absent. Never touches an existing tab;
        the person's filled-in values must survive re-running setup."""
        ws, created = self._open_or_create(PREFERENCES_TAB)
        if created:
            ws.append_rows([list(row) for row in PREFERENCES_SEED_ROWS])

    def _status_sync_worksheet(self):
        """AI_StatusSync, created on first use and seeded from the legacy single-cell
        snapshot (older sheets) so no feedback already read is read again."""
        ws, created = self._open_or_create(STATUS_SYNC_TAB)
        if not created:
            return ws
        raw = self.get_meta().get(LEGACY_STATUS_SNAPSHOT_KEY, "")
        try:
            legacy = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            legacy = {}
        rows = [
            [job_id, *entry.split("||", 1)] for job_id, entry in legacy.items() if "||" in entry
        ]
        if rows:
            ws.append_rows(rows)
        return ws

    def apply_status_validation(self) -> None:
        """Put the Status dropdown on the Jobs Status column, and only there.

        Clears validation over the whole data range first, so a dropdown left on whatever
        column Status used to occupy before a column reorder can't linger."""
        from gspread.utils import ValidationConditionType, rowcol_to_a1

        ws = self._worksheet(JOBS_TAB)
        headers = ws.row_values(1)
        self.spreadsheet.batch_update(
            {
                "requests": [
                    {
                        "setDataValidation": {
                            "range": {
                                "sheetId": ws.id,
                                "startRowIndex": 1,
                                "startColumnIndex": 0,
                                "endColumnIndex": len(headers),
                            }
                        }
                    }
                ]
            }
        )
        letter = rowcol_to_a1(1, headers.index("Status") + 1).rstrip("0123456789")
        ws.add_validation(
            f"{letter}2:{letter}",
            ValidationConditionType.one_of_list,
            STATUS_VALUES,
            showCustomUi=True,
        )

    def test_write(self) -> None:
        self._worksheet(META_TAB).append_row(["test_write", now_iso()])

    # ---- Meta ---------------------------------------------------------------

    def get_meta(self) -> dict[str, str]:
        rows = self._worksheet(META_TAB).get_all_records()
        return {str(r["Key"]): str(r["Value"]) for r in rows if r.get("Key")}

    def update_meta(self, updates: dict[str, str]) -> None:
        ws = self._worksheet(META_TAB)
        row_of_key = {
            row[0]: idx for idx, row in enumerate(ws.get_all_values()[1:], start=2) if row
        }
        changes, new_rows = [], []
        for key, value in updates.items():
            if key in row_of_key:
                rng = f"A{row_of_key[key]}:B{row_of_key[key]}"
                changes.append({"range": rng, "values": [[key, value]]})
            else:
                new_rows.append([key, value])
        if changes:
            ws.batch_update(changes)
        if new_rows:
            ws.append_rows(new_rows)

    # ---- Rules and preferences ---------------------------------------------

    def get_rules(self) -> list[dict[str, Any]]:
        return [
            {
                "rule": r.get("Rule", ""),
                "type": r.get("Type", ""),
                "keywords": r.get("Keywords", ""),
                "based_on": r.get("Based on", ""),
                "added_by": r.get("Added by", ""),
                "active": r.get("Active", ""),
            }
            for r in self._worksheet(RULES_TAB).get_all_records()
        ]

    def append_rules(self, rules: list[dict[str, Any]]) -> None:
        """Add learned rules ({rule, type, keywords, based_on}); marked as agent-written."""
        rows = [
            [
                r["rule"],
                r["type"],
                r.get("keywords", ""),
                r.get("based_on", ""),
                r.get("added_by", AGENT_RULE_AUTHOR),
                r.get("active", "Yes"),
            ]
            for r in rules
        ]
        if rows:
            self._worksheet(RULES_TAB).append_rows(rows)

    def get_preferences(self) -> list[dict[str, str]]:
        """Only rows the person filled in. A blank Value means no preference was stated."""
        return [
            {"criterion": r.get("Criterion", ""), "value": r.get("Value", "")}
            for r in self._worksheet(PREFERENCES_TAB).get_all_records()
            if str(r.get("Value", "")).strip()
        ]

    # ---- Dedupe -------------------------------------------------------------

    def dedupe(self, jobs: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        """Split fetched jobs into genuinely new ones and reposts (same company and title as
        a recent posting). Jobs whose ID is already in the sheet are dropped."""
        records = self._worksheet(JOBS_TAB).get_all_records()
        known_ids = {str(r.get("Job ID", "")).strip() for r in records} - {""}
        recent = [
            {
                "title": r.get("Job title", ""),
                "company": r.get("Company", ""),
                "posted_date": r.get("Posted") or None,
            }
            for r in records
        ]

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
            else:
                new_jobs.append(job)
        return {"new": new_jobs, "reposts": reposts}

    # ---- Feedback (Status/Reason) for the learning step -----------------------

    def _status_snapshot(self) -> dict[str, str]:
        records = self._status_sync_worksheet().get_all_records()
        return {
            str(r["Job ID"]): snapshot_entry(str(r.get("Status", "")), str(r.get("Reason", "")))
            for r in records
            if r.get("Job ID")
        }

    def get_changed_status_rows(self) -> list[dict[str, str]]:
        records = self._worksheet(JOBS_TAB).get_all_records()
        return find_changed_status_rows(records, self._status_snapshot())

    def mark_status_synced(self, job_ids: list[str]) -> None:
        """After the learning step reads feedback, remember the Status+Reason it saw so the
        same rows aren't treated as changed again. Kept in AI_StatusSync, not as a second
        status-shaped column in Jobs, so the person only ever sees one Status column."""
        wanted = {str(j) for j in job_ids}
        current = {
            str(r.get("Job ID", "")): [
                str(r.get("Status", "")).strip(),
                str(r.get("Reason", "")).strip(),
            ]
            for r in self._worksheet(JOBS_TAB).get_all_records()
            if str(r.get("Job ID", "")) in wanted
        }
        ws = self._status_sync_worksheet()
        row_of_job = {
            row[0]: idx for idx, row in enumerate(ws.get_all_values()[1:], start=2) if row
        }

        changes, new_rows = [], []
        for job_id, (status, reason) in current.items():
            if job_id in row_of_job:
                rng = f"A{row_of_job[job_id]}:C{row_of_job[job_id]}"
                changes.append({"range": rng, "values": [[job_id, status, reason]]})
            else:
                new_rows.append([job_id, status, reason])
        if changes:
            ws.batch_update(changes)
        if new_rows:
            ws.append_rows(new_rows)

    # ---- Writing job rows -----------------------------------------------------

    def append_jobs(self, jobs: list[dict[str, Any]]) -> int:
        """Add jobs to the Jobs tab, scored, low-scored or blocked alike; only Why/Gaps/Score
        differ. Columns are placed by the sheet's own header names, not by position.
        Returns how many rows were actually added (jobs already present are skipped)."""
        ws = self._worksheet(JOBS_TAB)
        headers = ws.row_values(1)
        today = now_iso()[:10]
        # Skip jobs already in the sheet, so retrying a write never creates duplicate rows.
        id_col = headers.index("Job ID")
        known = {row[id_col] for row in ws.get_all_values()[1:] if len(row) > id_col}
        fresh = [job for job in jobs if str(job.get("job_id", "")) not in known]
        rows = [job_to_row(job, headers, today) for job in fresh]
        if rows:
            ws.append_rows(rows)
        return len(rows)

    def update_jobs(self, jobs: list[dict[str, Any]]) -> int:
        """Fill in Score/Why/Gaps/Flags/Role type on rows already in the sheet (matched by
        Job ID). Never touches Status/Reason and never appends.

        Second half of "write early, fill in later": raw listings are appended as Pending right
        after dedupe, so a crash during slow description-fetching or scoring never loses what
        was found; this then updates those same rows in place. Unknown Job IDs are skipped.
        Returns how many rows were updated.

        Cell addresses come from a snapshot of the sheet, so if the person sorted or deleted
        rows in the instant between reading and writing, scores could land on the wrong rows.
        After writing, the Job ID column is re-read and the update redone once if it moved."""
        from gspread.utils import rowcol_to_a1

        ws = self._worksheet(JOBS_TAB)
        for attempt in (1, 2):
            rows = ws.get_all_values()
            headers = rows[0]
            id_col = headers.index("Job ID")
            row_of_job = {
                row[id_col]: idx for idx, row in enumerate(rows[1:], start=2) if len(row) > id_col
            }
            scoring_cols = [(h, headers.index(h)) for h in SCORING_HEADERS if h in headers]

            updates, updated = [], 0
            for job in jobs:
                row_idx = row_of_job.get(str(job.get("job_id", "")))
                if row_idx is None:
                    continue
                for header, col in scoring_cols:
                    value = job_to_row(job, [header], "")[0]
                    updates.append({"range": rowcol_to_a1(row_idx, col + 1), "values": [[value]]})
                updated += 1
            if not updates:
                return 0
            ws.batch_update(updates)

            after = ws.col_values(id_col + 1)
            moved = any(
                idx - 1 >= len(after) or after[idx - 1] != job_id
                for job_id, idx in row_of_job.items()
                if job_id in {str(j.get("job_id", "")) for j in jobs}
            )
            if not moved or attempt == 2:
                return updated
        return updated

    def get_pending_jobs(self) -> list[dict[str, Any]]:
        """Rows still owed a score (Pending or failed), as job dicts ready for the fetch step."""
        return [
            {
                "job_id": str(r.get("Job ID", "")),
                "title": r.get("Job title", ""),
                "company": r.get("Company", ""),
                "location": r.get("Location", ""),
                "posted_date": r.get("Posted") or None,
                "link": r.get("Link", ""),
            }
            for r in self._worksheet(JOBS_TAB).get_all_records()
            if str(r.get("Job ID", "")).strip()
            and needs_scoring(r.get("Score", ""), str(r.get("Why it matches", "")))
        ]


# ---- CLI -------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--profile", required=True)
    sub = parser.add_subparsers(dest="command", required=True)

    for name in ("connect", "init-tabs", "test-write", "meta", "apply-status-validation"):
        sub.add_parser(name)

    for name in ("rules", "preferences", "changed-status", "pending"):
        sub.add_parser(name).add_argument("--out", type=Path, required=True)

    dedupe_p = sub.add_parser("dedupe")
    dedupe_p.add_argument("--in", dest="in_path", type=Path, required=True)
    dedupe_p.add_argument("--out", type=Path, required=True)

    for name in ("add-rules", "append-jobs", "update-jobs"):
        sub.add_parser(name).add_argument("--in", dest="in_path", type=Path, required=True)

    sub.add_parser("update-meta").add_argument(
        "--set", action="append", default=[], metavar="KEY=VALUE"
    )
    sub.add_parser("mark-status-synced").add_argument("--job-ids", nargs="+", required=True)
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    client = SheetClient(args.profile)
    command = args.command

    if command == "connect":
        print(f"SHEET_URL: {client.connect(interactive=True)}")
    elif command == "init-tabs":
        client.init_tabs()
        print("Tabs ready: Jobs, Preferences, AI_Rules, AI_MetaData, AI_StatusSync")
    elif command == "test-write":
        client.test_write()
        print("Wrote a test row to AI_MetaData.")
    elif command == "apply-status-validation":
        client.apply_status_validation()
        print("Status dropdown applied to Jobs.")
    elif command == "meta":
        print(json.dumps(client.get_meta(), indent=2))
    elif command == "rules":
        save_json(args.out, client.get_rules())
        print(f"Wrote rules to {args.out}")
    elif command == "add-rules":
        rules = load_json(args.in_path)
        client.append_rules(rules)
        print(f"Added {len(rules)} rules to AI_Rules")
    elif command == "preferences":
        save_json(args.out, client.get_preferences())
        print(f"Wrote preferences to {args.out}")
    elif command == "changed-status":
        save_json(args.out, client.get_changed_status_rows())
        print(f"Wrote changed-status rows to {args.out}")
    elif command == "pending":
        pending = client.get_pending_jobs()
        save_json(args.out, pending)
        print(f"{len(pending)} jobs still waiting for a score; wrote {args.out}")
    elif command == "mark-status-synced":
        client.mark_status_synced(args.job_ids)
        print(f"Synced status for {len(args.job_ids)} jobs")
    elif command == "dedupe":
        result = client.dedupe(unwrap_jobs(load_json(args.in_path)))
        save_json(args.out, result)
        print(f"{len(result['new'])} new, {len(result['reposts'])} reposts skipped")
    elif command == "append-jobs":
        jobs = unwrap_jobs(load_json(args.in_path))
        added = client.append_jobs(jobs)
        print(f"Appended {added} rows to Jobs ({len(jobs) - added} already present)")
    elif command == "update-jobs":
        jobs = unwrap_jobs(load_json(args.in_path))
        updated = client.update_jobs(jobs)
        print(f"Updated {updated} of {len(jobs)} rows in Jobs (matched by Job ID)")
    elif command == "update-meta":
        updates = dict(item.split("=", 1) for item in args.set)
        client.update_meta(updates)
        print(f"Updated Meta: {updates}")


if __name__ == "__main__":
    run_cli(main)
