# =============================================================================
# test_config.py — the settings this server refuses to start on.
#
# Part of: google-maps-harness test suite.
# Every test here sets the environment explicitly and restores it, so the suite
# behaves the same on a machine that happens to export GOOGLE_MAPS_API_KEY.
# =============================================================================
"""Settings validation, and the .env.example that documents it."""

import os
import re
import unittest
from pathlib import Path

from google_maps_harness.config import SETTING_NAMES, ConfigError, load_config

from .support import FAKE_API_KEY

_REPO_ROOT = Path(__file__).resolve().parent.parent


class ConfigTestCase(unittest.TestCase):
    """Base that gives each test a clean environment."""

    def setUp(self) -> None:
        """Remove every setting this project reads, and restore it afterwards."""
        self._saved = {name: os.environ.get(name) for name in SETTING_NAMES}
        for name in SETTING_NAMES:
            os.environ.pop(name, None)
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        """Put the environment back as it was."""
        for name, value in self._saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


class TestApiKey(ConfigTestCase):
    """The one required setting."""

    def test_missing_key_is_refused(self) -> None:
        """A server with no key fails at startup rather than at the first call."""
        with self.assertRaises(ConfigError) as caught:
            load_config()
        self.assertIn("GOOGLE_MAPS_API_KEY", str(caught.exception))

    def test_plausible_key_is_accepted(self) -> None:
        """The positive case: a well-formed key loads.

        Without this the shape check could reject everything and still pass the
        negative tests below (ledger LL-4).
        """
        os.environ["GOOGLE_MAPS_API_KEY"] = FAKE_API_KEY
        self.assertEqual(load_config().api_key, FAKE_API_KEY)

    def test_key_with_a_newline_is_refused(self) -> None:
        """A key carrying a control character never reaches a URL or a header."""
        os.environ["GOOGLE_MAPS_API_KEY"] = "AIza" + "x" * 20 + "\nInjected: yes"
        with self.assertRaises(ConfigError):
            load_config()

    def test_the_bad_value_is_not_echoed(self) -> None:
        """A malformed key is still a credential, so the message withholds it."""
        secret = "AIza" + "x" * 20 + " has a space"
        os.environ["GOOGLE_MAPS_API_KEY"] = secret
        with self.assertRaises(ConfigError) as caught:
            load_config()
        self.assertNotIn(secret, str(caught.exception))
        self.assertNotIn("AIzaxxxx", str(caught.exception))


class TestNumericSettings(ConfigTestCase):
    """The bounded numbers, and the values that are not numbers."""

    def setUp(self) -> None:
        """Give every test a usable key."""
        super().setUp()
        os.environ["GOOGLE_MAPS_API_KEY"] = FAKE_API_KEY

    def test_defaults_apply_when_unset(self) -> None:
        """The positive case: an environment with only a key is complete."""
        config = load_config()
        self.assertEqual(config.timeout_seconds, 10.0)
        self.assertEqual(config.max_requests_per_call, 25)
        self.assertEqual(config.max_seconds_per_call, 30.0)
        self.assertEqual(config.language_code, "en")
        self.assertIsNone(config.region_code)
        self.assertFalse(config.allow_atmosphere_fields)

    def test_a_value_inside_the_range_is_kept(self) -> None:
        """A legitimate override survives, so the range check is not a blanket no."""
        os.environ["GOOGLE_MAPS_TIMEOUT_SECONDS"] = "20"
        os.environ["GOOGLE_MAPS_MAX_REQUESTS_PER_CALL"] = "5"
        config = load_config()
        self.assertEqual(config.timeout_seconds, 20.0)
        self.assertEqual(config.max_requests_per_call, 5)

    def test_nan_timeout_is_refused(self) -> None:
        """float() accepts "nan"; an unbounded wait must not reach the socket."""
        os.environ["GOOGLE_MAPS_TIMEOUT_SECONDS"] = "nan"
        with self.assertRaises(ConfigError):
            load_config()

    def test_infinite_timeout_is_refused(self) -> None:
        """float() turns "1e999" into inf, which is a timeout that never fires."""
        os.environ["GOOGLE_MAPS_TIMEOUT_SECONDS"] = "1e999"
        with self.assertRaises(ConfigError):
            load_config()

    def test_out_of_range_is_refused(self) -> None:
        """A finite number outside the range is still refused."""
        os.environ["GOOGLE_MAPS_MAX_SECONDS_PER_CALL"] = "1000"
        with self.assertRaises(ConfigError):
            load_config()

    def test_non_ascii_digits_are_refused(self) -> None:
        """int() converts some non-ASCII digits silently; the parse is guarded."""
        os.environ["GOOGLE_MAPS_MAX_REQUESTS_PER_CALL"] = "٣"
        with self.assertRaises(ConfigError):
            load_config()

    def test_superscript_digit_is_refused(self) -> None:
        """ "²" passes str.isdigit() and then crashes int(); it must be refused."""
        os.environ["GOOGLE_MAPS_MAX_REQUESTS_PER_CALL"] = "²"
        with self.assertRaises(ConfigError):
            load_config()


class TestFlagsAndCodes(ConfigTestCase):
    """The permission flag and the locale codes."""

    def setUp(self) -> None:
        """Give every test a usable key."""
        super().setUp()
        os.environ["GOOGLE_MAPS_API_KEY"] = FAKE_API_KEY

    def test_flag_reads_true(self) -> None:
        """The positive case: the flag can be turned on."""
        os.environ["GOOGLE_MAPS_ALLOW_ATMOSPHERE_FIELDS"] = "true"
        self.assertTrue(load_config().allow_atmosphere_fields)

    def test_flag_reads_false(self) -> None:
        """And explicitly off."""
        os.environ["GOOGLE_MAPS_ALLOW_ATMOSPHERE_FIELDS"] = "no"
        self.assertFalse(load_config().allow_atmosphere_fields)

    def test_unreadable_flag_is_refused(self) -> None:
        """A typo in a permission flag fails loudly rather than reading as off.

        Silently defaulting would leave an operator believing a setting applied
        when it never did (ledger LL-12).
        """
        os.environ["GOOGLE_MAPS_ALLOW_ATMOSPHERE_FIELDS"] = "ture"
        with self.assertRaises(ConfigError):
            load_config()

    def test_region_and_language_are_matched_exactly(self) -> None:
        """The positive case, then the shapes that are not region codes."""
        os.environ["GOOGLE_MAPS_REGION_CODE"] = "GB"
        os.environ["GOOGLE_MAPS_LANGUAGE_CODE"] = "pt-BR"
        config = load_config()
        self.assertEqual(config.region_code, "GB")
        self.assertEqual(config.language_code, "pt-BR")

        os.environ["GOOGLE_MAPS_REGION_CODE"] = "United Kingdom"
        with self.assertRaises(ConfigError):
            load_config()


class TestEnvExample(unittest.TestCase):
    """The shipped template is loaded through the real name set, not eyeballed."""

    def test_every_documented_variable_is_one_the_code_reads(self) -> None:
        """A variable in the template that the code ignores is a lie in a file.

        The reverse direction matters as much: a setting the code reads and the
        template never mentions is undiscoverable (ledger LL-12).
        """
        text = (_REPO_ROOT / ".env.example").read_text(encoding="utf-8")
        documented = set(re.findall(r"^#?\s*(GOOGLE_MAPS_[A-Z_]+)=", text, re.MULTILINE))
        self.assertEqual(documented, set(SETTING_NAMES))


if __name__ == "__main__":
    unittest.main()
