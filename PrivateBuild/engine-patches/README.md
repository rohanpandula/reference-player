# Reference Player's AetherEngine patch

The upstream baseline remains **AetherEngine 7.61.0**, commit
`d612bedb3e60f8bc403c646fe44c0a852ee8a995`, in `UPSTREAM.json` and the original
`Package.resolved`. The patch here is owned by Reference Player. No engine repository or release
has to be published to build it. Upstream changelog and documentation files are left untouched;
fork behavior and validation are documented here and in `PrivateBuild/RESAMPLING.md`.

## Build

Run `python3 PrivateBuild/engine.py`, then open `ReferencePlayer.xcworkspace`.
The workspace overrides the remote engine with `PrivateBuild/.build/AetherEngine`, using
[Xcode's local package override](https://developer.apple.com/documentation/xcode/editing-a-package-dependency-as-a-local-package).
Opening `Rivulet.xcodeproj` directly still uses the unmodified upstream engine.
`check.py` and `build-tvos.sh` use the patched workspace. The workspace has its own dependency lock for the eight remote/transitive packages; the engine
is local and is verified against `UPSTREAM.json` plus the patch. The project's original nine-package
lock remains untouched, and the dependency versions stay pinned. The project's five package traits stay enabled.

`engine.py` clones the exact release, checks its commit, and applies the patch to its index and working tree. Re-running it
verifies the existing checkout without changing anything. `--check` needs no network. The check
rejects a changed commit, extra tracked edits, untracked files, or a symlinked checkout. If it
refuses an existing checkout, move that directory aside to preserve your work before preparing
again. A failed clone or patch application never installs a partially prepared engine.

## What the patch changes

`0001-fel-accuracy.patch` fixes reconstruction state and arithmetic on the dual-layer route:

- Keep `vdr_rpu_id` and `prev_vdr_rpu_id` through RPU extraction. Reuse resolves the named slot
  (0 through 15), not the most recently supplied mapping. An absent slot uses slot zero; without
  either slot, or for an invalid ID, that frame bypasses composition.
- A missing/corrupt RPU borrows the last *resolved* frame's parameters and display metadata,
  including a residual switch changed by a reuse frame. Valid reuse frames reset the existing
  three-frame corruption-concealment budget.
- Clear the slot cache at seek and teardown, in the same critical section as the existing RPU state.

- Preserve unsigned 16-bit prediction and signed 17-bit residual until they are added, then round
  once to the declared VDR depth. Keep fractional residual ceilings and apply the sign before shifting.
- Accept the full signed MMR coefficient range (-65536 through 65535 integer parts). Upload raw
  Int64 coefficients and evaluate the specified Q20 terms on the GPU. Splitting product quotients
  and remainders avoids overflow in large sums without losing cancellation precision.

The numerical baseline is [ETSI GS CCM 001, sections 5.3 and 5.4](https://www.etsi.org/deliver/etsi_gs/CCM/001_099/001/01.01.01_60/gs_ccm001v010101p.pdf).
Regression tests cover fractional reconstruction, positive/negative residuals, sub-code ceilings,
wide MMR cancellation, accumulator overflow, and real RPU coefficient sets against an independent
Int128 model. The 16-bit prediction is stored exactly in the existing Float luma lookup table;
MMR coefficients use a separate Int64 table. Removing the duplicate coefficients from the parameter
block keeps the combined parameter/table allocation unchanged. Layout is checked on the GPU.

Resampling defaults, output formats, dithering policy, routing and audio are unchanged.
The patch also carries an opt-in GPU comparison test for the existing bilinear and Annex B
luma paths. Reference Player owns its independent CPU model, generated inputs, runner and
[measured resampling comparison](../RESAMPLING.md); it adds no playback setting or dependency. The internal
reconstruction precision change can affect a final output code. Real-title fidelity and physical
Apple TV GPU performance have not been measured. The [Release baseline comparison](../FEL-PERFORMANCE.md)
finds about 2.3× GPU time for 4K MMR composition on this Mac; physical Apple TV validation remains
a merge gate. The Mac result does not establish device headroom.

The supplied `dovi_cleanroom` corpus was inspected as reference material. Its ID-indexed payload
handling suggested this investigation. No decompiled code or binary is included. The behavior is
also independently visible in [FFmpeg's RPU decoder](https://github.com/FFmpeg/FFmpeg/blob/master/libavcodec/dovi_rpudec.c)
(`s->vdr[prev_vdr_rpu_id]` and the fallback to slot zero). The cleanroom's coefficient packing helped identify the range limitation; its hardware register
layout does not establish pixel-output equivalence. These are targeted corrections against the
published reconstruction process, not a claim of certified Dolby reconstruction.

## Follow upstream

1. Review the candidate upstream release, including audio and all five traits, as before. Update
   the engine pin in the project, `Package.resolved`, `UPSTREAM.json`, and `verify.py` together.
2. Keep the existing engine checkout elsewhere, then run `python3 PrivateBuild/engine.py` against
   the new baseline. Patch conflicts stop preparation. Review every hunk even if it applies cleanly.
3. If upstream already fixes this behavior, remove the superseded hunks and their duplicate tests.
   An empty patch is valid when no private engine changes remain. The state and arithmetic changes
   are separate sections of this one diff, so either can be removed when superseded.
4. Run the engine regression tests with and without traits, then `python3 PrivateBuild/check.py --all`.
   Test FEL playback and seeks on a physical Apple TV before claiming image fidelity or performance.

On 2026-11-12, `git apply --check --index` accepted this patch on clean detached worktrees at
7.61.0 (`d612bedb`), 7.62.0 (`a1d2cab8`), 7.63.0 (`f81a8b5b`), 7.64.0 (`b0d91055`), and
7.65.0 (`260af699`). This checks textual applicability only; those later releases have not been
adopted or validated for playback. The build remains pinned to 7.61.0.

To revise the patch, work in the prepared engine and export its diff against the pinned HEAD.
Preserve the dated license/source notice before the first `diff --git` line when replacing the hunks:

```bash
# Add new regression files to the index before exporting so the patch includes them.
git -C PrivateBuild/.build/AetherEngine diff --binary --no-ext-diff --no-color --no-renames HEAD -- > PrivateBuild/.build/engine-changes.diff
# Replace only the existing patch hunks with engine-changes.diff; retain/update its notice.
python3 PrivateBuild/engine.py --check
swift test --package-path PrivateBuild/.build/AetherEngine --enable-all-traits --filter 'Dovi|DualLayer|FrameSideData'
```

## License, changes and corresponding source

**Modified by Reference Player contributors on 2026-11-12.** Copyright © 2026 Reference Player
contributors for these modifications; upstream copyright © 2026 Vincent Herbst is retained.
The engine modifications in this patch are licensed under **GNU LGPL version 3 with AetherEngine's
Apple Store / DRM Exception**, as reproduced in the pinned upstream `LICENSE`. The patch's introductory
notice records that license, date, baseline and changes. The application's PolyForm license does not
replace the engine patch's license.

The corresponding engine source is the [pinned upstream commit](https://github.com/superuser404notfound/AetherEngine/tree/d612bedb3e60f8bc403c646fe44c0a852ee8a995)
plus this patch from the **same Reference Player source revision** as the app. That revision also holds
`PrivateBuild/engine.py`, the original dependency lock, `ReferencePlayer.xcworkspace`, and the build
scripts. The in-app Licenses & Legal entry identifies both source locations and reproduces the upstream
LGPL/GPL texts and exception. Keep the matching source revision available with any distributed build.

To obtain the modified engine, check out that app revision and run `python3 PrivateBuild/engine.py`.
The complete prepared source is in `PrivateBuild/.build/AetherEngine`. To build your own changes, edit
that checkout and open `ReferencePlayer.xcworkspace`; Xcode compiles the local engine. The verification
scripts deliberately reject edits beyond the recorded patch: update its hunks, retaining the notice,
before using `check.py` or `build-tvos.sh`. This keeps the reviewed release reproducible while allowing
recipients to modify and rebuild the library. Use your own signing configuration as described in
`PrivateBuild/SYNC.md` and `.github/README.md`.

The patch can be extracted for a future upstream contribution; keeping it here does not publish
anything upstream.
