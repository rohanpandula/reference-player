"""felbench.py reads the engine's log lines as the engine writes them."""

import importlib.util
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("felbench", Path(__file__).resolve().parents[1] / "felbench.py")
felbench = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(felbench)

LOG = """
DV P7 dual-layer route: taken (el_type=FEL, 3840x2160@23.976, budget 80%)
[DoviFelComposer] gpu 10.0ms/frame over 120 frames
[DualLayerVideoDecoder] totals: composed=250, bypassDeadline=1, bypassHeldWall=0, bypassBackstop=0
[SWDiag] clk=1.00 rebuf=n enq=+24 disp=+24 lost=0(+0) skip=0 dpts=41.0/42.0 layerDrop=0(+0)
[DoviFelComposer] gpu 6.0ms/frame over 120 frames
[DualLayerVideoDecoder] totals: composed=500, bypassDeadline=2, bypassHeldWall=0, bypassBackstop=1
[DualLayerVideoDecoder] totals: composed=30, bypassDeadline=0, bypassHeldWall=0, bypassBackstop=0
[SWDiag] clk=2.00 rebuf=y enq=+24 disp=+24 lost=3(+3) skip=0 dpts=41.0/42.0 layerDrop=2(+2)
[DoviFelComposer] gpu 8.0ms/frame over 120 frames, 4:4:4 pipeline allows 1024 threads per threadgroup
"""


class FelBenchTests(unittest.TestCase):
    def test_a_run_is_summarised_from_the_engines_lines(self):
        run = felbench.summarise(LOG)
        self.assertEqual("3840x2160 at 23.976 fps", run["source"])
        self.assertEqual((3, 360, 8.0, 6.0, 10.0), (run["windows"], run["frames_timed"], run["gpu_median_ms"],
                                                    run["gpu_min_ms"], run["gpu_max_ms"]))
        self.assertAlmostEqual(41.708, run["frame_budget_ms"], places=3)
        # Totals restart with each playback: 500 and 30 are two runs' last lines.
        self.assertEqual((530, 3), (run["composed"], run["bypassed"]))
        self.assertEqual((3, 2, 1), (run["lost"], run["layer_drops"], run["rebuffer_seconds"]))

    def test_a_log_without_fel_timing_is_not_a_run(self):
        self.assertIsNone(felbench.summarise("[playback] Session Aether offset=0 type=movie"))
        self.assertIsNone(felbench.summarise("[DoviFelComposer] gpu 6.0ms/frame over 120 frames"))


if __name__ == "__main__":
    unittest.main()
