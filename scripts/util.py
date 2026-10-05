"""Shared helpers used by fetch.py, sheet.py and prefilter.py."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
PROFILES_DIR = REPO_ROOT / "profiles"
SECRETS_DIR = REPO_ROOT / "secrets"
RUNS_DIR = REPO_ROOT / "runs"

_JOB_ID_RE = re.compile(r"/jobs/view/(\d+)")


class ProfileError(RuntimeError):
    pass


EXAMPLE_PROFILE = "_example"
RESUME_EXTENSIONS = (".pdf", ".docx", ".txt", ".md")

CONFIG_DEFAULTS: dict[str, Any] = {
    "roles": [],
    "explore_adjacent_roles": True,
    "credentials": "secrets/service-account.json",
    "max_jobs_to_score_per_run": 60,
    "first_run_hours_old": 72,
    "results_per_keyword": 100,
}

_SHEET_ID_RE = re.compile(r"^[A-Za-z0-9_-]{20,}$")
_SHEET_URL_RE = re.compile(r"/spreadsheets/d/([A-Za-z0-9_-]+)")
_PLACEHOLDER_RE = re.compile(r"^\s*(<.*>|paste.*|your.*|)\s*$", re.IGNORECASE)


def list_profiles() -> list[str]:
    """Real profile folders (the _example template and hidden folders don't count)."""
    if not PROFILES_DIR.is_dir():
        return []
    return sorted(
        p.name
        for p in PROFILES_DIR.iterdir()
        if p.is_dir() and not p.name.startswith(("_", "."))
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
    """Any pdf/docx/txt/md in the profile folder, other than the files we write ourselves."""
    ignore = {"profile.md", "readme.md", "readme.txt", "put-your-resume-here.txt"}
    candidates = [
        path
        for path in sorted(profile_dir(profile).iterdir())
        if path.is_file()
        and path.suffix.lower() in RESUME_EXTENSIONS
        and path.name.lower() not in ignore
    ]
    # With several documents (cover letter, notes...), prefer one named like a resume.
    named = [p for p in candidates if re.search(r"resume|r[eé]sum[eé]|cv", p.stem, re.I)]
    return (named or candidates or [None])[0]


def parse_sheet_id(value: str | None) -> str | None:
    """Accept a full Google Sheets URL or a bare sheet ID; return the ID (or None if unset)."""
    value = (value or "").strip()
    if _PLACEHOLDER_RE.match(value):
        return None
    match = _SHEET_URL_RE.search(value)
    return match.group(1) if match else value


def config_problems(config: dict[str, Any]) -> list[str]:
    """Plain-English list of what's wrong with a config (empty list = fine)."""
    problems = []
    for key in ("max_jobs_to_score_per_run", "first_run_hours_old", "results_per_keyword"):
        try:
            if int(config[key]) < 1:
                raise ValueError
        except (TypeError, ValueError):
            problems.append(f"`{key}` must be a whole number above 0 (or delete the line).")
    if any(loc.upper().startswith("YOUR ") for loc in config.get("locations", [])):
        problems.append("`locations` still has the placeholder. Replace it with your own city.")
    if not config.get("locations"):
        problems.append(
            "`locations` is empty. Add at least one city, e.g. \"London, United Kingdom\", "
            "\"Austin, TX\" or \"Remote\"."
        )
    if not config.get("sheet_id"):
        problems.append(
            "`sheet` is not set. Paste your Google Sheet's web address into config.yaml."
        )
    elif not _SHEET_ID_RE.match(config["sheet_id"]):
        problems.append(
            "`sheet` doesn't look like a Google Sheets address. Copy it from your browser's "
            "address bar while the sheet is open (it contains /spreadsheets/d/...)."
        )
    return problems


def load_config(profile: str, *, require_sheet: bool = True) -> dict[str, Any]:
    """Load profiles/<profile>/config.yaml merged over CONFIG_DEFAULTS.

    Only `locations` and `sheet` are required. Old key names (`sheet_id`,
    `target_roles`) are still accepted."""
    import yaml

    config_path = profile_dir(profile) / "config.yaml"
    if not config_path.exists():
        raise ProfileError(
            f"Missing config.yaml for profile '{profile}' at {config_path}. "
            f"Copy it from profiles/{EXAMPLE_PROFILE}/config.yaml."
        )
    with config_path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    # A key left blank in the YAML (None) must not wipe out its default.
    config = {**CONFIG_DEFAULTS, **{k: v for k, v in raw.items() if v is not None}}
    config["name"] = profile
    config["roles"] = raw.get("roles") or raw.get("target_roles") or []
    config["sheet_id"] = parse_sheet_id(raw.get("sheet") or raw.get("sheet_id"))
    if isinstance(config.get("locations"), str):
        config["locations"] = [config["locations"]]
    config["locations"] = [str(x).strip() for x in (config.get("locations") or []) if str(x).strip()]

    problems = config_problems(config)
    if not require_sheet:
        problems = [p for p in problems if not p.startswith("`sheet`")]
    if problems:
        raise ProfileError(
            f"profiles/{profile}/config.yaml needs attention:\n  - " + "\n  - ".join(problems)
        )
    return config


def credentials_path(config: dict[str, Any]) -> Path:
    path = Path(config.get("credentials") or CONFIG_DEFAULTS["credentials"])
    return path if path.is_absolute() else REPO_ROOT / path


def load_profile_md(profile: str) -> str:
    profile_path = profile_dir(profile) / "profile.md"
    if not profile_path.exists():
        raise ProfileError(f"Missing profile.md for profile '{profile}' at {profile_path}")
    return profile_path.read_text(encoding="utf-8")


def extract_job_id(url: str) -> str | None:
    """Pull the numeric LinkedIn job ID out of a /jobs/view/<id> link."""
    match = _JOB_ID_RE.search(url or "")
    return match.group(1) if match else None


def trim_description(text: str, max_words: int = 1500) -> str:
    """Cut a job description down to roughly its first max_words words.

    Keeps token cost predictable when descriptions go to the Sonnet scorer.
    """
    if not text:
        return ""
    words = text.split()
    if len(words) <= max_words:
        return text.strip()
    return " ".join(words[:max_words]).strip() + " …"


def is_repost(
    title: str,
    company: str,
    posted_date: str | None,
    existing_jobs: list[dict[str, Any]],
    window_days: int = 30,
) -> bool:
    """Same company + same title as something already seen within window_days."""
    if not posted_date:
        return False
    try:
        posted = datetime.fromisoformat(posted_date)
    except ValueError:
        return False

    norm_title = (title or "").strip().lower()
    norm_company = (company or "").strip().lower()
    cutoff = posted - timedelta(days=window_days)

    for job in existing_jobs:
        if (job.get("title") or "").strip().lower() != norm_title:
            continue
        if (job.get("company") or "").strip().lower() != norm_company:
            continue
        other_posted = job.get("posted_date")
        if not other_posted:
            continue
        try:
            other_dt = datetime.fromisoformat(other_posted)
        except ValueError:
            continue
        if cutoff <= other_dt <= posted + timedelta(days=window_days):
            return True
    return False


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def hours_since(timestamp_iso: str | None, default_hours: int) -> int:
    if not timestamp_iso:
        return default_hours
    try:
        last_run = datetime.fromisoformat(timestamp_iso)
    except ValueError:
        return default_hours
    if last_run.tzinfo is None:
        last_run = last_run.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - last_run
    return max(1, int(delta.total_seconds() // 3600) + 1)


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def new_run_dir(profile: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = RUNS_DIR / profile / stamp
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir
