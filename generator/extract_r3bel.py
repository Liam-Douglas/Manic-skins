#!/usr/bin/env python3
"""Extract the screen-filter mesh tile from an R3BEL .deltaskin for personal builds.

Usage: python3 generator/extract_r3bel.py <skin.deltaskin>
Writes third_party/r3bel/mesh-<orientation>.png (git-ignored: third-party art, not ours to publish).
The skin's art is 3 device pixels per point, so a tile of N px covers N/3 points on the phone.
"""
import io
import json
import os
import sys
import zipfile

import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "third_party", "r3bel")


def find_period(alpha):
    """Smallest shift (px) that maps the pattern exactly onto itself, both axes."""
    for s in range(3, 200):
        if (np.abs(alpha[:, s:] - alpha[:, :-s]).max() == 0 and np.abs(alpha[s:, :] - alpha[:-s, :]).max() == 0):
            return s
    raise SystemExit("no exact repeat found")


def main(path):
    z = zipfile.ZipFile(path)
    info = json.loads(z.read("info.json"))
    os.makedirs(OUT, exist_ok=True)
    for orient, rep in info["representations"]["iphone"]["edgeToEdge"].items():
        asset = next(iter(rep["assets"].values()))
        img = Image.open(io.BytesIO(z.read(asset))).convert("RGBA")
        f = rep["screens"][0]["outputFrame"]
        # a block well inside the first screen, away from its rounded corners
        x0, y0 = f["x"] + 60, f["y"] + 60
        block = np.array(img.crop((x0, y0, x0 + 400, y0 + 400)))
        period = find_period(block[..., 3].astype(int))
        tile = Image.fromarray(block[:period, :period])
        out = os.path.join(OUT, f"mesh-{orient}.png")
        tile.save(out)
        print(f"{orient}: {period}x{period} px tile -> {out}")


if __name__ == "__main__":
    main(sys.argv[1])
