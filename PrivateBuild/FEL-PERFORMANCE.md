# Patched versus upstream FEL composition cost

**Do not merge the accuracy patch until performance is checked on the target Apple TV.**
On this Mac, the patch's real-RPU/chroma-MMR workload takes about **2.3× the baseline GPU time
at 4K** through both production composition paths. Identity-mapped composition stays near baseline.
This is a material cost, not evidence that the older Apple TV has enough headroom. The earlier
[resampling comparison](RESAMPLING.md) compared filters on the patched engine and did not measure
this baseline-to-patch difference.

## Results — 2026-11-12

Apple M6, macOS 27.2 (26B123), Xcode 27.1 / Swift 6.4. Both engines are built in **Release** with
all five traits. Baseline is unpatched AetherEngine 7.61.0 (`d612bedb`); the other applies the exact
Reference Player patch recorded by SHA-256 in the [complete results](measurements/fel-patch-performance-2026-11-12.json).
The shipping luma filter remains bilinear in both. No arithmetic or shader instrumentation was
added for measurement. All recorded thermal states were nominal; Low Power Mode was off.

Each cell shows the **median of eight run medians**, followed by the full range of those medians.
These ranges are not confidence intervals. Ratios divide the summary medians; they are not paired
per-command ratios. Every run and sample is retained in the JSON, including the unusual blocks
explained below. MMR here means one real FEL RPU fixture with both chroma MMR paths active on
synthetic textured frames, not a decoded-title benchmark.

| Frame size / mapping | Route | Baseline GPU ms (range) | Patched GPU ms (range) | Ratio |
|---|---|---:|---:|---:|
| 1080p / real RPU, MMR | in-place | 0.171 (0.170–0.205) | 0.420 (0.416–0.421) | 2.45× |
| 1080p / real RPU, MMR | pool | 0.181 (0.177–0.350) | 0.432 (0.427–0.444) | 2.38× |
| 1080p / identity | in-place | 0.143 (0.141–0.478) | 0.150 (0.150–0.151) | 1.05× |
| 1080p / identity | pool | 0.158 (0.145–0.476) | 0.160 (0.156–0.248) | 1.01× |
| 4K / real RPU, MMR | in-place | 0.742 (0.735–2.271) | 1.755 (1.692–1.911) | 2.37× |
| 4K / real RPU, MMR | pool | 0.750 (0.745–0.802) | 1.724 (1.717–1.785) | 2.30× |
| 4K / identity | in-place | 0.702 (0.700–0.728) | 0.716 (0.706–0.735) | 1.02× |
| 4K / identity | pool | 0.704 (0.688–0.723) | 0.704 (0.693–0.731) | 1.00× |

At 4K with MMR, the pool summary increases from **0.750 to 1.724 ms** GPU time, an extra **0.973 ms**;
in-place increases from **0.742 to 1.755 ms**, an extra **1.013 ms**. The identity controls show
small changes, so the result is consistent with the cost being concentrated in MMR evaluation.
This comparison does not isolate individual arithmetic instructions or prove the precise cause.

Completion latency measures slot acquisition through the asynchronous callback, including encode,
per-frame LUT generation/upload, GPU execution and delivery. It is not CPU time or full playback latency.

| Frame size / mapping | Route | Baseline completion ms (range) | Patched completion ms (range) |
|---|---|---:|---:|
| 1080p / real RPU, MMR | in-place | 0.340 (0.332–0.386) | 0.592 (0.588–0.594) |
| 1080p / real RPU, MMR | pool | 0.359 (0.349–0.555) | 0.616 (0.607–0.626) |
| 1080p / identity | in-place | 0.308 (0.305–0.659) | 0.317 (0.307–0.319) |
| 1080p / identity | pool | 0.332 (0.323–0.672) | 0.341 (0.333–0.427) |
| 4K / real RPU, MMR | in-place | 0.923 (0.918–2.471) | 1.961 (1.883–2.170) |
| 4K / real RPU, MMR | pool | 0.937 (0.931–1.066) | 1.917 (1.911–2.011) |
| 4K / identity | in-place | 0.882 (0.877–0.927) | 0.898 (0.891–0.918) |
| 4K / identity | pool | 0.886 (0.872–0.912) | 0.890 (0.882–0.941) |

The first pass's third baseline round has a sustained **2.271 ms** 4K MMR in-place block, versus
roughly 0.74 ms in its other rounds. Some early 1080p baseline blocks are also slower. These are
whole-block changes, not a few samples that can be trimmed. A second complete pass repeated the
same protocol and binaries; it did not reproduce the 2.271 ms block. Both complete passes remain
in the summary and raw data. Nominal thermal state does not establish stable clocks or exclude
system contention; their cause is unmeasured. The 4K MMR pool increase appears in every round.

## Measurement method and boundary

- Two deterministic BL/EL frame pairs per size, 1920×1080 plus half-size EL and 3840×2160 plus
  half-size EL. Identity mapping and the existing real FEL RPU fixture exercise polynomial and
  chroma-MMR composition. Input hashes agree between engines and runs.
- Each fresh composer gets ten warm-up calls and sixty measured calls. Both async pool and async
  in-place routes must succeed; route counters and pool allocation checks reject silent fallback.
  Base planes are restored before every call in both routes, outside timing, so in-place never
  reads its previous output. Bayer 8×8 output and the same frame-index sequence are used throughout.
- Parameters are parsed once and reused. RPU parsing, mapping-slot state updates, initialization,
  buffer restoration, decode, rendering and file I/O are excluded. Per-compose LUT generation,
  serialization, upload, encoding and callback delivery remain inside completion latency.
- GPU durations come from the existing production `meanGPUTimeMs`: successive differences of
  cumulative mean × successful-call count, before its first 120-frame window closes. There are
  exactly 70 successful calls per composer. Nonpositive/nonfinite durations fail the benchmark.
- Each pass alternates process order across four rounds: baseline/patched, patched/baseline,
  baseline/patched, patched/baseline. The repeat used the same binaries and schedule. These are
  matched crossover rounds, not interleaved command-buffer pairs. No other builds or tests ran
  during either measured pass.
- Pool/in-place output hashes must agree for the **final frame only** (sequence 69 / input frame 1)
  within each engine. Cross-engine output equality is deliberately not required: reconstruction
  arithmetic differs. This calibration is not a fidelity test of all timed frames.

The general upstream test suite does not compile in Release because some tests call DEBUG-only
seams. The runner therefore narrows **only the test target in isolated benchmark checkouts** to
this harness, verbatim fixture helpers and its existing queue support. Production sources, target
settings, dependency pins and Release optimization remain intact. Original/selected manifest hashes,
fixture hashes and production-source hashes are recorded. The maintained engine patch and normal
regression suite are not pruned or weakened. The ordinary 464-test engine run passes separately.

This measures composition with reused RPU parameters, not the entire patch's CPU cost or a full
player under decode/render contention. It does not bound every legal MMR mapping, metadata change,
output format or GPU architecture. No physical Apple TV or encoded title/master pair was available.

## Reproduce on a Metal Mac

No NumPy dependency is needed for this comparison. Use a fresh directory:

```sh
python3 PrivateBuild/engine.py
python3 PrivateBuild/tools/fel_performance.py --directory PrivateBuild/.build/fel-performance --prepare-only
python3 PrivateBuild/tools/fel_performance.py --directory PrivateBuild/.build/fel-performance --build-only
# Wait for all builds/tests/GPU workloads to stop before measuring.
python3 PrivateBuild/tools/fel_performance.py --directory PrivateBuild/.build/fel-performance --run-only
python3 PrivateBuild/tools/fel_performance.py --directory PrivateBuild/.build/fel-performance --analyze-only
```

Preparation checks the maintained engine, creates isolated pinned snapshots, applies the patch to
one, and installs the same benchmark. Source, patch, harness, fixture and lock drift is rejected.
Existing measurement reports are not overwritten. To repeat on the same binaries, archive that
output directory's `run-*.json`, `run-*.log` and `analysis.json` in a subdirectory, then rerun
`--run-only`; retain both complete passes. That is how the published repeat was obtained.

## Remaining merge gate

On the actual older Apple TV, compare baseline and patched Release builds with representative FEL
MMR titles and sustained playback. Record the device/model/OS, route, GPU time, frame deadlines,
drops, seek recovery and any fallback behavior under decode/render load. Check that the intended
picture path remains usable within its budget; if it does not, optimize the exact arithmetic before
merging. The Mac ratio must not be extrapolated into an Apple TV frame-rate or headroom claim.
The PR stays draft pending that device evidence.
