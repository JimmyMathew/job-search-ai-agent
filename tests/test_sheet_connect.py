import config
import pytest
import sheet
from sheet_schema import JOBS_TAB, default_tab_to_adopt, sheet_title

SHEET_ID = "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"
# The fixture below replaces this on the class; keep the real one for the test that needs it.
REAL_GSPREAD_CLIENT = sheet.SheetClient._gspread_client


class FakeWorksheet:
    def __init__(self, title, rows=()):
        self.title = title
        self.rows = [list(r) for r in rows]
        self.frozen = 0

    def get_all_values(self):
        return self.rows

    def update_title(self, title):
        self.title = title

    def append_row(self, row):
        self.rows.append(list(row))

    def freeze(self, rows=0):
        self.frozen = rows


class FakeSpreadsheet:
    def __init__(self, sheet_id, title, worksheets):
        self.id = sheet_id
        self.title = title
        self._worksheets = worksheets

    def worksheets(self):
        return self._worksheets


class FakeResponse:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


class FakeHttp:
    def __init__(self, gspread_client):
        self._gc = gspread_client

    def request(self, method, url, params=None):
        file_id = url.rsplit("/", 1)[-1]
        return FakeResponse({"trashed": file_id in self._gc.trashed})


class FakeGspread:
    """Just enough of gspread.Client for connect(): a tiny in-memory Drive."""

    def __init__(self, existing=()):
        self.files = {s.id: s for s in existing}
        self.created = 0
        self.trashed = set()
        self.modified = {}
        self.http_client = FakeHttp(self)

    def list_spreadsheet_files(self, title=None):
        return [
            {"id": s.id, "name": s.title, "modifiedTime": self.modified.get(s.id, "")}
            for s in self.files.values()
            if s.title == title
        ]

    def open_by_key(self, key):
        return self.files[key]

    def create(self, title):
        self.created += 1
        new = FakeSpreadsheet(f"{SHEET_ID[:-1]}{self.created}", title, [FakeWorksheet("Sheet1")])
        self.files[new.id] = new
        return new


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROFILES_DIR", tmp_path)
    folder = tmp_path / "jane"
    folder.mkdir()
    (folder / "config.yaml").write_text(
        'locations: ["Berlin"]\ncredentials: "no-such-key.json"\n', encoding="utf-8"
    )
    gspread_client = FakeGspread()
    calls = {"init_tabs": 0, "test_write": 0}
    monkeypatch.setattr(
        sheet.SheetClient, "_gspread_client", lambda self, interactive=False: gspread_client
    )
    monkeypatch.setattr(
        sheet.SheetClient, "init_tabs", lambda self: calls.update(init_tabs=calls["init_tabs"] + 1)
    )
    monkeypatch.setattr(
        sheet.SheetClient,
        "test_write",
        lambda self: calls.update(test_write=calls["test_write"] + 1),
    )
    return gspread_client, calls


def test_connect_twice_creates_once(client):
    gc, calls = client
    first = sheet.SheetClient("jane").connect()
    second = sheet.SheetClient("jane").connect()  # a new process: only agent-state.json remains
    assert first == second
    assert gc.created == 1
    assert calls == {"init_tabs": 2, "test_write": 1}
    state = config.read_state("jane")
    assert state["sheet_id"] in first
    assert state["sheet_title"] == sheet_title("jane")
    assert state["auth"] == "oauth"


def test_connect_renames_the_empty_default_tab_to_jobs(client):
    gc, _ = client
    sheet.SheetClient("jane").connect()
    (only,) = next(iter(gc.files.values())).worksheets()
    assert only.title == JOBS_TAB
    assert only.rows[0][0] == "Date added"
    assert only.frozen == 1


def test_lost_state_adopts_the_sheet_it_created(client):
    gc, calls = client
    url = sheet.SheetClient("jane").connect()
    (config.PROFILES_DIR / "jane" / "agent-state.json").unlink()
    assert sheet.SheetClient("jane").connect() == url
    assert gc.created == 1
    assert config.read_state("jane")["sheet_id"] in url


def test_unrelated_sheets_with_the_same_title_are_not_adopted(client):
    gc, _ = client
    for i in (1, 2):
        gc.files[f"id{i}"] = FakeSpreadsheet(
            f"id{i}", sheet_title("jane"), [FakeWorksheet("Budget", [["x"]])]
        )
    sheet.SheetClient("jane").connect()
    assert gc.created == 1


def test_trashed_sheet_is_not_adopted(client):
    gc, _ = client
    gc.files["old"] = FakeSpreadsheet("old", sheet_title("jane"), [FakeWorksheet(JOBS_TAB)])
    gc.trashed.add("old")
    sheet.SheetClient("jane").connect()
    assert gc.created == 1
    assert config.read_state("jane")["sheet_id"] != "old"


def test_newest_of_several_own_sheets_is_adopted(client):
    gc, _ = client
    for name, stamp in (("older", "2026-01-01"), ("newer", "2026-06-01")):
        gc.files[name] = FakeSpreadsheet(name, sheet_title("jane"), [FakeWorksheet(JOBS_TAB)])
        gc.modified[name] = stamp
    sheet.SheetClient("jane").connect()
    assert gc.created == 0
    assert config.read_state("jane")["sheet_id"] == "newer"


def test_adopting_keeps_other_state_and_the_original_creation_time(client):
    gc, _ = client
    gc.files["mine"] = FakeSpreadsheet("mine", sheet_title("jane"), [FakeWorksheet(JOBS_TAB)])
    config.write_state("jane", {"created_at": "2026-01-01T00:00:00+00:00", "note": "keep me"})
    sheet.SheetClient("jane").connect()
    state = config.read_state("jane")
    assert state["created_at"] == "2026-01-01T00:00:00+00:00"
    assert state["note"] == "keep me"
    assert state["sheet_id"] == "mine"


def test_sheet_address_without_a_key_is_explained(client):
    folder = config.PROFILES_DIR / "jane"
    (folder / "config.yaml").write_text(
        f'locations: ["Berlin"]\nsheet: "{SHEET_ID}"\ncredentials: "no-such-key.json"\n',
        encoding="utf-8",
    )
    # Use the real client factory for this one (the fixture stubs it out).
    with pytest.raises(config.ProfileError, match="delete the"):
        REAL_GSPREAD_CLIENT(sheet.SheetClient("jane"))


def test_service_account_mode_without_sheet_is_explained(client, tmp_path):
    folder = config.PROFILES_DIR / "jane"
    (folder / "config.yaml").write_text(
        'locations: ["Berlin"]\nauth: service_account\n', encoding="utf-8"
    )
    with pytest.raises(config.ProfileError, match="service-account-setup"):
        sheet.SheetClient("jane").connect()


def test_no_sheet_yet_message(client, monkeypatch):
    with pytest.raises(config.ProfileError, match="--profile jane connect"):
        _ = sheet.SheetClient("jane").spreadsheet


class TestDefaultTabToAdopt:
    def test_single_empty_default_tab(self):
        assert default_tab_to_adopt(["Sheet1"], True)

    def test_default_tab_in_another_language(self):
        assert default_tab_to_adopt(["Hoja 1"], True)

    def test_default_tab_with_content_is_kept(self):
        assert not default_tab_to_adopt(["Sheet1"], False)

    def test_one_of_our_tabs_is_not_adopted(self):
        assert not default_tab_to_adopt([JOBS_TAB], True)

    def test_several_tabs(self):
        assert not default_tab_to_adopt(["Sheet1", "Other"], True)

    def test_no_tabs(self):
        assert not default_tab_to_adopt([], True)
