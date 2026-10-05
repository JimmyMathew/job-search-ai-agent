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
from pathlib import Path
from typing import Any

from util import load_json, save_json


def _active_block_rules(rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        r
        for r in rules
        if str(r.get("type", "")).strip().lower() == "block"
        and str(r.get("active", "")).strip().lower() in ("yes", "true")
        and r.get("keywords")
    ]


def _matched_keyword(text: str, keywords: str) -> str | None:
    haystack = text.lower()
    for kw in keywords.split(";"):
        kw = kw.strip()
        if kw and kw.lower() in haystack:
            return kw
    return None


def apply_block_rules(
    jobs: list[dict[str, Any]], rules: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    block_rules = _active_block_rules(rules)
    passed: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []

    for job in jobs:
        text = f"{job.get('title', '')} {job.get('description', '')}"
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
    args = parser.parse_args()

    payload = load_json(args.in_path)
    jobs = payload["jobs"] if isinstance(payload, dict) else payload
    rules = load_json(args.rules)

    passed, blocked = apply_block_rules(jobs, rules)

    save_json(args.out_passed, {"jobs": passed, "count": len(passed)})
    save_json(args.out_blocked, {"jobs": blocked, "count": len(blocked)})
    print(f"{len(passed)} passed, {len(blocked)} blocked by rules")


if __name__ == "__main__":
    main()
