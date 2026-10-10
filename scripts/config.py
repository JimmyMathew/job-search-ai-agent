"""Profiles and their config.yaml: locating, loading, defaulting and validating.

A profile is a folder under profiles/ holding a person's resume, config.yaml and the
profile.md that /job-setup writes. Only `locations` is required in config.yaml; every other
setting has a default in CONFIG_DEFAULTS. The sheet is normally created by `sheet.py connect`
and remembered in profiles/<name>/agent-state.json; `sheet:` in config.yaml is only for the
advanced "use an existing sheet" (service account) option and wins over the saved state.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
PROFILES_DIR = REPO_ROOT / "profiles"
RUNS_DIR = REPO_ROOT / "runs"
TOKENS_DIR = REPO_ROOT / "secrets" / "tokens"
STATE_FILE = "agent-state.json"
AUTH_MODES = ("oauth", "service_account")

EXAMPLE_PROFILE = "_example"
RESUME_EXTENSIONS = (".pdf", ".docx", ".txt", ".md")
# Files we write into a profile folder ourselves; never mistaken for the resume.
_NOT_A_RESUME = {"profile.md", "readme.md", "readme.txt", "put-your-resume-here.txt"}
_RESUME_NAME_RE = re.compile(r"resume|r[eé]sum[eé]|\bcv\b", re.IGNORECASE)

CONFIG_DEFAULTS: dict[str, Any] = {
    "roles": [],
    "explore_adjacent_roles": True,
    "credentials": "secrets/service-account.json",
    "auth": "auto",
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

    # The sheet is optional: setup creates it. Only a sheet that is present must look right.
    sheet_id = config.get("sheet_id")
    if sheet_id and not _SHEET_ID_RE.match(sheet_id):
        problems.append(
            "`sheet` doesn't look like a Google Sheets address. Copy it from your browser's "
            "address bar while the sheet is open (it contains /spreadsheets/d/...), or delete "
            "the line and let setup create a sheet for you."
        )

    if str(config.get("auth", "auto")).lower() not in ("auto", *AUTH_MODES):
        problems.append('`auth` must be "oauth" or "service_account" (or delete the line).')
    return problems


def normalize_config(
    raw: dict[str, Any], profile: str, state: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Merge raw YAML over the defaults and tidy values. Does not validate.

    Old key names (`sheet_id`, `target_roles`) are still accepted. A sheet set in the YAML
    beats the one remembered in `state` (agent-state.json)."""
    config = {**CONFIG_DEFAULTS, **{k: v for k, v in raw.items() if v is not None}}
    config["name"] = profile
    config["roles"] = raw.get("roles") or raw.get("target_roles") or []
    yaml_sheet = parse_sheet_id(raw.get("sheet") or raw.get("sheet_id"))
    config["sheet_from_yaml"] = bool(yaml_sheet)
    config["sheet_id"] = yaml_sheet or (state or {}).get("sheet_id") or None
    config["auth"] = str(config.get("auth") or "auto").lower()

    locations = raw.get("locations") or []
    if isinstance(locations, str):
        locations = [locations]
    config["locations"] = [str(loc).strip() for loc in locations if str(loc).strip()]
    return config


def load_config(profile: str) -> dict[str, Any]:
    """Load profiles/<profile>/config.yaml, apply defaults, fold in the saved sheet from
    agent-state.json, and raise ProfileError listing everything that needs fixing."""
    import yaml

    config_path = profile_dir(profile) / "config.yaml"
    if not config_path.exists():
        raise ProfileError(
            f"Missing config.yaml for profile '{profile}' at {config_path}. "
            f"Copy it from profiles/{EXAMPLE_PROFILE}/config.yaml."
        )
    with config_path.open("r", encoding="utf-8") as f:
        config = normalize_config(yaml.safe_load(f) or {}, profile, read_state(profile))

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


def credentials_path(config: dict[str, Any]) -> Path:
    """Where this person's Google service-account key is (relative paths start at the repo root)."""
    path = Path(config.get("credentials") or CONFIG_DEFAULTS["credentials"])
    return path if path.is_absolute() else REPO_ROOT / path


def read_state(profile: str) -> dict[str, Any]:
    """What the app itself remembers about a profile (the sheet it created, and so on).

    Empty if the file is missing or damaged: losing it must never break a run, because
    `sheet.py connect` can find the sheet again by its title."""
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


def token_path(profile: str) -> Path:
    """Where this profile's Google sign-in token is kept (git-ignored)."""
    return TOKENS_DIR / f"{profile}.json"


def oauth_client_path() -> Path:
    """The app's Google OAuth client file (shipped by the project owner, not a user secret)."""
    override = os.environ.get("JOB_AGENT_OAUTH_CLIENT")
    return Path(override) if override else REPO_ROOT / "scripts" / "google_oauth_client.json"


def choose_auth(config: dict[str, Any], key_exists: bool) -> str:
    """Which Google sign-in to use: "oauth" or "service_account".

    An explicit `auth:` in config.yaml wins. Otherwise a person who pasted their own sheet
    into config.yaml and has a service-account key keeps using it, because the app's
    drive.file access cannot open sheets it did not create. Everyone else gets OAuth."""
    explicit = str(config.get("auth") or "auto").lower()
    if explicit in AUTH_MODES:
        return explicit
    if config.get("sheet_from_yaml") and key_exists:
        return "service_account"
    return "oauth"
