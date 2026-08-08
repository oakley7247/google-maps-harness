# =============================================================================
# env_file.py — applies the settings file named by --env-file to the process
# environment.
#
# Part of: google-maps-harness. Called by: server.py, immediately before
# config.load_config(). Calls: config.py, for the set of names it will accept.
# Security: the file is never searched for — the operator names it, because a
# file discovered in the working directory is whatever happened to be there
# (see the SECURITY note at load_from_command_line). Only the documented
# settings are accepted, so a settings file cannot reach the variables that
# steer the HTTP stack, the dynamic loader, or the shell. No value is ever
# printed: a bad line is named by number and a rejected variable by name.
# =============================================================================
"""Apply a NAME=value settings file named on the command line."""

import argparse
import os
import re
import unicodedata
from collections.abc import Sequence
from pathlib import Path

from .config import SETTING_NAMES


class EnvFileError(Exception):
    """Raised when the named settings file is missing, unreadable, or malformed."""


# A settings file is a handful of short lines. The cap exists so pointing
# --env-file at a log file fails at once, instead of reading it into memory to
# discover it was never a settings file. Device files never get this far:
# read_env_file opens regular files only.
_MAX_CHARACTERS = 64 * 1024

# The shape POSIX gives an environment variable, bounded in length so a
# malformed file cannot push an unbounded string into an error message.
_NAME_PATTERN = re.compile(r"\A[A-Z][A-Z0-9_]{0,63}\Z")

_QUOTE_CHARACTERS = ("'", '"')


# --- Parsing ------------------------------------------------------------------


def _is_unsafe(value: str) -> bool:
    """Return True when a value carries a character that could forge output.

    Args:
        value: The value read from the settings file.

    Returns:
        True if any character is a control, format, surrogate, private-use, or
        unassigned code point, or a Unicode line separator.
    """
    # Settings reach terminals and log lines. Unicode category "C" covers the
    # C0/C1 controls and the bidirectional-override format characters, both of
    # which can rewrite what a reader sees; U+2028 and U+2029 are line breaks
    # that category misses (ledger LL-2).
    return any(
        unicodedata.category(char).startswith("C") or char in "\u2028\u2029" for char in value
    )


def _unquoted(value: str) -> str:
    """Return the value with one matching pair of surrounding quotes removed.

    Args:
        value: The text to the right of the first `=`, already whitespace-trimmed.

    Returns:
        The value, unquoted if it was wrapped in one matching pair.
    """
    # NOTE: one pair, and nothing else. No escape sequences and no $VAR
    # expansion are honoured — expansion is a grammar, and a grammar in a
    # credential file is a place for surprises to hide. Everything after the
    # first `=` is literal, so a `#` is part of the value, not a comment.
    if len(value) >= 2 and value[0] == value[-1] and value[0] in _QUOTE_CHARACTERS:
        return value[1:-1]
    return value


def parse_env_file(text: str) -> dict[str, str]:
    """Parse settings-file text into a mapping of variable name to value.

    Blank lines and lines whose first non-blank character is `#` are ignored.
    Every other line must be `NAME=value` naming a variable this project reads.

    Args:
        text: The file's contents.

    Returns:
        The settings, in the order they appeared.

    Raises:
        EnvFileError: A line is not `NAME=value`, names a variable this project
            does not read, repeats a name, or holds an unsafe character. The
            message names the line number and never quotes the line, because one
            of these variables is a credential.
    """
    settings: dict[str, str] = {}
    # Split on newline alone rather than str.splitlines, which also breaks on
    # vertical tab and form feed — characters that would then slip past the
    # unsafe-character check by having become line boundaries.
    for number, line in enumerate(text.split("\n"), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        name, separator, raw_value = stripped.partition("=")
        name = name.strip()
        if not separator or not _NAME_PATTERN.match(name):
            raise EnvFileError(
                f"line {number} is not NAME=value. Names are uppercase letters, "
                "digits, and underscores; comments start the line with #. The "
                "line's contents are withheld from this message."
            )
        if name not in SETTING_NAMES:
            # SECURITY: an allowlist, not a denylist. It refuses the misspelling
            # that would otherwise be ignored while the operator believed the
            # setting applied (ledger LL-12), and it keeps a settings file from
            # reaching anything else in the process environment — the proxy
            # variables urllib reads, PATH, or the dynamic loader's own.
            raise EnvFileError(
                f"line {number} sets {name}, which this project does not read. "
                "See .env.example for every variable it does."
            )
        if name in settings:
            raise EnvFileError(
                f"line {number} sets {name} again. Which one wins would be a "
                "guess, so neither does; delete one."
            )

        value = _unquoted(raw_value.strip())
        if _is_unsafe(value):
            raise EnvFileError(
                f"line {number} sets {name} to a value holding a control or "
                "formatting character. Value withheld from this message; "
                "re-copy it without invisible characters."
            )
        settings[name] = value
    return settings


# --- Reading and applying -----------------------------------------------------


def read_env_file(path: Path) -> tuple[dict[str, str], list[str]]:
    """Read and parse a settings file.

    Args:
        path: The file named on the command line.

    Returns:
        A tuple of the parsed settings and any warnings for the operator.

    Raises:
        EnvFileError: The path is not a readable regular file, is not UTF-8
            text, is larger than the cap, or does not parse.
    """
    # Paths are shown quoted throughout this module: `repr` escapes any control
    # character in an operator's typo, so a mistyped path cannot rewrite the
    # terminal line that reports it.
    shown = repr(str(path))
    # is_file() is True only for a regular file, so a directory is refused here
    # and a FIFO — which would block the read forever — never gets opened.
    if not path.is_file():
        raise EnvFileError(
            f"no settings file at {shown}. Pass --env-file with the path to the "
            "file you filled in from .env.example."
        )
    try:
        with path.open("r", encoding="utf-8") as handle:
            text = handle.read(_MAX_CHARACTERS + 1)
    except UnicodeDecodeError as error:
        raise EnvFileError(f"{shown} is not UTF-8 text, so it is not a settings file.") from error
    except OSError as error:
        raise EnvFileError(f"could not read {shown}: {error.strerror}.") from error
    if len(text) > _MAX_CHARACTERS:
        raise EnvFileError(
            f"{shown} holds more than {_MAX_CHARACTERS} characters, which no "
            "settings file does. Check the path names the file you meant."
        )

    warnings: list[str] = []
    if path.stat().st_mode & 0o077:
        # SECURITY: reported rather than refused. The file holds the API key, and
        # `cp .env.example .env` leaves it readable by every account on the
        # machine; the operator has to be told, but a permission the operator
        # chose is not this command's to override.
        warnings.append(
            f"{shown} is readable by other accounts on this machine and holds a "
            f"credential. Run: chmod 600 {shown}"
        )
    return parse_env_file(text), warnings


def apply_env_file(path: Path) -> list[str]:
    """Apply a settings file to this process's environment, and report what it did.

    A variable already set to a non-empty value in the environment keeps that
    value; the file fills in the rest.

    Args:
        path: The file named on the command line.

    Returns:
        Lines for the operator, describing what was read and what was kept.

    Raises:
        EnvFileError: The file is missing, unreadable, or malformed.
    """
    settings, notes = read_env_file(path)
    applied: list[str] = []
    kept: list[str] = []
    for name in sorted(settings):
        # SECURITY: the environment wins, always. Precedence has to be one
        # direction that a reader can state without checking, and this is the
        # direction that lets a caller override a file it does not own — an
        # empty variable counts as unset, the same reading config.py gives it.
        if os.environ.get(name, "").strip():
            kept.append(name)
            continue
        os.environ[name] = settings[name]
        applied.append(name)

    summary = f"read {len(applied)} settings from {str(path)!r}"
    if kept:
        # Naming them makes precedence visible: an operator who exported a
        # variable months ago can see the file did not overrule it. Names are
        # published in .env.example; values are not printed here or anywhere.
        summary += f"; already set in the environment, so left alone: {', '.join(kept)}"
    return [f"{summary}.", *notes]


# --- Command line -------------------------------------------------------------


def load_from_command_line(argv: Sequence[str] | None, prog: str, description: str) -> list[str]:
    """Parse `--env-file` out of the command line and apply the file it names.

    Args:
        argv: The arguments after the command name, or None to read sys.argv.
        prog: The command's name, for --help and error messages.
        description: The command's one-line description, for --help.

    Returns:
        Lines for the operator, empty when no file was named.

    Raises:
        EnvFileError: A file was named and could not be read or parsed.
        SystemExit: argparse rejected the command line, exiting 2 as usual.
    """
    parser = argparse.ArgumentParser(prog=prog, description=description)
    parser.add_argument(
        "--env-file",
        metavar="PATH",
        help=(
            "Settings file to read, as filled in from .env.example. Variables "
            "already set in the environment win. Without this flag no file is "
            "read: this command never guesses which .env you meant."
        ),
    )
    # SECURITY: there is no default path, deliberately. Loading `.env` from the
    # working directory would load whichever one happened to be there, and this
    # server's one credential is a billable API key. Resolving against the
    # repository instead would work for an editable install and silently do
    # nothing for a wheel, so the same command would behave differently
    # depending on how it was installed. Naming the file is one word of typing
    # and removes both problems.
    named: str | None = parser.parse_args(argv).env_file
    if named is None:
        return []
    return apply_env_file(Path(named).expanduser())
