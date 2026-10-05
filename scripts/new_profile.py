"""Create a new person's profile folder from the template.

    python scripts/new_profile.py jane-doe     # creates profiles/jane-doe/
    python scripts/new_profile.py --list       # shows existing profiles

The folder is where you drop your resume and edit config.yaml. Everything under
profiles/ except the _example template is git-ignored, so personal data stays local.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys

from util import EXAMPLE_PROFILE, PROFILES_DIR, list_profiles

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name", nargs="?", help="Folder name, e.g. jane-doe (lowercase, digits, dashes)")
    parser.add_argument("--list", action="store_true", help="List existing profiles")
    args = parser.parse_args()

    if args.list or not args.name:
        profiles = list_profiles()
        print("\n".join(profiles) if profiles else "(no profiles yet)")
        return

    if not NAME_RE.match(args.name):
        sys.exit("Name must be lowercase letters, digits and dashes only, e.g. jane-doe")
    target = PROFILES_DIR / args.name
    if target.exists():
        sys.exit(f"{target} already exists. Pick another name, or edit the existing folder.")

    shutil.copytree(PROFILES_DIR / EXAMPLE_PROFILE, target)
    print(f"Created {target}\n")
    print("Next steps:")
    print(f"  1. Put your resume (PDF/DOCX/TXT) in {target}")
    print(f"  2. Edit {target / 'config.yaml'}: your locations and Google Sheet address")
    print("     (Google setup guide: docs/google-sheet-setup.md)")
    print(f"  3. python scripts/doctor.py --profile {args.name}")
    print(f"  4. In Claude Code: /job-setup {args.name}")


if __name__ == "__main__":
    main()
