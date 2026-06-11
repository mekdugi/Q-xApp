#!/usr/bin/env python3
"""Build an exactly size_m x size_m OSM map crop centered at (lat, lon).

Output is meter-accurate for the GUI simulation grid background:
the chart stretches the image over 0..size_m on both axes.
"""
import math, os, subprocess, sys
from PIL import Image, ImageDraw

LAT = float(sys.argv[1]) if len(sys.argv) > 1 else 37.5848
LON = float(sys.argv[2]) if len(sys.argv) > 2 else 127.0258
SIZE_M = float(sys.argv[3]) if len(sys.argv) > 3 else 500.0
ZOOM = int(sys.argv[4]) if len(sys.argv) > 4 else 18
OUT = sys.argv[5] if len(sys.argv) > 5 else "/tmp/univmap_osm.png"
CACHE = "/tmp/osm_tiles"
os.makedirs(CACHE, exist_ok=True)

TILE = 256
n = 2 ** ZOOM

def to_global_px(lat, lon):
    x = (lon + 180.0) / 360.0 * n * TILE
    s = math.sin(math.radians(lat))
    y = (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * n * TILE
    return x, y

mpp = 156543.03392 * math.cos(math.radians(LAT)) / n  # meters per pixel
half_px = SIZE_M / 2.0 / mpp
cx, cy = to_global_px(LAT, LON)
x0, y0 = cx - half_px, cy - half_px
x1, y1 = cx + half_px, cy + half_px
tx0, ty0 = int(x0 // TILE), int(y0 // TILE)
tx1, ty1 = int(x1 // TILE), int(y1 // TILE)

mosaic = Image.new("RGB", ((tx1 - tx0 + 1) * TILE, (ty1 - ty0 + 1) * TILE), "white")
for tx in range(tx0, tx1 + 1):
    for ty in range(ty0, ty1 + 1):
        fp = f"{CACHE}/{ZOOM}_{tx}_{ty}.png"
        if not os.path.isfile(fp):
            url = f"https://basemaps.cartocdn.com/rastertiles/voyager_nolabels/{ZOOM}/{tx}/{ty}.png"
            subprocess.run(["curl", "-s", "-A", "QxApp-paper-figure/1.0 (research)",
                            "-o", fp, url], check=True, timeout=30)
        try:
            t = Image.open(fp).convert("RGB")
        except Exception:
            continue
        mosaic.paste(t, ((tx - tx0) * TILE, (ty - ty0) * TILE))

crop = mosaic.crop((int(round(x0 - tx0 * TILE)), int(round(y0 - ty0 * TILE)),
                    int(round(x1 - tx0 * TILE)), int(round(y1 - ty0 * TILE))))
crop = crop.resize((1000, 1000), Image.LANCZOS)

d = ImageDraw.Draw(crop)
attr = "(C) OpenStreetMap contributors, (C) CARTO"
d.rectangle([(1000 - 300, 1000 - 20), (1000, 1000)], fill=(255, 255, 255))
d.text((1000 - 295, 1000 - 17), attr, fill=(60, 60, 60))
crop.save(OUT)
print(f"saved {OUT}  center=({LAT},{LON}) size={SIZE_M}m zoom={ZOOM} "
      f"mpp={mpp:.3f} px_span={2*half_px:.0f}")
