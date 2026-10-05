"""Check that a profile is ready to run, and say exactly how to fix anything that isn't.

    python scripts/doctor.py --profile jane-doe
    python scripts/doctor.py --profile jane-doe --online    # also tries one real LinkedIn search

Prints OK / WARN / FIX per check. Exits 1 if anything needs fixing.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib
import json
import sys

from config import ProfileError, credentials_path, find_resume, load_config, profile_dir

REQUIRED_PACKAGES = {
    "jobspy": "python-jobspy",
    "gspread": "gspread",
    "google.auth": "google-auth",
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
        report("OK", f"Resume found: {resume.name}")
    else:
        report(
            "FIX",
            "No resume in your profile folder",
            f"Drop your resume (PDF, DOCX or TXT) into profiles/{profile}/",
        )

    config = partial = None
    if "PyYAML" in missing:
        report("WARN", "Skipping config check until packages are installed")
    else:
        try:
            config = partial = load_config(profile)
            report("OK", f"config.yaml: {len(config['locations'])} location(s), sheet set")
        except ProfileError as exc:
            report("FIX", str(exc))
            with contextlib.suppress(ProfileError):
                partial = load_config(profile, require_sheet=False)

    if (profile_dir(profile) / "profile.md").exists():
        report("OK", "profile.md exists")
    else:
        report("WARN", "profile.md not written yet", f"In Claude Code, run: /job-setup {profile}")

    key_path = credentials_path(partial or {})
    client_email = None
    if not key_path.exists():
        report(
            "FIX",
            f"Google key not found at {key_path}",
            "Follow docs/google-sheet-setup.md (steps 1-4) and save the key there.",
        )
    else:
        try:
            client_email = json.loads(key_path.read_text(encoding="utf-8"))["client_email"]
            report("OK", f"Google key found. Share your sheet (as Editor) with:\n{client_email}")
        except (ValueError, KeyError):
            report(
                "FIX",
                f"{key_path.name} is not a valid service-account key",
                "Re-download the JSON key (docs/google-sheet-setup.md, step 4).",
            )

    if config and client_email and not missing:
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
                    f"Check the address in config.yaml, and share the sheet with "
                    f"{client_email} as Editor.",
                )
            elif "403" in text or "PERMISSION" in text.upper():
                report(
                    "FIX",
                    "Google refused access",
                    f"Share the sheet with {client_email} as Editor, and make sure the "
                    f"Google Sheets API is enabled (guide, step 2).",
                )
            else:
                report("FIX", f"Couldn't open the sheet: {name}: {text}")

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
