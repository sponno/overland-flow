# /// script
# requires-python = ">=3.11"
# dependencies = ["laspy[lazrs]", "numpy", "pyproj", "rasterio", "shapely"]
# ///
"""Put the fence lines in a fixed random order for checking by eye, and write sample.js.

  AREA=mtalbert uv run make_sample.py

check.html shows them one at a time. Because the order is random, the first 50 checked
are a fair sample of all of them, and checking more only makes it better. Each entry
carries the LINZ boundary line, so the page needs nothing else.
"""
import json
import random

import numpy as np
from pyproj import Transformer

from fetch_data import OUT
from scan_fences import boundaries, load_js

SEED = 16  # the hackathon's date, so the order never changes


def main():
    grid, ranked = load_js("data.js"), load_js("fences.js")
    fences = ranked["fences"]
    lines = boundaries(fences, grid["bounds"])
    to_wgs = Transformer.from_crs(2193, 4326, always_xy=True)
    order = list(range(len(fences)))
    random.Random(SEED).shuffle(order)

    items = []
    for k in order:
        segments = lines[k]
        if not segments:
            print(f"fence {k}: no shared boundary in LINZ parcels, skipped")
            continue
        drawn = []
        for p, q in segments:
            (x1, x2), (y1, y2) = to_wgs.transform([p[0], q[0]], [p[1], q[1]])
            drawn.append([[round(y1, 7), round(x1, 7)], [round(y2, 7), round(x2, 7)]])
        # The middle of the longest straight run, for the Street View and 3D links.
        p, q = max(segments, key=lambda s: np.hypot(*(s[1] - s[0])))
        lon, lat = to_wgs.transform(*((p + q) / 2))
        length = sum(float(np.hypot(*(q - p))) for p, q in segments)
        items.append({
            "n": len(items) + 1, "fence": k, "between": fences[k]["between"], "street": fences[k]["street"],
            "length": round(length), "centre": [round(lat, 7), round(lon, 7)], "lines": drawn,
        })

    out = OUT / "sample.js"
    data = {"seed": SEED, "tile": grid.get("focus", {}).get("name"), "items": items}
    out.write_text("globalThis.SAMPLE_DATA = " + json.dumps(data, separators=(",", ":")) + ";\n")
    print(f"wrote {out}, {len(items)} fence lines")


if __name__ == "__main__":
    main()
