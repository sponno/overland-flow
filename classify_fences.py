# /// script
# requires-python = ">=3.11"
# dependencies = ["laspy[lazrs]", "numpy", "pillow", "pyproj", "rasterio", "shapely"]
# ///
"""Fence or wall, hedge, or nothing? Sort every tested boundary and write it out in the
fields Council's brief asks for.

  AREA=mtalbert uv run classify_fences.py

Needs data.js, fences.js and rules.js (node rank_fences.js, then node test_rules.js), and
reuses the point cloud tiles scan_fences.py keeps in lidar/. Writes structures.js for the
page and structures.geojson for a GIS.

It reads two things at the points the laser found standing on each boundary:

- The survey's own point classes. The 2024 point cloud came classified: ground, low,
  medium and high vegetation, building, and "unclassified" for anything else. A fence or
  wall lands in unclassified, a hedge in vegetation, so the unclassified share says how
  much of what stands there is built. A car, shed or trampoline is unclassified too.
- Council's 2024-25 colour infrared photos. Leaves reflect near infrared strongly, so each
  point gets a green reading from the photo. That only sets confidence, not the type: from
  above, a fence with a climber on it looks like a hedge.

Then it runs the same laser scan on the 2016-18 survey, the one Council's flow paths were
drawn from, and says what changed on each line: more or less of it standing, or the top
half a metre or more taller or lower.

The cut-offs are mine, chosen with the first 10 hand checks in view, so those 10 are not
an independent test. The sort and the 2016 comparison also run on lines 4 m off every
boundary, to show how often each fires where there is no boundary.

Tried and dropped, because none of them told fence from hedge on the hand checks: laser
intensity, how many returns each pulse gave, how deep the standing thing is across the
line, and the 2016 survey's own classes (it files most fences as vegetation).
"""
import csv
import json
import urllib.request
from datetime import date

import numpy as np
from PIL import Image
from pyproj import Transformer

from fetch_data import OUT
from scan_fences import (ALL_ALONG, CACHE, CONTROL_SHIFT, PART, SURVEYS, Index, boundaries, ground_under, load_js,
                         read_points, scan, shifted)

INFRARED = "https://tiles.arcgis.com/tiles/n4yPwebTjJCmXB6W/arcgis/rest/services/AucklandInfraredImagery2024/MapServer/tile/20"
ORIGIN, PIXEL = (-4020900.0, 19998100.0), 0.14929107082380833  # the service's tile grid at level 20: 15 cm
SPAN = PIXEL * 256
UNCLASSIFIED, BUILDING, TREE = 1, 6, 5
BUILT = 0.2   # at least this share of unclassified points reads as built; some but fewer is uncertain
LEAFY = 0.5   # share of points that read green: above this, plants cover the top
FEW = 10      # fewer standing points than this is too little to call
MORE = 0.35   # change since 2016 in the share of a line with something standing on it
RAISED = 0.5  # metres: change since 2016 in the top of something standing all along both times
CHANGES = ["same", "taller", "lower", "more standing", "less standing", "can't compare"]
TYPES = ["fence or wall", "vegetation", "uncertain", "none", "building"]
# What a hand check's answer counts as.
EYE = {"fence": "fence or wall", "wall": "fence or wall", "retaining wall": "fence or wall", "gate": "fence or wall",
       "hedge": "vegetation", "building": "building", "none": "none"}


def infrared(points):
    """The colour infrared photo over the points, as one array, with its top-left corner."""
    x, y = points[:, 0], points[:, 1]
    c0, c1 = int((x.min() - 1 - ORIGIN[0]) // SPAN), int((x.max() + 1 - ORIGIN[0]) // SPAN)
    r0, r1 = int((ORIGIN[1] - y.max() - 1) // SPAN), int((ORIGIN[1] - y.min() + 1) // SPAN)
    folder = CACHE / "infrared"
    folder.mkdir(parents=True, exist_ok=True)
    photo = np.zeros(((r1 - r0 + 1) * 256, (c1 - c0 + 1) * 256, 3), np.uint8)
    for r in range(r0, r1 + 1):
        for c in range(c0, c1 + 1):
            path = folder / f"{r}_{c}.jpg"
            if not path.exists():
                urllib.request.urlretrieve(f"{INFRARED}/{r}/{c}", path)
            photo[(r - r0) * 256:(r - r0 + 1) * 256, (c - c0) * 256:(c - c0 + 1) * 256] = np.asarray(Image.open(path).convert("RGB"))
    return photo, ORIGIN[0] + c0 * SPAN, ORIGIN[1] - r0 * SPAN


def green_at(points, photo, west, north):
    """Green index under each point: near infrared against green, the median of a 45 cm square
    so a fence top a pixel off still counts. The bands are near infrared, green, blue."""
    nir, green = photo[..., 0].astype(float), photo[..., 1].astype(float)
    index = (nir - green) / np.maximum(nir + green, 1)
    col = ((points[:, 0] - west) / PIXEL).astype(int)
    row = ((north - points[:, 1]) / PIXEL).astype(int)
    h, w = index.shape
    around = [index[np.clip(row + dr, 0, h - 1), np.clip(col + dc, 0, w - 1)] for dr in (-1, 0, 1) for dc in (-1, 0, 1)]
    return np.median(around, axis=0)


def sort(r, cls, leafy):
    """Type and confidence for one scanned boundary."""
    v = r["verdict"]
    if v == "building":
        return "building", "high", None, None
    if v in ("none", "hidden"):
        return "uncertain", "low", None, None
    if v == "open":
        return "none", "high" if r["hidden"] <= 0.2 else "medium", None, None
    on = r["on"]
    if len(on) < FEW:
        return "uncertain", "low", None, None
    built = float(np.mean(cls[on] == UNCLASSIFIED))
    green = float(np.mean(leafy[on]))
    if built >= BUILT:
        return "fence or wall", "high" if green <= LEAFY else "medium", built, green
    if built > 0:
        return "uncertain", "low", built, green
    return "vegetation", "high" if green > LEAFY else "medium", built, green


def since_2016(old, new):
    """What changed on a line between the 2016 and 2024 scans."""
    if "along" not in old or "along" not in new or max(old["hidden"], new["hidden"]) > 0.5:
        return "can't compare"
    if new["along"] - old["along"] >= MORE:
        return "more standing"
    if old["along"] - new["along"] >= MORE:
        return "less standing"
    if min(old["along"], new["along"]) >= ALL_ALONG and abs(new["top"] - old["top"]) >= RAISED:
        return "taller" if new["top"] > old["top"] else "lower"
    return "same"


def flow_test(rule):
    """What a solid 1.8 m fence here did in the flow test (test_rules.js)."""
    return "divert" if rule["moved"] else "block" if rule["pond"] != "none" else "negligible"


def hand_checks():
    """Every hand check sent in, latest answer per fence line."""
    seen = {}
    for path in sorted((OUT / "checks").glob("*.csv")):
        with path.open() as f:
            for row in csv.DictReader(f):
                if row["structure"] in EYE and (row["fence"] not in seen or row["at"] > seen[row["fence"]]["at"]):
                    seen[row["fence"]] = row
    return {int(k): v for k, v in seen.items()}


def main():
    grid, ranked, rules = load_js("data.js"), load_js("fences.js"), load_js("rules.js")["fences"]
    fences = ranked["fences"]
    lines = boundaries(fences, grid["bounds"])
    controls = [shifted(segs, by) for segs in lines for by in (CONTROL_SHIFT, -CONTROL_SHIFT)]
    every = [s for segs in lines + controls for s in segs]
    points, _ = read_points(every)
    height = points[:, 2] - ground_under(points, grid["source"]["tiles"])
    index = Index(points)
    cls = points[:, 3].astype(int)
    # The 2016 survey, with heights from its own ground model.
    old_points, _ = read_points(every, "2016")
    old_height = old_points[:, 2] - ground_under(old_points, grid["source"]["tiles"], SURVEYS["2016"]["dem"])
    old_index = Index(old_points)
    def then(segs):
        return scan(segs, old_points, old_height, old_index) if segs else {"verdict": "none"}

    photo, west, north = infrared(points)
    reading = green_at(points, photo, west, north)
    # Where "green" starts, from the survey's own points: halfway between roofs and trees.
    trees = np.median(reading[(cls == TREE) & (height > 2)])
    roofs = np.median(reading[cls == BUILDING])
    cut = (trees + roofs) / 2
    leafy = reading > cut
    print(f"green index: roofs {roofs:.2f}, trees {trees:.2f}, cut {cut:.2f}")

    tile = grid.get("focus", {}).get("name", "area")
    checks = hand_checks()
    to_wgs = Transformer.from_crs(2193, 4326, always_xy=True)
    features, rows = [], []
    for k, (f, rule, segs) in enumerate(zip(fences, rules, lines)):
        r = scan(segs, points, height, index, keep=True) if segs else {"verdict": "none"}
        kind, confidence, built, green = sort(r, cls, leafy)
        old = then(segs)
        change = since_2016(old, r)
        test = flow_test(rule)
        influence = ("negligible" if kind == "none" else "uncertain" if kind == "vegetation" and test != "negligible" else test)
        standing = kind in ("fence or wall", "vegetation", "uncertain") and r.get("top")
        limits = ["Line is the LINZ legal boundary, not a surveyed fence position",
                  "Influence assumes a solid 1.8 m fence"]
        if standing:
            limits.append("Height is the laser's median top and includes any plants on or over it")
        if kind == "fence or wall" and green is not None and green > LEAFY:
            limits.append("Plants cover much of the top")
        if kind == "vegetation":
            limits.append("A hedge can hide a fence")
        if r["verdict"] == "hidden":
            limits.append("Laser could not see under canopy or roof")
        if not segs:
            limits.append("No shared line in LINZ parcels")
        check = checks.get(k)
        props = {
            "id": f"{tile}-{k:03d}",
            "structure_type": kind,
            "source": "LINZ NZ Primary Parcels; LINZ Auckland Part 1 LiDAR point cloud and 1 m DEM 2024; "
                      "Auckland Council colour infrared imagery 2024-25; flow model from this repo",
            "source_date": "LiDAR 2024, infrared 2023-25",
            "height_m": r.get("top") if standing else None,
            "continuity": {"level": "continuous", "ragged": "continuous", "part": "discontinuous"}.get(r["verdict"]) if kind != "none" else None,
            "permeability": "unknown",
            "flow_path": f"Modelled flow path crosses it; {f['area']:,} m² drains to it" + ("; street front" if f["street"] else ""),
            "influence": influence,
            "confidence": confidence,
            "review_status": f"checked by eye: {check['structure']} ({check['sure']})" if check else "not reviewed",
            "limitations": "; ".join(limits),
            "parcel_a": f["between"][0], "parcel_b": f["between"][1], "street_front": f["street"],
            "length_m": r.get("length"), "laser": r["verdict"],
            "built_share": None if built is None else round(built, 2),
            "leafy_share": None if green is None else round(green, 2),
            "offset_m": r.get("offset") if standing else None,
            "fence_score": {"fence or wall": 1, "uncertain": 2, "vegetation": 3}.get(kind),
            "flow_test": test,
            "change_since_2016": change,
            "height_2016_m": old.get("top") if old.get("along", 0) >= PART else None,
            "compared_with": SURVEYS["2016"]["source"],
        }
        drawn = []
        for p, q in segs:
            (x1, x2), (y1, y2) = to_wgs.transform([p[0], q[0]], [p[1], q[1]])
            drawn.append([[round(x1, 7), round(y1, 7)], [round(x2, 7), round(y2, 7)]])
        features.append({"type": "Feature", "geometry": {"type": "MultiLineString", "coordinates": drawn} if drawn else None,
                         "properties": props})
        eye = EYE[check["structure"]] if check else None
        rows.append({**{k: v for k, v in props.items() if k != "source"}, "eye": eye})

    control, control_change = [], []
    for segs in controls:
        if segs:
            r = scan(segs, points, height, index, keep=True)
            control.append(sort(r, cls, leafy)[0])
            control_change.append(since_2016(then(segs), r))

    def tally(kinds, names=TYPES):
        return {t: sum(1 for x in kinds if x == t) for t in names}
    checked = [r for r in rows if r["eye"]]
    score = {"checked": len(checked),
             "agree": sum(1 for r in checked if r["structure_type"] == r["eye"]),
             "uncertain": sum(1 for r in checked if r["structure_type"] == "uncertain"),
             "disagree": sum(1 for r in checked if r["structure_type"] not in (r["eye"], "uncertain"))}
    counts, chance = tally([r["structure_type"] for r in rows]), tally(control)
    print("boundaries:", counts)
    print(f"lines 4 m off ({len(control)}):", chance)
    print("against hand checks:", score)
    change = tally([r["change_since_2016"] for r in rows], CHANGES)
    change_control = tally(control_change, CHANGES)
    print("since 2016:", change)
    print("since 2016, lines 4 m off:", change_control)

    collection = {
        "type": "FeatureCollection",
        "name": f"Built structures on overland flow paths, {tile}",
        "disclaimer": "Not authoritative. A hackathon prototype (Auckland, 16 October 2026): candidate structures "
                      "for a person to check, not a record of what is there. Does not replace a site visit.",
        "made": date.today().isoformat(),
        "features": features,
    }
    (OUT / "structures.geojson").write_text(json.dumps(collection, indent=1))
    out = {"tile": tile, "source": features[0]["properties"]["source"], "cut": round(float(cut), 3),
           "built": BUILT, "leafy": LEAFY, "few": FEW, "counts": counts,
           "control": chance, "controlLines": len(control), "score": score,
           "more": MORE, "raised": RAISED, "change": change, "changeControl": change_control, "fences": rows}
    path = OUT / "structures.js"
    path.write_text("globalThis.STRUCTURE_DATA = " + json.dumps(out, separators=(",", ":")) + ";\n")
    print("wrote", path, "and structures.geojson")


if __name__ == "__main__":
    main()
