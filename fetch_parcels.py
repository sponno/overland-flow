# /// script
# requires-python = ">=3.11"
# dependencies = ["rasterio", "numpy", "pyproj"]
# ///
"""Fetch LINZ primary parcels for the area data.js covers and write parcels.js.

  uv run fetch_parcels.py

Run fetch_data.py first: this reads the grid from data.js so the two line up cell
for cell. Each cell ends up holding 0 (no parcel) or a parcel number.
"""
import json
from collections import Counter
from pathlib import Path

from pyproj import Transformer
from rasterio.features import rasterize
from rasterio.transform import from_origin

from fetch_data import arcgis_features, run_length

PARCELS_URL = "https://services.arcgis.com/xdsHIIxuCWByZiCB/arcgis/rest/services/LINZ_NZ_Primary_Parcels/FeatureServer/0/query"
HERE = Path(__file__).parent


def main():
    text = (HERE / "data.js").read_text()
    grid = json.loads(text[text.index("{"):text.rindex("}") + 1])
    south, west, north, east = grid["bounds"]
    width, height = grid["width"], grid["height"]

    to_merc = Transformer.from_crs(4326, 3857, always_xy=True)
    x0, y0 = to_merc.transform(west, north)
    x1, _ = to_merc.transform(east, south)
    k = (x1 - x0) / width  # mercator units per cell, the same as fetch_data.py used
    transform = from_origin(x0, y0, k, k)

    feats = arcgis_features(PARCELS_URL, grid["bounds"], "id,parcel_intent,Shape__Area")
    # Biggest first, so a small lot is never hidden under a big parcel that overlaps it.
    feats.sort(key=lambda f: -(f["properties"]["Shape__Area"] or 0))
    shapes, parcels = [], []
    for f in feats:
        merc = [[[to_merc.transform(x, y) for x, y in ring] for ring in polygon] for polygon in polygons(f)]
        parcels.append({"id": f["properties"]["id"], "intent": f["properties"]["parcel_intent"]})
        shapes.append(({"type": "MultiPolygon", "coordinates": merc}, len(parcels)))
    cells = rasterize(shapes, out_shape=(height, width), transform=transform, fill=0, dtype="uint16")

    runs, values = run_length(cells)
    print(f"parcels: {len(parcels)}, covering {(cells > 0).mean() * 100:.0f}% of the map")
    for intent, n in Counter(p["intent"] for p in parcels).most_common():
        print(f"  {n:5d}  {intent}")

    out = HERE / "parcels.js"
    data = {
        "source": "LINZ NZ Primary Parcels", "width": width, "height": height,
        # Cell value n means the nth parcel; parcels[n - 1] holds its LINZ id and intent.
        "runs": runs, "values": values, "parcels": parcels,
    }
    out.write_text("globalThis.PARCEL_DATA = " + json.dumps(data, separators=(",", ":")) + ";\n")
    print("wrote", out, f"{out.stat().st_size / 1e6:.1f} MB")


def polygons(feature):
    g = feature["geometry"]
    return [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]


if __name__ == "__main__":
    main()
