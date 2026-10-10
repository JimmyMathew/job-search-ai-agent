"""Google sign-in for the people who let the app create their sheet (OAuth, desktop flow).

    python scripts/google_auth.py --profile <name> login     # opens a browser, saves the token
    python scripts/google_auth.py --profile <name> status    # signed in? (never opens a browser)
    python scripts/google_auth.py --profile <name> logout    # forget the saved token

The only scope is drive.file: the app can see and edit files it created itself, and nothing
else in the person's Drive. Only `login` and `sheet.py connect` ever open a browser; every
other script calls get_credentials() non-interactively and fails with SignInNeeded instead,
because a browser popping up in the middle of a long unattended run helps nobody.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import sys
import tempfile
from typing import Any

from config import UserFacingError, oauth_client_path, token_path
from google.auth.exceptions import RefreshError, TransportError

SCOPE = "https://www.googleapis.com/auth/drive.file"
SIGN_IN_TIMEOUT_SECONDS = 300


class SignInNeeded(UserFacingError):
    """No usable Google sign-in for this profile; the message says how to get one."""


class NetworkProblem(UserFacingError):
    """Google could not be reached, so the saved sign-in could not be checked. Signing in again
    would not help, so this is kept apart from SignInNeeded."""


def _connect_hint(profile: str) -> str:
    return f"Google sign-in needed. Run: uv run python scripts/sheet.py --profile {profile} connect"


def _load_token(profile: str) -> Any | None:
    """The saved credentials, or None if there is no readable token file."""
    from google.oauth2.credentials import Credentials

    path = token_path(profile)
    if not path.exists():
        return None
    try:
        return Credentials.from_authorized_user_file(str(path))
    except (ValueError, KeyError, OSError):
        return None


def _save_token(profile: str, creds: Any) -> None:
    """Write the token atomically, readable only by this user where the OS supports it."""
    path = token_path(profile)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".token-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(creds.to_json())
        if os.name == "posix":
            os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _request() -> Any:
    from google.auth.transport.requests import Request

    return Request()


def _has_scope(creds: Any) -> bool:
    return SCOPE in (creds.scopes or [])


def _run_flow(profile: str) -> Any:
    """Open the browser sign-in and return the new credentials (also saved)."""
    client_path = oauth_client_path()
    if not client_path.exists():
        raise UserFacingError(
            f"Google sign-in is not available yet: the project owner has not added "
            f"{client_path.name} to the scripts folder. "
            "See docs/maintainer-oauth-client.md."
        )
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(str(client_path), [SCOPE])
    print(
        "A browser tab is opening for Google sign-in. Pick your account, then click "
        "Continue / Allow.",
        flush=True,
    )
    creds = flow.run_local_server(
        host="localhost",
        port=0,
        open_browser=True,
        timeout_seconds=SIGN_IN_TIMEOUT_SECONDS,
        authorization_prompt_message="If no browser opened, open this link: {url}",
        success_message="Signed in. Close this tab and return to VS Code.",
        prompt="consent",  # makes Google hand out a refresh token again on a re-login
    )
    _save_token(profile, creds)
    return creds


def get_credentials(profile: str, interactive: bool = False) -> Any:
    """Valid Google credentials for this profile.

    Reuses the saved token, refreshing it silently when it has expired. If there is no
    usable token: runs the browser sign-in when `interactive`, otherwise raises SignInNeeded."""
    creds = _load_token(profile)
    if creds is not None and _has_scope(creds):
        if creds.valid:
            return creds
        # Not valid covers an expired access token and a token file that has none at all;
        # either way a refresh token can get a new one without bothering the person.
        if creds.refresh_token:
            try:
                creds.refresh(_request())
            except RefreshError:
                pass  # revoked or expired for good: fall through to signing in again
            except TransportError as exc:
                raise NetworkProblem(
                    "Couldn't reach Google to check your sign-in. Check your internet "
                    "connection and try again."
                ) from exc
            else:
                _save_token(profile, creds)
                return creds
    if interactive:
        return _run_flow(profile)
    raise SignInNeeded(_connect_hint(profile))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("command", choices=["login", "status", "logout"])
    args = parser.parse_args()

    if args.command == "login":
        get_credentials(args.profile, interactive=True)
        print("Signed in.")
    elif args.command == "status":
        try:
            get_credentials(args.profile)
        except SignInNeeded as exc:
            print(f"Not signed in. {exc}")
            sys.exit(1)
        print("Signed in.")
    else:
        path = token_path(args.profile)
        if path.exists():
            path.unlink()
            print(
                "Signed out on this computer. To also revoke access, visit "
                "https://myaccount.google.com/permissions"
            )
        else:
            print("Nothing to do: no saved sign-in.")


if __name__ == "__main__":
    main()
