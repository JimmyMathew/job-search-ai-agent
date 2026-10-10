"""Apply Block rules (keyword checks, no model) to fetched-and-deduped jobs.

CLI:
    python scripts/prefilter.py --in deduped.json --rules rules.json \
        --out-passed passed.json --out-blocked blocked.json

`rules.json` is the Rules tab exported by `sheet.py rules`: a list of
{rule, type, keywords, active} dicts. Only rows with type == "Block" and
active == "Yes" are used here; Lower/Boost rules are ranking hints the
Sonnet scorer applies, not prefilter rules.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any

from util import load_json, save_json, unwrap_jobs


def _active_block_rules(rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        r
        for r in rules
        if str(r.get("type", "")).strip().lower() == "block"
        and str(r.get("active", "")).strip().lower() in ("yes", "true")
        and r.get("keywords")
    ]


def _matched_keyword(text: str, keywords: str) -> str | None:
    """First of the `;`-separated keywords found in text as a whole word or phrase, so a
    keyword like "sales" doesn't match "wholesales" and "ca" doesn't match "cat"."""
    for keyword in (k.strip() for k in keywords.split(";")):
        if keyword and re.search(rf"(?<!\w){re.escape(keyword)}(?!\w)", text, re.IGNORECASE):
            return keyword
    return None


def apply_block_rules(
    jobs: list[dict[str, Any]], rules: list[dict[str, Any]], *, title_only: bool = False
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split jobs into (passed, blocked). With title_only, rules are checked against the title
    alone, which lets a job be blocked before its description is fetched. Anything blocked on
    its title alone would also have been blocked with the description, so nothing new is hidden."""
    block_rules = _active_block_rules(rules)
    passed: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []

    for job in jobs:
        text = (
            job.get("title", "")
            if title_only
            else f"{job.get('title', '')} {job.get('description', '')}"
        )
        hit = None
        for rule in block_rules:
            keyword = _matched_keyword(text, rule["keywords"])
            if keyword:
                hit = (rule, keyword)
                break
        if hit:
            rule, keyword = hit
            blocked.append(
                {
                    **job,
                    "filtered_reason": f"Blocked by rule: {rule['rule']} (matched {keyword!r})",
                }
            )
        else:
            passed.append(job)

    return passed, blocked


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--in", dest="in_path", type=Path, required=True)
    parser.add_argument("--rules", type=Path, required=True)
    parser.add_argument("--out-passed", type=Path, required=True)
    parser.add_argument("--out-blocked", type=Path, required=True)
    parser.add_argument(
        "--title-only", action="store_true", help="Check rules against titles only (pre-fetch)"
    )
    args = parser.parse_args()

    jobs = unwrap_jobs(load_json(args.in_path))
    rules = load_json(args.rules)

    passed, blocked = apply_block_rules(jobs, rules, title_only=args.title_only)

    save_json(args.out_passed, {"jobs": passed, "count": len(passed)})
    save_json(args.out_blocked, {"jobs": blocked, "count": len(blocked)})
    print(f"{len(passed)} passed, {len(blocked)} blocked by rules")


if __name__ == "__main__":
    main()
