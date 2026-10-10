import google_auth
import pytest
from config import UserFacingError
from google.auth.exceptions import RefreshError, TransportError


class FakeCreds:
    def __init__(self, *, valid=True, expired=False, refresh_token="r", scopes=None, fail=False):
        self.valid = valid
        self.expired = expired
        self.refresh_token = refresh_token
        self.scopes = [google_auth.SCOPE] if scopes is None else scopes
        self.fail = fail
        self.refreshed = False

    def refresh(self, request):
        if self.fail:
            raise self.fail if isinstance(self.fail, Exception) else RefreshError("invalid_grant")
        self.refreshed = True
        self.valid = True

    def to_json(self):
        return "{}"


@pytest.fixture
def env(monkeypatch):
    """Stub out disk, network and browser; record what happened."""
    calls = {"flows": 0, "saved": 0}
    state = {"creds": None}

    def fake_flow(profile):
        calls["flows"] += 1
        return FakeCreds()

    monkeypatch.setattr(google_auth, "_load_token", lambda profile: state["creds"])
    monkeypatch.setattr(
        google_auth, "_save_token", lambda p, c: calls.update(saved=calls["saved"] + 1)
    )
    monkeypatch.setattr(google_auth, "_run_flow", fake_flow)
    monkeypatch.setattr(google_auth, "_request", lambda: object())
    return state, calls


def test_valid_token_is_returned_untouched(env):
    state, calls = env
    state["creds"] = FakeCreds()
    assert google_auth.get_credentials("jane") is state["creds"]
    assert calls == {"flows": 0, "saved": 0}


def test_expired_token_is_refreshed_and_saved(env):
    state, calls = env
    state["creds"] = FakeCreds(valid=False, expired=True)
    creds = google_auth.get_credentials("jane")
    assert creds.refreshed
    assert calls == {"flows": 0, "saved": 1}


def test_refresh_failure_raises_and_starts_no_flow(env):
    state, calls = env
    state["creds"] = FakeCreds(valid=False, expired=True, fail=True)
    with pytest.raises(google_auth.SignInNeeded, match="--profile jane connect"):
        google_auth.get_credentials("jane")
    assert calls["flows"] == 0


def test_missing_token_non_interactive_raises(env):
    _, calls = env
    with pytest.raises(google_auth.SignInNeeded):
        google_auth.get_credentials("jane")
    assert calls["flows"] == 0


def test_token_without_our_scope_needs_sign_in(env):
    state, calls = env
    state["creds"] = FakeCreds(scopes=["https://www.googleapis.com/auth/other"])
    with pytest.raises(google_auth.SignInNeeded):
        google_auth.get_credentials("jane")
    assert calls["flows"] == 0


def test_interactive_runs_the_flow_when_nothing_usable(env):
    state, calls = env
    state["creds"] = FakeCreds(valid=False, expired=True, fail=True)
    google_auth.get_credentials("jane", interactive=True)
    assert calls["flows"] == 1


def test_interactive_does_not_open_browser_when_token_is_fine(env):
    state, calls = env
    state["creds"] = FakeCreds()
    google_auth.get_credentials("jane", interactive=True)
    assert calls["flows"] == 0


def test_missing_client_file_is_explained(monkeypatch, tmp_path):
    monkeypatch.setenv("JOB_AGENT_OAUTH_CLIENT", str(tmp_path / "nope.json"))
    with pytest.raises(UserFacingError, match="maintainer-oauth-client"):
        google_auth._run_flow("jane")


def test_token_with_no_access_token_is_refreshed_not_discarded(env):
    state, calls = env
    state["creds"] = FakeCreds(valid=False, expired=False)  # e.g. a hand-edited token file
    creds = google_auth.get_credentials("jane")
    assert creds.refreshed
    assert calls == {"flows": 0, "saved": 1}


def test_offline_is_not_reported_as_needing_sign_in(env):
    state, calls = env
    state["creds"] = FakeCreds(valid=False, expired=True, fail=TransportError("offline"))
    with pytest.raises(google_auth.NetworkProblem, match="internet"):
        google_auth.get_credentials("jane", interactive=True)
    assert calls["flows"] == 0


def test_both_errors_are_user_facing():
    assert issubclass(google_auth.SignInNeeded, UserFacingError)
    assert issubclass(google_auth.NetworkProblem, UserFacingError)
