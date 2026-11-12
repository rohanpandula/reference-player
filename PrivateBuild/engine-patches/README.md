# Reference Player's AetherEngine patch

The upstream baseline remains **AetherEngine 7.61.0**, commit
`d612bedb3e60f8bc403c646fe44c0a852ee8a995`, in `UPSTREAM.json` and the original
`Package.resolved`. The patch here is owned by Reference Player. No engine repository or release
has to be published to build it.

## Build

Run `python3 PrivateBuild/engine.py`, then open `ReferencePlayer.xcworkspace`.
The workspace overrides the remote engine with `PrivateBuild/.build/AetherEngine`, using
[Xcode's local package override](https://developer.apple.com/documentation/xcode/editing-a-package-dependency-as-a-local-package).
Opening `Rivulet.xcodeproj` directly still uses the unmodified upstream engine.
`check.py` and `build-tvos.sh` use the patched workspace. The workspace shares the project's
original dependency lock through a relative symlink; the engine base and transitive dependencies
stay pinned. The project's five package traits stay enabled.

`engine.py` clones the exact release, checks its commit, and applies the patch. Re-running it
verifies the existing checkout without changing anything. `--check` needs no network. The check
rejects a changed commit, extra tracked edits, untracked files, or a symlinked checkout. If it
refuses an existing checkout, move that directory aside to preserve your work before preparing
again. A failed clone or patch application never installs a partially prepared engine.

## What the patch changes

`0001-fel-rpu-mapping.patch` fixes reconstruction state on the dual-layer route:

- Keep `vdr_rpu_id` and `prev_vdr_rpu_id` through RPU extraction. Reuse resolves the named slot
  (0 through 15), not the most recently supplied mapping. An absent slot uses slot zero; without
  either slot, or for an invalid ID, that frame bypasses composition.
- A missing/corrupt RPU borrows the last *resolved* frame's parameters and display metadata,
  including a residual switch changed by a reuse frame. Valid reuse frames reset the existing
  three-frame corruption-concealment budget.
- Clear the slot cache at seek and teardown, in the same critical section as the existing RPU state.

The patch includes parser/converter tests and decoder tests for slot replacement, out-of-order
frame pairing, absent/invalid IDs, seeking, and the stale residual switch. It leaves shader math,
resampling, output precision, routing and audio unchanged. Titles that only redefine slot zero
with no missing RPUs should have identical output.

The supplied `dovi_cleanroom` corpus was inspected as reference material. Its ID-indexed payload
handling suggested this investigation. No decompiled code or binary is included. The behavior is
also independently visible in [FFmpeg's RPU decoder](https://github.com/FFmpeg/FFmpeg/blob/master/libavcodec/dovi_rpudec.c)
(`s->vdr[prev_vdr_rpu_id]` and the fallback to slot zero). This is a targeted state-correctness fix,
not a claim of bit-exact Dolby reconstruction or measured picture improvement on an Apple TV.

## Follow upstream

1. Review the candidate upstream release, including audio and all five traits, as before. Update
   the engine pin in the project, `Package.resolved`, `UPSTREAM.json`, and `verify.py` together.
2. Keep the existing engine checkout elsewhere, then run `python3 PrivateBuild/engine.py` against
   the new baseline. Patch conflicts stop preparation. Review every hunk even if it applies cleanly.
3. If upstream already fixes this behavior, remove the superseded hunks and their duplicate tests.
   An empty patch is valid when no private engine changes remain.
4. Run the engine regression tests with and without traits, then `python3 PrivateBuild/check.py --all`.
   Test FEL playback and seeks on a physical Apple TV before claiming image fidelity or performance.

To revise the patch, work in the prepared engine and export its diff against the pinned HEAD:

```bash
git -C PrivateBuild/.build/AetherEngine diff --binary --no-ext-diff --no-color --no-renames HEAD -- > PrivateBuild/engine-patches/0001-fel-rpu-mapping.patch
python3 PrivateBuild/engine.py --check
swift test --package-path PrivateBuild/.build/AetherEngine --enable-all-traits --filter 'Dovi|DualLayer|FrameSideData'
```

The engine patch retains AetherEngine's LGPL-3.0 license. Its changes can be extracted as an ordinary
Git patch for a future upstream contribution; keeping it here does not publish anything upstream.
