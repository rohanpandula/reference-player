"""Fixed numerical probes for the optional NumPy FEL reference experiment."""
from pathlib import Path
import sys
import unittest

try:
    import numpy as np
except ModuleNotFoundError:
    np = None

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
if np is not None:
    from fel_resampling_reference import annexb, bilinear, reconstruct_identity


@unittest.skipIf(np is None, "optional FEL experiment requires NumPy")
class ResamplingReferenceTests(unittest.TestCase):
    def test_constant_and_single_texel(self):
        for code in (0, 512, 1023):
            for function in (bilinear, annexb):
                np.testing.assert_array_equal(function([[code]], 2, 2), np.full((2, 2), code))
                np.testing.assert_array_equal(function(np.full((3, 4), code), 8, 6), np.full((6, 8), code))

    def test_horizontal_phase_and_half_up(self):
        ramp = np.tile(np.arange(8) * 4 + 100, (4, 1))
        np.testing.assert_array_equal(bilinear(ramp, 16, 8)[3, 6:10], [111, 113, 115, 117])
        np.testing.assert_array_equal(annexb(ramp, 16, 8)[3, 6:10], [112, 114, 116, 118])
        np.testing.assert_array_equal(bilinear([[0, 2]], 4, 2)[0], [0, 1, 2, 2])

    def test_vertical_ramp_phase_and_edges(self):
        ramp = np.repeat((np.arange(6) * 128)[:, None], 4, axis=1)
        np.testing.assert_array_equal(annexb(ramp, 8, 12)[:, 2],
                                      [0, 23, 93, 160, 224, 288, 352, 416, 480, 547, 617, 649])
        np.testing.assert_array_equal(bilinear(ramp, 8, 12)[:, 2],
                                      [0, 32, 96, 160, 224, 288, 352, 416, 480, 544, 608, 640])

    def test_horizontal_impulse_coefficients(self):
        impulse = np.zeros((2, 9), dtype=np.int32)
        impulse[:, 4] = 1023
        np.testing.assert_array_equal(annexb(impulse, 18, 4)[1],
                                      [0, 5, 0, 23, 0, 0, 0, 613, 1023, 613, 0, 0, 0, 23, 0, 5, 0, 0])

    def test_vertical_impulse_coefficients(self):
        impulse = np.zeros((7, 2), dtype=np.int32)
        impulse[3] = 1023
        np.testing.assert_array_equal(annexb(impulse, 4, 14)[:, 0],
                                      [0, 0, 0, 0, 0, 232, 887, 887, 232, 0, 0, 0, 0, 0])

    def test_saturated_edge_keeps_filter_overshoot_until_lut(self):
        edge = np.tile([0, 0, 0, 0, 1023, 1023, 1023, 1023], (2, 1))
        filtered = annexb(edge, 16, 4)
        np.testing.assert_array_equal(filtered[0],
                                      [0, 5, 0, 29, 0, 0, 0, 512, 1023, 1125, 1023, 994, 1023, 1018, 1023, 1023])
        self.assertEqual(reconstruct_identity(filtered)[0, 9], 2559)

    def test_vertical_stage_clips_before_horizontal_stage(self):
        np.testing.assert_array_equal(annexb([[0, 1023], [1023, 0]], 4, 4),
                                      [[0, 548, 1095, 1204], [208, 512, 815, 875],
                                       [815, 512, 208, 148], [1095, 548, 0, 0]])

    def test_odd_size_is_cropped_double_grid_and_other_ratios_fall_back(self):
        source = np.arange(20).reshape(4, 5) * 37
        np.testing.assert_array_equal(annexb(source, 9, 7), annexb(source, 10, 8)[:7, :9])
        for width, height in ((5, 4), (11, 8), (10, 9), (3, 2)):
            np.testing.assert_array_equal(annexb(source, width, height), bilinear(source, width, height))

    def test_signed_nlq_deadzone_and_lut_clamp(self):
        np.testing.assert_array_equal(reconstruct_identity([-20, 0, 510, 511, 512, 513, 514, 1023, 65535]),
                                      [1537, 1537, 2047, 2048, 2048, 2049, 2050, 2559, 2559])

    def test_rejects_malformed_inputs(self):
        for function in (bilinear, annexb):
            for plane in ([], [1, 2], [[-1]], [[1024]], [[1.5]], [[True]], [[float("nan")]]):
                with self.subTest(function=function.__name__, plane=plane), self.assertRaises(ValueError):
                    function(plane, 2, 2)
            for dimensions in ((0, 2), (2, -1), (2.5, 2), (True, 2)):
                with self.assertRaises(ValueError):
                    function([[512]], *dimensions)
        with self.assertRaises(ValueError):
            reconstruct_identity([512.5])


if __name__ == "__main__":
    unittest.main()
