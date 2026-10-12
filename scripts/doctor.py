"""Check that a profile is ready to run, and say exactly how to fix anything that isn't.

    python scripts/doctor.py --profile jane-doe
    python scripts/doctor.py --profile jane-doe --online    # also tries one real LinkedIn search

Prints OK / WARN / FIX per check. Exits 1 if anything needs fixing.
"""

from __future__ import annotations

import argparse
import importlib
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from config import (
    PROFILES_DIR,
    LegacySheet,
    ProfileError,
    backups_dir,
    find_legacy_sheet,
    find_resume,
    load_config,
    profile_dir,
    read_state,
    sheet_command,
    workbook_path,
)
from sheet_schema import TAB_HEADERS
from util import run_cli

if TYPE_CHECKING:
    from workbook import Workbook

REQUIRED_PACKAGES = {
    "jobspy": "python-jobspy",
    "openpyxl": "openpyxl",
    "yaml": "PyYAML",
}
# Folders that a sync program watches. Two copies of one file can end up as "conflict copies"
# there, and the agent would then be updating a different file from the one the person reads.
SYNCED_FOLDER_MARKERS = ("onedrive", "dropbox", "google drive", "googledrive")
# What follows the list's own name in a copy a sync program or "Save a copy" made (see
# find_conflict_copies). Only the conflict wording ignores case; a computer name is capitals.
CONFLICT_SUFFIX_PATTERNS = (
    r" \(\d+\)",
    r"(?i: - copy(?: \(\d+\))?)",
    r"(?i: \([^)]*conflict[^)]*\))",
    r"-(?=[A-Z0-9_-]*[A-Z])[A-Z0-9_-]+",
)

failures = 0


def report(level: str, message: str, fix: str = "") -> None:
    global failures
    if level == "FIX":
        failures += 1
    print(f"[{level:>4}] {message}")
    for line in fix.splitlines():
        print(f"         {line}")


def synced_folder_name(path: Path) -> str | None:
    """The folder in this path that a sync program watches (OneDrive, Dropbox, Google Drive),
    or None."""
    for part in path.parts:
        if any(marker in part.lower() for marker in SYNCED_FOLDER_MARKERS):
            return part
    return None


def find_conflict_copies(path: Path) -> list[Path]:
    """Other Excel files beside the job list that look like copies of it made by a sync program
    or "Save a copy": the same name plus " (1)", " - Copy", "(... conflicted copy ...)" or, for
    OneDrive, "-COMPUTERNAME" (capitals, which a profile name never is). Anything else that
    merely starts with the same letters, such as another profile's list, is not a copy."""
    if not path.parent.is_dir():
        return []
    suffixes = "|".join(CONFLICT_SUFFIX_PATTERNS)
    pattern = re.compile(re.escape(path.stem) + f"(?:{suffixes})" + re.escape(path.suffix))
    return sorted(
        sibling
        for sibling in path.parent.iterdir()
        if sibling.name != path.name and pattern.fullmatch(sibling.name)
    )


def check_workbook(profile: str, config: dict) -> None:
    """Report on the Excel job list: where it is, whether it opens, has every tab, is free to
    be written, and is somewhere that could cause two copies. A profile that used a Google
    sheet before is sent to its old data first."""
    from workbook import Workbook  # needs openpyxl, which main() has already checked for

    path = workbook_path(profile, config)
    command = sheet_command(profile)
    workbook = Workbook(path, backups_dir(profile), command=command)
    legacy = find_legacy_sheet(config, read_state(profile))
    if not workbook.exists():
        _report_missing_list(command, path, legacy)
        _warn_old_settings(config, delete_ok=False)
        return
    if not _check_workbook_opens(workbook, command):
        return

    report("OK", f"Job list: {path}")
    _warn_old_settings(config, delete_ok=True)
    _check_workbook_state(workbook, command)
    synced = synced_folder_name(path)
    if synced:
        report(
            "WARN",
            f"The job list is inside a synced folder ({synced})",
            "Syncing can create duplicate copies of the file. Keeping it in a normal folder\n"
            "is safer: set `workbook:` in config.yaml, or move the whole project.",
        )
    copies = find_conflict_copies(path)
    if copies:
        report(
            "WARN",
            "Found other files that look like copies of your job list",
            "\n".join(copy.name for copy in copies)
            + "\nThe agent only updates the one named above. Keep the right one, delete the rest.",
        )


def _report_missing_list(command: str, path: Path, legacy: LegacySheet | None) -> None:
    if legacy:
        where = legacy.url or "a sheet called 'Job Search - <your profile>' in your Google Drive"
        report(
            "FIX",
            f"This profile kept its job list in a Google sheet before: {where}",
            "Open it, File > Download > Microsoft Excel (.xlsx), save it as\n"
            f"{path}\n"
            f"then run: {command} init\n"
            "Do this BEFORE init: it keeps the history of jobs you have already seen.\n"
            f"Only if you want to start with an empty list: {command} init --fresh",
        )
    else:
        report("FIX", f"No job list yet (expected at {path})", f"Run: {command} init")


def _warn_old_settings(config: dict, delete_ok: bool) -> None:
    if not config.get("deprecated_keys"):
        return
    keys = ", ".join(f"`{key}`" for key in config["deprecated_keys"])
    advice = (
        "You can delete those lines."
        if delete_ok
        else "Keep them until your list is in place (see above)."
    )
    report(
        "WARN",
        f"config.yaml still has {keys}: no longer used, your results are in an Excel file",
        advice,
    )


def _check_workbook_opens(workbook: Workbook, command: str) -> bool:
    """Report a file that can't be read or lacks tabs. True when it loads and has every tab."""
    from workbook import WorkbookBusy, WorkbookCorrupt, WorkbookError

    try:
        existing = set(workbook.tab_names())
    except WorkbookCorrupt as exc:
        report(
            "FIX",
            "The job list can't be opened",
            f"{exc}\nOr run: {command} restore-backup",
        )
        return False
    except WorkbookBusy as exc:
        report("WARN", "The job list is being updated right now", str(exc))
        return False
    except WorkbookError as exc:
        report("FIX", "The job list can't be read", str(exc))
        return False
    missing = [tab for tab in TAB_HEADERS if tab not in existing]
    if missing:
        report(
            "FIX",
            f"The job list is missing tabs: {', '.join(missing)}",
            f"Run: {command} init   (it only adds)",
        )
        return False
    return True


def _check_workbook_state(workbook: Workbook, command: str) -> None:
    """Report what would stop a run from writing: read-only, open in Excel, leftovers."""
    if workbook.is_read_only():
        report(
            "FIX",
            "The job list is marked read-only",
            "Right-click the file, choose Properties, untick Read-only and click OK\n"
            "(on a Mac: Get Info, untick Locked).",
        )
    elif workbook.is_open():
        report(
            "WARN",
            "The job list is open in Excel (or another program)",
            "Save and close it before running /job-run, or the run can't write to it.",
        )
    leftovers = workbook.leftover_temp_files()
    if leftovers:
        report(
            "WARN",
            "Found half-written temporary files from an update that was cut off",
            "\n".join(leftover.name for leftover in leftovers)
            + f"\nYour job list is not affected. Run `{command} clear-lock` to delete them.",
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--online", action="store_true", help="Also run one real LinkedIn search")
    args = parser.parse_args()
    profile = args.profile

    if sys.version_info < (3, 10):  # noqa: UP036 (friendly message instead of a traceback)
        report(
            "FIX",
            f"Python {sys.version.split()[0]} is too old",
            "Install Python 3.10 or newer (or use uv; see README).",
        )
    else:
        report("OK", f"Python {sys.version.split()[0]}")

    missing = []
    for module, package in REQUIRED_PACKAGES.items():
        try:
            importlib.import_module(module)
        except ImportError:
            missing.append(package)
    if missing:
        report(
            "FIX",
            f"Missing packages: {', '.join(missing)}",
            "Run: uv sync   (or: pip install -r requirements.txt)",
        )
    else:
        report("OK", "Required packages installed")

    try:
        profile_dir(profile)
    except ProfileError as exc:
        report("FIX", f"Profile folder: {exc}")
        sys.exit(1)
    report("OK", f"Profile folder profiles/{profile}/")

    resume = find_resume(profile)
    if resume:
        report("OK", f"Resume found: {resume.resolve()}")
    else:
        report(
            "FIX",
            "No resume in your profile folder",
            f"Drop your resume (PDF, DOCX or TXT) into {(PROFILES_DIR / profile).resolve()}",
        )

    config = None
    if "PyYAML" in missing:
        report("WARN", "Skipping config check until packages are installed")
    else:
        try:
            config = load_config(profile)
            report("OK", f"config.yaml: {len(config['locations'])} location(s)")
        except ProfileError as exc:
            report("FIX", str(exc))

    if (profile_dir(profile) / "profile.md").exists():
        report("OK", "profile.md exists")
    else:
        report("WARN", "profile.md not written yet", f"In Claude Code, run: /job-setup {profile}")

    if config and "openpyxl" not in missing:
        check_workbook(profile, config)

    if args.online and not missing and config:
        try:
            from fetch import fetch_for_keyword

            keyword = (config["roles"] or ["manager"])[0]
            jobs = fetch_for_keyword(keyword, config["locations"][0], 72, 10)
            if jobs:
                report("OK", f"LinkedIn search works ({len(jobs)} results for {keyword!r})")
            else:
                report(
                    "WARN",
                    "LinkedIn search returned nothing",
                    "Could be a quiet search, or LinkedIn blocking this connection. "
                    "Try later, from home Wi-Fi without a VPN.",
                )
        except Exception as exc:
            report("FIX", f"LinkedIn search failed: {exc}")

    print()
    print(
        "Everything looks good." if not failures else f"{failures} thing(s) to fix (see FIX above)."
    )
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    run_cli(main)
