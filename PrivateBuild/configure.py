#!/usr/bin/env python3
"""Configure this checkout's app identity; never provision or install anything.

Use an Apple team with App Groups support (normally a paid developer membership).
This preserves the existing Top Shelf and asset-pack extension structure; it is
not a promise that an unchanged Personal Team/free-signing build will provision.
Team/ID ownership is checked by Xcode/Apple, not by this offline script.

Only the allowlisted identity files below can change. Engine pins, playback
logic, and Secrets.swift are deliberately outside this tool's scope. All source
bindings are validated before any source write; unexpected upstream drift stops
setup rather than guessing where a setting moved. Do not run during an edit or
build. Per-file replacements are atomic; a filesystem failure triggers a best-
effort rollback. This is not a multi-file filesystem transaction.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import json
import os
from pathlib import Path
import plistlib
import re
import stat
import sys
import tempfile


STATE_FILE = "PrivateBuild/identity.json"
PROJECT = "Rivulet.xcodeproj/project.pbxproj"
ENTITLEMENTS = (
    "Rivulet/Rivulet.entitlements",
    "TopShelfExtension/TopShelfExtension.entitlements",
    "AssetPackExtension/AssetPackExtension.entitlements",
)
TV_INFO = "Rivulet/Info.plist"
IOS_INFO = "RivuletiOS/Info.plist"
GROUP_SOURCES = (
    "Rivulet/Services/Cache/TopShelfCache.swift",
    "TopShelfExtension/TopShelfCache.swift",
)
LINK_SOURCE = "RivuletCore/Links/RivuletLink.swift"  # moved upstream after 1.2.0
IOS_IDENTIFIERS = {
    "RivuletiOS/Downloads/IOSDownloadTransfer.swift": ("sessionIdentifier", ".downloads"),
    "RivuletiOS/Downloads/IOSDownloadActivity.swift": ("identifierPrefix", ".downloads.activity"),
}
MAC_INFO = "RivuletMac/Info.plist"
# One iCloud container (KVS preferences + CloudKit vault) shared by the tvOS, iOS and
# Mac apps; AccountVault names it in code, so it moves with the bundle ID.
CLOUD_FILES = (
    "Rivulet/Rivulet.entitlements",
    "RivuletiOS/RivuletiOS.entitlements",
    "RivuletMac/RivuletMac.entitlements",
    "RivuletCore/Sync/AccountVault.swift",
)
MANAGED_FILES = tuple(dict.fromkeys((PROJECT, *ENTITLEMENTS, TV_INFO, IOS_INFO, MAC_INFO,
                                     *GROUP_SOURCES, LINK_SOURCE, *IOS_IDENTIFIERS, *CLOUD_FILES)))
STATE_KEYS = {"schema_version", "bundle_id", "team_id", "app_group", "url_scheme", "display_name"}
# The identity committed to the repository: nobody's bundle ID, no team. A build signs only after
# configure.py has replaced all three; any mix of placeholder and real values is refused.
PLACEHOLDER = {"bundle_id": "com.example.referenceplayer", "team_id": "", "app_group": "group.com.example.referenceplayer"}
UPSTREAM_TEAM = "ZHB786H6YN"


class ConfigurationError(ValueError):
    """Invalid identity or source drift; source files should not be guessed at."""


@dataclass(frozen=True)
class Edit:
    relative_path: str
    before: bytes
    after: bytes
    mode: int


def _need(condition: bool, message: str) -> None:
    if not condition:
        raise ConfigurationError(message)


def _identifier(value: str, label: str, *, maximum: int) -> None:
    _need(len(value) <= maximum and re.fullmatch(
        r"[A-Za-z][A-Za-z0-9-]*(?:\.[A-Za-z][A-Za-z0-9-]*)+", value) is not None,
        f"{label} must be a reverse-DNS identifier with ASCII letters, digits, dots, and hyphens.")


def validate_identity(identity: dict, *, allow_placeholder: bool = False) -> None:
    _need(type(identity) is dict and set(identity) == STATE_KEYS, "Unexpected identity.json schema.")
    _need(type(identity["schema_version"]) is int and identity["schema_version"] == 1,
          "Unsupported identity.json schema_version.")
    _need(all(type(identity[key]) is str for key in STATE_KEYS - {"schema_version"}),
          "All identity fields except schema_version must be strings.")
    bundle, group, team = identity["bundle_id"], identity["app_group"], identity["team_id"]
    bundle_lower, group_lower = bundle.lower(), group.lower()
    _identifier(bundle, "Bundle ID", maximum=200)  # Leave room for extension suffixes.
    placeholder = all(identity[key] == value for key, value in PLACEHOLDER.items())
    _need(allow_placeholder or not placeholder,
          "This is the committed placeholder identity: configure your own bundle ID, team and App Group.")
    _need(placeholder or not (bundle_lower == "com.example" or bundle_lower.startswith("com.example.")),
          "Replace the example bundle ID with a unique ID you own.")
    _need(not (bundle_lower == "com.gstudios" or bundle_lower.startswith("com.gstudios.")),
          "Do not reuse upstream Rivulet's bundle namespace.")
    _need(placeholder or re.fullmatch(r"[A-Z0-9]{10}", team) is not None,
          "Team ID must be 10 uppercase ASCII letters/digits from your Apple account.")
    _need(team != UPSTREAM_TEAM, "Do not reuse upstream Rivulet's signing team.")
    _identifier(group, "App Group", maximum=255)
    _need(group.startswith("group."), "App Group must start with group.")
    _need(group_lower not in {"group.com.bain.rivulet", "group.com.gstudios"}
          and not group_lower.startswith("group.com.gstudios."),
          "Do not reuse upstream Rivulet's App Group.")
    _need(group != bundle, "App Group and app bundle ID must be distinct.")
    _need(placeholder or not (group_lower == "group.com.example" or group_lower.startswith("group.com.example.")),
          "Replace the example App Group with an owned App Group.")
    scheme = identity["url_scheme"]
    _need(re.fullmatch(r"[a-z][a-z0-9+.-]{1,62}", scheme) is not None,
          "URL scheme must be 2–63 lowercase ASCII letters/digits/+, ., - and start with a letter.")
    _need(scheme not in {"rivulet", "http", "https", "file", "ftp", "mailto", "itms", "itms-apps"},
          "Use your own URL scheme, not the stock app's or a standard protocol.")
    _display_name(identity["display_name"])


def _display_name(value: str) -> None:
    # Written into project.pbxproj (quoted) and two Info.plists: nothing that needs escaping there.
    _need(0 < len(value) <= 64 and value == value.strip() and value.isprintable()
          and not any(c in value for c in '"\\;<>&$'),
          "Display name must be 1-64 printable characters, without quotes, backslashes, ; < > & or $.")


def _safe_file(root: Path, relative: str) -> Path:
    _need(relative in (*MANAGED_FILES, STATE_FILE), "File is outside the identity allowlist.")
    _need(root.is_dir() and not root.is_symlink(), "Repository root must be a real directory.")
    path = root
    for part in Path(relative).parts:
        path = path / part
        _need(not path.is_symlink(), f"Refusing symlink in {relative}.")
    _need(path.is_file(), f"Required identity file is missing: {relative}")
    _need(root.resolve() in path.resolve().parents, f"Path escaped the repository: {relative}")
    return path


def _inputs(root: Path) -> tuple[dict[str, bytes], dict[str, int]]:
    raw, modes = {}, {}
    try:
        for relative in (*MANAGED_FILES, STATE_FILE):
            path = _safe_file(root, relative)
            raw[relative] = path.read_bytes()
            modes[relative] = stat.S_IMODE(path.stat().st_mode)
    except OSError as exc:
        raise ConfigurationError(f"Could not read identity files: {exc}") from exc
    return raw, modes


def _unique_object(pairs: list[tuple]) -> dict:
    result = {}
    for key, value in pairs:
        _need(key not in result, f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _state(raw: bytes) -> dict:
    try:
        identity = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ConfigurationError("identity.json must be valid UTF-8 JSON.") from exc
    validate_identity(identity, allow_placeholder=True)
    return identity


def _replace(text: str, before: str, after: str, count: int, label: str) -> str:
    _need(text.count(before) == count, f"Source drift at {label}; expected {count} exact binding(s).")
    return text.replace(before, after)


def _plist(text: str, label: str) -> dict:
    try:
        value = plistlib.loads(text.encode("utf-8"))
    except Exception as exc:
        raise ConfigurationError(f"Invalid plist: {label}") from exc
    _need(type(value) is dict, f"Expected a dictionary plist: {label}")
    return value


def _unquote(value: str) -> str:
    value = value.strip()
    return value[1:-1] if value.startswith('"') and value.endswith('"') else value


def _project(text: str, old: dict, new: dict) -> str:
    # Check the entire set, not just the first app target: extensions must be
    # signed by the same team and carry bundle IDs under the containing app.
    team = re.compile(r"^([ \t]*DEVELOPMENT_TEAM = )([^;\n]+)(;[ \t]*)$", re.MULTILINE)
    teams = [m.group(2) for m in team.finditer(text)]
    _need(len(teams) == 14 and all(_unquote(x) == old["team_id"] for x in teams),
          "Source drift in DEVELOPMENT_TEAM fields; expected 14 matching settings.")
    text = team.sub(lambda m: f'{m[1]}"{new["team_id"]}"{m[3]}', text)
    bundle = re.compile(r"^([ \t]*PRODUCT_BUNDLE_IDENTIFIER = )([^;\n]+)(;[ \t]*)$", re.MULTILINE)
    expected = Counter({old["bundle_id"]: 6, old["bundle_id"] + ".Tests": 2,
                        old["bundle_id"] + ".TopShelfExtension": 2,
                        old["bundle_id"] + ".AssetPackExtension": 2})
    _need(Counter(_unquote(m[2]) for m in bundle.finditer(text)) == expected,
          "Source drift in bundle IDs; expected tvOS/iOS/Mac, Tests, and both extension pairs.")
    # Counts alone cannot catch two valid IDs swapped between targets. Inspect
    # each existing target/configuration before rewriting any setting. If Xcode
    # changes this serialization, setup stops for a deliberate review.
    target_suffixes = {"Rivulet": "", "Rivulet iOS": "", "Rivulet Mac": "", "RivuletTests": ".Tests",
                       "TopShelfExtension": ".TopShelfExtension",
                       "AssetPackExtension": ".AssetPackExtension"}
    configurations = re.compile(
        r'/\* (Debug|Release) configuration for PBXNativeTarget "([^"]+)" \*/ = \{\n'
        r'\t\t\tisa = XCBuildConfiguration;\n\t\t\tbuildSettings = \{(.*?)\n\t\t\t\};',
        re.DOTALL)
    found = list(configurations.finditer(text))
    _need(Counter((m[1], m[2]) for m in found) ==
          Counter((configuration, target) for configuration in ("Debug", "Release")
                  for target in target_suffixes), "Source drift in target configuration blocks.")
    modules = {"Rivulet": "Rivulet", "Rivulet iOS": "Rivulet_iOS", "Rivulet Mac": "Rivulet_Mac"}
    for configuration in found:
        target = configuration[2]
        identifiers = [_unquote(m[2]) for m in bundle.finditer(configuration[3])]
        _need(identifiers == [old["bundle_id"] + target_suffixes[target]],
              f"Bundle ID is attached to the wrong target: {target} {configuration[1]}.")
        if target in modules:
            # Product filenames change; module names must not change persisted Swift type identities.
            _need('PRODUCT_NAME = "Reference Player";' in configuration[3]
                  and f'PRODUCT_MODULE_NAME = {modules[target]};' in configuration[3],
                  f"App product/module name drift: {target} {configuration[1]}.")
    mapping = {value: new["bundle_id"] + value[len(old["bundle_id"]):] for value in expected}
    text = bundle.sub(lambda m: f'{m[1]}{mapping[_unquote(m[2])]}{m[3]}', text)
    display = re.compile(r"^[ \t]*INFOPLIST_KEY_CFBundleDisplayName = ([^;\n]+);[ \t]*$", re.MULTILINE)
    _need(Counter(_unquote(m[1]) for m in display.finditer(text)) ==
          Counter({old["display_name"]: 6, "TopShelfExtension": 2, "AssetPackExtension": 2}),
          "Source drift in target display names.")
    text = display.sub(lambda m: m[0] if _unquote(m[1]) in ("TopShelfExtension", "AssetPackExtension")
                       else m[0].replace(m[1], f'"{new["display_name"]}"'), text)
    _need("APPATTEST_ENVIRONMENT" not in text, "Upstream App Attest settings were reintroduced.")
    _need(text.count("path = System/Library/Frameworks/AVKit.framework; sourceTree = SDKROOT;") == 1,
          "AVKit must resolve through SDKROOT, not an obsolete hardcoded SDK path.")
    return text


def _edits(raw: dict[str, bytes], modes: dict[str, int], old: dict, new: dict) -> list[Edit]:
    try:
        texts = {name: data.decode("utf-8") for name, data in raw.items()}
    except UnicodeError as exc:
        raise ConfigurationError("An identity source file is not valid UTF-8.") from exc
    texts[PROJECT] = _project(texts[PROJECT], old, new)
    for name in (TV_INFO, IOS_INFO, MAC_INFO):
        _need(_plist(texts[name], name).get("CFBundleName") == "$(INFOPLIST_KEY_CFBundleDisplayName)",
              f"Bundle/menu name no longer follows the configured display name in {name}.")
    for name in ENTITLEMENTS:
        values = _plist(texts[name], name)
        _need(values.get("com.apple.security.application-groups") == [old["app_group"]],
              f"App Group drift in {name}.")
        _need("com.apple.developer.devicecheck.appattest-environment" not in values,
              f"Upstream App Attest entitlement reintroduced in {name}.")
        texts[name] = _replace(texts[name], f'<string>{old["app_group"]}</string>',
                               f'<string>{new["app_group"]}</string>', 1, name)
    tv = _plist(texts[TV_INFO], TV_INFO)
    _need(tv.get("BAAppGroupID") == old["app_group"], "BAAppGroupID drift in tvOS Info.plist.")
    _need(tv.get("CFBundleURLTypes") == [{"CFBundleURLName": old["bundle_id"],
                                        "CFBundleURLSchemes": [old["url_scheme"]]}],
          "URL registration drift in tvOS Info.plist.")
    for field in ("app_group", "bundle_id", "url_scheme"):
        texts[TV_INFO] = _replace(texts[TV_INFO], f'<string>{old[field]}</string>',
                                  f'<string>{new[field]}</string>', 1, TV_INFO + ":" + field)
    ios = _plist(texts[IOS_INFO], IOS_INFO)
    _need(ios.get("CFBundleDisplayName") == old["display_name"], "iOS display name drift.")
    _need(ios.get("BGTaskSchedulerPermittedIdentifiers") == [old["bundle_id"] + ".downloads.activity.*"],
          "Background-task identifier drift in iOS Info.plist.")
    texts[IOS_INFO] = _replace(texts[IOS_INFO], old["bundle_id"] + ".downloads.activity.*",
                               new["bundle_id"] + ".downloads.activity.*", 1, IOS_INFO)
    for name in (IOS_INFO, MAC_INFO):
        texts[name] = _replace(texts[name], f'<key>CFBundleDisplayName</key>\n\t<string>{old["display_name"]}</string>',
                               f'<key>CFBundleDisplayName</key>\n\t<string>{new["display_name"]}</string>', 1,
                               name + ":display_name")
    mac = _plist(texts[MAC_INFO], MAC_INFO)
    _need(mac.get("CFBundleDisplayName") == new["display_name"], "Mac display name drift.")
    _need(mac.get("CFBundleURLTypes") == [{"CFBundleURLName": old["bundle_id"],
                                         "CFBundleURLSchemes": [old["url_scheme"]]}],
          "URL registration drift in Mac Info.plist.")
    for field in ("bundle_id", "url_scheme"):
        texts[MAC_INFO] = _replace(texts[MAC_INFO], f'<string>{old[field]}</string>',
                                   f'<string>{new[field]}</string>', 1, MAC_INFO + ":" + field)
    for name in CLOUD_FILES:
        if name.endswith(".entitlements"):
            _need(_plist(texts[name], name).get("com.apple.developer.icloud-container-identifiers")
                  == [f'iCloud.{old["bundle_id"]}.sync'], f"iCloud container drift in {name}.")
        texts[name] = _replace(texts[name], f'iCloud.{old["bundle_id"]}.sync',
                               f'iCloud.{new["bundle_id"]}.sync', 1, name)
    for name in GROUP_SOURCES:
        texts[name] = _replace(texts[name], f'appGroupIdentifier = "{old["app_group"]}"',
                               f'appGroupIdentifier = "{new["app_group"]}"', 1, name)
    texts[LINK_SOURCE] = _replace(texts[LINK_SOURCE], f'static let scheme = "{old["url_scheme"]}"',
                                  f'static let scheme = "{new["url_scheme"]}"', 1, LINK_SOURCE)
    for name, (field, suffix) in IOS_IDENTIFIERS.items():
        texts[name] = _replace(texts[name], f'{field} = "{old["bundle_id"]}{suffix}"',
                               f'{field} = "{new["bundle_id"]}{suffix}"', 1, name)
    if new != old:
        texts[STATE_FILE] = json.dumps(new, indent=2) + "\n"
    # Parse generated plists too; identifiers are restricted, but correctness of
    # our own edit must not depend solely on that restriction.
    for name in MANAGED_FILES:
        if name.endswith((".plist", ".entitlements")):
            _plist(texts[name], name)
    return [Edit(name, raw[name], texts[name].encode("utf-8"), modes[name])
            for name in (*MANAGED_FILES, STATE_FILE) if texts[name].encode("utf-8") != raw[name]]


def validate_configuration(root: Path, *, require_configured: bool = False) -> dict:
    """Read-only validation shared with verify.py; never changes local files."""
    raw, modes = _inputs(root)
    identity = _state(raw[STATE_FILE])
    validate_identity(identity, allow_placeholder=not require_configured)
    _edits(raw, modes, identity, identity)
    return identity


def plan_configuration(root: Path, identity: dict, *, placeholder: bool = False) -> list[Edit]:
    """`placeholder` allows the target to be the committed placeholder identity (--placeholder)."""
    validate_identity(identity, allow_placeholder=placeholder)
    raw, modes = _inputs(root)
    old = _state(raw[STATE_FILE])
    return _edits(raw, modes, old, identity)


def _stage(path: Path, content: bytes, mode: int) -> Path:
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + ".private-", dir=path.parent)
    staged = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as handle:
            os.fchmod(handle.fileno(), mode)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        staged.unlink(missing_ok=True)
        raise
    return staged


def apply_plan(root: Path, edits: list[Edit]) -> None:
    """Stage all writes, check for intervening edits, then atomically replace files."""
    staged: dict[str, Path] = {}
    applied: list[Edit] = []
    try:
        for edit in edits:
            path = _safe_file(root, edit.relative_path)
            _need(path.read_bytes() == edit.before, f"File changed during setup: {edit.relative_path}")
            staged[edit.relative_path] = _stage(path, edit.after, edit.mode)
        # Validate the entire set again before the first replacement, so an edit
        # during staging does not result in a half-configured project.
        for edit in edits:
            _need(_safe_file(root, edit.relative_path).read_bytes() == edit.before,
                  f"File changed during setup: {edit.relative_path}")
        for edit in edits:
            os.replace(staged[edit.relative_path], _safe_file(root, edit.relative_path))
            del staged[edit.relative_path]
            applied.append(edit)
    except BaseException as exc:
        rollback_failures = []
        for edit in reversed(applied):
            rollback = None
            try:
                path = _safe_file(root, edit.relative_path)
                _need(path.read_bytes() == edit.after, "A concurrent edit prevents safe rollback.")
                rollback = _stage(path, edit.before, edit.mode)
                os.replace(rollback, path)
            except (OSError, ConfigurationError):
                rollback_failures.append(edit.relative_path)
            finally:
                if rollback is not None:
                    rollback.unlink(missing_ok=True)
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        suffix = (" Restore these files from your Git checkpoint: " + ", ".join(rollback_failures)
                  if rollback_failures else " Any applied changes were rolled back.")
        raise ConfigurationError(f"Identity setup stopped: {exc}.{suffix}") from exc
    finally:
        for path in staged.values():
            path.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--bundle-id", help="A unique reverse-DNS app ID you own.")
    parser.add_argument("--team-id", help="Your 10-character Apple Developer team ID.")
    parser.add_argument("--app-group", help="Owned App Group; defaults to group.<bundle-id>.")
    parser.add_argument("--url-scheme", help="Deep-link scheme; defaults to the current one (referenceplayer).")
    parser.add_argument("--display-name", help="Home-screen name of the apps; defaults to the current one.")
    parser.add_argument("--placeholder", action="store_true",
                        help="Put back the committed placeholder bundle ID, App Group and empty team "
                             "(before committing a change to the shared repository).")
    parser.add_argument("--dry-run", action="store_true", help="Validate and list changes without writing.")
    args = parser.parse_args(argv)
    if args.placeholder and (args.bundle_id or args.team_id or args.app_group):
        parser.error("--placeholder replaces --bundle-id, --team-id and --app-group")
    if not args.placeholder and (args.bundle_id is None or args.team_id is None):
        parser.error("--bundle-id and --team-id are required (or --placeholder)")
    root = Path(__file__).resolve().parent.parent  # Never depends on the caller's working directory.
    try:
        current = _state(_safe_file(root, STATE_FILE).read_bytes())
        chosen = dict(PLACEHOLDER) if args.placeholder else {
            "bundle_id": args.bundle_id, "team_id": args.team_id,
            "app_group": args.app_group if args.app_group is not None else "group." + args.bundle_id}
        identity = {"schema_version": 1, **chosen,
                    "url_scheme": args.url_scheme if args.url_scheme is not None else current["url_scheme"],
                    "display_name": args.display_name if args.display_name is not None else current["display_name"]}
        edits = plan_configuration(root, identity, placeholder=args.placeholder)
        if not args.dry_run:
            apply_plan(root, edits)
        print(("Would update" if args.dry_run else "Updated") + f" {len(edits)} identity files.")
        for edit in edits:
            print("  " + edit.relative_path)
        print("No provisioning, uploads, installation, engine updates, or credential changes were performed.")
        return 0
    except (ConfigurationError, OSError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
