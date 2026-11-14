"""Exercise identity setup only in disposable copies, never the supplied checkout.

No Xcode, Apple credentials, network, or third-party Python packages are needed.
These tests validate setup safety; they do not claim that tvOS signing succeeds.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import plistlib
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

PRIVATE_DIR = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PRIVATE_DIR.parent
sys.path.insert(0, str(PRIVATE_DIR))
import configure  # noqa: E402


def identity(**overrides):
    value = {"schema_version": 1, "bundle_id": "net.builder.referenceplayer",
             "team_id": "ABCDE12345", "app_group": "group.net.builder.referenceplayer",
             "url_scheme": "referenceplayer-test", "display_name": "Reference Player Test"}
    value.update(overrides)
    return value


class ConfigureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="rivulet-identity-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "project with spaces"
        for relative in (*configure.MANAGED_FILES, configure.STATE_FILE):
            destination = self.root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(SOURCE_ROOT / relative, destination)
        # The user's checkout may already be configured. Normalize only this
        # newly created disposable copy so tests do not depend on their IDs.
        # Use validated internal edits: the production public planner must
        # continue to reject placeholder identities as signing destinations.
        raw, modes = configure._inputs(self.root)
        previous = configure._state(raw[configure.STATE_FILE])
        placeholder = identity(**configure.PLACEHOLDER)
        configure.validate_identity(placeholder, allow_placeholder=True)
        configure.apply_plan(self.root, configure._edits(raw, modes, previous, placeholder))

    def snapshot(self):
        return {str(path.relative_to(self.root)): path.read_bytes()
                for path in self.root.rglob("*") if path.is_file() and not path.is_symlink()}

    def setup_identity(self, value=None):
        value = identity() if value is None else value
        edits = configure.plan_configuration(self.root, value)
        configure.apply_plan(self.root, edits)
        return edits

    def assert_no_setup(self, value=None):
        before = self.snapshot()
        with self.assertRaises(configure.ConfigurationError):
            self.setup_identity(identity() if value is None else value)
        self.assertEqual(self.snapshot(), before)

    def test_placeholder_validates_but_cannot_sign(self):
        self.assertEqual(configure.validate_configuration(self.root)["team_id"], "")
        with self.assertRaises(configure.ConfigurationError):
            configure.validate_configuration(self.root, require_configured=True)

    def test_suite_fixture_works_after_user_has_configured_the_source(self):
        configured = identity(bundle_id="org.builder.alreadyconfigured", team_id="12345ABCDE",
                              app_group="group.org.builder.library", url_scheme="home-cinema")
        self.setup_identity(configured)
        before = self.snapshot()
        times = {p: (self.root / p).stat().st_mtime_ns for p in before}
        # Run the same tests against a configured source without recursively
        # running this regression. Each nested setUp must normalize its own copy.
        names = unittest.defaultTestLoader.getTestCaseNames(ConfigureTests)
        suite = unittest.TestSuite(ConfigureTests(name) for name in names
                                   if name != self._testMethodName)
        result = unittest.TestResult()
        with mock.patch.object(sys.modules[__name__], "SOURCE_ROOT", self.root):
            suite.run(result)
        self.assertGreater(result.testsRun, 0)
        self.assertTrue(result.wasSuccessful(), result.errors + result.failures)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(times, {p: (self.root / p).stat().st_mtime_ns for p in before})
        self.assertEqual(configure.validate_configuration(self.root, require_configured=True), configured)

    def test_all_bindings_update_together(self):
        wanted = identity(url_scheme="my-player")
        edits = self.setup_identity(wanted)
        self.assertEqual(len(edits), len(configure.MANAGED_FILES) + 1)
        self.assertEqual(configure.validate_configuration(self.root, require_configured=True), wanted)
        project = (self.root / configure.PROJECT).read_text()
        self.assertEqual(project.count('DEVELOPMENT_TEAM = "ABCDE12345";'), 14)
        self.assertEqual(project.count("PRODUCT_BUNDLE_IDENTIFIER = net.builder.referenceplayer;"), 6)
        for suffix in [".Tests", ".TopShelfExtension", ".AssetPackExtension"]:
            self.assertEqual(project.count("PRODUCT_BUNDLE_IDENTIFIER = net.builder.referenceplayer" + suffix + ";"), 2)
        for name in configure.ENTITLEMENTS:
            values = plistlib.loads((self.root / name).read_bytes())
            self.assertEqual(values["com.apple.security.application-groups"], [wanted["app_group"]])
            self.assertNotIn("com.apple.developer.devicecheck.appattest-environment", values)
        self.assertIn('scheme = "my-player"', (self.root / configure.LINK_SOURCE).read_text())
        for name in configure.CLOUD_FILES:
            self.assertIn("iCloud." + wanted["bundle_id"] + ".sync", (self.root / name).read_text())
            self.assertNotIn("iCloud.com.example.referenceplayer.sync", (self.root / name).read_text())

    def test_repeating_the_same_setup_does_not_rewrite_files(self):
        self.setup_identity()
        before = self.snapshot()
        times = {p: (self.root / p).stat().st_mtime_ns for p in before}
        self.assertEqual(self.setup_identity(), [])
        self.assertEqual(before, self.snapshot())
        self.assertEqual(times, {p: (self.root / p).stat().st_mtime_ns for p in before})

    def test_reconfiguration_uses_previous_state(self):
        self.setup_identity()
        second = identity(bundle_id="org.builder.homecinema", app_group="group.org.builder.media",
                          team_id="12345ABCDE", url_scheme="home-cinema")
        self.setup_identity(second)
        self.assertEqual(configure.validate_configuration(self.root, require_configured=True), second)
        self.assertNotIn("net.builder.referenceplayer", (self.root / configure.PROJECT).read_text())

    def test_pins_secrets_and_unrelated_files_are_untouched(self):
        untouched = {
            "Rivulet.xcodeproj/project.xcworkspace/xcshareddata/swiftpm/Package.resolved": b"pins must not change",
            "RivuletCore/Config/Secrets.swift": b"private local contents must not change",
            "notes.txt": b"user work stays untouched",
        }
        for name, data in untouched.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        self.setup_identity()
        for name, data in untouched.items():
            self.assertEqual((self.root / name).read_bytes(), data)

    def test_invalid_bundle_ids_fail_before_writes(self):
        for value in [None, "", "app", ".com.app", "com..app", "com.app.", "com.own_app",
                      "com.app;echo", "com.$(id).app", "../escape", "com.app/app", "com.äpp",
                      "com.app\nOTHER = 1", "com.example.other", "COM.EXAMPLE.APP",
                      "com.gstudios.rivulet", "COM.GSTUDIOS.RIVULET", "com." + "a" * 198]:
            with self.subTest(value=value):
                self.assert_no_setup(identity(bundle_id=value))

    def test_invalid_team_ids_fail_before_writes(self):
        for value in [None, "", "ABC", "abcdefghij", "ABCD1234567", "ABCDEFGHI!", "ABCDEFGHI\n",
                      "ZHB786H6YN", "$(whoami)", ["ABCDE12345"]]:
            with self.subTest(value=value):
                self.assert_no_setup(identity(team_id=value))

    def test_invalid_app_groups_fail_before_writes(self):
        for value in [None, "", "com.builder.app", "group.", "group.com..app", "group.com.app/evil",
                      "group.com.app\n", "group.com.bain.Rivulet", "group.com.BAIN.RIVULET",
                      "group.com.gstudios", "group.com.gstudios.rivulet", "group.com.example", "group.com.example.app",
                      "group.com." + "a" * 250]:
            with self.subTest(value=value):
                self.assert_no_setup(identity(app_group=value))
        self.assert_no_setup(identity(bundle_id="group.builder.app", app_group="group.builder.app"))

    def test_invalid_schemes_fail_before_writes(self):
        for value in [None, "", "a", "1app", "MyPlayer", "my player", "player:", "player/evil",
                      "rivulet", "http", "https", "file", "ftp", "mailto", "itms", "itms-apps", "a" * 64]:
            with self.subTest(value=value):
                self.assert_no_setup(identity(url_scheme=value))

    def test_invalid_display_names_fail_before_writes(self):
        # The name is written into project.pbxproj (quoted) and two Info.plists: nothing that needs escaping.
        for value in [None, "", " Padded", "Padded ", 'Say "hi"', "back\\slash", "semi;colon", "<tag>", "a&b",
                      "$(VAR)", "line\nbreak", "a" * 65]:
            with self.subTest(value=value):
                self.assert_no_setup(identity(display_name=value))

    def test_display_name_is_data_and_reaches_every_app(self):
        self.setup_identity(identity(display_name="Living Room Player"))
        project = (self.root / configure.PROJECT).read_text()
        self.assertEqual(project.count('INFOPLIST_KEY_CFBundleDisplayName = "Living Room Player";'), 6)
        self.assertEqual(project.count("INFOPLIST_KEY_CFBundleDisplayName = TopShelfExtension;"), 2)
        self.assertEqual(project.count('PRODUCT_NAME = "Reference Player";'), 6)
        for module in ("Rivulet", "Rivulet_iOS", "Rivulet_Mac"):
            self.assertEqual(project.count(f"PRODUCT_MODULE_NAME = {module};"), 2)
        for name in (configure.TV_INFO, configure.IOS_INFO, configure.MAC_INFO):
            self.assertEqual(plistlib.loads((self.root / name).read_bytes())["CFBundleName"],
                             "$(INFOPLIST_KEY_CFBundleDisplayName)", name)
        for name in (configure.IOS_INFO, configure.MAC_INFO):
            self.assertEqual(plistlib.loads((self.root / name).read_bytes())["CFBundleDisplayName"], "Living Room Player", name)
        self.assertEqual(configure.validate_configuration(self.root, require_configured=True)["display_name"], "Living Room Player")

    def test_app_product_and_module_name_drift_is_rejected(self):
        path = self.root / configure.PROJECT
        original = path.read_text()
        for before, after in [('PRODUCT_NAME = "Reference Player";', 'PRODUCT_NAME = Rivulet;'),
                              ('PRODUCT_MODULE_NAME = Rivulet;', 'PRODUCT_MODULE_NAME = Reference_Player;')]:
            with self.subTest(binding=before):
                self.assertIn(before, original)
                path.write_text(original.replace(before, after, 1))
                self.assert_no_setup()
        path.write_text(original)

    def test_bundle_menu_name_drift_is_rejected(self):
        for name in (configure.TV_INFO, configure.IOS_INFO, configure.MAC_INFO):
            path = self.root / name
            original = path.read_text()
            with self.subTest(platform=name):
                path.write_text(original.replace('$(INFOPLIST_KEY_CFBundleDisplayName)', 'Rivulet', 1))
                self.assert_no_setup()
            path.write_text(original)

    def test_placeholder_set_is_all_or_nothing(self):
        # The committed state is the whole placeholder set; any mix with real values is refused.
        for mix in [dict(configure.PLACEHOLDER, team_id="ABCDE12345"),
                    dict(configure.PLACEHOLDER, bundle_id="net.builder.referenceplayer"),
                    dict(configure.PLACEHOLDER, app_group="group.net.builder.referenceplayer"),
                    dict(configure.PLACEHOLDER, app_group="group.com.example.other"),
                    {"bundle_id": "net.builder.referenceplayer", "team_id": "", "app_group": "group.net.builder.referenceplayer"}]:
            with self.subTest(mix=mix):
                with self.assertRaises(configure.ConfigurationError):
                    configure.validate_identity(identity(**mix), allow_placeholder=True)
                self.assert_no_setup(identity(**mix))
        with self.assertRaises(configure.ConfigurationError):   # the placeholder itself is not a signing target
            configure.plan_configuration(self.root, identity(**configure.PLACEHOLDER))

    def test_cli_placeholder_restores_the_committed_identity(self):
        before = self.snapshot()
        self.setup_identity()
        result = self.run_cli("--placeholder")
        self.assertEqual(result.returncode, 0, result.stderr)
        after = {path: data for path, data in self.snapshot().items() if path != "PrivateBuild/configure.py"}
        self.assertEqual(after, before, "configure, then --placeholder, is a round trip")
        for arguments in [["--placeholder", "--team-id", "ABCDE12345"], ["--placeholder", "--bundle-id", "net.builder.x"]]:
            self.assertEqual(self.run_cli(*arguments).returncode, 2, arguments)

    def test_identity_field_types_and_schema_are_strict(self):
        bad = [identity(schema_version=True), identity(schema_version=2), identity(schema_version="1"),
               {**identity(), "unexpected": "value"},
               {k: v for k, v in identity().items() if k != "team_id"}, []]
        for value in bad:
            with self.subTest(value=value):
                self.assert_no_setup(value)

    def test_bad_identity_json_is_rejected(self):
        state = self.root / configure.STATE_FILE
        for raw in [b"{", b"null", b"[]", b"\xff", b'{"schema_version":1,"schema_version":1}',
                    json.dumps(identity(schema_version=99)).encode()]:
            with self.subTest(raw=raw):
                state.write_bytes(raw)
                self.assert_no_setup()

    def test_each_missing_managed_file_stops_before_writes(self):
        for name in (*configure.MANAGED_FILES, configure.STATE_FILE):
            with self.subTest(name=name):
                path = self.root / name
                data = path.read_bytes()
                path.unlink()
                self.assert_no_setup()
                path.write_bytes(data)

    def test_project_identity_or_sdk_drift_is_rejected(self):
        project = self.root / configure.PROJECT
        original = project.read_text()
        cases = [original.replace('DEVELOPMENT_TEAM = "";', 'DEVELOPMENT_TEAM = "0123456789";', 1),
                 original.replace('DEVELOPMENT_TEAM = "";', '', 1),
                 original.replace("com.example.referenceplayer.TopShelfExtension", "com.other.extension", 1),
                 original.replace('INFOPLIST_KEY_CFBundleDisplayName = "Reference Player Test";',
                                  'INFOPLIST_KEY_CFBundleDisplayName = "Different";', 1),
                 original.replace("sourceTree = SDKROOT;", "sourceTree = DEVELOPER_DIR;", 1),
                 original + "\nAPPATTEST_ENVIRONMENT = development;\n"]
        for value in cases:
            with self.subTest(value=value[-80:]):
                project.write_text(value)
                self.assert_no_setup()
        project.write_text(original)

    def test_group_and_url_drift_in_plists_is_rejected(self):
        for name in (*configure.ENTITLEMENTS, configure.TV_INFO):
            path = self.root / name
            original = path.read_bytes()
            with self.subTest(name=name):
                path.write_bytes(original.replace(b"group.com.example.referenceplayer", b"group.com.wrong.app", 1))
                self.assert_no_setup()
                path.write_bytes(original)
        path = self.root / configure.TV_INFO
        original = path.read_bytes()
        for replacement in [b"stock-scheme", b"rivulet"]:
            path.write_bytes(original.replace(b"<string>referenceplayer-test</string>", b"<string>" + replacement + b"</string>"))
            self.assert_no_setup()
        path.write_bytes(original)

    def test_swapped_bundle_ids_cannot_hide_behind_matching_counts(self):
        project = self.root / configure.PROJECT
        original = project.read_text()
        top_shelf = "com.example.referenceplayer.TopShelfExtension"
        asset_pack = "com.example.referenceplayer.AssetPackExtension"
        swapped = original.replace(top_shelf, "TEMPORARY_BUNDLE_MARKER")
        swapped = swapped.replace(asset_pack, top_shelf).replace("TEMPORARY_BUNDLE_MARKER", asset_pack)
        project.write_text(swapped)
        self.assert_no_setup()

    def test_changed_target_configuration_block_requires_review(self):
        project = self.root / configure.PROJECT
        project.write_text(project.read_text().replace(
            'Debug configuration for PBXNativeTarget "Rivulet"',
            'Debug configuration for PBXNativeTarget "Another Target"', 1))
        self.assert_no_setup()

    def test_app_attest_entitlement_cannot_return_silently(self):
        path = self.root / configure.ENTITLEMENTS[0]
        values = plistlib.loads(path.read_bytes())
        values["com.apple.developer.devicecheck.appattest-environment"] = "production"
        path.write_bytes(plistlib.dumps(values))
        self.assert_no_setup()

    def test_malformed_plist_and_utf8_are_rejected(self):
        path = self.root / configure.TV_INFO
        for raw in [b"not XML", b"\xff", plistlib.dumps(["not a dictionary"])]:
            path.write_bytes(raw)
            self.assert_no_setup()

    def test_runtime_bindings_must_match_saved_identity(self):
        for name in (*configure.GROUP_SOURCES, configure.LINK_SOURCE, *configure.IOS_IDENTIFIERS):
            path = self.root / name
            original = path.read_text()
            marker = ("group.com.example.referenceplayer" if name in configure.GROUP_SOURCES else
                      "referenceplayer-test" if name == configure.LINK_SOURCE else "com.example.referenceplayer")
            with self.subTest(name=name):
                path.write_text(original.replace(marker, "changed-elsewhere", 1))
                self.assert_no_setup()
                path.write_text(original)

    def test_ios_background_registration_must_match_runtime(self):
        path = self.root / configure.IOS_INFO
        path.write_text(path.read_text().replace("com.example.referenceplayer.downloads.activity.*", "wrong.*"))
        self.assert_no_setup()

    def test_refuses_symlink_target_without_touching_its_destination(self):
        path = self.root / configure.PROJECT
        external = Path(self.temporary.name) / "outside-project.pbxproj"
        external.write_bytes(path.read_bytes())
        before = external.read_bytes()
        path.unlink()
        path.symlink_to(external)
        self.assert_no_setup()
        self.assertEqual(external.read_bytes(), before)

    def test_refuses_symlink_parent(self):
        path = self.root / configure.GROUP_SOURCES[0]
        external = Path(self.temporary.name) / "outside-cache"
        external.mkdir()
        (external / path.name).write_bytes(path.read_bytes())
        path.unlink()
        path.parent.rmdir()
        path.parent.symlink_to(external, target_is_directory=True)
        self.assert_no_setup()

    def test_refuses_nonrepository_or_nonallowlisted_paths(self):
        for root in [self.root / "missing", self.root / configure.PROJECT]:
            with self.subTest(root=root), self.assertRaises(configure.ConfigurationError):
                configure.plan_configuration(root, identity())
        for path in ["../outside", "/tmp/elsewhere", "RivuletCore/Config/Secrets.swift"]:
            with self.subTest(path=path), self.assertRaises(configure.ConfigurationError):
                configure._safe_file(self.root, path)

    def test_intervening_user_edit_aborts_before_any_replacement(self):
        edits = configure.plan_configuration(self.root, identity())
        path = self.root / configure.GROUP_SOURCES[-1]
        path.write_text(path.read_text() + "\n// An edit after planning.\n")
        before = self.snapshot()
        with self.assertRaises(configure.ConfigurationError):
            configure.apply_plan(self.root, edits)
        self.assertEqual(self.snapshot(), before)

    def test_staging_failure_leaves_sources_unchanged(self):
        edits = configure.plan_configuration(self.root, identity())
        before = self.snapshot()
        with mock.patch.object(configure, "_stage", side_effect=OSError("simulated disk full")):
            with self.assertRaises(configure.ConfigurationError):
                configure.apply_plan(self.root, edits)
        self.assertEqual(self.snapshot(), before)

    def test_mid_commit_failure_rolls_back_and_cleans_staged_files(self):
        edits = configure.plan_configuration(self.root, identity())
        before = self.snapshot()
        actual_replace = os.replace
        calls = 0

        def fail_third(source, destination):
            nonlocal calls
            calls += 1
            if calls == 3:
                raise OSError("simulated replace failure")
            return actual_replace(source, destination)

        with mock.patch.object(configure.os, "replace", side_effect=fail_third):
            with self.assertRaises(configure.ConfigurationError):
                configure.apply_plan(self.root, edits)
        self.assertEqual(self.snapshot(), before)

    def test_original_file_permissions_are_preserved(self):
        path = self.root / configure.GROUP_SOURCES[0]
        path.chmod(0o640)
        self.setup_identity()
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o640)

    def run_cli(self, *arguments):
        script = self.root / "PrivateBuild/configure.py"
        shutil.copy2(PRIVATE_DIR / "configure.py", script)
        return subprocess.run([sys.executable, str(script), *arguments],
                              cwd=self.temporary.name, text=True, capture_output=True, timeout=20)

    def test_cli_uses_its_checkout_not_the_working_directory(self):
        result = self.run_cli("--bundle-id", "net.builder.referenceplayer", "--team-id", "ABCDE12345")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(configure.validate_configuration(self.root, require_configured=True), identity())
        self.assertFalse((Path(self.temporary.name) / "Rivulet.xcodeproj").exists())

    def test_cli_dry_run_does_not_modify_sources(self):
        shutil.copy2(PRIVATE_DIR / "configure.py", self.root / "PrivateBuild/configure.py")
        before = self.snapshot()
        result = self.run_cli("--bundle-id", "net.builder.referenceplayer", "--team-id", "ABCDE12345", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_cli_rejects_missing_and_hostile_arguments(self):
        for arguments in [[], ["--bundle-id", "com.builder.app"],
                          ["--bundle-id", "com.app;touch bad", "--team-id", "ABCDE12345"],
                          ["--root", "/tmp", "--bundle-id", "com.builder.app", "--team-id", "ABCDE12345"]]:
            with self.subTest(arguments=arguments):
                result = self.run_cli(*arguments)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(configure.validate_configuration(self.root)["team_id"], "")


if __name__ == "__main__":
    unittest.main()
