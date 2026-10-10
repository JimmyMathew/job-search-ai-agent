import json

import pipeline
import pytest
from sheet_schema import WHY_OVER_CAP, WHY_PENDING, WHY_SCORING_FAILED, needs_scoring, parse_scores


def make_job(i, title="Analyst", posted="2026-10-01", description="Some description"):
    return {
        "job_id": str(i),
        "title": title,
        "company": "Acme",
        "location": "Berlin",
        "posted_date": posted,
        "link": f"https://x/jobs/view/{i}",
        "description": description,
    }


BLOCK_SALES = [{"rule": "No sales", "type": "Block", "keywords": "sales", "active": "Yes"}]


class FakeClient:
    def __init__(self):
        self.updates = []

    def update_jobs(self, jobs):
        self.updates.extend(jobs)
        return len(jobs)


class TestPlanJobs:
    def test_newest_first_and_cap(self):
        jobs = [
            make_job(1, posted="2026-10-01"),
            make_job(2, posted="2026-10-05"),
            make_job(3, posted="2026-10-03"),
        ]
        to_score, over_cap, blocked = pipeline.plan_jobs(jobs, [], cap=2)
        assert [j["job_id"] for j in to_score] == ["2", "3"]
        assert [j["job_id"] for j in over_cap] == ["1"]
        assert blocked == []

    def test_title_blocked_jobs_do_not_use_the_cap(self):
        jobs = [make_job(1, title="Sales Rep", posted="2026-10-09"), make_job(2), make_job(3)]
        to_score, over_cap, blocked = pipeline.plan_jobs(jobs, BLOCK_SALES, cap=2)
        assert len(to_score) == 2
        assert over_cap == []
        assert [j["job_id"] for j in blocked] == ["1"]

    def test_missing_dates_sort_last(self):
        jobs = [make_job(1, posted=None), make_job(2, posted="2026-10-01")]
        to_score, _, _ = pipeline.plan_jobs(jobs, [], cap=1)
        assert to_score[0]["job_id"] == "2"

    def test_sheet_rows_carry_state_markers(self):
        to_score, over_cap, blocked = pipeline.plan_jobs(
            [make_job(1, title="Sales Rep"), make_job(2), make_job(3, posted="2026-01-01")],
            BLOCK_SALES,
            cap=1,
        )
        rows = {r["job_id"]: r for r in pipeline.sheet_rows_for_plan(to_score, over_cap, blocked)}
        assert rows["2"]["why"] == WHY_PENDING
        assert rows["3"]["why"] == WHY_OVER_CAP
        assert "Blocked by rule" in rows["1"]["filtered_reason"]
        assert "description" not in rows["2"]


class TestEstimate:
    def test_scales_with_jobs_and_batches(self):
        small, big = pipeline.estimate_tokens(10), pipeline.estimate_tokens(60)
        assert small["batches"] == 1
        assert big["batches"] == 6
        assert big["tokens_in_k"] > small["tokens_in_k"] * 5

    def test_zero_jobs(self):
        assert pipeline.estimate_tokens(0)["batches"] == 0


class TestMarkdown:
    def test_batch_markdown_wraps_long_lines_and_lists_every_job(self):
        long_line = "word " * 200
        text = pipeline.batch_markdown(1, [make_job(1, description=long_line), make_job(2)], 1500)
        assert "## Job 1" in text
        assert "## Job 2" in text
        assert max(len(line) for line in text.splitlines()) <= pipeline.WRAP_WIDTH

    def test_description_is_trimmed(self):
        text = pipeline.batch_markdown(1, [make_job(1, description="w " * 50)], 5)
        assert text.count("w") < 10

    def test_context_includes_only_active_lower_and_boost_rules(self):
        rules = [
            {"rule": "Avoid X", "type": "Lower", "keywords": "x", "active": "Yes"},
            {"rule": "Off", "type": "Lower", "keywords": "y", "active": "No"},
            {"rule": "Blocked", "type": "Block", "keywords": "z", "active": "Yes"},
        ]
        text = pipeline.context_markdown(rules, [{"criterion": "Remote", "value": "yes"}])
        assert "Avoid X" in text
        assert "Off" not in text
        assert "Blocked" not in text
        assert "Remote: yes" in text

    def test_context_with_nothing(self):
        text = pipeline.context_markdown([], [])
        assert "(none)" in text
        assert "(none stated)" in text


class TestApplyIngest:
    def state(self, attempt=1):
        return {
            "batches": [{"n": 1, "attempt": attempt, "job_ids": ["a", "b", "c"], "status": "todo"}]
        }

    def test_all_scored(self):
        state = self.state()
        assert pipeline.apply_ingest(state, 1, {"a", "b", "c"}) == ([], [])
        assert state["batches"][0]["status"] == "done"
        assert len(state["batches"]) == 1

    def test_missing_jobs_get_one_retry_batch(self):
        state = self.state()
        retry, failed = pipeline.apply_ingest(state, 1, {"a"})
        assert (retry, failed) == (["b", "c"], [])
        queued = state["batches"][-1]
        assert queued["n"] == 2
        assert queued["attempt"] == 2
        assert queued["job_ids"] == ["b", "c"]
        assert pipeline.next_batch(state)["n"] == 2

    def test_second_miss_is_marked_failed(self):
        state = self.state(attempt=2)
        assert pipeline.apply_ingest(state, 1, {"a"}) == ([], ["b", "c"])
        assert pipeline.next_batch(state) is None


class TestParseScores:
    ids = {"1", "2", "3"}

    def line(self, job_id, **extra):
        return json.dumps(
            {"job_id": job_id, "score": 70, "why": "ok", "role_type": "target", **extra}
        )

    def test_json_lines(self):
        valid, problems = parse_scores(self.line("1") + "\n" + self.line("2"), self.ids)
        assert [r["job_id"] for r in valid] == ["1", "2"]
        assert problems == []

    def test_array_with_leading_commas_and_code_fence(self):
        text = "```json\n[" + self.line("1") + "\n," + self.line("2") + "]\n```"
        valid, _ = parse_scores(text, self.ids)
        assert len(valid) == 2

    def test_lines_with_leading_commas(self):
        valid, _ = parse_scores(self.line("1") + "\n," + self.line("2"), self.ids)
        assert len(valid) == 2

    @pytest.mark.parametrize(
        "bad",
        [
            {"job_id": "99", "score": 50, "why": "x"},
            {"job_id": "1", "score": 150, "why": "x"},
            {"job_id": "1", "score": "high", "why": "x"},
            {"job_id": "1", "score": 50, "why": "  "},
        ],
    )
    def test_rejects_invalid_results(self, bad):
        valid, problems = parse_scores(json.dumps(bad), self.ids)
        assert valid == []
        assert len(problems) == 1

    def test_duplicate_results_keep_the_first(self):
        valid, problems = parse_scores(self.line("1") + "\n" + self.line("1"), self.ids)
        assert len(valid) == 1
        assert len(problems) == 1

    def test_unknown_role_type_becomes_adjacent_and_float_score_rounds(self):
        text = json.dumps({"job_id": "1", "score": 71.6, "why": "ok", "role_type": "weird"})
        valid, _ = parse_scores(text, self.ids)
        assert valid[0]["role_type"] == "adjacent"
        assert valid[0]["score"] == 72

    def test_garbage_is_reported_not_raised(self):
        valid, problems = parse_scores("not json at all", self.ids)
        assert valid == []
        assert problems

    def test_empty_text(self):
        assert parse_scores("", self.ids) == ([], [])


class TestNeedsScoring:
    @pytest.mark.parametrize(
        ("score", "why", "expected"),
        [
            ("", WHY_PENDING, True),
            ("", WHY_SCORING_FAILED, True),
            ("", WHY_OVER_CAP, False),
            ("", "Blocked by rule: No sales (matched 'sales')", False),
            (80, WHY_PENDING, False),
            ("", "", False),
        ],
    )
    def test_cases(self, score, why, expected):
        assert needs_scoring(score, why) is expected


class TestIngestBatch:
    def prepare(self, tmp_path, job_ids):
        jobs = {i: make_job(i) for i in job_ids}
        pipeline.save_json(pipeline._jobs_path(tmp_path), jobs)
        state = {
            "max_words": 1500,
            "batches": [{"n": 1, "attempt": 1, "job_ids": job_ids, "status": "todo"}],
        }
        pipeline.save_json(pipeline._state_path(tmp_path), state)

    def write_scores(self, tmp_path, n, ids):
        lines = [
            json.dumps({"job_id": i, "score": 60, "why": "ok", "role_type": "target"}) for i in ids
        ]
        pipeline._scores_path(tmp_path, n).write_text("\n".join(lines), encoding="utf-8")

    def test_partial_scores_are_saved_and_the_rest_retried(self, tmp_path):
        self.prepare(tmp_path, ["1", "2", "3"])
        self.write_scores(tmp_path, 1, ["1", "2"])
        client = FakeClient()
        summary = pipeline.ingest_batch(client, tmp_path, 1)
        assert [u["job_id"] for u in client.updates] == ["1", "2"]
        assert "2 scored" in summary
        assert "1 queued for one retry" in summary
        assert pipeline._batch_path(tmp_path, 2).exists()
        assert "## Job 3" in pipeline._batch_path(tmp_path, 2).read_text(encoding="utf-8")

    def test_missing_scores_file_queues_everything_for_retry(self, tmp_path):
        self.prepare(tmp_path, ["1", "2"])
        client = FakeClient()
        pipeline.ingest_batch(client, tmp_path, 1)
        assert client.updates == []
        state = json.loads(pipeline._state_path(tmp_path).read_text(encoding="utf-8"))
        assert state["batches"][-1]["job_ids"] == ["1", "2"]

    def test_second_failure_marks_jobs_for_next_run(self, tmp_path):
        self.prepare(tmp_path, ["1"])
        client = FakeClient()
        pipeline.ingest_batch(client, tmp_path, 1)  # queues retry as batch 2
        pipeline.ingest_batch(client, tmp_path, 2)  # still nothing
        assert client.updates == [{"job_id": "1", "why": WHY_SCORING_FAILED}]


class TestTopScored:
    def test_reads_back_best_scores_from_done_batches(self, tmp_path):
        jobs = {i: make_job(i, title=f"Job {i}") for i in ("1", "2", "3")}
        pipeline.save_json(pipeline._jobs_path(tmp_path), jobs)
        pipeline.save_json(
            pipeline._state_path(tmp_path),
            {"batches": [{"n": 1, "attempt": 1, "job_ids": ["1", "2", "3"], "status": "done"}]},
        )
        lines = [
            json.dumps({"job_id": i, "score": s, "why": "ok", "role_type": "target"})
            for i, s in (("1", 40), ("2", 90), ("3", 65))
        ]
        pipeline._scores_path(tmp_path, 1).write_text("\n".join(lines), encoding="utf-8")
        top = pipeline.top_scored(tmp_path, 2)
        assert [t["title"] for t in top] == ["Job 2", "Job 3"]

    def test_no_state_means_no_results(self, tmp_path):
        assert pipeline.top_scored(tmp_path, 5) == []
