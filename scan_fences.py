# /// script
# requires-python = ">=3.11"
# dependencies = ["laspy[lazrs]", "numpy", "pyproj", "rasterio", "shapely"]
# ///
"""Is anything standing there? Look along every tested boundary in the LiDAR point cloud.

  uv run scan_fences.py      (AREA=mtalbert for another area, as in fetch_data.py)

Needs data.js and fences.js (run rank_fences.js first). Downloads the 2024 point cloud
tiles for the area into lidar/ and keeps them there for next time, then writes lidar.js.

For each boundary it takes the points within a couple of metres, works out their height
above the ground, and asks what stands 0.5 to 2.6 m high along it, metre by metre. A fence
is a thin sheet, so the test slides and tilts a 30 cm wide strip to wherever the most
metres have something standing in it, then looks at how level the top is.

It cannot tell a paling fence from a clipped hedge, or solid from see-through. It can say
"nothing is standing here", which rules a boundary out.

The same test is run on each line shifted 4 m to either side, where no boundary is, to
show how often it fires by chance.
"""
import hashlib
import json
import math
import urllib.request
from pathlib import Path

import laspy
import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.features import rasterize
from rasterio.transform import from_origin
from rasterio.windows import from_bounds
from shapely import line_merge
from shapely.geometry import LineString, MultiLineString, shape
from shapely.ops import transform as reproject

from fetch_data import DEM_BASE, OUT, SHEET_ROWS, arcgis_features
from fetch_parcels import PARCELS_URL

HERE = Path(__file__).parent
CACHE = HERE / "lidar"
POINTS_URL = "https://opentopography.s3.sdsc.edu/pc-bulk/NZ24_Auckland"
# The two laser surveys we read. The 2016 one is what Council's flow paths were drawn from;
# its ground model is used for its own heights, so earthworks since don't read as fences.
SURVEYS = {
    "2024": dict(points=POINTS_URL, tile="CL2_{sheet}_2024_1000_{rc}", dem=DEM_BASE,
                 source="LINZ Auckland Part 1 LiDAR point cloud (2024), via OpenTopography"),
    "2016": dict(points="https://opentopography.s3.sdsc.edu/pc-bulk/NZ16_NAuckland", tile="CL3_{sheet}_2016_1000_{rc}",
                 dem="https://nz-elevation.s3.ap-southeast-2.amazonaws.com/auckland/auckland-north_2016-2018/dem_1m/2193",
                 source="LINZ Auckland North LiDAR point cloud (2016-18), via OpenTopography"),
}

REACH = 2.5          # metres either side of a line that we keep points for
SEARCH = 1.5         # a fence can sit this far off the surveyed boundary
TILT = 2.0           # and run this many degrees off it
THIN = 0.15          # metres either side of the fitted line that count as on it
LOW, HIGH = 0.5, 2.6 # metres above ground: tall enough to stop water, short enough not to be a tree
MIN_SEGMENT = 3.0    # metres: shorter straight runs are ignored
CONTROL_SHIFT = 4.0  # metres: where the comparison lines sit
GROUND, BUILDING, NOISE = 2, 6, (7, 18)  # point classes in the survey
# How much of a boundary must have something standing on it to call it "all along" or "part".
ALL_ALONG, PART = 0.7, 0.35
LEVEL = 0.4          # metres: a top that varies less than this along the boundary looks built
# level and ragged both mean something stands all along; hidden means the laser couldn't see.
VERDICTS = ["level", "ragged", "part", "open", "hidden", "building", "none"]


def load_js(name):
    text = (OUT / name).read_text()
    return json.loads(text[text.index("{"):text.rindex("}") + 1])


def tile_1000(e, n, survey="2024"):
    """1:1,000 point cloud tile (50 x 50 per Topo50 sheet) for an NZTM point."""
    col, row = int((e - 988000) // 24000), int((6234000 - n) // 36000)
    sheet_w, sheet_n = 988000 + col * 24000, 6234000 - row * 36000
    tr, tc = int((sheet_n - n) // 720) + 1, int((e - sheet_w) // 480) + 1
    name = SURVEYS[survey]["tile"].format(sheet=f"{SHEET_ROWS[row]}{col:02d}", rc=f"{tr:02d}{tc:02d}")
    return name, sheet_w + (tc - 1) * 480, sheet_n - (tr - 1) * 720  # name, west edge, north edge


def boundaries(fences, bounds):
    """The line each fence would stand on: the boundary its two parcels share, in NZTM."""
    to_nztm = Transformer.from_crs(4326, 2193, always_xy=True).transform
    parcels = {}
    for f in arcgis_features(PARCELS_URL, bounds, "id"):
        parcels[f["properties"]["id"]] = reproject(to_nztm, shape(f["geometry"])).buffer(0)
    lines = []
    for f in fences:
        a, b = (parcels.get(i) for i in f["between"])
        if a is None or b is None:
            lines.append([])
            continue
        shared = a.boundary.intersection(b.buffer(0.3))
        pieces = [g for g in getattr(shared, "geoms", [shared]) if g.geom_type == "LineString"]
        merged = line_merge(MultiLineString(pieces)) if pieces else MultiLineString()
        segments = []
        for part in getattr(merged, "geoms", [merged]):
            xy = np.asarray(part.simplify(0.1).coords)
            segments += [(p, q) for p, q in zip(xy[:-1], xy[1:]) if np.hypot(*(q - p)) >= MIN_SEGMENT]
        lines.append(segments)
    return lines


def shifted(segments, by):
    out = []
    for p, q in segments:
        u = (q - p) / np.hypot(*(q - p))
        normal = np.array([-u[1], u[0]]) * by
        out.append((p + normal, q + normal))
    return out


def read_points(all_segments, survey="2024"):
    """Every point within REACH of any segment, from whichever tiles they fall in."""
    tiles = {}
    for p, q in all_segments:
        for x in (min(p[0], q[0]) - REACH, max(p[0], q[0]) + REACH):
            for y in (min(p[1], q[1]) - REACH, max(p[1], q[1]) + REACH):
                name, west, north = tile_1000(x, y, survey)
                tiles[name] = (west, north)
    CACHE.mkdir(exist_ok=True)
    # Reading the tiles is the slow part, so the points we keep are saved for next time.
    stamp = hashlib.sha1(np.round(np.array(all_segments), 2).tobytes() + f"{REACH} {survey}".encode()).hexdigest()[:12]
    saved = CACHE / f"corridors-{stamp}.npy"
    if saved.exists():
        return np.load(saved), sorted(tiles)
    corridors = [LineString([p, q]).buffer(REACH + 0.5) for p, q in all_segments]
    kept = []
    for k, (name, (west, north)) in enumerate(sorted(tiles.items())):
        path = CACHE / f"{name}.laz"
        if not path.exists():
            print(f"downloading {name} ({k + 1} of {len(tiles)})")
            part = path.with_suffix(".part")  # so a stopped download is never mistaken for a tile
            urllib.request.urlretrieve(f"{SURVEYS[survey]['points']}/{name}.laz", part)
            part.rename(path)
        las = laspy.read(path)
        x, y = np.asarray(las.x), np.asarray(las.y)
        # A half-metre mask of the corridors picks the points out quickly.
        mask = rasterize([(c, 1) for c in corridors], out_shape=(1440, 960),
                         transform=from_origin(west, north, 0.5, 0.5), fill=0, dtype="uint8")
        col, row = ((x - west) * 2).astype(int), ((north - y) * 2).astype(int)
        inside = (col >= 0) & (col < 960) & (row >= 0) & (row < 1440)
        inside[inside] = mask[row[inside], col[inside]] == 1
        cls = np.asarray(las.classification)[inside]
        keep = ~np.isin(cls, NOISE)
        kept.append(np.column_stack([x[inside], y[inside], np.asarray(las.z)[inside], cls])[keep])
        print(f"{name}: {len(x):,} points, kept {int(keep.sum()):,}")
    points = np.concatenate(kept)
    np.save(saved, points)
    return points, sorted(tiles)


def ground_under(points, dem_tiles, dem_url=DEM_BASE):
    """Bare-earth height under each point, from the same 1 m DEM the flow model uses."""
    x, y = points[:, 0], points[:, 1]
    west, east, south, north = x.min() - 2, x.max() + 2, y.min() - 2, y.max() + 2
    ground = np.full(len(points), np.nan)
    for tile in dem_tiles:
        with rasterio.open(f"/vsicurl/{dem_url}/{tile}.tiff") as src:
            window = from_bounds(west, south, east, north, src.transform).round_offsets().round_lengths()
            dem = src.read(1, window=window, boundless=True, fill_value=np.nan).astype("float64")
            if src.nodata is not None:
                dem[dem == src.nodata] = np.nan
            t = src.window_transform(window)
        # Bilinear between the four cell centres around each point.
        fx, fy = (x - t.c) / t.a - 0.5, (y - t.f) / t.e - 0.5
        c0 = np.clip(np.floor(fx).astype(int), 0, dem.shape[1] - 2)
        r0 = np.clip(np.floor(fy).astype(int), 0, dem.shape[0] - 2)
        wx, wy = np.clip(fx - c0, 0, 1), np.clip(fy - r0, 0, 1)
        z = (dem[r0, c0] * (1 - wx) * (1 - wy) + dem[r0, c0 + 1] * wx * (1 - wy)
             + dem[r0 + 1, c0] * (1 - wx) * wy + dem[r0 + 1, c0 + 1] * wx * wy)
        ground = np.where(np.isnan(ground), z, ground)
    return ground


class Index:
    """Points bucketed on a coarse grid, so each segment only looks at its own neighbours."""
    CELL = 16.0

    def __init__(self, points):
        self.x0, self.y0 = points[:, 0].min(), points[:, 1].min()
        cx = ((points[:, 0] - self.x0) // self.CELL).astype(np.int64)
        cy = ((points[:, 1] - self.y0) // self.CELL).astype(np.int64)
        self.nx = int(cx.max()) + 1
        key = cy * self.nx + cx
        self.order = np.argsort(key, kind="stable")
        self.keys = key[self.order]

    def near(self, p, q):
        x0, x1 = sorted((p[0], q[0]))
        y0, y1 = sorted((p[1], q[1]))
        cols = range(max(0, int((x0 - REACH - self.x0) // self.CELL)), min(self.nx - 1, int((x1 + REACH - self.x0) // self.CELL)) + 1)
        rows = range(max(0, int((y0 - REACH - self.y0) // self.CELL)), int((y1 + REACH - self.y0) // self.CELL) + 1)
        found = [self.order[slice(*np.searchsorted(self.keys, [r * self.nx + c, r * self.nx + c + 1]))] for r in rows for c in cols]
        return np.concatenate(found) if found else np.empty(0, dtype=np.int64)


def scan(segments, points, height, index, profile=False, keep=False, trace=False):
    """What stands along one line, metre by metre. Each straight run is fitted on its own.
    keep=True also returns which points the standing thing is made of, as "on".
    trace=True also returns, per straight run, the fitted line's ends and which of its metres
    stand, were seen and are building, as "trace"."""
    total = dict(length=0, open=0, seen=0, standing=0)
    tops, shown, picked, offsets, traced, run = [], [], [], [], [], 0.0
    # Try the surveyed line first, then further off it, so a tie goes to the closest fit.
    fits = sorted(((t, o) for t in np.radians(np.arange(-TILT, TILT + 0.01, 0.5)) for o in np.arange(-SEARCH, SEARCH + 0.001, 0.05)),
                  key=lambda fit: (abs(fit[0]), abs(fit[1])))
    for p, q in segments:
        length = float(np.hypot(*(q - p)))
        u = (q - p) / length
        near = index.near(p, q)
        rel = points[near, :2] - p
        along, across = rel @ u, rel @ np.array([-u[1], u[0]])
        ok = (along >= 0) & (along < length) & (np.abs(across) <= REACH)
        along, across, h, cls = along[ok], across[ok], height[near][ok], points[near, 3][ok]
        n = math.ceil(length)
        metre = along.astype(int)

        # A building on the boundary is already a wall in the model: leave those metres out.
        built = np.bincount(metre[(cls == BUILDING) & (np.abs(across) <= 0.75)], minlength=n) >= 3
        up = (h >= LOW) & (h <= HIGH) & (cls != GROUND) & (cls != BUILDING)
        best, tilt, shift = -1, 0.0, 0.0
        for t, o in fits:
            on = np.abs(across[up] - np.tan(t) * (along[up] - length / 2) - o) <= THIN
            metres = int(((np.bincount(metre[up][on], minlength=n) > 0) & ~built).sum())
            if metres > best:
                best, tilt, shift = metres, t, o
        from_line = across - np.tan(tilt) * (along - length / 2) - shift
        on = up & (np.abs(from_line) <= THIN)
        standing = (np.bincount(metre[on], minlength=n) > 0) & ~built
        # Under a roof or thick canopy the laser never gets down there: say so rather than "open".
        reached = (h < HIGH) & (cls != BUILDING) & (np.abs(from_line) <= 0.5)
        seen = ((np.bincount(metre[reached], minlength=n) > 0) | standing) & ~built

        total["length"] += n
        total["open"] += int((~built).sum())
        total["seen"] += int(seen.sum())
        total["standing"] += int(standing.sum())
        if standing.any():
            top = np.zeros(n)
            np.maximum.at(top, metre[on], h[on])
            tops += top[standing].tolist()
        if keep:
            picked.append(near[ok][on & ~built[metre]])
            offsets.append((float(shift), int(standing.sum())))
        if trace:
            normal = np.array([-u[1], u[0]])
            ends = [p + u * a + normal * (np.tan(tilt) * (a - length / 2) + shift) for a in (0.0, length)]
            traced.append({"ends": ends, "length": length, "standing": standing, "seen": seen, "built": built})
        if profile:
            side = (np.abs(from_line) <= 0.3) & (h > -0.5) & (h < 4.5)
            shown += [(round(run + float(a), 1), round(float(z), 2), int(c)) for a, z, c in zip(along[side], h[side], cls[side])]
        run += length

    if not total["open"]:
        return {"length": total["length"], "verdict": "building" if total["length"] else "none", **({"trace": traced} if trace else {})}
    out = {
        "length": total["length"], "open": total["open"],
        "along": round(total["standing"] / max(1, total["seen"]), 2),
        "hidden": round(1 - total["seen"] / total["open"], 2),
        "top": round(float(np.median(tops)), 2) if tops else 0,
        "ragged": round(float(np.percentile(tops, 75) - np.percentile(tops, 25)), 2) if tops else 0,
    }
    out["verdict"] = ("hidden" if out["hidden"] > 0.5 else "part" if PART <= out["along"] < ALL_ALONG
                      else "open" if out["along"] < PART else "level" if out["ragged"] <= LEVEL else "ragged")
    if keep:
        out["on"] = np.concatenate(picked) if picked else np.empty(0, dtype=np.int64)
        # How far the fitted line sits off the surveyed one, averaged over the metres that stand.
        weight = sum(w for _, w in offsets)
        out["offset"] = round(sum(abs(o) * w for o, w in offsets) / weight, 2) if weight else None
    if trace:
        out["trace"] = traced
    if profile:
        if len(shown) > 1500:  # plenty to draw
            shown = [shown[i] for i in np.random.default_rng(0).choice(len(shown), 1500, replace=False)]
        out["points"] = sorted(shown)
    return out


def main():
    grid, ranked = load_js("data.js"), load_js("fences.js")
    fences = ranked["fences"]
    lines = boundaries(fences, grid["bounds"])
    controls = [shifted(segs, by) for segs in lines for by in (CONTROL_SHIFT, -CONTROL_SHIFT)]
    points, tiles = read_points([s for segs in lines + controls for s in segs])
    height = points[:, 2] - ground_under(points, grid["source"]["tiles"])
    index = Index(points)
    print(f"{len(points):,} points along {sum(1 for s in lines if s)} boundaries")

    # A side view is kept for the fences the pages open one at a time: those that reroute water
    # onto a new property (v3), and those that would need consent (test_rules.js, if it has run).
    rules = load_js("rules.js")["fences"] if (OUT / "rules.js").exists() else [{}] * len(fences)
    results = [scan(segs, points, height, index, profile=f["grade"] == 2 or r.get("outcome") == "consent")
               if segs else {"verdict": "none"} for f, r, segs in zip(fences, rules, lines)]
    control = [scan(segs, points, height, index) for segs in controls if segs]

    def tally(rows):
        return {v: sum(1 for r in rows if r["verdict"] == v) for v in VERDICTS}
    by_grade = [tally([r for f, r in zip(fences, results) if f["grade"] == g]) for g in (0, 1, 2)]
    print("boundaries:", tally(results))
    print("the ones that matter:", by_grade[2])
    print("shifted lines:", tally(control))

    out = {
        "source": "LINZ Auckland Part 1 LiDAR point cloud (2024), via OpenTopography", "tiles": tiles,
        "low": LOW, "high": HIGH, "search": SEARCH, "shift": CONTROL_SHIFT, "allAlong": ALL_ALONG, "part": PART,
        "level": LEVEL, "thin": THIN,
        "counts": tally(results), "byGrade": by_grade, "control": tally(control), "controlLines": len(control),
        # One entry per fence, in the same order as fences.js.
        "fences": results,
    }
    path = OUT / "lidar.js"
    path.write_text("globalThis.LIDAR_DATA = " + json.dumps(out, separators=(",", ":")) + ";\n")
    print("wrote", path, f"{path.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
