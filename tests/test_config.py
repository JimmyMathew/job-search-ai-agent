import config
import pytest
from config import (
    ProfileError,
    choose_auth,
    config_problems,
    find_resume,
    list_profiles,
    normalize_config,
    oauth_client_path,
    parse_sheet_id,
    read_state,
    token_path,
    write_state,
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

    def test_sheet_is_optional(self):
        assert config_problems(self.valid(sheet="")) == []

    def test_bad_auth_value(self):
        assert any("`auth`" in p for p in config_problems(self.valid(auth="password")))

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


class TestSheetFromYamlOrState:
    def test_state_supplies_sheet_when_yaml_has_none(self):
        cfg = normalize_config({"locations": ["Berlin"]}, "jane", {"sheet_id": SHEET_ID})
        assert cfg["sheet_id"] == SHEET_ID
        assert cfg["sheet_from_yaml"] is False

    def test_yaml_sheet_overrides_state(self):
        other = "9ZyXwVuTsRqPoNmLkJiHgFeDcBa0987654321"
        cfg = normalize_config({"sheet": SHEET_ID}, "jane", {"sheet_id": other})
        assert cfg["sheet_id"] == SHEET_ID
        assert cfg["sheet_from_yaml"] is True

    def test_placeholder_in_yaml_falls_back_to_state(self):
        raw = {"sheet": "PASTE YOUR GOOGLE SHEET ADDRESS HERE"}
        cfg = normalize_config(raw, "jane", {"sheet_id": SHEET_ID})
        assert cfg["sheet_id"] == SHEET_ID
        assert cfg["sheet_from_yaml"] is False

    def test_no_sheet_anywhere(self):
        assert normalize_config({}, "jane")["sheet_id"] is None

    def test_load_config_reads_state(self, profiles):
        folder = make_profile(profiles, "jane")
        (folder / "config.yaml").write_text('locations: ["Berlin"]\n', encoding="utf-8")
        write_state("jane", {"sheet_id": SHEET_ID})
        assert config.load_config("jane")["sheet_id"] == SHEET_ID


class TestState:
    def test_round_trip(self, profiles):
        make_profile(profiles, "jane")
        write_state("jane", {"sheet_id": SHEET_ID, "auth": "oauth"})
        assert read_state("jane") == {"sheet_id": SHEET_ID, "auth": "oauth"}
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


class TestChooseAuth:
    @pytest.mark.parametrize(
        ("raw", "key_exists", "expected"),
        [
            # dubai-bil: sheet set in config.yaml and a service-account key on disk
            ({"sheet_id": SHEET_ID}, True, "service_account"),
            ({"sheet": SHEET_ID}, True, "service_account"),
            # Sheet in yaml but no key: nothing else can open it, so sign in with Google
            ({"sheet": SHEET_ID}, False, "oauth"),
            # A new user: no sheet in yaml; a stray key must not change that
            ({}, True, "oauth"),
            ({}, False, "oauth"),
            # Explicit choice always wins
            ({"auth": "oauth", "sheet": SHEET_ID}, True, "oauth"),
            ({"auth": "service_account"}, False, "service_account"),
            ({"auth": "auto", "sheet": SHEET_ID}, True, "service_account"),
        ],
    )
    def test_matrix(self, raw, key_exists, expected):
        assert choose_auth(normalize_config(raw, "jane"), key_exists) == expected

    def test_sheet_from_state_alone_is_oauth(self):
        cfg = normalize_config({}, "jane", {"sheet_id": SHEET_ID})
        assert choose_auth(cfg, key_exists=True) == "oauth"


class TestPaths:
    def test_token_path_is_per_profile_under_secrets(self):
        path = token_path("jane")
        assert path.name == "jane.json"
        assert path.parent.name == "tokens"
        assert path.parent.parent.name == "secrets"

    def test_oauth_client_default_and_override(self, monkeypatch, tmp_path):
        monkeypatch.delenv("JOB_AGENT_OAUTH_CLIENT", raising=False)
        assert oauth_client_path().name == "google_oauth_client.json"
        monkeypatch.setenv("JOB_AGENT_OAUTH_CLIENT", str(tmp_path / "x.json"))
        assert oauth_client_path() == tmp_path / "x.json"
