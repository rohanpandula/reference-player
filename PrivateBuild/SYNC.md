# Following upstream

"Private" below (private lines, the private import block, private files) means this fork's overlay,
after the `PrivateBuild/` folder's historical name.

Reference Player is upstream Rivulet plus this fork's overlay, so it moves forward by merging each upstream
release tag into the fork's branch (`main`; `sync.py --branch <name>` for another). Upstream's
history stays underneath. Keep the `upstream` remote fetch-only, with a push URL that cannot resolve
(`git remote set-url --push upstream no-push://upstream-is-read-only`).

## Every release

On the fork's branch:

    python3 PrivateBuild/sync.py v1.3.0-127 --forecast   # optional: what would conflict; changes nothing
    python3 PrivateBuild/sync.py v1.3.0-127              # merge, the mechanical part, a report
    # resolve what the report lists (below), `git add` each file
    python3 PrivateBuild/check.py --all
    git commit                                           # "Merge upstream v1.3.0-127"

Then play a few titles on an Apple TV (Dolby Vision, an object-audio soundtrack, a seek) before trusting
the merged build. Commit subjects are plain; there is no suffix convention (the overlay's old
"(private build)" suffix is retired).

**Before the merge** `sync.py <tag>` checks everything it can and changes nothing (no ref, no file,
no config) when it refuses: another branch or a detached HEAD, a merge in progress, a dirty tree; a
file on `MUST_EQUAL_UPSTREAM` that is no longer the recorded baseline's text ("the overlay now
modifies <file>": take it off the list in `footprint.py`, or restore upstream's text and commit);
`CLAUDE.md` that is not exactly the private import block over the baseline's text (or has a carriage
return); a tag upstream does not have (fetched into `FETCH_HEAD` only), or one already merged; an
upstream change to `verify.py`, `UPSTREAM.json` or `CLAUDE-private.md`; an edit to those three that does
not find its text exactly once; a changelog key it cannot read; and a `fallback/candidate-before-<tag>`
that exists somewhere other than HEAD. That last one may be the only undo point of an earlier sync:
check that nothing needs it, then remove it yourself with `git tag -d` and run again. Building from
the Xcode window rewrites upstream's String Catalogs; when that is the only change it says so, and
`--discard-catalog-drift` restores exactly those files from HEAD. Only catalogs upstream's baseline
has count: a modified private catalog (`Private.xcstrings`, anywhere) keeps the tree dirty.

Then, and only then, it turns on `rerere` for this repository (a conflict resolved once is replayed
next time), restores the catalog drift, creates `fallback/candidate-before-<tag>` at HEAD and
`upstream/<tag>` (or reuses them when they already point there; it never moves a tag), and merges
without committing. `rerere.enabled` is written to the repository's shared config, which the
worktrees of the same repository read too.

### After the merge starts

1. The project file, when upstream changed it: `resolve-pbxproj-conflicts.py` (upstream's
   `MARKETING_VERSION`, the bundle identifier in `identity.json`; any other shape is refused and left to you),
   `plutil -lint`, `Scripts/restore-package-traits.py` (the five engine traits), `git add`.
2. `MUST_EQUAL_UPSTREAM`: any listed file that differs from `upstream/<tag>` is reported for a
   person. Nothing is overwritten. (When the overlay leaves a file alone, git takes upstream's bytes
   by itself.)
3. `CLAUDE.md`: upstream's new text under the private import block, written as bytes; `git add`.
4. The new baseline in `verify.py`, `UPSTREAM.json` and the baseline line of `CLAUDE-private.md`, computed before the merge and
   written together; `git add`.
5. `Package.resolved` against `PINS`.
6. The report (printed, and kept in `PrivateBuild/.build/sync/sync-<tag>.txt`; an old report of that
   name is deleted when a run starts, and a run that stops writes its error there): the files still
   conflicted, with hunk counts and the private hooks each one carries; the files both sides changed
   that merged with no conflict, each with the `git diff upstream/<tag> -- <file>` to read; new
   upstream test lines that spell `rivulet://`; any upstream change to a sha256-pinned file; what
   `verify.py` says (it fails on any conflict marker left in Swift sources, the project file, the
   lock or a catalog); and the footprint before and after.

Exit 0 means the mechanical part is done and nothing is conflicted, 1 that something is left for a
person, 2 that it refused or stopped.

**If it stops after the merge started**, its message names the step above where it stopped. There
are exactly two ways out: fix what it names and do the remaining steps of this list by hand from
that step, or `git merge --abort`, which undoes the merge and everything the script staged (the two
tags stay where they are, and a rerun reuses them). Do not rerun `sync.py` during the merge: it
refuses.

**rerere replays without judging.** If a replayed resolution is wrong, forget it with
`git rerere forget <path>` (during the merge, on the conflicted file), then resolve it again.

Undo: `python3 PrivateBuild/sync.py --undo` prints the command for the state the tree is in,
`git merge --abort` before the merge is committed, `git reset --hard fallback/candidate-before-<tag>`
after (give the tag, `--undo <tag>`, once commits sit on top of the merge). It runs neither. When
commits were made after the merge, it lists them, says that the reset would drop them, and prints
`git revert -m 1 <merge>` as the way to keep them.

`sync.py <tag> --forecast` (or `--forecast` alone, for upstream `main`) fetches into
`refs/upstream-forecast/<ref>` and changes no other ref, then runs `git merge-tree`: exit 0 clean or
nothing new, 1 would conflict, 2 error. It also says how many commits and files are new since the
baseline, whether upstream moved a pin, and whether the release touches files that carry hooks or a
pinned digest.

## Judgement: what the scripts leave to a person

**Swift conflicts.** Keep both: upstream's change and the private lines. What remains private inside
upstream files is hook lines, each listed in `HOOKS` in `verify.py` with what goes dark without it;
the report repeats them under each conflicted file. A careless resolution (taking upstream's whole
file) compiles and drops every hook in it: `verify.py` names each one. If upstream renamed a type
the overlay uses, take upstream's name everywhere (`DolbyVisionEnhancementLayer` became
`DolbyVisionLayerType`). If upstream deleted or replaced a function the overlay extended, move the
private behaviour into upstream's replacement rather than reviving the old function, and check that
no call inside the conflicted block was lost with it.

**Silent merges.** For each file in the report's "changed on both sides" list, the `git diff` against
upstream must read as the private overlay and nothing else. Git merges the same thing added twice
with no conflict (in 1.3.0, `dolbyVisionEnhancementLayer` was declared by both).

**Stock scheme in new tests.** A new upstream test that spells `rivulet://` fails here by design,
since this fork answers only its own: write `\(RivuletLink.scheme)://` in it, except where
a test asserts the stock scheme is refused. That edit is a new upstream file modified, so
`footprint.py` fails until you record it with `python3 PrivateBuild/footprint.py --update` (the one
case where growth is expected; say so in the commit).

**The product name in new tests.** Servers are told `PlexAPI.productName` ("Reference Player"). An upstream test
that spells `Client="Rivulet"` fails here by design: write `PlexAPI.productName` in it (three upstream
test lines already do). A `Rivulet/` user agent becomes `PrivateBuildPolicy.userAgentProduct`
("ReferencePlayer"), because a User-Agent token cannot hold the name's space (one upstream test line
does). Then record the footprint as above.

**Settings text.** Upstream's `SettingsDescriptorLengthTests` fails on any private Settings
description longer than the panel's three lines: shorten the private text, never the test.

**A pin moved.** `sync.py` stops short of "done" and edits no pin. Read the dependency's release
notes, then update `PINS` in `verify.py` and the matching section of `UPSTREAM.json` to the revisions
in `Package.resolved`. If an engine release drops `DualLayerDolbyVision` or `TrueHDObjectAudio`, do
not take that pin: the fork then stays on the last engine that has them, and that is where it
stops following upstream's engine. `verify.py --resolved-checkouts` (run by `check.py`) fails when
the new engine no longer logs a line the app parses (`ENGINE_LOG_LITERALS`).

**A pinned file changed.** If upstream changed `RivuletCore/Security/URLSession+Attested.swift`,
`verify.py` fails on its digest. Read the merged function: the statement that hands the request to
`DirectMetadata` must still come first, ahead of the guard, with nothing else before it. Then put the
digest `verify.py` prints into `REVIEWED_SOURCES`.

**The footprint grew.** `footprint.py` fails when a file's added or removed lines inside upstream
files exceed `footprint.json`, when a new upstream file is modified, or when a file on
`MUST_EQUAL_UPSTREAM` appears in it at all. `--update` is not a step of every merge: it is a
deliberate act after reading the numbers it printed, with the reason in the commit. A footprint that
only shrank passes and says so; recording the smaller numbers is then a choice.

**Files the overlay must not own.** `MUST_EQUAL_UPSTREAM` in `footprint.py` (the one definition;
`sync.py` reads it) lists `README.md`, `Rivulet/Localizable.xcstrings` and four upstream tests that
once carried private lines and were restored on purpose. It is an assertion, never an action: the
sync refuses to start while one differs from the baseline, reports one that differs from the new
release, and overwrites nothing; `footprint.py` fails while one is modified. A private correction to
upstream's `CLAUDE.md` or `README.md` goes into `CLAUDE-private.md`. If a file on that list ever
needs a private line, take it off the list in the same commit.

## Where private code lives

Private code lives in private files, so a merge meets as few private lines as possible. The private
Settings pages, rows and descriptions are in `Rivulet/Views/Settings/UIKit/PrivateSettingsContent.swift`;
upstream's three Settings files keep only the four enum cases and one-line calls into it. Private
tests are in their own files (`FormatTagBadgeTests`, `DualLayerPlaybackTests`,
`PrivateDeepLinkSchemeTests`, `PrivateSettingsRowsTests` and the private files beside them), and the
two TMDB classes the private policy refuses (`TMDBDiscoverServiceTests`, `TMDBLogoCacheTests`, 20
tests) are skipped by `<SkippedTests>` in `Reference Player.xcscheme`, not in their sources. Only that
scheme skips them: under upstream's `Rivulet` scheme they still run, and fail, because the policy
refuses their requests. The playback code is in `UniversalPlayerViewModel+HomeAssistant.swift`,
`Views/Player/UIKit/DolbyVisionRouteLine.swift`, the `// MARK: - Reference Player` extension at the end
of `AetherPlayer.swift` (the one file allowed to name an engine type), `RivuletCore/Integrations/FormatTagBadges.swift`
and `Rivulet/Config/PrivateLaunch.swift`. Private user-visible text is in `Rivulet/Private.xcstrings`
(table `Private`), written `String(localized: "…", table: "Private")` and handed to a funnel's
`verbatim:` path; a literal given to a `String.LocalizationValue` funnel, or to a helper taking one,
is extracted into upstream's `Localizable`. `check-private-strings.py` (run by `check.py` after its
builds) confirms where every private literal is headed.

New private code must not go inside an upstream file beyond a hook line, and `footprint.py` holds
that line. `HOOKS` in `verify.py` covers private lines inside upstream files whose removal would
still compile: a merge can drop those with no conflict and no build error. A line whose removal fails
the build is left to the compiler unless dropping it could be "fixed" with an empty answer (the
forwarding `case` lines are listed for that reason). A private line that only a test protects (it
compiles without the line and a Swift test fails) is not protected by `verify.py`: name that test
beside the line in a comment, or add the line to `HOOKS`.

To add a private Settings page: add its case to the one private `case` line of `SettingsPage` in
`SettingsModels.swift`, and to the three forwarding lines (`case .heightSpeakersPicker, … : return
PrivateSettings.…`) in `SettingsModels.title`, `SettingsContent.allRows(for:)` and
`SettingsDescriptorStore.pageInfo(for:)`; `PRIVATE_PAGES` in `verify.py` must list it too. Then add
it to the three switches in `PrivateSettings` (`title(for:)`, `pageInfo(for:)`, `rows(for:)`). Those
end in `default:`, so a page missing from them compiles and opens untitled and empty: nothing but
the test catches it. Add the page's rows and descriptions to `PrivateSettingsRowsTests`.

## History

- `v1.3.0-125`, rehearsed 2026-11-09 by hand: 33 upstream commits, 180 files; 14 conflict hunks in 3
  files, all of the project-file and keep-both shapes, plus one property both sides had added.
- `v1.3.0-126`, merged 2026-11-11 by hand: 44 upstream commits, 135 files; 5 conflict hunks in 5
  files. The project file merged clean (no version bump). Upstream replaced `calmBadges` with
  `MediaSource.detailBadges` and deleted `reportProviderState` beside the Home Assistant section; the
  `addVersionsButton(detail)` call in that block was kept by hand. `Localizable.xcstrings` conflicted
  on five private keys, which then moved to `Private.xcstrings`.
- 2026-11-11: `sync.py`, `check.py` and `footprint.py` took over the mechanical steps; rehearsed on
  synthetic releases. The same day an adversarial review broke them in a clone; the
  fixes (nothing changes before the merge is certain, the must-equal list only asserts, markers fail
  `verify.py`) followed. No real upstream release has been merged with them yet.

## Private engine patch

The app still records upstream's exact engine pin. The Reference Player workspace overrides it with
that revision plus `PrivateBuild/engine-patches/0001-fel-rpu-mapping.patch`; see the
[engine patch procedure](engine-patches/README.md). A Rivulet sync does not silently rebase this patch.
After reviewing an engine update, reapply the patch against its new pin and run the engine tests and
`check.py --all`. Keep the old checkout until the new one is verified.
