# =============================================================================
# redaction.py — the registry of credential strings, and the scrubber that
# keeps them out of anything a human or a model ever reads.
#
# Part of: google-maps-harness. Called by: server.py (registers the API key
# before any client exists) and runtime.py (scrubs every exception on its way
# out of a tool). Calls: nothing.
# Security: this is a backstop, not the primary control. Every message in this
# project is written not to contain the key in the first place; this exists
# because "every path" includes the paths nobody anticipated — a library's
# exception text, a future edit, an error shape Google has not shipped yet. It
# matters more here than in a bearer-token project: two of the six APIs this
# server calls take the key in the query string, so the key is part of a URL,
# and a URL is exactly the thing an HTTP library likes to put in an exception.
# =============================================================================
"""Hold the process's credential strings and scrub them out of any text."""

# Short strings would match ordinary words and turn every message into
# redaction markers. Nothing this project treats as a secret is shorter than
# this, so a shorter value is a bug in the caller rather than a secret worth
# hiding.
_MIN_SECRET_LENGTH = 8

REDACTED = "[redacted]"


class SecretRegistry:
    """Collects every credential this process holds, and removes them from text.

    One instance is built at startup and handed to whatever holds a credential.
    """

    def __init__(self) -> None:
        """Build an empty registry."""
        self._secrets: set[str] = set()

    def add(self, secret: str | None) -> None:
        """Register a credential string so it is scrubbed from future messages.

        Args:
            secret: The credential. None, empty, and implausibly short values
                are ignored rather than registered — a two-character "secret"
                would redact half of every sentence.
        """
        if secret and len(secret) >= _MIN_SECRET_LENGTH:
            self._secrets.add(secret)

    def scrub(self, text: str) -> str:
        """Return `text` with every registered credential replaced.

        Args:
            text: Any message bound for a log line, an exception, or the model.

        Returns:
            The text with each registered secret replaced by `[redacted]`.
        """
        # Longest first, so a longer credential that contains a shorter
        # registered value as a substring is still replaced whole rather than
        # leaving a fragment of itself behind.
        for secret in sorted(self._secrets, key=len, reverse=True):
            text = text.replace(secret, REDACTED)
        return text
