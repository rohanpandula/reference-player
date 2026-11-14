#!/usr/bin/env python3
"""The overlay's footprint in upstream files, and a ratchet on it.

    python3 PrivateBuild/footprint.py            # compare with PrivateBuild/footprint.json
    python3 PrivateBuild/footprint.py --update   # record today's numbers (a deliberate act)

Measures `git diff --numstat` from the recorded upstream baseline (BASELINE in verify.py) to the
working tree, for files upstream has (modified or deleted by the overlay; new private files do not
count). Fails when the overlay modifies an upstream file the record does not list, adds or removes
more lines in a file than the record allows, or modifies a file on MUST_EQUAL_UPSTREAM. Passes, and
reports the decrease, when numbers only went down; --update is then a choice, not a chore.
Not a git checkout, or the baseline commit not present: "not checked", exit 0, so the portable
checks still run from a source ZIP.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
RECORD = "PrivateBuild/footprint.json"
# Upstream files the overlay must not own: each must stay exactly upstream's text, so none may appear in
# the footprint (checked here) and sync.py refuses to merge while one differs from the baseline (it
# never overwrites one). The one definition of the list; sync.py reads it from this module. Each once
# carried private lines that were moved out on purpose: the five catalog keys (now Private.xcstrings),
# and four upstream test files restored to upstream's. README.md now belongs to the fork's branding.
# A path that needs a private line again leaves this list in the same commit.
MUST_EQUAL_UPSTREAM = (
    "Rivulet/Localizable.xcstrings",
    "RivuletTests/Unit/DolbyVisionPlaybackTests.swift",
    "RivuletTests/Unit/SettingsUsageTelemetryTests.swift",
    "RivuletTests/Unit/TMDBDiscoverServiceTests.swift",
    "RivuletTests/Unit/TMDBLogoCacheTests.swift",
)


def baseline(root: Path) -> str:
    spec = importlib.util.spec_from_file_location("private_verify_baseline", root / "PrivateBuild/verify.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.BASELINE


def measure(root: Path, base: str) -> Optional[dict]:
    """{path: [added, removed]} for upstream files the overlay changes, or None when git or the
    baseline commit is not available."""
    def git(*args: str) -> str:
        return subprocess.run(["git", "-c", "core.fsmonitor=false", "-C", str(root), *args],
                              check=True, capture_output=True, text=True).stdout
    try:
        git("cat-file", "-e", f"{base}^{{commit}}")
        out = git("diff", "--numstat", "--no-renames", "--diff-filter=MD", base, "--")
    except (OSError, subprocess.CalledProcessError):
        return None
    files = {}
    for line in out.splitlines():
        added, removed, path = line.split("\t", 2)
        files[path] = [0 if added == "-" else int(added), 0 if removed == "-" else int(removed)]
    return files


def totals(files: dict) -> tuple[int, int, int]:
    return len(files), sum(a for a, _ in files.values()), sum(r for _, r in files.values())


def compare(recorded: dict, current: dict, must_equal=MUST_EQUAL_UPSTREAM) -> tuple[list[str], list[str]]:
    """(failures, decreases). A file on MUST_EQUAL_UPSTREAM, a new file, or more added or more removed
    lines fails; anything smaller is a decrease."""
    failures, decreases = [], []
    for path, (added, removed) in sorted(current.items()):
        if path in must_equal:
            failures.append(f"{path} is on MUST_EQUAL_UPSTREAM but the overlay modifies it (+{added}/-{removed}): "
                            "restore upstream's text, or take it off the list in footprint.py")
        elif path not in recorded:
            failures.append(f"new upstream file modified: {path} (+{added}/-{removed})")
        elif added > recorded[path][0] or removed > recorded[path][1]:
            grew = [f"+{added} added lines, the record allows +{recorded[path][0]}"] if added > recorded[path][0] else []
            grew += [f"-{removed} removed lines, the record allows -{recorded[path][1]}"] if removed > recorded[path][1] else []
            failures.append(f"{path}: " + "; ".join(grew))
        elif (added, removed) != tuple(recorded[path]):
            decreases.append(f"{path}: +{added}/-{removed}, recorded +{recorded[path][0]}/-{recorded[path][1]}")
    decreases += [f"{path}: no longer modified" for path in sorted(set(recorded) - set(current))]
    return failures, decreases


def render(base: str, files: dict) -> str:
    count, added, removed = totals(files)
    lines = ['{', f'  "baseline": "{base}",',
             f'  "totals": {{"files": {count}, "added": {added}, "removed": {removed}}},',
             '  "files": {']
    rows = [f'    {json.dumps(path)}: [{a}, {r}]' for path, (a, r) in sorted(files.items())]
    lines += [",\n".join(rows), "  }", "}"] if rows else ["  }", "}"]
    return "\n".join(lines) + "\n"


def main(argv: Optional[list[str]] = None, root: Path = ROOT) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--update", action="store_true", help="rewrite PrivateBuild/footprint.json with today's numbers")
    args = parser.parse_args(argv)
    base = baseline(root)
    current = measure(root, base)
    if current is None:
        print(f"NOT CHECKED: footprint needs a git checkout with the baseline commit {base[:7]}")
        return 0
    count, added, removed = totals(current)
    summary = f"{count} upstream files, +{added}/-{removed} against {base[:7]}"
    if args.update:
        (root / RECORD).write_text(render(base, current), encoding="utf-8")
        print(f"UPDATED: {RECORD} records {summary}")
        return 0
    try:
        record = json.loads((root / RECORD).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"FAIL: cannot read {RECORD}: {exc}", file=sys.stderr)
        return 1
    failures, decreases = compare(record.get("files", {}), current)
    if record.get("baseline") != base:
        print(f"note: the record was made against {str(record.get('baseline'))[:7]}, the baseline is now {base[:7]}", flush=True)
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        print("The overlay grew inside upstream files (or a must-equal file is no longer upstream's). Move the "
              "private code into a private file. Only if the growth is deliberate and you have read the numbers "
              "above, record it with --update and say why in the commit.", file=sys.stderr)
        return 1
    print(f"PASS: footprint {summary}, within the record")
    if decreases:
        for decrease in decreases:
            print(f"  smaller: {decrease}")
        print("  The footprint shrank and nothing grew. If the smaller numbers are what you meant, "
              "`python3 PrivateBuild/footprint.py --update` locks them in; nothing requires it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
