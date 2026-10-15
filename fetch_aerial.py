# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "pillow", "pyproj", "rasterio"]
# ///
"""Map tiles of the 2024 aerial photo for the pilot tile, so the pages can show it under the lines.

  AREA=mtalbert uv run fetch_aerial.py

Downloads LINZ's 7.5 cm Auckland urban aerial photo (2024-25, CC BY 4.0) for the area's
focus tile (AREAS in fetch_data.py) into lidar/aerial/ (75 MB, not committed), and cuts
web map tiles from it, zoom 16 to 20, into aerial/ next to data.js, with aerial.js saying
where they are for the pages. Zoom 20 is 12 cm a pixel, enough to see a fence.
Outside the tile the pages fall back to Esri's photo.
"""
import io
import json
import urllib.request
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image
from pyproj import Transformer
from rasterio.transform import from_bounds
from rasterio.warp import Resampling, reproject

from fetch_data import AREA, AREAS, OUT

PHOTOS = "https://nz-imagery.s3.ap-southeast-2.amazonaws.com/auckland/auckland_2024_0.075m/rgb/2193"
ZOOMS = range(16, 21)
WORLD = 20037508.342789244  # half the width of the web map, metres
SIZE = 256


def main():
    name, west, north, east, south = AREAS[AREA]["focus"]
    path = Path(__file__).parent / "lidar" / "aerial" / f"{name}.tiff"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        print(f"downloading {name}.tiff (75 MB)")
        urllib.request.urlretrieve(f"{PHOTOS}/{name}.tiff", path.with_suffix(".part"))
        path.with_suffix(".part").rename(path)

    to_web = Transformer.from_crs(2193, 3857, always_xy=True)
    xs, ys = to_web.transform([west, east, east, west], [north, north, south, south])
    folder = OUT / "aerial"
    count = 0
    for z in ZOOMS:
        span = 2 * WORLD / 2 ** z
        # The photo has overviews at 2, 4, 8, 16 and 32 times coarser: read the finest one coarser
        # than the zoom needs (about 0.8 of the web map's metres, this far south).
        ground = span / SIZE * 0.8
        level = max((k for k, f in enumerate([2, 4, 8, 16, 32]) if 0.075 * f <= ground), default=None)
        with rasterio.open(path, **({} if level is None else {"overview_level": level})) as src:
            for tx in range(int((min(xs) + WORLD) // span), int((max(xs) + WORLD) // span) + 1):
                for ty in range(int((WORLD - max(ys)) // span), int((WORLD - min(ys)) // span) + 1):
                    left, top = tx * span - WORLD, WORLD - ty * span
                    tile = np.zeros((4, SIZE, SIZE), np.uint8)
                    reproject(rasterio.band(src, (1, 2, 3, 4)), tile, dst_crs="EPSG:3857",
                              dst_transform=from_bounds(left, top - span, left + span, top, SIZE, SIZE),
                              resampling=Resampling.bilinear)
                    if not tile[3].any():
                        continue
                    out = folder / str(z) / str(tx) / f"{ty}.webp"
                    out.parent.mkdir(parents=True, exist_ok=True)
                    buffer = io.BytesIO()
                    Image.fromarray(np.moveaxis(tile, 0, -1), "RGBA").save(buffer, "WEBP", quality=72, method=5)
                    out.write_bytes(buffer.getvalue())
                    count += 1
        print(f"zoom {z}: done")
    lon, lat = Transformer.from_crs(2193, 4326, always_xy=True).transform([west, east, east, west], [north, north, south, south])
    about = {"tiles": "aerial/{z}/{x}/{y}.webp", "bounds": [[min(lat), min(lon)], [max(lat), max(lon)]],
             "minZoom": ZOOMS[0], "maxZoom": ZOOMS[-1],
             "attribution": "Aerial 2024-25 © LINZ, Aerial Surveys Ltd, Auckland Council, CC BY 4.0"}
    (OUT / "aerial.js").write_text("globalThis.AERIAL_DATA = " + json.dumps(about) + ";\n")
    size = sum(p.stat().st_size for p in folder.rglob("*.webp"))
    print(f"wrote {count} tiles into {folder}, {size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
