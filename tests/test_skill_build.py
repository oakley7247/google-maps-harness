# =============================================================================
# test_skill_build.py — the packaging control: a credential must never ride
# along in the shareable build.
#
# Part of: google-maps-harness test suite.
# This is the one test in the suite guarding a mistake that cannot be taken
# back. A shareable zip that carries a live API key is a credential handed to
# whoever receives it, and unlike a bug there is no fixing it after the fact —
# only rotating the key. So the check is executable rather than a comment, and
# it asserts both directions: absent where it must be absent, present where the
# whole point is that it is present.
# =============================================================================
"""The two Skill packages, and what may and may not be inside each."""

import contextlib
import importlib.util
import io
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BUILD_PATH = _REPO_ROOT / "skill" / "build.py"

# Self-evidently fake, low-entropy, and the right shape to pass the builder's
# own validation. A fixture that looked like a real key would train a reviewer
# to wave through findings in tests/.
FAKE_KEY = "EXAMPLEskillBUILDkeyNotARealCredential"


def _load_build():
    """Import skill/build.py, which is a script rather than a package member.

    Returns:
        The imported module.
    """
    spec = importlib.util.spec_from_file_location("skill_build", _BUILD_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SkillBuildTestCase(unittest.TestCase):
    """Base that builds both packages into a scratch directory."""

    def setUp(self) -> None:
        """Build both flavours against a fake key, into a temporary dist."""
        self.build = _load_build()
        self.workspace = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.workspace, ignore_errors=True)

        key_file = self.workspace / "key.txt"
        key_file.write_text(FAKE_KEY + "\n", encoding="utf-8")
        # Redirect the build's output rather than writing into the real dist,
        # so running the suite never leaves a key-bearing artifact behind.
        self.build.DIST_DIR = self.workspace / "dist"
        # The builder narrates what it wrote, which is right at a terminal and
        # noise in a test run.
        with contextlib.redirect_stdout(io.StringIO()):
            self.build.main(["--with-key", str(key_file)])
        self.shareable = self.workspace / "dist" / self.build.SHAREABLE_NAME
        self.personal = self.workspace / "dist" / self.build.PERSONAL_NAME

    def _contents(self, archive: Path) -> dict[str, str]:
        """Read every file in a zip as text.

        Args:
            archive: The zip to read.

        Returns:
            Member name to contents.
        """
        with zipfile.ZipFile(archive) as zipped:
            return {
                name: zipped.read(name).decode("utf-8", errors="replace")
                for name in zipped.namelist()
            }


class TestShareableBuild(SkillBuildTestCase):
    """What must not be in the package other people receive."""

    def test_no_file_contains_the_key(self) -> None:
        """The control this whole module exists for.

        Checked across every member rather than against the key file's name,
        because the failure worth catching is the key appearing somewhere
        nobody thought to look — inlined into SKILL.md, left in a comment.
        """
        for name, text in self._contents(self.shareable).items():
            with self.subTest(member=name):
                self.assertNotIn(FAKE_KEY, text)

    def test_no_key_file_is_present_at_all(self) -> None:
        """Not merely empty of the value: the file itself is absent."""
        names = set(self._contents(self.shareable))
        for candidate in names:
            self.assertNotIn(self.build.BUNDLED_KEY_NAME, candidate)

    def test_a_stray_key_in_the_source_tree_does_not_ride_along(self) -> None:
        """The realistic accident: somebody left a key beside SKILL.md.

        The builder deletes it unconditionally from the staging copy before
        deciding whether to add one, so the shareable build cannot inherit a
        credential from the working tree it was built in.
        """
        stray = self.build.SKILL_DIR / self.build.BUNDLED_KEY_NAME
        existed = stray.exists()
        previous = stray.read_text(encoding="utf-8") if existed else None
        stray.write_text(FAKE_KEY + "\n", encoding="utf-8")
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                self.build.main([])
            for name, text in self._contents(self.shareable).items():
                with self.subTest(member=name):
                    self.assertNotIn(FAKE_KEY, text)
        finally:
            if previous is None:
                stray.unlink(missing_ok=True)
            else:
                stray.write_text(previous, encoding="utf-8")

    def test_it_carries_no_do_not_share_banner(self) -> None:
        """A banner on the shareable build would train people to ignore it."""
        skill_md = self._contents(self.shareable)["google-maps/SKILL.md"]
        self.assertNotIn("carries a live Google Maps API key", skill_md)

    def test_it_is_otherwise_complete(self) -> None:
        """The positive case: stripping the key did not strip the Skill.

        Without this, a builder that emitted an empty zip would pass every
        assertion above.
        """
        names = set(self._contents(self.shareable))
        self.assertIn("google-maps/SKILL.md", names)
        self.assertIn("google-maps/scripts/maps.py", names)
        self.assertIn("google-maps/references/commands.md", names)
        self.assertIn("google-maps/references/setup.md", names)


class TestPersonalBuild(SkillBuildTestCase):
    """What must be in the package built for one person's own account."""

    def test_the_key_is_bundled(self) -> None:
        """The positive case: the build does the thing it exists to do."""
        contents = self._contents(self.personal)
        key_member = f"google-maps/{self.build.BUNDLED_KEY_NAME}"
        self.assertIn(key_member, contents)
        self.assertEqual(contents[key_member].strip(), FAKE_KEY)

    def test_it_is_stamped_do_not_share(self) -> None:
        """Whoever opens the bundle sees the warning before anything else."""
        skill_md = self._contents(self.personal)["google-maps/SKILL.md"]
        self.assertIn("carries a live Google Maps API key", skill_md)
        # Above the setup section, not buried under it.
        self.assertLess(
            skill_md.index("carries a live Google Maps API key"),
            skill_md.index("## Before the first call"),
        )

    def test_the_two_builds_are_different_files(self) -> None:
        """Different names are what keep the wrong one from being sent."""
        self.assertNotEqual(self.build.SHAREABLE_NAME, self.build.PERSONAL_NAME)
        self.assertIn("personal", self.build.PERSONAL_NAME)


class TestKeyReading(SkillBuildTestCase):
    """What the builder accepts as a key file."""

    def test_bare_value_and_name_equals_value_both_work(self) -> None:
        """A file copied from the server's .env needs no editing."""
        bare = self.workspace / "bare.txt"
        bare.write_text(f"{FAKE_KEY}\n", encoding="utf-8")
        self.assertEqual(self.build.read_key(bare), FAKE_KEY)

        dotenv = self.workspace / "dotenv"
        dotenv.write_text(f"# comment\nGOOGLE_MAPS_API_KEY={FAKE_KEY}\n", encoding="utf-8")
        self.assertEqual(self.build.read_key(dotenv), FAKE_KEY)

    def test_a_file_with_no_key_fails_loudly(self) -> None:
        """Better to stop than to ship a bundle whose key is a blank line."""
        empty = self.workspace / "empty.txt"
        empty.write_text("# nothing here\n\n", encoding="utf-8")
        with self.assertRaises(SystemExit):
            self.build.read_key(empty)


if __name__ == "__main__":
    unittest.main()
