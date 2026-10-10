import config
import doctor
import google_auth
import new_profile
import pipeline
import pytest
from config import UserFacingError, looks_like_resume_file
from util import run_cli


class TestRunCli:
    def test_user_facing_errors_end_with_the_plain_message(self):
        def main():
            raise UserFacingError("Please sign in again.")

        with pytest.raises(SystemExit) as exit_info:
            run_cli(main)
        assert str(exit_info.value) == "Please sign in again."

    def test_other_errors_still_raise(self):
        def main():
            raise ValueError("a real bug")

        with pytest.raises(ValueError):
            run_cli(main)

    def test_known_errors_are_user_facing(self):
        assert issubclass(config.ProfileError, UserFacingError)
        assert issubclass(google_auth.SignInNeeded, UserFacingError)


class TestLooksLikeResumeFile:
    @pytest.mark.parametrize("name", ["Jane Resume.pdf", "cv.DOCX", "notes.txt"])
    def test_documents_are_accepted(self, tmp_path, name):
        assert looks_like_resume_file(tmp_path / name)

    @pytest.mark.parametrize(
        "name", ["profile.md", "README.md", "readme.txt", "config.yaml", "x.png"]
    )
    def test_our_own_files_and_other_types_are_not(self, tmp_path, name):
        assert not looks_like_resume_file(tmp_path / name)


class TestNewProfileResume:
    def test_rejects_a_file_that_would_clash_with_ours(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(config, "PROFILES_DIR", tmp_path / "profiles")
        monkeypatch.setattr(new_profile, "PROFILES_DIR", tmp_path / "profiles")
        bad = tmp_path / "profile.md"
        bad.write_text("x", encoding="utf-8")
        monkeypatch.setattr("sys.argv", ["new_profile.py", "jane", "--resume", str(bad)])
        with pytest.raises(SystemExit, match="doesn't look like a resume"):
            new_profile.main()
        assert not (tmp_path / "profiles" / "jane").exists()

    def test_drop_folder_ignores_the_readme_and_needs_exactly_one_file(self, tmp_path, monkeypatch):
        drop = tmp_path / "resume"
        drop.mkdir()
        monkeypatch.setattr(new_profile, "DROP_FOLDER", drop)
        (drop / "README.txt").write_text("hi", encoding="utf-8")
        assert new_profile.drop_folder_resume() is None
        (drop / "Jane.pdf").write_text("x", encoding="utf-8")
        assert new_profile.drop_folder_resume().name == "Jane.pdf"
        (drop / "Other.docx").write_text("x", encoding="utf-8")
        assert new_profile.drop_folder_resume() is None


class TestSplitScorable:
    def test_partitions_in_one_pass_and_keeps_order(self):
        jobs = [
            {"job_id": "1", "description": "text"},
            {"job_id": "2", "description": ""},
            {"job_id": "3", "description": "text", "description_unavailable": True},
            {"job_id": "4", "description": "more"},
        ]
        scorable, unavailable = pipeline.split_scorable(jobs)
        assert [j["job_id"] for j in scorable] == ["1", "4"]
        assert [j["job_id"] for j in unavailable] == ["2", "3"]


class TestDoctorOauthCheck:
    def stub(self, monkeypatch, tmp_path, *, client_exists, outcome):
        client = tmp_path / "client.json"
        if client_exists:
            client.write_text("{}", encoding="utf-8")
        monkeypatch.setattr(doctor, "oauth_client_path", lambda: client)

        def fake_get_credentials(profile, interactive=False):
            if outcome is not None:
                raise outcome

        monkeypatch.setattr(google_auth, "get_credentials", fake_get_credentials)
        reports = []
        monkeypatch.setattr(
            doctor, "report", lambda level, msg, fix="": reports.append((level, msg))
        )
        return reports

    def test_saved_sign_in_works_even_without_the_client_file(self, monkeypatch, tmp_path):
        reports = self.stub(monkeypatch, tmp_path, client_exists=False, outcome=None)
        assert doctor.check_oauth_ready("jane", {"sheet_id": "abc"})
        assert not [r for r in reports if r[0] == "FIX"]

    def test_missing_client_file_matters_only_when_signing_in_is_needed(
        self, monkeypatch, tmp_path
    ):
        reports = self.stub(
            monkeypatch, tmp_path, client_exists=False, outcome=google_auth.SignInNeeded("x")
        )
        assert not doctor.check_oauth_ready("jane", {"sheet_id": "abc"})
        assert any("isn't available" in msg for level, msg in reports)

    def test_offline_is_reported_as_a_connection_problem(self, monkeypatch, tmp_path):
        reports = self.stub(
            monkeypatch,
            tmp_path,
            client_exists=True,
            outcome=google_auth.NetworkProblem("Couldn't reach Google"),
        )
        assert not doctor.check_oauth_ready("jane", {"sheet_id": "abc"})
        assert any("reach Google" in msg for level, msg in reports)
