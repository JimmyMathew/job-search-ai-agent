from sheet_schema import JOBS_HEADERS, find_changed_status_rows, job_to_row

JOB = {
    "job_id": "42",
    "title": "Analyst",
    "company": "Acme",
    "location": "Berlin",
    "posted_date": "2026-09-01",
    "link": "https://x/jobs/view/42",
    "score": 80,
    "why": "Strong overlap",
    "gaps": "",
    "flags": ["onsite_only", "language_required"],
    "role_type": "target",
}


class TestJobToRow:
    def test_follows_jobs_header_order(self):
        values = job_to_row(JOB, JOBS_HEADERS, "2026-10-01")
        row = dict(zip(JOBS_HEADERS, values, strict=True))
        assert row["Date added"] == "2026-10-01"
        assert row["Job ID"] == "42"
        assert row["Score"] == 80
        assert row["Flags"] == "onsite_only, language_required"
        assert row["Status"] == "New"
        assert row["Reason"] == ""

    def test_follows_the_sheets_own_column_order(self):
        headers = ["Job ID", "Brand New Column", "Score", "Status"]
        assert job_to_row(JOB, headers, "d") == ["42", "", 80, "New"]

    def test_blocked_job_reason_goes_in_why_column(self):
        blocked = {"job_id": "7", "filtered_reason": "Blocked by rule: X"}
        row = dict(zip(JOBS_HEADERS, job_to_row(blocked, JOBS_HEADERS, "d"), strict=True))
        assert row["Why it matches"] == "Blocked by rule: X"
        assert row["Score"] == ""


class TestChangedStatusRows:
    def row(self, job_id, status, reason=""):
        return {
            "Job ID": job_id,
            "Status": status,
            "Reason": reason,
            "Job title": "T",
            "Company": "C",
        }

    def test_fresh_new_rows_are_not_feedback(self):
        assert find_changed_status_rows([self.row("1", "New")], {}) == []

    def test_new_status_value_is_feedback(self):
        changed = find_changed_status_rows([self.row("1", "Not for me", "too junior")], {})
        assert [c["job_id"] for c in changed] == ["1"]
        assert changed[0]["reason"] == "too junior"

    def test_already_synced_row_is_not_reported(self):
        snapshot = {"1": "Not for me||too junior"}
        rows = [self.row("1", "Not for me", "too junior")]
        assert find_changed_status_rows(rows, snapshot) == []

    def test_edited_reason_is_reported(self):
        snapshot = {"1": "Not for me||too junior"}
        rows = [self.row("1", "Not for me", "wrong industry")]
        assert len(find_changed_status_rows(rows, snapshot)) == 1

    def test_revert_to_new_after_feedback_is_reported(self):
        snapshot = {"1": "Not for me||too junior"}
        assert len(find_changed_status_rows([self.row("1", "New")], snapshot)) == 1

    def test_rows_without_id_or_status_are_ignored(self):
        rows = [self.row("", "Applied"), self.row("2", "")]
        assert find_changed_status_rows(rows, {}) == []
