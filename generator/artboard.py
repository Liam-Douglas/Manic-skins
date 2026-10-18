"""Read Arctic Skins canvas artboards (.dc.html) into skin layouts.

The canvas is the source of truth: every screen, bezel, button, label and
decoration is taken from the artboard's absolutely positioned elements.
"""
import re
from html.parser import HTMLParser

ACCENT = "{{accent}}"

# aria-label -> (Manic inputs, icon name or None)
BUTTONS = {
    "Menu": (["menu"], "menu"),
    "Fast forward": (["toggleFastForward"], "fast"),
    "Swap screens": (["reverseScreens"], "swap"),
    "Quick save": (["quickSave"], "save"),
    "Home": (["homeMenu"], "home"),
    "L": (["l"], None), "R": (["r"], None), "ZL": (["zl"], None), "ZR": (["zr"], None),
    "X": (["x"], None), "Y": (["y"], None), "A": (["a"], None), "B": (["b"], None),
    "Select": (["select"], None), "Start": (["start"], None),
}
DPAD_LABELS = {"D-pad up", "D-pad down", "D-pad left", "D-pad right"}


def parse_style(s):
    out = {}
    for part in s.split(";"):
        if ":" in part:
            k, v = part.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def px(v):
    return float(v.replace("px", "")) if v else 0.0


class _Parser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []
        self.elements = []
        self.root_size = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        el = {"tag": tag, "attrs": a, "style": parse_style(a.get("style", "")), "text": "",
              "parent": self.stack[-1] if self.stack else None, "children": []}
        if el["parent"] is not None:
            el["parent"]["children"].append(el)
        st = el["style"]
        if st.get("position") == "relative" and self.root_size is None:
            self.root_size = (int(px(st["width"])), int(px(st["height"])))
        if st.get("position") == "absolute":
            self.elements.append(el)
        if tag not in ("meta", "link", "br", "img", "input", "path"):
            self.stack.append(el)

    def handle_endtag(self, tag):
        if tag == "path":
            return
        while self.stack:
            el = self.stack.pop()
            if el["tag"] == tag:
                break

    def handle_data(self, data):
        if self.stack:
            for el in self.stack:
                el["text"] += data


def rect(el):
    s = el["style"]
    return (px(s.get("left")), px(s.get("top")), px(s.get("width")), px(s.get("height")))


def radius(el, w):
    r = el["style"].get("border-radius", "0")
    return w / 2 if r.endswith("%") else px(r)


def contains(outer, inner):
    ox, oy, ow, oh = outer
    ix, iy, iw, ih = inner
    return ox <= ix and oy <= iy and ox + ow >= ix + iw and oy + oh >= iy + ih


def parse_artboard(html, input_frames):
    """input_frames: {"top": (x,y,w,h), "bottom": (x,y,w,h)} in core pixels."""
    p = _Parser()
    p.feed(html)
    W, H = p.root_size
    o = {"size": (W, H), "frame_r": 50, "screens": [], "bezels": [], "rings": [], "pads": [],
         "slots": [], "labels": [], "items": [], "ignored": []}
    dpad_buttons, dpad_arms = [], []

    for el in p.elements:
        st, a = el["style"], el["attrs"]
        bg = st.get("background", "")
        x, y, w, h = rect(el)
        text = " ".join(el["text"].split())
        if el["tag"] == "button":
            label = a.get("aria-label", "")
            if label in DPAD_LABELS:
                dpad_buttons.append((x, y, w, h))
                continue
            if label == "Circle pad":
                o["items"].append({"id": "circlepad", "kind": "thumb", "thumb": (x, y, w, h),
                                   "inputs": {"up": "leftThumbstickUp", "down": "leftThumbstickDown",
                                              "left": "leftThumbstickLeft", "right": "leftThumbstickRight"}})
                continue
            if label not in BUTTONS:
                o["ignored"].append(f"button '{label}'")
                continue
            inputs, icon = BUTTONS[label]
            item = {"id": inputs[0], "frame": (x, y, w, h), "inputs": inputs}
            if icon:
                item.update(kind="round", icon=icon, accent=ACCENT in st.get("border", ""))
            elif st.get("border-radius", "").endswith("%"):
                item.update(kind="face", letter=label, light="#d7dade" in bg)
            else:
                item.update(kind="pill", text=text, filled="#1f2124" in bg)
            o["items"].append(item)
            continue
        if el["tag"] != "div":
            continue
        # a child div inside a screen placeholder is the screen's caption
        par = el["parent"]
        if par is not None and par in p.elements and "#6e8c8c" in par["style"].get("background", ""):
            continue
        if "#6e8c8c" in bg:
            which = "top" if "TOP" in text.upper() else "bottom"
            o["screens"].append({"which": which, "in": input_frames[which], "out": (x, y, w, h),
                                 "hole_r": radius(el, w), "touch": "TOUCH" in text.upper()})
        elif "#050506" in bg:
            o["bezels"].append({"rect": (x, y, w, h), "r": radius(el, w),
                                "open_top": "border-top-color: transparent" in el["attrs"].get("style", "")})
        elif ACCENT in bg:
            o["led"] = (x + w / 2, y + h / 2, w / 2)
        elif "rgba(255,255,255,0.015)" in bg:
            o["rings"].append((x + w / 2, y + h / 2, w / 2))
        elif "#1f2124" in bg and st.get("border-radius", "").endswith("%"):
            o["pads"].append((x + w / 2, y + h / 2, w / 2))
        elif "#26282c" in bg:
            dpad_arms.append((x, y, w, h))
        elif "rgba(0,0,0,0.6)" in bg:
            o["slots"].append((x, y, w, h, px(st.get("border-radius"))))
        elif not bg and "rgba(255,255,255,0.06)" in st.get("border", ""):
            o["frame_r"] = px(st.get("border-radius"))
        elif st.get("color") == "#7d848b" and text:
            if "width" in st:
                o["labels"].append((text, x + w / 2, y))
            else:
                o["labels"].append((text, x, y, "left"))
        else:
            o["ignored"].append(f"div at ({x:g},{y:g},{w:g},{h:g}) {text[:30]!r}")

    if dpad_arms:
        xs = [r[0] for r in dpad_arms] + [r[0] + r[2] for r in dpad_arms]
        ys = [r[1] for r in dpad_arms] + [r[1] + r[3] for r in dpad_arms]
        fx, fy = min(xs), min(ys)
        o["items"].append({"id": "dpad", "kind": "dpad", "frame": (fx, fy, max(xs) - fx, max(ys) - fy),
                           "arm": min(min(r[2], r[3]) for r in dpad_arms),
                           "inputs": {"up": "up", "down": "down", "left": "left", "right": "right"}})
    # circle pad: the item frame is the base circle the thumb sits in
    for it in o["items"]:
        if it["kind"] == "thumb":
            tx, ty, tw, th = it["thumb"]
            tcx, tcy = tx + tw / 2, ty + th / 2
            base = min(o["pads"], key=lambda c: (c[0] - tcx) ** 2 + (c[1] - tcy) ** 2)
            cx, cy, r = base
            it["frame"] = (cx - r, cy - r, 2 * r, 2 * r)
    for s in o["screens"]:
        if s["touch"]:
            o["items"].append({"id": "touch", "kind": "touch", "frame": s["out"],
                               "inputs": {"x": "touchScreenX", "y": "touchScreenY"}})
    # screens: top first, as Manic expects the screen list in core order
    o["screens"].sort(key=lambda s: 0 if s["which"] == "top" else 1)
    return o


def check_rules(o, name, safe_y=None, landscape_margin=48, island=47):
    """Return a list of hard-rule problems for one parsed artboard."""
    problems, notes = [], []
    W, H = o["size"]
    if len(o["screens"]) != 2 or {s["which"] for s in o["screens"]} != {"top", "bottom"}:
        problems.append(f"{name}: needs exactly one top and one bottom screen")
    screens = [s["out"] for s in o["screens"]]

    def overlaps(a, b):
        return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]

    for it in o["items"]:
        if it["kind"] == "touch":
            continue
        for sc in screens:
            if overlaps(it["frame"], sc):
                problems.append(f"{name}: {it['id']} covers a screen")
    for sc in screens:
        if W < H and sc[1] < island:
            problems.append(f"{name}: a screen starts inside the Dynamic Island zone")
        if W > H and (sc[0] < landscape_margin or sc[0] + sc[2] > W - landscape_margin):
            notes.append(f"{name}: a screen reaches into the {landscape_margin}pt side margin "
                         f"(x {sc[0]:g} to {sc[0] + sc[2]:g}); fine if the camera is on the left")
    for it in o["items"]:
        if W < H and it["frame"][1] < island:
            problems.append(f"{name}: {it['id']} is in the Dynamic Island zone")
    if safe_y is not None:
        for it in o["items"]:
            f = it["frame"]
            if f[1] + f[3] > safe_y:
                problems.append(f"{name}: {it['id']} goes below the safe line y {safe_y}")
        for sc in screens:
            if sc[1] + sc[3] > safe_y:
                problems.append(f"{name}: a screen goes below the safe line y {safe_y}")
        for b in o["bezels"]:
            r = b["rect"]
            if r[1] + r[3] > safe_y:
                problems.append(f"{name}: a bezel goes below the safe line y {safe_y}")
    return problems, notes
