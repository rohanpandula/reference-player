# FEL luma resampling comparison

This experiment compares the **existing** centered bilinear luma path with the existing debug
Annex B path. It changes no playback default. The engine test is carried in Reference Player's
maintained patch; the generator, independent CPU model and analysis live here. No upstream engine
repository, package pin, app setting or runtime dependency changes.

## Result and default decision — 2026-11-12

**Keep centered bilinear as the shipping luma default.** Annex B is implemented consistently with
its independent integer model, but its accuracy benefit depends strongly on horizontal source
alignment and it introduces overshoot. The available evidence does not justify changing all FEL
playback to Annex B. Its current debug-only switch remains available for controlled experiments.

Measurements below are from **Apple M6, macOS 27.2 (26B123), Xcode 27.1 / Swift 6.4**, DEBUG,
all five engine traits, on the patched AetherEngine 7.61.0 baseline. Low Power Mode was off and all
recorded thermal states were nominal. These are Mac measurements, not Apple TV results.
[Full results, input/source hashes and all paired timings](measurements/fel-luma-2026-11-12.json)
are checked in; large generated frames and captures remain in the ignored output directory.

### Accuracy against the analytic source

Interior RMSE in raw 12-bit VDR codes (lower is better). Each column uses its stated source grid;
there is no aggregate score across incompatible phase assumptions.

| Pattern | Centered: bilinear | Centered: Annex B | H co-sited: bilinear | H co-sited: Annex B |
|---|---:|---:|---:|---:|
| constant | 0.000 | 0.000 | 0.000 | 0.000 |
| ramp | 0.354 | 0.661 | 0.624 | 0.611 |
| horizontal low | 2.007 | 16.686 | 16.794 | 0.977 |
| horizontal mid | 30.291 | 66.292 | 68.493 | 0.354 |
| horizontal high | 127.522 | 150.472 | 160.142 | 91.624 |
| vertical high | 127.770 | 109.802 | 127.770 | 109.802 |
| diagonal | 86.591 | 85.565 | 109.052 | 22.109 |
| zone plate | 76.019 | 76.446 | 92.026 | 39.667 |
| band limited | 10.535 | 15.514 | 17.347 | 2.427 |
| vertical edge | 12.963 | 13.498 | 12.963 | 13.498 |
| horizontal edge | 12.494 | 10.806 | 12.494 | 10.806 |
| diagonal edge | 14.521 | 17.007 | 16.659 | 15.006 |
| glyphs | 138.400 | 146.145 | 138.401 | 129.206 |
| pulse | 0.437 | 0.550 | 0.563 | 0.338 |

For example, horizontally co-sited mid-frequency detail improves from 68.493 to 0.354 codes RMSE,
while the centered version worsens from 30.291 to 66.292. The same direction survives 10-bit output:
17.172 → 0.224 and 7.590 → 16.662 codes respectively. Vertical high-frequency detail improves in
both groups because their vertical phase is the same. These are reconstruction-error measurements,
not a subjective sharpness ranking.

Annex B exceeds the synthetic source's reconstructed range by up to 64 codes on the vertical step,
45 on the horizontal step, 93 on the diagonal step and 132 on the geometric strokes; bilinear's
range excursion is zero on those fixtures. Negative lobes can preserve smooth detail and ring
around abrupt transitions. A pure step has no recoverable unique subpixel edge location after
point sampling; its result is not evidence that either filter inverts a real encoder.

The exact arithmetic paths matched **51,972,585/51,972,585** reconstructed luma samples:
all exact 2:1 and 1:1 bilinear cases and every eligible Annex B case, including odd-size crop and
saturation. Non-binary bilinear ratios had 682/231,401 one-code differences for odd dimensions
and 5,362/129,600 for the 3:2 fallback in each variant; maximum error was one raw 12-bit code.
These cases are reported with a separate tolerance, not counted as exact agreement. All 32 accuracy/conformance
entries’ undithered 10-bit captures matched rounding/clamping of the corresponding raw result. All 35 entries had
identical chroma between variants in every run.

A follow-up calculation using rational bilinear weights places all 3:2 discrepancies exactly at
mathematical half-code ties; the odd-size discrepancies are within 0.018 EL code of a half boundary.
That is consistent with floating-point division/coordinate evaluation and contraction, without
proving the compiler's exact instruction sequence. The two fallback variants are identical to one
another. Against the CPU model, 157 odd-size and 1,351 fallback samples still differ by one code
after 10-bit quantization; these paths are not claimed bit-exact.

### Composition cost

Ranges are the minimum and maximum of the **three per-run medians** (60 pairs each); they are not
confidence intervals. Extra time and percentage are medians of paired differences/ratios within
each run. Timing includes the unchanged chroma work, so this is the cost of whole composition,
not isolated luma filtering.

| Workload | Bilinear GPU ms | Annex B GPU ms | Extra GPU ms | Paired GPU increase |
|---|---:|---:|---:|---:|
| 1080p, identity | 0.167–0.225 | 0.350–0.496 | 0.192–0.261 | 114.1–119.8% |
| 4K, identity | 0.761–0.784 | 1.474–1.562 | 0.722–0.740 | 97.9–99.7% |
| 4K, real RPU / chroma MMR | 1.747–1.776 | 2.446–2.579 | 0.708–0.743 | 41.0–41.2% |

| Workload | Bilinear wall median ms | Annex B wall median ms | Bilinear GPU p95 ms | Annex B GPU p95 ms |
|---|---:|---:|---:|---:|
| 1080p, identity | 1.197–1.481 | 1.381–1.773 | 0.397–0.452 | 0.782–1.737 |
| 4K, identity | 1.830–2.109 | 2.587–2.905 | 1.144–1.653 | 2.355–3.255 |
| 4K, real RPU / chroma MMR | 3.747–4.041 | 4.648–4.878 | 2.201–2.837 | 3.143–4.037 |

The 1080p medians drift between runs and the tails are wider than the medians, despite nominal
thermal state. Paired measurements consistently show the added work; no single run is presented
as a universal timing. A synthetic frame with real RPU coefficients is still not an encoded-title
benchmark. These times do not establish end-to-end frame rate or HDMI behavior.

A default change needs source/master comparisons that establish the actual horizontal alignment
and filtering of representative FEL encodes, plus sustained on-device playback/seek and GPU-budget
checks on the supported Apple TVs, including the in-place route. No physical Apple TV was available
for this experiment, and no title/master pair was available. The device inventory contained an Apple TV
simulator; its disconnected state was previously mistaken for a paired physical device. This comparison is complete within
those measured limits; it does not claim certified or universally bit-exact Dolby output.

## What is being compared

[ETSI GS CCM 001 V1.1.1, Annex B.3](https://www.etsi.org/deliver/etsi_gs/CCM/001_099/001/01.01.01_60/gs_ccm001v010101p.pdf)
is an **informative**, non-normative reference resampler. At exact 2:1 enlargement:

- Bilinear samples EL coordinates `(x/2 − 0.25, y/2 − 0.25)` for output site `(x,y)`.
- Annex B luma samples horizontal phase `x/2`, with vertical phase `y/2 − 0.25`.
  It applies four vertical taps first, then eight horizontal taps at odd sites (even sites copy).
  Each stage rounds half up and clips to 0…65535; boundaries replicate the nearest sample.

The horizontal phases differ by **half an output pixel**. Choosing a filter also chooses a source
grid assumption. More taps alone do not establish better reconstruction. The existing NLQ lookup
clamps resampled EL codes to 0…1023, including Annex B overshoot. This experiment preserves that
behavior. For odd output sizes `2×EL−1`, Annex B crops its doubled grid; bilinear uses the actual
size ratio. Other ratios use bilinear in both variants. Chroma already uses its existing filter
and is compared for equality, not changed.

## Method

The accuracy inputs are analytically defined, point-sampled residual-code fields, quantized to
10 bits. They are **not encoder-downsampled HDR masters or decoded movie frames**. There are two
separate source grids: centered `(2i+0.5,2j+0.5)` and horizontally co-sited `(2i,2j+0.5)`. Ground
truth samples the same analytic function at full-resolution integer sites; it does not use either
upsampling filter. BL is constant 512, with identity mapping and active linear NLQ
(offset 512, scale 2048, shift 11, cap 512). All errors are in raw **12-bit reconstructed VDR codes**
unless explicitly marked 10-bit.

Four translated frames per pattern at 640×360 test constant/ramp, low/mid/high horizontal sine,
high vertical sine, diagonal sine, zone plate, seeded band-limited texture, three edge directions,
geometric strokes and a narrow pulse. Smooth sines stay below EL Nyquist. Edges, strokes and the
pulse contain out-of-band energy, so they expose aliasing and ringing rather than a conformance
requirement. Quality metrics exclude a 16-pixel border; full-frame and final undithered 10-bit
metrics are also retained. The four-frame temporal-error metric measures changes in numerical
error, not perceived flicker. Global range excursion is not a local halo metric.

Separate random/edge fixtures cover odd dimensions, 1:1, a non-2:1 fallback, saturation and
overshoot. The independent NumPy model checks both resampling and signed fixed-point identity
composition. Exact-ratio bilinear and eligible integer Annex B paths must match exactly.
Non-binary bilinear coordinates may differ by one code due to GPU floating-point evaluation;
these differences are reported separately. Undithered 10-bit captures must match the raw result's
rounding/clamp. Each run also checks that both variants produce identical chroma.

Performance uses prepared P010 buffers at 1080p and 4K, two deterministic textured frames per
case, and either identity mapping or the existing real FEL RPU fixture with both chroma MMR paths
active. Three separate runs each have ten warm-up pairs and sixty measured pairs per case. A/B
order is balanced for each frame across cycles. Timing measures the complete **blocking pool
composer** (luma plus unchanged chroma), using normal Bayer 8×8 output. GPU command-buffer and
synchronous wall times exclude file I/O, pixel readback, decode, rendering and the in-place
playback route. Captures occur outside the timed region. Quality analysis uses the last run's
captures; timings retain every run. No other builds or benchmark jobs were launched during timing.

## Reproduce

On a Metal-capable Mac with Xcode and NumPy available to Python 3:

```sh
python3 PrivateBuild/engine.py
python3 -m unittest discover -s PrivateBuild/tests -p 'test_resampling*.py'
python3 PrivateBuild/tools/fel_resampling.py --directory PrivateBuild/.build/fel-resampling
# Recalculate metrics from the same captures without remeasuring:
python3 PrivateBuild/tools/fel_resampling.py --directory PrivateBuild/.build/fel-resampling --analyze-only
```

Use a fresh output directory for a new measurement; the runner rejects existing run reports.
NumPy is an optional offline-tool dependency, not an app or build dependency. Without NumPy,
the Python experiment tests skip; normal app verification still works. The Swift test skips
unless `AETHER_LUMA_COMPARISON_DIR` is set. The runner sets it automatically and fails if no report
is produced. The harness validates input dimensions, byte counts, 10-bit codes and SHA-256 hashes;
the analyzer checks the manifest hash, paired sample coverage, input hashes and chroma equality,
and regenerates analytic truth to detect altered reference captures.
The measurement records toolchain, source hashes, device, OS, thermal state and raw paired times.
Do not run other GPU workloads alongside it.

The cleanroom corpus motivated inspecting the filters. The numerical reference and fixtures here
are independently implemented from the public standard and analytic functions; no decompiled
code or binary from that corpus is copied.
