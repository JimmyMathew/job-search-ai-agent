import config
import pytest
from config import (
    ProfileError,
    config_problems,
    find_resume,
    list_profiles,
    normalize_config,
    read_state,
    workbook_path,
    write_state,
)


@pytest.fixture
def profiles(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROFILES_DIR", tmp_path)
    return tmp_path


def make_profile(root, name, files=()):
    folder = root / name
    folder.mkdir()
    for file in files:
        (folder / file).write_text("x", encoding="utf-8")
    return folder


class TestNormalizeConfig:
    def test_applies_defaults(self):
        cfg = normalize_config({"locations": ["Berlin"]}, "jane")
        assert cfg["max_jobs_to_score_per_run"] == 60
        assert cfg["name"] == "jane"
        assert cfg["deprecated_keys"] == []

    def test_blank_value_keeps_default(self):
        cfg = normalize_config({"first_run_hours_old": None}, "jane")
        assert cfg["first_run_hours_old"] == 72

    def test_accepts_legacy_roles_key(self):
        assert normalize_config({"target_roles": ["Nurse"]}, "jane")["roles"] == ["Nurse"]

    def test_single_location_string_becomes_list(self):
        assert normalize_config({"locations": "Remote"}, "jane")["locations"] == ["Remote"]

    def test_workbook_setting_is_kept(self):
        assert normalize_config({"workbook": "~/jobs.xlsx"}, "jane")["workbook"] == "~/jobs.xlsx"


class TestOldGoogleSettings:
    """A config.yaml written for the Google Sheets version must keep working."""

    OLD = {
        "locations": ["Dubai"],
        "sheet": "https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789/edit",
        "auth": "service_account",
        "credentials": "secrets/service-account.json",
    }

    def test_old_keys_are_ignored_and_listed(self):
        cfg = normalize_config(self.OLD, "dubai")
        assert cfg["deprecated_keys"] == ["sheet", "auth", "credentials"]
        for key in ("sheet", "sheet_id", "auth", "credentials"):
            assert key not in cfg
        assert config_problems(cfg) == []

    def test_the_old_sheet_id_key_is_deprecated_too(self):
        assert normalize_config({"sheet_id": "abc"}, "jane")["deprecated_keys"] == ["sheet_id"]

    def test_empty_old_keys_do_not_count(self):
        raw = {"sheet": None, "auth": "", "credentials": None}
        assert normalize_config(raw, "jane")["deprecated_keys"] == []

    def test_load_config_does_not_crash_on_an_old_file(self, profiles):
        folder = make_profile(profiles, "dubai")
        lines = ['locations: ["Dubai"]', 'sheet: "https://docs.google.com/x"', "auth: oauth"]
        (folder / "config.yaml").write_text("\n".join(lines), encoding="utf-8")
        cfg = config.load_config("dubai")
        assert cfg["deprecated_keys"] == ["sheet", "auth"]
        assert cfg["locations"] == ["Dubai"]


class TestConfigProblems:
    def valid(self, **overrides):
        return normalize_config({"locations": ["Berlin, Germany"], **overrides}, "jane")

    def test_valid_config_has_no_problems(self):
        assert config_problems(self.valid()) == []

    def test_missing_locations(self):
        assert any("locations" in p for p in config_problems(self.valid(locations=[])))

    def test_placeholder_location(self):
        problems = config_problems(self.valid(locations=["YOUR CITY, COUNTRY"]))
        assert any("placeholder" in p for p in problems)

    @pytest.mark.parametrize("value", [0, -5, "many"])
    def test_bad_number(self, value):
        problems = config_problems(self.valid(max_jobs_to_score_per_run=value))
        assert any("max_jobs_to_score_per_run" in p for p in problems)

    @pytest.mark.parametrize("value", ["~/jobs.xlsx", "C:/Lists/Mine.XLSX", "lists/jobs.xlsx"])
    def test_a_workbook_path_is_fine(self, value):
        assert config_problems(self.valid(workbook=value)) == []

    @pytest.mark.parametrize("value", ["", "   ", "jobs.csv", "jobs", 5, ["a.xlsx"]])
    def test_a_workbook_that_is_not_an_excel_path_is_reported(self, value):
        problems = config_problems(self.valid(workbook=value))
        assert any("`workbook`" in p for p in problems)


class TestWorkbookPath:
    def test_defaults_to_a_named_file_in_the_profile_folder(self, profiles):
        assert workbook_path("jane", {}) == profiles / "jane" / "Job Search - jane.xlsx"

    def test_a_relative_path_starts_at_the_profile_folder(self, profiles):
        path = workbook_path("jane", {"workbook": "lists/mine.xlsx"})
        assert path == profiles / "jane" / "lists" / "mine.xlsx"

    def test_an_absolute_path_is_used_as_it_is(self, profiles, tmp_path):
        target = tmp_path / "elsewhere" / "mine.xlsx"
        assert workbook_path("jane", {"workbook": str(target)}) == target

    def test_a_home_folder_path_is_expanded(self, profiles, tmp_path, monkeypatch):
        home = tmp_path / "home"
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("USERPROFILE", str(home))
        assert workbook_path("jane", {"workbook": "~/jobs.xlsx"}) == home / "jobs.xlsx"

    @pytest.mark.parametrize("blank", [None, "", "  "])
    def test_blank_means_the_default(self, profiles, blank):
        assert workbook_path("jane", {"workbook": blank}).name == "Job Search - jane.xlsx"

    def test_needs_no_files_to_exist(self, profiles):
        assert not (profiles / "jane").exists()
        workbook_path("jane", {})

    def test_backups_live_in_the_profile_folder(self, profiles):
        make_profile(profiles, "jane")
        assert config.backups_dir("jane") == profiles / "jane" / "backups"


class TestProfiles:
    def test_list_skips_template_and_hidden(self, profiles):
        for name in ("jane", "_example", ".hidden"):
            make_profile(profiles, name)
        assert list_profiles() == ["jane"]

    def test_missing_profile_error_lists_existing(self, profiles):
        make_profile(profiles, "jane")
        with pytest.raises(ProfileError, match="jane"):
            config.profile_dir("nobody")

    def test_find_resume_any_filename(self, profiles):
        make_profile(profiles, "jane", ["My Career.pdf", "config.yaml"])
        assert find_resume("jane").name == "My Career.pdf"

    def test_find_resume_prefers_resume_named_file(self, profiles):
        make_profile(profiles, "jane", ["Cover letter.docx", "Jane Resume.pdf"])
        assert find_resume("jane").name == "Jane Resume.pdf"

    def test_find_resume_ignores_files_we_write(self, profiles):
        make_profile(profiles, "jane", ["profile.md", "PUT-YOUR-RESUME-HERE.txt"])
        assert find_resume("jane") is None


class TestState:
    def test_round_trip(self, profiles):
        make_profile(profiles, "jane")
        write_state("jane", {"workbook": "/x/jobs.xlsx", "created_at": "2026-10-01"})
        assert read_state("jane") == {"workbook": "/x/jobs.xlsx", "created_at": "2026-10-01"}
        assert [p.name for p in (profiles / "jane").iterdir()] == ["agent-state.json"]

    def test_overwrite(self, profiles):
        make_profile(profiles, "jane")
        write_state("jane", {"a": 1})
        write_state("jane", {"b": 2})
        assert read_state("jane") == {"b": 2}

    def test_missing_is_empty(self, profiles):
        make_profile(profiles, "jane")
        assert read_state("jane") == {}

    def test_corrupt_is_empty(self, profiles):
        folder = make_profile(profiles, "jane")
        (folder / "agent-state.json").write_text("{not json", encoding="utf-8")
        assert read_state("jane") == {}
