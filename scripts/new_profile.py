"""Create a new person's profile folder from the template.

    python scripts/new_profile.py jane-doe                       # creates profiles/jane-doe/
    python scripts/new_profile.py jane-doe --resume "C:/Users/me/Jane Resume.pdf"
    python scripts/new_profile.py --list                         # shows existing profiles

The folder holds the resume and config.yaml. Without --resume, a single resume sitting in the
top-level resume/ drop folder is used. Everything under profiles/ except the _example template
is git-ignored, so personal data stays local.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

from config import (
    EXAMPLE_PROFILE,
    PROFILES_DIR,
    REPO_ROOT,
    list_profiles,
    looks_like_resume_file,
)

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
DROP_FOLDER = REPO_ROOT / "resume"


def drop_folder_resume() -> Path | None:
    """The one resume in resume/, or None if it holds none or several (then we don't guess)."""
    if not DROP_FOLDER.is_dir():
        return None
    found = [p for p in sorted(DROP_FOLDER.iterdir()) if p.is_file() and looks_like_resume_file(p)]
    return found[0] if len(found) == 1 else None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "name", nargs="?", help="Folder name, e.g. jane-doe (lowercase, digits, dashes)"
    )
    parser.add_argument("--list", action="store_true", help="List existing profiles")
    parser.add_argument("--resume", type=Path, help="Resume file to copy into the new folder")
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

    resume = args.resume
    if resume is None:
        resume = drop_folder_resume()
        if resume:
            print(f"Using the resume in the resume/ folder: {resume.name}")
    elif not resume.is_file():
        sys.exit(f"No resume file at {resume}")
    elif not looks_like_resume_file(resume):
        sys.exit(
            f"{resume.name} doesn't look like a resume. Use a PDF, Word (.docx) or text file, "
            "and not one named profile.md or README."
        )

    shutil.copytree(PROFILES_DIR / EXAMPLE_PROFILE, target)
    print(f"Created {target}\n")
    if resume:
        shutil.copy2(resume, target / resume.name)
        (target / "PUT-YOUR-RESUME-HERE.txt").unlink(missing_ok=True)
        print(f"Copied your resume into the folder: {target / resume.name}\n")

    print("Next steps:")
    step = 1
    if not resume:
        print(f"  {step}. Put your resume (PDF/DOCX/TXT) in {target}")
        step += 1
    print(f"  {step}. Edit {target / 'config.yaml'}: set your locations")
    step += 1
    print(f"  {step}. python scripts/sheet.py --profile {args.name} connect")
    print("     (opens a browser tab to sign in to Google, then creates your sheet)")
    step += 1
    print(f"  {step}. python scripts/doctor.py --profile {args.name}")
    step += 1
    print(f"  {step}. In Claude Code: /job-setup {args.name}")


if __name__ == "__main__":
    main()
