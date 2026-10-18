#!/usr/bin/env python3
"""Manic EMU skin generator (Arctic Modern style).

Builds .manicskin files (zip of info.json + vector PDFs) and preview PNGs.
Coordinates are in mapping points, top-left origin, as in the skin layout.

Usage: python3 generator/build.py [skin ...]   (default: every skin in SKINS)
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

from PIL import Image, ImageDraw
from reportlab.lib.colors import Color, HexColor
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.pdfgen.canvas import FILL_EVEN_ODD

from artboard import check_rules, parse_artboard

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONTS = os.path.join(ROOT, "fonts")
DIST = os.path.join(ROOT, "dist")

pdfmetrics.registerFont(TTFont("SG-Medium", os.path.join(FONTS, "SpaceGrotesk-Medium.ttf")))
pdfmetrics.registerFont(TTFont("SG-Bold", os.path.join(FONTS, "SpaceGrotesk-Bold.ttf")))

# ---------------------------------------------------------------- style
STYLE = {
    "body_top": "#1b1c1f",
    "body_bottom": "#0f1011",
    "bezel": "#050506",
    "button": "#1f2124",
    "dpad": "#26282c",
    "face_dark": "#2a2c30",
    "face_light": "#d7dade",
    "icon": "#e7e9ec",
    "pill_text": "#c9ced3",
    "label": "#7d848b",
    "dark_text": "#141517",
    "accent": "#ff4d4f",
}

# Pocket Taco safe line, calibrated on device with the Taco Ruler: the y 560 line is the
# last one fully visible, so nothing interactive goes below 550 on Taco layouts.
TACO_SAFE_Y = 550

DS_TOP = (0, 0, 256, 192)
DS_BOTTOM = (0, 192, 256, 192)


def col(hexstr, alpha=1.0):
    c = HexColor(hexstr)
    return Color(c.red, c.green, c.blue, alpha=alpha)


WHITE = lambda a: Color(1, 1, 1, alpha=a)


# ---------------------------------------------------------------- drawing helpers (top-left coords)
class Pen:
    def __init__(self, path, w, h):
        self.c = canvas.Canvas(path, pagesize=(w, h))
        self.w, self.h = w, h

    def y(self, y):
        return self.h - y

    def rrect(self, p, x, y, w, h, r):
        p.roundRect(x, self.h - y - h, w, h, r)

    def fill_rrect(self, x, y, w, h, r, fill, stroke=None, lw=1.0):
        c = self.c
        c.setFillColor(fill)
        c.roundRect(x, self.h - y - h, w, h, r, stroke=0, fill=1)
        if stroke is not None:
            self.stroke_rrect(x, y, w, h, r, stroke, lw)

    def stroke_rrect(self, x, y, w, h, r, stroke, lw=1.0):
        c = self.c
        c.setStrokeColor(stroke)
        c.setLineWidth(lw)
        i = lw / 2
        c.roundRect(x + i, self.h - y - h + i, w - 2 * i, h - 2 * i, max(r - i, 0), stroke=1, fill=0)

    def circle(self, cx, cy, r, fill=None, stroke=None, lw=1.0):
        c = self.c
        if fill is not None:
            c.setFillColor(fill)
            c.circle(cx, self.h - cy, r, stroke=0, fill=1)
        if stroke is not None:
            c.setStrokeColor(stroke)
            c.setLineWidth(lw)
            c.circle(cx, self.h - cy, r - lw / 2, stroke=1, fill=0)

    def poly(self, pts, fill=None, stroke=None, lw=1.5, close=False):
        c = self.c
        p = c.beginPath()
        p.moveTo(pts[0][0], self.h - pts[0][1])
        for x, y in pts[1:]:
            p.lineTo(x, self.h - y)
        if close:
            p.close()
        if fill is not None:
            c.setFillColor(fill)
        if stroke is not None:
            c.setStrokeColor(stroke)
            c.setLineWidth(lw)
            c.setLineCap(1)
            c.setLineJoin(1)
        c.drawPath(p, stroke=1 if stroke is not None else 0, fill=1 if fill is not None else 0)

    def text(self, s, cx, baseline, font, size, color, spacing=0.0, align="center", x=None):
        c = self.c
        sw = pdfmetrics.stringWidth(s, font, size) + spacing * (len(s) - 1)
        if align == "center":
            x0 = cx - sw / 2
        else:
            x0 = x
        t = c.beginText()
        t.setFont(font, size)
        t.setCharSpace(spacing)
        t.setFillColor(color)
        t.setTextOrigin(x0, self.h - baseline)
        t.textOut(s)
        c.drawText(t)

    def save(self):
        self.c.showPage()
        self.c.save()


# icons in a 20x20 box, stroke 1.5
ICONS = {
    "menu": [([(4, 6), (16, 6)], False), ([(4, 10), (16, 10)], False), ([(4, 14), (16, 14)], False)],
    "fast": [([(3, 5), (9, 10), (3, 15)], True), ([(10.5, 5), (16.5, 10), (10.5, 15)], True)],
    "save": [([(10, 3), (10, 12)], False), ([(6, 8), (10, 12), (14, 8)], False), ([(4, 15.5), (16, 15.5)], False)],
    "swap": [([(7, 15), (7, 5)], False), ([(4, 8), (7, 5), (10, 8)], False),
             ([(13, 5), (13, 15)], False), ([(10, 12), (13, 15), (16, 12)], False)],
    "home": [([(3.5, 9.5), (10, 4), (16.5, 9.5)], False), ([(5.5, 8), (5.5, 16), (14.5, 16), (14.5, 8)], False)],
}


def draw_icon(pen, name, cx, cy, size, color):
    k = size / 20.0
    for pts, close in ICONS[name]:
        mapped = [(cx + (x - 10) * k, cy + (y - 10) * k) for x, y in pts]
        pen.poly(mapped, stroke=color, lw=1.5, close=close)


# ---------------------------------------------------------------- assets
def asset_round(path, d, icon, accent_ring=False):
    pen = Pen(path, d, d)
    r = d / 2
    pen.circle(r, r, r, fill=col(STYLE["button"]))
    if accent_ring:
        pen.circle(r, r, r, stroke=col(STYLE["accent"]), lw=1.5)
        color = col(STYLE["accent"])
    else:
        pen.circle(r, r, r, stroke=WHITE(0.10), lw=1.0)
        color = col(STYLE["icon"])
    draw_icon(pen, icon, r, r, 20 if d >= 44 else 18, color)
    pen.save()


def asset_face(path, d, letter, light):
    pen = Pen(path, d, d)
    r = d / 2
    if light:
        pen.circle(r, r, r, fill=col(STYLE["face_light"]))
        tc = col(STYLE["dark_text"])
    else:
        pen.circle(r, r, r, fill=col(STYLE["face_dark"]), stroke=WHITE(0.10))
        tc = col(STYLE["icon"])
    size = 18 if d >= 54 else 17
    pen.text(letter, r, r + size * 0.36, "SG-Bold", size, tc)
    pen.save()


def asset_pill(path, w, h, text, filled):
    pen = Pen(path, w, h)
    r = h / 2
    if filled:
        pen.fill_rrect(0, 0, w, h, r, col(STYLE["button"]), stroke=WHITE(0.10))
        size = 13 if len(text) == 1 else 11
        pen.text(text, w / 2, h / 2 + size * (0.36 if size == 13 else 0.33), "SG-Bold", size, col(STYLE["icon"]))
    else:
        pen.stroke_rrect(0, 0, w, h, r, WHITE(0.16))
        # CSS centres the text plus its trailing letter-spacing, so shift left by half of it
        pen.text(text, w / 2 - 0.75, h / 2 + 3.57, "SG-Bold", 9, col(STYLE["pill_text"]), spacing=1.5)
    pen.save()


def asset_dpad(path, size, arm):
    pen = Pen(path, size, size)
    o = (size - arm) / 2
    rr = 8 if arm >= 40 else 7
    pen.fill_rrect(o, 0, arm, size, rr, col(STYLE["dpad"]))
    pen.fill_rrect(0, o, size, arm, rr, col(STYLE["dpad"]))
    g = col(STYLE["label"])
    c = size / 2
    end = o / 2  # centre of each arm end
    t = 4 if arm >= 40 else 3.6
    pen.poly([(c, end - t * 0.75), (c + t, end + t * 0.75), (c - t, end + t * 0.75)], fill=g, close=True)
    e2 = size - end
    pen.poly([(c, e2 + t * 0.75), (c - t, e2 - t * 0.75), (c + t, e2 - t * 0.75)], fill=g, close=True)
    pen.poly([(end - t * 0.75, c), (end + t * 0.75, c - t), (end + t * 0.75, c + t)], fill=g, close=True)
    pen.poly([(e2 + t * 0.75, c), (e2 - t * 0.75, c + t), (e2 - t * 0.75, c - t)], fill=g, close=True)
    pen.save()


def asset_thumb(path, d):
    pen = Pen(path, d, d)
    r = d / 2
    pen.circle(r, r, r, fill=col(STYLE["dpad"]), stroke=WHITE(0.10))
    pen.circle(r, r, r * 19 / 29, stroke=WHITE(0.06))
    pen.save()


# ---------------------------------------------------------------- background
def draw_background(path, o):
    W, H = o["size"]
    pen = Pen(path, W, H)
    c = pen.c
    # even-odd clip: whole page minus every screen hole
    p = c.beginPath()
    p.rect(0, 0, W, H)
    for s in o["screens"]:
        x, y, w, h = s["out"]
        pen.rrect(p, x, y, w, h, s["hole_r"])
    c.clipPath(p, stroke=0, fill=0, fillMode=FILL_EVEN_ODD)

    c.linearGradient(0, H, 0, 0, (col(STYLE["body_top"]), col(STYLE["body_bottom"])), extend=True)

    # hairline frame
    pen.stroke_rrect(8, 8, W - 16, H - 16, o["frame_r"], WHITE(0.06))

    # decorative rings (d-pad ring etc.)
    for cx, cy, r in o.get("rings", []):
        pen.circle(cx, cy, r, fill=WHITE(0.015), stroke=WHITE(0.05))

    # circle pad bases (the thumb is its own asset)
    for cx, cy, r in o.get("pads", []):
        pen.circle(cx, cy, r, fill=col(STYLE["button"]), stroke=WHITE(0.10))

    # bezels, in canvas order; an open-top bezel joins the one above it, so its top edge is not stroked
    bezels = o.get("bezels") or [{"rect": s["bezel"], "r": s["bezel_r"], "open_top": False} for s in o["screens"]]
    for b in bezels:
        bx, by, bw, bh = b["rect"]
        pen.fill_rrect(bx, by, bw, bh, b["r"], col(STYLE["bezel"]))
        c.saveState()
        if b["open_top"]:
            cp = c.beginPath()
            cp.rect(bx - 1, H - (by + bh) - 1, bw + 2, bh - b["r"] + 1)
            c.clipPath(cp, stroke=0, fill=0)
        pen.stroke_rrect(bx, by, bw, bh, b["r"], WHITE(0.08))
        c.restoreState()

    # speaker slots
    for x, y in o.get("speakers", []):
        for i in range(3):
            pen.fill_rrect(x, y + i * 8, 30, 3, 1.5, Color(0, 0, 0, alpha=0.6))
    for x, y, w, h, r in o.get("slots", []):
        pen.fill_rrect(x, y, w, h, min(r, h / 2), Color(0, 0, 0, alpha=0.6))

    # power LED with a soft glow
    if "led" in o:
        cx, cy, r = o["led"]
        for k, a in ((3.0, 0.06), (2.2, 0.10), (1.6, 0.18)):
            pen.circle(cx, cy, r * k, fill=col(STYLE["accent"], a))
        pen.circle(cx, cy, r, fill=col(STYLE["accent"]))

    # labels: (text, centre x or left x, top y, align)
    for lab in o.get("labels", []):
        text, x, top = lab[:3]
        align = lab[3] if len(lab) > 3 else "center"
        pen.text(text, x, top + 8.83, "SG-Medium", 9, col(STYLE["label"]), spacing=2,
                 align=align, x=x)

    # calibration ruler: a line every `step` pt from y0 to y1, labelled with its y value
    if "ruler" in o:
        y0, y1, step = o["ruler"]
        for i, y in enumerate(range(y0, y1, step)):
            if i % 2 == 0:
                pen.fill_rrect(0, y, W, step, 0, WHITE(0.05))
        for y in range(y0, y1 + 1, step):
            major = y % 50 == 0
            c.setStrokeColor(col(STYLE["accent"]) if major else WHITE(0.75))
            c.setLineWidth(2 if major else 1)
            c.line(0, H - y, W, H - y)
            tc = col(STYLE["accent"]) if major else col(STYLE["icon"])
            pen.text(str(y), 0, y - 1.5, "SG-Bold", 8, tc, align="left", x=14)
            pen.text(str(y), W / 2, y - 1.5, "SG-Bold", 8, tc)
            sw = pdfmetrics.stringWidth(str(y), "SG-Bold", 8)
            pen.text(str(y), 0, y - 1.5, "SG-Bold", 8, tc, align="left", x=W - 14 - sw)

    # free text lines: (text, centre x, baseline, size, colour key)
    for text, cx, base, size, key in o.get("notes", []):
        pen.text(text, cx, base, "SG-Medium", size, col(STYLE[key]))
    pen.save()


# ---------------------------------------------------------------- items
def frame(x, y, w, h):
    return {"x": x, "y": y, "width": w, "height": h}


def edges(n):
    return {"top": n, "bottom": n, "left": n, "right": n}


def default_ext(it, landscape):
    kind, w = it["kind"], it["frame"][2]
    return {"round": 10 if w >= 44 else 6, "face": 4, "pill": 8 if landscape else 5,
            "dpad": 12, "thumb": 10, "touch": 0}[kind]


def rect_gap(a, b):
    dx = max(b[0] - (a[0] + a[2]), a[0] - (b[0] + b[2]), 0)
    dy = max(b[1] - (a[1] + a[3]), a[1] - (b[1] + b[3]), 0)
    return max(dx, dy) if dx == 0 or dy == 0 else (dx * dx + dy * dy) ** 0.5


# measured as circles for hit-area spacing; a d-pad's frame corners are empty, so it counts as one too
CIRCLES = ("round", "face", "thumb", "dpad")


def shape_gap(a, b):
    """Gap between two hit shapes: (kind, frame); round buttons are measured as circles."""
    (ka, fa), (kb, fb) = a, b
    ca, cb = ka in CIRCLES, kb in CIRCLES
    if not ca and not cb:
        return rect_gap(fa, fb)
    if ca and cb:
        ax, ay, ar = fa[0] + fa[2] / 2, fa[1] + fa[3] / 2, fa[2] / 2
        bx, by, br = fb[0] + fb[2] / 2, fb[1] + fb[3] / 2, fb[2] / 2
        return max(((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5 - ar - br, 0)
    (fc, fr) = (fa, fb) if ca else (fb, fa)
    cx, cy, r = fc[0] + fc[2] / 2, fc[1] + fc[3] / 2, fc[2] / 2
    dx = max(fr[0] - cx, 0, cx - (fr[0] + fr[2]))
    dy = max(fr[1] - cy, 0, cy - (fr[1] + fr[3]))
    return max((dx * dx + dy * dy) ** 0.5 - r, 0)


def fit_ext(o):
    """Extended edges per item: the default, capped at half the gap to any other item or
    screen so neighbouring hit areas never overlap, and kept above a Taco safe line."""
    landscape = o["size"][0] > o["size"][1]
    others = [(it["kind"], it["frame"]) for it in o["items"]] + [("screen", s["out"]) for s in o["screens"]]
    safe = o.get("safe_y")
    for it in o["items"]:
        if "ext" in it:
            continue
        n = default_ext(it, landscape)
        for other in others:
            if other[1] == it["frame"]:
                continue
            n = min(n, int(shape_gap((it["kind"], it["frame"]), other) // 2))
        if safe is not None:
            n = min(n, int(safe - (it["frame"][1] + it["frame"][3])))
        it["ext"] = max(n, 0)


def build_assets(o, workdir, prefix):
    """Draw each item's asset; returns list of (item_json, asset_file or None)."""
    fit_ext(o)
    out = []
    for it in o["items"]:
        kind = it["kind"]
        x, y, w, h = it["frame"]
        name = None
        if kind != "touch":
            name = f"{prefix}_{it['id']}.pdf"
            p = os.path.join(workdir, name)
            if kind == "round":
                asset_round(p, w, it["icon"], it.get("accent", False))
            elif kind == "face":
                asset_face(p, w, it["letter"], it["light"])
            elif kind == "pill":
                asset_pill(p, w, h, it["text"], it["filled"])
            elif kind == "dpad":
                asset_dpad(p, w, it["arm"])
            elif kind == "thumb":
                asset_thumb(p, it["thumb"][2])
        j = {"inputs": it["inputs"], "frame": frame(x, y, w, h), "extendedEdges": edges(it["ext"])}
        if kind == "thumb":
            tw, th = it["thumb"][2], it["thumb"][3]
            j["thumbstick"] = {"name": name, "width": tw, "height": th}
        elif name:
            j["asset"] = {"normal": name}
        out.append((j, name))
    return out


def orientation_json(o, bg_name, items):
    return {
        "assets": {"resizable": bg_name},
        "items": [j for j, _ in items],
        "screens": [{"inputFrame": frame(*s["in"]), "outputFrame": frame(*s["out"])} for s in o["screens"]],
        "mappingSize": {"width": o["size"][0], "height": o["size"][1]},
        "extendedEdges": edges(0),
        "translucent": False,
    }


# ---------------------------------------------------------------- preview
def render_pdf(pdf, scale, out_png):
    base = out_png[:-4]
    subprocess.run(["pdftocairo", "-png", "-transp", "-singlefile", "-r", str(72 * scale), pdf, base], check=True)
    return Image.open(out_png).convert("RGBA")


def render_preview(o, workdir, bg_name, items, out_png, scale=3, debug=False):
    W, H = o["size"]
    img = Image.new("RGBA", (W * scale, H * scale), (0, 0, 0, 255))
    d = ImageDraw.Draw(img)
    placeholder = [(110, 140, 140, 255), (140, 110, 150, 255)]
    for i, s in enumerate(o["screens"]):
        x, y, w, h = s["out"]
        d.rectangle([x * scale, y * scale, (x + w) * scale - 1, (y + h) * scale - 1], fill=placeholder[i % 2])
    tmp = tempfile.mkdtemp()
    bg = render_pdf(os.path.join(workdir, bg_name), scale, os.path.join(tmp, "bg.png"))
    img.alpha_composite(bg)
    for j, name in items:
        if not name:
            continue
        a = render_pdf(os.path.join(workdir, name), scale, os.path.join(tmp, name + ".png"))
        f = j["frame"]
        x, y = f["x"], f["y"]
        if "thumbstick" in j:  # the thumb sits centred in its frame
            x += (f["width"] - j["thumbstick"]["width"]) / 2
            y += (f["height"] - j["thumbstick"]["height"]) / 2
        img.alpha_composite(a, (round(x * scale), round(y * scale)))
    if debug:
        ov = Image.new("RGBA", img.size, (0, 0, 0, 0))
        od = ImageDraw.Draw(ov)
        for j, _ in items:
            f, e = j["frame"], j["extendedEdges"]
            od.rectangle([(f["x"] - e["left"]) * scale, (f["y"] - e["top"]) * scale,
                          (f["x"] + f["width"] + e["right"]) * scale, (f["y"] + f["height"] + e["bottom"]) * scale],
                         fill=(255, 0, 0, 60), outline=(255, 0, 0, 220), width=2)
        img.alpha_composite(ov)
    shutil.rmtree(tmp)
    img.convert("RGB").save(out_png)
    return out_png


# ---------------------------------------------------------------- skin build
def build_skin(skin):
    os.makedirs(DIST, exist_ok=True)
    results = []
    for debug in ((True,) if skin.get("debug_only") else (False, True)):
        work = tempfile.mkdtemp()
        reps = {}
        previews = []
        for orient, o in skin["orientations"].items():
            bg = f"{orient}_bg.pdf"
            draw_background(os.path.join(work, bg), o)
            items = build_assets(o, work, orient)
            reps[orient] = orientation_json(o, bg, items)
            if not debug:
                png = os.path.join(DIST, f"{skin['file']}-{orient}.png")
                previews.append(render_preview(o, work, bg, items, png))
            else:
                png = os.path.join(DIST, f"{skin['file']}-{orient}-debug.png")
                previews.append(render_preview(o, work, bg, items, png, debug=True))
        tag = debug and not skin.get("debug_only")
        ident = skin["identifier"] + (".debug" if tag else "") + "." + skin["version"]
        info = {
            "name": skin["name"] + (" (Debug)" if tag else ""),
            "identifier": ident,
            "gameTypeIdentifier": skin["gameType"],
            "debug": debug,
            "representations": {"iphone": {"edgeToEdge": reps}},
        }
        with open(os.path.join(work, "info.json"), "w") as fh:
            json.dump(info, fh, indent=2)
        out = os.path.join(DIST, skin["file"] + ("-Debug" if tag else "") + ".manicskin")
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for f in sorted(os.listdir(work)):
                z.write(os.path.join(work, f), f)
        shutil.rmtree(work)
        results.append((out, ident, previews))
    return results


# ---------------------------------------------------------------- layouts
def ds_portrait():
    return {
        "size": (414, 896),
        "frame_r": 50,
        "screens": [
            {"in": DS_TOP, "out": (15, 54, 384, 288), "bezel": (7, 46, 400, 304), "bezel_r": 18, "hole_r": 8},
            {"in": DS_BOTTOM, "out": (47, 352, 320, 240), "bezel": (39, 344, 336, 254), "bezel_r": 16, "hole_r": 6},
        ],
        "rings": [(100, 730, 80)],
        "speakers": [(34, 848), (350, 848)],
        "led": (27.5, 27.5, 3.5),
        "labels": [("PWR", 37, 21, "left"), ("SAVE", 145, 643), ("FAST", 203, 643), ("MENU", 261, 643)],
        "items": [
            {"id": "l", "kind": "pill", "frame": (16, 604, 80, 30), "text": "L", "filled": True, "inputs": ["l"]},
            {"id": "r", "kind": "pill", "frame": (318, 604, 80, 30), "text": "R", "filled": True, "inputs": ["r"]},
            {"id": "save", "kind": "round", "frame": (127, 601, 36, 36), "icon": "save", "inputs": ["quickSave"], "ext": 6},
            {"id": "fast", "kind": "round", "frame": (185, 601, 36, 36), "icon": "fast", "inputs": ["toggleFastForward"], "ext": 6},
            {"id": "menu", "kind": "round", "frame": (243, 601, 36, 36), "icon": "menu", "inputs": ["menu"], "ext": 6},
            {"id": "dpad", "kind": "dpad", "frame": (30, 660, 140, 140), "arm": 46, "ext": 12,
             "inputs": {"up": "up", "down": "down", "left": "left", "right": "right"}},
            {"id": "x", "kind": "face", "frame": (295, 648, 54, 54), "letter": "X", "light": False, "inputs": ["x"], "ext": 4},
            {"id": "y", "kind": "face", "frame": (240, 703, 54, 54), "letter": "Y", "light": False, "inputs": ["y"], "ext": 4},
            {"id": "a", "kind": "face", "frame": (350, 703, 54, 54), "letter": "A", "light": True, "inputs": ["a"], "ext": 4},
            {"id": "b", "kind": "face", "frame": (295, 758, 54, 54), "letter": "B", "light": True, "inputs": ["b"], "ext": 4},
            {"id": "select", "kind": "pill", "frame": (177, 790, 60, 24), "text": "SELECT", "filled": False, "inputs": ["select"], "ext": 5},
            {"id": "start", "kind": "pill", "frame": (177, 824, 60, 24), "text": "START", "filled": False, "inputs": ["start"], "ext": 5},
            {"id": "touch", "kind": "touch", "frame": (47, 352, 320, 240),
             "inputs": {"x": "touchScreenX", "y": "touchScreenY"}},
        ],
    }


def ds_landscape():
    return {
        "size": (896, 414),
        "frame_r": 52,
        "screens": [
            {"in": DS_TOP, "out": (222, 21, 452, 373), "bezel": (216, 15, 464, 385), "bezel_r": 16, "hole_r": 8},
            {"in": DS_BOTTOM, "out": (698, 144, 168, 126), "bezel": (691, 137, 182, 140), "bezel_r": 14, "hole_r": 6},
        ],
        "led": (128, 303, 4),
        "labels": [("MENU", 128, 102), ("FAST", 782, 102), ("PWR", 128, 313)],
        "items": [
            {"id": "menu", "kind": "round", "frame": (106, 50, 44, 44), "icon": "menu", "inputs": ["menu"], "ext": 10},
            {"id": "fast", "kind": "round", "frame": (760, 50, 44, 44), "icon": "fast", "inputs": ["toggleFastForward"], "ext": 10},
            {"id": "swap", "kind": "round", "frame": (760, 294, 44, 44), "icon": "swap", "accent": True,
             "inputs": ["reverseScreens"], "ext": 8},
            {"id": "select", "kind": "pill", "frame": (96, 352, 64, 26), "text": "SELECT", "filled": False, "inputs": ["select"], "ext": 8},
            {"id": "start", "kind": "pill", "frame": (750, 352, 64, 26), "text": "START", "filled": False, "inputs": ["start"], "ext": 8},
            {"id": "touch", "kind": "touch", "frame": (698, 144, 168, 126),
             "inputs": {"x": "touchScreenX", "y": "touchScreenY"}},
        ],
    }


def taco_ruler_portrait():
    """Calibration layout: both screens and Menu sit above y 400; the ruler fills y 400 to 560."""
    return {
        "size": (414, 896),
        "frame_r": 50,
        "screens": [
            {"in": DS_TOP, "out": (79, 50, 256, 192), "bezel": (73, 44, 268, 204), "bezel_r": 14, "hole_r": 6},
            {"in": DS_BOTTOM, "out": (127, 258, 160, 120), "bezel": (121, 252, 172, 132), "bezel_r": 12, "hole_r": 5},
        ],
        "led": (27.5, 27.5, 3.5),
        "labels": [("PWR", 37, 21, "left"), ("MENU", 58, 340)],
        "ruler": (400, 560, 10),
        "notes": [
            ("TACO RULER", 207, 600, 13, "icon"),
            ("Clip the Pocket Taco on, then note the last", 207, 624, 10, "label"),
            ("line you can fully see. Lines every 10 pt;", 207, 639, 10, "label"),
            ("red lines every 50 pt.", 207, 654, 10, "label"),
        ],
        "items": [
            {"id": "menu", "kind": "round", "frame": (40, 298, 36, 36), "icon": "menu", "inputs": ["menu"], "ext": 6},
            {"id": "touch", "kind": "touch", "frame": (127, 258, 160, 120),
             "inputs": {"x": "touchScreenX", "y": "touchScreenY"}},
        ],
    }


SKINS = {
    "demo": {
        "name": "Arctic Demo DS",
        "file": "Arctic-Demo-DS",
        "identifier": "com.liamdouglas.manic.arcticdemo.ds",
        "version": "v1",
        "gameType": "public.aoshuang.game.ds",
        "orientations": {"portrait": ds_portrait(), "landscape": ds_landscape()},
    },
    "ruler": {
        "name": "Taco Ruler DS",
        "file": "Taco-Ruler-DS",
        "identifier": "com.liamdouglas.manic.tacoruler.ds",
        "version": "v1",
        "gameType": "public.aoshuang.game.ds",
        "debug_only": True,
        "orientations": {"portrait": taco_ruler_portrait(), "landscape": ds_landscape()},
    },
}


# ---------------------------------------------------------------- skins built from the Arctic Skins canvas
CANVAS = os.path.join(ROOT, "canvas", "project")
DS_FRAMES = {"top": DS_TOP, "bottom": DS_BOTTOM}
# 3DS core output: top 400x240 above bottom 320x240, centred. NOT confirmed on device yet.
N3DS_FRAMES = {"top": (0, 0, 400, 240), "bottom": (40, 240, 320, 240)}


def from_canvas(fname, frames, safe_y=None):
    with open(os.path.join(CANVAS, fname)) as fh:
        o = parse_artboard(fh.read(), frames)
    if safe_y is not None:
        o["safe_y"] = safe_y
    o["canvas_file"] = fname
    return o


def canvas_skins():
    ds, n3 = "public.aoshuang.game.ds", "public.aoshuang.game.3ds"
    base = "com.liamdouglas.manic."
    return {
        "modern-ds": {"name": "Arctic Modern DS", "file": "Arctic-Modern-DS", "identifier": base + "arcticmodern.ds",
                      "version": "v1", "gameType": ds,
                      "orientations": {"portrait": from_canvas("Main.dc.html", DS_FRAMES),
                                       "landscape": from_canvas("DS-landscape.dc.html", DS_FRAMES)}},
        "taco-ds": {"name": "Arctic Taco DS", "file": "Arctic-Taco-DS", "identifier": base + "arctictaco.ds",
                    "version": "v1", "gameType": ds,
                    "orientations": {"portrait": from_canvas("DS-taco.dc.html", DS_FRAMES, TACO_SAFE_Y),
                                     "landscape": from_canvas("DS-landscape.dc.html", DS_FRAMES)}},
        "modern-3ds": {"name": "Arctic Modern 3DS", "file": "Arctic-Modern-3DS", "identifier": base + "arcticmodern.3ds",
                       "version": "v1", "gameType": n3,
                       "orientations": {"portrait": from_canvas("3DS-portrait.dc.html", N3DS_FRAMES),
                                        "landscape": from_canvas("3DS-landscape.dc.html", N3DS_FRAMES)}},
        "taco-3ds": {"name": "Arctic Taco 3DS", "file": "Arctic-Taco-3DS", "identifier": base + "arctictaco.3ds",
                     "version": "v1", "gameType": n3,
                     "orientations": {"portrait": from_canvas("3DS-taco.dc.html", N3DS_FRAMES, TACO_SAFE_Y),
                                      "landscape": from_canvas("3DS-landscape.dc.html", N3DS_FRAMES)}},
    }


SKINS.update(canvas_skins())


def check(names):
    ok = True
    for n in names:
        for orient, o in SKINS[n]["orientations"].items():
            if "canvas_file" not in o:
                continue
            label = f"{SKINS[n]['name']} {orient}"
            problems, notes = check_rules(o, label, o.get("safe_y"))
            for p in problems:
                print("RULE BROKEN:", p)
                ok = False
            for p in notes:
                print("note:", p)
            for p in o["ignored"]:
                print(f"ignored ({label}):", p)
    return ok


if __name__ == "__main__":
    names = sys.argv[1:] or list(SKINS)
    if not check(names):
        sys.exit("hard rules broken; not building")
    for n in names:
        for out, ident, previews in build_skin(SKINS[n]):
            print(out, ident)
            for p in previews:
                print("  ", p)
