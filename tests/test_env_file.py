# =============================================================================
# test_env_file.py — the settings file, and what it refuses to apply.
#
# Part of: google-maps-harness test suite.
# =============================================================================
"""Parsing, precedence, and the allowlist that keeps a file from reaching PATH."""

import os
import tempfile
import unittest
from pathlib import Path

from google_maps_harness.env_file import EnvFileError, apply_env_file, parse_env_file

from .support import FAKE_API_KEY


class TestParsing(unittest.TestCase):
    """What a settings file may say."""

    def test_a_well_formed_file_parses(self) -> None:
        """The positive case, including comments, blanks, and quotes."""
        parsed = parse_env_file(
            "\n".join(
                (
                    "# the key",
                    "",
                    f'GOOGLE_MAPS_API_KEY="{FAKE_API_KEY}"',
                    "GOOGLE_MAPS_REGION_CODE=US",
                )
            )
        )
        self.assertEqual(
            parsed, {"GOOGLE_MAPS_API_KEY": FAKE_API_KEY, "GOOGLE_MAPS_REGION_CODE": "US"}
        )

    def test_an_unknown_variable_is_refused(self) -> None:
        """An allowlist, so a settings file cannot reach HTTPS_PROXY or PATH."""
        with self.assertRaises(EnvFileError) as caught:
            parse_env_file("HTTPS_PROXY=http://evil.example")
        self.assertIn("does not read", str(caught.exception))

    def test_a_misspelled_setting_is_refused_rather_than_ignored(self) -> None:
        """Silently ignoring it leaves the operator believing it applied."""
        with self.assertRaises(EnvFileError):
            parse_env_file("GOOGLE_MAPS_APIKEY=abc")

    def test_a_repeated_name_is_refused(self) -> None:
        """Which one wins would be a guess, so neither does."""
        with self.assertRaises(EnvFileError):
            parse_env_file("GOOGLE_MAPS_REGION_CODE=US\nGOOGLE_MAPS_REGION_CODE=GB")

    def test_a_control_character_in_a_value_is_refused(self) -> None:
        """A bidirectional override can rewrite what the operator reads."""
        with self.assertRaises(EnvFileError):
            parse_env_file("GOOGLE_MAPS_REGION_CODE=U‮S")

    def test_the_bad_line_is_not_quoted_back(self) -> None:
        """One of these variables is a credential, so no line is echoed."""
        with self.assertRaises(EnvFileError) as caught:
            parse_env_file(f"GOOGLE_MAPS_API_KEY={FAKE_API_KEY}\x01")
        self.assertNotIn(FAKE_API_KEY, str(caught.exception))


class TestApplying(unittest.TestCase):
    """Precedence, and the permission warning."""

    def setUp(self) -> None:
        """Work in a temporary directory with a clean environment."""
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.path = Path(self._directory.name) / "settings.env"
        self._saved = os.environ.get("GOOGLE_MAPS_REGION_CODE")
        os.environ.pop("GOOGLE_MAPS_REGION_CODE", None)
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        """Put the one variable these tests touch back."""
        if self._saved is None:
            os.environ.pop("GOOGLE_MAPS_REGION_CODE", None)
        else:
            os.environ["GOOGLE_MAPS_REGION_CODE"] = self._saved

    def test_a_setting_reaches_the_environment(self) -> None:
        """The positive case: the file does what it is for."""
        self.path.write_text("GOOGLE_MAPS_REGION_CODE=GB\n", encoding="utf-8")
        self.path.chmod(0o600)
        apply_env_file(self.path)
        self.assertEqual(os.environ["GOOGLE_MAPS_REGION_CODE"], "GB")

    def test_the_environment_wins_over_the_file(self) -> None:
        """Precedence runs one direction, so a caller can override a file."""
        os.environ["GOOGLE_MAPS_REGION_CODE"] = "US"
        self.path.write_text("GOOGLE_MAPS_REGION_CODE=GB\n", encoding="utf-8")
        self.path.chmod(0o600)
        notes = apply_env_file(self.path)
        self.assertEqual(os.environ["GOOGLE_MAPS_REGION_CODE"], "US")
        self.assertIn("GOOGLE_MAPS_REGION_CODE", notes[0])

    def test_a_world_readable_file_is_reported(self) -> None:
        """The file holds a credential; the operator has to be told, not overruled."""
        self.path.write_text("GOOGLE_MAPS_REGION_CODE=GB\n", encoding="utf-8")
        self.path.chmod(0o644)
        notes = apply_env_file(self.path)
        self.assertTrue(any("chmod 600" in note for note in notes))

    def test_a_missing_file_names_the_flag(self) -> None:
        """No file is searched for, so a wrong path says so plainly."""
        with self.assertRaises(EnvFileError) as caught:
            apply_env_file(self.path.parent / "absent.env")
        self.assertIn("--env-file", str(caught.exception))

    def test_a_directory_is_not_a_settings_file(self) -> None:
        """is_file() is what keeps a FIFO from blocking the read forever."""
        with self.assertRaises(EnvFileError):
            apply_env_file(self.path.parent)


if __name__ == "__main__":
    unittest.main()
