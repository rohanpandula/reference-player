"""check-private-strings.py on fabricated string data: the real catalogs, no build needed (xcrun required)."""

import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "PrivateBuild/check-private-strings.py"
SPEC = importlib.util.spec_from_file_location("check_private_strings", SCRIPT)
check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check)
SETTINGS = "Rivulet/Views/Settings/UIKit/PrivateSettingsContent.swift"


@unittest.skipUnless(shutil.which("xcrun"), "needs Xcode's xcstringstool")
class CheckPrivateStringsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.objects = Path(temp.name)
        base = check.baseline()
        private = [line.split("\t")[-1] for line in check.git("diff", "--name-status", "--diff-filter=A", base, "--",
                                                               *check.APP_ROOTS).splitlines() if line.endswith(".swift")]
        for path in private:
            self.data(path, {})
        keys = json.loads((REPO / check.PRIVATE_CATALOG).read_text())["strings"]
        self.data(SETTINGS, {"Private": [self.entry(key, 10) for key in keys],
                             "Localizable": [self.entry("On", 20), self.entry("OK", 21)]})

    @staticmethod
    def entry(key, line):
        return {"comment": "", "key": key, "location": {"startingColumn": 1, "startingLine": line}}

    def data(self, path, tables):
        (self.objects / (Path(path).stem + ".stringsdata")).write_text(
            json.dumps({"source": str(REPO / path), "tables": tables, "version": 1}))

    def edit(self, change):
        file = self.objects / (Path(SETTINGS).stem + ".stringsdata")
        record = json.loads(file.read_text())
        change(record["tables"])
        file.write_text(json.dumps(record))

    def run_check(self):
        return subprocess.run([sys.executable, str(SCRIPT), "--objects", str(self.objects)], capture_output=True, text=True)

    def test_the_catalog_as_used_passes(self):
        done = self.run_check()
        self.assertEqual(0, done.returncode, done.stderr)

    def test_a_private_literal_headed_for_localizable_fails(self):
        self.edit(lambda tables: tables["Localizable"].append(self.entry("Check Keys", 30)))
        self.assertIn("private 'Check Keys' is headed for Localizable", self.run_check().stderr)

    def test_a_hook_line_in_an_upstream_file_is_held_to_the_private_table(self):
        path = "Rivulet/Views/Player/UIKit/PlayerInfoPaneViews.swift"
        lines = (REPO / path).read_text().splitlines()
        hook = 1 + next(i for i, line in enumerate(lines) if 'case .dualLayer: String(localized:' in line)
        upstream = 1 + next(i for i, line in enumerate(lines) if 'String(localized: "HDR10 Base Layer")' in line)
        self.data(path, {"Localizable": [self.entry("HDR10 Base Layer", upstream)]})
        self.assertEqual(0, self.run_check().returncode, "upstream's own literal stays on upstream's table")
        self.data(path, {"Localizable": [self.entry("Dual Layer (FEL)", hook)]})
        self.assertIn(f"{path}:{hook}: private 'Dual Layer (FEL)' is headed for Localizable", self.run_check().stderr)

    def test_a_key_missing_from_the_catalog_fails(self):
        self.edit(lambda tables: tables["Private"].append(self.entry("A New Private Row", 31)))
        self.assertIn("lacks 'A New Private Row'", self.run_check().stderr)

    def test_a_key_nothing_uses_fails(self):
        # Untranslated, a sync drops it; translated (Height Speakers has German), a sync marks it stale.
        for key in ("Check Keys", "Height Speakers"):
            with self.subTest(key=key):
                self.setUp()
                self.edit(lambda tables: tables.__setitem__("Private", [e for e in tables["Private"] if e["key"] != key]))
                self.assertIn(f"keeps {key!r}, which no code uses", self.run_check().stderr)

    def test_a_private_file_without_string_data_fails(self):
        (self.objects / "PrivateLaunch.stringsdata").unlink()
        self.assertIn("no string data for Rivulet/Config/PrivateLaunch.swift", self.run_check().stderr)


class PrivateSwiftListingTests(unittest.TestCase):
    """The file listing needs only Git: a renamed private file is still a private file."""

    def test_a_renamed_file_is_listed_under_its_new_name(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)

            def git(*args):
                return subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t",
                                       "-c", "commit.gpgsign=false", *args], check=True, capture_output=True, text=True).stdout
            git("init", "-q")
            body = "".join(f"let line{i} = {i}\n" for i in range(40))
            (root / "Rivulet").mkdir()
            (root / "Rivulet/Upstream.swift").write_text(body)
            git("add", "-A")
            git("commit", "-qm", "base")
            base = git("rev-parse", "HEAD").strip()
            git("mv", "Rivulet/Upstream.swift", "Rivulet/PrivateRenamed.swift")
            (root / "Rivulet/PrivateRenamed.swift").write_text(body + 'let t = String(localized: "X", table: "Private")\n')
            git("commit", "-qam", "rename")
            with unittest.mock.patch.object(check, "ROOT", root):
                private_files, added = check.private_swift(base)
            self.assertIn("Rivulet/PrivateRenamed.swift", private_files)


if __name__ == "__main__":
    unittest.main()
