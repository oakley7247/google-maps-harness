# =============================================================================
# test_skill_manifest.py — the Skill's frontmatter, and whether its docs still
# describe the commands it has.
#
# Part of: google-maps-harness test suite.
# This exists because the frontmatter was invalid once and nothing here noticed.
# A description containing ": " parses as a YAML mapping, so the whole manifest
# was malformed and claude.ai would have rejected the upload; an external
# validator caught it, which is luck rather than a process. The rules below are
# Anthropic's documented limits, checked without a YAML dependency so they run
# wherever the suite does.
# =============================================================================
"""The Skill manifest's documented constraints, and doc-to-CLI agreement."""

import importlib.util
import re
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SKILL_DIR = _REPO_ROOT / "skill" / "google-maps"
_SKILL_MD = _SKILL_DIR / "SKILL.md"

# Anthropic's published limits for a Skill's frontmatter.
_MAX_NAME_CHARS = 64
_MAX_DESCRIPTION_CHARS = 1024
_NAME_PATTERN = re.compile(r"\A[a-z0-9-]+\Z")
_RESERVED_WORDS = ("anthropic", "claude")


def _frontmatter(text: str) -> dict[str, str]:
    """Pull the YAML frontmatter out of a SKILL.md as flat key/value pairs.

    Args:
        text: The file's contents.

    Returns:
        The top-level scalars. Parsed by hand rather than with a YAML library so
        this test needs no dependency the project does not otherwise have — and
        so it sees the raw text, which is where the defect it guards lives.

    Raises:
        AssertionError: The file does not open with a delimited block.
    """
    match = re.match(r"\A---\n(.*?)\n---\n", text, re.S)
    assert match, "SKILL.md must open with a --- delimited frontmatter block"
    fields: dict[str, str] = {}
    for line in match.group(1).split("\n"):
        if line and not line.startswith((" ", "\t")) and ":" in line:
            name, _, value = line.partition(":")
            fields[name.strip()] = value.strip()
    return fields


class TestFrontmatter(unittest.TestCase):
    """What claude.ai will and will not accept on upload."""

    def setUp(self) -> None:
        """Read the manifest once."""
        self.text = _SKILL_MD.read_text(encoding="utf-8")
        self.fields = _frontmatter(self.text)

    def test_required_fields_are_present(self) -> None:
        """name and description are the two the platform requires."""
        self.assertIn("name", self.fields)
        self.assertIn("description", self.fields)

    def test_name_matches_the_documented_shape(self) -> None:
        """Lowercase letters, digits, and hyphens, up to 64 characters."""
        name = self.fields["name"]
        self.assertLessEqual(len(name), _MAX_NAME_CHARS)
        self.assertRegex(name, _NAME_PATTERN)

    def test_name_avoids_the_reserved_words(self) -> None:
        """A name containing "anthropic" or "claude" is rejected outright."""
        for word in _RESERVED_WORDS:
            self.assertNotIn(word, self.fields["name"].lower())

    def test_description_is_present_and_within_the_ceiling(self) -> None:
        """Non-empty and at most 1024 characters."""
        description = self.fields["description"]
        self.assertTrue(description)
        self.assertLessEqual(len(description), _MAX_DESCRIPTION_CHARS)

    def test_no_unquoted_scalar_carries_a_colon_space(self) -> None:
        """The defect this module was written for.

        A plain YAML scalar may not contain ": " — the parser reads it as a
        nested mapping and the whole manifest becomes invalid. It is invisible
        on the page and fatal on upload, and the natural way to write a
        description ("Use this when: ...") walks straight into it.
        """
        for name, value in self.fields.items():
            if value.startswith(("'", '"', "|", ">")):
                continue  # A quoted or block scalar may contain anything.
            with self.subTest(field=name):
                self.assertNotIn(": ", value)

    def test_no_field_contains_an_xml_tag(self) -> None:
        """The platform rejects XML tags in either field."""
        for name, value in self.fields.items():
            with self.subTest(field=name):
                self.assertNotRegex(value, r"<[A-Za-z/][^>]*>")

    def test_the_body_is_not_empty(self) -> None:
        """The positive case: valid frontmatter over nothing is not a Skill."""
        body = self.text.split("---\n", 2)[-1]
        self.assertGreater(len(body.strip()), 500)


class TestDocsMatchTheCli(unittest.TestCase):
    """Whether the documentation still describes the commands that exist."""

    def setUp(self) -> None:
        """Load the Skill script and read what its parser actually accepts."""
        spec = importlib.util.spec_from_file_location(
            "skill_maps", _SKILL_DIR / "scripts" / "maps.py"
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        # argparse offers no public route to the subcommands a parser registered,
        # and reading them from the parser is the point: a maintained list here
        # would drift in exactly the way these tests exist to catch.
        actions = module.build_parser()._subparsers._group_actions
        self.commands = set(actions[0].choices)

    def test_every_command_the_reference_documents_exists(self) -> None:
        """A documented command that was renamed sends the reader to an error.

        The reference is where Claude looks when a flag is not obvious, so drift
        here costs a whole tool call to discover.
        """
        reference = (_SKILL_DIR / "references" / "commands.md").read_text(encoding="utf-8")
        documented = set(re.findall(r"^### `([a-z-]+)", reference, re.M))
        self.assertTrue(documented, "the reference documents no commands at all")
        self.assertEqual(documented - self.commands, set())

    def test_every_command_the_manifest_names_exists(self) -> None:
        """Same for the table in SKILL.md, which is read on every trigger."""
        manifest = _SKILL_MD.read_text(encoding="utf-8")
        table = re.findall(r"^\| .+ \| `([a-z-]+)` \|$", manifest, re.M)
        self.assertTrue(table, "SKILL.md's command table found no commands")
        self.assertEqual(set(table) - self.commands, set())

    def test_every_command_is_documented_somewhere(self) -> None:
        """The other direction: a command nobody wrote down is one nobody uses."""
        manifest = _SKILL_MD.read_text(encoding="utf-8")
        reference = (_SKILL_DIR / "references" / "commands.md").read_text(encoding="utf-8")
        for command in sorted(self.commands):
            with self.subTest(command=command):
                self.assertTrue(
                    f"`{command}`" in manifest or f"`{command}`" in reference,
                    f"{command} is not mentioned in SKILL.md or the reference",
                )


if __name__ == "__main__":
    unittest.main()
