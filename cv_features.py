"""Measurements of each fence line from the photo, the infrared and the laser, for classify_cv.py.

Everything is read in the frame from evidence.py, where the line runs left to right. The
photo measurements first find the straightest edge near the line: a fence or wall top and
its shadow make a sharp edge that stays put along the line, while leaves don't. Then they
read colour and infrared on that edge and either side of it.
"""
import numpy as np
from scipy.ndimage import map_coordinates

from evidence import ACROSS, STEP

SHIFTS = np.arange(-1.5, 1.5001, STEP)       # metres off the surveyed line to try
TILTS = np.radians(np.arange(-2, 2.01, 0.5))  # degrees off its direction
HALF = 0.3       # metres either side of an edge when measuring its contrast
WINDOW = 3.0     # metres: continuity is counted in pieces this long
STRONG = 12.0    # grey levels: an edge at least this sharp counts as there
LOW, HIGH = 0.5, 2.6
ROOF_GAP = 0.6   # metres: where a roof comes this close to the line, the photo edge is the roof's


def lane(image, frame, shift, tilt, offsets, cols):
    """Mean of an image along a straight line, at each offset across it: an across profile."""
    a = frame.start + (cols + 0.5) * STEP
    mid = (frame.start + frame.end) / 2
    centre = shift + np.tan(tilt) * (a - mid)
    out = []
    for d in offsets:
        rows = (ACROSS - (centre + d)) / STEP - 0.5
        out.append(map_coordinates(image, [rows, cols.astype(float)], order=1, mode="nearest"))
    return np.array(out)  # (offsets, cols)


def straight_edge(gray, frame, cols):
    """The straightest sharp edge within 1.5 m of the line: where it is, how sharp, how continuous.
    Only the given columns count: the rest are beside a building, whose roof edge would win."""
    if len(cols) < 20:
        return dict(edge=0.0, edge_shift=0.0, edge_tilt=0.0, edge_held=0.0)
    offsets = np.array([-HALF, 0.0, HALF])
    best = (0.0, 0.0, 0.0, None)
    for t in TILTS:
        for s in SHIFTS:
            p = lane(gray, frame, s, t, offsets, cols)
            step = p[0] - p[2]          # one side against the other, per column
            contrast = abs(step.mean())  # an edge that holds along the line survives the average
            if contrast > best[0]:
                best = (contrast, s, t, step)
    contrast, shift, tilt, step = best
    pieces = max(1, int(len(cols) * STEP // WINDOW))
    held = np.array([abs(c.mean()) >= STRONG and np.sign(c.mean()) == np.sign(step.mean())
                     for c in np.array_split(step, pieces)])
    return dict(edge=contrast, edge_shift=shift, edge_tilt=float(np.degrees(tilt)), edge_held=float(held.mean()))


def features(frame, rgba, ir, along, across, height, cls, roof=None, under_roof=None):
    """All measurements for one line. rgba (4, rows, cols) photo; ir (3, rows, cols) NIR, G, B.
    roof (rows, cols) marks buildings and their eaves on the grid: where one sits on the line the
    photo edge would be the roof's, so those stretches are left out. under_roof marks laser points
    inside building outlines, also left out: buildings are already mapped."""
    r, g, b, alpha = rgba
    valid = alpha > 0
    gray = (0.299 * r + 0.587 * g + 0.114 * b)
    on_line = slice(int((ACROSS - ROOF_GAP) / STEP), int((ACROSS + ROOF_GAP) / STEP))
    clear = np.ones(frame.cols, bool) if roof is None else ~roof[on_line].any(axis=0)
    cols = np.flatnonzero(clear)
    f = straight_edge(gray, frame, cols)
    f["clear"] = float(clear.mean())
    if not len(cols):  # all beside buildings: read colour along the whole line
        cols = np.arange(frame.cols)

    # Colour and infrared: on a 30 cm strip at the edge, and on 1.5 m either side.
    exg = (2 * g - r - b) / np.maximum(r + g + b, 1)
    ndvi = (ir[0] - ir[1]) / np.maximum(ir[0] + ir[1], 1)  # near infrared against green
    tilt = np.radians(f["edge_tilt"])
    strip = lane(exg, frame, f["edge_shift"], tilt, np.array([-0.15, 0, 0.15]), cols)
    f["green_on_edge"] = float(np.mean(strip))
    strip = lane(ndvi, frame, f["edge_shift"], tilt, np.array([-0.15, 0, 0.15]), cols)
    f["leafy_on_edge"] = float(np.mean(strip > 0.17))
    band = lane(ndvi, frame, f["edge_shift"], tilt, np.arange(-1.5, 1.51, 0.15), cols)
    f["leafy_band"] = float(np.mean(band > 0.17))
    on_line = lane(ndvi, frame, 0.0, 0.0, np.arange(-0.6, 0.61, 0.15), cols)
    f["leafy_line"] = float(np.mean(on_line > 0.17))
    f["texture"] = float(np.mean(np.abs(np.diff(lane(gray, frame, f["edge_shift"], tilt, np.arange(-1.0, 1.01, 0.15), cols), axis=1))))
    f["photo_cover"] = float(valid.mean())

    # Laser: what stands 0.5 to 2.6 m up within 1.5 m of the line, and how thin it is.
    up = (height >= LOW) & (height <= HIGH) & (cls != 2) & (cls != 6)
    if under_roof is not None:
        up &= ~under_roof
    near = up & (np.abs(across) <= 1.5)
    f["standing"] = int(near.sum())
    if near.sum() >= 10:
        centre = np.median(across[near])
        f["thin"] = float(np.mean(np.abs(across[near] - centre) <= 0.2))
        f["laser_built"] = float(np.mean(cls[near] == 1))
        f["laser_veg"] = float(np.mean(np.isin(cls[near], (3, 4, 5))))
        tall = (height > HIGH) & (np.abs(across) <= 1.5) & (cls != 6)
        f["canopy"] = float(tall.sum() / max(1, near.sum() + tall.sum()))
        f["laser_vs_edge"] = float(abs(centre - f["edge_shift"]))
    else:
        f.update(thin=None, laser_built=None, laser_veg=None, canopy=None, laser_vs_edge=None)
    return f
