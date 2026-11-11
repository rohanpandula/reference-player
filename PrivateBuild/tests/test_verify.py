"""Portable negative tests of our guardrails; no Apple frameworks or network."""

import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("private_verify", Path(__file__).resolve().parents[1] / "verify.py")
verify_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verify_module)
REPO = Path(__file__).resolve().parents[2]


class VerificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        # configure.py owns and independently tests the identity-file schema.
        # Here we test integration/error propagation rather than duplicating it.
        identity_patch = patch.object(verify_module, "validate_private_identity", return_value=None)
        self.identity_validator = identity_patch.start()
        self.addCleanup(identity_patch.stop)
        self.project_path = "Rivulet.xcodeproj/project.pbxproj"
        self.player_path = "Rivulet/Services/Plex/Playback/AetherPlayer.swift"
        self.write(self.project_path, '''
        B2000020AAAA000000000001 /* XCRemoteSwiftPackageReference "AetherEngine" */ = {
            repositoryURL = "https://github.com/superuser404notfound/AetherEngine";
            requirement = { kind = exactVersion; version = 7.61.0; };
            traits = (DolbyVisionFelCurves, DualLayerDolbyVision, DolbyVisionStillReshaping, TrueHDObjectAudio, DTSXObjectAudio,);
\t\t};
DEVELOPMENT_TEAM = ABCDE12345;
PRODUCT_BUNDLE_IDENTIFIER = net.builder.referenceplayer;
''')
        self.lock = {"version": 3, "pins": []}
        for identity, (url, version, revision) in verify_module.PINS.items():
            state = {"revision": revision}
            if version is not None:
                state["version"] = version
            self.lock["pins"].append({"identity": identity, "kind": "remoteSourceControl", "location": url, "state": state})
        self.write_json(verify_module.LOCK, self.lock)
        self.write_json("PrivateBuild/UPSTREAM.json", {
            "schema_version": 1, "rivulet": {"revision": verify_module.BASELINE},
            "engine": {"version": verify_module.PINS["aetherengine"][1], "revision": verify_module.PINS["aetherengine"][2]},
            "truehd": {"version": verify_module.PINS["libtruehd"][1], "revision": verify_module.PINS["libtruehd"][2]},
        })
        self.write("Rivulet/Config/PrivatePlaybackDefaults.swift", '''
defaults.register(defaults: [AetherPlayer.dolbyVisionFELKey: true])
''')
        # The files that carry private hooks or parse engine log text are the real ones: the hook
        # and literal checks read them as they are (AetherPlayer and RivuletApp included).
        for path in {hook[0] for hook in verify_module.HOOKS} | set(verify_module.ENGINE_LOG_LITERALS) | set(verify_module.PRIVATE_SWIFT):
            self.write(path, (REPO / path).read_text(encoding="utf-8"))
        self.write("RivuletCore/Config/Secrets.swift", 'enum Secrets {\nstatic let sentryDSN = ""\n}\n')
        self.write("RivuletCore/Config/PrivateBuildPolicy.swift", "enum PrivateBuildPolicy { static let allowsUpstreamHostedServices = false }")
        self.write_wrapper(self.ANSWER, self.GUARD, self.ATTEST)
        self.write("RivuletCore/Integrations/DirectMetadata.swift", self.DIRECT)
        self.write("RivuletCore/Diagnostics/SentryStartup.swift", '''
guard !Secrets.sentryDSN.isEmpty else { return }
SentrySDK.start { options in options.dsn = Secrets.sentryDSN }
''')
        self.write("Scripts/sentry-upload-dsyms.sh", '''#!/bin/sh
set -u
echo "sentry: disabled in this fork (no symbols or sources uploaded)"
exit 0
''')
        self.write_json("PrivateBuild/identity.json", {"schema_version": 1, "bundle_id": "net.builder.referenceplayer", "team_id": "ABCDE12345"})

    def write(self, path, value):
        destination = self.root / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(value, encoding="utf-8")

    def write_json(self, path, value):
        self.write(path, json.dumps(value))

    def replace(self, path, old, new):
        text = (self.root / path).read_text()
        self.assertIn(old, text, f"the fixture text this test edits is gone from {path}: upstream changed it, update the test")
        self.write(path, text.replace(old, new))

    def pins(self):
        """Digests of the fixture's own copies of the pinned files; a missing one keeps a digest nothing has."""
        return {path: hashlib.sha256((self.root / path).read_bytes()).hexdigest() if (self.root / path).exists() else "0" * 64
                for path in verify_module.REVIEWED_SOURCES}

    def errors(self, pinned=None, **kwargs):
        # The fixture's files stand for the reviewed ones, unless a test pins something else.
        with patch.object(verify_module, "REVIEWED_SOURCES", self.pins() if pinned is None else pinned):
            return verify_module.verify(self.root, **kwargs)

    def test_reviewed_fixture_passes(self):
        self.assertEqual([], self.errors())

    def test_signing_delegates_to_identity_validator(self):
        self.assertEqual([], self.errors(require_signing=True))
        self.identity_validator.assert_called_once_with(self.root, require_configured=True)

    def test_conflict_markers_fail_even_when_the_hook_on_our_side_reads_as_present(self):
        vm = "Rivulet/Views/Player/UniversalPlayerViewModel.swift"
        text = (self.root / vm).read_text()
        self.assertIn("endHomeAssistantSession()", text)
        self.write(vm, text.replace("endHomeAssistantSession()", "\n<<<<<<< HEAD\nendHomeAssistantSession()\n=======\n"
                                    "upstreamLine()\n>>>>>>> upstream/v1\n", 1))
        errors = self.errors()
        self.assertFalse(any("Private hook missing" in error for error in errors),
                         "the hook on our side still counts: the markers are the only failure")
        marked = [error for error in errors if error.startswith(f"Unresolved merge conflict marker in {vm}:")]
        self.assertEqual(3, len(marked), errors)

    def test_conflict_markers_in_the_project_file_and_a_catalog_fail(self):
        self.write(self.project_path, (self.root / self.project_path).read_text() + "<<<<<<< HEAD\nA\n=======\nB\n>>>>>>> t\n")
        self.write("RivuletiOS/Localizable.xcstrings", '{\n<<<<<<< HEAD\n  "a" : {}\n=======\r\n  "b" : {}\n>>>>>>> t\n}\n')
        errors = self.errors()
        self.assertTrue(any(error.startswith(f"Unresolved merge conflict marker in {self.project_path}:") for error in errors), errors)
        catalog = [error for error in errors if "RivuletiOS/Localizable.xcstrings:" in error]
        self.assertEqual(3, len(catalog), "a CRLF separator line counts too")
        self.assertIn("RivuletiOS/Localizable.xcstrings:2:", catalog[0])

    def test_marker_like_text_that_is_not_at_a_line_start_passes(self):
        self.write("Rivulet/Doc.swift", '// ======= a ruler\nlet s = "<<<<<<< HEAD"\n  =======\n')
        self.assertEqual([], self.errors())

    def test_no_tracked_source_holds_a_conflict_marker_today(self):
        listed = subprocess.run(["git", "-C", str(REPO), "ls-files", "*.swift", "*.pbxproj", "*.resolved", "*.xcstrings"],
                                capture_output=True, text=True)
        if listed.returncode != 0:
            self.skipTest("not a git checkout")
        found = [f"{path}: {error}" for path in listed.stdout.splitlines() if (REPO / path).is_file()
                 for error in verify_module.conflict_markers(path, (REPO / path).read_text(encoding="utf-8", errors="replace"))]
        self.assertEqual([], found)

    def test_engine_range_rejected(self):
        self.replace(self.project_path, "exactVersion", "upToNextMajorVersion")
        self.assertTrue(any("exactVersion" in error for error in self.errors()))

    def test_new_engine_version_rejected(self):
        self.replace(self.project_path, "7.61.0", "7.62.0")
        self.assertTrue(any("7.61.0" in error for error in self.errors()))

    def test_missing_audio_trait_rejected(self):
        self.replace(self.project_path, "TrueHDObjectAudio,", "")
        self.assertTrue(any("traits" in error for error in self.errors()))

    def test_curve_only_replacement_rejected(self):
        self.replace(self.project_path, "DualLayerDolbyVision", "DolbyVisionFelCurves")
        self.assertTrue(any("traits" in error for error in self.errors()))

    def test_duplicate_pin_rejected(self):
        self.lock["pins"].append(self.lock["pins"][0])
        self.write_json(verify_module.LOCK, self.lock)
        self.assertTrue(any("duplicate" in error for error in self.errors()))

    def test_invalid_pin_identity_is_reported_not_raised(self):
        self.lock["pins"][0]["identity"] = ["aetherengine"]
        self.write_json(verify_module.LOCK, self.lock)
        self.assertTrue(any("string identities" in error for error in self.errors()))

    def test_invalid_metadata_section_is_reported_not_raised(self):
        self.write_json("PrivateBuild/UPSTREAM.json", {"schema_version": 1, "rivulet": [], "engine": []})
        self.assertTrue(any("UPSTREAM.json" in error for error in self.errors()))

    def test_decoder_revision_drift_rejected(self):
        pin = next(pin for pin in self.lock["pins"] if pin["identity"] == "libtruehd")
        pin["state"]["revision"] = "0" * 40
        self.write_json(verify_module.LOCK, self.lock)
        self.assertTrue(any("libtruehd" in error for error in self.errors()))

    def test_non_audio_dependency_drift_rejected(self):
        pin = next(pin for pin in self.lock["pins"] if pin["identity"] == "smbclient")
        pin["state"]["version"] = "9.0.0"
        self.write_json(verify_module.LOCK, self.lock)
        self.assertTrue(any("smbclient" in error for error in self.errors()))

    def test_dependency_source_redirect_rejected(self):
        self.lock["pins"][0]["location"] = "https://example.invalid/AetherEngine"
        self.write_json(verify_module.LOCK, self.lock)
        self.assertTrue(any("aetherengine" in error for error in self.errors()))

    def test_commented_out_runtime_audio_rejected(self):
        self.replace(self.player_path, "if let heightLayout", "// if let heightLayout")
        self.assertTrue(any("Height Speakers" in error for error in self.errors()))

    def test_commented_out_runtime_fel_rejected(self):
        self.replace(self.player_path, "options.preferDualLayerDolbyVision = panelDV && felOn", "/* options.preferDualLayerDolbyVision = panelDV && felOn */")
        self.assertTrue(any("FEL setting" in error for error in self.errors()))

    def test_empty_secrets_required(self):
        self.replace("RivuletCore/Config/Secrets.swift", 'sentryDSN = ""', 'sentryDSN = "https://example.invalid/production"')
        self.assertTrue(any("empty Sentry" in error for error in self.errors()))

    def test_upload_guard_cannot_be_removed(self):
        self.replace("Scripts/sentry-upload-dsyms.sh", "exit 0", "sentry-cli upload")
        self.assertTrue(any("symbol/source uploader" in error for error in self.errors()))

    def test_a_registered_height_layout_or_fel_off_is_rejected(self):
        for old, new in (("dolbyVisionFELKey: true\n", 'dolbyVisionFELKey: true,\n            AetherPlayer.heightSpeakersKey: "5.1.2"\n'),
                         ("dolbyVisionFELKey: true", "dolbyVisionFELKey: false")):
            with self.subTest(new=new):
                original = (self.root / "Rivulet/Config/PrivatePlaybackDefaults.swift").read_text()
                self.replace("Rivulet/Config/PrivatePlaybackDefaults.swift", old, new)
                self.assertTrue(any("Height Speakers unregistered" in error for error in self.errors()))
                self.write("Rivulet/Config/PrivatePlaybackDefaults.swift", original)

    def test_nothing_is_contacted_until_configured(self):
        index, reporter = "RivuletCore/Integrations/FormatIndex.swift", "RivuletCore/Integrations/HomeAssistantReporter.swift"
        cases = ((index, 'static let defaultURL = ""', 'static let defaultURL = "http://index.example.invalid/f.json"', "no built-in URL"),
                 (index, "as? Bool ?? false", "as? Bool ?? true", "Format Tags must default Off"),
                 (reporter, 'static let defaultAddress = ""', 'static let defaultAddress = "http://ha.example.invalid:8123"',
                  "no built-in address"),
                 (reporter, "get { UserDefaults.standard.bool(forKey: Keys.enabled) }",
                  "get { UserDefaults.standard.object(forKey: Keys.enabled) as? Bool ?? true }", "default Off"))
        for path, old, new, says in cases:
            with self.subTest(new=new):
                original = (self.root / path).read_text()
                self.replace(path, old, new)
                self.assertTrue(any(says in error for error in self.errors()), says)
                self.write(path, original)

    def test_late_defaults_registration_rejected(self):
        self.write("Rivulet/RivuletApp.swift", 'AetherPlayer.settleFELDefault(existingInstall: false)\nPrivatePlaybackDefaults.register()')
        self.assertTrue(any("before the upstream FEL migration" in error for error in self.errors()))

    def test_upstream_policy_cannot_be_enabled(self):
        self.replace("RivuletCore/Config/PrivateBuildPolicy.swift", "= false", "= true")
        self.assertTrue(any("hosted services" in error for error in self.errors()))

    GUARD = "guard PrivateBuildPolicy.allowsUpstreamHostedServices else { throw URLError(.userAuthenticationRequired) }"
    ATTEST = "let client = AppAttestClient.shared"
    ANSWER = "if let answer = try await DirectMetadata.answer(request) { return answer }"

    def write_wrapper(self, *statements, after=""):
        """The attested wrapper as upstream shapes it: one function, with these statements as its body."""
        self.write("RivuletCore/Security/URLSession+Attested.swift",
                   "import Foundation\n\nextension URLSession {\n"
                   "    func attestedData(for request: URLRequest) async throws -> (Data, URLResponse) {\n"
                   + "".join(f"        {statement}\n" for statement in statements) + "    }\n" + after + "}\n")

    def test_guard_after_enrollment_rejected(self):
        self.write_wrapper(self.ATTEST, self.GUARD)
        self.assertTrue(any("before App Attest" in error for error in self.errors()))

    def test_guard_written_over_several_lines_is_read(self):
        self.write_wrapper("// A separately signed fork cannot authenticate as upstream.",
                           "guard PrivateBuildPolicy.allowsUpstreamHostedServices else {",
                           "    throw URLError(.userAuthenticationRequired)", "}", self.ATTEST)
        self.assertEqual([], self.errors())

    # A stand-in for the second pinned file: what matters to these tests is only that it is pinned.
    DIRECT = '''// A comment.
nonisolated enum DirectMetadata {
    static func authorization(_ key: String) -> String { "Bearer \\(key)" }
    static func makeSession() -> URLSession {
        return URLSession(configuration: configuration, delegate: Redirects(), delegateQueue: nil)
    }
}
'''
    PINNED = ("RivuletCore/Security/URLSession+Attested.swift", "RivuletCore/Integrations/DirectMetadata.swift")

    def test_the_two_boundary_files_are_pinned(self):
        self.assertEqual(set(self.PINNED), set(verify_module.REVIEWED_SOURCES))
        for digest in verify_module.REVIEWED_SOURCES.values():
            self.assertRegex(digest, r"^[0-9a-f]{64}$")

    def test_any_change_to_a_pinned_file_needs_a_review(self):
        reviewed = self.pins()
        for path in self.PINNED:
            original = (self.root / path).read_text()
            changes = (original + "\n",                                                   # a trailing line
                       original.replace("\n", "\n// another comment\n", 1),                # a comment
                       original.replace("    ", "\t", 1),                                  # indentation
                       original.replace("(", "(\n", 1),                                    # a line break inside a call
                       original.replace("return", "self.dataTask(with: request).resume()\n        return", 1),
                       original.replace("Bearer ", "Bearer  ") if "Bearer " in original else original.replace("answer", "reply"))
            for changed in changes:
                with self.subTest(path=path, changed=changed[:60]):
                    self.assertNotEqual(original, changed)
                    self.write(path, changed)
                    errors = self.errors(pinned=reviewed)
                    self.assertEqual(1, len(errors))
                    self.assertIn(path + " is not the file reviewed", errors[0])
                    # The message carries the digest of what is there now, for whoever has read it.
                    self.assertIn(self.pins()[path], errors[0])
            self.write(path, original)
            self.assertEqual([], self.errors(pinned=reviewed))

    def test_a_missing_pinned_file_is_reported(self):
        reviewed = self.pins()
        (self.root / "RivuletCore/Integrations/DirectMetadata.swift").unlink()
        self.assertEqual(["Cannot read RivuletCore/Integrations/DirectMetadata.swift"],
                         [error.split(":")[0] for error in self.errors(pinned=reviewed)])

    def test_signing_validation_failure_reported(self):
        self.identity_validator.side_effect = ValueError("Team ID is not configured")
        self.assertTrue(any("Team ID" in error for error in self.errors(require_signing=True)))

    def test_identity_consistency_checked_even_for_unsigned_build(self):
        self.identity_validator.side_effect = ValueError("App Group differs from identity.json")
        self.assertTrue(any("App Group" in error for error in self.errors()))
        self.identity_validator.assert_called_once_with(self.root, require_configured=False)

    def test_missing_resolved_checkout_directory_rejected(self):
        self.assertTrue(any("resolved checkouts" in error for error in self.errors(resolved_checkouts=self.root / "missing")))

    def prepare_checkouts(self):
        directory = self.root / "checkouts"
        revisions = {}
        for _, (url, _, revision) in verify_module.PINS.items():
            checkout = directory / url.rsplit("/", 1)[-1]
            checkout.mkdir(parents=True)
            revisions[str(checkout)] = revision
        spatial = directory / "AetherEngine/Sources/AetherEngine/Audio/Spatial"
        spatial.mkdir(parents=True)
        (spatial / "BedRegression.swift").write_text("struct BedRegression {}")
        (spatial / "ObjectAudioPlacement.swift").write_text("struct ObjectAudioPlacement {}")
        (spatial / "SpatialAudioBridge.swift").write_text(
            "placement = ObjectAudioPlacement(layout: layout, roles: block.roles) "
            "splitter = BedRegression(layout: layout, bedRoles: bed.roles, folds: bed.folds)")
        (spatial / "TrueHDObjectAudioDecoder.swift").write_text(
            "let handle = truehd_atmos_decoder_create(TRUEHD_ATMOS_PRESENTATION_HIGHEST) "
            "_ = truehd_atmos_decoder_set_ignore_interpolation(handle, true)")
        # Every log line the app parses, spelled as the engine's sources spell them.
        self.engine_log = directory / "AetherEngine/Sources/AetherEngine/EngineLines.swift"
        self.engine_log.write_text("let lines = [\n" + "".join(f'    "{literal}",\n' for literals in verify_module.ENGINE_LOG_LITERALS.values()
                                                           for literal in literals) + "]\n")
        def fake_git(command, **kwargs):
            checkout = command[4]
            args = command[5:]
            if args == ["rev-parse", "--show-toplevel"]:
                return checkout
            if args == ["rev-parse", "HEAD"]:
                return revisions[checkout]
            if args == ["status", "--porcelain", "--untracked-files=all"]:
                return ""
            self.fail(f"Unexpected Git invocation: {command}")
        return directory, spatial, revisions, fake_git

    def test_actual_checkout_contract(self):
        directory, _, _, fake_git = self.prepare_checkouts()
        with patch.object(verify_module.subprocess, "check_output", side_effect=fake_git):
            self.assertEqual([], self.errors(resolved_checkouts=directory))

    def test_actual_checkout_revision_drift(self):
        directory, _, revisions, fake_git = self.prepare_checkouts()
        revisions[str(directory / "LibTrueHD")] = "0" * 40
        with patch.object(verify_module.subprocess, "check_output", side_effect=fake_git):
            self.assertTrue(any("Actual libtruehd checkout revision" in error for error in self.errors(resolved_checkouts=directory)))

    def test_actual_direct_renderer_rejected(self):
        directory, spatial, _, fake_git = self.prepare_checkouts()
        (spatial / "ObjectAudioRenderer.swift").write_text("class ObjectAudioRenderer {}")
        with patch.object(verify_module.subprocess, "check_output", side_effect=fake_git):
            self.assertTrue(any("direct object renderer" in error for error in self.errors(resolved_checkouts=directory)))

    def test_actual_missing_bed_regression_rejected(self):
        directory, spatial, _, fake_git = self.prepare_checkouts()
        (spatial / "BedRegression.swift").unlink()
        with patch.object(verify_module.subprocess, "check_output", side_effect=fake_git):
            self.assertTrue(any("bed-regression" in error for error in self.errors(resolved_checkouts=directory)))

    def test_actual_exact_interpolation_rejected(self):
        directory, spatial, _, fake_git = self.prepare_checkouts()
        (spatial / "TrueHDObjectAudioDecoder.swift").write_text("let handle = truehd_atmos_decoder_create(TRUEHD_ATMOS_PRESENTATION_HIGHEST)")
        with patch.object(verify_module.subprocess, "check_output", side_effect=fake_git):
            self.assertTrue(any("bed-regression configuration" in error for error in self.errors(resolved_checkouts=directory)))

    def test_dirty_actual_checkout_rejected(self):
        directory, _, _, fake_git = self.prepare_checkouts()
        def dirty_git(command, **kwargs):
            return " M Package.swift" if command[5] == "status" else fake_git(command, **kwargs)
        with patch.object(verify_module.subprocess, "check_output", side_effect=dirty_git):
            self.assertTrue(any("checkout is modified" in error for error in self.errors(resolved_checkouts=directory)))

    def test_engine_rewording_a_parsed_log_line_is_caught(self):
        directory, _, _, fake_git = self.prepare_checkouts()
        text = self.engine_log.read_text()
        for literal in ('"[SWHost] audio: "', '"DV P7 dual-layer route:"', '"over the real-time budget"'):
            with self.subTest(literal=literal):
                self.engine_log.write_text(text.replace(literal, '"reworded"'))
                with patch.object(verify_module.subprocess, "check_output", side_effect=fake_git):
                    errors = self.errors(resolved_checkouts=directory)
                self.assertTrue(any("Actual engine no longer logs " + literal.replace('"', "'") in error for error in errors), errors)
        # Text that survives only in a comment is not a log line.
        self.engine_log.write_text(text.replace('    "bed levels ",', '    // "bed levels ",'))
        with patch.object(verify_module.subprocess, "check_output", side_effect=fake_git):
            self.assertTrue(any("'bed levels '" in error for error in self.errors(resolved_checkouts=directory)))

    def test_every_listed_engine_literal_must_stay_in_its_swift_parser(self):
        for path, literals in verify_module.ENGINE_LOG_LITERALS.items():
            original = (self.root / path).read_text()
            for literal in literals:
                with self.subTest(path=path, literal=literal):
                    self.write(path, original.replace(literal, "<gone>"))
                    self.assertTrue(any(path in error and repr(literal) in error for error in self.errors()))
            self.write(path, original)
        self.assertEqual([], self.errors())

    def test_removing_any_private_hook_fails_and_names_it(self):
        """A line deleted by an upstream merge: each hook's own text goes, and verify.py says what went dark."""
        for path, scope, pattern, dark in verify_module.HOOKS:
            original = (self.root / path).read_text()
            stripped = verify_module.without_comments(original)
            start = 0 if scope is None else stripped.index(scope)
            match = re.compile(pattern).search(stripped, start)
            self.assertIsNotNone(match, f"{path}: {pattern}")
            lines = original.split("\n")
            first, last = stripped.count("\n", 0, match.start()), stripped.count("\n", 0, match.end())
            with self.subTest(path=path, dark=dark):
                self.write(path, "\n".join(lines[:first] + lines[last + 1:]))
                errors = self.errors()
                self.assertTrue(any(error.startswith("Private hook missing in " + path) and error.endswith(": " + dark)
                                    for error in errors), errors)
            self.write(path, original)
        self.assertEqual([], self.errors())

    def test_a_hook_outside_its_function_does_not_count(self):
        path = "Rivulet/Views/Player/UniversalPlayerViewModel.swift"
        # endHomeAssistantSession() is called from three places; the one in stopPlayback is pinned there.
        # Removed by position (the first call after the declaration), not by upstream's neighbouring line.
        text = (self.root / path).read_text()
        start = text.index("func stopPlayback() {")
        line = text.index("        endHomeAssistantSession()\n", start)
        self.assertLess(text.count("\n", start, line), 5, "the hook sits at the top of stopPlayback")
        self.write(path, text[:line] + text[line + len("        endHomeAssistantSession()\n"):])
        self.assertIn("endHomeAssistantSession()", (self.root / path).read_text())
        self.assertTrue(any("in func stopPlayback: Home Assistant keeps showing a title the user stopped" in error
                            for error in self.errors()))

    def test_a_scope_that_is_missing_or_repeated_is_named_with_its_count(self):
        path, scope = self.player_path, "func resetPrivateLoadState("
        original = (self.root / path).read_text()
        for count, text in ((0, original.replace(scope, "func resetPrivateState(")),
                            (2, original + "\n// " + scope + "\nfunc resetPrivateLoadState() {}\n")):
            with self.subTest(count=count):
                self.write(path, text)
                errors = self.errors()
                self.assertIn(f"Hook scope {scope!r} occurs {count} times in {path}, not once: "
                              "point HOOKS at the declaration upstream now uses", errors)
                self.assertFalse(any(error.startswith("Private hook missing in " + path + ", in func resetPrivateLoadState")
                                     for error in errors), errors)
        self.write(path, original)
        self.assertEqual([], self.errors())

    SETTINGS = "Rivulet/Views/Settings/UIKit/PrivateSettingsContent.swift"

    def test_private_text_must_name_the_private_table(self):
        """Each way a private literal reaches upstream's Localizable table, made in the real private Settings file."""
        original = (self.root / self.SETTINGS).read_text()
        breaks = {
            "String(localized:) without a table": ('String(localized: "Refresh Now", table: "Private")', 'String(localized: "Refresh Now")'),
            "another table": ('String(localized: "Key Check", table: "Private")', 'String(localized: "Key Check", table: "Localizable")'),
            "an interpolated key without a table": ('?? "-").", table: "Private")', '?? "-").")'),
            "a resource without a table": ('LocalizedStringResource("Integrations", table: "Private")', 'LocalizedStringResource("Integrations")'),
            "a bare row title": ('verbatim: String(localized: "Index URL", table: "Private")', 'title: "Index URL"'),
            "a bare description": ('verbatim: String(localized: "How many files the loaded index describes and when it was built.", table: "Private")',
                                   'description: "How many files the loaded index describes and when it was built."'),
            "a bare header": ('.header(LocalizedStringResource("Integrations", table: "Private"))', '.header("Integrations")'),
        }
        for name, (good, bad) in breaks.items():
            with self.subTest(name):
                self.assertEqual(1, original.count(good), good)
                self.write(self.SETTINGS, original.replace(good, bad))
                errors = self.errors()
                self.assertEqual(1, len(errors), errors)
                self.assertTrue(errors[0].startswith(self.SETTINGS + ":"), errors)
        # Upstream's shared words stay on its table; comments and other String initialisers are not calls.
        self.write(self.SETTINGS, original + '\nlet a = String(localized: "OK")\n// String(localized: "x")\nlet b = String(describing: 1)\n')
        self.assertEqual([], self.errors())
        self.write(self.SETTINGS, original + '\nlet a = String(localized: "Okay")\n')
        self.assertEqual(1, len(self.errors()))

    def test_every_private_swift_file_is_read(self):
        (self.root / "RivuletCore/Integrations/DeviceIdentity.swift").unlink()
        self.assertTrue(any(error.startswith("Cannot read RivuletCore/Integrations/DeviceIdentity.swift") for error in self.errors()))

    STRAY = "Rivulet/Services/Stray.swift"

    def test_a_stray_stock_identity_fails(self):
        for line in ('let link = URL(string: "rivulet://play?ratingKey=1")',
                     'let group = "GROUP.com.bain.Rivulet"',
                     'let container = "iCloud.com.gstudios.rivulet.sync"'):
            with self.subTest(line=line):
                self.write(self.STRAY, line + "\n")
                self.assertTrue(any(error.startswith("Stock Rivulet identity in " + self.STRAY) for error in self.errors()))

    def test_stock_identity_in_comments_or_on_the_allowlist_passes(self):
        self.write(self.STRAY, "// a `rivulet://detail` link\n/* com.gstudios.rivulet */ let x = 1\n")
        for path, line in verify_module.STOCK_IDENTITY_ALLOWED:
            self.write(path, "    " + line + "\n")
        self.assertEqual([], self.errors())
        # The allowlist names a file and a line: the same line anywhere else is a new occurrence.
        path, line = next(iter(verify_module.STOCK_IDENTITY_ALLOWED))
        self.write(self.STRAY, line + "\n")
        self.assertTrue(any(self.STRAY in error for error in self.errors()))

    def test_comments_do_not_hide_url_literals(self):
        value = 'let url = "https://example.invalid/a/*b*/" // comment\n/* nested /* inner */ done */ live()'
        self.assertIn('"https://example.invalid/a/*b*/"', verify_module.without_comments(value))
        self.assertNotIn("inner", verify_module.without_comments(value))
        self.assertIn("live()", verify_module.without_comments(value))


class ShellGuardTests(unittest.TestCase):
    """The build helper's argument guard; it must refuse before running anything."""

    def test_build_rejects_unknown_mode_without_building(self):
        script = Path(__file__).resolve().parents[1] / "build-tvos.sh"
        result = subprocess.run(["bash", str(script), "publish"], capture_output=True, text=True)
        self.assertEqual(2, result.returncode)
        self.assertIn("Usage:", result.stderr)


class BuildScriptTests(unittest.TestCase):
    """Fake only the Apple tools to test our wrapper, not to claim a Swift build."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "export"
        self.private = self.root / "PrivateBuild"
        self.private.mkdir(parents=True)
        self.bin = self.root / "fake-tools"
        self.bin.mkdir()
        source = Path(__file__).resolve().parents[1] / "build-tvos.sh"
        (self.private / "build-tvos.sh").write_bytes(source.read_bytes())
        self.lock = self.root / verify_module.LOCK
        self.lock.parent.mkdir(parents=True)
        self.lock.write_text("reviewed lock\n")
        self.calls = self.root / "calls.jsonl"
        # A separate test above exercises the real guard. This pass-through lets
        # these tests observe the wrapper's independent byte-mutation trap.
        (self.private / "verify.py").write_text('''import json, os, sys
with open(os.environ["MOCK_CALLS"], "a") as stream:
    stream.write(json.dumps(["verify", *sys.argv[1:]]) + "\\n")
''')
        self.executable("uname", "#!/bin/sh\necho Darwin\n")
        self.executable("xcodebuild", '''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
if args == ["-version"]:
    print("Xcode " + os.environ.get("MOCK_XCODE_VERSION", "27.2"))
    raise SystemExit(0)
with open(os.environ["MOCK_CALLS"], "a") as stream:
    stream.write(json.dumps(["xcodebuild", *args]) + "\\n")
mode = os.environ.get("MOCK_XCODE_MODE", "ok")
if "-resolvePackageDependencies" in args and mode == "fail-resolve":
    raise SystemExit(17)
if args[-1] == "build":
    if mode == "fail-build":
        raise SystemExit(23)
    if mode == "mutate-lock":
        Path(os.environ["MOCK_LOCK"]).write_text("unexpected resolver mutation\\n")
''')
        self.env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ["PATH"],
                        MOCK_CALLS=str(self.calls), MOCK_LOCK=str(self.lock))

    def executable(self, name, content):
        path = self.bin / name
        path.write_text(content)
        path.chmod(0o755)

    def run_build(self, mode="simulator"):
        return subprocess.run(["bash", str(self.private / "build-tvos.sh"), mode],
                              env=self.env, capture_output=True, text=True, timeout=30)

    def recorded(self):
        return [json.loads(line) for line in self.calls.read_text().splitlines()]

    def test_success_keeps_locked_flags_and_rechecks(self):
        result = self.run_build()
        self.assertEqual(0, result.returncode, result.stderr)
        calls = self.recorded()
        self.assertEqual(3, sum(call[0] == "verify" for call in calls))
        commands = [call for call in calls if call[0] == "xcodebuild"]
        self.assertEqual(2, len(commands))
        for command in commands:
            self.assertIn("-onlyUsePackageVersionsFromResolvedFile", command)
            self.assertIn("Release", command)
            self.assertNotIn("-allowProvisioningUpdates", command)
        self.assertIn("ARCHS=arm64", commands[-1])
        self.assertIn("CODE_SIGNING_ALLOWED=NO", commands[-1])

    def test_lock_mutation_causes_failure_and_is_not_restored(self):
        self.env["MOCK_XCODE_MODE"] = "mutate-lock"
        result = self.run_build()
        self.assertEqual(1, result.returncode, result.stderr)
        self.assertIn("Package.resolved changed", result.stderr)
        self.assertEqual("unexpected resolver mutation\n", self.lock.read_text())

    def test_failed_resolution_preserves_status_and_does_not_build(self):
        self.env["MOCK_XCODE_MODE"] = "fail-resolve"
        result = self.run_build()
        self.assertEqual(17, result.returncode, result.stderr)
        calls = self.recorded()
        self.assertEqual("verify", calls[-1][0])
        self.assertFalse(any(call[-1] == "build" for call in calls))
        self.assertEqual("reviewed lock\n", self.lock.read_text())

    def test_failed_build_preserves_status_and_still_rechecks(self):
        self.env["MOCK_XCODE_MODE"] = "fail-build"
        result = self.run_build()
        self.assertEqual(23, result.returncode, result.stderr)
        self.assertEqual("verify", self.recorded()[-1][0])
        self.assertIn("--resolved-checkouts", self.recorded()[-1])

    def test_device_mode_requires_private_signing_but_never_installs(self):
        result = self.run_build("device")
        self.assertEqual(0, result.returncode, result.stderr)
        for call in self.recorded():
            if call[0] == "verify":
                self.assertIn("--require-signing", call)
            else:
                self.assertNotIn("-allowProvisioningUpdates", call)
                self.assertNotIn("install", call)
        command = [call for call in self.recorded() if call[0] == "xcodebuild"][-1]
        self.assertIn("generic/platform=tvOS", command)
        self.assertNotIn("CODE_SIGNING_ALLOWED=NO", command)

    def test_older_xcode_fails_before_resolution(self):
        self.env["MOCK_XCODE_VERSION"] = "27.1"
        result = self.run_build()
        self.assertNotEqual(0, result.returncode)
        self.assertIn("requires Xcode 27.2", result.stderr)
        self.assertFalse(any(call[0] == "xcodebuild" for call in self.recorded()))


if __name__ == "__main__":
    unittest.main()
