"""Small helpers shared by the scripts: JSON files, timestamps, job IDs, description
trimming and repost detection. Profile/config handling lives in config.py."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from config import RUNS_DIR

_JOB_ID_RE = re.compile(r"/jobs/view/(\d+)")


def extract_job_id(url: str) -> str | None:
    """Pull the numeric LinkedIn job ID out of a /jobs/view/<id> link."""
    match = _JOB_ID_RE.search(url or "")
    return match.group(1) if match else None


def trim_description(text: str, max_words: int = 1500) -> str:
    """Cut a job description down to roughly its first max_words words.

    Keeps token cost predictable when descriptions go to the scorer."""
    if not text:
        return ""
    words = text.split()
    if len(words) <= max_words:
        return text.strip()
    return " ".join(words[:max_words]).strip() + " …"


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _normalize(text: str | None) -> str:
    return (text or "").strip().lower()


def is_repost(
    title: str,
    company: str,
    posted_date: str | None,
    existing_jobs: list[dict[str, Any]],
    window_days: int = 30,
) -> bool:
    """Same company and title as something already seen within window_days of this posting."""
    posted = _parse_iso(posted_date)
    if posted is None:
        return False

    window = timedelta(days=window_days)
    norm_title, norm_company = _normalize(title), _normalize(company)
    for job in existing_jobs:
        if _normalize(job.get("title")) != norm_title:
            continue
        if _normalize(job.get("company")) != norm_company:
            continue
        other = _parse_iso(job.get("posted_date"))
        if other is not None and posted - window <= other <= posted + window:
            return True
    return False


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def hours_since(timestamp_iso: str | None, default_hours: int) -> int:
    """Whole hours since a timestamp, rounded up, at least 1; default_hours if unknown."""
    last_run = _parse_iso(timestamp_iso)
    if last_run is None:
        return default_hours
    if last_run.tzinfo is None:
        last_run = last_run.replace(tzinfo=timezone.utc)
    elapsed = datetime.now(timezone.utc) - last_run
    return max(1, int(elapsed.total_seconds() // 3600) + 1)


def unwrap_jobs(payload: Any) -> list[dict[str, Any]]:
    """The scripts pass jobs either as a bare list or as {"jobs": [...], ...}."""
    return payload["jobs"] if isinstance(payload, dict) else payload


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
