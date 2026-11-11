"""The format-index tools' own logic: no server, no ffprobe, no dovi_tool."""

import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

TOOLS = Path(__file__).resolve().parents[1] / "tools" / "format-index"
SPEC = importlib.util.spec_from_file_location("library_probe", TOOLS / "library_probe.py")
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)  # importing must not read the command line


def dovi(profile=7, bl=0, el=1, rpu=1):
    return {"side_data_type": "DOVI configuration record", "dv_version_major": 1, "dv_version_minor": 0, "dv_profile": profile,
            "dv_level": 6, "rpu_present_flag": rpu, "el_present_flag": el, "bl_present_flag": bl, "dv_bl_signal_compatibility_id": 6}


def video(index, width, height, codec="hevc", record=None):
    stream = {"index": index, "codec_type": "video", "codec_name": codec, "width": width, "height": height,
              "avg_frame_rate": "24000/1001", "color_transfer": "smpte2084", "disposition": {"attached_pic": 0, "default": 1}}
    if record:
        stream["side_data_list"] = [record]
    return stream


# Three dual-track and single-track layouts, as ffprobe reports them.
BASE = video(0, 3840, 2160)                                           # a dual-track file's first track: bare HDR10
DEMO_LAYER = video(1, 1920, 1080, record=dovi(bl=0))                  # a layer track whose record claims no base layer
REMUX_LAYER = video(1, 1920, 1080, record=dovi(bl=1))                 # a layer track whose record claims both layers
SINGLE = video(0, 3840, 2160, record=dovi(bl=1))                      # a single-track remux


class EnhancementTrackTests(unittest.TestCase):
    def test_a_later_hevc_track_with_a_profile_7_record_is_the_layer(self):
        self.assertIs(probe.enhancement_track([BASE, DEMO_LAYER]), DEMO_LAYER)
        self.assertIs(probe.enhancement_track([BASE, REMUX_LAYER]), REMUX_LAYER,
                      "whatever the layer's record says about the base layer")

    def test_the_first_track_is_never_the_layer(self):
        self.assertIsNone(probe.enhancement_track([SINGLE]))
        self.assertIsNone(probe.enhancement_track([]))

    def test_other_second_tracks_are_not_a_layer(self):
        self.assertIsNone(probe.enhancement_track([BASE, video(1, 1920, 1080)]), "no record")
        self.assertIsNone(probe.enhancement_track([BASE, video(1, 1920, 1080, record=dovi(profile=8))]), "not Profile 7")
        self.assertIsNone(probe.enhancement_track([BASE, video(1, 600, 900, codec="mjpeg", record=dovi())]), "not HEVC")
        self.assertIsNone(probe.enhancement_track([BASE, video(1, 1920, 1080, record=dovi(el=0, bl=1))]),
                          "a record that names no enhancement layer")
        self.assertIsNone(probe.enhancement_track([BASE, video(1, 1920, 1080, record=dovi(rpu=0))]), "no RPUs to read")

    def test_a_second_complete_picture_is_not_a_layer(self):
        self.assertIsNone(probe.enhancement_track([video(0, 1920, 1080), video(1, 3840, 2160, record=dovi(bl=1))]),
                          "larger than the first track")
        self.assertIsNone(probe.enhancement_track([video(0, 1920, 1080), video(1, 3840, 2160, record=dovi(bl=0))]),
                          "larger than the first track, whatever its record says")
        self.assertIsNone(probe.enhancement_track([BASE, video(1, 4096, 1080, record=dovi(bl=0))]), "wider than the first track")
        self.assertIsNone(probe.enhancement_track([video(0, 3840, 1600), video(1, 1920, 2160, record=dovi(bl=0))]),
                          "taller than the first track")
        self.assertIsNone(probe.enhancement_track([BASE, video(1, 3840, 2160, record=dovi(bl=1))]),
                          "the first track's own size, and its record claims a base layer")
        full_size_layer = video(1, 3840, 2160, record=dovi(bl=0))
        self.assertIs(probe.enhancement_track([BASE, full_size_layer]), full_size_layer,
                      "a layer may be as large as the base layer when its record says it is only a layer")

    def test_an_ambiguous_file_is_left_alone(self):
        self.assertIsNone(probe.enhancement_track([BASE, DEMO_LAYER, video(2, 1920, 1080, record=dovi(bl=0))]), "two candidates")
        self.assertIsNone(probe.enhancement_track([video(0, 3840, 2160, record=dovi(profile=8, el=0, bl=1)), DEMO_LAYER]),
                          "a first track that is Dolby Vision of another profile is not the base of a pair")
        self.assertIsNone(probe.enhancement_track([video(0, 3840, 2160, codec="h264"), DEMO_LAYER]), "the base layer is HEVC")
        self.assertIs(probe.enhancement_track([SINGLE, DEMO_LAYER]), DEMO_LAYER, "a base track with its own Profile 7 record")


class SummarizeTests(unittest.TestCase):
    def summarize(self, real_video, verdicts):
        """Runs summarize with dovi_verdict stubbed: `verdicts` maps a stream index to its answer."""
        calls = []

        def fake(head, stream_index, work, own_track=False):
            calls.append((stream_index, own_track))
            return dict(verdicts[stream_index], stream=stream_index)

        with mock.patch.object(probe, "dovi_verdict", fake):
            rec = probe.summarize(1000, "/srv/media/Movies/T (2021)/t.mkv", 500, real_video, real_video, [], Path("."), Path("head.bin"))
        return rec, calls

    def test_a_dual_track_file_takes_its_verdict_from_the_layers_own_track(self):
        rec, calls = self.summarize([BASE, DEMO_LAYER], {1: {"el_type": "FEL", "el_bytes": 9, "hevc_bytes": 9}})
        self.assertEqual(calls, [(1, True)])
        self.assertIsNone(rec["video"]["dv"], "the first track is still the bare base layer")
        self.assertEqual(rec["video"]["width"], 3840)
        self.assertEqual(rec["dv_track"]["index"], 1)
        self.assertEqual(rec["dv_track"]["dv"]["dv_profile"], 7)
        self.assertEqual(rec["dv_track"]["dv"]["bl_present_flag"], 0)
        self.assertEqual(rec["fel"]["el_type"], "FEL")
        self.assertEqual(rec["probe"], probe.PROBE)

    def test_a_single_track_file_is_read_as_before(self):
        rec, calls = self.summarize([SINGLE], {0: {"el_type": "MEL", "el_bytes": 5, "hevc_bytes": 50}})
        self.assertEqual(calls, [(0, False)])
        self.assertNotIn("dv_track", rec)
        self.assertEqual(rec["video"]["dv"]["dv_profile"], 7)

    def test_the_first_tracks_answer_stands_when_it_gave_one(self):
        rec, calls = self.summarize([SINGLE, DEMO_LAYER], {0: {"el_type": "FEL", "el_bytes": 5, "hevc_bytes": 50}})
        self.assertEqual(calls, [(0, False)])
        self.assertNotIn("dv_track", rec)

    def test_a_base_track_with_a_record_but_no_rpu_defers_to_the_layers_track(self):
        rec, calls = self.summarize([SINGLE, DEMO_LAYER], {0: {"error": "No RPU was found in input file"},
                                                           1: {"el_type": "FEL", "el_bytes": 9, "hevc_bytes": 9}})
        self.assertEqual(calls, [(0, False), (1, True)])
        self.assertEqual(rec["dv_track"]["index"], 1)
        self.assertEqual(rec["fel"]["el_type"], "FEL")

    def test_a_plain_file_with_two_video_tracks_gets_no_verdict(self):
        rec, calls = self.summarize([BASE, video(1, 1920, 1080)], {})
        self.assertEqual(calls, [])
        self.assertNotIn("fel", rec)
        self.assertNotIn("dv_track", rec)

    def test_a_second_track_that_is_a_complete_picture_leaves_the_file_unclassified(self):
        with mock.patch.object(probe, "dovi_verdict", lambda *a, **k: None):   # demux found a layer inside it
            rec = probe.summarize(1000, "/p", 500, [BASE, DEMO_LAYER], [BASE, DEMO_LAYER], [], Path("."), Path("head.bin"))
        self.assertNotIn("dv_track", rec)
        self.assertNotIn("fel", rec)


class VerdictTests(unittest.TestCase):
    """dovi_verdict against stand-ins for ffmpeg and dovi_tool, to pin where the layer's bytes are counted."""

    NONE_FOUND = "Error: No enhancement layer was found in input file"   # dovi_tool 2.3.4, on a real layer track

    def run_verdict(self, own_track, demux_writes, demux_says=NONE_FOUND):
        """`demux_writes`: the bytes of wrapped layer demux finds; with none it fails, saying `demux_says`."""
        ran = []

        def fake_run(cmd, **kwargs):
            tool = Path(cmd[0]).name
            ran.append(cmd[1] if tool == "dovi_tool" else tool)
            if tool == "ffmpeg":
                Path(cmd[-1]).write_bytes(b"x" * 700)             # the copied elementary stream
            elif cmd[1] == "info":
                return types.SimpleNamespace(returncode=0, stdout="Summary:\n  Frames: 12\n  Profile: 7 (FEL)\n", stderr="")
            elif cmd[1] == "demux" and demux_writes:
                Path(cmd[cmd.index("--el-out") + 1]).write_bytes(b"e" * demux_writes)
            elif cmd[1] == "demux":
                return types.SimpleNamespace(returncode=1, stdout="", stderr=demux_says)
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(probe.subprocess, "run", fake_run):
            return probe.dovi_verdict(Path(tmp) / "head.bin", 1, Path(tmp), own_track=own_track), ran

    def test_a_layer_wrapped_in_a_single_track_is_counted_by_demux(self):
        out, ran = self.run_verdict(own_track=False, demux_writes=300)
        self.assertIn("demux", ran)
        self.assertEqual((out["el_type"], out["frames"], out["el_bytes"], out["hevc_bytes"]), ("FEL", 12, 300, 700))

    def test_a_single_track_with_no_layer_inside_counts_none(self):
        out, _ = self.run_verdict(own_track=False, demux_writes=0)
        self.assertEqual(out["el_bytes"], 0, "demux found no layer and wrote no file")

    def test_a_layers_own_track_is_the_layer(self):
        out, ran = self.run_verdict(own_track=True, demux_writes=0)
        self.assertIn("demux", ran, "demux is still asked: it must find nothing wrapped inside")
        self.assertEqual((out["el_type"], out["el_bytes"], out["hevc_bytes"]), ("FEL", 700, 700))

    def test_a_track_with_a_layer_wrapped_inside_is_no_layer_of_its_own(self):
        out, _ = self.run_verdict(own_track=True, demux_writes=300)
        self.assertIsNone(out, "a complete single-track picture, not the layer of a pair")

    def test_a_demux_that_fails_another_way_settles_nothing(self):
        for words in ("Error: unexpected end of file", ""):
            out, _ = self.run_verdict(own_track=True, demux_writes=0, demux_says=words)
            self.assertNotIn("el_type", out, "no verdict from a run that did not answer the question")
            self.assertNotIn("el_bytes", out)
            self.assertTrue(out["error"].startswith("demux:"), out)

    def test_a_single_track_does_not_care_why_demux_wrote_nothing(self):
        out, _ = self.run_verdict(own_track=False, demux_writes=0, demux_says="Error: unexpected end of file")
        self.assertEqual((out["el_type"], out["el_bytes"]), ("FEL", 0), "as before this change")


class RetryTests(unittest.TestCase):
    """probe_file with the server, ffprobe and dovi_tool replaced: when is the larger pull made?"""

    SIZE = 10 ** 9

    def probe_file(self, real_video, verdicts, after_the_larger_pull=None):
        """`real_video`: what ffprobe sees in the head; `after_the_larger_pull`: what it sees then (the same by default)."""
        pulls, answers = [], iter(verdicts)
        seen = iter([real_video, after_the_larger_pull or real_video])

        def pull(path, n, dest):
            pulls.append(n)
            return n

        def probe_head(head):
            streams = next(seen)
            return streams, streams, [{}]

        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(probe, "WORK", Path(tmp)), \
                mock.patch.object(probe, "pull", pull), \
                mock.patch.object(probe, "probe_head", probe_head), \
                mock.patch.object(probe, "dovi_verdict", lambda *a, **k: dict(next(answers))):
            return probe.probe_file(self.SIZE, "/srv/media/Movies/T (2021)/t.mp4"), pulls

    def test_a_layer_track_with_nothing_readable_in_the_head_earns_the_larger_pull(self):
        rec, pulls = self.probe_file([BASE, DEMO_LAYER], [{"stream": 1, "error": "no hevc copied"},
                                                          {"stream": 1, "el_type": "FEL", "el_bytes": 9, "hevc_bytes": 9}])
        self.assertEqual(pulls, [probe.HEAD, probe.HEAD_RETRY])
        self.assertTrue(rec["retried"])
        self.assertEqual(rec["fel"]["el_type"], "FEL")
        self.assertEqual(rec["dv_track"]["index"], 1)

    def test_a_layer_track_unreadable_after_the_larger_pull_is_not_pulled_every_night(self):
        nothing = {"stream": 1, "error": "no hevc copied"}
        rec, pulls = self.probe_file([BASE, DEMO_LAYER], [nothing, nothing])
        self.assertEqual(pulls, [probe.HEAD, probe.HEAD_RETRY])
        self.assertNotIn("el_type", rec["fel"])
        self.assertTrue(probe.settled(dict(rec, mtime="1700000000"), (self.SIZE, "1700000000")))

    def test_a_layer_track_read_from_the_head_needs_one_pull(self):
        rec, pulls = self.probe_file([BASE, DEMO_LAYER], [{"stream": 1, "el_type": "MEL", "el_bytes": 9, "hevc_bytes": 9}])
        self.assertEqual(pulls, [probe.HEAD])
        self.assertNotIn("retried", rec)

    def test_a_layer_track_with_no_picture_size_in_the_head_earns_the_larger_pull(self):
        sizeless = video(1, 0, 0, record=dovi(bl=0))
        rec, pulls = self.probe_file([BASE, sizeless], [{"stream": 1, "el_type": "FEL", "el_bytes": 9, "hevc_bytes": 9}],
                                     after_the_larger_pull=[BASE, DEMO_LAYER])
        self.assertEqual(pulls, [probe.HEAD, probe.HEAD_RETRY])
        self.assertEqual(rec["fel"]["el_type"], "FEL", "the size arrived with the larger pull")
        self.assertEqual(rec["dv_track"]["width"], 1920)

    def test_a_layer_track_still_without_a_size_stays_unclassified_and_settled(self):
        sizeless = video(1, 0, 0, record=dovi(bl=0))
        del sizeless["width"]                                    # missing counts as unknown too
        rec, pulls = self.probe_file([BASE, sizeless], [])
        self.assertEqual(pulls, [probe.HEAD, probe.HEAD_RETRY])
        self.assertNotIn("dv_track", rec)
        self.assertNotIn("fel", rec)
        self.assertTrue(rec["retried"])
        self.assertTrue(probe.settled(dict(rec, mtime="1700000000"), (self.SIZE, "1700000000")), "not pulled every night")

    def test_a_first_track_that_answered_is_not_pulled_again_for_a_sizeless_second_track(self):
        rec, pulls = self.probe_file([SINGLE, video(1, 0, 0, record=dovi(bl=0))],
                                     [{"stream": 0, "el_type": "FEL", "el_bytes": 5, "hevc_bytes": 50}])
        self.assertEqual(pulls, [probe.HEAD], "the first track's answer stands; a second pull could only lose it")
        self.assertEqual(rec["fel"], {"stream": 0, "el_type": "FEL", "el_bytes": 5, "hevc_bytes": 50})
        self.assertNotIn("dv_track", rec)

    def test_a_file_with_two_plain_video_tracks_needs_one_pull(self):
        rec, pulls = self.probe_file([BASE, video(1, 0, 0)], [])
        self.assertEqual(pulls, [probe.HEAD], "a sizeless second track that names no layer is nobody's business")

    def test_a_single_track_file_whose_verdict_failed_is_pulled_once_as_before(self):
        rec, pulls = self.probe_file([SINGLE], [{"stream": 0, "error": "No RPU was found in input file"}])
        self.assertEqual(pulls, [probe.HEAD])
        self.assertNotIn("dv_track", rec)


class ResumeTests(unittest.TestCase):
    NOW = (1000, "1700000000")

    def record(self, **extra):
        return dict({"path": "/p", "size": 1000, "mtime": "1700000000", "video": {"codec": "hevc"}}, **extra)

    def test_an_unchanged_file_with_one_video_track_stays_settled(self):
        self.assertTrue(probe.settled(self.record(video_streams=1), self.NOW), "a record from before this probe")
        self.assertTrue(probe.settled(self.record(video_streams=1, probe=probe.PROBE), self.NOW))

    def test_a_file_with_several_video_tracks_is_probed_once_more(self):
        self.assertFalse(probe.settled(self.record(video_streams=2), self.NOW),
                         "the older probe never looked at the second track")
        self.assertTrue(probe.settled(self.record(video_streams=2, probe=probe.PROBE), self.NOW))

    def test_a_changed_or_vanished_file_is_never_settled(self):
        self.assertFalse(probe.settled(self.record(video_streams=1, probe=probe.PROBE), (1001, "1700000000")))
        self.assertFalse(probe.settled(self.record(video_streams=1, probe=probe.PROBE), (1000, "1700000001")))
        self.assertFalse(probe.settled(self.record(video_streams=1, probe=probe.PROBE), None))


class ProbeHostTests(unittest.TestCase):
    def test_the_probe_names_no_host_of_its_own(self):
        with mock.patch.dict(os.environ, {"PROBE_HOST": ""}):
            with self.assertRaises(SystemExit):
                probe.ssh()
        with mock.patch.dict(os.environ, {"PROBE_HOST": "user@media.example.invalid"}):
            self.assertEqual("user@media.example.invalid", probe.ssh()[-1])


class BuildIndexTests(unittest.TestCase):
    # The roots come from the environment; the tool has no default that names anyone's share.
    ROOTS = dict(os.environ, MOVIES_ROOT="/srv/media/Movies/", MEDIA_ROOT="/srv/media/")

    def build(self, records):
        with tempfile.TemporaryDirectory() as tmp:
            source, out = Path(tmp) / "probe.jsonl", Path(tmp) / "out"
            source.write_text("".join(json.dumps(r) + "\n" for r in records))
            done = subprocess.run([sys.executable, str(TOOLS / "build_index.py"), str(source), str(out)],
                                  capture_output=True, text=True, env=self.ROOTS)
            self.assertEqual(done.returncode, 0, done.stderr)
            index = json.loads((out / "format-index.json").read_text())
            rows = [line.split("\t") for line in (out / "format-index.tsv").read_text().splitlines()[1:]]
            return {e["path"]: e for e in index["entries"]}, {r[0]: r for r in rows}, done.stdout

    @staticmethod
    def record(path, size, dv=None, **extra):
        return dict({"path": path, "size": size, "video_streams": 1, "audio": [],
                     "video": {"index": 0, "codec": "hevc", "width": 3840, "height": 2160, "fps": 23.976,
                               "transfer": "smpte2084", "dv": dv},
                     "frame_side_data": []}, **extra)

    def test_a_dual_track_file_is_listed_and_marked(self):
        p7 = {"dv_profile": 7, "dv_level": 6, "el_present_flag": 1, "bl_present_flag": 1, "rpu_present_flag": 1}
        layer = {"index": 1, "width": 1920, "height": 1080, "dv": dict(p7, bl_present_flag=0)}
        entries, rows, summary = self.build([
            self.record("/srv/media/Movies/Single (2020)/single.mkv", 10, dv=p7,
                        fel={"stream": 0, "el_type": "FEL", "el_bytes": 400, "hevc_bytes": 900}),
            self.record("/srv/media/Demos/Disc/clip.mp4", 20, video_streams=2, dv_track=layer,
                        fel={"stream": 1, "el_type": "FEL", "el_bytes": 700, "hevc_bytes": 700}),
            self.record("/srv/media/Movies/Remux (2021)/remux.mkv", 30, video_streams=2, dv_track=dict(layer, dv=p7),
                        fel={"stream": 1, "el_type": "MEL", "el_bytes": 60, "hevc_bytes": 60}),
            self.record("/srv/media/Movies/Plain (2019)/plain.mkv", 40, video_streams=2),
        ])
        self.assertEqual(sorted(entries), ["Demos/Disc/clip.mp4", "Remux (2021)/remux.mkv", "Single (2020)/single.mkv"],
                         "the app's index lists the Profile 7 files, dual-track ones included, and nothing else")
        self.assertEqual(entries["Demos/Disc/clip.mp4"],
                         {"path": "Demos/Disc/clip.mp4", "size": 20, "dolbyVisionProfile": 7, "enhancementLayer": "FEL",
                          "enhancementLayerPresent": True, "enhancementLayerSeparateTrack": True})
        self.assertEqual(entries["Remux (2021)/remux.mkv"]["enhancementLayer"], "MEL")
        self.assertIs(entries["Remux (2021)/remux.mkv"]["enhancementLayerSeparateTrack"], True)
        self.assertNotIn("enhancementLayerSeparateTrack", entries["Single (2020)/single.mkv"])
        self.assertEqual(rows["Demos/Disc/clip.mp4"][5:7], ["P7", "FEL (2nd track)"])
        self.assertEqual(rows["Single (2020)/single.mkv"][5:7], ["P7", "FEL"])
        self.assertEqual(rows["Plain (2019)/plain.mkv"][5:7], ["", ""])
        self.assertIn("on a second track 2", summary)

    def test_the_builders_output_is_what_the_app_decodes(self):
        """Read the Swift decoder (RivuletCore/Integrations/FormatIndex.swift) and hold the builder to it:
        the format marker, every key the builder writes is a field the app reads, every field the app
        reads is one the builder can write, and the required ones are always there."""
        swift = (Path(__file__).resolve().parents[2] / "RivuletCore/Integrations/FormatIndex.swift").read_text()
        def fields(struct):
            body = re.search(r"struct " + struct + r"\b[^{]*\{(.*?)\n\}", swift, re.S).group(1)
            body = re.sub(r"\n    enum .*?\n    \}", "", body, flags=re.S)   # the nested enum's cases are not fields
            found = re.findall(r"^    (let|var) (\w+): (\S+)", body, re.M)
            return {name for _, name, _ in found}, {name for kind, name, kind_type in found if kind == "let" and not kind_type.endswith("?")}
        entry_fields, entry_required = fields("FormatIndexEntry")
        index_fields, index_required = fields("FormatIndex")
        self.assertEqual({"path", "size"}, entry_required)
        self.assertEqual({"format", "version", "entries"}, index_required)
        layers = set(re.findall(r'case \w+ = "(\w+)"', swift))
        marker = re.search(r'index\.format == "([^"]+)"', swift).group(1)

        p7 = {"dv_profile": 7, "dv_level": 6, "el_present_flag": 1, "bl_present_flag": 1, "rpu_present_flag": 1}
        layer = {"index": 1, "width": 1920, "height": 1080, "dv": dict(p7, bl_present_flag=0)}
        with tempfile.TemporaryDirectory() as tmp:
            source, out = Path(tmp) / "probe.jsonl", Path(tmp) / "out"
            records = [self.record("/srv/media/Movies/A (2020)/a.mkv", 10, dv=p7,
                                   fel={"stream": 0, "el_type": "FEL", "el_bytes": 400, "hevc_bytes": 900}),
                       self.record("/srv/media/Movies/B (2021)/b.mkv", 30, video_streams=2, dv_track=dict(layer, dv=p7),
                                   fel={"stream": 1, "el_type": "MEL", "el_bytes": 60, "hevc_bytes": 60})]
            source.write_text("".join(json.dumps(r) + "\n" for r in records))
            done = subprocess.run([sys.executable, str(TOOLS / "build_index.py"), str(source), str(out)], capture_output=True, text=True,
                                  env=self.ROOTS)
            self.assertEqual(done.returncode, 0, done.stderr)
            index = json.loads((out / "format-index.json").read_text())
        self.assertEqual(marker, index["format"])
        self.assertLessEqual(index_required, set(index))
        written = set().union(*(set(e) for e in index["entries"]))
        self.assertEqual(entry_fields, written, "every key one side names, the other side names too")
        for e in index["entries"]:
            self.assertLessEqual(entry_required, set(e))
            self.assertIn(e["enhancementLayer"], layers)


if __name__ == "__main__":
    unittest.main()
