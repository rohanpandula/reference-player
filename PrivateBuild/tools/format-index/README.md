# Format index

What a media server cannot tell the app about a file, pre-computed from the files themselves and
read by Reference Player (`RivuletCore/Integrations/FormatIndex.swift`): the Dolby Vision enhancement-layer
type of a Profile 7 title (FEL or MEL, from the RPU) and whether the stream really carries an
enhancement layer. Nothing about audio: Plex records each stream's own profile, Atmos and DTS:X
included. The index is optional. With no index URL set (the default), the app fetches nothing and
Format Tags stays off.

## Building one

The tools read only; they never change a media file. You need `ffmpeg`, `ffprobe` and `dovi_tool`
on the machine that runs them, and read-only ssh access to the server that holds the files.

1. A listing of the files, one line each: `size<TAB>mtime<TAB>path`, with the path as the server
   spells it. For example, on the server: `find /srv/media/Movies -type f -name '*.mkv' -printf
   '%s\t%T@\t%p\n'`.
2. `PROBE_HOST=user@server python3 library_probe.py files.tsv probe.jsonl` pulls the first 24 MB of
   each file over one multiplexed ssh connection (64 MB when that is not enough), runs ffprobe and,
   for Profile 7, dovi_tool, and appends one JSON line per file. It resumes: a file already probed
   with the same size and modification time is skipped.
3. `MOVIES_ROOT=/srv/media/Movies/ MEDIA_ROOT=/srv/media/ python3 build_index.py probe.jsonl out/ files.tsv`
   writes `format-index.json` (the app's index: only the Profile 7 files and their verdicts, keyed by
   path and size) and `format-index.tsv` (every file, for people: codecs, resolution, frame rate,
   HDR10+, audio tracks). With the listing, files no longer on the share are left out.
4. Serve `format-index.json` from any web server the Apple TV can reach, and put its address in
   Settings > Playback > Format Tags > Index URL, then turn the tags on.

`python3 -m unittest discover -s PrivateBuild/tests -p 'test_format_index.py'` checks both tools'
logic without a server.

The index names which Profile 7 titles a library holds. A file served without a login (Home
Assistant's `/local/` folder, for one) is readable by anyone who can reach that server; if you serve
it that way, put it under a long random path segment and treat the URL like a password.

The app fetches the index at launch, whenever it returns to the foreground with a copy older than
six hours, and at once from Format Tags > Refresh Now.

## Paths and limits

A movie's index path is relative to `MOVIES_ROOT`; every other library's is relative to
`MEDIA_ROOT` (`TV/...`). The app matches a server path by cutting it at its first `/movies/`, which
fits a server that mounts the movie library at `/movies`, and falls back to size plus file name for
any other layout.

A dual-track file keeps its enhancement layer, with every RPU and the only Dolby Vision record, on a
second video track; the first is a bare HDR10 base layer. The probe reads the verdict from the
layer's own track, and the index marks the entry `enhancementLayerSeparateTrack` (the TSV says
`(2nd track)`). It takes a later track for a layer only when the track's record says Profile 7 with
an enhancement layer and RPUs, the track is no larger than the first (and smaller, when its record
also claims a base layer), dovi_tool finds nothing wrapped inside it, and it is the only such track.
AetherEngine plays the base layer of such a file as HDR10, so the app keeps `HDR` on it and adds
`DV FEL 2-track` or `DV MEL 2-track` beside it.

FEL/MEL is read from the first RPUs of the file, which is how discs declare it. MP4 files whose
index sits at the end of the file cannot be read from their head and are skipped. The index is per
file, so a title with both a FEL and a MEL file gets each file's own verdict.
