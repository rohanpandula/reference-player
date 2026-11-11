"""The merge helper and the Home Assistant key contract: no Git, no network, no Home Assistant."""

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RESOLVER = REPO / "PrivateBuild/resolve-pbxproj-conflicts.py"


class ResolvePbxprojConflictsTests(unittest.TestCase):
    # The resolver keeps whatever identifier this checkout is configured with (identity.json).
    BUNDLE = json.loads((REPO / "PrivateBuild/identity.json").read_text())["bundle_id"]
    PRIVATE = f"\t\t\t\tPRODUCT_BUNDLE_IDENTIFIER = {BUNDLE};"
    EXTENSION = f"\t\t\t\tPRODUCT_BUNDLE_IDENTIFIER = {BUNDLE}.TopShelfExtension;"
    OTHER = "\t\t\t\tPRODUCT_BUNDLE_IDENTIFIER = org.someone.else;"
    STOCK = "\t\t\t\tPRODUCT_BUNDLE_IDENTIFIER = com.gstudios.rivulet;"
    OLD = "\t\t\t\tMARKETING_VERSION = 1.2.0;"
    NEW = "\t\t\t\tMARKETING_VERSION = 1.3.0;"

    def resolve(self, text, *python_flags):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "project.pbxproj"
            path.write_bytes(text.encode())
            done = subprocess.run([sys.executable, *python_flags, str(RESOLVER), str(path)], capture_output=True, text=True)
            return done, path.read_bytes().decode()

    def hunk(self, ours, theirs, base=None):
        middle = ["||||||| base", *base] if base is not None else []
        return "\n".join(["<<<<<<< HEAD", *ours, *middle, "=======", *theirs, ">>>>>>> upstream/v1.3.0-126"])

    def test_the_expected_shape_keeps_upstreams_version_and_the_private_identifier(self):
        hunk = self.hunk([self.OLD, self.PRIVATE], [self.NEW, self.STOCK])
        done, text = self.resolve("before\n" + hunk + "\nmiddle\n" + hunk + "\nafter\n")
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertIn("resolved 2 ", done.stdout)
        self.assertEqual("before\n" + self.NEW + "\n" + self.PRIVATE + "\nmiddle\n" + self.NEW + "\n" + self.PRIVATE + "\nafter\n", text)

    def test_an_extension_identifier_under_ours_is_kept(self):
        done, text = self.resolve(self.hunk([self.OLD, self.EXTENSION], [self.NEW, self.STOCK]) + "\n")
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(self.NEW + "\n" + self.EXTENSION + "\n", text)

    def test_a_file_without_conflicts_resolves_nothing_and_is_untouched(self):
        original = "// !$*UTF8*$!\n{\n\tobjects = {\n" + self.NEW + "\n" + self.PRIVATE + "\n\t};\n}"   # no trailing newline
        done, text = self.resolve(original)
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertIn("resolved 0 ", done.stdout)
        self.assertEqual(original, text)

    def test_any_other_shape_is_refused_and_the_file_left_as_it_was(self):
        good = self.hunk([self.OLD, self.PRIVATE], [self.NEW, self.STOCK])
        shapes = {
            "diff3 style": self.hunk([self.OLD, self.PRIVATE], [self.NEW, self.STOCK], base=[self.OLD, self.STOCK]),
            "a third line": self.hunk([self.OLD, self.PRIVATE, "\t\t\t\tSWIFT_VERSION = 6.0;"], [self.NEW, self.STOCK]),
            "another setting": self.hunk(["\t\t\t\tSWIFT_VERSION = 5.0;", self.PRIVATE], ["\t\t\t\tSWIFT_VERSION = 6.0;", self.STOCK]),
            "sides swapped": self.hunk([self.NEW, self.STOCK], [self.OLD, self.PRIVATE]),
            "our side is not private": self.hunk([self.OLD, self.STOCK], [self.NEW, self.STOCK]),
            "our side is another identity": self.hunk([self.OLD, self.OTHER], [self.NEW, self.STOCK]),
            "our side only starts with the identifier": self.hunk([self.OLD, self.PRIVATE.replace(";", "x;")], [self.NEW, self.STOCK]),
            # The other line of a side is checked too: an unrelated setting there would be dropped silently.
            "our other line is another setting": self.hunk(["\t\t\t\tSWIFT_VERSION = 6.0;", self.PRIVATE], [self.NEW, self.STOCK]),
            "upstream's other line is another setting": self.hunk([self.OLD, self.PRIVATE], [self.NEW, "\t\t\t\tSWIFT_VERSION = 6.0;"]),
            "upstream's lines in the other order": self.hunk([self.OLD, self.PRIVATE], [self.STOCK, self.NEW]),
            "a key twice": self.hunk([self.OLD, self.PRIVATE], [self.NEW, self.NEW]),
            "a side of one line": self.hunk([self.PRIVATE], [self.NEW, self.STOCK]),
            "unterminated": "<<<<<<< HEAD\n" + self.OLD + "\n=======\n" + self.NEW,
        }
        for name, bad in shapes.items():
            for flags in ((), ("-O",)):   # explicit checks: optimisation must not switch the refusal off
                with self.subTest(shape=name, flags=flags):
                    original = good + "\nbetween\n" + bad + "\n"
                    done, text = self.resolve(original, *flags)
                    self.assertNotEqual(0, done.returncode)
                    self.assertIn("refused", done.stderr)
                    self.assertEqual(original, text, "nothing is written, not even the hunk that did resolve")


class HomeAssistantKeyContractTests(unittest.TestCase):
    """The package YAML reads the JSON the app posts. A renamed Swift field turns its attribute
    silently empty; a new field nobody reads is a dashboard gap."""

    @staticmethod
    def sent_keys():
        source = (REPO / "RivuletCore/Integrations/HomeAssistantReporter.swift").read_text()
        body = re.search(r"struct HomeAssistantPlaybackReport\b[^{]*\{(.*?)\n    static func idle", source, re.S).group(1)
        assert "keyEncodingStrategy = .convertToSnakeCase" in source
        fields = re.findall(r"^\s*var\s+(\w+)\s*:", body, re.M)
        # JSONEncoder's convertToSnakeCase, for plain camelCase names (no acronym runs here).
        return {re.sub(r"(?<!^)(?=[A-Z])", "_", field).lower() for field in fields}

    @staticmethod
    def read_keys():
        return set(re.findall(r"trigger\.json\.get\('(\w+)'\)", (REPO / "PrivateBuild/homeassistant/rivulet_playback.yaml").read_text()))

    def test_every_key_the_package_reads_is_sent(self):
        sent, read = self.sent_keys(), self.read_keys()
        self.assertGreater(len(sent), 30, "the struct parse found the report's fields")
        self.assertEqual(set(), read - sent, "the YAML reads keys the app never sends")

    def test_every_key_the_app_sends_is_read(self):
        self.assertEqual(set(), self.sent_keys() - self.read_keys(), "the app sends keys the YAML never reads")


if __name__ == "__main__":
    unittest.main()
