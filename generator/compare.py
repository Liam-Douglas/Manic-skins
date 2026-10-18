#!/usr/bin/env python3
"""Render canvas artboards in headless Chromium and compare them with the skin previews.

Usage: python3 generator/compare.py [skin ...]
Writes dist/compare/<skin>-<orientation>.png (canvas | skin | difference) and prints
the regions where the two disagree, screens masked out.
"""
import os
import re
import subprocess
import sys
import tempfile

from PIL import Image, ImageChops, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build  # noqa: E402

CHROME = "/opt/pw-browsers/chromium_headless_shell-1194/chrome-linux/headless_shell"
SCALE = 3
OUT = os.path.join(build.DIST, "compare")
# canvas asset ids of the uploaded R3BEL mesh tiles
TEXTURE_BLOBS = {"grain": "4278af5b59bfabbaafc4c035778876d1", "speckle": "394dbd3bb96d337d5867997fcee5c66c",
                 "pebble": "9e149dcb5240ae2c905e7a9a060b482e"}
MESH_BLOBS = {"portrait": "8192c6d469f7ad15c4d10c0ea8475386", "landscape": "c04d6cf2230417e8316642f2ca8a0527"}


def prepare(html, vals=None):
    """Fill the artboard's holes and point fonts and uploaded tiles at local files."""
    vals = vals or {"bodyTop": build.STYLE["body_top"], "bodyBottom": build.STYLE["body_bottom"],
                    "accent": build.STYLE["accent"]}
    html = re.sub(r"\{\{\s*(\w+)\s*\}\}", lambda m: vals.get(m.group(1), ""), html)
    html = html.replace('<script src="./support.js"></script>', "")
    html = re.sub(r'<script type="text/x-dc".*?</script>', "", html, flags=re.S)
    html = re.sub(r'<link[^>]*fonts\.g[^>]*>', "", html)
    for name, blob in TEXTURE_BLOBS.items():
        html = html.replace(f"url(/_blob/{blob})", "url('file://%s')" % os.path.join(build.TEXTURES, f"{name}.png"))
    for orient, blob in MESH_BLOBS.items():
        html = html.replace(f"url(/_blob/{blob})", "url('file://%s')" % os.path.join(build.THIRD_PARTY, f"mesh-{orient}.png"))
    fonts = os.path.join(build.ROOT, "fonts")
    faces = "".join("@font-face{font-family:'%s';font-weight:%d;src:url('file://%s/%s')}" % (fam, wt, fonts, f)
                    for fam, wt, f in (("Space Grotesk", 500, "SpaceGrotesk-Medium.ttf"),
                                       ("Space Grotesk", 700, "SpaceGrotesk-Bold.ttf"),
                                       ("Rubik", 500, "Rubik-Medium.ttf"), ("Rubik", 700, "Rubik-Bold.ttf")))
    face = "<style>%shtml,body{background:#000}</style>" % faces
    return html.replace("<helmet>", face).replace("</helmet>", "")


def render_artboard(fname, size, out_png, vals=None):
    with open(os.path.join(build.CANVAS, fname)) as fh:
        html = prepare(fh.read(), vals)
    tmp = tempfile.NamedTemporaryFile("w", suffix=".html", delete=False)
    tmp.write(html)
    tmp.close()
    subprocess.run([CHROME, "--no-sandbox", "--disable-gpu", "--hide-scrollbars",
                    f"--force-device-scale-factor={SCALE}", f"--window-size={size[0]},{size[1]}",
                    "--virtual-time-budget=3000", f"--screenshot={out_png}", "file://" + tmp.name],
                   check=True, capture_output=True)
    os.unlink(tmp.name)
    return Image.open(out_png).convert("RGB")


def compare(skin_key):
    skin = build.SKINS[skin_key]
    os.makedirs(OUT, exist_ok=True)
    report = []
    for orient, o in skin["orientations"].items():
        if "canvas_file" not in o:
            continue
        W, H = o["size"]
        canvas_png = os.path.join(OUT, f"{skin['file']}-{orient}-canvas.png")
        a = render_artboard(o["canvas_file"], o["size"], canvas_png, o.get("vals"))
        b = Image.open(os.path.join(build.DIST, f"{skin['file']}-{orient}.png")).convert("RGB")
        if a.size != b.size:
            a = a.resize(b.size)
        diff = ImageChops.difference(a, b).convert("L").point(lambda v: 255 if v > 48 else 0)
        mask = Image.new("L", diff.size, 255)
        md = ImageDraw.Draw(mask)
        for s in o["screens"]:  # placeholders differ on purpose
            x, y, w, h = s["out"]
            md.rectangle([x * SCALE, y * SCALE, (x + w) * SCALE, (y + h) * SCALE], fill=0)
        if "safe_y" in o:  # canvas shades the covered area; the skin draws plain body there
            md.rectangle([0, o["safe_y"] * SCALE, W * SCALE, H * SCALE], fill=0)
        # the canvas clips its corners to the 55pt screen radius; the skin is a full rectangle
        corner = Image.new("L", diff.size, 0)
        ImageDraw.Draw(corner).rounded_rectangle([0, 0, W * SCALE - 1, H * SCALE - 1], radius=55 * SCALE, fill=255)
        mask = ImageChops.multiply(mask, corner)
        diff = ImageChops.multiply(diff, mask)

        # 6pt cells; a cell disagrees when over 12% of its pixels differ
        cell = 6 * SCALE
        bad = []
        px = diff.load()
        for cy in range(0, diff.size[1], cell):
            for cx in range(0, diff.size[0], cell):
                n = sum(1 for yy in range(cy, min(cy + cell, diff.size[1]), 2)
                        for xx in range(cx, min(cx + cell, diff.size[0]), 2) if px[xx, yy])
                if n > 0.12 * (cell / 2) ** 2:
                    bad.append((cx // SCALE, cy // SCALE))
        sheet = Image.new("RGB", (b.size[0] * 3 + 40, b.size[1]), (236, 237, 239))
        sheet.paste(a, (0, 0))
        sheet.paste(b, (b.size[0] + 20, 0))
        red = Image.new("RGB", b.size, (255, 40, 40))
        overlay = Image.composite(red, b.point(lambda v: v // 3), diff)
        sheet.paste(overlay, (b.size[0] * 2 + 40, 0))
        path = os.path.join(OUT, f"{skin['file']}-{orient}.png")
        sheet.save(path)
        report.append((orient, bad, path))
    return report


if __name__ == "__main__":
    keys = sys.argv[1:] or [k for k, s in build.SKINS.items()
                            if any("canvas_file" in o for o in s["orientations"].values())]
    for k in keys:
        for orient, bad, path in compare(k):
            status = "match" if not bad else f"{len(bad)} cells differ, e.g. {bad[:8]}"
            print(f"{build.SKINS[k]['name']} {orient}: {status}  -> {path}")
