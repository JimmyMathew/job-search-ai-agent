"""All reads and writes of one profile's Excel job list.

CLI (each subcommand prints a short result, or reads/writes JSON files):
    python scripts/sheet.py --profile <name> init [--fresh]  # create it, or add what is missing
    python scripts/sheet.py --profile <name> ready           # pre-flight: loads, closed, backed up
    python scripts/sheet.py --profile <name> where
    python scripts/sheet.py --profile <name> open
    python scripts/sheet.py --profile <name> clear-lock
    python scripts/sheet.py --profile <name> restore-backup
    python scripts/sheet.py --profile <name> meta
    python scripts/sheet.py --profile <name> rules          --out rules.json
    python scripts/sheet.py --profile <name> add-rules      --in new_rules.json
    python scripts/sheet.py --profile <name> preferences    --out prefs.json
    python scripts/sheet.py --profile <name> changed-status --out changed.json
    python scripts/sheet.py --profile <name> pending        --out pending.json
    python scripts/sheet.py --profile <name> mark-status-synced --in changed.json
    python scripts/sheet.py --profile <name> dedupe         --in raw.json --out deduped.json
    python scripts/sheet.py --profile <name> append-jobs    --in jobs.json
    python scripts/sheet.py --profile <name> update-jobs    --in results.json
    python scripts/sheet.py --profile <name> update-meta    --set last_run_iso=... --set ...
    python scripts/sheet.py --profile <name> apply-status-validation

The job list is an Excel file on the person's computer (see workbook.py for how it is read and
written safely, and config.workbook_path for where it lives). One tab holds every job the
agent has ever seen for this profile: scored high, scored low, or blocked by a rule. Nothing is
hidden in a second tab. The person only edits Status and Reason; everything else is
agent-written. The layout and pure logic live in sheet_schema.py.

Every command that writes does one load and one save. If the file is open in Excel it stops
before changing anything and prints a message starting `WORKBOOK_OPEN:`; once the person has
closed it, running the same command again is safe.
"""

from __future__ import annotations

import argparse
import contextlib
import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from config import (
    LegacySheet,
    UserFacingError,
    backups_dir,
    find_legacy_sheet,
    load_config,
    read_state,
    sheet_command,
    workbook_path,
    write_state,
)
from sheet_schema import (
    COLUMN_WIDTHS,
    HIDDEN_TABS,
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
    find_changed_status_rows,
    job_to_row,
    needs_scoring,
    snapshot_entry,
)
from util import is_repost, load_json, now_iso, run_cli, save_json, unwrap_jobs
from workbook import TabSpec, Workbook, WorkbookError, WorkbookMissing

REPOST_WINDOW_DAYS = 30
AGENT_RULE_AUTHOR = "AI"  # rules with any other "Added by" were written by a human
WORKBOOK_LINE = "WORKBOOK: {path}"


def _tab_spec(name: str) -> TabSpec:
    headers = TAB_HEADERS[name]
    return TabSpec(
        name=name,
        headers=headers,
        seed_rows=PREFERENCES_SEED_ROWS if name == PREFERENCES_TAB else (),
        hidden=name in HIDDEN_TABS,
        widths={header: COLUMN_WIDTHS[header] for header in headers if header in COLUMN_WIDTHS},
    )


TAB_SPECS = [_tab_spec(name) for name in TAB_HEADERS]


class OldGoogleListError(WorkbookError):
    """The profile used a Google sheet before, so a new empty list must not take its place."""


class SheetClient:
    def __init__(self, profile: str):
        self.profile = profile
        self.config = load_config(profile)
        self.command = sheet_command(profile)
        self.workbook = Workbook(
            workbook_path(profile, self.config), backups_dir(profile), command=self.command
        )

    # ---- Finding and creating the file ---------------------------------------------

    def _recorded_workbook(self) -> str | None:
        return read_state(self.profile).get("workbook")

    def _legacy_sheet(self) -> LegacySheet | None:
        return find_legacy_sheet(self.config, read_state(self.profile))

    def _legacy_message(self, legacy: LegacySheet) -> str:
        where = legacy.url or f'a sheet called "Job Search - {self.profile}" in your Google Drive'
        return (
            f"This profile kept its job list in a Google sheet before: {where}\n"
            "Your history of jobs you have already seen is in it. Do this BEFORE anything "
            "else: open it, choose File > Download > Microsoft Excel (.xlsx), save the file as "
            f"{self.workbook.path} and then run `{self.command} init`.\n"
            "Starting a new, empty list instead means you will see every job again. Only if "
            f"that is really what the person wants, run `{self.command} init --fresh`."
        )

    def _missing_message(self) -> str:
        legacy = self._legacy_sheet()
        if not self._recorded_workbook():
            if legacy:
                return self._legacy_message(legacy)
            return (
                f"There is no job list yet at {self.workbook.path}. "
                f"Create it with: {self.command} init"
            )
        return (
            f"Your job list file is missing: {self.workbook.path}\n"
            "It was created earlier, so it should be there. If you moved it, move it back (or "
            f"set `workbook:` in profiles/{self.profile}/config.yaml to where it is now). "
            f"Automatic copies are kept in {self.workbook.backups_dir}: put the newest back "
            f"with `{self.command} restore-backup`.\n"
            f"Only if you want a brand-new, empty list, delete profiles/{self.profile}/"
            "agent-state.json and run `init`; but then the agent forgets every job it has "
            "already shown you, and you will see them again."
        )

    @contextlib.contextmanager
    def _session(self) -> Iterator[None]:
        """One load and one save for everything inside, and a plain error if the file is gone."""
        if not self.workbook.exists():
            raise WorkbookMissing(self._missing_message())
        with self.workbook.session():
            yield

    def init(self, fresh: bool = False) -> Path:
        """Create the job list, or add whatever an existing one lacks. Safe to repeat: it only
        ever adds, so a person's Preferences, rules and jobs are never overwritten.

        It refuses to start an empty list where a Google sheet used to be (unless `fresh`),
        because the person's exported sheet is meant to go at this path first, and an empty
        list would silently throw their job history away."""
        if not self.workbook.exists():
            if self._recorded_workbook():
                raise WorkbookMissing(self._missing_message())
            legacy = self._legacy_sheet()
            if legacy and not fresh:
                raise OldGoogleListError(self._legacy_message(legacy))
        with self.workbook.session(create=True):
            self.workbook.ensure_tabs(TAB_SPECS)
            self.workbook.set_list_validation(JOBS_TAB, "Status", STATUS_VALUES)
            self._migrate_legacy_status_snapshot()
        state = read_state(self.profile)
        write_state(
            self.profile,
            {
                **state,
                "workbook": str(self.workbook.path),
                "created_at": state.get("created_at") or now_iso(),
            },
        )
        return self.workbook.path

    def _migrate_legacy_status_snapshot(self) -> None:
        """A list exported from the Google version keeps what the learning step had read as one
        JSON blob in AI_MetaData. Carry it into AI_StatusSync (once, while that tab is empty)
        so old feedback isn't learned from again. Call inside a session."""
        if self.workbook.get_records(STATUS_SYNC_TAB):
            return
        blob = {r.get("Key"): r.get("Value", "") for r in self.workbook.get_records(META_TAB)}
        try:
            legacy = json.loads(blob.get(LEGACY_STATUS_SNAPSHOT_KEY) or "{}")
        except ValueError:
            return
        if not isinstance(legacy, dict):
            return
        rows = [
            {"Job ID": job_id, "Status": status, "Reason": reason}
            for job_id, entry in legacy.items()
            if "||" in str(entry)
            for status, reason in [str(entry).split("||", 1)]
        ]
        self.workbook.upsert_by_key(STATUS_SYNC_TAB, "Job ID", rows, append_missing=True)

    def ready(self) -> Path:
        """Pre-flight for a run: the file loads, is not open in Excel, and one backup is
        taken. The load comes first so a damaged file never replaces a good backup."""
        if not self.workbook.exists():
            raise WorkbookMissing(self._missing_message())
        self.workbook.verify()
        self.workbook.require_closed()
        self.workbook.backup()
        return self.workbook.path

    def open_in_app(self) -> None:
        """Show the job list to the person in their spreadsheet program."""
        if not self.workbook.exists():
            raise WorkbookMissing(self._missing_message())
        self.workbook.open_in_app()

    def apply_status_validation(self) -> None:
        """Put the Status dropdown on the Jobs Status column (replacing one already on that
        column; dropdowns the person added elsewhere are kept)."""
        with self._session():
            self.workbook.set_list_validation(JOBS_TAB, "Status", STATUS_VALUES)

    # ---- Meta ---------------------------------------------------------------------

    def get_meta(self) -> dict[str, str]:
        with self._session():
            rows = self.workbook.get_records(META_TAB)
        return {r["Key"]: r.get("Value", "") for r in rows if r.get("Key")}

    def update_meta(self, updates: dict[str, str]) -> None:
        with self._session():
            rows = [{"Key": key, "Value": value} for key, value in updates.items()]
            self.workbook.upsert_by_key(META_TAB, "Key", rows, append_missing=True)

    # ---- Rules and preferences ------------------------------------------------------

    def get_rules(self) -> list[dict[str, Any]]:
        with self._session():
            records = self.workbook.get_records(RULES_TAB)
        return [
            {
                "rule": r.get("Rule", ""),
                "type": r.get("Type", ""),
                "keywords": r.get("Keywords", ""),
                "based_on": r.get("Based on", ""),
                "added_by": r.get("Added by", ""),
                "active": r.get("Active", ""),
            }
            for r in records
        ]

    def append_rules(self, rules: list[dict[str, Any]]) -> int:
        """Add learned rules ({rule, type, keywords, based_on}); marked as agent-written.
        A rule whose text is already listed is skipped, so adding the same file twice is
        harmless. Returns how many were added."""
        rows = [
            {
                "Rule": r["rule"],
                "Type": r["type"],
                "Keywords": r.get("keywords", ""),
                "Based on": r.get("based_on", ""),
                "Added by": r.get("added_by", AGENT_RULE_AUTHOR),
                "Active": r.get("active", "Yes"),
            }
            for r in _checked(rules, "rule", ("rule", "type"))
        ]
        with self._session():
            return self.workbook.append_rows(RULES_TAB, rows, unique_header="Rule")

    def get_preferences(self) -> list[dict[str, str]]:
        """Only rows the person filled in. A blank Value means no preference was stated."""
        with self._session():
            records = self.workbook.get_records(PREFERENCES_TAB)
        return [
            {"criterion": r.get("Criterion", ""), "value": r["Value"]}
            for r in records
            if r.get("Value", "").strip()
        ]

    # ---- Dedupe -------------------------------------------------------------------

    def dedupe(self, jobs: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        """Split fetched jobs into genuinely new ones and reposts (same company and title as
        a recent posting). Jobs whose ID is already in the job list are dropped."""
        with self._session():
            records = self.workbook.get_records(JOBS_TAB)
        known_ids = {r.get("Job ID", "").strip() for r in records} - {""}
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

    # ---- Feedback (Status/Reason) for the learning step -----------------------------

    def get_changed_status_rows(self) -> list[dict[str, str]]:
        with self._session():
            records = self.workbook.get_records(JOBS_TAB)
            synced = self.workbook.get_records(STATUS_SYNC_TAB)
        snapshot = {
            r["Job ID"]: snapshot_entry(r.get("Status", ""), r.get("Reason", ""))
            for r in synced
            if r.get("Job ID")
        }
        return find_changed_status_rows(records, snapshot)

    def mark_status_synced(self, changed: list[dict[str, str]]) -> None:
        """After the learning step reads feedback, remember the Status+Reason it was shown so
        the same rows aren't treated as changed again. `changed` is what
        get_changed_status_rows reported: those exact values are recorded, not whatever the
        cells say now, so an edit the person made in between is still reported next time.
        Kept in the hidden AI_StatusSync tab, not as a second status-shaped column in Jobs,
        so the person only ever sees one Status."""
        rows = [
            {"Job ID": str(r["job_id"]), "Status": r["status"], "Reason": r.get("reason", "")}
            for r in _checked(changed, "changed row", ("job_id", "status"))
        ]
        with self._session():
            self.workbook.upsert_by_key(STATUS_SYNC_TAB, "Job ID", rows, append_missing=True)

    # ---- Writing job rows ---------------------------------------------------------------

    def append_jobs(self, jobs: list[dict[str, Any]]) -> int:
        """Add jobs to the Jobs tab, scored, low-scored or blocked alike; only Why/Gaps/Score
        differ. Columns are placed by the file's own header names, not by position.
        Returns how many rows were actually added (jobs already present are skipped, so
        retrying a write never creates duplicate rows)."""
        today = now_iso()[:10]
        with self._session():
            headers = self.workbook.get_headers(JOBS_TAB)
            rows = [_job_row(job, headers, today) for job in jobs]
            return self.workbook.append_rows(JOBS_TAB, rows, unique_header="Job ID")

    def update_jobs(self, jobs: list[dict[str, Any]]) -> int:
        """Fill in Score/Why/Gaps/Flags/Role type on rows already in the Jobs tab (matched by
        Job ID). Never touches Status/Reason (the person's) and never appends. Unknown Job IDs
        are skipped. Returns how many rows were updated.

        Second half of "write early, fill in later": raw listings are appended as Pending right
        after dedupe, so a crash during slow description-fetching or scoring never loses what
        was found; this then updates those same rows in place, matched by Job ID when the
        write happens, so a list the person has sorted still updates the right rows."""
        with self._session():
            present = set(self.workbook.get_headers(JOBS_TAB))
            scoring = [header for header in SCORING_HEADERS if header in present]
            rows = [_job_row(job, scoring, "") for job in jobs]
            return self.workbook.upsert_by_key(JOBS_TAB, "Job ID", rows, append_missing=False)

    def get_pending_jobs(self) -> list[dict[str, Any]]:
        """Rows still owed a score (Pending or failed), as job dicts ready for the fetch step."""
        with self._session():
            records = self.workbook.get_records(JOBS_TAB)
        return [
            {
                "job_id": r["Job ID"],
                "title": r.get("Job title", ""),
                "company": r.get("Company", ""),
                "location": r.get("Location", ""),
                "posted_date": r.get("Posted") or None,
                "link": r.get("Link", ""),
            }
            for r in records
            if r.get("Job ID", "").strip()
            and needs_scoring(r.get("Score", ""), r.get("Why it matches", ""))
        ]


def _checked(items: Any, kind: str, required: tuple[str, ...]) -> list[dict[str, Any]]:
    """A JSON list of dicts that each have the required fields, or a plain error saying which
    one doesn't (the file may have been written by hand or by Claude)."""
    if not isinstance(items, list):
        raise UserFacingError(f"The file must be a JSON list of {kind}s.")
    for number, item in enumerate(items, start=1):
        missing = [f for f in required if not isinstance(item, dict) or item.get(f) is None]
        if missing:
            raise UserFacingError(
                f"The {kind} #{number} is missing {', '.join(missing)}. Nothing was written."
            )
    return items


def _job_row(job: dict[str, Any], headers: list[str], today: str) -> dict[str, Any]:
    """A job as {header: value} for the given headers, plus its Job ID to match rows by. The
    Job ID is always text: as a number Excel would show a long ID as 3.9E+09."""
    job_id = str(job.get("job_id") or "").strip()
    if not job_id:
        raise UserFacingError(f"A job has no job_id, so it can't be written: {job}")
    row = dict(zip(headers, job_to_row(job, headers, today), strict=True))
    row["Job ID"] = job_id
    return row


# ---- CLI -------------------------------------------------------------------


Handler = Callable[[SheetClient, argparse.Namespace], None]


def _cmd_init(client: SheetClient, args: argparse.Namespace) -> None:
    print(WORKBOOK_LINE.format(path=client.init(fresh=args.fresh)))


def _cmd_ready(client: SheetClient, args: argparse.Namespace) -> None:
    print(WORKBOOK_LINE.format(path=client.ready()))


def _cmd_where(client: SheetClient, args: argparse.Namespace) -> None:
    print(WORKBOOK_LINE.format(path=client.workbook.path))


def _cmd_open(client: SheetClient, args: argparse.Namespace) -> None:
    client.open_in_app()
    print(f"Opening {client.workbook.path}")


def _cmd_clear_lock(client: SheetClient, args: argparse.Namespace) -> None:
    removed = client.workbook.clear_lock_files()
    print("\n".join(f"Removed {p}" for p in removed) if removed else "Nothing to clear.")


def _cmd_restore_backup(client: SheetClient, args: argparse.Namespace) -> None:
    restored, replaced = client.workbook.restore_latest_backup()
    print(f"Restored {client.workbook.path} from {restored.name}")
    if replaced:
        print(f"The file it replaced was kept as {replaced}")


def _cmd_apply_status_validation(client: SheetClient, args: argparse.Namespace) -> None:
    client.apply_status_validation()
    print("Status dropdown applied to Jobs.")


def _cmd_meta(client: SheetClient, args: argparse.Namespace) -> None:
    print(json.dumps(client.get_meta(), indent=2))


def _cmd_rules(client: SheetClient, args: argparse.Namespace) -> None:
    save_json(args.out, client.get_rules())
    print(f"Wrote rules to {args.out}")


def _cmd_add_rules(client: SheetClient, args: argparse.Namespace) -> None:
    rules = load_json(args.in_path)
    added = client.append_rules(rules)
    print(f"Added {added} rules to AI_Rules ({len(rules) - added} already there)")


def _cmd_preferences(client: SheetClient, args: argparse.Namespace) -> None:
    save_json(args.out, client.get_preferences())
    print(f"Wrote preferences to {args.out}")


def _cmd_changed_status(client: SheetClient, args: argparse.Namespace) -> None:
    save_json(args.out, client.get_changed_status_rows())
    print(f"Wrote changed-status rows to {args.out}")


def _cmd_pending(client: SheetClient, args: argparse.Namespace) -> None:
    pending = client.get_pending_jobs()
    save_json(args.out, pending)
    print(f"{len(pending)} jobs still waiting for a score; wrote {args.out}")


def _cmd_mark_status_synced(client: SheetClient, args: argparse.Namespace) -> None:
    changed = load_json(args.in_path)
    client.mark_status_synced(changed)
    print(f"Synced status for {len(changed)} jobs")


def _cmd_dedupe(client: SheetClient, args: argparse.Namespace) -> None:
    result = client.dedupe(unwrap_jobs(load_json(args.in_path)))
    save_json(args.out, result)
    print(f"{len(result['new'])} new, {len(result['reposts'])} reposts skipped")


def _cmd_append_jobs(client: SheetClient, args: argparse.Namespace) -> None:
    jobs = unwrap_jobs(load_json(args.in_path))
    added = client.append_jobs(jobs)
    print(f"Appended {added} rows to Jobs ({len(jobs) - added} already present)")


def _cmd_update_jobs(client: SheetClient, args: argparse.Namespace) -> None:
    jobs = unwrap_jobs(load_json(args.in_path))
    updated = client.update_jobs(jobs)
    print(f"Updated {updated} of {len(jobs)} rows in Jobs (matched by Job ID)")


def _parse_assignments(items: list[str]) -> dict[str, str]:
    bad = [item for item in items if "=" not in item]
    if bad:
        raise UserFacingError(f"--set needs KEY=VALUE, but got: {', '.join(bad)}")
    return dict(item.split("=", 1) for item in items)


def _cmd_update_meta(client: SheetClient, args: argparse.Namespace) -> None:
    updates = _parse_assignments(args.set)
    client.update_meta(updates)
    print(f"Updated Meta: {updates}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--profile", required=True)
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name: str, handler: Handler, aliases: tuple[str, ...] = ()) -> argparse.ArgumentParser:
        command = sub.add_parser(name, aliases=list(aliases))
        command.set_defaults(handler=handler)
        return command

    init_p = add("init", _cmd_init, aliases=("init-tabs",))
    init_p.add_argument(
        "--fresh",
        action="store_true",
        help="Start an empty list even though this profile used a Google sheet before",
    )
    add("ready", _cmd_ready)
    add("where", _cmd_where)
    add("open", _cmd_open)
    add("clear-lock", _cmd_clear_lock)
    add("restore-backup", _cmd_restore_backup)
    add("apply-status-validation", _cmd_apply_status_validation)
    add("meta", _cmd_meta)

    for name, handler in (
        ("rules", _cmd_rules),
        ("preferences", _cmd_preferences),
        ("changed-status", _cmd_changed_status),
        ("pending", _cmd_pending),
    ):
        add(name, handler).add_argument("--out", type=Path, required=True)

    dedupe_p = add("dedupe", _cmd_dedupe)
    dedupe_p.add_argument("--in", dest="in_path", type=Path, required=True)
    dedupe_p.add_argument("--out", type=Path, required=True)

    for name, handler in (
        ("add-rules", _cmd_add_rules),
        ("append-jobs", _cmd_append_jobs),
        ("update-jobs", _cmd_update_jobs),
    ):
        add(name, handler).add_argument("--in", dest="in_path", type=Path, required=True)

    add("update-meta", _cmd_update_meta).add_argument(
        "--set", action="append", default=[], metavar="KEY=VALUE"
    )
    add("mark-status-synced", _cmd_mark_status_synced).add_argument(
        "--in", dest="in_path", type=Path, required=True
    )
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    args.handler(SheetClient(args.profile), args)


if __name__ == "__main__":
    run_cli(main)
