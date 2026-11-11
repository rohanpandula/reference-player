#!/usr/bin/env python3
"""Where this fork's UI text is headed, read from the string data of the last app build.

Run after any build of the tvOS app (`xcodebuild … build` or `test`):

    python3 PrivateBuild/check-private-strings.py [--objects <…/Rivulet.build/Objects-normal/arm64>]

Without --objects it takes the newest `Rivulet.build/*/Rivulet.build/Objects-normal/*` in this
checkout's DerivedData. Fails when:
  - `Rivulet/Localizable.xcstrings` differs from upstream's (BASELINE in verify.py);
  - a literal from a private-only Swift file, or from a private line inside an upstream file, is
    headed for any table but `Private` (On, Off and OK are upstream's words and stay on its table);
  - a private Swift file under Rivulet/ or RivuletCore/ has no string data (not built);
  - syncing a copy of `Rivulet/Private.xcstrings` from the string data adds, removes or marks stale
    any key (the catalog is incomplete or keeps a key nothing uses).
It cannot tell string data from an older build of the same files: run it after building the tree
you mean to check. Reads only; the catalogs are synced as copies in a temporary directory. Covers the tvOS app target:
private files under RivuletCore/ also build into the iOS and Mac apps, whose catalogs it does not read.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHARED = {"On", "Off", "OK"}   # upstream's catalog owns these; private code uses its key and table
PRIVATE_CATALOG = "Rivulet/Private.xcstrings"
UPSTREAM_CATALOG = "Rivulet/Localizable.xcstrings"
APP_ROOTS = ("Rivulet/", "RivuletCore/")   # folders compiled into the tvOS app target


def baseline() -> str:
    spec = importlib.util.spec_from_file_location("private_verify", ROOT / "PrivateBuild/verify.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.BASELINE


def git(*args: str) -> str:
    return subprocess.check_output(["git", "-c", "core.fsmonitor=false", "-C", str(ROOT), *args], text=True)


def private_swift(base: str) -> tuple[set, dict]:
    """(Swift files the overlay added, {upstream Swift file it modified: its added line numbers}) under
    APP_ROOTS. --no-renames: a renamed file is a deletion plus an addition, never skipped as "R"."""
    private_files, added_lines = set(), {}
    for line in git("diff", "--name-status", "--no-renames", base, "--", *APP_ROOTS).splitlines():
        status, path = line.split("\t")[0], line.split("\t")[-1]
        if not path.endswith(".swift"):
            continue
        if status == "A":
            private_files.add(path)
        elif status == "M":
            numbers = set()
            for start, count in re.findall(r"^@@ -\S+ \+(\d+)(?:,(\d+))? @@", git("diff", "-U0", "--no-renames", base, "--", path), re.M):
                numbers.update(range(int(start), int(start) + int(count or 1)))
            added_lines[path] = numbers
    return private_files, added_lines


def newest_objects() -> Path:
    derived = Path.home() / "Library/Developer/Xcode/DerivedData"
    found = []
    for info in derived.glob("*/info.plist"):
        try:
            workspace = plistlib.loads(info.read_bytes()).get("WorkspacePath", "")
        except (OSError, plistlib.InvalidFileException):
            continue
        if Path(workspace).parent == ROOT:
            found += info.parent.glob("Build/Intermediates.noindex/Rivulet.build/*/Rivulet.build/Objects-normal/*")
    if not found:
        sys.exit(f"no build of the Rivulet target found in DerivedData for {ROOT}; build first or pass --objects")
    return max(found, key=lambda path: path.stat().st_mtime)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--objects", type=Path, help="the Objects-normal/<arch> directory of a Rivulet target build")
    objects = parser.parse_args().objects or newest_objects()
    base = baseline()
    errors: list[str] = []

    if subprocess.run(["git", "-C", str(ROOT), "diff", "--quiet", base, "--", UPSTREAM_CATALOG]).returncode != 0:
        errors.append(f"{UPSTREAM_CATALOG} differs from upstream's ({base[:7]}): private keys belong in {PRIVATE_CATALOG}")
    upstream_keys = json.loads(git("show", f"{base}:{UPSTREAM_CATALOG}"))["strings"]
    errors += [f"shared word {word!r} is not in upstream's catalog" for word in sorted(SHARED - set(upstream_keys))]

    private_files, added_lines = private_swift(base)

    stringsdata = sorted(objects.glob("*.stringsdata"))
    seen, private_entries = set(), 0
    for data in stringsdata:
        record = json.loads(data.read_text(encoding="utf-8"))
        source = Path(record["source"])
        relative = source.relative_to(ROOT).as_posix() if source.is_relative_to(ROOT) else None
        if relative in private_files:
            seen.add(relative)
        for table, entries in record["tables"].items():
            for entry in entries:
                line = entry.get("location", {}).get("startingLine")   # some entries carry no location
                private = relative in private_files or line in added_lines.get(relative, ())
                if table == "Private":
                    private_entries += 1
                    if not private:
                        errors.append(f"{relative}:{line}: {entry['key']!r} is headed for Private from an upstream line")
                elif private and entry["key"] not in SHARED:
                    errors.append(f"{relative}:{line}: private {entry['key']!r} is headed for {table}, not Private")
    for path in sorted(private_files):
        if path not in seen:
            errors.append(f"no string data for {path} in {objects}: build the app first")

    with tempfile.TemporaryDirectory() as scratch:
        copy = Path(scratch) / "Private.xcstrings"
        shutil.copyfile(ROOT / PRIVATE_CATALOG, copy)
        before = json.loads(copy.read_text(encoding="utf-8"))["strings"]
        subprocess.run(["xcrun", "xcstringstool", "sync", str(copy), "--stringsdata", *map(str, stringsdata)],
                       check=True, capture_output=True)
        after = json.loads(copy.read_text(encoding="utf-8"))["strings"]
    errors += [f"{PRIVATE_CATALOG} lacks {key!r}, which the code uses" for key in sorted(after.keys() - before.keys())]
    # A sync drops an unused key that has no translation, and marks one that has a translation stale.
    errors += [f"{PRIVATE_CATALOG} keeps {key!r}, which no code uses" for key in sorted(before.keys() - after.keys())]
    errors += [f"{PRIVATE_CATALOG} keeps {key!r}, which no code uses" for key, value in sorted(after.items())
               if value.get("extractionState") == "stale"]

    if errors:
        for error in errors:
            print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print(f"PASS: {private_entries} private literals from {len(stringsdata)} string data files are headed for Private; "
          f"{PRIVATE_CATALOG} holds exactly the {len(before)} keys they use; {UPSTREAM_CATALOG} is upstream's.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
