"""Small helpers shared by the scripts: JSON files, timestamps, job IDs, description
trimming and repost detection. Profile/config handling lives in config.py."""

from __future__ import annotations

import contextlib
import json
import os
import re
import sys
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from config import RUNS_DIR, UserFacingError

_JOB_ID_RE = re.compile(r"/jobs/view/(\d+)")

DEBUG_ENV_VAR = "JOB_AGENT_DEBUG"


class DataFileError(UserFacingError):
    """A JSON file a script was told to read is missing or isn't valid."""


def _use_utf8_output() -> None:
    """Job titles can hold any language or emoji. A piped Windows shell encodes output as a
    legacy code page, so printing them would raise UnicodeEncodeError; write UTF-8 instead,
    replacing anything that still can't be written."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            with contextlib.suppress(ValueError, OSError):  # closed or already-read stream
                reconfigure(encoding="utf-8", errors="replace")


def run_cli(main: Callable[[], None]) -> None:
    """Run a script's main() the way a person should see it: UTF-8 output, a plain message for a
    UserFacingError, and for any other failure one plain sentence instead of a traceback (set
    JOB_AGENT_DEBUG=1 to see the traceback)."""
    _use_utf8_output()
    try:
        main()
    except UserFacingError as exc:
        sys.exit(str(exc))
    except Exception:
        if os.environ.get(DEBUG_ENV_VAR):
            raise
        sys.exit(
            "Something unexpected went wrong. Run the same command again; if it keeps "
            f"happening, set {DEBUG_ENV_VAR}=1 and run it once more to see the technical "
            "details."
        )


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
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except OSError as exc:
        raise DataFileError(f"Couldn't read {path}: {exc.strerror or exc}") from exc
    except ValueError as exc:
        raise DataFileError(f"{path} isn't valid JSON: {exc}") from exc


def save_json(path: Path, data: Any) -> None:
    """Write via a temp file and rename, so a crash never leaves a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    tmp.replace(path)


def new_run_dir(profile: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = RUNS_DIR / profile / stamp
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir
