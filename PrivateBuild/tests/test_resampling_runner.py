"""Small statistical and failure-path checks for the optional FEL experiment."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

try:
    import numpy as np
except ModuleNotFoundError:
    np = None

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
if np is not None:
    import fel_resampling as runner


@unittest.skipIf(np is None, "optional FEL experiment requires NumPy")
class ResamplingRunnerTests(unittest.TestCase):
    def fixture(self):
        entry = dict(name="small", width=4, height=4, elWidth=2, elHeight=2,
                     frames=2, profile="identity", measure=True, blSHA256="base", elSHA256="enhancement")
        manifest = dict(samplePairs=4, warmupPairs=2, entries=[entry])
        samples = []
        for pair, (baseline, candidate) in enumerate(((1, 2), (100, 101), (2, 8), (200, 400))):
            for index, (variant, gpu) in enumerate((("bilinear", baseline), ("annexb", candidate))):
                samples.append(dict(pair=pair, frame=pair % 2, position=(index + pair) % 2,
                                    variant=variant, gpuMs=gpu, wallMs=gpu + 0.5, thermalState=0))
        report = dict(schemaVersion=1, manifestSHA256="manifest", samplePairs=4, warmupPairs=2,
                      results=[dict(entry=deepcopy(entry), chromaIdentical=True, samples=samples)])
        return manifest, report

    def test_paired_statistics_match_pairs_regardless_of_sample_order(self):
        manifest, report = self.fixture()
        runner.validate_reports(manifest, "manifest", [report])
        stats = runner.summarize_timing(list(reversed(report["results"][0]["samples"])))
        self.assertEqual(stats["bilinear"]["gpuMs"]["median"], 51)
        self.assertEqual(stats["annexb"]["wallMs"]["median"], 55)
        self.assertAlmostEqual(stats["bilinear"]["gpuMs"]["p95"], 185)
        self.assertEqual(stats["pairedGpuDeltaMs"], 3.5)
        # Median of paired percentages is 100%, unlike the ratio of independent medians.
        self.assertEqual(stats["pairedGpuOverheadPercent"], 100)

    def test_missing_duplicate_or_unknown_timing_samples_are_rejected(self):
        for change in ("missing", "duplicate", "unknown"):
            manifest, report = self.fixture()
            samples = report["results"][0]["samples"]
            if change == "missing": samples.pop()
            elif change == "duplicate": samples[-1] = deepcopy(samples[0])
            else: samples[0]["variant"] = "other"
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "missing or duplicate"):
                runner.validate_reports(manifest, "manifest", [report])

    def test_pair_must_use_one_frame_and_both_order_positions(self):
        for field, value in (("frame", 1), ("position", 1)):
            manifest, report = self.fixture()
            report["results"][0]["samples"][0][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "not paired"):
                runner.validate_reports(manifest, "manifest", [report])

    def test_invalid_durations_are_rejected(self):
        for field in ("gpuMs", "wallMs"):
            for value in (0, -1, float("nan"), float("inf")):
                manifest, report = self.fixture()
                report["results"][0]["samples"][0][field] = value
                with self.subTest(field=field, value=value), self.assertRaisesRegex(ValueError, "invalid timing"):
                    runner.validate_reports(manifest, "manifest", [report])

    def test_report_identity_and_unchanged_chroma_are_required(self):
        for change in ("hash", "schema", "count", "entry", "input", "chroma", "empty"):
            manifest, report = self.fixture()
            reports = [report]
            if change == "hash": report["manifestSHA256"] = "different"
            elif change == "schema": report["schemaVersion"] = 2
            elif change == "count": report["samplePairs"] = 3
            elif change == "entry": report["results"][0]["entry"]["name"] = "different"
            elif change == "input": report["results"][0]["entry"]["elSHA256"] = "different"
            elif change == "chroma": report["results"][0]["chromaIdentical"] = False
            else: reports = []
            with self.subTest(change=change), self.assertRaises(ValueError):
                runner.validate_reports(manifest, "manifest", reports)

    def test_untimed_entries_require_no_timing_samples(self):
        manifest, report = self.fixture()
        manifest["entries"][0]["measure"] = False
        report["results"][0]["entry"]["measure"] = False
        with self.assertRaisesRegex(ValueError, "missing or duplicate"):
            runner.validate_reports(manifest, "manifest", [report])
        report["results"][0]["samples"] = []
        runner.validate_reports(manifest, "manifest", [report])

    def test_metrics_preserve_signed_error_and_measure_range_excursion(self):
        stats = runner.metrics(np.array([8, 20, 31, 45], dtype=np.uint16), np.array([10, 20, 30, 40]))
        self.assertEqual(stats["mae"], 2)
        self.assertAlmostEqual(stats["rmse"], 7.5 ** 0.5)
        self.assertEqual(stats["bias"], 1)
        self.assertAlmostEqual(stats["p99"], 4.91)
        self.assertEqual(stats["maximum"], 5)
        self.assertEqual(stats["withinOne"], 0.5)
        self.assertEqual(stats["excursion"], 5)
        np.testing.assert_array_equal(runner.quantize(np.array([-1, 0.49, 0.5, 1.5, 1023.5])),
                                      [0, 0, 1, 2, 1023])

    def capture_fixture(self, directory):
        manifest, report = self.fixture()
        entry = manifest["entries"][0]
        entry.update(frames=1, measure=False, phase="conformance", pattern="constant")
        base = directory / entry["name"]
        base.mkdir()
        for name, shape in (("bl", (4, 4)), ("el", (2, 2))):
            path = base / f"{name}.yuv"
            with path.open("wb") as handle:
                runner.write420(handle, np.full(shape, 512, dtype=np.uint16))
            entry[f"{name}SHA256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        for variant in ("bilinear", "annexb"):
            (base / f"gpu_{variant}_0.u16").write_bytes(np.full((4, 4), 2048, dtype="<u2").tobytes())
            (base / f"gpu10_{variant}_0.u16").write_bytes(np.full((4, 4), 512, dtype="<u2").tobytes())
        data = json.dumps(manifest).encode()
        (directory / "manifest.json").write_bytes(data)
        report["manifestSHA256"] = hashlib.sha256(data).hexdigest()
        report["results"][0].update(entry={k: v for k, v in entry.items() if k not in ("phase", "pattern")}, samples=[])
        (directory / "comparison-run-1.json").write_text(json.dumps(report))
        (directory / "provenance.json").write_text("{}")
        return base

    def test_analysis_checks_input_hash_raw_conformance_and_output_rounding(self):
        for change in (None, "input", "raw", "output10"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                base = self.capture_fixture(directory)
                if change == "input":
                    with (base / "bl.yuv").open("ab") as handle:
                        handle.write(b"\0\0")
                elif change:
                    name = "gpu_bilinear_0.u16" if change == "raw" else "gpu10_bilinear_0.u16"
                    path = base / name
                    codes = np.fromfile(path, dtype="<u2")
                    codes[0] += 1
                    path.write_bytes(codes.tobytes())
                if change is None:
                    result = runner.analyze(directory)
                    self.assertEqual([row["mismatches"] for row in result["conformance"]], [0, 0])
                else:
                    with self.assertRaises(ValueError):
                        runner.analyze(directory)


if __name__ == "__main__":
    unittest.main()
