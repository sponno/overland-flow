# /// script
# requires-python = ">=3.11"
# dependencies = ["laspy[lazrs]", "numpy", "pillow", "pyproj", "rasterio", "scipy", "shapely"]
# ///
"""Fence, hedge, unknown or nothing: the laser and the 7.5 cm aerial photo together (v10).

  AREA=mtalbert uv run classify_cv.py

Needs what classify_fences.py needs, plus reference/claude_labels.csv. Writes detected.js
for the page and detected.geojson in the fields of Healthy Waters' challenge memo.

Each fence line is cut into pieces about 6 m long, and each piece is measured
(cv_features.py):
- laser_built: the share of standing laser points the survey left unclassified, which is
  where a fence or wall lands (a hedge lands in vegetation);
- thin: the share of standing points within 20 cm of their middle: a fence is a sheet, a
  hedge is deep;
- edge: how sharp the straightest edge within 1.5 m of the line is in the aerial photo,
  averaged along it. A fence top and its shadow make an edge that holds; leaves don't.
Building outlines (LINZ) are left out of both, since buildings are already mapped.

A logistic regression on those three gives each piece a chance of being built. A line is a
fence where any piece reaches HI: fences often show only where the planting along them
thins out. The fence's geometry is the run of pieces that reach it.

The regression is fitted on whole lines labelled in reference/claude_labels.csv: 101 lines
labelled by Claude from the evidence chips (make_chips.py). Those labels are AI-made and
unchecked, and see only what shows from above, so they are for fitting and development
only; the score that counts is against the human checks in checks/, which are left out of
the fit. Each other line is scored by a model fitted without it, so no line grades itself.
"""
import csv
import json
from datetime import date

import numpy as np
import shapely
from pyproj import Transformer
from shapely.geometry import LineString, MultiLineString
from shapely.ops import nearest_points

from classify_fences import EYE, PIXEL as IR_PIXEL, flow_test, hand_checks, infrared
from cv_features import features
from evidence import ACROSS, Frame, aerial_in, buildings, hold, infrared_in
from fetch_data import OUT
from scan_fences import CONTROL_SHIFT, Index, boundaries, ground_under, load_js, read_points, scan, shifted

INPUTS = ["laser_built", "thin", "edge"]
PIECE = 6.0       # metres: lines are judged in pieces about this long
HI = 0.8          # chance of built: a piece this sure makes the line a fence
SURE = 0.95       # and this sure, with high confidence
MIXED = 0.5       # best piece between this and HI: can't tell
CANOPY = 0.6      # share of points above 2.6 m: more than this and it is under trees
FEW = 10          # standing points: fewer than this is too little to call
BUILT_LABELS = {"fence", "wall", "gate", "other", "retaining wall"}
PLANT_LABELS = {"hedge", "trees"}
TYPES = ["fence", "hedge", "unknown", "none", "building"]
SOURCES = {
    "laser": "LINZ Auckland Part 1 LiDAR point cloud, captured April to June 2024",
    "photo": "LINZ Auckland 0.075 m urban aerial photos, captured January 2024 to March 2025",
    "infrared": "Auckland Council colour infrared aerial photos, captured 2023 to 2025",
    "buildings": "LINZ NZ Building Outlines",
    "parcels": "LINZ NZ Primary Parcels, fetched October 2026",
    "flow": "Auckland Council Overland Flow Paths (WSP 2019) and this repo's flow model on the 2024 1 m DEM",
}


def inputs(f):
    return [f["laser_built"] or 0.0, f["thin"] or 0.0, float(np.log1p(f["edge"]))]


def fit(X, y, ridge=1.0, steps=60):
    """Logistic regression with a light ridge, by Newton's method. Inputs are standardised."""
    mean, std = X.mean(0), X.std(0) + 1e-9
    Z = np.column_stack([(X - mean) / std, np.ones(len(X))])
    w = np.zeros(Z.shape[1])
    pen = np.diag([ridge] * (Z.shape[1] - 1) + [0.0])
    for _ in range(steps):
        p = 1 / (1 + np.exp(-Z @ w))
        w -= np.linalg.solve(Z.T @ (Z * (p * (1 - p))[:, None]) + pen, Z.T @ (p - y) + pen @ w)
    return mean, std, w


def chance(model, x):
    mean, std, w = model
    z = np.append((np.asarray(x) - mean) / std, 1.0)
    return float(1 / (1 + np.exp(-z @ w)))


def cut(segs):
    """The line in pieces about PIECE long, each a straight bit of one segment."""
    out = []
    for p, q in segs:
        n = max(1, int(round(np.hypot(*(q - p)) / PIECE)))
        for i in range(n):
            a, b = p + (q - p) * i / n, p + (q - p) * (i + 1) / n
            if np.hypot(*(b - a)) >= 2.0:
                out.append((a, b))
    return out


def runs(pieces, scores):
    """Join neighbouring pieces at or over HI into stretches of fence."""
    out, current = [], None
    for (a, b), s in zip(pieces, scores):
        if s is not None and s >= HI:
            if current is not None and np.allclose(current[1], a):
                current = (current[0], b)
            else:
                if current is not None:
                    out.append(current)
                current = (a, b)
        elif current is not None:
            out.append(current)
            current = None
    if current is not None:
        out.append(current)
    return out


def sort(line, laser, best, p_line):
    """Council's structure type, its primary class, a plain detail, and how sure we are of the type."""
    v = laser["verdict"]
    if v == "building":
        return "building", "building", "a building stands on the line", 0.9
    if v == "none":
        return "unknown", "uncertain", "no shared line in LINZ parcels", 0.0
    if v == "hidden":
        return "unknown", "uncertain", "hidden from the laser by roofs or canopy", 0.0
    if v == "open" or line["standing"] < FEW:
        return "none", "none", "nothing standing along most of the line", 0.0
    if best >= HI:
        return "fence", "linear built barrier", "fence or wall: the two can't be told apart from the air", best
    if best >= MIXED:
        return "unknown", "uncertain", "built and planted mixed, often a fence with plants over it", 0.5
    if (line["canopy"] or 0) > CANOPY:
        return "unknown", "vegetation", "under tree canopy: what is below can't be seen", 1 - p_line
    return "hedge", "vegetation", "hedge or shrubs", 1 - p_line


def relation(line, council):
    """How the line sits against Council's own flow paths, in the memo's categories."""
    if line.is_empty:
        return "outside", None
    d = line.distance(council)
    _, b = nearest_points(line, council)
    near = council.intersection(b.buffer(3))

    def heading(g):
        coords = [c for part in getattr(g, "geoms", [g]) for c in getattr(part, "coords", [])]
        if len(coords) < 2:
            return None
        (x0, y0), (x1, y1) = coords[0], coords[-1]
        return np.degrees(np.arctan2(y1 - y0, x1 - x0))
    h1, h2 = heading(line), heading(near)
    angle = None if h1 is None or h2 is None else abs((h1 - h2 + 90) % 180 - 90)
    if d == 0:
        return ("crosses" if angle is None or angle >= 30 else "intersects"), d
    if d <= 3:
        return ("parallels" if angle is not None and angle < 30 else "adjacent"), d
    return ("adjacent" if d <= 10 else "outside"), d


def main():
    grid, ranked, rules = load_js("data.js"), load_js("fences.js"), load_js("rules.js")["fences"]
    before = load_js("structures.js")["fences"]  # v9: change since 2016
    fences = ranked["fences"]
    lines = boundaries(fences, grid["bounds"])
    controls = [segs for segs in (shifted(s, by) for s in lines for by in (CONTROL_SHIFT, -CONTROL_SHIFT)) if segs]
    points, _ = read_points([s for segs in lines + controls for s in segs])
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

    def judge(segs, model):
        """Measure a line whole and in pieces; score the pieces."""
        pieces = cut(segs)
        measured = [measure([pc], margin=0.0) for pc in pieces]
        scores = [chance(model, inputs(m)) if m["standing"] >= FEW else None for m in measured]
        return pieces, measured, scores

    print("measuring the fence lines")
    whole = [measure(segs) if segs else None for segs in lines]
    lasers = [scan(segs, points, height, index) if segs else {"verdict": "none"} for segs in lines]

    # Fit on the AI reference labels, leaving out the lines people checked.
    labels = {int(r["fence"]): r for r in csv.DictReader(open(OUT / "reference" / "claude_labels.csv"))}
    checks = hand_checks()
    train = [k for k, f in enumerate(whole) if f and f["standing"] >= FEW and k not in checks
             and labels.get(k, {}).get("structure") in BUILT_LABELS | PLANT_LABELS]
    X = np.array([inputs(whole[k]) for k in train])
    y = np.array([labels[k]["structure"] in BUILT_LABELS for k in train], float)
    model = fit(X, y)
    models = {k: fit(np.delete(X, i, 0), np.delete(y, i)) for i, k in enumerate(train)}  # each without itself

    print("scoring the pieces")
    judged = [judge(segs, models.get(k, model)) if segs else ([], [], []) for k, segs in enumerate(lines)]

    print("scoring the lines 4 m off")
    control_calls = []
    for segs in controls:
        line = measure(segs)
        _, _, scores = judge(segs, model)
        best = max([s for s in scores if s is not None], default=0.0)
        control_calls.append(sort(line, scan(segs, points, height, index), best, chance(model, inputs(line)))[0])

    to_nztm = Transformer.from_crs(4326, 2193, always_xy=True)
    council = MultiLineString([LineString([to_nztm.transform(*c) for c in path["c"]]) for path in grid["olfp"] if len(path["c"]) > 1])
    to_wgs = Transformer.from_crs(2193, 4326, always_xy=True)

    def latlng(pairs):
        out = []
        for a, b in pairs:
            (x1, x2), (y1, y2) = to_wgs.transform([a[0], b[0]], [a[1], b[1]])
            out.append([[round(x1, 7), round(y1, 7)], [round(x2, 7), round(y2, 7)]])
        return out

    tile = grid.get("focus", {}).get("name", "area")
    features_out, rows = [], []
    for k, (segs, laser, rule) in enumerate(zip(lines, lasers, rules)):
        line = whole[k] or {"standing": 0, "canopy": None, "edge": 0.0, "laser_built": None, "thin": None}
        pieces, measured, scores = judged[k]
        best = max([s for s in scores if s is not None], default=0.0)
        p_line = chance(models.get(k, model), inputs(line)) if whole[k] else 0.0
        kind, primary, detail, sure = sort(line, laser, best, p_line)
        stretches = runs(pieces, scores) if kind == "fence" else []
        test = flow_test(rule)
        influence = {"fence": test, "building": "block", "none": "negligible"}.get(kind, "uncertain" if test != "negligible" else "negligible")
        rel, dist = relation(MultiLineString([LineString([a, b]) for a, b in segs]) if segs else MultiLineString(), council)
        along = laser.get("along", 0)
        detection = ("high" if along >= 0.7 and line["standing"] >= 30 else "medium" if along >= 0.35 else "low") \
            if kind != "none" else ("high" if laser.get("hidden", 1) <= 0.2 else "medium")
        significance = "high" if kind == "fence" and test != "negligible" else \
                       "medium" if influence in ("uncertain", "block", "divert") else "low"
        check = checks.get(k)
        limits = ["Boundary is the LINZ legal line; a structure can stand up to 1.5 m off it",
                  "Influence is from a flow test that assumes a solid 1.8 m fence along the whole line",
                  "Permeability can't be seen from the air"]
        if kind == "fence":
            limits.append("Fence and wall can't be told apart from the air")
        if kind in ("hedge", "unknown"):
            limits.append("Planting can hide a fence")
        if laser.get("top") and kind != "none":
            limits.append("Height is the laser's median top, plants included")
        if before[k].get("change_since_2016") not in (None, "same"):
            limits.append(f"Changed since 2016 ({before[k]['change_since_2016']})")
        fence_length = sum(float(np.hypot(*(b - a))) for a, b in stretches)
        props = {
            "id": f"{tile}-{k:03d}",
            "structure_type": kind,
            "primary_class": primary,
            "structure_detail": detail,
            "source": "; ".join(SOURCES.values()),
            "source_date": "2024 (laser), 2024-25 (photos)",
            "height_m": laser.get("top") if kind in ("fence", "hedge", "unknown") and laser.get("top") else None,
            "continuity": "continuous" if along >= 0.7 else "discontinuous" if along >= 0.35 else None,
            "permeability": "unknown",
            "flow_path_relationship": rel,
            "council_path_distance_m": None if dist is None else round(float(dist), 1),
            "potential_influence": influence,
            "detection_confidence": detection,
            "classification_confidence": round(float(sure), 2),
            "classification_level": "high" if sure >= SURE else "medium" if sure >= HI else "low",
            "hydraulic_significance": significance,
            "review_status": "manually reviewed" if check else "automated",
            "limitations": "; ".join(limits),
            "parcel_a": fences[k]["between"][0], "parcel_b": fences[k]["between"][1],
            "street_front": fences[k]["street"],
            "boundary_length_m": round(sum(float(np.hypot(*(b - a))) for a, b in segs), 1),
            "fence_length_m": round(fence_length, 1) if kind == "fence" else None,
            "best_piece_chance_built": round(best, 2),
            "line_chance_built": round(p_line, 2),
            "laser_built": None if line["laser_built"] is None else round(line["laser_built"], 2),
            "laser_thin": None if line["thin"] is None else round(line["thin"], 2),
            "photo_edge": round(float(line["edge"]), 1),
            "flow_test": test,
            "change_since_2016": before[k].get("change_since_2016"),
            "evidence_image": f"chips/{k:03d}.jpg" if segs else None,
            "reviewed_as": check["structure"] if check else None,
            "ai_reference_label": labels.get(k, {}).get("structure"),
        }
        shown = latlng(stretches) if stretches else latlng(segs)
        features_out.append({"type": "Feature", "geometry": {"type": "MultiLineString", "coordinates": shown} if shown else None,
                             "properties": props})
        rows.append({**{key: v for key, v in props.items() if key != "source"},
                     "line": [[[a[1], a[0]], [b[1], b[0]]] for a, b in latlng(segs)],
                     "fence": [[[a[1], a[0]], [b[1], b[0]]] for a, b in latlng(stretches)],
                     "pieces": [None if s is None else round(s, 2) for s in scores]})

    # How it does. Truth "built" is a fence, wall or gate; "plants" a hedge or trees.
    def truth_of(label):
        return {"fence or wall": "built", "vegetation": "plants"}.get(EYE.get(label), EYE.get(label))

    def evaluate(pairs):
        right = sum(1 for t, c in pairs if (t, c) in (("built", "linear built barrier"), ("plants", "vegetation"),
                                                       ("none", "none"), ("building", "building")))
        unsure = sum(1 for t, c in pairs if c == "uncertain")
        calls = [t for t, c in pairs if c == "linear built barrier"]
        return dict(n=len(pairs), right=right, unsure=unsure, wrong=len(pairs) - right - unsure,
                    fence_calls=len(calls), fence_wrong=sum(1 for t in calls if t != "built"),
                    built_found=sum(1 for t, c in pairs if t == "built" and c == "linear built barrier"),
                    built_total=sum(1 for t, c in pairs if t == "built"))
    human = {k: (truth_of(c["structure"]), rows[k]["primary_class"]) for k, c in checks.items()}
    # AI labels: built if a fence, wall or gate is anywhere on the line (main or second label).
    dev = [("built" if labels[k]["structure"] in BUILT_LABELS or labels[k]["secondary"] in BUILT_LABELS else "plants",
            rows[k]["primary_class"]) for k in train]
    high = [t for t, r in zip(dev, (rows[k] for k in train)) if r["structure_type"] == "fence" and r["classification_level"] == "high"]
    summary = {
        "tile": tile, "inputs": INPUTS, "piece": PIECE, "hi": HI, "sure": SURE, "mixed": MIXED, "canopy": CANOPY,
        "weights": dict(zip(INPUTS + ["bias"], [round(float(v), 3) for v in model[2]])),
        "trained_on": len(train), "trained_built": int(y.sum()),
        "counts": {t: sum(1 for r in rows if r["structure_type"] == t) for t in TYPES},
        "control": {t: control_calls.count(t) for t in TYPES}, "controlLines": len(control_calls),
        "human": evaluate(list(human.values())),
        "humanPairs": [[k, t, c] for k, (t, c) in human.items()],
        "dev": evaluate(dev),
        "devHigh": dict(calls=len(high), wrong=sum(1 for t, _ in high if t != "built")),
        "sources": SOURCES,
    }
    print(json.dumps({k: summary[k] for k in ("weights", "counts", "control", "human", "dev", "devHigh")}, indent=1))

    collection = {
        "type": "FeatureCollection",
        "name": f"Built structures on overland flow paths, {tile}",
        "disclaimer": "Not authoritative. A hackathon prototype (Auckland, 16 October 2026): candidate structures for a person "
                      "to check, not a record of what is there. Does not replace a site visit or a hydraulic assessment.",
        "made": date.today().isoformat(),
        "features": features_out,
    }
    (OUT / "detected.geojson").write_text(json.dumps(collection, indent=1, default=float))
    (OUT / "detected.js").write_text("globalThis.DETECTED_DATA = " + json.dumps({**summary, "fences": rows}, separators=(",", ":"), default=float) + ";\n")
    print("wrote detected.js and detected.geojson")


if __name__ == "__main__":
    main()
