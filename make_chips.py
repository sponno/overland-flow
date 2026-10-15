# /// script
# requires-python = ">=3.11"
# dependencies = ["laspy[lazrs]", "numpy", "pillow", "pyproj", "rasterio", "scipy", "shapely"]
# ///
"""One picture of the evidence for each fence line, for a person (or a vision model) to check.

  AREA=mtalbert uv run make_chips.py

Writes chips/NNN.jpg next to data.js, NNN being the line's place in fences.js. Each is
the line turned to run left to right, 4 m either side, with the line's ends marked by red
ticks at the edges, in four strips: the 2024 aerial photo, the colour infrared photo
(leaves show red), the laser points standing 0.3 to 3 m high seen from above, and the laser
points within 0.75 m of the line seen from the side (0 to 4 m up). Laser colours: black
unclassified (fences, walls, cars), green vegetation, red building.
"""
import numpy as np
from PIL import Image, ImageDraw

from classify_fences import PIXEL as IR_PIXEL, infrared
from evidence import ACROSS, STEP, Frame, aerial_in, infrared_in
from fetch_data import OUT
from scan_fences import CONTROL_SHIFT, boundaries, ground_under, load_js, read_points, shifted

COLOURS = {1: (20, 20, 20), 2: (176, 128, 80), 3: (47, 158, 68), 4: (47, 158, 68), 5: (47, 158, 68), 6: (208, 64, 64)}
WIDEST = 1100  # pixels: longer lines are drawn smaller
SIDE_TOP, SIDE_STEP = 4.0, 0.0375  # the side view runs 0 to 4 m up, at twice the detail across


def strip_points(frame, along, across, values, cls, rows, value_to_row):
    """Draw laser points onto a white strip as small squares."""
    img = np.full((rows, frame.cols, 3), 255, np.uint8)
    order = np.argsort(values)  # higher points on top
    for a, v, c in zip(along[order], values[order], cls[order]):
        x = int((a - frame.start) / STEP)
        y = value_to_row(v)
        if 0 <= x < frame.cols and 0 <= y < rows:
            img[max(0, y - 1):y + 2, max(0, x - 1):x + 2] = COLOURS.get(int(c), (120, 120, 120))
    return img


def chip(frame, rgba, ir, along, across, height, cls, title):
    rows = frame.rows
    photo = np.moveaxis(rgba[:3], 0, -1).clip(0, 255).astype(np.uint8)
    nir = np.moveaxis(ir, 0, -1).clip(0, 255).astype(np.uint8)
    up = (height >= 0.3) & (height <= 3.0) & (cls != 2)
    # Seen from above: position across is the row.
    top = np.full((rows, frame.cols, 3), 255, np.uint8)
    for a, c, k in zip(along[up], across[up], cls[up]):
        x, y = int((a - frame.start) / STEP), int((ACROSS - c) / STEP)
        if 0 <= x < frame.cols and 0 <= y < rows:
            top[max(0, y - 1):y + 2, max(0, x - 1):x + 2] = COLOURS.get(int(k), (120, 120, 120))
    near = np.abs(across) <= 0.75
    side_rows = int(SIDE_TOP / SIDE_STEP)
    side = strip_points(frame, along[near], across[near], height[near], cls[near], side_rows,
                        lambda v: int((SIDE_TOP - v) / SIDE_STEP))
    for m in (1, 2, 3):  # faint lines every metre up
        side[int((SIDE_TOP - m) / SIDE_STEP), ::6] = (170, 190, 220)
    gap = np.full((6, frame.cols, 3), 255, np.uint8)
    stack = np.concatenate([photo, gap, nir, gap, top, gap, side])
    img = Image.fromarray(stack)
    draw = ImageDraw.Draw(img)
    # Ticks where the line is, at both ends of the three top-down strips.
    mid = rows // 2
    for k in range(3):
        y = k * (rows + 6) + mid
        for x0, dx in ((0, 1), (frame.cols - 1, -1)):
            draw.polygon([(x0, y - 7), (x0, y + 7), (x0 + 12 * dx, y)], fill=(230, 30, 30))
    scale = min(1.0, WIDEST / frame.cols)
    if scale < 1:
        img = img.resize((int(frame.cols * scale), int(img.height * scale)), Image.LANCZOS)
    out = Image.new("RGB", (max(img.width, 420), img.height + 22), (255, 255, 255))
    out.paste(img, (0, 22))
    ImageDraw.Draw(out).text((4, 5), title, fill=(20, 20, 20))
    return out


def main():
    grid, ranked = load_js("data.js"), load_js("fences.js")
    fences = ranked["fences"]
    lines = boundaries(fences, grid["bounds"])
    controls = [shifted(segs, by) for segs in lines for by in (CONTROL_SHIFT, -CONTROL_SHIFT)]
    points, _ = read_points([s for segs in lines + controls for s in segs])
    height = points[:, 2] - ground_under(points, grid["source"]["tiles"])
    ir, west, north = infrared(points)
    folder = OUT / "chips"
    folder.mkdir(exist_ok=True)
    for k, segs in enumerate(lines):
        if not segs:
            continue
        frame = Frame(segs)
        along, across = frame.local(points[:, :2])
        keep = (along >= frame.start) & (along <= frame.end) & (np.abs(across) <= ACROSS)
        rgba = aerial_in(frame)
        nir = infrared_in(frame, ir, west, north, IR_PIXEL)
        title = f"Line {k} · {frame.end - frame.start - 4:.0f} m · photo, infrared, laser from above, laser from the side"
        chip(frame, rgba, nir, along[keep], across[keep], height[keep], points[keep, 3], title).save(folder / f"{k:03d}.jpg", quality=85)
    print(f"wrote {len(list(folder.glob('*.jpg')))} chips into {folder}")


if __name__ == "__main__":
    main()
