"""Profiles and their config.yaml: locating, loading, defaulting and validating.

A profile is a folder under profiles/ holding a person's resume, config.yaml and the
profile.md that /job-setup writes. Only `locations` is required in config.yaml; every other
setting has a default in CONFIG_DEFAULTS. The person's results live in one Excel workbook, by
default inside the profile folder (see workbook_path); the optional `workbook:` setting moves it.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sheet_schema import workbook_filename

REPO_ROOT = Path(__file__).resolve().parent.parent
PROFILES_DIR = REPO_ROOT / "profiles"
RUNS_DIR = REPO_ROOT / "runs"
STATE_FILE = "agent-state.json"
BACKUPS_DIRNAME = "backups"
WORKBOOK_SUFFIX = ".xlsx"

EXAMPLE_PROFILE = "_example"
RESUME_EXTENSIONS = (".pdf", ".docx", ".txt", ".md")
# Files we write into a profile folder ourselves; never mistaken for the resume.
_NOT_A_RESUME = {"profile.md", "readme.md", "readme.txt", "put-your-resume-here.txt"}
_RESUME_NAME_RE = re.compile(r"resume|r[eé]sum[eé]|\bcv\b", re.IGNORECASE)

CONFIG_DEFAULTS: dict[str, Any] = {
    "roles": [],
    "explore_adjacent_roles": True,
    "max_jobs_to_score_per_run": 60,
    "first_run_max_jobs_to_score": 30,
    "description_max_words": 1500,
    "first_run_hours_old": 72,
    "results_per_keyword": 100,
}
_POSITIVE_INT_KEYS = (
    "max_jobs_to_score_per_run",
    "first_run_max_jobs_to_score",
    "description_max_words",
    "first_run_hours_old",
    "results_per_keyword",
)
# Settings from the Google Sheets era. An old config.yaml may still hold them; they are
# ignored (never an error) and reported so the doctor can say they are no longer used.
DEPRECATED_KEYS = ("sheet", "sheet_id", "auth", "credentials")

_LOCATION_PLACEHOLDER_PREFIX = "YOUR "

# Only used to point people with an old Google sheet at their data (see find_legacy_sheet).
_LEGACY_SHEET_URL = "https://docs.google.com/spreadsheets/d/{sheet_id}/edit"
_LEGACY_SHEET_URL_RE = re.compile(r"/spreadsheets/d/([A-Za-z0-9_-]+)")
_LEGACY_SHEET_ID_RE = re.compile(r"^[A-Za-z0-9_-]{20,}$")


class UserFacingError(RuntimeError):
    """A problem whose message is written for the person running the tool. The scripts print
    it plainly and exit instead of showing a traceback (see util.run_cli)."""


class ProfileError(UserFacingError):
    """A problem with a profile folder or its config that the person can fix."""


def list_profiles() -> list[str]:
    """Real profile folders (the _example template and hidden folders don't count)."""
    if not PROFILES_DIR.is_dir():
        return []
    return sorted(
        p.name for p in PROFILES_DIR.iterdir() if p.is_dir() and not p.name.startswith(("_", "."))
    )


def profile_dir(profile: str) -> Path:
    path = PROFILES_DIR / profile
    if not path.is_dir():
        existing = ", ".join(list_profiles()) or "none yet"
        raise ProfileError(
            f"No profile folder at {path}. Existing profiles: {existing}. "
            f"Create one with: python scripts/new_profile.py {profile}"
        )
    return path


def looks_like_resume_file(path: Path) -> bool:
    """A document that could be a resume, and not one of the files the tool writes itself."""
    return path.suffix.lower() in RESUME_EXTENSIONS and path.name.lower() not in _NOT_A_RESUME


def find_resume(profile: str) -> Path | None:
    """The person's resume: a pdf/docx/txt/md in their folder.

    With several documents (cover letter, notes...), one named like a resume or CV wins;
    otherwise the first in alphabetical order."""
    candidates = [
        path
        for path in sorted(profile_dir(profile).iterdir())
        if path.is_file() and looks_like_resume_file(path)
    ]
    named = [p for p in candidates if _RESUME_NAME_RE.search(p.stem)]
    return (named or candidates or [None])[0]


def config_problems(config: dict[str, Any]) -> list[str]:
    """Plain-English list of what's wrong with a loaded config (empty list = fine)."""
    problems = []
    for key in _POSITIVE_INT_KEYS:
        try:
            valid = int(config[key]) >= 1
        except (TypeError, ValueError):
            valid = False
        if not valid:
            problems.append(f"`{key}` must be a whole number above 0 (or delete the line).")

    locations = config.get("locations") or []
    if not locations:
        problems.append(
            '`locations` is empty. Add at least one city, e.g. "London, United Kingdom", '
            '"Austin, TX" or "Remote".'
        )
    elif any(loc.upper().startswith(_LOCATION_PLACEHOLDER_PREFIX) for loc in locations):
        problems.append("`locations` still has the placeholder. Replace it with your own city.")

    if "workbook" in config and not _is_workbook_setting(config["workbook"]):
        problems.append(
            f"`workbook` must be the path of an Excel file ending in {WORKBOOK_SUFFIX} "
            "(or delete the line to keep it in your profile folder)."
        )
    return problems


def _is_workbook_setting(value: Any) -> bool:
    return isinstance(value, str) and value.strip().lower().endswith(WORKBOOK_SUFFIX)


def normalize_config(raw: dict[str, Any], profile: str) -> dict[str, Any]:
    """Merge raw YAML over the defaults and tidy values. Does not validate.

    The old key name `target_roles` is still accepted. Keys from the Google Sheets era are
    dropped from the result and listed under `deprecated_keys` instead of causing an error."""
    kept = {k: v for k, v in raw.items() if v is not None and k not in DEPRECATED_KEYS}
    config = {**CONFIG_DEFAULTS, **kept}
    config["name"] = profile
    config["roles"] = raw.get("roles") or raw.get("target_roles") or []
    config["deprecated_keys"] = [key for key in DEPRECATED_KEYS if raw.get(key)]
    config["legacy_sheet_url"] = _legacy_sheet_url(raw.get("sheet"), raw.get("sheet_id"))

    locations = raw.get("locations") or []
    if isinstance(locations, str):
        locations = [locations]
    config["locations"] = [str(loc).strip() for loc in locations if str(loc).strip()]
    return config


def _legacy_sheet_url(*values: Any) -> str | None:
    """The address of an old Google sheet, from whichever value holds a sheet address or ID."""
    for value in values:
        text = str(value or "").strip()
        match = _LEGACY_SHEET_URL_RE.search(text)
        if match:
            return _LEGACY_SHEET_URL.format(sheet_id=match.group(1))
        if _LEGACY_SHEET_ID_RE.match(text):
            return _LEGACY_SHEET_URL.format(sheet_id=text)
    return None


@dataclass(frozen=True)
class LegacySheet:
    """Evidence that this profile kept its job list in a Google sheet in an earlier version.
    `url` is where that sheet is, when the files say."""

    url: str | None


def find_legacy_sheet(config: dict[str, Any], state: dict[str, Any]) -> LegacySheet | None:
    """Whether this profile has job history in an old Google sheet, judging by the settings
    that version wrote to config.yaml and agent-state.json. That history is what dedupe
    depends on, so a brand-new empty list must never silently take its place."""
    url = config.get("legacy_sheet_url") or _legacy_sheet_url(
        state.get("sheet_url"), state.get("sheet_id")
    )
    if config.get("deprecated_keys") or state.get("sheet_id") or state.get("sheet_url"):
        return LegacySheet(url)
    return None


def sheet_command(profile: str) -> str:
    """The start of every command that talks to a profile's job list, for messages that tell
    the person (or Claude on their behalf) what to run."""
    return f"uv run python scripts/sheet.py --profile {profile}"


def load_config(profile: str) -> dict[str, Any]:
    """Load profiles/<profile>/config.yaml, apply defaults, and raise ProfileError listing
    everything that needs fixing."""
    import yaml

    config_path = profile_dir(profile) / "config.yaml"
    if not config_path.exists():
        raise ProfileError(
            f"Missing config.yaml for profile '{profile}' at {config_path}. "
            f"Copy it from profiles/{EXAMPLE_PROFILE}/config.yaml."
        )
    with config_path.open("r", encoding="utf-8") as f:
        config = normalize_config(yaml.safe_load(f) or {}, profile)

    problems = config_problems(config)
    if problems:
        raise ProfileError(
            f"profiles/{profile}/config.yaml needs attention:\n  - " + "\n  - ".join(problems)
        )
    return config


def load_profile_md(profile: str) -> str:
    path = profile_dir(profile) / "profile.md"
    if not path.exists():
        raise ProfileError(f"Missing profile.md for profile '{profile}' at {path}")
    return path.read_text(encoding="utf-8")


def workbook_path(profile: str, config: dict[str, Any]) -> Path:
    """Where this person's Excel job list lives. Only computes the path; touches no file.

    By default it sits in the profile folder. `workbook:` in config.yaml may be an absolute
    path, one starting with ~ (the home folder), or one relative to the profile folder."""
    folder = PROFILES_DIR / profile
    configured = str(config.get("workbook") or "").strip()
    if not configured:
        return folder / workbook_filename(profile)
    path = Path(configured).expanduser()
    return path if path.is_absolute() else folder / path


def backups_dir(profile: str) -> Path:
    """Where the automatic copies of this person's job list are kept (inside the profile
    folder even when `workbook:` points elsewhere, so they stay together with the profile)."""
    return profile_dir(profile) / BACKUPS_DIRNAME


def read_state(profile: str) -> dict[str, Any]:
    """What the app itself remembers about a profile (the workbook it created, and so on).

    Empty if the file is missing or damaged: losing it must never break a run, because
    `sheet.py init` re-opens the workbook if it is still where the profile expects it."""
    path = profile_dir(profile) / STATE_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_state(profile: str, data: dict[str, Any]) -> None:
    """Replace agent-state.json atomically, so a crash mid-write can't leave half a file."""
    path = profile_dir(profile) / STATE_FILE
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".agent-state-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
