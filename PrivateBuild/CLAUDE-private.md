# Reference Player: notes for Claude and other agents

Reference Player is a fork of Rivulet. `CLAUDE.md` is upstream's file, kept byte for byte apart from the
lines that import this one, so an upstream merge never conflicts there. Everything the fork needs to
say lives here. Where this file and `CLAUDE.md` (or `README.md`) disagree, this file is right for this
fork. The folder and symbol names (`PrivateBuild/`, `PrivateSettings`, `Private.xcstrings`) are the
overlay's historical names; they mean "this fork's", not "secret".

This tree is upstream Rivulet `v1.3.0-126` (`eabb7cf7833e6d1bb92d6fad116cb38a47305a0a`), merged 2026-11-11, plus this fork's overlay.

## What Reference means for changes

The name states the aim: a reference player plays what is in the file, as intact as Apple's platform
allows. Working rules:

1. Prefer the playback path that preserves the source. A change that drops, re-encodes, down-mixes,
   tone-maps or converts where the platform does not force it needs a stated reason.
2. When the platform forces a conversion or a fallback, surface it truthfully (badge, Details panel,
   report) and never label output as more than it is. A format name on screen comes from evidence
   (the file, the engine's own log), not from a guess.
3. Defaults for strangers stay conservative: a feature that renders or assumes hardware is off until
   the user sets it, or is requested only when the device reports the hardware (Dolby Vision FEL is on
   by default but asked for only on a Dolby Vision display). A fidelity feature fails back to
   upstream's behaviour, never to something worse.
4. No fidelity claim in docs or UI without a measurement on a device. "Not yet checked on hardware"
   stays in `.github/README.md` until it is no longer true.
5. When upstream and this principle disagree, keep upstream's behaviour reachable and put the fork's
   behaviour behind a setting or a hook listed in `HOOKS` (`PrivateBuild/verify.py`).

## Scope

- This is the existing Rivulet app with a small overlay, not a new player. Reuse upstream
  `AetherPlayer`, the existing settings, renderer, demuxer and Plex/Jellyfin/Emby clients. Do not add
  a second engine or copy codec code into the app.
- Keep the reviewed Rivulet/engine/decoder combination in `PrivateBuild/UPSTREAM.json` until a
  candidate has been explicitly reviewed. AetherEngine 7.57 changed audio semantics without renaming
  `TrueHDObjectAudio`; a successful build is not enough.
- Preserve `Package.resolved`; do not solve dependency problems by deleting it, silently upgrading
  packages, or using unpinned `main`.
- `PrivateBuild/verify.py` is a source/configuration check, NOT a Dolby fidelity, security, Xcode
  compilation, or hardware certification. No claim of bitstream passthrough or certified Dolby
  rendering: PCM delivery can be lossless while rendering differs.
- Never borrow upstream's signing identities, App Groups, App Attest keys, Worker credentials or
  telemetry destinations, and do not weaken an upstream access control. Upstream's hosted services
  (metadata Workers, trivia, scoreboard, diagnostics upload) stay refused (`PrivateBuildPolicy`).
- The repository carries placeholder identifiers (`com.example.referenceplayer`, no team). A builder runs
  `PrivateBuild/configure.py` with their own; `configure.py --placeholder` puts the placeholders back
  before a change is committed. Never commit a real team ID or bundle prefix.
- Nothing the app contacts is built in beyond the user's own media servers: the format index URL and
  the Home Assistant address are empty until a user sets them, and Format Tags and Home Assistant
  reporting start off.
- Review every added or changed block adversarially. Do not turn failures into passing tests by
  deleting assertions. Keep the overlay small and commented; prefer code or a focused test over a new
  abstraction.

## Verify

The upstream test command under "Build & Run" in `CLAUDE.md` gives no usable result in this fork:
without ad hoc signing the App Group is inaccessible, and about 50 player tests and every Keychain
write fail. Run this before calling a change done:

```bash
python3 PrivateBuild/check.py --all     # every check, one at a time, with a result table
python3 PrivateBuild/check.py --quick   # only the checks that need no xcodebuild (seconds)
```

It runs `verify.py` (also against the SwiftPM checkout), the Python tests, `footprint.py`, the whole
tvOS suite signed ad hoc, `check-private-strings.py`, the unsigned Release build for a real Apple TV,
and with `--all` the iOS and Mac compile checks. `check.py` is the one place the xcodebuild arguments
live: `python3 PrivateBuild/check.py --print-commands` prints them, and adding
`-only-testing:RivuletTests/<Class>` to the printed test command runs one class. The Xcode scheme is
`Reference Player`. `footprint.py` is the ratchet on fork lines inside upstream files: it fails when a new
upstream file is modified, a file adds or removes more lines than `PrivateBuild/footprint.json`
records, or a file on its `MUST_EQUAL_UPSTREAM` list is modified; move the code into a fork file, or
record a deliberate change with `--update` after reading the numbers. Following an upstream release is
`PrivateBuild/sync.py` (`PrivateBuild/SYNC.md`). Not checked by these scripts: SwiftLint, and anything
on an Apple TV. Playback features need checking on real hardware; the simulator says nothing about
HDMI channels, picture or performance.

The fork's user-visible text lives in `Rivulet/Private.xcstrings` (table `Private`), written as
`String(localized: "…", table: "Private")` and passed through a funnel's `verbatim:` path. Never add
a fork string to upstream's `Localizable.xcstrings`; `PrivateBuild/SYNC.md` says why and how.

## Where this fork differs from the upstream text in CLAUDE.md

- **Name.** The apps are called Reference Player (display name, `PlexAPI.productName`; user agents
  send the token `ReferencePlayer`, `PrivateBuildPolicy.userAgentProduct`, since a token cannot hold
  a space) and answer the `referenceplayer://` link scheme. Upstream's `rivulet://` examples in
  `CLAUDE.md` read `referenceplayer://` here (`RivuletLink.scheme`). Type names, the changelog and
  the artwork stay upstream's.
- **Engine traits** (Quick Reference, "AetherEngine"). Upstream sets one optional trait,
  `DolbyVisionFelCurves`, and leaves the other four off. This fork sets five in `project.pbxproj`:
  `DualLayerDolbyVision` (P7 FEL composer), `DolbyVisionFelCurves` (P7 FEL curve fold on the
  native route, the fallback when the composer route is not taken), `DolbyVisionStillReshaping`
  (P5 stills), `TrueHDObjectAudio` and `DTSXObjectAudio` (heights rendered from the lossless bed,
  steered by the stream's objects; off until Settings → Playback → Height Speakers names a layout).
  A bump must keep all five: `Scripts/restore-package-traits.py` puts them back after
  `use-profiles`, and `verify.py` fails without them.
- **Routing policy (VOD), item 2.** DV P7 FEL composes both layers on a DV panel when the
  dual-layer route is taken; otherwise P7 becomes 8.1, FEL with its enhancement layer folded into
  the curves.
- **HDR / DV.** P7 FEL on a DV panel takes the engine's dual-layer route (#345): `AetherPlayer`
  sets `preferDualLayerDolbyVision` from `AetherEngine.displayCapabilities.supportsDolbyVision`
  (and the Dolby Vision FEL setting, on by default), because the engine does not check the panel
  itself. That route is the software host: height channels still render there (Height Speakers), but
  it has no AirPlay picture, and the engine refuses it over its real-time budget (UHD 30 fps plus a
  1080p EL). P7 MEL, and FEL that does not take the dual-layer route, is converted live to
  single-layer 8.1 (libdovi) on a DV panel; for FEL the `DolbyVisionFelCurves` trait folds the EL's
  luma residual into each scene's luma curve. Upstream's sentence that the `DualLayerDolbyVision`
  composer "stays off" does not hold here. Both FEL routes and P5 stills exist only because of
  package traits, so a bump that drops them silently turns them off. Upstream's note that P5 has
  no engine stills does not hold either: `DolbyVisionStillReshaping` is on.
- **README.md, HDR.** Profile 7 FEL plays with both layers composed on a Dolby Vision TV.
  Profile 7 MEL, and FEL titles too heavy to compose in real time, play as 8.1, a FEL title
  keeping most of its enhancement layer's picture. Without a Dolby Vision TV, Profile 7 plays its
  HDR10 base layer.
- **README.md, Audio.** TrueHD and DTS-HD MA stay lossless, but not only "at up to 7.1": with
  Height Speakers set (Off by default), object soundtracks render their heights into that layout.
- **Discover and scores.** Upstream's Workers are refused; Discover, recommendations, biographies
  and Metacritic/Letterboxd scores come from the user's own TMDB and MDBList keys
  (`RivuletCore/Integrations/DirectMetadata.swift`). In-player trivia, the Live TV scoreboard and
  diagnostics upload stay off.
