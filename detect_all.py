# /// script
# requires-python = ">=3.11"
# dependencies = ["laspy[lazrs]", "numpy", "pillow", "pyproj", "rasterio", "scipy", "shapely"]
# ///
"""Every property boundary in the pilot tile, sorted from the laser and the photo (v12).

  AREA=mtalbert uv run detect_all.py

v10 looks only at the boundaries a flow path crosses. This looks at every boundary two
parcels share inside the tile, with the same model and the same 6 m pieces
(classify_cv.py), and nothing about flow.

Each boundary is drawn where the laser found something, not on the legal line: the scan
fits a line through what stands within 1.5 m of the boundary (scan_fences.py), and each
metre of that line where something 0.5 to 2.6 m high stands takes the class of its 6 m
piece. Metres where the laser saw the ground and nothing on it are open: a driveway, a
gate, a path. A retaining wall is where the bare ground steps across the boundary in the
1 m DEM, drawn on the legal line.

The model is fitted once on the AI reference labels, leaving out every line people
checked, so the checks score it fairly. Lines people checked carry their answer, so the
page can show where the two agree. Writes allfences.js for indexv12.html and
allfences.geojson.
"""
import csv
import json
import math
from collections import Counter
from datetime import date

import numpy as np
import shapely
from pyproj import Transformer
from shapely import STRtree, line_merge
from shapely.geometry import LineString, MultiLineString, Polygon, shape
from shapely.ops import transform as reproject

from classify_cv import BUILT_LABELS, CANOPY, FEW, HI, MIXED, PIECE, PLANT_LABELS, SOURCES, chance, cut, fit, inputs, sort
from classify_fences import EYE, PIXEL as IR_PIXEL, hand_checks, infrared
from cv_features import features
from evidence import ACROSS, Frame, aerial_in, buildings, hold, infrared_in
from fetch_data import OUT, arcgis_features
from fetch_parcels import PARCELS_URL
from scan_fences import MIN_SEGMENT, Index, boundaries, ground_under, load_js, read_points, scan

PUBLIC = {"Road", "Hydro"}
NEAR, FAR = 1.5, 4.0  # metres either side of the line: the ground step, and the slope beyond it
RETAIN = 0.5          # metres of step, beyond the slope around it, that a retaining wall leaves
RETAIN_RUN = 6.0      # metres of line that must step that much for the line to count as retaining
GAP = 2               # metres: shorter breaks are bridged, and shorter bits standing alone dropped
STANDS = ("fence", "hedge", "unknown")

# What people saw, in the map's classes.
SEEN = {"fence or wall": "fence", "vegetation": "hedge", "none": "none", "building": "building"}


def flat(g):
    """Every simple part of a geometry, however deep its collections go."""
    return [p for part in getattr(g, "geoms", [g]) for p in (flat(part) if hasattr(part, "geoms") else [part])]


def shared_lines(parcels, tile):
    """Every boundary two parcels share with its middle inside the tile, as NZTM segments."""
    ids = list(parcels)
    shapes = [parcels[i]["shape"] for i in ids]
    tree = STRtree(shapes)
    out = []
    for i, a in enumerate(shapes):
        for j in tree.query(a.buffer(0.3)):
            if j <= i:
                continue
            pa, pb = parcels[ids[i]], parcels[ids[j]]
            if pa["intent"] in PUBLIC and pb["intent"] in PUBLIC:
                continue
            shared = a.boundary.intersection(shapes[j].buffer(0.3))
            pieces = [g for g in flat(shared) if g.geom_type == "LineString" and not g.is_empty]
            if not pieces:
                continue
            merged = line_merge(MultiLineString(pieces))
            segments = []
            for part in getattr(merged, "geoms", [merged]):
                xy = np.asarray(part.simplify(0.1).coords)
                segments += [(p, q) for p, q in zip(xy[:-1], xy[1:]) if np.hypot(*(q - p)) >= MIN_SEGMENT]
            if not segments:
                continue
            middle = MultiLineString([LineString(s) for s in segments])
            if not tile.contains(middle.interpolate(0.5, normalized=True)):
                continue
            street = pa["intent"] in PUBLIC or pb["intent"] in PUBLIC
            out.append({"between": [ids[i], ids[j]], "street": int(street), "segments": segments})
    return out


def ground_steps(lines, dem_tiles):
    """For every metre of every segment, how far the bare ground drops across the line beyond
    the slope either side. One DEM read for the lot."""
    where, shape_of = [], []
    for line in lines:
        for p, q in line["segments"]:
            length = float(np.hypot(*(q - p)))
            u = (q - p) / length
            n = np.array([-u[1], u[0]])
            centres = np.minimum(np.arange(math.ceil(length)) + 0.5, length - 0.01)
            for t in centres:
                c = p + u * t
                where += [c + n * d for d in (-FAR, -NEAR, NEAR, FAR)]
            shape_of.append(len(centres))
    xy = np.array(where)
    z = ground_under(np.column_stack([xy, np.zeros(len(xy))]), dem_tiles).reshape(-1, 4)
    far_l, near_l, near_r, far_r = z.T
    slope = ((near_l - far_l) + (far_r - near_r)) / 2 / (FAR - NEAR)
    excess = np.nan_to_num(np.abs((near_r - near_l) - slope * 2 * NEAR))
    out, at = [], 0
    for n in shape_of:
        out.append(excess[at:at + n])
        at += n
    return out


def runs_of(values):
    """(start, end, value) for each run of equal values."""
    out, start = [], 0
    for i in range(1, len(values) + 1):
        if i == len(values) or values[i] != values[start]:
            out.append((start, i, values[start]))
            start = i
    return out


def tidy(classes):
    """Bridge breaks shorter than GAP between things standing, then drop bits standing alone
    shorter than GAP (a letterbox, a post by a driveway)."""
    classes = list(classes)
    rs = runs_of(classes)
    for k, (a, b, v) in enumerate(rs):
        if v == "open" and b - a < GAP and 0 < k < len(rs) - 1 and rs[k - 1][2] in STANDS and rs[k + 1][2] in STANDS:
            classes[a:b] = [rs[k - 1][2]] * (b - a)
    rs = runs_of(classes)
    for k, (a, b, v) in enumerate(rs):
        if v in STANDS and b - a < GAP and all(rs[j][2] == "open" for j in (k - 1, k + 1) if 0 <= j < len(rs)):
            classes[a:b] = ["open"] * (b - a)
    return classes


def piece_class(score, measured):
    if score is None:
        return "unknown"  # too few points in this piece to call
    if score >= HI:
        return "fence"
    if score >= MIXED or (measured["canopy"] or 0) > CANOPY:
        return "unknown"
    return "hedge"


def main():
    grid, ranked = load_js("data.js"), load_js("fences.js")
    to_nztm = Transformer.from_crs(4326, 2193, always_xy=True)
    to_wgs = Transformer.from_crs(2193, 4326, always_xy=True)
    tile = Polygon([to_nztm.transform(lon, lat) for lat, lon in grid["focus"]["corners"]])

    print("finding every shared boundary in the tile")
    parcels = {}
    for f in arcgis_features(PARCELS_URL, grid["bounds"], "id,parcel_intent"):
        parcels[f["properties"]["id"]] = {"intent": f["properties"]["parcel_intent"],
                                           "shape": reproject(to_nztm.transform, shape(f["geometry"])).buffer(0)}
    lines = shared_lines(parcels, tile)
    print(f"  {len(lines)} boundaries")

    # v10's lines, for fitting: the AI reference labels are on those.
    train_lines = boundaries(ranked["fences"], grid["bounds"])
    every = [s for line in lines for s in line["segments"]] + [s for segs in train_lines for s in segs]
    points, _ = read_points(every)
    height = points[:, 2] - ground_under(points, grid["source"]["tiles"])
    index = Index(points)
    ir, west, north = infrared(points)
    roofs = buildings(grid["bounds"])
    hold(points[:, 0].min() - 10, points[:, 1].min() - 10, points[:, 0].max() + 10, points[:, 1].max() + 10)
    inside = shapely.contains_xy(buildings(grid["bounds"], 0.0), points[:, 0], points[:, 1])

    def measure(segs, margin=None):
        frame = Frame(segs) if margin is None else Frame(segs, margin=margin)
        along, across = frame.local(points[:, :2])
        keep = (along >= frame.start) & (along <= frame.end) & (np.abs(across) <= ACROSS)
        return features(frame, aerial_in(frame), infrared_in(frame, ir, west, north, IR_PIXEL), along[keep], across[keep],
                        height[keep], points[keep, 3].astype(int), frame.blocked(roofs), inside[keep])

    # One model, fitted on the AI labels with every checked line left out.
    print("fitting")
    checks = hand_checks()
    labels = {int(r["fence"]): r for r in csv.DictReader(open(OUT / "reference" / "claude_labels.csv"))}
    train = [k for k, segs in enumerate(train_lines) if segs and k not in checks
             and labels.get(k, {}).get("structure") in BUILT_LABELS | PLANT_LABELS]
    whole = {k: measure(train_lines[k]) for k in train}
    train = [k for k in train if whole[k]["standing"] >= FEW]
    model = fit(np.array([inputs(whole[k]) for k in train]),
                np.array([labels[k]["structure"] in BUILT_LABELS for k in train], float))

    # What people saw, keyed by the two parcels either side.
    seen = {}
    for k, c in checks.items():
        pair = frozenset(int(p) for p in (c["parcel_a"], c["parcel_b"]))
        seen[pair] = {"n": int(c["n"]), "structure": c["structure"], "water": c["water"], "sure": c["sure"],
                      "class": SEEN.get(EYE.get(c["structure"]), "other")}

    def ll(p):
        x, y = to_wgs.transform(p[0], p[1])
        return [round(y, 7), round(x, 7)]

    print("reading the ground steps")
    steps = iter(ground_steps(lines, grid["source"]["tiles"]))

    print(f"sorting {len(lines)} boundaries")
    rows, metres = [], Counter()
    for i, line in enumerate(lines):
        segs = line["segments"]
        m = measure(segs)
        laser = scan(segs, points, height, index, trace=True)
        pieces = cut(segs)
        measured = [measure([pc], margin=0.0) for pc in pieces]
        scores = [chance(model, inputs(x)) if x["standing"] >= FEW else None for x in measured]
        best = max([s for s in scores if s is not None], default=0.0)
        kind = sort(m, laser, best, chance(model, inputs(m)) if m["standing"] >= FEW else 0.0)[0]

        parts, retained, stepped, first = [], [], [], 0
        for (p, q), tr in zip(segs, laser.get("trace") or [None] * len(segs)):
            length = float(np.hypot(*(q - p)))
            n_pieces = max(1, int(round(length / PIECE)))
            excess = next(steps)
            if tr is not None:
                # Each metre that stands takes its 6 m piece's class; seen and empty is open.
                piece_of = lambda a: first + min(n_pieces - 1, int(a / (length / n_pieces)))
                classes = ["building" if tr["built"][k] else
                           piece_class(scores[piece_of(k + 0.5)], measured[piece_of(k + 0.5)]) if tr["standing"][k] else
                           "open" if tr["seen"][k] else "unknown"
                           for k in range(len(tr["standing"]))]
                a_end, b_end = tr["ends"]
                at = lambda a: ll(a_end + (b_end - a_end) * min(a, length) / length)
                for a, b, v in runs_of(tidy(classes)):
                    if v != "building":
                        parts.append([v, [at(a), at(b)]])
                        metres[v] += min(b, length) - a
            # Retaining: runs of the legal line where the ground steps, gaps under GAP bridged.
            stepping = tidy(["unknown" if e >= RETAIN else "open" for e in excess])
            for a, b, v in runs_of(stepping):
                if v == "unknown":
                    retained.append([ll(p + (q - p) * a / length), ll(p + (q - p) * min(b, length) / length)])
                    stepped += excess[a:b].tolist()
            first += n_pieces

        check = seen.get(frozenset(line["between"]))
        rows.append({
            "id": i, "between": line["between"], "street": line["street"],
            "length_m": round(sum(float(np.hypot(*(q - p))) for p, q in segs), 1),
            "class": kind, "retaining": len(stepped) >= RETAIN_RUN, "step_m": round(float(np.median(stepped)), 2) if stepped else 0.0,
            "stepped_m": len(stepped), "chance_built": round(best, 2), "height_m": laser.get("top"), "standing": m["standing"],
            "check": check, "line": [[ll(p), ll(q)] for p, q in segs], "parts": parts,
            "retained": retained if len(stepped) >= RETAIN_RUN else [],
        })
        if (i + 1) % 100 == 0:
            print(f"  {i + 1}", flush=True)

    # How it does where people looked, line by line, as people answered.
    checked = [r for r in rows if r["check"]]
    pairs = Counter((r["check"]["class"], r["class"]) for r in checked)
    retain = Counter((r["check"]["structure"] == "retaining wall", r["retaining"]) for r in checked)
    summary = {
        "tile": grid["focus"]["name"], "made": date.today().isoformat(), "retain": RETAIN, "retainRun": RETAIN_RUN,
        "trainedOn": len(train), "counts": dict(Counter(r["class"] for r in rows)),
        "metres": {k: round(v) for k, v in metres.items()},
        "retaining": sum(r["retaining"] for r in rows),
        "retainedMetres": sum(r["stepped_m"] for r in rows if r["retaining"]),
        "checked": len(checked),
        "agreement": [[a, b, n] for (a, b), n in sorted(pairs.items())],
        "retainScore": {"wall_found": retain[(True, True)], "wall_missed": retain[(True, False)],
                        "false_alarm": retain[(False, True)], "agree_none": retain[(False, False)]},
        "sources": {k: SOURCES[k] for k in ("laser", "photo", "infrared", "buildings", "parcels")}
                   | {"ground": "LINZ Auckland Part 1 LiDAR 1 m DEM 2024"},
    }
    print(json.dumps({k: summary[k] for k in ("counts", "metres", "retaining", "retainedMetres", "checked", "agreement", "retainScore")}, indent=1))

    (OUT / "allfences.js").write_text("globalThis.ALLFENCES_DATA = " + json.dumps({**summary, "lines": rows}, separators=(",", ":"), default=float) + ";\n")
    name = {"fence": "fence or wall", "hedge": "hedge or plants", "unknown": "can't tell", "open": "open (driveway, gate, gap)"}
    features_out = []
    for r in rows:
        base = {"line": r["id"], "parcels": r["between"], "street": r["street"], "checked_as": r["check"]["structure"] if r["check"] else None}
        for v, (a, b) in r["parts"]:
            features_out.append({"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[a[1], a[0]], [b[1], b[0]]]},
                                 "properties": {"class": name[v], **base}})
        for a, b in r["retained"]:
            features_out.append({"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[a[1], a[0]], [b[1], b[0]]]},
                                 "properties": {"class": "retaining (ground steps 0.5 m or more)", **base}})
    collection = {
        "type": "FeatureCollection",
        "name": f"What stands along every property boundary in {summary['tile']}, from the laser and the aerial photo",
        "disclaimer": "Not authoritative. A hackathon prototype (Auckland, 16 October 2026): what the laser and photo suggest "
                      "stands along each boundary, drawn where the laser found it, for a person to check. The model was "
                      "fitted on AI-made labels.",
        "made": summary["made"], "sources": summary["sources"],
        "features": features_out,
    }
    (OUT / "allfences.geojson").write_text(json.dumps(collection, default=float))
    print("wrote allfences.js and allfences.geojson")


if __name__ == "__main__":
    main()
