from datetime import datetime, timedelta, timezone

from util import extract_job_id, hours_since, is_repost, trim_description, unwrap_jobs


def test_extract_job_id():
    assert extract_job_id("https://www.linkedin.com/jobs/view/4447358729?x=1") == "4447358729"
    assert extract_job_id("https://example.com/other") is None
    assert extract_job_id("") is None


def test_trim_description_short_text_unchanged():
    assert trim_description("  a short text ", max_words=10) == "a short text"


def test_trim_description_cuts_long_text():
    text = " ".join(f"w{i}" for i in range(100))
    assert trim_description(text, max_words=5) == "w0 w1 w2 w3 w4 …"


def test_unwrap_jobs_accepts_list_or_dict():
    assert unwrap_jobs([{"a": 1}]) == [{"a": 1}]
    assert unwrap_jobs({"jobs": [{"a": 1}], "count": 1}) == [{"a": 1}]


class TestIsRepost:
    existing = [{"title": "Data Analyst", "company": "Acme", "posted_date": "2026-09-01"}]

    def test_same_company_and_title_within_window(self):
        assert is_repost("data analyst ", "ACME", "2026-09-20", self.existing)

    def test_outside_window(self):
        assert not is_repost("Data Analyst", "Acme", "2026-12-01", self.existing)

    def test_different_company(self):
        assert not is_repost("Data Analyst", "Other", "2026-09-05", self.existing)

    def test_missing_or_bad_date_is_not_a_repost(self):
        assert not is_repost("Data Analyst", "Acme", None, self.existing)
        assert not is_repost("Data Analyst", "Acme", "not a date", self.existing)


class TestHoursSince:
    def test_unknown_uses_default(self):
        assert hours_since(None, 72) == 72
        assert hours_since("garbage", 72) == 72

    def test_rounds_up_and_is_at_least_one(self):
        recent = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        assert hours_since(recent, 72) == 1
        two_hours = (datetime.now(timezone.utc) - timedelta(hours=2, minutes=1)).isoformat()
        assert hours_since(two_hours, 72) == 3

    def test_naive_timestamp_is_treated_as_utc(self):
        naive = (datetime.now(timezone.utc) - timedelta(hours=1)).replace(tzinfo=None).isoformat()
        assert hours_since(naive, 72) in (2, 3)
