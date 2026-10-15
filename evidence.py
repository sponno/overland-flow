"""The evidence for one fence line, turned so the line runs left to right.

Three sources, sampled on the same grid: the 2024 7.5 cm aerial photo (LINZ), Council's
2024-25 colour infrared photo, and the 2024 laser points. Rows run across the line (row 0
is ACROSS metres to the line's left), columns along it (column 0 is MARGIN metres before
its start). classify_cv.py reads its measurements from these; make_chips.py draws them.
"""
import json
import urllib.request
from pathlib import Path

import numpy as np
import rasterio
import shapely
from pyproj import Transformer
from rasterio.merge import merge
from scipy.ndimage import map_coordinates
from shapely.geometry import shape
from shapely.ops import transform as reproject

from fetch_aerial import PHOTOS
from fetch_data import AREA, BUILDINGS_URL, SHEET_ROWS, arcgis_features

STEP = 0.075     # metres per sample, the aerial photo's own pixel
ACROSS = 4.0     # metres either side of the line
MARGIN = 2.0     # metres past each end
FOLDER = Path(__file__).parent / "lidar" / "aerial"
EAVES = 0.5      # metres: roofs reach this far past a building outline


def buildings(bounds, grow=EAVES):
    """LINZ building outlines over the map (south, west, north, east), in NZTM, grown by grow
    metres. Fetched once and kept in lidar/."""
    path = Path(__file__).parent / "lidar" / f"buildings-{AREA or 'root'}.json"
    if not path.exists():
        path.write_text(json.dumps(arcgis_features(BUILDINGS_URL, bounds, "building_id")))
    to_nztm = Transformer.from_crs(4326, 2193, always_xy=True).transform
    shapes = [reproject(to_nztm, shape(f["geometry"])).buffer(grow) for f in json.loads(path.read_text())]
    union = shapely.union_all(shapes)
    shapely.prepare(union)
    return union


def tile_names(west, south, east, north):
    """The 1:1,000 photo tiles (480 x 720 m) a box touches."""
    names = set()
    for e in list(np.arange(west, east, 400.0)) + [east]:      # tiles are 480 x 720 m: step under that
        for n in list(np.arange(south, north, 600.0)) + [north]:
            col, row = int((e - 988000) // 24000), int((6234000 - n) // 36000)
            sheet_w, sheet_n = 988000 + col * 24000, 6234000 - row * 36000
            tr, tc = int((sheet_n - n) // 720) + 1, int((e - sheet_w) // 480) + 1
            names.add(f"{SHEET_ROWS[row]}{col:02d}_1000_{tr:02d}{tc:02d}")
    return sorted(names)


def photo(west, south, east, north):
    """The aerial photo over a box: an RGBA array and its transform. Tiles are downloaded once."""
    sources = []
    for name in tile_names(west, south, east, north):
        path = FOLDER / f"{name}.tiff"
        if not path.exists():
            FOLDER.mkdir(parents=True, exist_ok=True)
            print(f"downloading aerial {name}.tiff (75 MB)")
            urllib.request.urlretrieve(f"{PHOTOS}/{name}.tiff", path.with_suffix(".part"))
            path.with_suffix(".part").rename(path)
        sources.append(rasterio.open(path))
    array, transform = merge(sources, bounds=(west, south, east, north), res=STEP, nodata=0)
    for s in sources:
        s.close()
    return array, transform


class Frame:
    """A grid laid along a line: the longest straight run sets the direction. margin is how
    far past each end it runs."""

    def __init__(self, segments, margin=MARGIN):
        p, q = max(segments, key=lambda s: np.hypot(*(s[1] - s[0])))
        self.u = (q - p) / np.hypot(*(q - p))
        self.n = np.array([-self.u[1], self.u[0]])  # to the line's left
        self.origin = p
        ends = np.array([e for s in segments for e in s])
        along = (ends - p) @ self.u
        self.start, self.end = along.min() - margin, along.max() + margin
        self.segments = [((a - p) @ self.u, (a - p) @ self.n, (b - p) @ self.u, (b - p) @ self.n) for a, b in segments]
        self.cols = int(round((self.end - self.start) / STEP))
        self.rows = int(round(2 * ACROSS / STEP))
        a = self.start + (np.arange(self.cols) + 0.5) * STEP
        c = ACROSS - (np.arange(self.rows) + 0.5) * STEP
        A, C = np.meshgrid(a, c)
        self.X = p[0] + A * self.u[0] + C * self.n[0]
        self.Y = p[1] + A * self.u[1] + C * self.n[1]

    def bounds(self, pad=1.0):
        return self.X.min() - pad, self.Y.min() - pad, self.X.max() + pad, self.Y.max() + pad

    def local(self, xy):
        """Along and across, in metres, for world points."""
        rel = xy - self.origin
        return rel @ self.u, rel @ self.n

    def blocked(self, roofs):
        """True where a grid cell is under a building or its eaves."""
        return shapely.contains_xy(roofs, self.X, self.Y)

    def pixel(self, along, across):
        """Column and row in the frame for an along/across position."""
        return (along - self.start) / STEP, (ACROSS - across) / STEP


def sample(array, transform, frame):
    """Sample a (bands, rows, cols) raster at the frame's grid, bilinear."""
    inv = ~transform
    cols, rows = inv * (frame.X, frame.Y)
    return np.stack([map_coordinates(band, [rows - 0.5, cols - 0.5], order=1, mode="constant", cval=0, output=np.float32)
                     for band in array])


_held = {}


def hold(west, south, east, north):
    """Read the photo over a whole area once, so every frame inside it samples from memory."""
    _held["area"] = (west, south, east, north), *photo(west, south, east, north)


def aerial_in(frame):
    """RGBA photo along the line, (4, rows, cols)."""
    west, south, east, north = frame.bounds()
    held = _held.get("area")
    if held and held[0][0] <= west and held[0][1] <= south and held[0][2] >= east and held[0][3] >= north:
        return sample(held[1], held[2], frame)
    array, transform = photo(west, south, east, north)
    return sample(array, transform, frame)


def infrared_in(frame, ir, west, north, pixel):
    """Near infrared, green and blue along the line from the infrared mosaic (rows, cols, 3)."""
    cols = (frame.X - west) / pixel - 0.5
    rows = (north - frame.Y) / pixel - 0.5
    return np.stack([map_coordinates(ir[..., k], [rows, cols], order=1, mode="nearest", output=np.float32) for k in range(3)])
