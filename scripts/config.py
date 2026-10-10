"""Profiles and their config.yaml: locating, loading, defaulting and validating.

A profile is a folder under profiles/ holding a person's resume, config.yaml and the
profile.md that /job-setup writes. Only `locations` and `sheet` are required in
config.yaml; every other setting has a default in CONFIG_DEFAULTS.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
PROFILES_DIR = REPO_ROOT / "profiles"
RUNS_DIR = REPO_ROOT / "runs"

EXAMPLE_PROFILE = "_example"
RESUME_EXTENSIONS = (".pdf", ".docx", ".txt", ".md")
# Files we write into a profile folder ourselves; never mistaken for the resume.
_NOT_A_RESUME = {"profile.md", "readme.md", "readme.txt", "put-your-resume-here.txt"}
_RESUME_NAME_RE = re.compile(r"resume|r[eé]sum[eé]|\bcv\b", re.IGNORECASE)

CONFIG_DEFAULTS: dict[str, Any] = {
    "roles": [],
    "explore_adjacent_roles": True,
    "credentials": "secrets/service-account.json",
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

_SHEET_ID_RE = re.compile(r"^[A-Za-z0-9_-]{20,}$")
_SHEET_URL_RE = re.compile(r"/spreadsheets/d/([A-Za-z0-9_-]+)")
_SHEET_PLACEHOLDER_RE = re.compile(r"^\s*(<.*>|paste.*)?\s*$", re.IGNORECASE)
_LOCATION_PLACEHOLDER_PREFIX = "YOUR "


class ProfileError(RuntimeError):
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


def find_resume(profile: str) -> Path | None:
    """The person's resume: a pdf/docx/txt/md in their folder.

    With several documents (cover letter, notes...), one named like a resume or CV wins;
    otherwise the first in alphabetical order."""
    candidates = [
        path
        for path in sorted(profile_dir(profile).iterdir())
        if path.is_file()
        and path.suffix.lower() in RESUME_EXTENSIONS
        and path.name.lower() not in _NOT_A_RESUME
    ]
    named = [p for p in candidates if _RESUME_NAME_RE.search(p.stem)]
    return (named or candidates or [None])[0]


def parse_sheet_id(value: str | None) -> str | None:
    """Accept a full Google Sheets address or a bare ID; None if unset or still a placeholder."""
    value = (value or "").strip()
    if _SHEET_PLACEHOLDER_RE.match(value):
        return None
    match = _SHEET_URL_RE.search(value)
    return match.group(1) if match else value


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

    sheet_id = config.get("sheet_id")
    if not sheet_id:
        problems.append(
            "`sheet` is not set. Paste your Google Sheet's web address into config.yaml."
        )
    elif not _SHEET_ID_RE.match(sheet_id):
        problems.append(
            "`sheet` doesn't look like a Google Sheets address. Copy it from your browser's "
            "address bar while the sheet is open (it contains /spreadsheets/d/...)."
        )
    return problems


def normalize_config(raw: dict[str, Any], profile: str) -> dict[str, Any]:
    """Merge raw YAML over the defaults and tidy values. Does not validate.

    Old key names (`sheet_id`, `target_roles`) are still accepted."""
    config = {**CONFIG_DEFAULTS, **{k: v for k, v in raw.items() if v is not None}}
    config["name"] = profile
    config["roles"] = raw.get("roles") or raw.get("target_roles") or []
    config["sheet_id"] = parse_sheet_id(raw.get("sheet") or raw.get("sheet_id"))

    locations = raw.get("locations") or []
    if isinstance(locations, str):
        locations = [locations]
    config["locations"] = [str(loc).strip() for loc in locations if str(loc).strip()]
    return config


def load_config(profile: str, *, require_sheet: bool = True) -> dict[str, Any]:
    """Load profiles/<profile>/config.yaml, apply defaults, and raise ProfileError listing
    everything that needs fixing. `require_sheet=False` skips only the sheet check."""
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
    if not require_sheet:
        problems = [p for p in problems if not p.startswith("`sheet`")]
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


def credentials_path(config: dict[str, Any]) -> Path:
    """Where this person's Google service-account key is (relative paths start at the repo root)."""
    path = Path(config.get("credentials") or CONFIG_DEFAULTS["credentials"])
    return path if path.is_absolute() else REPO_ROOT / path
