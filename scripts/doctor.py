"""Check that a profile is ready to run, and say exactly how to fix anything that isn't.

    python scripts/doctor.py --profile jane-doe
    python scripts/doctor.py --profile jane-doe --online    # also tries one real LinkedIn search

Prints OK / WARN / FIX per check. Exits 1 if anything needs fixing.
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys

from config import (
    PROFILES_DIR,
    ProfileError,
    choose_auth,
    credentials_path,
    find_resume,
    load_config,
    oauth_client_path,
    profile_dir,
)

REQUIRED_PACKAGES = {
    "jobspy": "python-jobspy",
    "gspread": "gspread",
    "google.auth": "google-auth",
    "google_auth_oauthlib": "google-auth-oauthlib",
    "yaml": "PyYAML",
}

failures = 0


def report(level: str, message: str, fix: str = "") -> None:
    global failures
    if level == "FIX":
        failures += 1
    print(f"[{level:>4}] {message}")
    for line in fix.splitlines():
        print(f"         {line}")


def check_service_account_key(key_path) -> str | None:
    """Report on the service-account key; returns its client_email if it is usable."""
    if not key_path.exists():
        report(
            "FIX",
            f"Google key not found at {key_path}",
            "Follow docs/service-account-setup.md (steps 1-4) and save the key there.",
        )
        return None
    try:
        client_email = json.loads(key_path.read_text(encoding="utf-8"))["client_email"]
    except (ValueError, KeyError):
        report(
            "FIX",
            f"{key_path.name} is not a valid service-account key",
            "Re-download the JSON key (docs/service-account-setup.md, step 4).",
        )
        return None
    report("OK", f"Google key found. Share your sheet (as Editor) with:\n{client_email}")
    return client_email


def check_oauth_ready(profile: str, config: dict) -> bool:
    """Report on the app's OAuth client, the person's sign-in and their sheet. True when a
    sheet is known and the person is signed in, so it can be opened."""
    from google_auth import NetworkProblem, SignInNeeded, get_credentials

    connect_cmd = f"Run: uv run python scripts/sheet.py --profile {profile} connect"
    # The app's client file is only needed to open the browser sign-in. A saved sign-in keeps
    # working without it, so a missing file is a problem only when signing in is needed.
    try:
        get_credentials(profile)
        report("OK", "Signed in to Google")
        signed_in = True
    except NetworkProblem as exc:
        report("FIX", str(exc))
        return False
    except SignInNeeded:
        signed_in = False
        if oauth_client_path().exists():
            report("FIX", "Not signed in to Google yet", f"{connect_cmd}\n(it opens a browser tab)")
        else:
            report(
                "FIX",
                "Google sign-in isn't available in this copy of the app "
                f"({oauth_client_path().name} is missing)",
                "The project owner has to add it: see docs/maintainer-oauth-client.md",
            )
            return False
    if not config.get("sheet_id"):
        report("FIX", "No Google Sheet yet", connect_cmd)
        return False
    report("OK", f"Sheet: https://docs.google.com/spreadsheets/d/{config['sheet_id']}/edit")
    return signed_in


def check_sheet(profile: str, access_hint: str) -> None:
    """Open the sheet and make sure every tab exists (creating missing ones)."""
    try:
        from sheet import SheetClient
        from sheet_schema import TAB_HEADERS

        client = SheetClient(profile)
        report("OK", f"Sheet opens: {client.spreadsheet.title!r}")
        existing = {ws.title for ws in client.spreadsheet.worksheets()}
        needed = set(TAB_HEADERS)
        if needed - existing:
            client.init_tabs()  # only touches the sheet when a tab is missing
            report("OK", "Created the missing tabs: " + ", ".join(sorted(needed - existing)))
        else:
            report("OK", "Tabs ready: " + ", ".join(TAB_HEADERS))
    except Exception as exc:  # gspread raises several unrelated types
        name, text = type(exc).__name__, str(exc)
        if "SpreadsheetNotFound" in name or "404" in text:
            report(
                "FIX",
                "Can't find the sheet",
                access_hint
                or "Was it deleted? Run `sheet.py --profile "
                f"{profile} connect` to look for it again.",
            )
        elif "403" in text or "PERMISSION" in text.upper():
            report("FIX", "Google refused access", access_hint or "Try signing in again.")
        else:
            report("FIX", f"Couldn't open the sheet: {name}: {text}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--online", action="store_true", help="Also run one real LinkedIn search")
    args = parser.parse_args()
    profile = args.profile

    if sys.version_info < (3, 10):  # noqa: UP036 (friendly message instead of a traceback)
        report(
            "FIX",
            f"Python {sys.version.split()[0]} is too old",
            "Install Python 3.10 or newer (or use uv; see README).",
        )
    else:
        report("OK", f"Python {sys.version.split()[0]}")

    missing = []
    for module, package in REQUIRED_PACKAGES.items():
        try:
            importlib.import_module(module)
        except ImportError:
            missing.append(package)
    if missing:
        report(
            "FIX",
            f"Missing packages: {', '.join(missing)}",
            "Run: uv sync   (or: pip install -r requirements.txt)",
        )
    else:
        report("OK", "Required packages installed")

    try:
        profile_dir(profile)
    except ProfileError as exc:
        report("FIX", f"Profile folder: {exc}")
        sys.exit(1)
    report("OK", f"Profile folder profiles/{profile}/")

    resume = find_resume(profile)
    if resume:
        report("OK", f"Resume found: {resume.resolve()}")
    else:
        report(
            "FIX",
            "No resume in your profile folder",
            f"Drop your resume (PDF, DOCX or TXT) into {(PROFILES_DIR / profile).resolve()}",
        )

    config = None
    if "PyYAML" in missing:
        report("WARN", "Skipping config check until packages are installed")
    else:
        try:
            config = load_config(profile)
            report("OK", f"config.yaml: {len(config['locations'])} location(s)")
        except ProfileError as exc:
            report("FIX", str(exc))

    if (profile_dir(profile) / "profile.md").exists():
        report("OK", "profile.md exists")
    else:
        report("WARN", "profile.md not written yet", f"In Claude Code, run: /job-setup {profile}")

    if config and not missing:
        key_path = credentials_path(config)
        mode = choose_auth(config, key_path.exists())
        if mode == "service_account":
            report("OK", "Google access: service-account key (existing sheet)")
            client_email = check_service_account_key(key_path)
            if client_email:
                check_sheet(profile, f"Share the sheet with {client_email} as Editor.")
        else:
            report("OK", "Google access: sign in with your Google account")
            if check_oauth_ready(profile, config):
                check_sheet(profile, "")

    if args.online and not missing and config:
        try:
            from fetch import fetch_for_keyword

            keyword = (config["roles"] or ["manager"])[0]
            jobs = fetch_for_keyword(keyword, config["locations"][0], 72, 10)
            if jobs:
                report("OK", f"LinkedIn search works ({len(jobs)} results for {keyword!r})")
            else:
                report(
                    "WARN",
                    "LinkedIn search returned nothing",
                    "Could be a quiet search, or LinkedIn blocking this connection. "
                    "Try later, from home Wi-Fi without a VPN.",
                )
        except Exception as exc:
            report("FIX", f"LinkedIn search failed: {exc}")

    print()
    print(
        "Everything looks good." if not failures else f"{failures} thing(s) to fix (see FIX above)."
    )
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
