#!/usr/bin/env python3
"""Export the Reference R SVG into the tvOS asset catalog; requires ImageMagick 7."""
import json
from pathlib import Path
import struct
import subprocess
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
CATALOG = HERE.parents[1] / 'Rivulet/PrivateAssets.xcassets'
BRAND = CATALOG / 'ReferencePlayer.brandassets'
NAVY = '#050E27'
INFO = {'author': 'xcode', 'version': 1}
ET.register_namespace('', 'http://www.w3.org/2000/svg')
source = ET.parse(HERE / 'reference-r.svg').getroot()
_, _, MARK_WIDTH, MARK_HEIGHT = map(float, source.attrib['viewBox'].split())
MARK = ET.tostring(source.find('{http://www.w3.org/2000/svg}g'), encoding='unicode')


def contents(folder, **values):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'Contents.json').write_text(json.dumps(dict(values, info=INFO), indent=2) + '\n')


def render(path, width, height, foreground=True, background=False, proportion=.64):
    scale = height * proportion / MARK_HEIGHT
    x, y = (width - MARK_WIDTH * scale) / 2, (height - MARK_HEIGHT * scale) / 2
    assert x >= 0 and y >= 0
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
    if background:
        svg += f'<rect width="{width}" height="{height}" fill="{NAVY}"/>'
    if foreground:
        svg += f'<g transform="translate({x} {y}) scale({scale})">{MARK}</g>'
    svg += '</svg>'
    subprocess.run(['magick', '-background', 'none', 'svg:-', '-colorspace', 'sRGB', '-depth', '8',
                    '-define', 'png:exclude-chunk=date,time', 'PNG32:' + str(path)], input=svg.encode(), check=True)
    header = path.read_bytes()[:26]
    assert header[:8] == b'\x89PNG\r\n\x1a\n'
    assert struct.unpack('>II', header[16:24]) == (width, height)
    assert header[24:26] == bytes([8, 6]), 'Expected 8-bit RGBA'
    opaque = subprocess.check_output(['magick', str(path), '-format', '%[opaque]', 'info:'], text=True).strip()
    assert (opaque == 'True') == background, f'Incorrect alpha in {path}'


def imageset(folder, width, height, scales=(1, 2), **render_options):
    images = [dict(filename=f'art@{s}x.png', idiom='tv', scale=f'{s}x') for s in scales]
    contents(folder, images=images)
    for image, scale in zip(images, scales):
        render(folder / image['filename'], width * scale, height * scale, **render_options)


def stack(name, width, height, scales):
    folder = BRAND / (name + '.imagestack')
    contents(folder, layers=[dict(filename=f'{name}.imagestacklayer') for name in ('Front', 'Back')])
    for layer in ('Front', 'Back'):
        layer_folder = folder / (layer + '.imagestacklayer')
        contents(layer_folder)
        imageset(layer_folder / 'Content.imageset', width, height, scales,
                 foreground=layer == 'Front', background=layer == 'Back')


def main():
    contents(CATALOG)
    contents(BRAND, assets=[
        dict(filename='App Icon.imagestack', idiom='tv', role='primary-app-icon', size='400x240'),
        dict(filename='App Store.imagestack', idiom='tv', role='primary-app-icon', size='1280x768'),
        dict(filename='Top Shelf.imageset', idiom='tv', role='top-shelf-image', size='1920x720'),
        dict(filename='Top Shelf Wide.imageset', idiom='tv', role='top-shelf-image-wide', size='2320x720'),
    ])
    stack('App Icon', 400, 240, (1, 2))
    stack('App Store', 1280, 768, (1,))
    imageset(BRAND / 'Top Shelf.imageset', 1920, 720, background=True, proportion=.5)
    imageset(BRAND / 'Top Shelf Wide.imageset', 2320, 720, background=True, proportion=.5)
    imageset(CATALOG / 'ReferencePlayerIconPreview.imageset', 400, 240, background=True)
    imageset(CATALOG / 'ReferencePlayerLaunchMark.imageset', 624, 520, proportion=1)
    print(f'Exported and checked Reference R assets in {CATALOG}')


if __name__ == '__main__':
    main()
