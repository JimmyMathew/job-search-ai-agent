"""Search LinkedIn (logged out, via JobSpy) for every keyword in a profile.

CLI:
    python scripts/fetch.py search --profile <name> --hours-old 72 --out raw.json
    python scripts/fetch.py descriptions --in deduped.json --out enriched.json

Two-phase by design, matching the plan: `search` is a cheap listing-only pass
(title/company/location/link/posted date, no description — one request per
page of ~10 results). `descriptions` fetches the full description for a
specific list of job IDs — one request per job — and is meant to be called
only on jobs that survived dedupe (and ideally the title prefilter), never
on every raw search result. JobSpy's own `linkedin_fetch_description=True`
flag does the description fetch *inline* during search, for every result;
that's what made an early test take 90-140s per keyword, so we don't use it.

Neither phase dedupes against the job list or writes to it — that's sheet.py,
downstream. Keeping fetch pure means it can be re-run and tested without
touching the job list at all.
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import re
import sys
import time
import types
from collections.abc import Callable
from pathlib import Path
from typing import Any

from config import ProfileError, load_config, load_profile_md
from util import extract_job_id, load_json, now_iso, run_cli, save_json, unwrap_jobs

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("fetch")

# JobSpy pages internally up to this many results per search (config: results_per_keyword)
DEFAULT_RESULTS_PER_KEYWORD = 100
PAUSE_BETWEEN_CALLS = (3.0, 7.0)  # seconds, randomized, between keyword/location searches
PAUSE_BETWEEN_DESCRIPTIONS = (2.0, 5.0)  # seconds, randomized, between per-job description fetches
# JobSpy swallows errors and returns "", so a run of empty descriptions is our block signal
MAX_CONSECUTIVE_EMPTY_DESCRIPTIONS = 5


class LinkedInBlocked(RuntimeError):
    """Raised when LinkedIn appears to be rate-limiting or blocking this IP."""


def _pause() -> None:
    time.sleep(random.uniform(*PAUSE_BETWEEN_CALLS))


def _normalize(row: dict[str, Any], keyword: str) -> dict[str, Any] | None:
    import pandas as pd

    url = row.get("job_url") or ""
    job_id = extract_job_id(url)
    if not job_id:
        return None
    posted = row.get("date_posted")
    # JobSpy leaves this NaT/NaN when LinkedIn omits it
    posted = None if pd.isna(posted) else posted
    return {
        "job_id": job_id,
        "title": (row.get("title") or "").strip(),
        "company": (row.get("company") or "").strip(),
        "location": (row.get("location") or "").strip(),
        "posted_date": str(posted) if posted else None,
        "link": url,
        "description": (row.get("description") or "").strip(),
        "source_keyword": keyword,
    }


def fetch_for_keyword(
    keyword: str, location: str, hours_old: int, results_wanted: int = DEFAULT_RESULTS_PER_KEYWORD
) -> list[dict[str, Any]]:
    from jobspy import scrape_jobs  # imported lazily so --help doesn't need the dependency

    log.info("Searching %r in %r (hours_old=%s)", keyword, location, hours_old)
    try:
        df = scrape_jobs(
            site_name=["linkedin"],
            search_term=keyword,
            location=location,
            hours_old=hours_old,
            results_wanted=results_wanted,
            linkedin_fetch_description=False,
        )
    except Exception as exc:  # JobSpy raises plain Exceptions on HTTP failures
        message = str(exc).lower()
        if "429" in message or "too many requests" in message:
            raise LinkedInBlocked(str(exc)) from exc
        raise

    if df is None or df.empty:
        return []

    jobs: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        job = _normalize(row.to_dict(), keyword)
        if job:
            jobs.append(job)
    return jobs


def fetch_all(
    profile: str,
    hours_old: int,
    on_progress: Callable[[list[dict[str, Any]]], None] | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    """Returns (jobs, was_blocked). Partial results are kept on a block.

    `on_progress` is called with the jobs found so far after every keyword x location search,
    so the caller can checkpoint them to disk."""
    config = load_config(profile)
    keywords = _keywords_for(profile, config["roles"])
    locations: list[str] = config["locations"]
    results_wanted = int(config["results_per_keyword"])

    all_jobs: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    blocked = False

    for location in locations:
        for keyword in keywords:
            try:
                jobs = fetch_for_keyword(keyword, location, hours_old, results_wanted)
            except LinkedInBlocked:
                log.warning("LinkedIn appears to be blocking this IP. Stopping fetch early.")
                blocked = True
                break
            for job in jobs:
                if job["job_id"] not in seen_ids:
                    seen_ids.add(job["job_id"])
                    all_jobs.append(job)
            if on_progress:
                on_progress(all_jobs)
            _pause()
        if blocked:
            break

    return all_jobs, blocked


_NUMBERED_ITEM_RE = re.compile(r"^\d+[.)]\s*(.+)$")


def parse_search_keywords(profile_md: str) -> list[str]:
    """The numbered list under "## Search keywords" in profile.md, e.g.
    "1. Data Analyst" or "4. **Senior Analyst**" -> "Data Analyst", "Senior Analyst"."""
    keywords: list[str] = []
    in_section = False
    for line in profile_md.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("## search keywords"):
            in_section = True
        elif in_section and stripped.startswith("##"):
            break
        elif in_section:
            item = _NUMBERED_ITEM_RE.match(stripped)
            if item and item.group(1).strip("* "):
                keywords.append(item.group(1).strip("* "))
    return keywords


def _keywords_for(profile: str, roles: list[str]) -> list[str]:
    """Search keywords from profile.md, falling back to the roles in config.yaml when
    profile.md doesn't exist yet or has no keyword list."""
    try:
        keywords = parse_search_keywords(load_profile_md(profile))
    except ProfileError:
        keywords = []
    keywords = keywords or [str(r).strip() for r in roles if str(r).strip()]
    if not keywords:
        raise ProfileError(
            f"No search keywords for '{profile}': profile.md has no '## Search keywords' "
            f"list and config.yaml has no `roles`. Run /job-setup {profile} to generate them."
        )
    return keywords


def _load_cached_descriptions(cache_path: Path | None) -> dict[str, str]:
    """Descriptions already fetched by an earlier, interrupted attempt (job_id -> text)."""
    cached: dict[str, str] = {}
    if cache_path and cache_path.exists():
        for line in cache_path.read_text(encoding="utf-8").splitlines():
            try:
                entry = json.loads(line)
            except ValueError:
                continue  # a half-written last line from a crash
            if entry.get("description"):
                cached[str(entry["job_id"])] = entry["description"]
    return cached


def fetch_descriptions(
    jobs: list[dict[str, Any]], cache_path: Path | None = None
) -> tuple[list[dict[str, Any]], bool]:
    """Fetch the full description for each job (one request per job).

    Only call this on jobs that already survived dedupe: this is the expensive, blockable
    part of fetching. Returns (jobs_with_description, was_blocked), in the same order.

    Each description is appended to `cache_path` (JSON lines) the moment it arrives, and
    jobs already in the cache are not fetched again, so an interrupted fetch resumes where
    it stopped. A job whose description could not be fetched gets "description": "" and
    "description_unavailable": True; those are never sent to the scorer."""
    from jobspy.linkedin import LinkedIn
    from jobspy.model import DescriptionFormat

    scraper = LinkedIn()
    scraper.scraper_input = types.SimpleNamespace(description_format=DescriptionFormat.MARKDOWN)

    cached = _load_cached_descriptions(cache_path)
    enriched: list[dict[str, Any]] = []
    consecutive_empty = 0
    blocked = False

    for i, job in enumerate(jobs):
        job_id = str(job["job_id"])
        if job_id in cached:
            enriched.append({**job, "description": cached[job_id]})
            continue
        if blocked:
            enriched.append({**job, "description": "", "description_unavailable": True})
            continue

        log.info("Fetching description %d/%d: %s", i + 1, len(jobs), job.get("title"))
        # JobSpy 1.3 renamed the private _get_job_details to _fetch_details (same result).
        get_details = getattr(scraper, "_fetch_details", None) or scraper._get_job_details
        details = get_details(job_id)
        description = (details or {}).get("description") or ""

        if description:
            consecutive_empty = 0
            enriched.append({**job, "description": description})
            if cache_path:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                with cache_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps({"job_id": job_id, "description": description}) + "\n")
        else:
            enriched.append({**job, "description": "", "description_unavailable": True})
            consecutive_empty += 1
            if consecutive_empty >= MAX_CONSECUTIVE_EMPTY_DESCRIPTIONS:
                log.warning(
                    "%d description fetches in a row came back empty — likely blocked. "
                    "Stopping description fetch early.",
                    consecutive_empty,
                )
                blocked = True

        if i < len(jobs) - 1 and not blocked:
            time.sleep(random.uniform(*PAUSE_BETWEEN_DESCRIPTIONS))

    return enriched, blocked


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    search_p = sub.add_parser("search", help="Listing-only search across a profile's keywords")
    search_p.add_argument("--profile", required=True)
    search_p.add_argument("--hours-old", type=int, required=True)
    search_p.add_argument("--out", type=Path, required=True)

    desc_p = sub.add_parser("descriptions", help="Fetch full descriptions for a list of jobs")
    desc_p.add_argument("--in", dest="in_path", type=Path, required=True)
    desc_p.add_argument("--out", type=Path, required=True)

    args = parser.parse_args()

    if args.command == "search":
        started_at = now_iso()  # the next run's search window starts here, not at run end

        def checkpoint(found: list[dict[str, Any]]) -> None:
            save_json(
                args.out,
                {
                    "jobs": found,
                    "blocked": False,
                    "partial": True,
                    "count": len(found),
                    "started_at": started_at,
                },
            )

        jobs, blocked = fetch_all(args.profile, args.hours_old, on_progress=checkpoint)
        save_json(
            args.out,
            {"jobs": jobs, "blocked": blocked, "count": len(jobs), "started_at": started_at},
        )
        log.info("Fetched %d unique jobs%s", len(jobs), " (blocked partway)" if blocked else "")
        if blocked:
            sys.exit(2)
    elif args.command == "descriptions":
        jobs = unwrap_jobs(load_json(args.in_path))
        cache_path = args.out.with_name(args.out.stem + ".cache.jsonl")
        enriched, blocked = fetch_descriptions(jobs, cache_path)
        save_json(args.out, {"jobs": enriched, "blocked": blocked, "count": len(enriched)})
        log.info(
            "Fetched %d descriptions%s", len(enriched), " (blocked partway)" if blocked else ""
        )
        if blocked:
            sys.exit(2)


if __name__ == "__main__":
    run_cli(main)
