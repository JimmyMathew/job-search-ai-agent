"""The mechanical middle of /job-run: planning, batching, ingesting scores and resuming.

The model's only job in a run is to read one batch file and write one scores file. Everything
around that (choosing what to score, writing batch files, validating what came back, saving
it to the sheet, retrying, resuming after a crash) is plain code here, so no job text ever
passes through the orchestrating session and finished work is saved batch by batch.

CLI (all paths are inside the run folder, runs/<profile>/<timestamp>/):
    pipeline.py checkpoint --deduped D/deduped.json --profile P [--first-run]
    pipeline.py plan       --deduped D/deduped.json --rules R --cap N --run-dir D
    pipeline.py resume-plan --pending D/pending.json --cap N --run-dir D
    pipeline.py make-batches --profile P --run-dir D --jobs D/passed.json --rules R --prefs X
    pipeline.py next       --run-dir D
    pipeline.py ingest     --profile P --run-dir D --batch N
    pipeline.py recover    --profile P
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import textwrap
from pathlib import Path
from typing import Any

from config import RUNS_DIR, load_config
from prefilter import apply_block_rules
from sheet_schema import (
    WHY_DESCRIPTION_UNAVAILABLE,
    WHY_OVER_CAP,
    WHY_PENDING,
    WHY_SCORING_FAILED,
    parse_scores,
)
from util import load_json, new_run_dir, save_json, trim_description, unwrap_jobs

BATCH_SIZE = 10
WRAP_WIDTH = 100  # the Read tool can truncate very long lines, so descriptions are wrapped

# Rough token model for the checkpoint estimate (not measured): the fixed per-batch context
# (instructions, profile.md, rules, preferences), then per job its description and its answer.
FIXED_TOKENS_PER_BATCH = 3500
TYPICAL_WORDS_PER_DESCRIPTION = 430  # median of a real 484-job run
TOKENS_PER_WORD = 1.35
OVERHEAD_TOKENS_PER_JOB = 60
OUTPUT_TOKENS_PER_JOB = 80

_LISTING_KEYS = ("job_id", "title", "company", "location", "posted_date", "link")


# ---- Pure logic (unit-tested) -------------------------------------------------------


def estimate_tokens(n_jobs: int, batch_size: int = BATCH_SIZE) -> dict[str, int]:
    """A rough scorer-token estimate for scoring n_jobs, in whole thousands."""
    batches = math.ceil(n_jobs / batch_size) if n_jobs else 0
    tokens_in = batches * FIXED_TOKENS_PER_BATCH + n_jobs * (
        round(TYPICAL_WORDS_PER_DESCRIPTION * TOKENS_PER_WORD) + OVERHEAD_TOKENS_PER_JOB
    )
    tokens_out = n_jobs * OUTPUT_TOKENS_PER_JOB
    return {
        "jobs": n_jobs,
        "batches": batches,
        "tokens_in_k": round(tokens_in / 1000),
        "tokens_out_k": round(tokens_out / 1000),
    }


def _listing(job: dict[str, Any]) -> dict[str, Any]:
    return {key: job.get(key) for key in _LISTING_KEYS}


def plan_jobs(
    jobs: list[dict[str, Any]], rules: list[dict[str, Any]], cap: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Split deduped jobs into (to_score, over_cap, blocked_on_title).

    Newest first. Jobs a Block rule already rejects by title are set aside before the cap, so
    the cap is never spent on them; they are still written to the sheet with their reason."""
    newest_first = sorted(jobs, key=lambda j: j.get("posted_date") or "", reverse=True)
    passed, blocked = apply_block_rules(newest_first, rules, title_only=True)
    return passed[:cap], passed[cap:], blocked


def sheet_rows_for_plan(
    to_score: list[dict[str, Any]],
    over_cap: list[dict[str, Any]],
    blocked: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Every job in the plan as a Jobs row, in the state it will stay in until scored."""
    return (
        [{**_listing(j), "why": WHY_PENDING} for j in to_score]
        + [{**_listing(j), "why": WHY_OVER_CAP} for j in over_cap]
        + [{**_listing(j), "filtered_reason": j["filtered_reason"]} for j in blocked]
    )


def _wrap(text: str) -> str:
    lines = []
    for line in text.splitlines():
        lines.extend(textwrap.wrap(line, WRAP_WIDTH) or [""] if len(line) > WRAP_WIDTH else [line])
    return "\n".join(lines)


def batch_markdown(n: int, jobs: list[dict[str, Any]], max_words: int) -> str:
    parts = [f"# Batch {n}: {len(jobs)} jobs to score\n"]
    for job in jobs:
        parts.append(
            f"## Job {job['job_id']}\n"
            f"- Title: {job.get('title', '')}\n"
            f"- Company: {job.get('company', '')}\n"
            f"- Location: {job.get('location', '')}\n"
            f"- Posted: {job.get('posted_date') or 'unknown'}\n"
            f"- Link: {job.get('link', '')}\n\n"
            f"Description:\n{_wrap(trim_description(job.get('description', ''), max_words))}\n"
        )
    return "\n".join(parts)


def context_markdown(rules: list[dict[str, Any]], prefs: list[dict[str, Any]]) -> str:
    """The rules and stated preferences the scorer weighs, shared by every batch of a run."""
    active = [
        r
        for r in rules
        if str(r.get("active", "")).strip().lower() in ("yes", "true")
        and str(r.get("type", "")).strip().lower() in ("lower", "boost")
    ]
    lines = ["# Learned rules (Lower / Boost)\n"]
    lines += [f"- [{r['type']}] {r['rule']} (keywords: {r.get('keywords', '')})" for r in active]
    if not active:
        lines.append("(none)")
    lines.append("\n# Stated preferences\n")
    lines += [f"- {p['criterion']}: {p['value']}" for p in prefs]
    if not prefs:
        lines.append("(none stated)")
    return "\n".join(lines) + "\n"


def next_batch(state: dict[str, Any]) -> dict[str, Any] | None:
    return next((b for b in state["batches"] if b["status"] == "todo"), None)


def apply_ingest(
    state: dict[str, Any], n: int, scored_ids: set[str]
) -> tuple[list[str], list[str]]:
    """Record that batch n was ingested. Mutates state; returns (retry_ids, failed_ids).

    Jobs the scorer skipped get one retry batch of their own; if they are missed again they
    are marked failed so they don't look pending forever."""
    batch = next(b for b in state["batches"] if b["n"] == n)
    batch["status"] = "done"
    missing = [i for i in batch["job_ids"] if i not in scored_ids]
    if not missing:
        return [], []
    if batch["attempt"] >= 2:
        return [], missing
    state["batches"].append(
        {
            "n": max(b["n"] for b in state["batches"]) + 1,
            "attempt": batch["attempt"] + 1,
            "job_ids": missing,
            "status": "todo",
        }
    )
    return missing, []


# ---- File layout in a run folder ------------------------------------------------------


def _state_path(run_dir: Path) -> Path:
    return run_dir / "state.json"


def _jobs_path(run_dir: Path) -> Path:
    return run_dir / "batch_jobs.json"


def _batch_path(run_dir: Path, n: int) -> Path:
    return run_dir / f"batch_{n:02d}.md"


def _scores_path(run_dir: Path, n: int) -> Path:
    return run_dir / f"scores_{n:02d}.jsonl"


def _load_optional(path: Path | None, default: Any) -> Any:
    return load_json(path) if path and path.exists() else default


# ---- Commands -----------------------------------------------------------------------


def cmd_checkpoint(args: argparse.Namespace) -> None:
    config = load_config(args.profile)
    deduped = load_json(args.deduped)
    cap = config["first_run_max_jobs_to_score" if args.first_run else "max_jobs_to_score_per_run"]
    n_new = len(deduped["new"])
    est = estimate_tokens(min(n_new, cap))
    print(
        json.dumps(
            {
                "new": n_new,
                "reposts": len(deduped["reposts"]),
                "suggested_cap": cap,
                "estimate_if_cap": est,
                "message": (
                    f"{n_new} new jobs. Scoring up to {cap} of them is about {est['batches']} "
                    f"{'batch' if est['batches'] == 1 else 'batches'} of 10, roughly "
                    f"{est['tokens_in_k']}k tokens read and about {max(est['tokens_out_k'], 1)}k "
                    f"written by the scorer (a rough estimate). Every new job is added to your "
                    f"sheet either way."
                ),
            }
        )
    )


def cmd_plan(args: argparse.Namespace) -> None:
    deduped = load_json(args.deduped)
    rules = _load_optional(args.rules, [])
    to_score, over_cap, blocked = plan_jobs(deduped["new"], rules, args.cap)
    args.run_dir.mkdir(parents=True, exist_ok=True)
    save_json(args.run_dir / "to_score.json", {"jobs": to_score, "count": len(to_score)})
    save_json(args.run_dir / "append.json", sheet_rows_for_plan(to_score, over_cap, blocked))
    print(
        f"{len(to_score)} to score, {len(over_cap)} over the limit, "
        f"{len(blocked)} blocked by title rules; wrote append.json and to_score.json"
    )


def cmd_resume_plan(args: argparse.Namespace) -> None:
    pending = sorted(
        load_json(args.pending), key=lambda j: j.get("posted_date") or "", reverse=True
    )
    save_json(
        args.run_dir / "to_score.json",
        {"jobs": pending[: args.cap], "count": len(pending[: args.cap])},
    )
    print(
        f"{min(len(pending), args.cap)} of {len(pending)} pending jobs queued; wrote to_score.json"
    )


def _write_batch(
    run_dir: Path, n: int, job_ids: list[str], jobs: dict[str, Any], words: int
) -> None:
    _batch_path(run_dir, n).write_text(
        batch_markdown(n, [jobs[i] for i in job_ids], words), encoding="utf-8"
    )


def cmd_make_batches(args: argparse.Namespace) -> None:
    from sheet import SheetClient

    config = load_config(args.profile)
    all_jobs = unwrap_jobs(load_json(args.jobs))
    scorable = [
        j for j in all_jobs if j.get("description") and not j.get("description_unavailable")
    ]
    unavailable = [j for j in all_jobs if j not in scorable]

    run_dir = args.run_dir
    rules = _load_optional(args.rules, [])
    prefs = _load_optional(args.prefs, [])
    (run_dir / "context.md").write_text(context_markdown(rules, prefs), encoding="utf-8")

    jobs_by_id = {str(j["job_id"]): j for j in scorable}
    ids = list(jobs_by_id)
    state = {
        "profile": args.profile,
        "max_words": config["description_max_words"],
        "batches": [],
    }
    for i in range(0, len(ids), BATCH_SIZE):
        n = len(state["batches"]) + 1
        chunk = ids[i : i + BATCH_SIZE]
        state["batches"].append({"n": n, "attempt": 1, "job_ids": chunk, "status": "todo"})
        _write_batch(run_dir, n, chunk, jobs_by_id, state["max_words"])
    save_json(_jobs_path(run_dir), jobs_by_id)
    save_json(_state_path(run_dir), state)

    if unavailable:
        rows = [{**_listing(j), "why": WHY_DESCRIPTION_UNAVAILABLE} for j in unavailable]
        SheetClient(args.profile).update_jobs(rows)
    print(
        f"{len(state['batches'])} batches ({len(ids)} jobs) ready; "
        f"{len(unavailable)} jobs left Pending because their description could not be fetched"
    )


def cmd_next(args: argparse.Namespace) -> None:
    batch = next_batch(load_json(_state_path(args.run_dir)))
    if batch is None:
        print(json.dumps({"done": True}))
        return
    n = batch["n"]
    print(
        json.dumps(
            {
                "batch": n,
                "jobs": len(batch["job_ids"]),
                "batch_file": str(_batch_path(args.run_dir, n).resolve()),
                "context_file": str((args.run_dir / "context.md").resolve()),
                "scores_file": str(_scores_path(args.run_dir, n).resolve()),
            }
        )
    )


def ingest_batch(client: Any, run_dir: Path, n: int) -> str:
    """Validate batch n's scores file, write what's valid to the sheet, queue retries."""
    state = load_json(_state_path(run_dir))
    batch = next(b for b in state["batches"] if b["n"] == n)
    scores_file = _scores_path(run_dir, n)
    text = scores_file.read_text(encoding="utf-8") if scores_file.exists() else ""
    valid, problems = parse_scores(text, set(batch["job_ids"]))

    if valid:
        client.update_jobs(valid)
    retry, failed = apply_ingest(state, n, {r["job_id"] for r in valid})
    jobs_by_id = load_json(_jobs_path(run_dir))
    if retry:
        retry_n = max(b["n"] for b in state["batches"])
        _write_batch(run_dir, retry_n, retry, jobs_by_id, state["max_words"])
    if failed:
        client.update_jobs([{"job_id": i, "why": WHY_SCORING_FAILED} for i in failed])
    save_json(_state_path(run_dir), state)

    summary = f"batch {n}: {len(valid)} scored"
    if retry:
        summary += f", {len(retry)} queued for one retry"
    if failed:
        summary += f", {len(failed)} could not be scored (left to retry next run)"
    if problems:
        summary += f"; ignored {len(problems)} bad result(s)"
    return summary


def cmd_ingest(args: argparse.Namespace) -> None:
    from sheet import SheetClient

    print(ingest_batch(SheetClient(args.profile), args.run_dir, args.batch))


def cmd_recover(args: argparse.Namespace) -> None:
    """Save scores that an earlier, interrupted run already produced but never reached the sheet."""
    from sheet import SheetClient

    client, saved = None, []
    for state_file in sorted((RUNS_DIR / args.profile).glob("*/state.json")):
        run_dir = state_file.parent
        for batch in load_json(state_file)["batches"]:
            scores = _scores_path(run_dir, batch["n"])
            if batch["status"] == "todo" and scores.exists() and scores.stat().st_size:
                client = client or SheetClient(args.profile)
                saved.append(f"{run_dir.name}: {ingest_batch(client, run_dir, batch['n'])}")
    print("\n".join(saved) if saved else "nothing to recover")


def top_scored(run_dir: Path, limit: int) -> list[dict[str, Any]]:
    """The best-scoring jobs of a run, read back from its saved scores files."""
    state_file, jobs_file = _state_path(run_dir), _jobs_path(run_dir)
    if not (state_file.exists() and jobs_file.exists()):
        return []
    jobs = load_json(jobs_file)
    scored = []
    for batch in load_json(state_file)["batches"]:
        scores = _scores_path(run_dir, batch["n"])
        if batch["status"] == "done" and scores.exists():
            valid, _ = parse_scores(scores.read_text(encoding="utf-8"), set(batch["job_ids"]))
            for r in valid:
                job = jobs.get(r["job_id"], {})
                scored.append(
                    {
                        "score": r["score"],
                        "title": job.get("title", ""),
                        "company": job.get("company", ""),
                        "link": job.get("link", ""),
                        "why": r["why"],
                    }
                )
    return sorted(scored, key=lambda r: r["score"], reverse=True)[:limit]


def cmd_new_run(args: argparse.Namespace) -> None:
    print(new_run_dir(args.profile))


def cmd_summary(args: argparse.Namespace) -> None:
    print(json.dumps(top_scored(args.run_dir, args.top), indent=2, ensure_ascii=False))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name: str, func: Any, **arguments: dict[str, Any]) -> None:
        p = sub.add_parser(name)
        for flag, options in arguments.items():
            p.add_argument(f"--{flag.replace('_', '-')}", **options)
        p.set_defaults(func=func)

    path, req = {"type": Path}, {"required": True}
    add(
        "checkpoint",
        cmd_checkpoint,
        profile=req,
        deduped={**path, **req},
        first_run={"action": "store_true"},
    )
    add(
        "plan",
        cmd_plan,
        deduped={**path, **req},
        rules={**path},
        cap={"type": int, **req},
        run_dir={**path, **req},
    )
    add(
        "resume-plan",
        cmd_resume_plan,
        pending={**path, **req},
        cap={"type": int, **req},
        run_dir={**path, **req},
    )
    add(
        "make-batches",
        cmd_make_batches,
        profile=req,
        run_dir={**path, **req},
        jobs={**path, **req},
        rules={**path},
        prefs={**path},
    )
    add("next", cmd_next, run_dir={**path, **req})
    add("ingest", cmd_ingest, profile=req, run_dir={**path, **req}, batch={"type": int, **req})
    add("recover", cmd_recover, profile=req)
    add("new-run", cmd_new_run, profile=req)
    add("summary", cmd_summary, run_dir={**path, **req}, top={"type": int, "default": 5})
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    sys.exit(main())
