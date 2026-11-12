"""Small guards for the separate, opt-in Release performance experiment."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import fel_performance as perf


class FelPerformanceTests(unittest.TestCase):
    def test_baseline_definition_preserves_default_and_hashes_supplied_patch(self):
        data, metadata = perf.baseline_config(None)
        self.assertIsNone(data)
        self.assertIsNone(metadata['baselinePatchSHA256'])
        self.assertEqual(metadata['baselineDescription'], 'Unpatched pinned upstream')
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'before.patch'
            path.write_bytes(b'exact saved patch\n')
            data, metadata = perf.baseline_config(path)
            self.assertEqual(data, b'exact saved patch\n')
            self.assertEqual(metadata['baselinePatchSHA256'], perf.sha(data))
            self.assertIn('supplied baseline patch: before.patch', metadata['baselineDescription'])
            path.write_bytes(b'\n')
            with self.assertRaisesRegex(ValueError, 'empty'):
                perf.baseline_config(path)

    def report(self):
        results = []
        for width, height, profile, route in sorted(perf.CASES):
            inplace = route == 'in_place_async'
            results.append(dict(width=width, height=height, profile=profile, route=route,
                                calls=70, inPlaceLostFrames=0, inPlaceComposedFrames=70 if inplace else 0,
                                hasOutputPool=not inplace, flags=0, lumaNlqScale=2048,
                                cbMmr=profile == 'fel_fixture', crMmr=profile == 'fel_fixture',
                                inputSHA256=['frame0', 'frame1'], outputSHA256='output',
                                samples=[dict(index=i, frame=i % 2, gpuMs=1+i/100,
                                              completionMs=2+i/100, thermalState=0) for i in range(60)]))
        return dict(schemaVersion=1, configuration='Release', warmups=10, sampleCount=60,
                    device='test device', operatingSystem='test OS', results=results)

    def test_trial_order_balances_first_and_second_positions(self):
        order = perf.run_order()
        self.assertEqual([variant for _, variant in order],
                         ['baseline', 'patched', 'patched', 'baseline', 'baseline', 'patched', 'patched', 'baseline'])
        for variant in perf.VARIANTS:
            self.assertEqual(sum(order[i][1] == variant for i in range(0, 8, 2)), 2)

    def test_percentile_interpolates(self):
        self.assertEqual(perf.percentile([30, 0, 20, 10], .5), 15)
        self.assertAlmostEqual(perf.percentile([30, 0, 20, 10], .95), 28.5)

    def test_dependency_lock_is_hashed_even_if_not_tracked(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory/'Package.swift').write_bytes(b'package')
            (directory/'Package.resolved').write_bytes(b'locked versions')
            with patch.object(perf, 'git', return_value='Package.swift'):
                self.assertEqual(perf.sources(directory)['Package.resolved'], perf.sha(b'locked versions'))

    def test_fixture_extraction_requires_unique_ordered_anchors(self):
        self.assertEqual(perf.between('header BEGIN exact helper END tail', 'BEGIN', 'END'), 'BEGIN exact helper ')
        for source in ('BEGIN helper', 'BEGIN BEGIN END', 'END BEGIN'):
            with self.subTest(source=source), self.assertRaises(ValueError):
                perf.between(source, 'BEGIN', 'END')

    def test_release_test_selection_preserves_production_manifest_and_sources(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            folder = root/perf.TEST.parent; folder.mkdir(parents=True)
            fixtures = {
                'DoviGpuCurveSweepTests': '    static func makeBuffer(\nBUFFER\n    static func readPlane(\n    static func identityCurve()\nIDENTITY\n    // MARK: - Luma:',
                'DoviGpuEdgeCaseTests': '    static func nlq(\nNLQ\n    /// Constant-field compose',
                'DoviFelInPlaceComposeTests': '    private static func noise(\nNOISE\n    private static func polynomial(\n    static func visibleWords(\nWORDS\n    private struct Box',
                'DoviComposedFrameRpuTests': "    /// dovi_tool's\nRPU\n    // MARK: - Production path",
            }
            for name, text in fixtures.items(): (folder/f'{name}.swift').write_text(text)
            original = 'production target unchanged\n            path: "Tests/AetherEngineTests"\n'
            (root/'Package.swift').write_text(original)
            metadata = perf.install_benchmark(root)
            self.assertEqual(metadata['packageBeforeSHA256'], perf.sha(original.encode()))
            self.assertIn('production target unchanged', (root/'Package.swift').read_text())
            for name in perf.TEST_SOURCES: self.assertIn(name, (root/'Package.swift').read_text())
            for name, text in fixtures.items(): self.assertEqual((folder/f'{name}.swift').read_text(), text)
            for body in ('BUFFER', 'IDENTITY', 'NLQ', 'NOISE', 'WORDS', 'RPU'):
                self.assertIn(body, (root/perf.FIXTURES).read_text())

    def test_valid_production_routes(self):
        perf.validate_report(self.report())

    def test_bad_counts_modes_durations_and_calibration_are_rejected(self):
        for change in ('debug', 'case', 'calls', 'lost', 'fallback', 'residual', 'mmr', 'samples', 'frame', 'nan', 'zero', 'output', 'input'):
            report = self.report()
            result = report['results'][0]
            if change == 'debug': report['configuration'] = 'DEBUG'
            elif change == 'case': report['results'].pop()
            elif change == 'calls': result['calls'] = 120
            elif change == 'lost': result['inPlaceLostFrames'] = 1
            elif change == 'fallback': result['hasOutputPool'] = not result['hasOutputPool']
            elif change == 'residual': result['lumaNlqScale'] = 0
            elif change == 'mmr':
                next(r for r in report['results'] if r['profile'] == 'fel_fixture')['cbMmr'] = False
            elif change == 'samples': result['samples'].pop()
            elif change == 'frame': result['samples'][0]['frame'] = 1
            elif change == 'nan': result['samples'][0]['gpuMs'] = float('nan')
            elif change == 'zero': result['samples'][0]['completionMs'] = 0
            elif change == 'output': result['outputSHA256'] = 'different'
            elif change == 'input': result['inputSHA256'] = ['different']
            with self.subTest(change=change), self.assertRaises(ValueError):
                perf.validate_report(report)

    def test_arithmetic_changes_need_not_preserve_baseline_output(self):
        baseline = self.report()
        patched = deepcopy(baseline)
        for result in patched['results']: result['outputSHA256'] = 'different-but-calibrated'
        perf.validate_report(baseline)
        perf.validate_report(patched)

    def test_analysis_compares_matched_round_medians(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for round_, variant in perf.run_order():
                report = self.report()
                if variant == 'patched':
                    for result in report['results']:
                        result['outputSHA256'] = 'changed arithmetic'
                        for sample in result['samples']:
                            sample['gpuMs'] *= 1.1
                            sample['completionMs'] *= 1.2
                (directory/f'run-{round_}-{variant}.json').write_text(json.dumps(report))
            with patch.object(perf, 'verify', return_value={}):
                result = perf.analyze(directory)
            self.assertEqual(len(result['comparisons']), 8)
            for case in result['comparisons']:
                for delta in case['roundMedianChangePercent']['gpuMs']: self.assertAlmostEqual(delta, 10)
                for delta in case['roundMedianChangePercent']['completionMs']: self.assertAlmostEqual(delta, 20)

    def test_per_frame_calibration_detects_a_changed_first_frame(self):
        report = self.report()
        for result in report['results']: result['outputSHA256ByFrame'] = ['first', result['outputSHA256']]
        perf.validate_report(report)
        report['results'][0]['outputSHA256ByFrame'][0] = 'changed first frame'
        with self.assertRaisesRegex(ValueError, 'per-frame output calibration'):
            perf.validate_report(report)

    def test_outputs_match_records_both_frames_without_rejecting_engine_differences(self):
        for mode, expected in (('same', True), ('different-first-frame', False), ('legacy', None)):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                for round_, variant in perf.run_order():
                    report = self.report()
                    if mode != 'legacy':
                        for result in report['results']:
                            first = variant if mode == 'different-first-frame' else 'same first frame'
                            result['outputSHA256ByFrame'] = [first, result['outputSHA256']]
                    (directory/f'run-{round_}-{variant}.json').write_text(json.dumps(report))
                with patch.object(perf, 'verify', return_value={}): result = perf.analyze(directory)
                self.assertTrue(all(case['outputsMatch'] is expected for case in result['comparisons']))


if __name__ == '__main__': unittest.main()
