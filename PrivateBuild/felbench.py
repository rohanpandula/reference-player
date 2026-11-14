#!/usr/bin/env python3
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Summarise a Dolby Vision Profile 7 FEL run from an Apple TV console log.

    python3 PrivateBuild/felbench.py fel.log

It times nothing itself: it reads the lines the playback engine writes while it composes the two
layers. The GPU line is debug-level, so capture the log with the launch command in the README
("FEL on real hardware"). Keep one film per log: the counters are read across the whole file.
"""
import re
import statistics
import sys

ROUTE = re.compile(r"DV P7 dual-layer route: taken \(el_type=FEL, (\d+x\d+)@([\d.]+)")
GPU = re.compile(r"\[DoviFelComposer\] gpu ([\d.]+)ms/frame over (\d+) frames")
TOTALS = re.compile(r"totals: composed=(\d+), bypassDeadline=(\d+), bypassHeldWall=(\d+), bypassBackstop=(\d+)")
DIAG = re.compile(r"\[SWDiag\].*?rebuf=(\w).*?lost=(\d+).*?layerDrop=(\d+)")


def summarise(text):
    """The run as a dict, or None when the log holds no GPU timing for a FEL title."""
    route, gpu = ROUTE.search(text), [(float(ms), int(n)) for ms, n in GPU.findall(text)]
    if not route or not gpu:
        return None
    # The totals count up within one playback and start again on the next, so add each run's last line.
    composed = bypassed = 0
    last = None
    for found in [*TOTALS.finditer(text), None]:
        now = tuple(map(int, found.groups())) if found else None
        if last and (now is None or now[0] < last[0]):
            composed, bypassed = composed + last[0], bypassed + sum(last[1:])
        last = now
    diag = DIAG.findall(text)
    times = [ms for ms, _ in gpu]
    budget = 1000 / float(route.group(2))
    return {
        "source": f"{route.group(1)} at {route.group(2)} fps",
        "windows": len(gpu),
        "frames_timed": sum(n for _, n in gpu),
        "gpu_median_ms": statistics.median(times),
        "gpu_min_ms": min(times),
        "gpu_max_ms": max(times),
        "frame_budget_ms": budget,
        "budget_used": statistics.median(times) / budget,
        "composed": composed,
        "bypassed": bypassed,
        "lost": max((int(lost) for _, lost, _ in diag), default=0),
        "layer_drops": max((int(drops) for _, _, drops in diag), default=0),
        "rebuffer_seconds": sum(1 for rebuf, _, _ in diag if rebuf != "n"),
    }


def main(argv):
    if len(argv) != 2:
        sys.exit(__doc__)
    with open(argv[1], errors="replace") as log:
        run = summarise(log.read())
    if run is None:
        sys.exit("No FEL GPU timing in this log. Was a Profile 7 FEL title played, and was the app launched "
                 "with OS_ACTIVITY_MODE=debug?")
    print(f"source       {run['source']}, Profile 7 FEL, dual-layer route")
    print(f"gpu          {run['gpu_median_ms']:.1f} ms/frame median ({run['gpu_min_ms']:.1f} to {run['gpu_max_ms']:.1f}) "
          f"over {run['windows']} windows, {run['frames_timed']:,} frames")
    print(f"frame time   {run['frame_budget_ms']:.1f} ms, so the median is {run['budget_used']:.0%} of it")
    print(f"composed     {run['composed']:,} frames, {run['bypassed']:,} sent as base layer only")
    print(f"dropped      {run['lost']} lost before the display layer, {run['layer_drops']} dropped by it, "
          f"{run['rebuffer_seconds']} s rebuffering")


if __name__ == "__main__":
    main(sys.argv)
