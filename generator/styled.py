"""Read styled canvas artboards (data-role markup) into a drawable skin layout.

Unlike artboard.py, which knows the Arctic colours, this takes every colour, border,
shadow, font and icon from the artboard itself, so any look on the canvas can be built.

Roles: frame, led, label, bezel, screen (data-screen top|bottom, data-touch), filter
(class "mesh"), ring, pad, dpad, slot, annotation (design notes, never built). Buttons are
<button aria-label="..."> elements, mapped to Manic inputs by artboard.BUTTONS.
"""
import json
import re
from html.parser import HTMLParser

from artboard import BUTTONS, DPAD_LABELS, parse_style, px

CIRCLE_PAD = "Circle pad"
THUMB_INPUTS = {"up": "leftThumbstickUp", "down": "leftThumbstickDown",
                "left": "leftThumbstickLeft", "right": "leftThumbstickRight"}


# ---------------------------------------------------------------- template values
def template_values(html):
    """Defaults for {{holes}}, read from the artboard's own tweak declarations."""
    m = re.search(r"data-props='([^']*)'", html)
    props = json.loads(m.group(1).replace("&#39;", "'").replace("&amp;", "&")) if m else {}
    vals = {}
    if "accent" in props:
        vals["accent"] = props["accent"]["default"]
    bodies = dict((k, (a, b)) for k, a, b in re.findall(r"'([^']+)':\s*\['(#[0-9a-fA-F]+)',\s*'(#[0-9a-fA-F]+)'\]", html))
    body = props.get("body", {}).get("default")
    if body in bodies:
        vals["bodyTop"], vals["bodyBottom"] = bodies[body]
    if "filter" in props:
        vals["mesh"] = "" if props["filter"]["default"] == "none" else "mesh"
    return vals


def fill_holes(s, vals):
    return re.sub(r"\{\{\s*(\w+)\s*\}\}", lambda m: vals.get(m.group(1), ""), s)


# ---------------------------------------------------------------- css values
def color(s):
    """CSS colour -> (r, g, b, a) floats, or None for none/transparent."""
    s = (s or "").strip()
    if not s or s in ("none", "transparent"):
        return None
    if s.startswith("#"):
        h = s[1:]
        if len(h) == 3:
            h = "".join(ch * 2 for ch in h)
        return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)) + (1.0,)
    m = re.match(r"rgba?\(([^)]*)\)", s)
    if m:
        parts = [float(p) for p in m.group(1).split(",")]
        return (parts[0] / 255, parts[1] / 255, parts[2] / 255, parts[3] if len(parts) > 3 else 1.0)
    raise ValueError(f"unsupported colour {s!r}")


def find_color(s):
    m = re.search(r"(#[0-9a-fA-F]{3,6}|rgba?\([^)]*\))", s or "")
    return color(m.group(1)) if m else None


def border(st):
    b = st.get("border", "0").strip()
    if b in ("0", "none", ""):
        return None
    w = float(re.match(r"([\d.]+)px", b).group(1))
    return (w, find_color(b))


def shadows(st):
    """box-shadow -> dict with optional inset (width, colour), drop (dy, colour), glow (blur, colour)."""
    out = {}
    s = st.get("box-shadow", "")
    if not s:
        return out
    col = find_color(s)
    nums = [float(n) for n in re.findall(r"-?[\d.]+", re.sub(r"(rgba?\([^)]*\)|#[0-9a-fA-F]+)", "", s))]
    if s.startswith("inset"):
        out["inset"] = (nums[3], col)
    elif len(nums) >= 3 and nums[2] == 0 and nums[1] > 0:
        out["drop"] = (nums[1], col)
    elif len(nums) >= 3 and nums[0] == 0 and nums[1] == 0 and nums[2] > 0:
        out["glow"] = (nums[2], col)
    return out


def radius(st, w, h):
    r = st.get("border-radius", "0")
    return min(w, h) / 2 if r.endswith("%") else px(r)


def rect(st):
    return (px(st.get("left")), px(st.get("top")), px(st.get("width")), px(st.get("height")))


def font_spec(st, family):
    return {"family": family, "size": px(st.get("font-size", "12px")),
            "weight": int(st.get("font-weight", "400")), "ls": px(st.get("letter-spacing", "0")),
            "color": color(st.get("color", "#000"))}


# ---------------------------------------------------------------- html
class _Tree(HTMLParser):
    VOID = {"meta", "link", "br", "img", "input"}

    def __init__(self):
        super().__init__()
        self.root = {"tag": "#root", "attrs": {}, "children": [], "text": ""}
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        el = {"tag": tag, "attrs": dict(attrs), "children": [], "text": ""}
        self.stack[-1]["children"].append(el)
        if tag not in self.VOID:
            self.stack.append(el)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i]["tag"] == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        for el in self.stack:
            el["text"] += data


def walk(el):
    for ch in el["children"]:
        yield ch
        yield from walk(ch)


def svg_spec(svg, current):
    a = svg["attrs"]
    vb = [float(v) for v in a.get("viewbox", "0 0 20 20").split()]  # the parser lowercases attribute names
    stroke = a.get("stroke")
    out = {"size": (float(a.get("width", vb[2])), float(a.get("height", vb[3]))), "box": (vb[2], vb[3]),
           "stroke": current if stroke == "currentColor" else color(stroke) if stroke else None,
           "width": float(a.get("stroke-width", "1")), "cap": a.get("stroke-linecap", "butt"),
           "join": a.get("stroke-linejoin", "miter"), "paths": []}
    for p in walk(svg):
        if p["tag"] == "path":
            fill = p["attrs"].get("fill", a.get("fill", "#000"))
            out["paths"].append({"d": p["attrs"]["d"],
                                 "fill": current if fill == "currentColor" else color(fill) if fill != "none" else None})
    return out


def parse_path(d):
    """SVG path data (M L H V Z, absolute and relative) -> list of (points, closed)."""
    toks = re.findall(r"[MmLlHhVvZz]|-?\d*\.?\d+(?:e-?\d+)?", d)
    subs, pts, cmd, i = [], [], None, 0
    x = y = sx = sy = 0.0

    def num():
        nonlocal i
        v = float(toks[i])
        i += 1
        return v

    while i < len(toks):
        t = toks[i]
        if t.isalpha():
            cmd = t
            i += 1
            if cmd in "Zz":
                if pts:
                    subs.append((pts, True))
                pts, x, y = [], sx, sy
                continue
        if cmd in "Mm":
            nx, ny = num(), num()
            x, y = (x + nx, y + ny) if cmd == "m" else (nx, ny)
            if pts:
                subs.append((pts, False))
            pts, sx, sy = [(x, y)], x, y
            cmd = "l" if cmd == "m" else "L"
        elif cmd in "Ll":
            nx, ny = num(), num()
            x, y = (x + nx, y + ny) if cmd == "l" else (nx, ny)
            pts.append((x, y))
        elif cmd in "Hh":
            v = num()
            x = x + v if cmd == "h" else v
            pts.append((x, y))
        elif cmd in "Vv":
            v = num()
            y = y + v if cmd == "v" else v
            pts.append((x, y))
        else:
            raise ValueError(f"unsupported path command {cmd!r} in {d!r}")
    if pts:
        subs.append((pts, False))
    return subs


def parse(html, input_frames):
    vals = template_values(html)
    tree = _Tree()
    tree.feed(fill_holes(html, vals))
    root = next(el for el in walk(tree.root)
                if parse_style(el["attrs"].get("style", "")).get("position") == "relative")
    rs = parse_style(root["attrs"]["style"])
    W, H = int(px(rs["width"])), int(px(rs["height"]))
    grad = re.findall(r"(#[0-9a-fA-F]{6})", rs.get("background", ""))
    family = re.match(r"\s*'?([^',]+)", rs.get("font-family", "Space Grotesk")).group(1)
    o = {"styled": True, "size": (W, H), "vals": vals, "family": family,
         "body": (color(grad[0]), color(grad[-1])), "screens": [], "bezels": [], "bg": [],
         "filters": [], "items": [], "ignored": []}
    arms, dpad_buttons, pads = [], [], []

    for el in root["children"]:
        a, st = el["attrs"], parse_style(el["attrs"].get("style", ""))
        if st.get("position") != "absolute":
            continue
        role = a.get("data-role")
        x, y, w, h = rect(st)
        r = radius(st, w, h)
        bg = color(st.get("background")) if "gradient" not in st.get("background", "") else None
        if el["tag"] == "button":
            label = a.get("aria-label", "")
            cur = color(st.get("color", "#000"))
            svgs = [svg_spec(s, cur) for s in walk(el) if s["tag"] == "svg"]
            if label in DPAD_LABELS:
                dpad_buttons.append(((x, y, w, h), svgs))
                continue
            sh = shadows(st)
            if "drop" in sh:  # a hard drop shadow stays put in the background; the button art moves
                o["bg"].append({"t": "rrect", "rect": (x, y + sh["drop"][0], w, h), "r": r, "fill": sh["drop"][1]})
            style = {"fill": bg, "border": border(st), "r": r, "svgs": svgs,
                     "text": " ".join(el["text"].split()), "font": font_spec(st, family)}
            if label == CIRCLE_PAD:
                span = next((s for s in walk(el) if s["tag"] == "span"), None)
                if span:
                    ss = parse_style(span["attrs"].get("style", ""))
                    style["ring"] = (px(ss.get("width")), border(ss))
                o["items"].append({"id": "circlepad", "kind": "thumb", "thumb": (x, y, w, h),
                                   "inputs": THUMB_INPUTS, "style": style})
                continue
            if label not in BUTTONS:
                o["ignored"].append(f"button '{label}'")
                continue
            inputs, _ = BUTTONS[label]
            circle = st.get("border-radius", "").endswith("%")
            kind = ("round" if svgs else "face") if circle else "pill"
            o["items"].append({"id": inputs[0], "kind": kind, "frame": (x, y, w, h), "inputs": inputs, "style": style})
            continue
        if role == "screen":
            which = a.get("data-screen")
            o["screens"].append({"which": which, "in": input_frames[which], "out": (x, y, w, h), "hole_r": r,
                                 "touch": a.get("data-touch") == "true"})
        elif role == "filter":
            if "mesh" in a.get("class", "").split():
                o["filters"].append({"rect": (x, y, w, h), "r": r, "kind": "r3bel"})
        elif role == "bezel":
            sh = shadows(st)
            o["bezels"].append({"rect": (x, y, w, h), "r": r})
            o["bg"].append({"t": "rrect", "rect": (x, y, w, h), "r": r, "fill": bg, "border": border(st),
                            "inset": sh.get("inset"), "open_top": "border-top-color: transparent" in a.get("style", "")})
        elif role in ("frame", "ring", "slot"):
            o["bg"].append({"t": "rrect", "rect": (x, y, w, h), "r": r, "fill": bg, "border": border(st)})
        elif role == "pad":
            pads.append((x, y, w, h))
            o["bg"].append({"t": "rrect", "rect": (x, y, w, h), "r": r, "fill": bg, "border": border(st)})
        elif role == "led":
            glow = shadows(st).get("glow")
            if glow:
                o["bg"].append({"t": "glow", "c": (x + w / 2, y + h / 2), "r": w / 2, "color": glow[1]})
            o["bg"].append({"t": "rrect", "rect": (x, y, w, h), "r": r, "fill": bg})
        elif role == "label":
            o["bg"].append({"t": "text", "text": " ".join(el["text"].split()), "left": x, "top": y,
                            "width": w if "width" in st else None, "pad": px(st.get("padding-left", "0")),
                            "font": font_spec(st, family)})
        elif role == "dpad":
            arms.append(((x, y, w, h), r, bg))
        elif role == "annotation":
            continue
        else:
            o["ignored"].append(f"{el['tag']} role={role} at ({x:g},{y:g},{w:g},{h:g})")

    if arms:
        xs = [r[0][0] for r in arms] + [r[0][0] + r[0][2] for r in arms]
        ys = [r[0][1] for r in arms] + [r[0][1] + r[0][3] for r in arms]
        fx, fy = min(xs), min(ys)
        o["items"].append({"id": "dpad", "kind": "dpad", "frame": (fx, fy, max(xs) - fx, max(ys) - fy),
                           "inputs": {"up": "up", "down": "down", "left": "left", "right": "right"},
                           "arms": [((ax - fx, ay - fy, aw, ah), ar, af) for (ax, ay, aw, ah), ar, af in arms],
                           "arrows": [((bx - fx, by - fy, bw, bh), svgs) for (bx, by, bw, bh), svgs in dpad_buttons]})
    for it in o["items"]:
        if it["kind"] == "thumb":
            tx, ty, tw, th = it["thumb"]
            base = min(pads, key=lambda p: (p[0] + p[2] / 2 - tx - tw / 2) ** 2 + (p[1] + p[3] / 2 - ty - th / 2) ** 2)
            it["frame"] = base
    for s in o["screens"]:
        if s["touch"]:
            o["items"].append({"id": "touch", "kind": "touch", "frame": s["out"],
                               "inputs": {"x": "touchScreenX", "y": "touchScreenY"}})
    o["screens"].sort(key=lambda s: 0 if s["which"] == "top" else 1)
    return o
