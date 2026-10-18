#!/usr/bin/env python3
"""Generate the body texture tiles (original, seeded, seamless).

Usage: python3 generator/make_textures.py
Writes generator/textures/<name>.png: RGBA tiles (128 px; pebble 256 px) of light and dark specks on transparent,
drawn at 3 px per device point. Pure per-pixel noise repeats without seams; the pebbled tile is
blurred with wrap-around so it stays seamless too.
"""
import os

import numpy as np
from PIL import Image, ImageFilter

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "textures")
SIZE = 128


def to_rgba(v, light, dark):
    """v in [-1, 1] per pixel: positive -> white at v*light opacity, negative -> black at -v*dark."""
    rgba = np.zeros(v.shape + (4,), dtype=np.uint8)
    pos = v > 0
    rgba[pos, :3] = 255
    rgba[..., 3] = np.where(pos, v * light, -v * dark).clip(0, 1) * 255
    return Image.fromarray(rgba, "RGBA")


def grain(rng):
    v = rng.uniform(-1, 1, (SIZE, SIZE))
    return to_rgba(v, 0.09, 0.10)


def speckle(rng):
    v = np.zeros((SIZE, SIZE))
    r = rng.random((SIZE, SIZE))
    v[r < 0.035] = -1.0
    v[(r >= 0.035) & (r < 0.06)] = 1.0
    v += rng.uniform(-0.25, 0.25, (SIZE, SIZE))  # a faint grain between the flecks
    return to_rgba(v, 0.16, 0.20)


def pebbled(rng, size=256):
    base = rng.uniform(0, 255, (size, size)).astype(np.uint8)
    tiled = Image.fromarray(np.tile(base, (3, 3)))
    blurred = np.array(tiled.filter(ImageFilter.GaussianBlur(1.6)), dtype=float)[size:2 * size, size:2 * size]
    v = (blurred - blurred.mean()) / (blurred.std() * 2.5)
    v += rng.uniform(-0.2, 0.2, (size, size))
    return to_rgba(v.clip(-1, 1), 0.06, 0.07)


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    for name, fn, seed in (("grain", grain, 11), ("speckle", speckle, 23), ("pebble", pebbled, 37)):
        path = os.path.join(OUT, f"{name}.png")
        fn(np.random.default_rng(seed)).save(path, optimize=True)
        print(path, os.path.getsize(path), "bytes")
