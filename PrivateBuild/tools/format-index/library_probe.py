"""Ground-truth probe of every video file in a listing of a media share.

Pulls the first HEAD bytes of each file over one multiplexed SSH connection (read-only) to the
host named by PROBE_HOST (an ssh destination; there is no default), runs
ffprobe on the head (streams with disposition, first two video frames' side data) and, for a
Dolby Vision Profile 7 record, dovi_tool on the HEVC stream that carries the RPUs for the FEL/MEL
verdict and the enhancement-layer byte count: the first video track of a single-track file, the
enhancement layer's own track of a dual-track one. Every task gets its own scratch directory (the
first pass shared one per worker slot, which let concurrent tasks overwrite each other's head file).
One JSON line per file, resumable.
"""
import concurrent.futures, json, os, pathlib, shlex, shutil, subprocess, sys, tempfile, time

TSV = OUT = LOG = WORK = None  # set by main() from the command line
HEAD, HEAD_RETRY, WORKERS = 24_000_000, 64_000_000, 4
# What a record knows. 2: a Dolby Vision record on a later video track is read (a dual-track file).
PROBE = 2
DV_KEYS = ("dv_profile", "dv_level", "dv_bl_signal_compatibility_id", "el_present_flag", "bl_present_flag", "rpu_present_flag")
# dovi_tool demux's own words (2.3.4) for a stream with no enhancement layer wrapped inside it.
NO_WRAPPED_LAYER = "No enhancement layer was found"
TOOLS = {t: shutil.which(t) or t for t in ("ssh", "ffprobe", "ffmpeg", "dovi_tool")}


def ssh():
    """ssh to the media server named by PROBE_HOST. No default: the tool names nobody's network."""
    host = os.environ.get("PROBE_HOST", "")
    if not host:
        raise SystemExit("set PROBE_HOST to the ssh destination of the server that holds the files (e.g. user@server)")
    return [TOOLS["ssh"], "-n", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "-o", "ControlMaster=auto",
            "-o", "ControlPath=~/.ssh/cm-%C", "-o", "ControlPersist=900", host]


def log(msg):
    with open(LOG, "a") as f:
        f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")


def pull(path, n, dest):
    # shlex.quote: the remote shell may run as root, and a file name is data, never syntax.
    with open(dest, "wb") as f:
        subprocess.run(ssh() + [f"head -c {n} {shlex.quote(path)}"], stdout=f, stderr=subprocess.DEVNULL, timeout=600)
    return dest.stat().st_size


def ffprobe_json(args, timeout=180):
    r = subprocess.run([TOOLS["ffprobe"], "-v", "error", "-of", "json", *args], capture_output=True, text=True, timeout=timeout)
    try:
        return json.loads(r.stdout or "{}")
    except json.JSONDecodeError:
        return {}


def probe_head(head):
    streams = ffprobe_json(["-show_streams", "-show_entries",
                            "stream=index,codec_type,codec_name,profile,width,height,avg_frame_rate,pix_fmt,"
                            "color_transfer,color_primaries,color_space,channels,channel_layout,sample_rate,"
                            "bits_per_raw_sample:stream_disposition=attached_pic,default:stream_tags=language,title:stream_side_data_list",
                            str(head)]).get("streams", [])
    real_video = [s for s in streams if s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic")]
    frames = []
    if real_video:
        frames = ffprobe_json(["-select_streams", str(real_video[0]["index"]), "-show_frames", "-read_intervals", "%+#2",
                               "-show_entries", "frame=pix_fmt,color_transfer:frame_side_data_list", str(head)]).get("frames", [])
    return streams, real_video, frames


def dovi_record(stream):
    """The stream's Dolby Vision configuration record as ffprobe prints it, or None."""
    return next((sd for sd in stream.get("side_data_list", []) if sd.get("side_data_type") == "DOVI configuration record"), None)


def layer_candidates(real_video):
    """The later tracks whose own record names a Profile 7 enhancement layer with RPUs.

    Empty when the first track cannot be the base layer of a pair: it is not HEVC, or it is Dolby
    Vision of another profile. A file's first video track is never a candidate itself.
    """
    if not real_video or real_video[0].get("codec_name") != "hevc":
        return []
    if (dovi_record(real_video[0]) or {}).get("dv_profile") not in (None, 7):
        return []

    def names_a_layer(s):
        record = dovi_record(s) or {}
        return (s.get("codec_name") == "hevc" and record.get("dv_profile") == 7
                and bool(record.get("el_present_flag")) and bool(record.get("rpu_present_flag")))

    return [s for s in real_video[1:] if names_a_layer(s)]


def picture_size(stream):
    return stream.get("width") or 0, stream.get("height") or 0


def layer_size_unknown(real_video):
    """True when one track names itself a layer but the head gave no picture size for it or for the
    first track, so the pair cannot be judged yet. More of the file may tell."""
    candidates = layer_candidates(real_video)
    return len(candidates) == 1 and 0 in picture_size(candidates[0]) + picture_size(real_video[0])


def enhancement_track(real_video):
    """The enhancement-layer track of a dual-track Profile 7 file, or None.

    Such a file keeps its two layers as two video tracks: the first is the base layer, plain HDR10
    with no RPU and usually no record of its own, and a later HEVC track is the enhancement layer,
    which carries every RPU and the Profile 7 record. A second complete picture is not a layer, so
    the track must say it holds an enhancement layer and RPUs (`layer_candidates`), be the only
    such track and be no larger than the first; anything else is left unclassified.
    """
    candidates = layer_candidates(real_video)
    if len(candidates) != 1:
        return None
    (w, h), (width, height) = picture_size(candidates[0]), picture_size(real_video[0])
    if not (0 < w <= width and 0 < h <= height):
        return None
    # The record of a layer's own track says there is no base layer in it. Some muxers write that
    # one too (seen in a remux); then only a track smaller than the base layer, the quarter size a
    # disc's layer has, is taken for a layer. One of the base layer's own size that claims a base
    # layer is a second complete picture.
    if dovi_record(candidates[0]).get("bl_present_flag") and not (w < width and h < height):
        return None
    return candidates[0]


def dovi_verdict(head, stream_index, work, own_track=False):
    """FEL or MEL from the stream's RPUs, and how many bytes of enhancement layer the head holds.

    `own_track`: the stream is a dual-track file's enhancement layer, so its own bytes are the layer:
    dovi_tool's demux looks for a layer wrapped inside a single track and says it found none. When it
    does find one, the track is a complete single-track picture and no layer of its own; the answer
    is then None and the file is left unclassified. When demux fails any other way the question is
    open, and the answer carries an error and no type.
    """
    hevc, rpu, bl, el = work / "v.hevc", work / "v.rpu", work / "bl.hevc", work / "el.hevc"
    for p in (hevc, rpu, bl, el):
        p.unlink(missing_ok=True)
    subprocess.run([TOOLS["ffmpeg"], "-v", "error", "-nostdin", "-y", "-i", str(head), "-map", f"0:{stream_index}", "-c:v", "copy",
                    "-bsf:v", "hevc_mp4toannexb", "-f", "hevc", str(hevc)], capture_output=True, timeout=300)
    if not hevc.exists() or hevc.stat().st_size == 0:
        return {"stream": stream_index, "error": "no hevc copied"}
    out = {"stream": stream_index}
    x = subprocess.run([TOOLS["dovi_tool"], "extract-rpu", str(hevc), "-o", str(rpu)], capture_output=True, text=True, timeout=300)
    if x.returncode != 0:
        out["error"] = (x.stderr or x.stdout)[-120:].strip()
    else:
        info = subprocess.run([TOOLS["dovi_tool"], "info", "-i", str(rpu), "-s"], capture_output=True, text=True, timeout=120).stdout
        for line in info.splitlines():
            s = line.strip()
            if s.startswith("Profile:"):
                out["profile"] = s.split(":", 1)[1].strip()
                out["el_type"] = "FEL" if "(FEL)" in s else "MEL" if "(MEL)" in s else None
            elif s.startswith("Frames:"):
                out["frames"] = int(s.split(":", 1)[1].strip() or 0)
    out["hevc_bytes"] = hevc.stat().st_size
    d = subprocess.run([TOOLS["dovi_tool"], "demux", str(hevc), "--bl-out", str(bl), "--el-out", str(el)],
                       capture_output=True, text=True, timeout=300)
    wrapped = el.stat().st_size if el.exists() else 0
    if not own_track:
        out["el_bytes"] = wrapped
    elif wrapped:
        return None
    elif NO_WRAPPED_LAYER in (d.stderr or "") + (d.stdout or ""):
        out["el_bytes"] = out["hevc_bytes"]
    else:
        out.pop("el_type", None)
        out.setdefault("error", "demux: " + ((d.stderr or d.stdout or "").strip()[-100:] or f"exit {d.returncode}"))
    return out


def summarize(size, path, head_bytes, streams, real_video, frames, work, head):
    rec = {"path": path, "size": size, "probe": PROBE, "head_bytes": head_bytes, "video_streams": len(real_video), "audio": [],
           "cover_art_streams": sum(1 for s in streams if (s.get("disposition") or {}).get("attached_pic")),
           "subtitle_streams": sum(1 for s in streams if s.get("codec_type") == "subtitle")}
    for s in streams:
        if s.get("codec_type") == "audio":
            rec["audio"].append({"codec": s.get("codec_name"), "profile": s.get("profile"), "channels": s.get("channels"),
                                 "layout": s.get("channel_layout"), "rate": s.get("sample_rate"),
                                 "lang": (s.get("tags") or {}).get("language"), "title": (s.get("tags") or {}).get("title"),
                                 "default": (s.get("disposition") or {}).get("default")})
    if real_video:
        v = real_video[0]
        fps = v.get("avg_frame_rate") or "0/1"
        try:
            n, d = fps.split("/"); fps = round(int(n) / int(d), 3) if int(d) else None
        except (ValueError, ZeroDivisionError):
            fps = None
        dv = dovi_record(v)
        rec["video"] = {"index": v.get("index"), "codec": v.get("codec_name"), "profile": v.get("profile"), "width": v.get("width"),
                        "height": v.get("height"), "fps": fps, "pix_fmt": v.get("pix_fmt"), "transfer": v.get("color_transfer"),
                        "primaries": v.get("color_primaries"), "bit_depth": v.get("bits_per_raw_sample"),
                        "dv": {k: dv.get(k) for k in DV_KEYS} if dv else None,
                        "side_data": sorted({sd.get("side_data_type") for sd in v.get("side_data_list", [])})}
        fsd = [sd for f in frames for sd in f.get("side_data_list", [])]
        rec["frame_side_data"] = sorted({sd.get("side_data_type") for sd in fsd})
        rec["frames_in_head"] = len(frames)
        if dv and dv.get("dv_profile") == 7 and v.get("codec_name") == "hevc":
            rec["fel"] = dovi_verdict(head, v["index"], work)
        # A dual-track file: the RPUs, and so the verdict, are on the enhancement layer's own track.
        # The first track's answer stands when it gave one (a single-track file with a stray second track).
        el = enhancement_track(real_video)
        if el and not (rec.get("fel") or {}).get("el_type"):
            verdict = dovi_verdict(head, el["index"], work, own_track=True)
            if verdict is not None:
                rec["dv_track"] = {"index": el.get("index"), "width": el.get("width"), "height": el.get("height"),
                                   "dv": {k: dovi_record(el).get(k) for k in DV_KEYS}}
                rec["fel"] = verdict
    return rec


def seconds(mtime):
    """A modification time to the second: the fraction does not survive a move between filesystems."""
    return None if mtime is None else str(mtime).split(".")[0]


def process(item):
    size, mtime, path = item
    rec = probe_file(size, path)
    rec["mtime"] = mtime
    return rec


def probe_file(size, path):
    work = pathlib.Path(tempfile.mkdtemp(prefix="t", dir=WORK))
    head = work / "head.bin"
    try:
        got = pull(path, HEAD, head)
        if got < 100_000:
            return {"path": path, "size": size, "error": f"pull returned {got} bytes"}
        streams, real_video, frames = probe_head(head)
        rec = summarize(size, path, got, streams, real_video, frames, work, head)
        # A verdict that found RPUs but no type wants more of the file. So does a layer's own track
        # that gave nothing at all: a file may hold all of one track before the other, and then the
        # first megabytes have the base layer's samples and none of the layer's. And so does a layer
        # track of unknown size, unless the first track already answered: its answer stands.
        fel = rec.get("fel") or {}
        needs_more = (not streams or (real_video and not frames)
                      or (layer_size_unknown(real_video) and not fel.get("el_type"))
                      or (rec.get("fel") is not None and not fel.get("el_type")
                          and ("error" not in fel or "dv_track" in rec)
                          and (fel.get("profile") or "7").startswith("7")))
        if needs_more and size > got:
            got = pull(path, HEAD_RETRY, head)
            if got < 100_000:
                return {"path": path, "size": size, "error": f"retry pull returned {got} bytes"}
            streams, real_video, frames = probe_head(head)
            rec = summarize(size, path, got, streams, real_video, frames, work, head)
            rec["retried"] = True
        if not streams:
            rec["error"] = "ffprobe found no streams in the head"
        elif not real_video:
            rec["error"] = "no video stream"
        return rec
    except Exception as e:  # keep going; the record says what failed
        return {"path": path, "size": size, "error": f"{type(e).__name__}: {e}"[:200]}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def settled(rec, now):
    """Whether a file's last record still describes it; `now` is the listing's (size, mtime) or None."""
    if now is None or rec.get("size") != now[0] or seconds(rec.get("mtime")) != now[1]:
        return False
    # A record from before the probe read later video tracks says nothing about them.
    if rec.get("video_streams", 0) > 1 and rec.get("probe", 1) < PROBE:
        return False
    if not rec.get("error") and rec.get("video"):
        return True
    return bool(rec.get("retried")) and rec.get("head_bytes", 0) >= min(HEAD_RETRY, rec.get("size", 0))


def main():
    global TSV, OUT, LOG, WORK
    if len(sys.argv) < 3:
        sys.exit("usage: PROBE_HOST=<ssh destination> library_probe.py <files.tsv: size<TAB>mtime<TAB>path per line> "
                 "<out.jsonl> [log] [workdir]")
    ssh()   # refuse before reading anything when no host is named
    TSV = pathlib.Path(sys.argv[1])
    OUT = pathlib.Path(sys.argv[2])
    LOG = pathlib.Path(sys.argv[3]) if len(sys.argv) > 3 else OUT.with_suffix(".log")
    WORK = pathlib.Path(sys.argv[4]) if len(sys.argv) > 4 else OUT.parent / ".probe-work"
    items = []
    for line in TSV.read_text(errors="replace").splitlines():
        parts = line.split("\t", 2)
        if len(parts) == 3:
            items.append((int(parts[0]), seconds(parts[1]), parts[2]))
    # Resume: a path counts as done when its last record is for the same file (same size, same
    # modification time) and either succeeded or failed after the larger pull arrived in full (an
    # index at the end of the file; no head reads it, so pulling it nightly would be waste). A short
    # or failed pull is probed again, and so is any file whose size or time moved (a download that
    # was preallocated at full size and has since finished, a file edited in place). A record from
    # before the listing carried times has none, so its file is probed once more; so is a file with
    # several video tracks whose record predates the probe reading them. build_index.py keeps the
    # last record per path, so the new one supersedes the old.
    done = set()
    if OUT.exists():
        last = {}
        for line in OUT.read_text(errors="replace").splitlines():
            try:
                rec = json.loads(line); last[rec["path"]] = rec
            except (json.JSONDecodeError, KeyError):
                pass
        current = {path: (size, mtime) for size, mtime, path in items}
        done = {p for p, rec in last.items() if settled(rec, current.get(p))}
    todo = [i for i in items if i[2] not in done]
    log(f"start: {len(items)} files, {len(done)} already done, {len(todo)} to do, {WORKERS} workers, head {HEAD // 1_000_000} MB")
    WORK.mkdir(parents=True, exist_ok=True)
    started = time.time(); count = 0; bytes_pulled = 0
    with open(OUT, "a") as out, concurrent.futures.ThreadPoolExecutor(WORKERS) as pool:
        for rec in pool.map(process, todo):
            out.write(json.dumps(rec) + "\n"); out.flush(); count += 1; bytes_pulled += rec.get("head_bytes", 0)
            if count % 100 == 0:
                el = time.time() - started
                log(f"{count}/{len(todo)} done, {bytes_pulled / 1e6 / el:.1f} MB/s, {count / el * 60:.0f} files/min")
    el = time.time() - started
    log(f"PROBE_DONE {count} files in {el / 60:.1f} min")
    shutil.rmtree(WORK, ignore_errors=True)


if __name__ == "__main__":
    main()
