#!/usr/bin/env python3
"""Merge one upstream Rivulet release into this fork, do the mechanical part, and report.

    python3 PrivateBuild/sync.py <tag> [--discard-catalog-drift]   # e.g. v1.3.0-127
    python3 PrivateBuild/sync.py [<tag>] --forecast                 # what merging <tag> (or upstream main) would do
    python3 PrivateBuild/sync.py [<tag>] --undo                     # prints the undo commands, runs nothing
    python3 PrivateBuild/sync.py <tag> --branch my-branch           # the fork lives on another branch than main

Run on the fork's branch (`main` unless --branch says otherwise). It never commits and never pushes; the
only network use is a read-only `git fetch` of one ref from the `upstream` remote.

<tag> first checks everything it can with nothing changed: the branch, no merge in progress, a clean
tree (String Catalog drift in upstream's own catalogs alone may be discarded with
--discard-catalog-drift), that each file in MUST_EQUAL_UPSTREAM (footprint.py) is still the recorded
baseline's text, that CLAUDE.md is exactly the private import block over the baseline's text, the
tag fetched (into FETCH_HEAD only) and not already merged, that upstream left the three baseline
files alone, every edit to them, and the fallback tag. Any refusal there changes no ref and no file.
Only then: rerere on for this repository, the catalog drift restored, the tags
fallback/candidate-before-<tag> (at HEAD) and upstream/<tag> created (or reused when they already
point there; never moved), `git merge --no-ff --no-commit`, and the steps of SYNC.md "After the
merge starts". It writes a report (stdout and PrivateBuild/.build/sync/sync-<tag>.txt, also on a
refusal). Exit 0: the mechanical part is done and nothing is conflicted. 1: something is left for a
person (the report says what). 2: refused or failed.

--forecast changes no working-tree file, no branch and no tag: it fetches the ref into
refs/upstream-forecast/<ref> only and runs `git merge-tree`. Exit 0 when the merge would be clean (or
there is nothing new), 1 when it would conflict, 2 on error.

What this does not do: resolve Swift conflicts, judge a moved pin, put back a lost hook, review a
changed sha256-pinned file, overwrite any file the overlay owns, build, test, or commit. SYNC.md says
how a person does those.
"""
from __future__ import annotations

import argparse
import datetime
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional

ROOT = Path(__file__).resolve().parent.parent
CANDIDATE_BRANCH = "main"   # the branch the fork lives on; --branch sets another
REMOTE = "upstream"
OVERLAY_CATALOG = "Rivulet/Private.xcstrings"   # the overlay's own catalog: never discarded as drift
PBXPROJ = "Rivulet.xcodeproj/project.pbxproj"
CLAUDE = "CLAUDE.md"
CLAUDE_BLOCK_START = b"@PrivateBuild/CLAUDE-private.md\n"
WHATSNEW = "Rivulet/Views/Components/WhatsNewView.swift"
NOTES = "PrivateBuild/CLAUDE-private.md"   # carries one line naming the merged upstream release
BASELINE_FILES = ("PrivateBuild/verify.py", "PrivateBuild/UPSTREAM.json", NOTES)
REPORT_DIR = "PrivateBuild/.build/sync"   # git-ignored (.gitignore: PrivateBuild/.build/)
TAG = re.compile(r"v[0-9][0-9A-Za-z.\-]*")
REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._\-]*")
PIN_RULE = ("A dependency pin moved. Do not edit PINS until you have read that dependency's release notes "
            "(SYNC.md, 'A pin moved'). Never take an AetherEngine that drops DualLayerDolbyVision or "
            "TrueHDObjectAudio: the fork then stays on the last engine that has them.")


class SyncError(Exception):
    pass


# ---------------------------------------------------------------------------------------------------
# Pure parts (tested in PrivateBuild/tests/test_sync.py)

def replace_once(text: str, old: str, new: str, where: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SyncError(f"{where}: expected exactly one {old!r}, found {count}; edit it by hand and rerun")
    return text.replace(old, new)


VERIFY_LINE = re.compile(r'^BASELINE = "([0-9a-f]{40})"  # upstream (\S+), the last upstream release merged into this fork$', re.M)


def verify_baseline(text: str) -> tuple[str, str]:
    """(hash, tag) of the one BASELINE line in verify.py."""
    found = VERIFY_LINE.findall(text)
    if len(found) != 1:
        raise SyncError(f"verify.py: expected one BASELINE line of the known form, found {len(found)}")
    return found[0]


def bump_verify(text: str, new_hash: str, new_tag: str) -> str:
    old_hash, old_tag = verify_baseline(text)
    old = f'BASELINE = "{old_hash}"  # upstream {old_tag}, the last upstream release merged into this fork'
    new = f'BASELINE = "{new_hash}"  # upstream {new_tag}, the last upstream release merged into this fork'
    return replace_once(text, old, new, "verify.py BASELINE")


def bump_upstream_json(text: str, new_hash: str, label: str, date: str) -> str:
    data = json.loads(text)
    rivulet = data["rivulet"]
    for key, value in (("revision", new_hash), ("changelog_label", label), ("last_reviewed_upstream_main", new_hash)):
        text = replace_once(text, f'"{key}": "{rivulet[key]}"', f'"{key}": "{value}"', f"UPSTREAM.json rivulet.{key}")
    text = replace_once(text, f'"prepared_on": "{data["prepared_on"]}"', f'"prepared_on": "{date}"', "UPSTREAM.json prepared_on")
    after = json.loads(text)
    if (after["rivulet"]["revision"], after["rivulet"]["changelog_label"], after["rivulet"]["last_reviewed_upstream_main"],
            after["prepared_on"]) != (new_hash, label, new_hash, date):
        raise SyncError("UPSTREAM.json: a replacement landed on another key")
    return text


def bump_notes(text: str, old_tag: str, old_hash: str, new_tag: str, new_hash: str, date: str) -> str:
    """The one line of CLAUDE-private.md that names the merged release: "upstream Rivulet `<tag>`
    (`<hash>`), merged <date>"."""
    line = re.compile(re.escape(f"upstream Rivulet `{old_tag}` (`{old_hash}`), merged ") + r"\d{4}-\d{2}-\d{2}")
    found = line.findall(text)
    if len(found) != 1:
        raise SyncError(f"{NOTES}: expected one baseline line for {old_tag} ({old_hash[:7]}), found {len(found)}")
    return text.replace(found[0], f"upstream Rivulet `{new_tag}` (`{new_hash}`), merged {date}")


def newest_changelog_label(whatsnew: str) -> str:
    match = re.search(r'static let changelogs\b[^=]*=\s*\[\s*\("([^"]+)"', whatsnew)
    if not match:
        raise SyncError(f"{WHATSNEW}: cannot find the newest changelog key")
    return match.group(1)


def claude_block(ours: bytes, old_upstream: bytes) -> bytes:
    """The private import block on top of CLAUDE.md, when HEAD's file is exactly that block over the
    baseline's text. Bytes throughout: a carriage return anywhere is refused rather than rewritten."""
    if b"\r" in ours or b"\r" in old_upstream:
        raise SyncError("CLAUDE.md has a carriage return (here or in upstream's baseline text): merge it by hand")
    if not ours.endswith(old_upstream):
        raise SyncError("CLAUDE.md is no longer the private import block followed by upstream's baseline text: "
                        "restore that shape (a private correction belongs in PrivateBuild/CLAUDE-private.md) and commit")
    block = ours[:len(ours) - len(old_upstream)]
    if not block.startswith(CLAUDE_BLOCK_START):
        raise SyncError("CLAUDE.md: the private import block is missing from its top")
    return block


def claude_md(ours: bytes, old_upstream: bytes, new_upstream: bytes) -> bytes:
    """Upstream's new CLAUDE.md under the private import block exactly as it is today."""
    block = claude_block(ours, old_upstream)
    if b"\r" in new_upstream:
        raise SyncError("upstream's new CLAUDE.md has a carriage return: merge it by hand")
    return block + new_upstream


def preflight(branch: Optional[str], merging: bool, porcelain: list[str], discard_drift: bool,
              upstream_owned: Callable[[str], bool]) -> list[str]:
    """Catalog files to restore from HEAD (empty when the tree is clean); raises SyncError to refuse.
    Only a modified `.xcstrings` file that exists in the recorded upstream baseline counts as drift."""
    if branch != CANDIDATE_BRANCH:
        raise SyncError(f"refused: on {branch or 'a detached HEAD'}, not {CANDIDATE_BRANCH} (--branch names another)")
    if merging:
        raise SyncError("refused: a merge is already in progress (finish it, or `git merge --abort`)")
    lines = [line for line in porcelain if line.strip()]
    if not lines:
        return []
    drift = [line[3:] for line in lines
             if line[:2] in (" M", "M ", "MM") and line[3:].endswith(".xcstrings") and line[3:] != OVERLAY_CATALOG
             and upstream_owned(line[3:])]
    if len(drift) != len(lines):
        raise SyncError("refused: the tree is not clean:\n  " + "\n  ".join(lines))
    if not discard_drift:
        raise SyncError("refused: the only changes are String Catalog drift in upstream's catalogs (an Xcode-window "
                        "build rewrites them):\n  " + "\n  ".join(drift) + "\nRerun with --discard-catalog-drift to "
                        "restore exactly these files from HEAD and continue.")
    return drift


def moved_pins(resolved: dict, pins: dict) -> list[str]:
    found = {pin.get("identity"): pin for pin in resolved.get("pins", []) if isinstance(pin, dict)}
    moved = []
    for identity, (url, version, revision) in sorted(pins.items()):
        pin = found.get(identity)
        if pin is None:
            moved.append(f"{identity}: removed")
            continue
        state = pin.get("state", {})
        if (pin.get("location"), state.get("version"), state.get("revision")) != (url, version, revision):
            moved.append(f"{identity}: {version or '-'} {revision[:7]} -> {state.get('version') or '-'} "
                         f"{str(state.get('revision'))[:7]} ({pin.get('location')})")
    moved += [f"{identity}: added ({pin.get('location')})" for identity, pin in sorted(found.items()) if identity not in pins]
    return moved


def pins_of(resolved: dict) -> dict:
    """Package.resolved as PINS-shaped tuples, to compare one revision of it with another."""
    return {pin["identity"]: (pin.get("location"), pin.get("state", {}).get("version"), pin.get("state", {}).get("revision", ""))
            for pin in resolved.get("pins", []) if isinstance(pin, dict) and "identity" in pin}


def undo_commands(merging_tag: Optional[str], merged_tag: Optional[str]) -> list[str]:
    if merging_tag:
        return ["git merge --abort                                   # the merge is not committed yet"]
    if merged_tag:
        return [f"git reset --hard fallback/candidate-before-{merged_tag}   # after the merge was committed, "
                f"on {CANDIDATE_BRANCH} only"]
    return ["# nothing to undo: no merge in progress and HEAD is not a merge of an upstream tag"]


def conflict_hunks(text: str) -> int:
    return len(re.findall(r"^<<<<<<< ", text, re.M))


# ---------------------------------------------------------------------------------------------------
# Git

def git(root: Path, *args: str, check: bool = True) -> str:
    done = subprocess.run(["git", "-c", "core.fsmonitor=false", "-C", str(root), *args], capture_output=True, text=True)
    if check and done.returncode != 0:
        raise SyncError(f"git {' '.join(args)} failed: {done.stderr.strip() or done.stdout.strip()}")
    return done.stdout


def git_bytes(root: Path, *args: str) -> bytes:
    done = subprocess.run(["git", "-c", "core.fsmonitor=false", "-C", str(root), *args], capture_output=True)
    if done.returncode != 0:
        raise SyncError(f"git {' '.join(args)} failed: {done.stderr.decode(errors='replace').strip()}")
    return done.stdout


def git_ok(root: Path, *args: str) -> bool:
    return subprocess.run(["git", "-c", "core.fsmonitor=false", "-C", str(root), *args], capture_output=True).returncode == 0


def ref_value(root: Path, ref: str) -> str:
    """The object a ref names, or "" when the ref does not exist."""
    return git(root, "rev-parse", "-q", "--verify", ref, check=False).strip()


def load_module(root: Path, name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, root / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def names(root: Path, a: str, b: str) -> set[str]:
    return set(git(root, "diff", "--name-only", "--no-renames", a, b).splitlines())


def unmerged(root: Path) -> list[str]:
    return sorted(set(git(root, "diff", "--name-only", "--diff-filter=U").splitlines()))


def fetch(root: Path, source: str, destination: Optional[str]) -> str:
    """Fetch one ref. `--refmap=` (empty) keeps the configured refspecs out of it, so the only ref that can
    change is `destination`; with no destination, only FETCH_HEAD is written. Returns the fetched object."""
    spec = f"+{source}:{destination}" if destination else source
    git(root, "fetch", "--no-tags", "--refmap=", REMOTE, spec)
    return git(root, "rev-parse", destination or "FETCH_HEAD").strip()


def stock_links_in_new_tests(root: Path, a: str, b: str) -> list[str]:
    found, path = [], None
    for line in git(root, "diff", "-U0", "--no-renames", a, b).splitlines():
        if line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else None
        elif path and "Tests" in path and line.startswith("+") and "rivulet://" in line:
            found.append(f"{path}: {line[1:].strip()[:110]}")
    return found


# ---------------------------------------------------------------------------------------------------
# Commands

def run_sync(root: Path, tag: str, discard_drift: bool, today: str) -> int:
    if not TAG.fullmatch(tag):
        raise SyncError(f"refused: {tag!r} does not look like a release tag (v1.3.0-127)")
    report = root / REPORT_DIR / f"sync-{tag}.txt"
    if report.exists():
        report.unlink()   # an old report of the same name must not pass for this run's
    out: list[str] = []
    try:
        code = sync_steps(root, tag, discard_drift, today, out)
        print("\n".join(out))
        return code
    except BaseException as exc:
        out += ["", f"STOPPED: {exc}"]
        raise
    finally:
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text("\n".join(out) + "\n", encoding="utf-8")
        print(f"\nReport written to {report.relative_to(root)}", file=sys.stderr)


AFTER_MERGE = "PrivateBuild/SYNC.md, 'After the merge starts'"


def sync_steps(root: Path, tag: str, discard_drift: bool, today: str, out: list[str]) -> int:
    say = out.append
    verify = load_module(root, "private_verify_for_sync", "PrivateBuild/verify.py")
    footprint = load_module(root, "private_footprint_for_sync", "PrivateBuild/footprint.py")
    must_equal = footprint.MUST_EQUAL_UPSTREAM
    texts = {path: (root / path).read_text(encoding="utf-8") for path in BASELINE_FILES}
    old_hash, old_tag = verify_baseline(texts["PrivateBuild/verify.py"])

    # ---- Before the merge: everything that can be checked, with no ref and no file changed. ----
    branch = git(root, "symbolic-ref", "--short", "-q", "HEAD", check=False).strip() or None
    merging = git_ok(root, "rev-parse", "-q", "--verify", "MERGE_HEAD")
    porcelain = git(root, "status", "--porcelain=v1", "--untracked-files=normal").splitlines()
    drift = preflight(branch, merging, porcelain, discard_drift,
                      lambda path: git_ok(root, "cat-file", "-e", f"{old_hash}:{path}"))
    head = git(root, "rev-parse", "HEAD").strip()
    owned = [path for path in must_equal if not git_ok(root, "diff", "--quiet", old_hash, head, "--", path)]
    if owned:
        raise SyncError("refused before merging: the overlay now modifies " + ", ".join(owned) + ". Each is on "
                        "MUST_EQUAL_UPSTREAM (footprint.py): take it off the list, or restore upstream's text "
                        f"(git checkout {old_hash[:7]} -- <file>) and commit; then run again.")
    claude_ours = git_bytes(root, "show", f"{head}:{CLAUDE}")
    claude_old = git_bytes(root, "show", f"{old_hash}:{CLAUDE}")
    claude_block(claude_ours, claude_old)
    try:
        fetched = fetch(root, f"refs/tags/{tag}", None)
    except SyncError as exc:
        raise SyncError(f"refused: cannot fetch {tag} from {REMOTE}: {exc}") from None
    new_hash = git(root, "rev-parse", f"{fetched}^{{commit}}").strip()
    if git_ok(root, "merge-base", "--is-ancestor", new_hash, head):
        raise SyncError(f"refused: {tag} ({new_hash[:7]}) is already merged into {CANDIDATE_BRANCH}")
    base = git(root, "merge-base", head, new_hash).strip()
    ours, theirs = names(root, base, head), names(root, base, new_hash)
    touched = sorted(set(BASELINE_FILES) & theirs)
    if touched:
        raise SyncError(f"refused: upstream {tag} changes {', '.join(touched)}, which this script rewrites: merge by hand")
    try:
        label = newest_changelog_label(git(root, "show", f"{new_hash}:{WHATSNEW}"))
        new_texts = {
            "PrivateBuild/verify.py": bump_verify(texts["PrivateBuild/verify.py"], new_hash, tag),
            "PrivateBuild/UPSTREAM.json": bump_upstream_json(texts["PrivateBuild/UPSTREAM.json"], new_hash, label, today),
            NOTES: bump_notes(texts[NOTES], old_tag, old_hash, tag, new_hash, today),
        }
        claude_new = claude_md(claude_ours, claude_old, git_bytes(root, "show", f"{new_hash}:{CLAUDE}"))
    except SyncError as exc:
        raise SyncError(f"refused before merging, nothing changed: {exc}") from None
    fallback = f"fallback/candidate-before-{tag}"
    fallback_at = ref_value(root, f"refs/tags/{fallback}^{{commit}}")
    if fallback_at and fallback_at != head:
        raise SyncError(f"refused: the tag {fallback} already exists at {fallback_at[:7]}, not at HEAD ({head[:7]}). It may "
                        f"be the only undo point of an earlier sync of {tag}. Check that nothing needs it (`git log -1 "
                        f"{fallback}`, and that what it would restore is in HEAD), then remove it deliberately with "
                        f"`git tag -d {fallback}` and run again.")
    pinned_up = ref_value(root, f"refs/tags/upstream/{tag}")
    if pinned_up and pinned_up != fetched:
        raise SyncError(f"refused: upstream/{tag} here is {pinned_up[:7]} but upstream's {tag} is now {fetched[:7]} "
                        "(a moved tag?): look before merging")
    commits = git(root, "rev-list", "--count", f"{base}..{new_hash}").strip()

    # ---- The point of no return: config, drift, tags, merge. ----
    git(root, "config", "--local", "rerere.enabled", "true")
    if drift:
        git(root, "restore", "--source=HEAD", "--staged", "--worktree", "--", *drift)
        say(f"Discarded String Catalog drift (restored from HEAD): {', '.join(drift)}")
    if not fallback_at:   # the empty old value makes git refuse if the tag appeared meanwhile: never moved
        git(root, "update-ref", f"refs/tags/{fallback}", head, "")
    if not pinned_up:
        git(root, "update-ref", f"refs/tags/upstream/{tag}", fetched, "")
    before = footprint.measure(root, old_hash)
    target = f"upstream/{tag}"
    # The project-file resolver refuses diff3 hunks, so this one merge asks for plain markers
    # (a per-command setting; the repository's merge.conflictStyle is not changed).
    merge = subprocess.run(["git", "-C", str(root), "-c", "merge.conflictStyle=merge", "merge", "--no-ff", "--no-commit", target],
                           capture_output=True, text=True)
    if not git_ok(root, "rev-parse", "-q", "--verify", "MERGE_HEAD"):
        raise SyncError(f"git merge did not start: {merge.stderr.strip() or merge.stdout.strip()} (HEAD is unchanged; "
                        f"{fallback} and {target} stay, and a rerun reuses them)")
    stage = [0]
    ways_out = ("\nThe merge has started and stopped at step {n} of " + AFTER_MERGE + ". Two ways out: fix that and "
                "continue by hand from step {n} there, or undo everything with `git merge --abort`.")
    try:
        return after_merge(root, tag, target, head, base, new_hash, old_hash, old_tag, label, today, fallback,
                           "reused" if fallback_at else "created", commits, ours, theirs, merge, new_texts, texts, claude_new,
                           must_equal, verify, footprint, before, out, lambda n: stage.__setitem__(0, n))
    except SyncError as exc:
        raise SyncError(f"{exc}" + ways_out.format(n=stage[0])) from None
    except Exception as exc:   # an I/O error mid-merge gets the same two ways out
        raise SyncError(f"{type(exc).__name__}: {exc}" + ways_out.format(n=stage[0])) from None


def after_merge(root, tag, target, head, base, new_hash, old_hash, old_tag, label, today, fallback, fallback_state,
                commits, ours, theirs, merge, new_texts, old_texts, claude_new, must_equal, verify, footprint, before,
                out, at) -> int:
    say = out.append
    conflicted_by_git = unmerged(root)
    rerere = [line for line in (merge.stdout + merge.stderr).splitlines() if "using previous resolution" in line]
    say(f"Merged {target} ({new_hash[:7]}, {commits} upstream commits, {len(theirs)} files) into {head[:7]} "
        f"without committing; git left {len(conflicted_by_git)} conflicted files.")
    say(f"Fallback: {fallback} -> {head[:7]} ({fallback_state})")
    blockers: list[str] = []

    # Step 1: the project file, then the package traits.
    at(1)
    project = root / PBXPROJ
    if PBXPROJ not in theirs and PBXPROJ not in conflicted_by_git:
        say("Project file: upstream did not change it")
    else:
        resolver = subprocess.run([sys.executable, str(root / "PrivateBuild/resolve-pbxproj-conflicts.py"), str(project)],
                                  capture_output=True, text=True)
        say(f"Project file: {(resolver.stdout + resolver.stderr).strip()}")
        project_clean = conflict_hunks(project.read_text(encoding="utf-8")) == 0
        if resolver.returncode != 0 or not project_clean:
            blockers.append(f"{PBXPROJ}: the resolver refused a conflict shape; resolve it by hand, then run "
                            "`plutil -lint` and `python3 Scripts/restore-package-traits.py`")
        else:
            lint = subprocess.run(["plutil", "-lint", str(project)], capture_output=True, text=True)
            say(f"plutil -lint: {(lint.stdout + lint.stderr).strip()}")
            traits = subprocess.run([sys.executable, str(root / "Scripts/restore-package-traits.py")], cwd=root,
                                    capture_output=True, text=True)
            say("Package traits: " + " | ".join((traits.stdout + traits.stderr).strip().splitlines()))
            if lint.returncode != 0 or traits.returncode != 0:
                blockers.append(f"{PBXPROJ}: plutil or the traits restore failed (above)")
            else:
                git(root, "add", "--", PBXPROJ)

    # Step 2: files the overlay must not own. An assertion only: nothing is overwritten.
    at(2)
    differ = [path for path in must_equal
              if path in conflicted_by_git or not git_ok(root, "diff", "--quiet", new_hash, "--", path)]
    if differ:
        say(f"MUST_EQUAL_UPSTREAM files that differ from {target}, for a person (nothing was overwritten):")
        say("\n".join(f"  {path}: read `git diff {target} -- {path}`" for path in differ))
        blockers.append(f"{len(differ)} MUST_EQUAL_UPSTREAM files differ from upstream (above)")
    else:
        say(f"Files the overlay must not own ({len(must_equal)}): all equal upstream's bytes")

    # Step 3: upstream's CLAUDE.md under the private import block (checked before the merge).
    at(3)
    if claude_new != (root / CLAUDE).read_bytes() or CLAUDE in conflicted_by_git:
        (root / CLAUDE).write_bytes(claude_new)
        say(f"{CLAUDE}: upstream's text under the private import block"
            + (" (it conflicted)" if CLAUDE in conflicted_by_git else ""))
    git(root, "add", "--", CLAUDE)

    # Step 4: the new baseline, computed before the merge, written together.
    at(4)
    try:
        for path, text in new_texts.items():
            (root / path).write_text(text, encoding="utf-8")
        git(root, "add", "--", *new_texts)
    except BaseException:
        # `git merge --abort` keeps unstaged edits, so a half-written baseline would outlive the abort:
        # put HEAD's texts back first (best effort), then stop.
        for path, text in old_texts.items():
            try:
                (root / path).write_text(text, encoding="utf-8")
            except OSError:
                pass
        raise
    say(f"Baseline: {old_tag} ({old_hash[:7]}) -> {tag} ({new_hash[:7]}), changelog label {label!r}, dated {today}, "
        f"in verify.py, UPSTREAM.json and {NOTES}")

    # Step 5: pins.
    at(5)
    try:
        moved = moved_pins(json.loads((root / verify.LOCK).read_text(encoding="utf-8")), verify.PINS)
    except (OSError, ValueError) as exc:
        moved = [f"cannot read {verify.LOCK}: {exc}"]
    if moved:
        say("Pins MOVED:\n  " + "\n  ".join(moved) + "\n" + PIN_RULE)
        blockers.append("a dependency pin moved (above)")
    else:
        say("Pins: nothing moved (Package.resolved equals PINS in verify.py)")

    # Step 6: the report.
    at(6)
    left = unmerged(root)
    hook_dark: dict[str, list[str]] = {}
    for path, _, _, dark in verify.HOOKS:
        hook_dark.setdefault(path, []).append(dark)
    say("")
    if left:
        say(f"CONFLICTED, for a person ({len(left)}):")
        for path in left:
            text = (root / path).read_text(encoding="utf-8", errors="replace") if (root / path).exists() else ""
            hunks = conflict_hunks(text)
            state = f"{hunks} hunks" if hunks else "no markers left (rerere replayed a recorded resolution?): check it, then git add"
            say(f"  {path}: {state}")
            for dark in hook_dark.get(path, []):
                say(f"      keep the private hook, or {dark}")
    else:
        say("CONFLICTED: none")
    if rerere:
        say("rerere: " + "; ".join(rerere) + " (a wrong replay is forgotten with `git rerere forget <path>`)")
    silent = sorted((ours & theirs) - set(conflicted_by_git) - {CLAUDE, PBXPROJ})
    say(f"Changed on both sides and merged with no conflict ({len(silent)}): read each diff, it must be the overlay and nothing else")
    for path in silent:
        say(f"  git diff {target} -- {path}" + ("   (has private hooks)" if path in hook_dark else ""))
    links = stock_links_in_new_tests(root, base, new_hash)
    say(f"New upstream test lines spelling rivulet:// ({len(links)}): write \\(RivuletLink.scheme):// unless the test asserts the stock scheme is refused")
    for line in links:
        say(f"  {line}")
    pinned = sorted(theirs & set(verify.REVIEWED_SOURCES))
    say("sha256-pinned files upstream changed: " + (", ".join(pinned) + " (read the merged file; SYNC.md, 'A pinned file changed')" if pinned else "none"))
    check = subprocess.run([sys.executable, str(root / "PrivateBuild/verify.py")], capture_output=True, text=True)
    say(f"verify.py now: {'PASS' if check.returncode == 0 else 'FAIL'}")
    for line in (check.stdout + check.stderr).strip().splitlines():
        if line.startswith("FAIL"):
            say(f"  {line}")
    after = footprint.measure(root, new_hash)
    for name, files in (("before", before), ("after", after)):
        if files is None:
            say(f"Footprint {name}: not checked")
        else:
            count, added, removed = footprint.totals(files)
            markers = " (conflict markers included)" if name == "after" and left else ""
            say(f"Footprint {name}: {count} upstream files, +{added}/-{removed}{markers}")
    if before is not None and after is not None:
        for path in sorted(set(before) | set(after)):
            if before.get(path) != after.get(path):
                say(f"  {path}: {before.get(path)} -> {after.get(path)}")

    blockers += [f"{len(left)} conflicted files"] if left else []
    say("")
    say("MECHANICAL PART NOT DONE: " + "; ".join(blockers) if blockers else "Mechanical part done.")
    say("Next:")
    say("  1. Resolve what is listed (SYNC.md, 'Judgement'); `git add` each file; read every `git diff` listed above")
    say("  2. python3 PrivateBuild/check.py --all")
    say(f"  3. git commit   (message: Merge upstream {tag})")
    say("  4. Play a few titles on an Apple TV before trusting the merged build")
    say(f"  Undo: python3 PrivateBuild/sync.py --undo   (prints: git merge --abort, or git reset --hard {fallback})")
    return 1 if blockers else 0


def run_forecast(root: Path, ref: str) -> int:
    if not REF.fullmatch(ref) or ".." in ref:
        raise SyncError(f"refused: {ref!r} is not a plain tag or branch name")
    verify = load_module(root, "private_verify_for_forecast", "PrivateBuild/verify.py")
    code, lines = forecast(root, ref, verify.BASELINE, {hook[0] for hook in verify.HOOKS}, set(verify.REVIEWED_SOURCES))
    print("\n".join(lines))
    return code


def forecast(root: Path, ref: str, baseline: str, hook_files: set, pinned_files: set) -> tuple[int, list[str]]:
    source = f"refs/tags/{ref}" if TAG.fullmatch(ref) else f"refs/heads/{ref}"
    commit = git(root, "rev-parse", fetch(root, source, f"refs/upstream-forecast/{ref}") + "^{commit}").strip()
    head = git(root, "rev-parse", "HEAD").strip()
    lines = [f"Forecast for upstream {ref} ({commit[:7]}) against {head[:7]}"]
    if git_ok(root, "merge-base", "--is-ancestor", commit, head):
        return 0, lines + ["Nothing new: it is already merged."]
    since = baseline if git_ok(root, "cat-file", "-e", f"{baseline}^{{commit}}") else git(root, "merge-base", head, commit).strip()
    files = names(root, since, commit)
    lines.append(f"New since {since[:7]}: {git(root, 'rev-list', '--count', f'{since}..{commit}').strip()} commits, {len(files)} files")
    tree = subprocess.run(["git", "-C", str(root), "merge-tree", "--write-tree", "--name-only", "--no-messages", head, commit],
                          capture_output=True, text=True)
    if tree.returncode not in (0, 1):
        raise SyncError(f"git merge-tree failed: {tree.stderr.strip()}")
    conflicted = [line for line in tree.stdout.splitlines()[1:] if line.strip()]
    lines.append(f"Would conflict ({len(conflicted)}):" if conflicted else "Would merge with no conflict")
    lines += [f"  {path}" + ("   (has private hooks)" if path in hook_files else "") for path in conflicted]
    lock = "Rivulet.xcodeproj/project.xcworkspace/xcshareddata/swiftpm/Package.resolved"
    try:   # upstream's own lock at the baseline against the release: the overlay's extra pin is not upstream's
        moved = moved_pins(json.loads(git(root, "show", f"{commit}:{lock}")), pins_of(json.loads(git(root, "show", f"{since}:{lock}"))))
    except (SyncError, ValueError) as exc:
        moved = [f"cannot read Package.resolved: {exc}"]
    lines.append("Pins: " + ("; ".join(moved) + " (upstream moved them; SYNC.md, 'A pin moved')" if moved else "upstream moves none"))
    touched = sorted(files & hook_files)
    lines.append("Touches files with private hooks: " + (", ".join(touched) if touched else "none"))
    lines.append("Touches sha256-pinned files: " + (", ".join(sorted(files & pinned_files)) or "none"))
    return (1 if conflicted else 0), lines


def run_undo(root: Path, tag: Optional[str]) -> int:
    merging_tag = merged_tag = None
    if git_ok(root, "rev-parse", "-q", "--verify", "MERGE_HEAD"):
        merging_tag = tag or next((name[len("upstream/"):] for name in git(root, "tag", "--points-at", "MERGE_HEAD").split()
                                   if name.startswith("upstream/")), "<tag>")
    elif git_ok(root, "rev-parse", "-q", "--verify", "HEAD^2"):
        merged_tag = tag or next((name[len("upstream/"):] for name in git(root, "tag", "--points-at", "HEAD^2").split()
                                  if name.startswith("upstream/")), None)
    elif tag:
        merged_tag = tag
    lines = undo_commands(merging_tag, merged_tag)
    if merged_tag:
        fallback = f"fallback/candidate-before-{merged_tag}"
        if not ref_value(root, f"refs/tags/{fallback}"):
            print(f"# no fallback tag {fallback}: undo by hand")
            return 1
        if not git_ok(root, "merge-base", "--is-ancestor", fallback, "HEAD"):
            print(f"# {fallback} is not in HEAD's history: a reset to it would not undo the merge. Look before doing anything.")
            return 1
        later = git(root, "log", "--first-parent", "--format=%h %s", f"{fallback}..HEAD").splitlines()
        if len(later) > 1:   # the oldest is the merge itself
            lines += [f"# WARNING: reset --hard also drops {len(later) - 1} commit(s) made after the merge:"]
            lines += [f"#   {line}" for line in later[:-1]]
            lines += [f"# To keep them, revert the merge instead: git revert -m 1 {later[-1].split()[0]}"]
        elif not later:
            lines = [f"# nothing to undo: HEAD is {fallback}"]
    print("\n".join(lines))
    return 0


def main(argv: Optional[list[str]] = None, root: Path = ROOT) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("tag", nargs="?", help="the upstream release tag (e.g. v1.3.0-127)")
    parser.add_argument("--discard-catalog-drift", action="store_true",
                        help="restore modified upstream .xcstrings files from HEAD when they are the only change")
    parser.add_argument("--forecast", action="store_true", help="what a merge of TAG (upstream main without one) would do; changes no ref but refs/upstream-forecast/*")
    parser.add_argument("--undo", action="store_true", help="print the commands that undo a sync of TAG (or of the merge in progress)")
    parser.add_argument("--branch", default="main", help="the branch the fork lives on (default: main)")
    args = parser.parse_args(argv)
    global CANDIDATE_BRANCH
    CANDIDATE_BRANCH = args.branch
    if args.forecast and args.undo:
        parser.error("--forecast and --undo are separate commands")
    if args.discard_catalog_drift and (args.forecast or args.undo):
        parser.error("--discard-catalog-drift goes with a sync, not --forecast or --undo")
    try:
        if args.forecast:
            return run_forecast(root, args.tag or "main")
        if args.undo:
            return run_undo(root, args.tag)
        if not args.tag:
            parser.error("give a tag, --forecast or --undo")
        return run_sync(root, args.tag, args.discard_catalog_drift, datetime.date.today().isoformat())
    except SyncError as exc:
        print(str(exc), file=sys.stderr)
        if git_ok(root, "rev-parse", "-q", "--verify", "MERGE_HEAD"):
            print("A merge is in progress; `python3 PrivateBuild/sync.py --undo` prints how to undo it.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
