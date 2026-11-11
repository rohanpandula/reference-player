#!/usr/bin/env python3
"""Build the format index Reference Player reads, from a whole-library probe (library_probe.py).

Two files. `format-index.json` is the app's index: one entry per Dolby Vision Profile 7 file with
its enhancement-layer verdict from the RPU (FEL or MEL, and whether the layer is really in the
stream), keyed by its path under the library root and its size, and nothing else, so the served
file names only those titles. That verdict is the one thing the server cannot tell; audio is the
server's to name (Plex records each stream's own profile, Atmos and DTS:X included, checked against
this probe file by file). A dual-track file, whose enhancement layer is a second video track beside a
bare HDR10 base layer, is listed too and marked `enhancementLayerSeparateTrack`: the player uses
only its first track, so the app names the layer without calling the title Dolby Vision.
`format-index.tsv` is the whole library for people: codecs, resolution, frame rate, HDR10+ and
every audio track.

    MOVIES_ROOT=/path/to/Movies/ MEDIA_ROOT=/path/to/media/ python3 build_index.py <probe.jsonl> <out-dir> [files.tsv]

With the listing the probe ran from, a record for a file that is no longer on the share is left
out, so a deleted or renamed title does not linger. A movie's path is made relative to MOVIES_ROOT
(the app cuts a server path at its first /movies/); every other library's is made relative to
MEDIA_ROOT (TV/..., Demos/...), matching a server that mounts each library folder at its root (/tv).
Both roots are the paths as the probe's listing spells them; unset, a path is cut at its first
/Movies/ or kept whole. Serve format-index.json from any web server the Apple TV can reach and put
its address in Settings > Playback > Format Tags.
"""
import json, os, pathlib, sys, time

probe, out = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
present = None
if len(sys.argv) > 3:
    present = {line.split("\t", 2)[2] for line in pathlib.Path(sys.argv[3]).read_text(errors="replace").splitlines() if line.count("\t") >= 2}
out.mkdir(parents=True, exist_ok=True)
MOVIES_ROOT = os.environ.get("MOVIES_ROOT", "")   # no default: the tool describes nobody's share
MEDIA_ROOT = os.environ.get("MEDIA_ROOT", "")


def rel(path):
    for root in (MOVIES_ROOT, MEDIA_ROOT):
        if root and path.startswith(root):
            return path[len(root):]
    i = path.find("/Movies/")  # another server's layout: the app cuts at the first /movies/ too
    return path[i + 8:] if i >= 0 else path.lstrip("/")


def audio_kind(a):
    c, p = (a.get("codec") or "").lower(), (a.get("profile") or "")
    if c == "truehd": return "TrueHD Atmos" if "Atmos" in p else "TrueHD"
    if c == "eac3": return "DD+ Atmos" if "Atmos" in p else "DD+"
    if c == "dts": return "DTS:X" if "DTS:X" in p else "DTS-HD MA" if "MA" in p else "DTS-HD HRA" if "HRA" in p else "DTS"
    if c == "ac3": return "AC-3"
    return {"aac": "AAC", "flac": "FLAC", "opus": "Opus", "mp3": "MP3", "vorbis": "Vorbis"}.get(c, c.upper() or None)


# The probe appends; a re-probed path's newest record wins.
latest = {}
for line in open(probe):
    r = json.loads(line)
    latest[r["path"]] = r
entries = []
for r in latest.values():
    v = r.get("video")
    if r.get("error") or not v or (present is not None and r["path"] not in present):
        continue
    dv = v.get("dv") or {}
    # A dual-track file: the record, like the RPUs, is on the enhancement layer's own track.
    layer_track = r.get("dv_track") or {}
    if dv.get("dv_profile") is None:
        dv = layer_track.get("dv") or {}
    fel = r.get("fel") or {}
    fsd = set(r.get("frame_side_data") or [])
    entry = {
        "path": rel(r["path"]),
        "size": r["size"],
        "videoCodec": v.get("codec"),
        "width": v.get("width"),
        "height": v.get("height"),
        "frameRate": v.get("fps"),
        "hdr10Plus": any("HDR10+" in s for s in fsd),
        "transfer": v.get("transfer"),
        "audio": [{"kind": audio_kind(a), "channels": a.get("channels"), "language": a.get("lang")} for a in r.get("audio", [])],
    }
    if dv.get("dv_profile") is not None:
        entry["dolbyVisionProfile"] = dv.get("dv_profile")
        if dv.get("dv_profile") == 7:
            if fel.get("el_type") in ("FEL", "MEL"):
                entry["enhancementLayer"] = fel["el_type"]
                entry["enhancementLayerPresent"] = fel.get("el_bytes", 0) > 0
                if layer_track:
                    entry["enhancementLayerSeparateTrack"] = True
            elif fel.get("profile") and not str(fel["profile"]).startswith("7"):
                entry["rpuProfile"] = int(str(fel["profile"]).split()[0])
    entries.append(entry)

entries.sort(key=lambda e: e["path"])
# The app's index: the Profile 7 files and their verdicts, so the served file is small and names few titles.
tagged = [{k: e[k] for k in ("path", "size", "dolbyVisionProfile", "enhancementLayer", "enhancementLayerPresent",
                             "enhancementLayerSeparateTrack") if k in e}
          for e in entries if e.get("dolbyVisionProfile") == 7]
index = {"format": "rivulet-format-index", "version": 1, "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
         "entries": tagged}
(out / "format-index.json").write_text(json.dumps(index, separators=(",", ":")) + "\n")
with open(out / "format-index.tsv", "w") as f:
    f.write("path\tsize\tvideo\tresolution\tfps\tdolby_vision\tenhancement_layer\thdr10plus\taudio\n")
    for e in entries:
        dv = f"P{e['dolbyVisionProfile']}" if "dolbyVisionProfile" in e else ""
        el = (e.get("enhancementLayer", "") + ("" if e.get("enhancementLayerPresent", True) else " (no EL data)")
              + (" (2nd track)" if e.get("enhancementLayerSeparateTrack") else ""))
        f.write("\t".join(str(x) for x in (e["path"], e["size"], e["videoCodec"], f"{e['width']}x{e['height']}", e["frameRate"], dv, el,
                                            "yes" if e["hdr10Plus"] else "", "; ".join(f"{a['kind']} {a['channels']}ch" for a in e["audio"]))) + "\n")
fel = sum(1 for e in entries if e.get("enhancementLayer") == "FEL" and e.get("enhancementLayerPresent"))
mel = sum(1 for e in entries if e.get("enhancementLayer") == "MEL")
print(f"{len(tagged)} tagged of {len(entries)} files ({(out / 'format-index.json').stat().st_size // 1024} KB): FEL {fel}, MEL {mel}, "
      f"on a second track {sum(1 for e in entries if e.get('enhancementLayerSeparateTrack'))}, "
      f"DV any {sum(1 for e in entries if 'dolbyVisionProfile' in e)}, TrueHD Atmos {sum(1 for e in entries if any(a['kind'] == 'TrueHD Atmos' for a in e['audio']))}")
