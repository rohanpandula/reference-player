# Reference Player

Reference Player is a noncommercial fork of [Rivulet](https://github.com/l984-451/Rivulet), the Apple TV,
iPhone, iPad and Mac media client for Plex, Jellyfin, Emby and IPTV by Bain Gurley.

This is not the original app. It is not affiliated with or endorsed by Rivulet's author. Rivulet's
App Store and TestFlight builds are upstream's, not this fork's, and upstream's support channels are
for upstream's app. Questions about this fork belong in this repository.

## The idea

The name states the aim: a reference player plays what is in the file. This fork tries to keep a
source's picture and sound as intact as tvOS allows, and where there is a choice it prefers the path
that changes the least. On a Dolby Vision TV it asks the engine to compose a Profile 7 FEL title's
enhancement layer rather than drop it. When your server offers a file directly, the app plays the
original file, and it turns to a server transcode only when there is no usable direct link, when you
pick a lower quality, or when the player fails to start. With Height Speakers set, an object
soundtrack keeps its height channels instead of losing them, sent as lossless PCM over HDMI. Format
badges are there to tell you what a file is, and none of upstream's hosted services is contacted.
Where Apple's platform forces a conversion, the app should say so plainly and never label the result
as more than it is. The limits are just as plain: Apple TV cannot pass the original audio bitstream
to a receiver, so the receiver's own decoder is not used; the rendered channels have not been shown
to match a receiver's decoding; and nothing here has yet been checked on a physical Apple TV by this
fork's author. This is a goal the fork is held to, not a claim that it has been reached or measured.

## What it adds

The settings below are in the Apple TV app only. The iPhone, iPad and Mac apps build from the same
tree and also refuse upstream's hosted services, but have none of these settings.

- **Dolby Vision Profile 7 FEL.** For a Profile 7 title with a full enhancement layer (FEL), the
  playback engine can decode the base and enhancement layers and compose them itself (its dual-layer
  route) instead of dropping the enhancement layer. Settings > Playback > Dolby Vision FEL turns it
  on or off. It is on by default. Limits:
  - The app asks for the route only when the TV reports Dolby Vision support, and only FEL sources
    take it. Without a Dolby Vision TV, Profile 7 plays its HDR10 base layer.
  - Profile 7 MEL titles, and FEL titles that do not take the route, are converted to single-layer
    Profile 8.1. For FEL the engine then folds the enhancement layer's average effect on brightness
    into each scene's luma curve where it can measure it: the overall tone is kept, the fine detail
    and grain are not.
  - The engine refuses the route when a title is over its real-time budget (UHD at 30 fps plus a
    1080p enhancement layer), and plays the 8.1 conversion instead.
  - The route is a software path. An AirPlay receiver gets the sound but no picture.
  - The composed picture leaves the engine as 10-bit video. This is not a promise of 12-bit output
    to the TV.
  - A Dolby Vision indicator on the TV does not prove that every frame used the enhancement layer:
    the engine sends a frame as its base layer when that frame's enhancement layer arrives late or
    composition fails.
  - The player's Details tab shows which of these happened, from what the engine reported: Dual
    Layer (FEL), Profile 8.1 (converted) or HDR10 Base Layer.
  - It is demanding on older Apple TV hardware. Simulator and Debug builds say nothing about it.
  - Like everything here, it has not yet been checked on a physical Apple TV by this fork's author.
- **Height speakers for TrueHD Atmos and DTS:X.** Settings > Playback > Height Speakers picks a
  layout (5.1.2, 5.1.4, 7.1.2, 7.1.4 or 9.1.6). With one set, an object soundtrack keeps its height
  channels, rendered by the playback engine into that layout and sent as lossless PCM over HDMI. It
  is off by default. Limits:
  - tvOS 26 or later, and a 48 kHz track: TrueHD, or DTS-HD Master Audio for DTS:X.
  - DTS:X heights render only into 7.1.4 or 9.1.6. With a smaller layout a DTS:X track plays its
    ordinary channels.
  - The app sends fixed channels, not the original bitstream or movable objects, so your receiver's
    own Atmos or DTS:X decoder is not used. The rendering has not been shown to match a receiver's.
  - HDMI is the target. AirPlay is a different, lossy path.
  - An Atmos or PCM indicator on a receiver does not prove the heights arrived correctly.
  - It can be demanding on older Apple TV hardware. Simulator and Debug builds say nothing about
    playback performance.
- **Format badges** on detail pages and folder lists (DV FEL, DV MEL and TrueHD Atmos), read from an
  optional index of your own library (Settings > Playback > Format Tags). The tools that build the index are in
  `PrivateBuild/tools/format-index/`. With no index address set, which is the default, nothing is
  fetched.
- **Playback reporting to Home Assistant**, optional and off by default, with no address built in.
  A package and a dashboard card are in `PrivateBuild/homeassistant/`.
- **Discover, recommendations, actor biographies and Metacritic and Letterboxd scores** from your
  own free TMDB and MDBList keys (Settings > Playback > Discover and Scores). Without keys nothing is
  fetched from those services.
- **No upstream hosted services.** Rivulet's metadata Workers, in-player trivia, the Live TV sports
  scoreboard and diagnostics upload are run by its author for his app. This fork does not use them:
  those screens are still there, but each request to upstream's services is refused inside the app
  before anything goes out on the network. With your own keys, Discover and scores come from TMDB
  and MDBList instead (above). No Sentry destination is built in.
- **Tooling to follow upstream releases**: `PrivateBuild/sync.py` merges an upstream release tag and
  does the mechanical part, and `PrivateBuild/check.py` runs every check.

The engine also carries a [small pinned patch](../PrivateBuild/engine-patches/README.md) for FEL RPU
mapping reuse and reconstruction precision. It resolves the requested mapping ID, preserves the
current residual switch when concealing a missing RPU, combines prediction and residual before
rounding, and supports the full MMR coefficient range. The numerical changes follow the published
fixed-point process. Image fidelity and GPU performance on an Apple TV still need measurement.

## Status

- It builds, and its unit tests pass on the tvOS simulator: 3,534 of 3,534 tvOS unit tests and 176
  Python tests for the fork's tooling, measured on 2026-11-12 with Xcode 27.1, the version on the
  machine that ran them (upstream pins Xcode 27.2 in `codemagic.yaml`). The checks also built the
  tvOS app in Release for a real Apple TV and compiled the iOS and Mac apps in their default
  configuration.
- Playback features have not yet been checked by this fork's author on a physical Apple TV.
- No binaries are provided. You build it yourself, signed with your own Apple developer account.

## Build

You need a Mac with Xcode and an Apple developer account. Upstream builds with Xcode 27.2;
`PrivateBuild/build-tvos.sh` refuses anything older, although this fork's own checks above ran on
27.1. The App Group and iCloud container the apps use need a paid membership; a free Personal Team
is not promised to sign them.

```bash
# Secrets.swift with an empty Sentry DSN (this fork ships no Sentry destination; verify.py insists)
sed 's/"YOUR_SENTRY_DSN_HERE"/""/' RivuletCore/Config/Secrets.swift.template > RivuletCore/Config/Secrets.swift
python3 PrivateBuild/configure.py --bundle-id com.yourname.referenceplayer --team-id YOURTEAMID
python3 PrivateBuild/engine.py
python3 PrivateBuild/check.py --quick
open ReferencePlayer.xcworkspace    # scheme: Reference Player
```

`configure.py` writes your bundle prefix, App Group (`group.<bundle-id>` unless you pass
`--app-group`), iCloud container and team into the project, and can set `--display-name` and
`--url-scheme`. Register the App Group and iCloud container for your team in Xcode or the developer
portal. Before committing a change, `python3 PrivateBuild/configure.py --placeholder` puts the
repository's placeholder identifiers back. `python3 PrivateBuild/check.py --all` runs every check,
including the full tvOS test suite and the iOS and Mac builds.

## Where things are

The root `README.md`, the body of `CLAUDE.md`, `Docs/` and the artwork are upstream's, unmodified,
so that merges stay clean. Everything the root `README.md` says describes upstream's app, not this
fork. The issue templates and funding links in `.github/` are upstream's files, kept unmodified, and
donations through them go to Rivulet's author. Notes for this fork are in `PrivateBuild/`, starting
with `PrivateBuild/CLAUDE-private.md` and `PrivateBuild/SYNC.md`.

## License and credits

Rivulet and this fork are licensed under the [PolyForm Noncommercial License 1.0.0](../LICENSE).

Required Notice: Copyright (C) 2025-2026 Bain Gurley

This fork is free of charge and not for sale.

Playback runs on [AetherEngine](https://github.com/superuser404notfound/AetherEngine) (LGPL-3.0
with an Apple Store / DRM exception), with [FFmpegBuild](https://github.com/superuser404notfound/FFmpegBuild)
(FFmpeg, LGPL-2.1-or-later), [LibDovi](https://github.com/superuser404notfound/LibDovi) (libdovi from
[dovi_tool](https://github.com/quietvoid/dovi_tool), dual-licensed MIT or Apache-2.0) and
[LibTrueHD](https://github.com/superuser404notfound/LibTrueHD) (MIT, with
[truehdd](https://github.com/truehdd/truehdd) under Apache-2.0). Subtitles use
[LibassBuild](https://github.com/l984-451/LibassBuild) (libass, ISC). Each keeps its own license.

Some optional components may be subject to third-party patent licensing. Whoever builds and uses
them is responsible for their own licensing.

Dolby, Dolby Atmos, Dolby TrueHD and Dolby Vision are trademarks of Dolby Laboratories. DTS:X is a
trademark of DTS, Inc. Plex, Jellyfin and Emby are the trademarks of their respective owners. This
fork is not affiliated with or endorsed by any of them.
