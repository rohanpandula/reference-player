#!/usr/bin/env python3
"""Release FEL baseline comparison, using only isolated engine checkouts.

Run --prepare-only, --build-only, then --run-only separately to keep builds away
from measurements. --analyze-only rereads the recorded samples. Default runs all.
The Swift harness uses unchanged production async paths and their existing GPU
mean. No engine source instrumentation, DEBUG defines or shader changes are added.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
HARNESS = Path(__file__).with_name('ReferencePlayerFelPerformanceTests.swift')
TEST = Path('Tests/AetherEngineTests') / HARNESS.name
FIXTURES = TEST.with_name('ReferencePlayerFelPerformanceFixtures.swift')
TEST_SOURCES = [HARNESS.name, FIXTURES.name, 'Support/OffCooperativePool.swift']
COMPOSER = Path('Sources/AetherEngine/Video/Metal/DoviFelComposer.swift')
PATCH = ROOT / 'PrivateBuild/engine-patches/0001-fel-accuracy.patch'
VARIANTS = ('baseline', 'patched')
ROUNDS = 4
CASES = {(w, h, p, r) for w, h in ((1920, 1080), (3840, 2160))
         for p in ('identity', 'fel_fixture') for r in ('pool_async', 'in_place_async')}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def git(directory, *args):
    return subprocess.check_output(['git', '-c', 'core.fsmonitor=false', '-C', str(directory), *args], text=True).strip()


def sources(directory):
    names = set(git(directory, 'ls-files', 'Sources', 'Tests', 'Package.swift', 'Package.resolved').splitlines())
    names.add('Package.resolved')  # Include the actual lock even if a future upstream ignores it.
    return {name: sha((directory/name).read_bytes()) for name in sorted(names)}


def between(source, start, end):
    if source.count(start) != 1 or source.count(end) != 1:
        raise ValueError('Fixture source anchors changed; review the benchmark extraction')
    first, last = source.index(start), source.index(end)
    if last <= first: raise ValueError('Fixture source anchors are reversed')
    return source[first:last]


def install_benchmark(checkout):
    # Upstream's general test suite uses DEBUG-only seams and does not compile
    # in Release. Select only this benchmark and verbatim fixture helpers; the
    # production target, optimization settings and every Sources file stay intact.
    folder = checkout/TEST.parent
    specs = [
        ('DoviGpuCurveSweepTests', '', [('    static func makeBuffer(', '    static func readPlane('),
                                      ('    static func identityCurve()', '    // MARK: - Luma:')]),
        ('DoviGpuEdgeCaseTests', '    typealias H = DoviGpuCurveSweepTests\n',
         [('    static func nlq(', '    /// Constant-field compose')]),
        ('DoviFelInPlaceComposeTests', '    typealias H = DoviGpuCurveSweepTests\n',
         [('    private static func noise(', '    private static func polynomial('),
          ('    static func visibleWords(', '    private struct Box')]),
        ('DoviComposedFrameRpuTests', '', [("    /// dovi_tool's", '    // MARK: - Production path')]),
    ]
    fixture = ('// Verbatim helpers from the pinned AetherEngine tests; original licence and fixture attribution apply.\n'
               '#if DualLayerDolbyVision\nimport Foundation\nimport CoreVideo\n@testable import AetherEngine\n')
    for name, prefix, anchors in specs:
        source = (folder/f'{name}.swift').read_text()
        fixture += f'struct {name} {{\n' + prefix + ''.join(between(source, start, end) for start, end in anchors) + '}\n'
    fixture += '#endif\n'
    package = checkout/'Package.swift'
    original = package.read_text()
    anchor = '            path: "Tests/AetherEngineTests"\n'
    if original.count(anchor) != 1: raise ValueError('Benchmark test target manifest anchor changed')
    selected = original.replace(anchor, anchor.rstrip('\n')+',\n            sources: '+json.dumps(TEST_SOURCES)+'\n')
    package.write_text(selected)
    (checkout/FIXTURES).write_text(fixture)
    shutil.copyfile(HARNESS, checkout/TEST)
    return dict(packageBeforeSHA256=sha(original.encode()), packageAfterSHA256=sha(selected.encode()),
                fixtureSHA256=sha(fixture.encode()), sources=TEST_SOURCES)


def prepare(directory):
    if directory.exists():
        raise ValueError('Use a new directory for preparation; existing snapshots are never overwritten')
    subprocess.run([sys.executable, str(ROOT/'PrivateBuild/engine.py'), '--check'], check=True)
    pin = json.loads((ROOT/'PrivateBuild/UPSTREAM.json').read_text())['engine']
    original = ROOT/'PrivateBuild/.build/AetherEngine'
    directory.mkdir(parents=True)
    metadata = dict(schemaVersion=1, engineBase=pin['revision'], engineVersion=pin['version'],
                    patchSHA256=sha(PATCH.read_bytes()), harnessSHA256=sha(HARNESS.read_bytes()),
                    python=sys.version, swift=subprocess.check_output(['swift', '--version'], text=True).strip(),
                    rounds=ROUNDS, instrumentation='none; identical Release benchmark with test-only fixture extraction/source selection',
                    testTargetSelection={}, variants={})
    for variant in VARIANTS:
        checkout = directory/variant
        subprocess.run(['git', 'clone', '--no-hardlinks', '--no-checkout', str(original), str(checkout)], check=True)
        subprocess.run(['git', '-C', str(checkout), 'checkout', '--detach', pin['revision']], check=True)
        if variant == 'patched':
            subprocess.run(['git', '-C', str(checkout), 'apply', '--index', str(PATCH)], check=True)
        source = (checkout/COMPOSER).read_text()
        # The cumulative mean is only usable before its first completed window.
        for required in ('private static let gpuTimeWindow = 120',
                         'if let gpuTimeMean { return gpuTimeMean }',
                         'if gpuTimeCount > 0 { return gpuTimeAccum / Double(gpuTimeCount) }',
                         'composer?.recordGPUTime(ms: gpuMs)'):
            if required not in source:
                raise ValueError('Production timing contract changed; review the harness before benchmarking')
        metadata['testTargetSelection'][variant] = install_benchmark(checkout)
        metadata['variants'][variant] = sources(checkout)
    fixture_files = ['DoviFelInPlaceComposeTests.swift', 'DoviGpuCurveSweepTests.swift',
                     'DoviGpuEdgeCaseTests.swift', 'DoviComposedFrameRpuTests.swift']
    # Hashes record every source, including fixture helpers. Input hashes in the
    # reports prove that changed test files still generate identical samples.
    metadata['fixtureHelpers'] = fixture_files
    if (directory/'baseline/Package.resolved').read_bytes() != (directory/'patched/Package.resolved').read_bytes():
        raise ValueError('Baseline and patched dependency locks differ')
    (directory/'provenance.json').write_text(json.dumps(metadata, indent=2)+'\n')


def verify(directory):
    metadata = json.loads((directory/'provenance.json').read_text())
    if metadata['patchSHA256'] != sha(PATCH.read_bytes()):
        raise ValueError('Current engine patch differs from the snapshot; prepare and measure new snapshots')
    if metadata['harnessSHA256'] != sha(HARNESS.read_bytes()):
        raise ValueError('Harness changed after preparation; use new snapshots')
    for variant in VARIANTS:
        checkout = directory/variant
        if git(checkout, 'rev-parse', 'HEAD') != metadata['engineBase']:
            raise ValueError(f'{variant} engine revision changed')
        if sources(checkout) != metadata['variants'][variant]:
            raise ValueError(f'{variant} source or dependency lock changed after preparation')
        if set(git(checkout, 'ls-files', '--others', '--exclude-standard').splitlines()) != {str(TEST), str(FIXTURES)}:
            raise ValueError(f'{variant} contains unexpected untracked files')
        if sha((checkout/TEST).read_bytes()) != metadata['harnessSHA256']:
            raise ValueError(f'{variant} benchmark harness changed')
        if sha((checkout/FIXTURES).read_bytes()) != metadata['testTargetSelection'][variant]['fixtureSHA256']:
            raise ValueError(f'{variant} extracted fixtures changed')
    return metadata


def command(checkout):
    return ['swift', 'test', '--package-path', str(checkout), '-c', 'release', '--enable-all-traits',
            '--force-resolved-versions', '--filter', 'ReferencePlayerFelPerformanceTests']


def build(directory):
    verify(directory)
    env = dict(os.environ); env.pop('REFERENCE_FEL_PERFORMANCE_REPORT', None)
    for variant in VARIANTS:
        print(f'Building Release {variant}; measurements disabled', flush=True)
        with (directory/f'build-{variant}.log').open('w') as log:
            subprocess.run(command(directory/variant), env=env, stdout=log, stderr=subprocess.STDOUT, check=True)
    verify(directory)


def run_order():
    return [(round_+1, variant) for round_ in range(ROUNDS)
            for variant in (VARIANTS if round_ % 2 == 0 else VARIANTS[::-1])]


def measure(directory):
    verify(directory)
    if list(directory.glob('run-*.json')):
        raise ValueError('Measurements already exist; use --analyze-only or prepare a new directory')
    for round_, variant in run_order():
        report = directory/f'run-{round_}-{variant}.json'
        env = dict(os.environ, REFERENCE_FEL_PERFORMANCE_REPORT=str(report))
        print(f'Round {round_}/{ROUNDS}: Release {variant}', flush=True)
        with report.with_suffix('.log').open('w') as log:
            subprocess.run(command(directory/variant)+['--skip-build'], env=env, stdout=log, stderr=subprocess.STDOUT, check=True)
        if not report.is_file():
            raise ValueError(f'Benchmark skipped or produced no report: {variant}')
    verify(directory)


def percentile(values, quantile):
    values = sorted(values); position = (len(values)-1)*quantile
    low = int(position); high = min(low+1, len(values)-1)
    return values[low] + (values[high]-values[low])*(position-low)


def validate_report(report):
    if report['schemaVersion'] != 1 or report['configuration'] != 'Release' or report['warmups'] != 10 or report['sampleCount'] != 60:
        raise ValueError('Unexpected benchmark configuration')
    results = report['results']
    keys = [(r['width'], r['height'], r['profile'], r['route']) for r in results]
    if len(keys) != len(CASES) or set(keys) != CASES:
        raise ValueError('Missing, duplicate or unknown benchmark case')
    for result in results:
        inplace = result['route'] == 'in_place_async'
        if result['calls'] != 70 or result['inPlaceLostFrames'] != 0 or result['inPlaceComposedFrames'] != (70 if inplace else 0) or result['hasOutputPool'] == inplace:
            raise ValueError('Benchmark route failed or silently fell back')
        if result['flags'] != 0 or result['lumaNlqScale'] == 0:
            raise ValueError('Benchmark did not use production luma/residual')
        if result['profile'] == 'fel_fixture' and not (result['cbMmr'] and result['crMmr']):
            raise ValueError('FEL fixture did not exercise both chroma MMR paths')
        samples = result['samples']
        if len(samples) != 60 or [s['index'] for s in samples] != list(range(60)):
            raise ValueError('Missing or unordered benchmark samples')
        for sample in samples:
            if sample['frame'] != sample['index'] % 2:
                raise ValueError('Input frames do not follow the fixed schedule')
            if any(not 0 < sample[k] < float('inf') for k in ('gpuMs', 'completionMs')):
                raise ValueError('Invalid benchmark duration')
        peer = next(r for r in results if (r['width'], r['height'], r['profile']) == (result['width'], result['height'], result['profile']) and r['route'] != result['route'])
        if peer['outputSHA256'] != result['outputSHA256'] or peer['inputSHA256'] != result['inputSHA256']:
            raise ValueError('Pool/in-place fixture calibration differs within one engine')


def analyze(directory):
    metadata = verify(directory)
    reports = {(round_, variant): json.loads((directory/f'run-{round_}-{variant}.json').read_text())
               for round_, variant in run_order()}
    for report in reports.values(): validate_report(report)
    if len({(r['device'], r['operatingSystem']) for r in reports.values()}) != 1:
        raise ValueError('Benchmark runs used different devices or operating systems')
    comparisons = []
    for case in sorted(CASES):
        entries = {(round_, variant): next(r for r in report['results'] if (r['width'], r['height'], r['profile'], r['route']) == case)
                   for (round_, variant), report in reports.items()}
        if len({tuple(e['inputSHA256']) for e in entries.values()}) != 1:
            raise ValueError('Baseline and patched input frames differ')
        stats = {variant: {field: [dict(median=statistics.median(s[field] for s in entries[round_, variant]['samples']),
                                       p95=percentile([s[field] for s in entries[round_, variant]['samples']], .95))
                                  for round_ in range(1, ROUNDS+1)] for field in ('gpuMs', 'completionMs')}
                 for variant in VARIANTS}
        deltas = {field: [100*(stats['patched'][field][i]['median']/stats['baseline'][field][i]['median']-1)
                          for i in range(ROUNDS)] for field in ('gpuMs', 'completionMs')}
        comparisons.append(dict(width=case[0], height=case[1], profile=case[2], route=case[3], runs=stats,
                                roundMedianChangePercent=deltas))
    result = dict(schemaVersion=1, provenance=metadata, comparisons=comparisons,
                  comparisonMethod='matched crossover rounds of separate process runs; changes compare per-run medians, not paired command buffers',
                  reports=[dict(round=round_, variant=variant, report=reports[round_, variant]) for round_, variant in run_order()],
                  calibration='Pool/in-place output hashes compare only final sequence 69 (frame 1) within each engine, not every output frame and not different engines with intentionally different arithmetic.',
                  limitation='Mac single-frame composition comparison, not Apple TV playback or deadline validation. The RPU is parsed once per profile/dimensions and parameters are reused; RPU parsing and mapping-slot state handling are excluded. Per-compose LUT generation/upload, encoding and callback delivery are included in completion latency. Decode, rendering, contention and source restoration are excluded; the CPU delta covers composition, not the whole playback pipeline.')
    (directory/'analysis.json').write_text(json.dumps(result, indent=2)+'\n')
    print(f'Wrote {directory / "analysis.json"}')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    mode = parser.add_mutually_exclusive_group()
    for name in ('prepare', 'build', 'run', 'analyze'):
        mode.add_argument(f'--{name}-only', action='store_true')
    args = parser.parse_args(); directory = args.directory.resolve()
    try:
        if args.prepare_only: prepare(directory)
        elif args.build_only: build(directory)
        elif args.run_only: measure(directory); analyze(directory)
        elif args.analyze_only: analyze(directory)
        else:
            prepare(directory); build(directory); measure(directory); analyze(directory)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f'FAIL: {exc}\n')


if __name__ == '__main__': main()
