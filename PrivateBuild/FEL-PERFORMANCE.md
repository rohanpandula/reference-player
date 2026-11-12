# Exact MMR performance improvement

The maintained patch reduces 4K MMR composition median GPU time by **9.0% through the pool route**
and **9.5% in-place**, compared with the previous accuracy patch on this Mac. Both generated
output frames match byte for byte in every case and run. The change preserves the fixed-point
arithmetic and all resampling defaults; it does not switch back to the upstream float calculation.
Remaining 4K MMR GPU time is about **2.1× unmodified upstream** in the separate control experiment.

**The PR remains draft until the target Apple TV is tested.** These are composition measurements on
an Apple M6, not evidence of playback headroom on the older Apple TV.

## The change and its bounds

Only the MMR accumulation loop changes in production. It sums up to 16 coefficient/term products
before separating the quotient and remainder, then handles the remaining six. This reduces the
number of splits from as many as 22 to at most two. Basis construction, component callers,
coefficients, piece selection, residuals and final rounding are unchanged.

For a legal coefficient denominator `d <= 23`, coefficients are in `[-2^(16+d), 2^(16+d))`.
Every Q20 term is nonnegative and at most `2^20`, including the constant term. Each product is
therefore in `[-2^59, 2^59)`, and any group of 16 fits signed 64-bit. The complete 22-product sum
can overflow, so the implementation still separates partial sums before combining them.
For `s = d + 4`, write each partial as `q * 2^s + r`, with `0 <= r < 2^s`. Combining `q` values
and the carry from the two `r` values gives exactly the original floor, including negative partials.
The two remainders sum to less than `2^28`, so UInt32 is sufficient. Products and whole sums stay Int64.
There is no coefficient-size heuristic or approximate fast path.

## Previous accuracy patch versus optimized patch — 2026-11-12

Apple M6, macOS 27.2 (26B123), Xcode 27.1 / Swift 6.4. Both snapshots use **Release** and all five
traits on AetherEngine 7.61.0 (`d612bedb`). Before is the patch from Reference Player `be79b4e6`;
after is the maintained patch recorded by SHA-256 in the
[complete measurements](measurements/fel-mmr-optimization-2026-11-12.json). Bilinear remains the luma default.

Cells show the **median of four run medians**, then their full minimum–maximum range. These ranges
are not confidence intervals. Ratios divide those summary medians, not paired command-buffer times.
All runs and samples are retained. MMR means one real FEL RPU with both chroma MMR paths active on
two synthetic textured BL/EL pairs per size, not a decoded-title benchmark.

| Frame size / mapping | Route | Before GPU ms (range) | After GPU ms (range) | After / before |
|---|---|---:|---:|---:|
| 1080p / real RPU, MMR | in-place | 0.418 (0.417–0.420) | 0.378 (0.378–0.379) | 0.90× |
| 1080p / real RPU, MMR | pool | 0.428 (0.427–0.455) | 0.387 (0.387–0.388) | 0.90× |
| 1080p / identity | in-place | 0.151 (0.150–0.204) | 0.150 (0.150–0.151) | 1.00× |
| 1080p / identity | pool | 0.158 (0.158–0.479) | 0.159 (0.157–0.205) | 1.00× |
| 4K / real RPU, MMR | in-place | 1.702 (1.695–1.799) | 1.540 (1.536–1.544) | 0.90× |
| 4K / real RPU, MMR | pool | 1.714 (1.708–1.716) | 1.560 (1.555–1.562) | 0.91× |
| 4K / identity | in-place | 0.709 (0.708–0.717) | 0.709 (0.707–0.711) | 1.00× |
| 4K / identity | pool | 0.698 (0.694–0.705) | 0.701 (0.699–0.706) | 1.00× |

The 4K MMR median falls in every matched round. Tail timings are less uniform: in-place per-run
p95 ranges from 1.702–1.906 ms before and 1.721–1.727 ms after. Several 1080p identity blocks
also show substantial variation. All are retained; the median gain does not establish uniform
tail-latency improvement or stable GPU clocks.

## Remaining cost versus unmodified upstream

A separate four-round comparison uses unpatched 7.61.0 as the baseline and the same optimized patch.
It uses the identical harness and inputs. Its after timings are reported separately rather than
mixed into the before/after experiment. Upstream output is not required to match: it lacks the
accuracy fixes. The earlier unoptimized comparison, including both complete passes and slower
baseline blocks, remains in [its original data](measurements/fel-patch-performance-2026-11-12.json).

| Frame size / mapping | Route | Upstream GPU ms (range) | Optimized GPU ms (range) | Optimized / upstream |
|---|---|---:|---:|---:|
| 1080p / real RPU, MMR | in-place | 0.171 (0.171–0.183) | 0.383 (0.382–0.383) | 2.23× |
| 1080p / real RPU, MMR | pool | 0.188 (0.187–0.291) | 0.401 (0.397–0.402) | 2.13× |
| 1080p / identity | in-place | 0.146 (0.144–0.477) | 0.154 (0.151–0.156) | 1.06× |
| 1080p / identity | pool | 0.162 (0.157–0.476) | 0.170 (0.168–0.172) | 1.05× |
| 4K / real RPU, MMR | in-place | 0.760 (0.755–0.769) | 1.616 (1.540–1.729) | 2.13× |
| 4K / real RPU, MMR | pool | 0.763 (0.761–0.765) | 1.578 (1.572–1.589) | 2.07× |
| 4K / identity | in-place | 0.734 (0.725–0.752) | 0.733 (0.729–0.736) | 1.00× |
| 4K / identity | pool | 0.733 (0.722–0.737) | 0.737 (0.733–0.739) | 1.00× |

## Completion latency

Slot acquisition through the async callback includes per-frame LUT generation/upload, encoding,
GPU execution and delivery. This is neither pure CPU time nor full playback latency. The table
compares the previous accuracy patch with the optimized patch; upstream completion samples and
all per-run p95 values are also retained in the JSON.

| Frame size / mapping | Route | Before completion ms (range) | After completion ms (range) |
|---|---|---:|---:|
| 1080p / real RPU, MMR | in-place | 0.591 (0.586–0.595) | 0.546 (0.543–0.551) |
| 1080p / real RPU, MMR | pool | 0.617 (0.607–0.655) | 0.574 (0.573–0.575) |
| 1080p / identity | in-place | 0.320 (0.319–0.385) | 0.320 (0.317–0.321) |
| 1080p / identity | pool | 0.343 (0.340–0.685) | 0.343 (0.337–0.386) |
| 4K / real RPU, MMR | in-place | 1.943 (1.886–2.086) | 1.795 (1.740–1.831) |
| 4K / real RPU, MMR | pool | 1.912 (1.908–1.914) | 1.755 (1.751–1.758) |
| 4K / identity | in-place | 0.896 (0.895–0.897) | 0.894 (0.893–0.896) |
| 4K / identity | pool | 0.886 (0.885–0.896) | 0.891 (0.890–0.895) |

## Accuracy and measurement boundaries

- The complete two-frame Bayer P010 outputs match before/after for all eight cases and every run;
  pool and in-place outputs also agree within each engine. The two final buffers are retained at
  sequences 68/69 and hashed after timing. This checks both inputs and dither parities, not every
  intermediate call, raw prediction, or possible RPU.
- Separate GPU precision tests compare full 16-bit predictions with an independent Int128 model:
  1,134 isolated monomial cases across BL depths 8–16, six all-term cancellation cases, ten cases
  crossing the 16-product boundary at five denominators and both signs, plus eleven mixed chroma
  order/polynomial/identity combinations with separate component clamps and denominators.
  Existing full-range overflow, residual, rounding, layout and output-format tests remain intact.
  Of 468 engine tests, 432 run and pass; 36 media-dependent or opt-in tests are skipped.
- Reference Player’s `check.py --all` also passes: 3,534/3,534 tvOS app tests, 216 Python tests,
  dependency/private-string checks, and tvOS Release device-SDK, iOS and Mac builds.
- Each fresh composer gets ten warm-up and sixty measured calls. Both production async routes
  must succeed; counters and pool checks reject silent fallback. BL planes are restored before
  every call outside timing, so in-place never consumes its previous output.
- Parameters are parsed once and reused. Parsing, mapping-slot updates, initialization, restoration,
  decode, rendering and file I/O are excluded. Per-compose LUT work, upload and encoding stay
  inside completion latency. GPU time is derived from the existing cumulative `meanGPUTimeMs`
  before its 120-frame window closes; no engine timing instrumentation is added.
- Each experiment alternates process order across four rounds: before/after, after/before,
  before/after, after/before. No other builds or tests run during measurements. All recorded
  thermal states are nominal and Low Power Mode is off. Clocks and unrelated system contention
  are not controlled; the full ranges are shown instead of discarding unusual samples.

The upstream general test suite calls DEBUG-only seams and does not compile in Release. The runner
selects only the benchmark, verbatim fixture helpers and existing queue support in its isolated test
targets. Production sources, optimization and dependency locks remain intact. Manifest, fixture,
harness and all source hashes are recorded and checked before and after measurement. The maintained
regression suite is not pruned. The patch remains a focused Reference Player overlay and applies
textually to tags 7.61.0–7.65.0; later engines have not been adopted or validated for playback.

## Reproduce on a Metal Mac

Save the previous accuracy patch from the same repository history, then use a fresh directory:

```sh
python3 PrivateBuild/engine.py
git show be79b4e6dc17d8b119f1c6f3b9891aede6c1d843:PrivateBuild/engine-patches/0001-fel-accuracy.patch > PrivateBuild/.build/mmr-before.patch
python3 PrivateBuild/tools/fel_performance.py --directory PrivateBuild/.build/mmr-comparison --baseline-patch PrivateBuild/.build/mmr-before.patch --prepare-only
python3 PrivateBuild/tools/fel_performance.py --directory PrivateBuild/.build/mmr-comparison --build-only
# Wait for all builds/tests/GPU workloads to stop before measuring.
python3 PrivateBuild/tools/fel_performance.py --directory PrivateBuild/.build/mmr-comparison --run-only
python3 PrivateBuild/tools/fel_performance.py --directory PrivateBuild/.build/mmr-comparison --analyze-only
```

For upstream versus optimized, use another fresh directory and omit `--baseline-patch` during
preparation. Later stages use the saved baseline definition and reject patch/source/harness drift.
Existing reports are never overwritten. `outputsMatch` must be true for every before/after case;
it may be false against upstream because reconstruction differs. The old final-frame-only report
format remains readable, with `outputsMatch` null where both output hashes are unavailable.

## Remaining merge gate

Compare baseline and optimized Release builds on the actual older Apple TV with representative FEL
MMR titles and sustained playback. Record model/OS, composition route, GPU time, frame deadlines,
drops, seek recovery and fallback behavior under decode/render load. No physical Apple TV or encoded
title/master comparison was available for this work. The Mac measurements must not be extrapolated
into Apple TV frame-rate, headroom or certified Dolby-fidelity claims.
