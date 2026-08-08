# =============================================================================
# server.py — the entry point: builds the client, installs it, and serves MCP
# over stdio.
#
# Part of: google-maps-harness. Called by: whichever MCP client launches it
# (Claude Code, Claude Desktop) and by the `google-maps-harness` console script.
# Calls: env_file.py for `--env-file`, then config.py, maps_api.py, and every
# module under tools/.
# Security: `--env-file` exists so the API key can stay in a 0600 file. The
# alternative Claude Code offers — `-e NAME=value` on the registration command
# — writes the value into ~/.claude.json and into shell history, neither of
# which is owner-only. Configuration is validated here and the process exits
# naming the first bad variable, rather than failing at the first tool call.
# Logging is bound to stderr on purpose — stdout carries the MCP protocol, and
# one stray line there corrupts the session. The key is registered with the
# SecretRegistry before any client is built, so the scrubber is armed before
# anything can raise.
# =============================================================================
"""Expose Google Maps Platform to an MCP client over stdio."""

import logging
import sys
from collections.abc import Sequence

from . import tools  # noqa: F401 - imported for the tool registrations it performs
from .config import ConfigError, MapsConfig, load_config
from .env_file import EnvFileError, load_from_command_line
from .maps_api import build_client
from .redaction import SecretRegistry
from .runtime import Runtime, configure, mcp

_PROGRAM = "google-maps-harness"


def build_runtime(config: MapsConfig) -> Runtime:
    """Build the client this server needs from validated settings.

    Args:
        config: The validated configuration.

    Returns:
        The runtime, ready to install.
    """
    secrets_registry = SecretRegistry()
    # SECURITY: registered before any client exists, so the scrubber covers
    # every exception this process can raise from here on — including ones
    # raised while building the client itself. It matters more here than in a
    # bearer-token project: three of these APIs carry the key in the query
    # string, so the key is part of a URL, and URLs end up in exception text.
    secrets_registry.add(config.api_key)

    return Runtime(
        config=config,
        maps=build_client(
            config.api_key, config.timeout_seconds, config.language_code, config.region_code
        ),
        secrets=secrets_registry,
    )


def main(argv: Sequence[str] | None = None) -> None:
    """Load configuration, install the runtime, then serve MCP until the client exits.

    Args:
        argv: Arguments after the command name, or None to read sys.argv. The
            only one is `--env-file PATH`, which the MCP client passes after
            `--` so the API key stays in a 0600 file rather than in the client's
            own configuration; without it the settings must already be in the
            environment the client launched this process with.
    """
    # stderr, never stdout: stdout is the MCP transport.
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format=f"%(asctime)s {_PROGRAM} %(levelname)s %(message)s",
    )
    try:
        # Before load_config, because this is what puts the named file's
        # settings into the environment load_config then reads.
        for note in load_from_command_line(argv, _PROGRAM, __doc__ or ""):
            logging.info("%s", note)
        config = load_config()
    except (ConfigError, EnvFileError) as error:
        # Fail at startup with the reason, rather than at the first tool call.
        print(f"{_PROGRAM}: {error}", file=sys.stderr)
        raise SystemExit(1) from error

    runtime = build_runtime(config)
    configure(runtime)

    logging.info(
        "Ready. Up to %d upstream requests and %.0f seconds per tool call; language %s, region %s.",
        config.max_requests_per_call,
        config.max_seconds_per_call,
        config.language_code,
        config.region_code or "unset",
    )
    if config.allow_atmosphere_fields:
        logging.warning(
            "GOOGLE_MAPS_ALLOW_ATMOSPHERE_FIELDS is on. Place lookups may "
            "request reviews and editorial summaries, which are written by the "
            "public and bill at Google's most expensive Places tier."
        )

    mcp.run("stdio")


if __name__ == "__main__":
    main()
