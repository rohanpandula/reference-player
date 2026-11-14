#!/usr/bin/env python3
"""Fail-closed source/configuration guard for this fork's reviewed baseline.

Contract: upstream Rivulet plus the restored FEL/Height Speakers code on the engine upstream
pins, AetherEngine 7.61.0 with its bed-regression audio.

This is deliberately not a Swift compiler, a licence opinion, or an HDMI test.
Keep these expectations independent of UPSTREAM.json: changing a manifest must
not silently bless a new decoder. A deliberate upgrade needs a new review and
playback checks on an Apple TV, not merely updating these constants.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

BASELINE = "eabb7cf7833e6d1bb92d6fad116cb38a47305a0a"  # upstream v1.3.0-126, the last upstream release merged into this fork
LOCK = Path("Rivulet.xcodeproj/project.xcworkspace/xcshareddata/swiftpm/Package.resolved")
# Reviewed source, pinned byte for byte (sha256). These two files decide what may reach upstream's
# Workers and where the user's own TMDB and MDBList keys may be sent. Any edit to either, a comment
# included, fails verification until the file has been read again and its digest replaced here. Patterns over Swift source were tried for this and kept missing an
# ordinary form or refusing a harmless one; a pin does neither. It is not a proof of where bytes go:
# that is DirectMetadata's own refusal and redirect rule, and the Swift tests that exercise them.
REVIEWED_SOURCES = {
    "RivuletCore/Security/URLSession+Attested.swift": "2b9df423b433693903d9c2f57ed96649453e4481b40f7f7d0b19dcb5b3ce57fd",
    "RivuletCore/Integrations/DirectMetadata.swift": "f3ce1212cdfe284c7713ce0c805fc0ada5048cd2bb555201d99aae6394309d77",
}
TRAITS = {"DualLayerDolbyVision", "DolbyVisionFelCurves", "DolbyVisionStillReshaping", "TrueHDObjectAudio", "DTSXObjectAudio"}
# identity: (source URL, semantic version or None, immutable Git revision)
PINS = {
    "aetherengine": ("https://github.com/superuser404notfound/AetherEngine", "7.61.0", "d612bedb3e60f8bc403c646fe44c0a852ee8a995"),
    "ffmpegbuild": ("https://github.com/superuser404notfound/FFmpegBuild", "3.13.0", "9282e592b81d9aa1d699dcc70d46cf6b4d66b55f"),
    "fluidaudio": ("https://github.com/FluidInference/FluidAudio", None, "1b0ed50d686873128a4129b3510ce3914c849bab"),
    "libassbuild": ("https://github.com/l984-451/LibassBuild", "0.17.5-2", "434a18a439ece78631d671cce0ee7bab1622f725"),
    "libdovi": ("https://github.com/superuser404notfound/LibDovi", "2.2.0", "caff700968700467c089a3f38c8b4180620518b5"),
    "libtruehd": ("https://github.com/superuser404notfound/LibTrueHD", "3.2.0", "12b26e487a2ade918e53380d47f5727883fedafc"),
    "sentry-cocoa": ("https://github.com/getsentry/sentry-cocoa", "9.17.1", "2c420d31d0c31b525fa9e7c552ef0fc9d924befc"),
    "smbclient": ("https://github.com/kishikawakatsumi/SMBClient", "0.3.1", "e636c2b2458930770932a36d311ec9d478575b90"),
    "swiftdraw": ("https://github.com/swhitty/SwiftDraw", "0.29.0", "82699f4bdf2f075793cfa0d392eb27c59b41a3fe"),
}

# Private one-line hooks inside upstream files. An upstream merge can drop any of these with no
# conflict and no compile error, and the feature just goes dark. Each entry: (file, scope, pattern,
# what goes dark). `scope` is None (anywhere in the file) or the text that declares the function/property
# the hook must sit in (it must occur once); the scope runs from that line to the first later line that
# starts with `}` at the declaration's own indentation. Patterns run on comment-stripped text.
# What this can see: the hook's text, in the right body. What it cannot: whether the call is reached
# (an early return or a wrapping `if false` above it passes). A missing private `case` fails the build
# on its own; those lines are listed anyway, because a merge that "fixes" the build with an empty
# answer instead of the forwarding call compiles and shows nothing.
# The four private Settings pages, as the forwarding `case` lines in upstream's switches name them.
PRIVATE_PAGES = r"case\s+\.heightSpeakersPicker,\s*\.homeAssistant,\s*\.formatTags,\s*\.metadataKeys\s*:\s*return\s+"
HOOKS = (
    ("Rivulet.xcodeproj/project.pbxproj", None,
     r'(?:"ASSETCATALOG_COMPILER_APPICON_NAME\[sdk=appletv\*\]"\s*=\s*ReferencePlayer;[\s\S]*?){2}',
     "the tvOS default icon is not Reference Player in both build configurations"),
    ("Rivulet/Views/Settings/UIKit/SettingsLeftPanelView.swift", "private static func appIconArtwork(",
     r'if\s+icon\s*==\s*\.standard\s*\{\s*return\s+UIImage\(named:\s*"ReferencePlayerIconPreview"\)\s*\}',
     "the default icon preview shows upstream's artwork"),
    ("Rivulet/Views/Components/UIKit/LaunchSplashView.swift", None,
     r'private\s+let\s+art\s*=\s*UIImage\(named:\s*"ReferencePlayerLaunchMark"\)\?\.cgImage',
     "the launch splash shows upstream's artwork"),
    ("Rivulet/Views/Player/UniversalPlayerViewModel.swift", "func updatePlaybackState(", r"reportToHomeAssistant\(\s*state\s*\)\s*\{\s*\[weak self\]\s*in\s*self\?\.isSwappingItem\s*\?\?\s*false\s*\}",
     "Home Assistant no longer hears play, pause or stop"),
    ("Rivulet/Views/Player/UniversalPlayerViewModel.swift", "func stopPlayback(", r"endHomeAssistantSession\(\)",
     "Home Assistant keeps showing a title the user stopped as playing"),
    ("Rivulet/Views/Player/UniversalPlayerViewModel.swift", "func stopProviderReporter(", r"endHomeAssistantSession\(\)",
     "Home Assistant keeps showing a Jellyfin/Emby title as playing after its reporter stops"),
    ("Rivulet/Views/Player/UniversalPlayerViewModel.swift", "deinit {", r"HomeAssistantReporter\.shared\.end\(\)",
     "Home Assistant keeps showing a torn-down player as playing"),
    ("Rivulet/RivuletApp.swift", "init() {", r"PrivateLaunch\.startFormatIndex\(\)",
     "the format index never starts: no FEL/MEL badges and no refresh"),
    ("Rivulet/Config/PrivateLaunch.swift", "static func startFormatIndex(", r"FormatIndexStore\.shared\.startup\(\)",
     "FEL/MEL badges never load the cached or fetched format index"),
    ("Rivulet/Config/PrivateLaunch.swift", "static func startFormatIndex(",
     r"willEnterForegroundNotification[^}]*FormatIndexStore\.shared\.refreshIfStale\(\)",
     "the format index is never refreshed when the app returns to the front"),
    ("Rivulet/Config/PrivateLaunch.swift", "static func startFormatIndex(", r'environment\["XCTestConfigurationFilePath"\]\s*==\s*nil',
     "every hosted test run fetches the format index from the user's network"),
    ("Rivulet/Views/Media/MediaDetail/UIKit/MediaDetailChromeView.swift", "func commonInit(",
     r"#selector\(formatIndexDidChange\),\s*name:\s*FormatIndexStore\.didChange",
     "the hero's FEL/MEL badge stays missing when the index lands after the first draw"),
    ("Rivulet/Views/Media/MediaDetail/UIKit/MediaDetailChromeView.swift", "func formatIndexDidChange(", r"rebuildQualityRow\(",
     "the hero's quality row ignores format index changes"),
    ("Rivulet/Views/Media/MediaDetail/UIKit/MediaItemDetailPageViewController.swift", "func viewDidLoad(",
     r"publisher\(for:\s*FormatIndexStore\.didChange\)[\s\S]{0,300}?rebuildBadgeRow\(",
     "the episode page's FEL/MEL badge ignores format index changes"),
    ("Rivulet/Services/Plex/Playback/AetherPlayer.swift", "var dolbyVisionPlayback:", r"dualLayerRouteTaken:\s*dolbyVisionDualLayerRouteTaken",
     "the Details pane cannot tell a composed FEL picture from the base layer"),
    ("Rivulet/Services/Plex/Playback/AetherPlayer.swift", "var dolbyVisionPlayback:", r"onSoftwareRoute:\s*isOnSoftwareRoute",
     "the Details pane loses the software (dual-layer) route"),
    ("Rivulet/Services/Plex/Playback/AetherPlayer.swift", None, r'if\s+line\.contains\("DV P7 dual-layer route:"\)',
     "the engine's Profile 7 route line is never captured"),
    ("Rivulet/Services/Plex/Playback/AetherPlayer.swift", None, r"DolbyVisionLayerType\(routeLine:\s*routeLine\)",
     "FEL/MEL from the route line never reaches the Details pane"),
    ("Rivulet/Services/Plex/Playback/AetherPlayer.swift", None,
     r"dolbyVisionDualLayerRouteTaken\s*=\s*DolbyVisionPlayback\.dualLayerRouteTaken\(routeLine:\s*routeLine\)",
     "whether the dual-layer route was taken is never recorded"),
    # The private code that reads the engine is one extension at the end of AetherPlayer.swift; these
    # are the calls into it from upstream's loadFile, and the order the load depends on.
    ("Rivulet/Services/Plex/Playback/AetherPlayer.swift", "private func loadFile(",
     r"let\s+privateLoad\s*=\s*applyPrivateLoadOptions\(to:\s*&options\)",
     "Height Speakers and the FEL route never reach the engine's load options"),
    ("Rivulet/Services/Plex/Playback/AetherPlayer.swift", "private func loadFile(",
     r"let\s+p7Route\s*=\s*Self\.beginP7RouteCapture\(\)",
     "the engine's log handler is never set to catch the Profile 7 route line"),
    ("Rivulet/Services/Plex/Playback/AetherPlayer.swift", "private func loadFile(",
     r"defer\s*\{\s*if\s+p7Route\.ownsLogHandler\s*\{\s*EngineLog\.handler\s*=\s*nil\s*\}\s*\}",
     "the route capture keeps the engine's log handler after the load, so Live TV's join capture never gets it"),
    ("Rivulet/Services/Plex/Playback/AetherPlayer.swift", "private func loadFile(", r"resetPrivateLoadState\(\)",
     "a new title shows the previous title's route evidence and heights"),
    ("Rivulet/Services/Plex/Playback/AetherPlayer.swift", "private func loadFile(", r"finishP7RouteCapture\(\s*p7Route\s*,\s*privateLoad\s*\)",
     "the captured route line never reaches the Details pane or Sentry"),
    ("Rivulet/Services/Plex/Playback/AetherPlayer.swift", "private func loadFile(",
     r"resetPrivateLoadState\(\)[\s\S]*?try\s+await\s+engine\.load\([\s\S]*?finishP7RouteCapture\(",
     "the per-title reset or the route-line read no longer brackets the engine load"),
    ("Rivulet/Services/Plex/Playback/AetherPlayer.swift", "func resetPrivateLoadState(", r"dolbyVisionDualLayerRouteTaken\s*=\s*nil",
     "a new title keeps the previous title's Dual Layer evidence"),
    ("Rivulet/Views/Player/UIKit/PlayerInfoPaneViews.swift", "init?(sourceProfile:",
     r"if\s+sourceProfile\s*==\s*7,\s*!convertedTo81,\s*onSoftwareRoute,\s*dualLayerRouteTaken\s*==\s*true\s*\{\s*self\s*=\s*\.dualLayer\s*\}",
     "the Details pane never says Dual Layer for a composed FEL picture"),
    ("Rivulet/Services/Plex/Playback/AetherPlayer.swift", "func probeEnhancementLayer(",
     r"if\s+let\s+layer\s*\{\s*self\.dolbyVisionEnhancementLayer\s*=\s*layer\s*\}",
     "a failed enhancement-layer probe erases the route line's FEL/MEL"),
    ("Rivulet/Services/Plex/Playback/AetherPlayer.swift", None, r"EngineLog\.structuredHandler\s*=[^}]*SpatialBridgeLogReader\.shared\.ingest\(",
     "heights on the dual-layer route are never read from the engine's log"),
    ("Rivulet/Services/Plex/Playback/AetherPlayer.swift", None, r"_\s*=\s*Self\.bridgeLogReaderInstalled",
     "the spatial-bridge log reader is never installed"),
    ("Rivulet/Services/Plex/Playback/AetherPlayer.swift", None, r"SpatialBridgeLogReader\.shared\.reset\(\)",
     "a new title shows the previous title's heights"),
    # Settings: the private pages, rows and descriptions live in PrivateSettingsContent.swift; these
    # are the only lines that reach them from upstream's three Settings files.
    ("Rivulet/Views/Settings/SettingsModels.swift", None, r"case\s+heightSpeakersPicker,\s*homeAssistant,\s*formatTags,\s*metadataKeys\b",
     "the private Settings pages are gone (the build fails without them)"),
    ("Rivulet/Views/Settings/SettingsModels.swift", "var title: String {", PRIVATE_PAGES + r"PrivateSettings\.title\(for:\s*self\)",
     "the private Settings pages have no title"),
    ("Rivulet/Views/Settings/UIKit/SettingsPageModels.swift", "private static func allRows(for page:",
     PRIVATE_PAGES + r"PrivateSettings\.rows\(for:\s*page\)",
     "the Height Speakers, Home Assistant, Format Tags and Discover and Scores pages open empty"),
    ("Rivulet/Views/Settings/UIKit/SettingsPageModels.swift", "private static var playback:",
     r"\]\s*\+\s*PrivateSettings\.integrationRows\s*\+\s*\[",
     "Settings has no way into Home Assistant, Format Tags or Discover and Scores"),
    ("Rivulet/Views/Settings/UIKit/SettingsPageModels.swift", "private static var playback:",
     r"\]\s*\+\s*PrivateSettings\.homeTheaterRows\s*\+\s*\[",
     "Settings has no Dolby Vision FEL switch and no Height Speakers picker"),
    ("Rivulet/Views/Settings/SettingsDescriptors.swift", None, r"\]\.merging\(\s*PrivateSettings\.descriptors\s*\)",
     "the private Settings rows lose their descriptions in the left panel"),
    ("Rivulet/Views/Settings/SettingsDescriptors.swift", "static func descriptor(for id:",
     r'if\s+id\.hasPrefix\("hs_"\)\s*\{\s*return\s+descriptors\["heightSpeakers"\]\s*\}',
     "the Height Speakers choices lose their description"),
    ("Rivulet/Views/Settings/SettingsDescriptors.swift", "static func pageInfo(for page:",
     PRIVATE_PAGES + r"PrivateSettings\.pageInfo\(for:\s*page\)",
     "the private Settings pages lose their panel icon and colour"),
    # Badges: the private naming lives in FormatTagBadges.swift; MediaSource.swift keeps two calls into it.
    ("RivuletCore/Models/Media/MediaSource.swift", "var detailBadges:", r'hasSuffix\("Atmos"\)\s*\{\s*badges\.append\(atmosDetailBadge\)\s*\}',
     "the detail row never says TrueHD Atmos (the Atmos badge skips the private naming)"),
    ("RivuletCore/Models/Media/MediaSource.swift", None,
     r"var\s+rangeBadges:\s*\[String\]\s*\{\s*formatIndexRangeBadges\(plain:\s*plainRangeBadges\)\s*\}",
     "range badges skip the format index: no DV FEL, DV MEL or 2-track layer"),
    ("RivuletCore/Integrations/FormatTagBadges.swift", "var atmosDetailBadge:", r'"TrueHD Atmos"',
     "the detail row never says TrueHD Atmos"),
    ("RivuletCore/Integrations/FormatTagBadges.swift", "func formatIndexRangeBadges(", r"FormatIndexStore\.shared\.enhancementLayerBadge\(",
     "Profile 7 files never read DV FEL or DV MEL"),
    ("RivuletCore/Integrations/FormatTagBadges.swift", "func formatIndexRangeBadges(", r"FormatIndexStore\.shared\.separateTrackLayerBadge\(",
     "dual-track Profile 7 files never name their second-track layer"),
    # The name media servers are told: a merge that takes upstream's literal back compiles, and every
    # server dashboard then credits upstream's app with this fork.
    ("RivuletCore/Models/Plex/PlexModels.swift", "enum PlexAPI:", r"static\s+let\s+productName\s*=\s*PrivateBuildPolicy\.productName\b",
     "Plex, Jellyfin and Emby are told this app is upstream's Rivulet"),
    ("RivuletCore/LiveTV/LiveTVClientIdentity.swift", "static let userAgent:", r'return\s+"\\\(PrivateBuildPolicy\.userAgentProduct\)/',
     "Live TV streams carry upstream's user agent, or a product token with a space"),
    # A User-Agent product token cannot hold a space, so it is not the display name.
    ("RivuletCore/MediaProvider/Plex/PlexProvider+Playback.swift", "static func directPlayHeaders(",
     r'"User-Agent":\s*"\\\(PrivateBuildPolicy\.userAgentProduct\)/',
     "the Plex direct-play user agent carries a space in its product token"),
)

# Engine log text the app parses. Each literal must stay in its Swift file (checked always, so this
# list cannot outlive the code that matches it) and in the pinned engine's sources (checked with
# --resolved-checkouts, so an engine bump that rewords a line fails here instead of going quiet on
# the TV). Literals are cut to the piece the engine source spells verbatim: "el_type=" because the
# engine interpolates FEL, "bed levels " because the bridge prefix is added at the emit site.
# A new literal added to a Swift parser is not seen until it is listed here.
ENGINE_LOG_LITERALS = {
    "Rivulet/Services/Plex/Playback/AetherPlayer.swift": ("DV P7 dual-layer route:",),
    "Rivulet/Views/Player/UIKit/DolbyVisionRouteLine.swift": ("dual-layer route: taken", "el_type=", "el_type=MEL", "over the real-time budget"),
    "RivuletCore/Playback/SpatialBridgeLogReader.swift": (
        "[SpatialAudioBridge]", "[AetherEngine] reload begin:", "[SpatialAudioBridge] init: ",
        "[SpatialAudioBridge] object configuration", "beds ", "bed levels ", "; objects: up to ", " elevated",
        "[SWHost] audio: ", " for the renderer", " rendered into ", "[HLSVideoEngine] ",
        "Atmos object render into", "DTS:X render into", " re-encode", "audio: codec=", "(bridge required)",
        "keeping the channel bridge", "objectAudioRendering off", "object rendering requested but",
        "is not DTS:X this engine renders", "no DTS:X verdict"),
}

# Private UI text belongs to the `Private` string table (Rivulet/Private.xcstrings). Xcode extracts a literal
# given to `String(localized:)` without `table:`, or to a `String.LocalizationValue` funnel, into upstream's
# Localizable.xcstrings, which then conflicts on every merge. These are the private Swift files compiled into
# the tvOS app; each must exist. In them: every `String(localized:` call names `table: "Private"` unless it is
# one of upstream's shared words, `LocalizedStringResource(` likewise, and no funnel below gets a bare literal.
# What this cannot see: a literal passed through a variable, a funnel not listed, and private lines inside
# upstream files. PrivateBuild/check-private-strings.py reads the build's string data and covers all three.
PRIVATE_SWIFT = (
    "Rivulet/Config/PrivateLaunch.swift", "Rivulet/Config/PrivatePlaybackDefaults.swift",
    "Rivulet/Views/Player/UIKit/DolbyVisionRouteLine.swift", "Rivulet/Views/Player/UniversalPlayerViewModel+HomeAssistant.swift",
    "Rivulet/Views/Settings/UIKit/PrivateSettingsContent.swift", "RivuletCore/Config/PrivateBuildPolicy.swift",
    "RivuletCore/Integrations/DeviceIdentity.swift", "RivuletCore/Integrations/DirectMetadata.swift",
    "RivuletCore/Integrations/FormatIndex.swift", "RivuletCore/Integrations/FormatTagBadges.swift",
    "RivuletCore/Integrations/HomeAssistantReporter.swift", "RivuletCore/Integrations/PersonalDefaults.swift",
    "RivuletCore/Playback/SpatialBridgeLogReader.swift",
)
SHARED_WORDS = ("On", "Off", "OK")   # upstream's catalog owns these; private code keeps upstream's key and table
# funnel call -> the labels that take a `String.LocalizationValue` (a bare literal there is extracted to Localizable)
LOCALIZED_FUNNELS = {"SettingsRowItem": ("title",), "SettingDescriptor": ("description",), ".header": ("",)}


def calls(text: str, opener: str) -> list[tuple[int, str]]:
    """Each `opener(` … `)` call in `text` (line number, the argument text), skipping string contents
    and string interpolations, so a `)` or `"` inside a literal does not end the call."""
    found = []
    for match in re.finditer(re.escape(opener) + r"\(", text):
        i, modes = match.end(), [0]   # modes: an int is code at that paren depth, "s" is inside a string
        while i < len(text) and modes:
            c, mode = text[i], modes[-1]
            if mode == "s":
                if c == "\\" and text.startswith("(", i + 1):
                    modes.append(0)
                    i += 1
                elif c == "\\":
                    i += 1
                elif c == '"':
                    modes.pop()
            elif c == '"':
                modes.append("s")
            elif c == "(":
                modes[-1] += 1
            elif c == ")":
                if mode == 0:
                    modes.pop()
                else:
                    modes[-1] -= 1
            i += 1
        found.append((text.count("\n", 0, match.start()) + 1, text[match.end():i - 1]))
    return found


def private_text_errors(path: str, text: str) -> list[str]:
    errors = []
    for opener in ("String", "LocalizedStringResource"):
        for line, args in calls(text, opener):
            literal = re.match(r'\s*(?:localized:\s*)?"((?:[^"\\]|\\.)*)"\s*$', args)
            shared = opener == "String" and literal is not None and literal.group(1) in SHARED_WORDS
            if (opener == "LocalizedStringResource" or args.lstrip().startswith("localized:")) and not shared \
                    and not re.search(r',\s*table:\s*"Private"', args):
                errors.append(f"{path}:{line}: {opener}({args.strip()[:60]}…) is not on the Private table")
    for funnel, labels in LOCALIZED_FUNNELS.items():
        for line, args in calls(text, funnel):
            for label in labels:
                if re.match(r'\s*"', args) if label == "" else re.search(r"\b" + label + r':\s*"', args):
                    errors.append(f"{path}:{line}: a bare literal given to {funnel}"
                                  f"{'(' + label + ':)' if label else ''} is extracted to Localizable")
    return errors


# Upstream's own identity, which a stray literal would quietly reuse: the stock deep-link scheme,
# bundle prefix (and the iCloud container and Keychain service built on it) and App Group.
STOCK_IDENTITY = re.compile(r"rivulet://|com\.gstudios|group\.com\.bain", re.I)
SWIFT_ROOTS = ("Rivulet", "RivuletCore", "RivuletiOS", "RivuletMac", "TopShelfExtension", "AssetPackExtension", "RivuletTests")
# (file, the stripped source line) -> why that occurrence is harmless. Anything else fails.
STOCK_IDENTITY_ALLOWED = {
    ("RivuletCore/Security/AppAttestClient.swift", 'private static let service = "com.gstudios.rivulet.attest"'):
        "Keychain service of upstream's App Attest client, unreachable: hosted services are refused before it runs",
    ("TopShelfExtension/ContentProvider.swift",
     'private let logger = Logger(subsystem: "com.gstudios.rivulet.TopShelfExtension", category: "ContentProvider")'):
        "a log subsystem name, not an identity",
    ("RivuletTests/Unit/Siri/PrivateDeepLinkSchemeTests.swift", 'XCTAssertNil(RivuletLink(url: URL(string: "rivulet://play/1")!))'):
        "asserts the stock scheme is refused",
}


# Unresolved merge conflicts. A hook on "our" side of a marker still reads as present, so markers fail
# first, in every Swift source walked below, the project file, the lock and every String Catalog.
CONFLICT_MARKER = re.compile(r"^(?:<<<<<<< |\|\|\|\|\|\|\| |>>>>>>> |=======\r?$)", re.M)
CONFLICT_FILES = ("Rivulet.xcodeproj/project.pbxproj", LOCK.as_posix())


def conflict_markers(relative: str, text: str) -> list[str]:
    return [f"Unresolved merge conflict marker in {relative}:{text.count(chr(10), 0, match.start()) + 1}: "
            f"{match.group(0).strip()!r}" for match in CONFLICT_MARKER.finditer(text)]


def scoped(text: str, declaration: str) -> Optional[str]:
    """The body of the one declaration matching `declaration`: to the first `}` at its indentation."""
    matches = list(re.finditer(re.escape(declaration), text))
    if len(matches) != 1:
        return None
    start = text.rfind("\n", 0, matches[0].start()) + 1
    indent = re.match(r"[ \t]*", text[start:]).group(0)
    end = re.search(r"^" + re.escape(indent) + r"\}", text[matches[0].end():], re.M)
    return text[matches[0].end():matches[0].end() + end.start()] if end else None


def validate_private_identity(root: Path, require_configured: bool) -> None:
    """Reuse the configuration tool's read-only validator; no second ID policy."""
    name = "rivulet_private_configuration"
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name("configure.py"))
    if spec is None or spec.loader is None:
        raise ImportError("Cannot load the private identity validator")
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolves module annotations through sys.modules while loading.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    module.validate_configuration(root, require_configured=require_configured)


def without_comments(text: str) -> str:
    """Ignore comment-only 'evidence'; preserve strings, newlines, and token gaps.

    Supports nested Swift block comments and quoted strings. These are targeted
    checks of known source files, not a general Swift parser or control-flow proof.
    """
    result = []
    i = 0
    depth = 0
    quoted = False
    while i < len(text):
        if depth:
            if text.startswith("/*", i):
                depth += 1
                i += 2
            elif text.startswith("*/", i):
                depth -= 1
                i += 2
            else:
                result.append("\n" if text[i] == "\n" else " ")
                i += 1
        elif quoted:
            result.append(text[i])
            if text[i] == "\\" and i + 1 < len(text):
                result.append(text[i + 1])
                i += 2
            else:
                if text[i] == '"':
                    quoted = False
                i += 1
        elif text.startswith("//", i):
            end = text.find("\n", i)
            i = len(text) if end < 0 else end
        elif text.startswith("/*", i):
            result.append(" ")
            depth = 1
            i += 2
        else:
            result.append(text[i])
            quoted = text[i] == '"'
            i += 1
    return "".join(result)


def verify(root: Path, require_signing: bool = False,
           resolved_checkouts: Optional[Path] = None) -> list[str]:
    errors: list[str] = []

    def check(condition: bool, message: str) -> None:
        if not condition:
            errors.append(message)

    def cannot_read(relative: str | Path, exc: Exception) -> None:
        if not any(error.startswith(f"Cannot read {relative}:") for error in errors):   # once per file
            errors.append(f"Cannot read {relative}: {exc}")

    def read(relative: str | Path) -> str:
        try:
            return (root / relative).read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            cannot_read(relative, exc)
            return ""

    def read_json(relative: str | Path) -> dict:
        try:
            value = json.loads(read(relative))
            if not isinstance(value, dict):
                raise ValueError("expected a JSON object")
            return value
        except (ValueError, TypeError) as exc:
            errors.append(f"Invalid JSON in {relative}: {exc}")
            return {}

    swift: dict[str, str] = {}
    for folder in SWIFT_ROOTS:
        for file in sorted((root / folder).rglob("*.swift")) if (root / folder).is_dir() else ():
            swift[file.relative_to(root).as_posix()] = read(file.relative_to(root).as_posix())
    catalogs = [file.relative_to(root).as_posix() for folder in SWIFT_ROOTS if (root / folder).is_dir()
                for file in sorted((root / folder).rglob("*.xcstrings"))]
    for relative in [*swift, *CONFLICT_FILES, *catalogs]:
        errors.extend(conflict_markers(relative, swift[relative] if relative in swift else read(relative)))

    project = read("Rivulet.xcodeproj/project.pbxproj")
    # Keep the enclosing object, not a global search that could match a different
    # package's exactVersion. A changed project representation requires review.
    blocks = re.findall(r'/\* XCRemoteSwiftPackageReference "AetherEngine" \*/ = \{(.*?)^\t\t\};', project, re.M | re.S)
    check(len(blocks) == 1, "Expected one identifiable AetherEngine package reference")
    engine = without_comments(blocks[0]) if len(blocks) == 1 else ""
    check('repositoryURL = "https://github.com/superuser404notfound/AetherEngine";' in engine,
          "AetherEngine source URL changed")
    check(bool(re.search(r"kind\s*=\s*exactVersion\s*;", engine)), "AetherEngine must use exactVersion, not an upgrade range")
    check(bool(re.search(r'version\s*=\s*"?7\.61\.0"?\s*;', engine)), "AetherEngine must remain 7.61.0 until explicitly re-reviewed")
    traits_match = re.search(r"traits\s*=\s*\((.*?)\);", engine, re.S)
    traits = set(re.findall(r"\b[A-Za-z][A-Za-z0-9]*\b", traits_match.group(1))) if traits_match else set()
    check(traits == TRAITS, "Engine traits must be the five reviewed features: both FEL routes, P5 stills, TrueHD and DTS:X heights")
    check("XCLocalSwiftPackageReference" not in project, "Local package overrides need explicit review; they can bypass the pin contract")

    locked = read_json(LOCK)
    check(locked.get("version") == 3, "Unexpected Package.resolved schema")
    pins = locked.get("pins", [])
    if not isinstance(pins, list) or not all(isinstance(pin, dict) and isinstance(pin.get("identity"), str) for pin in pins):
        errors.append("Package.resolved pins must be objects with string identities")
        pins = []
    identities = [pin.get("identity") for pin in pins]
    check(len(pins) == len(PINS) and set(identities) == set(PINS), "Dependency set changed or contains duplicate pins")
    for identity, (url, version, revision) in PINS.items():
        candidates = [pin for pin in pins if pin.get("identity") == identity]
        if len(candidates) != 1:
            errors.append(f"Expected exactly one locked {identity} dependency")
            continue
        pin = candidates[0]
        state = {"revision": revision}
        if version is not None:
            state["version"] = version
        check(pin.get("kind") == "remoteSourceControl" and pin.get("location") == url and pin.get("state") == state,
              f"Locked {identity} URL/version/revision changed; do not silently accept a decoder upgrade")

    metadata = read_json("PrivateBuild/UPSTREAM.json")
    origin = metadata.get("rivulet", {})
    check(metadata.get("schema_version") == 1 and isinstance(origin, dict) and origin.get("revision") == BASELINE,
          "UPSTREAM.json must identify the reviewed Rivulet source baseline")
    for section, identity in (("engine", "aetherengine"), ("truehd", "libtruehd")):
        value = metadata.get(section, {})
        check(isinstance(value, dict) and value.get("version") == PINS[identity][1] and value.get("revision") == PINS[identity][2],
              f"UPSTREAM.json {section} disagrees with the reviewed dependency contract")

    player = without_comments(read("Rivulet/Services/Plex/Playback/AetherPlayer.swift"))
    check(bool(re.search(r"audioBridgeMode\s*:\s*\.lossless", player)), "The VOD audio bridge is no longer configured losslessly")
    check(bool(re.search(r"if\s+let\s+heightLayout\s*\{\s*options\.objectAudioRendering\s*=\s*\.render\(heightLayout\)\s*\}", player)),
          "The existing Height Speakers setting is not wired to objectAudioRendering.render")
    check(bool(re.search(r"options\.preferDualLayerDolbyVision\s*=\s*panelDV\s*&&\s*felOn", player)),
          "The existing FEL setting is not wired to the full dual-layer route")
    check("Self.heightSpeakersKey" in player and "Self.dolbyVisionFELKey" in player,
          "The existing playback preferences must remain connected")
    defaults = without_comments(read("Rivulet/Config/PrivatePlaybackDefaults.swift"))
    check(bool(re.search(r"AetherPlayer\.dolbyVisionFELKey\s*:\s*true", defaults))
          and "AetherPlayer.heightSpeakersKey" not in defaults,
          "Defaults must remain FEL on and Height Speakers unregistered (Off) (without rewriting the player)")
    # Nothing on anyone's network is contacted until a user configures it: no built-in index URL or
    # Home Assistant address, and Format Tags and reporting both start off.
    index = without_comments(read("RivuletCore/Integrations/FormatIndex.swift"))
    check(bool(re.search(r'static\s+let\s+defaultURL\s*=\s*""', index))
          and bool(re.search(r"forKey:\s*Keys\.enabled\)\s*as\?\s*Bool\s*\?\?\s*false", index)),
          "The format index must have no built-in URL and Format Tags must default Off")
    reporter = without_comments(read("RivuletCore/Integrations/HomeAssistantReporter.swift"))
    check(bool(re.search(r'static\s+let\s+defaultAddress\s*=\s*""', reporter))
          and bool(re.search(r"get\s*\{\s*UserDefaults\.standard\.bool\(forKey:\s*Keys\.enabled\)\s*\}", reporter)),
          "Home Assistant reporting must have no built-in address and default Off")
    app = without_comments(read("Rivulet/RivuletApp.swift"))
    registered = app.find("PrivatePlaybackDefaults.register()")
    migrated = app.find("AetherPlayer.settleFELDefault(")
    check(0 <= registered < migrated, "Register private defaults before the upstream FEL migration")

    sources: dict[str, str] = {}
    for path, scope, pattern, dark in HOOKS:
        if path not in sources:
            sources[path] = without_comments(read(path))
        count = 1 if scope is None else len(re.findall(re.escape(scope), sources[path]))
        if count != 1:
            message = (f"Hook scope {scope!r} occurs {count} times in {path}, not once: "
                       "point HOOKS at the declaration upstream now uses")
            if message not in errors:
                errors.append(message)
            continue
        body = sources[path] if scope is None else scoped(sources[path], scope)
        where = path if scope is None else f"{path}, in {scope.rstrip(' {(:')}"
        check(body is not None and bool(re.search(pattern, body)), f"Private hook missing in {where}: {dark}")
    for path, literals in ENGINE_LOG_LITERALS.items():
        text = sources.get(path) or without_comments(read(path))
        for literal in literals:
            check(literal in text, f"{path} no longer contains the engine log text {literal!r}; update ENGINE_LOG_LITERALS in verify.py")
    for path in PRIVATE_SWIFT:
        errors.extend(private_text_errors(path, without_comments(read(path))))
    for relative, text in swift.items():
        for line in without_comments(text).splitlines():
            if STOCK_IDENTITY.search(line) and (relative, line.strip()) not in STOCK_IDENTITY_ALLOWED:
                errors.append(f"Stock Rivulet identity in {relative}: {line.strip()[:120]} "
                              "(use RivuletLink.scheme or the private identity, or allowlist it in verify.py with a reason)")

    secrets = without_comments(read("RivuletCore/Config/Secrets.swift"))
    check(bool(re.search(r'^\s*static\s+let\s+sentryDSN\s*=\s*""\s*$', secrets, re.M)),
          "Secrets.swift must have an empty Sentry DSN (never package production credentials)")
    policy = without_comments(read("RivuletCore/Config/PrivateBuildPolicy.swift"))
    check(bool(re.search(r"static\s+let\s+allowsUpstreamHostedServices\s*=\s*false\b", policy)),
          "Upstream hosted services must remain disabled for this separately signed fork")
    wrapper = without_comments(read("RivuletCore/Security/URLSession+Attested.swift"))
    gate = re.search(r"guard\s+PrivateBuildPolicy\.allowsUpstreamHostedServices\s+else\s*\{\s*throw\s+URLError\(\.userAuthenticationRequired\)\s*\}", wrapper)
    first_use = wrapper.find("AppAttestClient.shared")
    check(bool(gate) and first_use >= 0 and gate.end() < first_use,
          "The private service guard must fail before App Attest enrollment or network requests")
    for path, reviewed in REVIEWED_SOURCES.items():
        try:
            found = hashlib.sha256((root / path).read_bytes()).hexdigest()
        except OSError as exc:
            cannot_read(path, exc)
            continue
        check(found == reviewed,
              f"{path} is not the file reviewed here. Read the change, run DirectMetadataTests and "
              f"PrivateBuildServicePolicyTests, then record its digest in verify.py (REVIEWED_SOURCES): {found}")
    sentry = without_comments(read("RivuletCore/Diagnostics/SentryStartup.swift"))
    sentry_guard = re.search(r"guard\s+!Secrets\.sentryDSN\.isEmpty\s+else\s*\{\s*return\s*\}", sentry)
    check(bool(sentry_guard) and 0 <= sentry_guard.end() < sentry.find("SentrySDK.start"),
          "Empty-DSN guard must run before Sentry SDK startup")
    upload_lines = [line.strip() for line in read("Scripts/sentry-upload-dsyms.sh").splitlines()
                    if line.strip() and not line.lstrip().startswith("#")]
    check(upload_lines[:3] == ["set -u", 'echo "sentry: disabled in this fork (no symbols or sources uploaded)"', "exit 0"],
          "Sentry symbol/source uploader must exit unconditionally before probing tools, credentials, or network")

    try:
        validate_private_identity(root, require_configured=require_signing)
    except (ValueError, OSError, ImportError) as exc:
        errors.append(f"Private identity configuration: {exc}")
    # Account ownership/capability availability can only be checked by Xcode.

    if resolved_checkouts is not None:
        verify_resolved_checkouts(resolved_checkouts, errors)
    return errors


def verify_resolved_checkouts(directory: Path, errors: list[str]) -> None:
    """Inspect actual SwiftPM sources, not only a possibly stale lockfile."""
    try:
        folders = [path for path in directory.iterdir() if path.is_dir()]
    except OSError as exc:
        errors.append(f"Cannot inspect resolved checkouts: {exc}")
        return
    checked: dict[str, Path] = {}
    for identity, (url, _, revision) in PINS.items():
        name = url.rsplit("/", 1)[-1].casefold()
        candidates = [path for path in folders if path.name.casefold() == name]
        if len(candidates) != 1:
            errors.append(f"Missing or ambiguous actual checkout for {identity}")
            continue
        path = candidates[0]
        try:
            def git(*args: str) -> str:
                return subprocess.check_output(["git", "-c", "core.fsmonitor=false", "-C", str(path), *args], text=True, stderr=subprocess.STDOUT).strip()
            if Path(git("rev-parse", "--show-toplevel")).resolve() != path.resolve():
                errors.append(f"{identity} is not a standalone package checkout")
            if git("rev-parse", "HEAD") != revision:
                errors.append(f"Actual {identity} checkout revision differs from the reviewed lock")
            if git("status", "--porcelain", "--untracked-files=all"):
                errors.append(f"Actual {identity} checkout is modified; inspect it before building")
            checked[identity] = path
        except (OSError, subprocess.CalledProcessError) as exc:
            errors.append(f"Cannot inspect Git state for {identity}: {exc}")
    if "aetherengine" in checked:
        spatial = checked["aetherengine"] / "Sources/AetherEngine/Audio/Spatial"
        try:
            bridge = without_comments((spatial / "SpatialAudioBridge.swift").read_text(encoding="utf-8"))
            decoder = without_comments((spatial / "TrueHDObjectAudioDecoder.swift").read_text(encoding="utf-8"))
            if (spatial / "ObjectAudioRenderer.swift").exists() or "ObjectAudioRenderer(layout:" in bridge:
                errors.append("Actual engine still carries the pre-7.57 direct object renderer; this candidate expects bed regression")
            if not ((spatial / "BedRegression.swift").is_file() and (spatial / "ObjectAudioPlacement.swift").is_file()
                    and "BedRegression(layout:" in bridge and "ObjectAudioPlacement(layout:" in bridge):
                errors.append("Actual engine lacks the reviewed bed-regression/object-placement audio path")
            if "truehd_atmos_decoder_create(TRUEHD_ATMOS_PRESENTATION_HIGHEST)" not in decoder or "set_ignore_interpolation" not in decoder:
                errors.append("Actual TrueHD decoder differs from the reviewed bed-regression configuration")
        except (OSError, UnicodeError) as exc:
            errors.append(f"Cannot inspect actual spatial-audio implementation: {exc}")
        try:
            engine = "\n".join(without_comments(path.read_text(encoding="utf-8"))
                               for path in sorted((checked["aetherengine"] / "Sources").rglob("*.swift")))
        except (OSError, UnicodeError) as exc:
            errors.append(f"Cannot read the actual engine sources: {exc}")
            engine = None
        if engine is not None:
            for path, literals in ENGINE_LOG_LITERALS.items():
                for literal in literals:
                    if literal not in engine:
                        errors.append(f"Actual engine no longer logs {literal!r}, which {path} parses; "
                                      "read the engine's new wording and update both")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--require-signing", action="store_true", help="also require a configured bundle ID, App Group and team")
    parser.add_argument("--resolved-checkouts", type=Path, help="SwiftPM SourcePackages/checkouts directory after dependency resolution")
    args = parser.parse_args()
    errors = verify(args.root.resolve(), args.require_signing, args.resolved_checkouts)
    if errors:
        for error in errors:
            print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: reviewed source/configuration contract is intact.")
    print("This does not establish Swift compilation, correct HDMI heights, FEL image fidelity, or playback performance.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
