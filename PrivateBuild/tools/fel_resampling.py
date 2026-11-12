#!/usr/bin/env python3
"""Reproduce the opt-in GPU luma comparison. Requires NumPy, Xcode and a Metal Mac.

python3 PrivateBuild/tools/fel_resampling.py --directory PrivateBuild/.build/fel-resampling
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

import numpy as np
from fel_resampling_reference import bilinear, annexb, reconstruct_identity

ROOT = Path(__file__).resolve().parents[2]
PATTERNS = ['constant', 'ramp', 'horizontal_low', 'horizontal_mid', 'horizontal_high',
            'vertical_high', 'diagonal', 'zone_plate', 'band_limited', 'vertical_edge',
            'horizontal_edge', 'diagonal_edge', 'glyphs', 'pulse']


def signal(name, x, y, width, height, motion):
    x, y = x - motion, y - motion * 0.6
    if name == 'constant': return np.zeros(np.broadcast_shapes(x.shape, y.shape)) + 640
    if name == 'ramp': return 128 + 768 * (0.65 * x / (width - 1) + 0.35 * y / (height - 1))
    frequencies = {'horizontal_low': (0.025, 0), 'horizontal_mid': (0.10, 0),
                   'horizontal_high': (0.22, 0), 'vertical_high': (0, 0.22), 'diagonal': (0.13, 0.13)}
    if name in frequencies:
        fx, fy = frequencies[name]
        return 512 + 300 * np.sin(2 * np.pi * (fx*x + fy*y) + 0.31)
    if name == 'zone_plate':
        return 512 + 260 * np.sin(np.pi * 0.22 * (x*x / width + y*y / height))
    if name == 'band_limited':
        result = np.zeros(np.broadcast_shapes(x.shape, y.shape)) + 512
        rng = np.random.default_rng(713)
        for _ in range(12):
            fx, fy = rng.uniform(-0.15, 0.15, 2)
            result += 24 * np.sin(2*np.pi*(fx*x+fy*y) + rng.uniform(0, 2*np.pi))
        return result
    if name == 'vertical_edge': return np.where(x < width*0.47, 192, 832) + y*0
    if name == 'horizontal_edge': return np.where(y < height*0.47, 192, 832) + x*0
    if name == 'diagonal_edge': return np.where(x + 0.7*y < width*0.53, 192, 832)
    if name == 'glyphs':
        # Repeated small E-shaped strokes, defined geometrically on the full-resolution plane.
        u, v = np.mod(x, 31), np.mod(y, 39)
        stroke = ((u >= 5) & (u < 8) & (v >= 5) & (v < 32)) | ((u >= 5) & (u < 24) &
                 (((v >= 5) & (v < 8)) | ((v >= 17) & (v < 20)) | ((v >= 29) & (v < 32))))
        return np.where(stroke, 832, 192)
    if name == 'pulse': return 512 + 350 * np.exp(-((x-width/2)**2+(y-height/2)**2)/2)
    raise ValueError(f'unknown pattern {name}')


def quantize(values):
    return np.clip(np.floor(values + 0.5), 0, 1023).astype('<u2')


def write420(handle, luma, chroma=512):
    height, width = luma.shape
    handle.write(np.asarray(luma, dtype='<u2').tobytes())
    plane = np.full(((height+1)//2, (width+1)//2), chroma, dtype='<u2')
    handle.write(plane.tobytes()); handle.write(plane.tobytes())


def prepare(directory):
    directory.mkdir(parents=True, exist_ok=True)
    entries = []
    def generate(name, w, h, ew, eh, frames, profile, measure, pattern=None, phase=None):
        entry = dict(name=name, width=w, height=h, elWidth=ew, elHeight=eh, frames=frames,
                     profile=profile, measure=measure, pattern=pattern, phase=phase)
        base = directory/name; base.mkdir(exist_ok=True)
        y, x = np.mgrid[:h, :w]
        ey, ex = np.mgrid[:eh, :ew]
        with (base/'bl.yuv').open('wb') as bl, (base/'el.yuv').open('wb') as el:
            for frame in range(frames):
                motion = frame * 0.375
                source = np.full((h,w), 512, dtype='<u2')
                if measure:
                    source = (64 + (x*3+y*5+(x*y % 31)) % 876).astype('<u2')
                    enhancement = (384 + (ex*7+ey*11+frame*13) % 256).astype('<u2')
                elif phase in ('centered','h_cosited'):
                    # Full-resolution sample sites are integer coordinates. The lower-resolution
                    # grid's sites are (2i+0.5,2j+0.5) or (2i,2j+0.5), respectively.
                    offset = 0.5 if phase == 'centered' else 0.0
                    enhancement = quantize(signal(pattern, 2*ex+offset, 2*ey+0.5, w,h,motion))
                    target = reconstruct_identity(quantize(signal(pattern,x,y,w,h,motion)))
                    (base/f'truth_{frame}.u16').write_bytes(np.asarray(target,dtype='<u2').tobytes())
                else:
                    rng = np.random.default_rng(117+frame)
                    enhancement = rng.integers(0,1024,(eh,ew),dtype=np.uint16)
                    if pattern == 'saturated_edge': enhancement = np.where(ex < ew//2,0,1023).astype('<u2')
                write420(bl, source); write420(el, enhancement)
        entry['blSHA256'] = hashlib.sha256((base/'bl.yuv').read_bytes()).hexdigest()
        entry['elSHA256'] = hashlib.sha256((base/'el.yuv').read_bytes()).hexdigest()
        entries.append(entry)
    for phase in ('centered','h_cosited'):
        for pattern in PATTERNS:
            generate(f'{phase}_{pattern}',640,360,320,180,4,'identity',False,pattern,phase)
    for name,w,h,ew,eh in [('odd',641,361,321,181),('same_size',320,180,320,180),
                          ('fallback_ratio',480,270,320,180),('saturated_edge',128,64,64,32)]:
        generate(name,w,h,ew,eh,1,'identity',False,name,'conformance')
    for name,w,h,profile in [('1080p_identity',1920,1080,'identity'),('4k_identity',3840,2160,'identity'),
                            ('4k_mmr',3840,2160,'fel_fixture')]:
        generate(name,w,h,w//2,h//2,2,profile,True)
    manifest = dict(warmupPairs=10,samplePairs=60,entries=entries)
    (directory/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    return manifest


def metrics(output, truth):
    error = output.astype(np.float64)-truth
    return dict(mae=float(np.mean(np.abs(error))),rmse=float(np.sqrt(np.mean(error**2))),
                bias=float(np.mean(error)),p99=float(np.quantile(np.abs(error),0.99)),
                maximum=int(np.max(np.abs(error))),withinOne=float(np.mean(np.abs(error)<=1)),
                excursion=max(0,int(output.max())-int(truth.max()),int(truth.min())-int(output.min())))


def summarize_timing(samples):
    by_variant = {v: [s for s in samples if s['variant'] == v] for v in ('bilinear', 'annexb')}
    result = {}
    for variant, values in by_variant.items():
        result[variant] = {field: dict(median=float(np.median([s[field] for s in values])),
                                      p95=float(np.quantile([s[field] for s in values], 0.95)))
                           for field in ('gpuMs', 'wallMs')}
    pairs = {}
    for sample in samples:
        pairs.setdefault(sample['pair'], {})[sample['variant']] = sample
    result['pairedGpuDeltaMs'] = float(np.median([p['annexb']['gpuMs']-p['bilinear']['gpuMs'] for p in pairs.values()]))
    result['pairedGpuOverheadPercent'] = float(np.median([
        100*(p['annexb']['gpuMs']/p['bilinear']['gpuMs']-1) for p in pairs.values()]))
    return result


def validate_reports(manifest, manifest_hash, reports):
    if not reports: raise ValueError('no comparison-run-N.json measurements')
    entries = {e['name']: e for e in manifest['entries']}
    for report in reports:
        if report['schemaVersion'] != 1 or report['manifestSHA256'] != manifest_hash:
            raise ValueError('measurement schema or manifest hash mismatch')
        if report['samplePairs'] != manifest['samplePairs'] or report['warmupPairs'] != manifest['warmupPairs']:
            raise ValueError('measurement repetition count mismatch')
        results = report['results']
        if len(results) != len(entries) or {r['entry']['name'] for r in results} != set(entries):
            raise ValueError('measurement entries mismatch')
        for result in results:
            entry = entries[result['entry']['name']]
            required = ('name','width','height','elWidth','elHeight','frames','profile','measure','blSHA256','elSHA256')
            if any(result['entry'].get(k) != entry[k] for k in required) or not result['chromaIdentical']:
                raise ValueError('measurement input mismatch or chroma changed')
            samples = result['samples']
            expected = {(p,v) for p in range(manifest['samplePairs']) for v in ('bilinear','annexb')} if entry['measure'] else set()
            if len(samples) != len(expected) or {(s['pair'],s['variant']) for s in samples} != expected:
                raise ValueError('missing or duplicate timing samples')
            for pair in range(manifest['samplePairs'] if entry['measure'] else 0):
                ab = [s for s in samples if s['pair'] == pair]
                if {s['position'] for s in ab} != {0,1} or len({s['frame'] for s in ab}) != 1:
                    raise ValueError('timing samples are not paired on the same frame')
            for sample in samples:
                if any(not np.isfinite(sample[k]) or sample[k] <= 0 for k in ('gpuMs','wallMs')):
                    raise ValueError('invalid timing value')


def analyze(directory):
    manifest_data=(directory/'manifest.json').read_bytes()
    manifest=json.loads(manifest_data)
    reports=[json.loads(p.read_text()) for p in sorted(directory.glob('comparison-run-*.json'))]
    validate_reports(manifest,hashlib.sha256(manifest_data).hexdigest(),reports)
    accuracy=[]; conformance=[]; performance=[]
    for e in manifest['entries']:
        base=directory/e['name']; w,h=e['width'],e['height']; ew,eh=e['elWidth'],e['elHeight']
        for plane in ('bl','el'):
            if hashlib.sha256((base/f'{plane}.yuv').read_bytes()).hexdigest() != e[f'{plane}SHA256']:
                raise ValueError(f'input changed: {e["name"]}/{plane}')
        if e['measure']:
            runs=[summarize_timing(next(r for r in report['results'] if r['entry']['name']==e['name'])['samples']) for report in reports]
            performance.append(dict(name=e['name'],runs=runs))
            continue
        elwords=np.fromfile(base/'el.yuv',dtype='<u2')
        stride=ew*eh+2*((ew+1)//2)*((eh+1)//2)
        outputs={}; outputs10={}
        for variant, model in [('bilinear',bilinear),('annexb',annexb)]:
            outputs[variant]=[]; outputs10[variant]=[]; mismatch=0; largest=0
            for frame in range(e['frames']):
                el=elwords[frame*stride:frame*stride+ew*eh].reshape(eh,ew)
                expected=reconstruct_identity(model(el,w,h))
                actual=np.fromfile(base/f'gpu_{variant}_{frame}.u16',dtype='<u2').reshape(h,w)
                actual10=np.fromfile(base/f'gpu10_{variant}_{frame}.u16',dtype='<u2').reshape(h,w)
                if not np.array_equal(actual10,np.clip((actual.astype(np.int32)+2)//4,64,940)):
                    raise ValueError(f'10-bit capture does not match raw reconstruction: {e["name"]}/{variant}')
                delta=np.abs(actual.astype(np.int32)-expected)
                mismatch+=int(np.count_nonzero(delta));largest=max(largest,int(delta.max()))
                outputs[variant].append(actual); outputs10[variant].append(actual10)
            half = w in (2*ew,2*ew-1) and h in (2*eh,2*eh-1)
            exact = (variant == 'annexb' and half) or (w in (ew,2*ew) and h in (eh,2*eh))
            tolerance = 0 if exact else 1  # Non-binary bilinear weights can differ with GPU float contraction.
            conformance.append(dict(name=e['name'],variant=variant,mismatches=mismatch,maxError=largest,
                                    tolerance=tolerance,samples=e['frames']*w*h))
            if largest > tolerance: raise ValueError(f'GPU/reference mismatch: {e["name"]}/{variant}, max {largest}')
        if e['phase'] == 'conformance': continue
        truths=np.stack([np.fromfile(base/f'truth_{f}.u16',dtype='<u2').reshape(h,w) for f in range(e['frames'])])
        y,x=np.mgrid[:h,:w]
        for frame,truth in enumerate(truths):
            expected_truth=reconstruct_identity(quantize(signal(e['pattern'],x,y,w,h,frame*0.375)))
            if not np.array_equal(truth,expected_truth):
                raise ValueError(f'analytic truth changed: {e["name"]}/{frame}')
        row=dict(name=e['name'],phase=e['phase'],pattern=e['pattern'],variants={})
        for variant in outputs:
            out=np.stack(outputs[variant]); inner=out[:,16:-16,16:-16]; truth=truths[:,16:-16,16:-16]
            stats=metrics(inner,truth)
            stats['fullFrame']=metrics(out,truths)
            stats['output10']=metrics(np.stack(outputs10[variant])[:,16:-16,16:-16],np.clip((truth+2)//4,64,940))
            stats['temporalErrorRmse']=float(np.sqrt(np.mean((np.diff(inner.astype(float)-truth,axis=0))**2)))
            row['variants'][variant]=stats
        accuracy.append(row)
    result=dict(schema=1,source='analytic synthetic residuals; not encoded title/master pairs',
                sourcePhases=['centered','horizontal co-sited, vertical centered'],
                cropBorder=16,manifest=manifest,accuracy=accuracy,conformance=conformance,
                performance=performance,runs=reports,provenance=json.loads((directory/'provenance.json').read_text()),
                analysisSources={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in
                                 (Path(__file__),Path(__file__).with_name('fel_resampling_reference.py'))})
    (directory/'analysis.json').write_text(json.dumps(result,indent=2)+'\n')
    return result


def provenance():
    engine=ROOT/'PrivateBuild/.build/AetherEngine'
    source_files=[Path(__file__),Path(__file__).with_name('fel_resampling_reference.py'),
                  engine/'Tests/AetherEngineTests/DoviLumaResamplingComparisonTests.swift',
                  *sorted((engine/'Sources/AetherEngine/Video/Metal').glob('Dovi*.swift'))]
    return dict(python=os.sys.version,numpy=np.__version__,
                swift=subprocess.check_output(['swift','--version'],text=True).strip(),
                engineBase=subprocess.check_output(['git','-C',str(engine),'rev-parse','HEAD'],text=True).strip(),
                sources={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in source_files})


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,required=True)
    parser.add_argument('--runs',type=int,default=3)
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--prepare-only',action='store_true')
    mode.add_argument('--analyze-only',action='store_true')
    args=parser.parse_args(); directory=args.directory.resolve()
    if not 1 <= args.runs <= 10: parser.error('--runs must be in 1...10')
    if not args.analyze_only:
        if list(directory.glob('comparison-run-*.json')):
            parser.error('directory already contains measurements; use a fresh directory or --analyze-only')
        prepare(directory)
    if args.prepare_only: return
    if not args.analyze_only:
        (directory/'provenance.json').write_text(json.dumps(provenance(),indent=2)+'\n')
        env=dict(os.environ,AETHER_LUMA_COMPARISON_DIR=str(directory))
        for run in range(1,args.runs+1):
            (directory/'comparison.json').unlink(missing_ok=True)
            print(f'Measurement run {run}/{args.runs}',flush=True)
            command=['swift','test','--package-path',str(ROOT/'PrivateBuild/.build/AetherEngine'),
                     '--enable-all-traits','--filter','DoviLumaResamplingComparisonTests']
            if run > 1: command.append('--skip-build')
            with (directory/f'run-{run}.log').open('w') as log:
                subprocess.run(command,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
            (directory/f'comparison-run-{run}.json').write_bytes((directory/'comparison.json').read_bytes())
    analyze(directory)
    print(f'Wrote {directory / "analysis.json"}')

if __name__ == '__main__': main()
