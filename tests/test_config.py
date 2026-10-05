import config
import pytest
from config import (
    ProfileError,
    config_problems,
    find_resume,
    list_profiles,
    normalize_config,
    parse_sheet_id,
)

SHEET_ID = "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"


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


class TestParseSheetId:
    def test_extracts_id_from_url(self):
        url = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit#gid=0"
        assert parse_sheet_id(url) == SHEET_ID

    def test_accepts_bare_id(self):
        assert parse_sheet_id(SHEET_ID) == SHEET_ID

    @pytest.mark.parametrize(
        "value", [None, "", "  ", "PASTE YOUR GOOGLE SHEET ADDRESS HERE", "<id>"]
    )
    def test_unset_or_placeholder_is_none(self, value):
        assert parse_sheet_id(value) is None


class TestNormalizeConfig:
    def test_applies_defaults(self):
        cfg = normalize_config({"locations": ["Berlin"], "sheet": SHEET_ID}, "jane")
        assert cfg["max_jobs_to_score_per_run"] == 60
        assert cfg["name"] == "jane"
        assert cfg["sheet_id"] == SHEET_ID

    def test_blank_value_keeps_default(self):
        cfg = normalize_config({"first_run_hours_old": None}, "jane")
        assert cfg["first_run_hours_old"] == 72

    def test_accepts_legacy_keys(self):
        cfg = normalize_config({"sheet_id": SHEET_ID, "target_roles": ["Nurse"]}, "jane")
        assert cfg["sheet_id"] == SHEET_ID
        assert cfg["roles"] == ["Nurse"]

    def test_single_location_string_becomes_list(self):
        assert normalize_config({"locations": "Remote"}, "jane")["locations"] == ["Remote"]


class TestConfigProblems:
    def valid(self, **overrides):
        raw = {"locations": ["Berlin, Germany"], "sheet": SHEET_ID, **overrides}
        return normalize_config(raw, "jane")

    def test_valid_config_has_no_problems(self):
        assert config_problems(self.valid()) == []

    def test_missing_locations(self):
        assert any("locations" in p for p in config_problems(self.valid(locations=[])))

    def test_placeholder_location(self):
        problems = config_problems(self.valid(locations=["YOUR CITY, COUNTRY"]))
        assert any("placeholder" in p for p in problems)

    def test_missing_sheet(self):
        assert any("`sheet` is not set" in p for p in config_problems(self.valid(sheet="")))

    def test_malformed_sheet(self):
        assert any("doesn't look like" in p for p in config_problems(self.valid(sheet="oops")))

    @pytest.mark.parametrize("value", [0, -5, "many"])
    def test_bad_number(self, value):
        problems = config_problems(self.valid(max_jobs_to_score_per_run=value))
        assert any("max_jobs_to_score_per_run" in p for p in problems)


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
