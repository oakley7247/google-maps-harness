#!/usr/bin/env python3
# =============================================================================
# build.py — packages the Skill, in two flavours that must never be confused.
#
# Part of: google-maps-harness. Run by hand when the Skill changes.
#
#   python3 skill/build.py                       -> dist/google-maps.zip
#   python3 skill/build.py --with-key ../.env    -> both, adding the personal one
#
# Why two: claude.ai has no environment to set and no persistent home, so an
# uploaded key lasts only as long as one conversation. A key bundled inside the
# Skill is uploaded once and stays. That is a real convenience and a real
# exposure — the credential then sits in the Skill artifact, stored server-side
# under standard retention — so the two builds are separate files with separate
# names, and the personal one is gitignored and stamped with a banner in its own
# SKILL.md. The failure this guards against is handing somebody the wrong zip.
# =============================================================================
"""Build the shareable Skill package, and optionally a personal one with a key."""

# Same reason as scripts/maps.py: this runs on whatever Python is to hand,
# including the 3.9 that ships with macOS.
from __future__ import annotations

import argparse
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent / "google-maps"
DIST_DIR = Path(__file__).resolve().parent.parent / "dist"

# The filename maps.py looks for first, beside SKILL.md.
BUNDLED_KEY_NAME = "google-maps-key.txt"

SHAREABLE_NAME = "google-maps.zip"
PERSONAL_NAME = "google-maps-personal.zip"

# Prepended to the personal build's SKILL.md body, where anyone opening the
# bundle sees it before anything else.
PERSONAL_BANNER = """> **This build carries a live Google Maps API key.** It was made for one
> person's own claude.ai account. Do not share this file, and do not commit it.
> The shareable build is `google-maps.zip`, which contains no credential.

"""

_KEY_ALPHABET = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-")


def read_key(path: Path) -> str:
    """Read a key from a bare-value file or a NAME=value file.

    Args:
        path: The file to read.

    Returns:
        The key.

    Raises:
        SystemExit: The file is missing or holds nothing key-shaped. Failing
            here rather than shipping a bundle whose key is a stray blank line.
    """
    if not path.is_file():
        raise SystemExit(f"build: no such key file: {path}")
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        candidate = stripped.partition("=")[2].strip().strip("'\"") if "=" in stripped else stripped
        if 20 <= len(candidate) <= 128 and set(candidate) <= _KEY_ALPHABET:
            return candidate
    raise SystemExit(f"build: {path} holds nothing that looks like a Maps API key.")


def write_zip(source: Path, target: Path) -> None:
    """Zip a skill directory with its own name as the top-level entry.

    Args:
        source: The skill directory.
        target: The .zip to write.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(source.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                archive.write(path, Path(source.name) / path.relative_to(source))


def stage(key: str | None) -> Path:
    """Copy the skill into a temporary directory, optionally adding the key.

    Args:
        key: The API key to bundle, or None for the shareable build.

    Returns:
        The staged directory. The caller removes its parent.
    """
    staging = Path(tempfile.mkdtemp()) / SKILL_DIR.name
    shutil.copytree(SKILL_DIR, staging, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))

    # A key that was already lying in the source tree must not ride along into
    # the shareable build. Removed unconditionally, then re-added only when
    # asked, so the shareable build cannot inherit one by accident.
    for stray in (staging / BUNDLED_KEY_NAME, staging / "scripts" / BUNDLED_KEY_NAME):
        stray.unlink(missing_ok=True)

    if key is not None:
        key_file = staging / BUNDLED_KEY_NAME
        key_file.write_text(key + "\n", encoding="utf-8")
        key_file.chmod(0o600)
        skill_md = staging / "SKILL.md"
        text = skill_md.read_text(encoding="utf-8")
        # After the frontmatter and the H1, so the banner is the first thing in
        # the body rather than something buried below the setup section.
        text = re.sub(r"(?m)^(# Google Maps\n)\n", r"\1\n" + PERSONAL_BANNER, text, count=1)
        skill_md.write_text(text, encoding="utf-8")
    return staging


def main(argv: list[str] | None = None) -> int:
    """Build one or both packages.

    Args:
        argv: Arguments after the command name, or None to read sys.argv.

    Returns:
        0 on success.
    """
    parser = argparse.ArgumentParser(prog="build.py", description=__doc__)
    parser.add_argument(
        "--with-key",
        metavar="PATH",
        help=(
            "Also build the personal package, bundling the key from this file. "
            "The result is gitignored and must not be shared."
        ),
    )
    args = parser.parse_args(argv)

    staging = stage(None)
    write_zip(staging, DIST_DIR / SHAREABLE_NAME)
    shutil.rmtree(staging.parent)
    print(f"  shareable  {DIST_DIR / SHAREABLE_NAME}")
    print("             no credential — this is the one you send people")

    if args.with_key:
        key = read_key(Path(args.with_key).expanduser())
        staging = stage(key)
        write_zip(staging, DIST_DIR / PERSONAL_NAME)
        shutil.rmtree(staging.parent)
        print(f"\n  personal   {DIST_DIR / PERSONAL_NAME}")
        print("             CARRIES YOUR API KEY — upload to your own account only")
        print("             Never send this file to anyone. Rotate the key if you do.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
