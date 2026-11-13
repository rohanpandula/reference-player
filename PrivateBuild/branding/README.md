# Reference R

The tvOS mark is an ivory R with a transparent play cutout and an amber accent on navy.
`reference-r.svg` is the editable source; it uses paths, not a font.

![Reference R](../../Rivulet/PrivateAssets.xcassets/ReferencePlayerIconPreview.imageset/art@2x.png)

| Colour | sRGB |
| --- | --- |
| Ivory | `#FFF3DC` |
| Amber | `#FFB300` |
| Navy | `#050E27` |

From the repository root, regenerate the committed PNGs and catalog metadata with Python 3 and
ImageMagick 7 installed:

```sh
python3 PrivateBuild/branding/export.py
```

The exporter checks each PNG's dimensions, 8-bit RGBA format and opacity. ImageMagick is only an
artwork export tool; building the app uses the checked-in assets and needs no extra dependency.

`Rivulet/PrivateAssets.xcassets` contains the home icon (400×240 and 800×480), store icon
(1280×768), standard and wide static Top Shelf images, Settings preview, and transparent launch
mark. Each icon has an opaque navy back layer and a transparent front layer containing the entire
mark, so the amber accent stays attached during parallax. The mark occupies 64% of the icon's
height, leaving 18% above and below for focus movement. Top Shelf uses 50% height. Corners and
focus effects are left to tvOS; see Apple's [app icon guidance](https://developer.apple.com/design/human-interface-guidelines/app-icons)
and [image stack configuration](https://developer.apple.com/documentation/xcode/configuring-your-app-icon).

The tvOS target's SDK-specific app-icon setting selects `ReferencePlayer` in Debug and Release.
Two image lookups connect the Settings standard-icon preview and existing launch animation.
These three integration points are guarded by `PrivateBuild/verify.py`. Upstream artwork remains
in its original catalog, keeping upstream merges small. iOS/Mac icons, alternate icon choices,
saved preferences and dynamic Top Shelf content are unchanged.

Run `python3 PrivateBuild/check.py --all` after changing the integration. Inspect the exported
artwork and check the focused icon/parallax on a physical Apple TV before release.
