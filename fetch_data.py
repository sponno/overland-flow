# /// script
# requires-python = ">=3.11"
# dependencies = ["rasterio", "numpy", "pyproj"]
# ///
"""Fetch real data around one address and write data.js for the viewer.

  uv run fetch_data.py [lat lon west east north south label]

Pulls:
  - Auckland 2024 1 m DEM (bare earth) from the LINZ nz-elevation open bucket,
    reading only the window we need from the cloud-optimised GeoTIFFs.
  - Auckland Council's mapped overland flow paths for the same box.

The DEM is resampled onto a Web Mercator grid at 1 m ground resolution so it
lines up exactly with web map tiles.
"""
import base64
import json
import math
import sys
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.transform import from_origin
from rasterio.warp import Resampling, reproject

# Top of Vermont Street, Ponsonby: the highest point on the street centreline (OSM)
# that drains down the street. The last couple of metres at Ponsonby Rd sit on the
# ridge and shed east instead.
LAT, LON = -36.85299552, 174.74517279
EXTENT = (1742, 258, 590, 710)  # metres west, east, north, south of the point
LABEL = "Top of Vermont St"

DEM_BASE = "https://nz-elevation.s3.ap-southeast-2.amazonaws.com/auckland/auckland-part-1_2024/dem_1m/2193"
OLFP_URL = "https://services1.arcgis.com/n4yPwebTjJCmXB6W/arcgis/rest/services/Overland_Flow_Paths/FeatureServer/0/query"

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
    return dem, (s_lat, w_lon, n_lat, e_lon), tiles, (ext_w, ext_n)


def fetch_olfp(bounds):
    s, w, n, e = bounds
    feats, offset = [], 0
    while True:
        q = urllib.parse.urlencode({
            "where": "1=1", "geometry": f"{w},{s},{e},{n}", "geometryType": "esriGeometryEnvelope",
            "inSR": 4326, "outSR": 4326, "spatialRel": "esriSpatialRelIntersects",
            "outFields": "CatchmentAreaGroup", "geometryPrecision": 6,
            "resultOffset": offset, "f": "geojson",
        })
        with urllib.request.urlopen(f"{OLFP_URL}?{q}") as r:
            page = json.load(r)
        feats += page.get("features", [])
        if not page.get("properties", {}).get("exceededTransferLimit") and not page.get("exceededTransferLimit"):
            break
        offset = len(feats)
    return [
        {"g": f["properties"]["CatchmentAreaGroup"], "c": f["geometry"]["coordinates"]}
        for f in feats if f.get("geometry") and f["geometry"]["type"] == "LineString"
    ] + [
        {"g": f["properties"]["CatchmentAreaGroup"], "c": line}
        for f in feats if f.get("geometry") and f["geometry"]["type"] == "MultiLineString"
        for line in f["geometry"]["coordinates"]
    ]


def main():
    lat, lon, extent, label = LAT, LON, EXTENT, LABEL
    if len(sys.argv) >= 3:
        lat, lon = float(sys.argv[1]), float(sys.argv[2])
        extent = tuple(int(v) for v in sys.argv[3:7]) if len(sys.argv) > 6 else EXTENT
        label = sys.argv[7] if len(sys.argv) > 7 else f"{lat:.5f}, {lon:.5f}"

    dem, bounds, tiles, home = fetch_dem(lat, lon, extent)
    valid = ~np.isnan(dem)
    zmin = math.floor(float(dem[valid].min()))
    q = np.full(dem.shape, NODATA_U16, dtype="<u2")
    q[valid] = np.clip(np.round((dem[valid] - zmin) * 100), 0, NODATA_U16 - 1).astype("<u2")
    print(f"dem {dem.shape}, {float(dem[valid].min()):.2f} to {float(dem[valid].max()):.2f} m, "
          f"{(~valid).sum()} nodata cells")

    olfp = fetch_olfp(bounds)
    print(f"council flow paths: {len(olfp)} lines")

    data = {
        "label": label, "lat": lat, "lon": lon,
        "bounds": bounds,  # south, west, north, east
        "home": home,  # cell (x, y) of the address
        "width": dem.shape[1], "height": dem.shape[0],
        "cell": 1.0,  # ground metres per cell
        "zmin": zmin, "zscale": 0.01, "nodata": NODATA_U16,
        "source": {"dem": "LINZ Auckland Part 1 LiDAR 1m DEM (2024)", "tiles": tiles,
                   "olfp": "Auckland Council Overland Flow Paths (WSP 2019, 2016/17 LiDAR)"},
        "olfp": olfp,
        "dem": base64.b64encode(q.tobytes()).decode(),
    }
    out = Path(__file__).with_name("data.js")
    out.write_text("globalThis.FLOW_DATA = " + json.dumps(data, separators=(",", ":")) + ";\n")
    print("wrote", out, f"{out.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
