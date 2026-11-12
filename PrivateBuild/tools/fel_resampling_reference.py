"""CPU luma oracles for the optional FEL experiment (requires NumPy).

Annex B is the informative reference in ETSI GS CCM 001 V1.1.1, B.3:
https://www.etsi.org/deliver/etsi_gs/CCM/001_099/001/01.01.01_60/gs_ccm001v010101p.pdf
The two filters use different horizontal phases. Agreement with either oracle
checks its implementation, not fidelity to a title's unavailable master.
"""
from __future__ import annotations

import numpy as np


def _plane(el, outwidth, outheight):
    el = np.asarray(el)
    if el.ndim != 2 or not all(el.shape):
        raise ValueError("EL must be a nonempty two-dimensional plane")
    if el.dtype.kind not in "iu" or np.any(el > 1023) or np.any(el < 0):
        raise ValueError("EL must contain integer 10-bit codes (0...1023)")
    for size in (outwidth, outheight):
        if isinstance(size, (bool, np.bool_)) or not isinstance(size, (int, np.integer)) or size < 1:
            raise ValueError("output dimensions must be positive integers")
    return el


def bilinear(el, outwidth, outheight):
    """Centered bilinear luma with float32 arithmetic and final half-up rounding.

    The phase is (site + 0.5) * EL_size / output_size - 0.5 on each axis.
    At 2:1 and 1:1 all weights and intermediate code values are exactly binary
    representable; other ratios can differ if a GPU contracts float operations.
    """
    el = _plane(el, outwidth, outheight).astype(np.float32)
    height, width = el.shape
    x = (np.arange(outwidth, dtype=np.float32) + np.float32(0.5)) * np.float32(width / outwidth) - np.float32(0.5)
    y = (np.arange(outheight, dtype=np.float32) + np.float32(0.5)) * np.float32(height / outheight) - np.float32(0.5)
    x0, y0 = np.floor(x).astype(np.int64), np.floor(y).astype(np.int64)
    fx, fy = x - np.floor(x), (y - np.floor(y))[:, None]
    xa, xb = np.clip(x0, 0, width - 1), np.clip(x0 + 1, 0, width - 1)
    ya, yb = np.clip(y0, 0, height - 1), np.clip(y0 + 1, 0, height - 1)
    top = el[ya[:, None], xa] * (np.float32(1) - fx) + el[ya[:, None], xb] * fx
    bottom = el[yb[:, None], xa] * (np.float32(1) - fx) + el[yb[:, None], xb] * fx
    return np.floor(top * (np.float32(1) - fy) + bottom * fy + np.float32(0.5)).astype(np.int32)


def annexb(el, outwidth, outheight):
    """Integer Annex B.3 luma: vertical four taps, then horizontal eight taps.

    Each stage rounds half up and clips to 0...65535, with replicated edges.
    Odd output sizes crop the final sample from a doubled EL grid. As in the
    composer, any size other than 2*EL or 2*EL-1 on either axis uses bilinear.
    """
    el = _plane(el, outwidth, outheight)
    height, width = el.shape
    if outwidth not in (2 * width, 2 * width - 1) or outheight not in (2 * height, 2 * height - 1):
        return bilinear(el, outwidth, outheight)
    el = el.astype(np.int64)
    vertical = np.empty((outheight, width), dtype=np.int64)
    for phase, offsets, taps in ((0, (-2, -1, 0, 1), (-3, 29, 111, -9)),
                                (1, (-1, 0, 1, 2), (-9, 111, 29, -3))):
        rows = np.arange(phase, outheight, 2) // 2
        acc = np.zeros((len(rows), width), dtype=np.int64)
        for offset, tap in zip(offsets, taps):
            acc += tap * el[np.clip(rows + offset, 0, height - 1)]
        vertical[phase::2] = np.clip((acc + 64) // 128, 0, 65535)
    result = np.empty((outheight, outwidth), dtype=np.int32)
    result[:, ::2] = vertical[:, : (outwidth + 1) // 2]
    columns = np.arange(outwidth // 2)
    acc = np.zeros((outheight, len(columns)), dtype=np.int64)
    for offset, tap in zip(range(-3, 5), (22, 94, -524, 2456, 2456, -524, 94, 22)):
        acc += tap * vertical[:, np.clip(columns + offset, 0, width - 1)]
    result[:, 1::2] = np.clip((acc + 2048) // 4096, 0, 65535)
    return result


def reconstruct_identity(codes):
    """12-bit VDR for BL=512, identity prediction, offset=512, scale=2048.

    Fixture NLQ: shift=11, threshold=0, vdrInMax=512. The shader first clamps
    the EL code to the 1024-entry LUT; negative half steps stay signed until
    the 16-bit prediction and residual are added and rounded to 12 bits.
    """
    codes = np.asarray(codes)
    if codes.dtype.kind not in "iu":
        raise ValueError("resampled codes must be integers")
    delta = np.clip(codes, 0, 1023).astype(np.int64) - 512
    value = (2 * delta - np.sign(delta)) * 2048
    residual16 = np.clip(value, -(512 << 12), 512 << 12) // 256
    return np.clip((32768 + residual16 + 8) // 16, 0, 4095).astype(np.int32)
