# /// script
# requires-python = ">=3.11"
# dependencies = ["rasterio", "numpy", "pyproj"]
# ///
"""Fetch real data around one point and write data.js for the viewer.

  uv run fetch_data.py                  the Vermont St area, into this folder
  AREA=mtalbert uv run fetch_data.py    another area from AREAS, into its own folder
  uv run fetch_data.py lat lon [west east north south label]

Pulls:
  - Auckland 2024 1 m DEM (bare earth) from the LINZ nz-elevation open bucket,
    reading only the window we need from the cloud-optimised GeoTIFFs.
  - Auckland Council's mapped overland flow paths for the same box.
  - LINZ building outlines for the same box, burnt onto the grid.

The DEM is resampled onto a Web Mercator grid at 1 m ground resolution so it
lines up exactly with web map tiles.
"""
import base64
import json
import math
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.features import rasterize
from rasterio.transform import from_origin
from rasterio.warp import Resampling, reproject

# Each area is a point, how far the map runs from it in metres (west, east, north, south),
# and the folder its data files go in. The scripts that follow pick it up from AREA too.
AREAS = {
    # Top of Vermont Street, Ponsonby: the highest point on the street centreline (OSM)
    # that drains down the street. The last couple of metres at Ponsonby Rd sit on the
    # ridge and shed east instead.
    "": dict(lat=-36.85299552, lon=174.74517279, extent=(1742, 258, 590, 710), label="Top of Vermont St"),
    # Mt Albert / Sandringham: the 2024 point cloud's 1:1,000 tile BA31_1000_4245, 480 x 720 m,
    # which is the pilot area the hackathon suggests. It sits on the slopes of Owairaka, so
    # little water comes in: everything that drains into the tile is within 60 m of it. The
    # map runs 300 m past the tile on every side so the streets it drains onto are in the
    # model too. Only fence lines a flow path crosses inside the tile are tested.
    "mtalbert": dict(lat=-36.88844381, lon=174.72102548, extent=(540, 540, 660, 660), label="Mt Albert",
                     focus=("BA31_1000_4245", 1753120, 5916480, 1753600, 5915760)),  # NZTM west, north, east, south
}
AREA = os.environ.get("AREA", "")
OUT = Path(__file__).parent / AREA  # where this area's data.js, parcels.js and the rest live

DEM_BASE = "https://nz-elevation.s3.ap-southeast-2.amazonaws.com/auckland/auckland-part-1_2024/dem_1m/2193"
OLFP_URL = "https://services1.arcgis.com/n4yPwebTjJCmXB6W/arcgis/rest/services/Overland_Flow_Paths/FeatureServer/0/query"
# LINZ publishes its national layers on ArcGIS Online with no API key. Primary parcels
# are on the same host: .../LINZ_NZ_Primary_Parcels/FeatureServer
BUILDINGS_URL = "https://services.arcgis.com/xdsHIIxuCWByZiCB/arcgis/rest/services/LINZ_NZ_Building_Outlines/FeatureServer/0/query"

NODATA_U16 = 65535
SHEET_ROWS = ["AS", "AT", "AU", "AV", "AW", "AX", "AY", "AZ", "BA", "BB", "BC", "BD", "BE"]


def tile_name(e, n):
    """NZ Topo50 sheet + 1:10,000 sub-tile (5x5 per sheet) for an NZTM point."""
    col = int((e - 988000) // 24000)
    row = int((6234000 - n) // 36000)
    sheet_w, sheet_n = 988000 + col * 24000, 6234000 - row * 36000
    tc = int((e - sheet_w) // 4800) + 1
    tr = int((sheet_n - n) // 7200) + 1
    return f"{SHEET_ROWS[row]}{col:02d}_10000_{tr:02d}{tc:02d}"


def fetch_dem(lat, lon, extent):
    to_merc = Transformer.from_crs(4326, 3857, always_xy=True)
    to_nztm = Transformer.from_crs(4326, 2193, always_xy=True)
    to_wgs = Transformer.from_crs(3857, 4326, always_xy=True)

    mx, my = to_merc.transform(lon, lat)
    k = 1 / math.cos(math.radians(lat))  # mercator units per ground metre
    ext_w, ext_e, ext_n, ext_s = extent
    width, height = ext_w + ext_e, ext_n + ext_s
    west, north = mx - ext_w * k, my + ext_n * k
    transform = from_origin(west, north, k, k)

    w_lon, s_lat = to_wgs.transform(west, north - height * k)
    e_lon, n_lat = to_wgs.transform(west + width * k, north)

    tiles = sorted({tile_name(*to_nztm.transform(x, y)) for x in (w_lon, e_lon) for y in (s_lat, n_lat)})
    dem = np.full((height, width), np.nan, dtype="float32")
    for t in tiles:
        url = f"/vsicurl/{DEM_BASE}/{t}.tiff"
        print("reading", t)
        with rasterio.open(url) as src:
            part = np.full((height, width), np.nan, dtype="float32")
            reproject(
                rasterio.band(src, 1), part,
                dst_transform=transform, dst_crs="EPSG:3857", dst_nodata=np.nan,
                resampling=Resampling.bilinear,
            )
        dem = np.where(np.isnan(dem), part, dem)
    return dem, (s_lat, w_lon, n_lat, e_lon), tiles, (ext_w, ext_n), transform


def arcgis_features(url, bounds, out_fields):
    """Every feature in the box from an ArcGIS feature layer, as GeoJSON in WGS84."""
    s, w, n, e = bounds
    feats = []
    while True:
        q = urllib.parse.urlencode({
            "where": "1=1", "geometry": f"{w},{s},{e},{n}", "geometryType": "esriGeometryEnvelope",
            "inSR": 4326, "outSR": 4326, "spatialRel": "esriSpatialRelIntersects",
            "outFields": out_fields, "geometryPrecision": 7,
            "resultOffset": len(feats), "f": "geojson",
        })
        with urllib.request.urlopen(f"{url}?{q}") as r:
            page = json.load(r)
        feats += page.get("features", [])
        more = page.get("properties", {}).get("exceededTransferLimit") or page.get("exceededTransferLimit")
        if not more or not page.get("features"):
            return [f for f in feats if f.get("geometry")]


def fetch_olfp(bounds):
    lines = []
    for f in arcgis_features(OLFP_URL, bounds, "CatchmentAreaGroup"):
        g = f["geometry"]
        parts = [g["coordinates"]] if g["type"] == "LineString" else g["coordinates"]
        lines += [{"g": f["properties"]["CatchmentAreaGroup"], "c": part} for part in parts]
    return lines


def fetch_buildings(bounds, transform, shape):
    """Building outlines burnt onto the grid: each cell holds 0 or a building number.

    A cell counts as building when its centre is inside the outline, so a gap of a
    metre or more between two houses stays open for water.
    """
    to_merc = Transformer.from_crs(4326, 3857, always_xy=True)
    feats = arcgis_features(BUILDINGS_URL, bounds, "building_id")
    shapes, linz_ids = [], []
    for f in feats:
        g = f["geometry"]
        polys = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
        merc = [[[to_merc.transform(x, y) for x, y in ring] for ring in poly] for poly in polys]
        linz_ids.append(f["properties"]["building_id"])
        shapes.append(({"type": "MultiPolygon", "coordinates": merc}, len(linz_ids)))
    ids = rasterize(shapes, out_shape=shape, transform=transform, fill=0, dtype="uint16")
    return ids, linz_ids


def run_length(a):
    """Flatten a grid to (run lengths, values): long empty stretches cost almost nothing."""
    flat = a.ravel()
    starts = np.concatenate([[0], np.flatnonzero(np.diff(flat)) + 1])
    lengths = np.diff(np.concatenate([starts, [flat.size]]))
    b64 = lambda arr, dtype: base64.b64encode(arr.astype(dtype).tobytes()).decode()
    return b64(lengths, "<u4"), b64(flat[starts], "<u2")


def focus_box(focus, transform, k):
    """The tile we test fences in: its corners as lat/lon for the map, and as grid cells."""
    name, w, n, e, s = focus
    to_wgs = Transformer.from_crs(2193, 4326, always_xy=True)
    to_merc = Transformer.from_crs(2193, 3857, always_xy=True)
    corners = [(w, n), (e, n), (e, s), (w, s)]
    latlon = [[round(lat, 7), round(lon, 7)] for lon, lat in (to_wgs.transform(x, y) for x, y in corners)]
    cells = [[round((mx - transform.c) / k, 1), round((transform.f - my) / k, 1)]
             for mx, my in (to_merc.transform(x, y) for x, y in corners)]
    return {"name": name, "corners": latlon, "cells": cells}


def main():
    area = AREAS[AREA]
    lat, lon, extent, label = area["lat"], area["lon"], area["extent"], area["label"]
    if len(sys.argv) >= 3:
        lat, lon = float(sys.argv[1]), float(sys.argv[2])
        extent = tuple(int(v) for v in sys.argv[3:7]) if len(sys.argv) > 6 else extent
        label = sys.argv[7] if len(sys.argv) > 7 else f"{lat:.5f}, {lon:.5f}"

    dem, bounds, tiles, home, transform = fetch_dem(lat, lon, extent)
    valid = ~np.isnan(dem)
    zmin = math.floor(float(dem[valid].min()))
    q = np.full(dem.shape, NODATA_U16, dtype="<u2")
    q[valid] = np.clip(np.round((dem[valid] - zmin) * 100), 0, NODATA_U16 - 1).astype("<u2")
    print(f"dem {dem.shape}, {float(dem[valid].min()):.2f} to {float(dem[valid].max()):.2f} m, "
          f"{(~valid).sum()} nodata cells")

    olfp = fetch_olfp(bounds)
    print(f"council flow paths: {len(olfp)} lines")

    building_cells, linz_ids = fetch_buildings(bounds, transform, dem.shape)
    runs, values = run_length(building_cells)
    print(f"buildings: {len(linz_ids)}, covering {(building_cells > 0).mean() * 100:.0f}% of the map")

    data = {
        "label": label, "lat": lat, "lon": lon,
        "bounds": bounds,  # south, west, north, east
        "home": home,  # cell (x, y) of the address
        "width": dem.shape[1], "height": dem.shape[0],
        "cell": 1.0,  # ground metres per cell
        "zmin": zmin, "zscale": 0.01, "nodata": NODATA_U16,
        "source": {"dem": "LINZ Auckland Part 1 LiDAR 1m DEM (2024)", "tiles": tiles,
                   "olfp": "Auckland Council Overland Flow Paths (WSP 2019, 2016/17 LiDAR)",
                   "buildings": "LINZ NZ Building Outlines"},
        "olfp": olfp,
        # Cell value n means the nth building; linz_ids[n - 1] is its LINZ building_id.
        "buildings": {"runs": runs, "values": values, "linz_ids": linz_ids},
        "dem": base64.b64encode(q.tobytes()).decode(),
    }
    if area.get("focus"):
        data["focus"] = focus_box(area["focus"], transform, transform.a)
    OUT.mkdir(exist_ok=True)
    out = OUT / "data.js"
    out.write_text("globalThis.FLOW_DATA = " + json.dumps(data, separators=(",", ":")) + ";\n")
    print("wrote", out, f"{out.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
