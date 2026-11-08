#!/usr/bin/env python3
"""Yearly vertical wall calendar generator.

12 month columns x 31 day rows, one writable cell per day.
Writes SVG (editable), PDF (print) and PNG (preview) for each theme.

    python3 make_calendar.py                       # current year, A1, both themes
    python3 make_calendar.py --year 2027 --size A0 A1 B1
    python3 make_calendar.py --theme dark --bleed 3

PDF/PNG rendering uses headless Google Chrome (set CHROME=/path/to/chrome to override).
If Ghostscript (gs) is installed, the PDF is then converted for print: CMYK colour,
text outlined, and the page box set to the exact paper size.
"""

import argparse
import calendar as cal
import datetime as dt
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
FONTS = HERE / "fonts"

SIZES = {  # portrait, mm
    "A0": (841, 1189),
    "A1": (594, 841),
    "A2": (420, 594),
    "A3": (297, 420),
    "B1": (707, 1000),
}

THEMES = {
    # Every grey is strictly neutral (R = G = B); the accent is the only colour.
    # soft off-black: less harsh than near-black, and less ink on a large flood
    "dark": dict(
        bg="#1D1D1D", ink="#E5E5E5", muted="#818181", rule="#414141",
        weekend="#2C2C2C", hatch="#474747", accent="#F2C500",  # yellow
        accent_cmyk=(0, 0.20, 1, 0),                    # how the accent prints
    ),
    # mid-light grey, light enough for ordinary pen/pencil to read
    "grey": dict(
        bg="#D4D4D4", ink="#111111", muted="#595959", rule="#A1A1A1",
        weekend="#C4C4C4", hatch="#AAAAAA", accent="#EE4E22",  # red-orange
        accent_cmyk=(0, 0.82, 1, 0),                    # vivid process red-orange (no black)
    ),
}

MONTHS = ["jan", "feb", "mar", "apr", "may", "jun",
          "jul", "aug", "sep", "oct", "nov", "dec"]
WD = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

MONO_ADV = 0.6  # JetBrains Mono advance width, em


def mix(a, b, f):
    """Blend hex colour a toward b by f (0..1). A solid blend instead of real
    transparency, which some print RIPs handle badly."""
    ca = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    cb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * f):02X}" for x, y in zip(ca, cb))


class Layer(list):
    def text(self, x, y, t, size, c, weight=400, fill="ink", anchor="start", ls=0.0):
        ff = "Cal Mono"
        lsa = f' letter-spacing="{ls:.3f}"' if ls else ""
        t = t.replace("&", "&amp;").replace("<", "&lt;")
        self.append(f'<text x="{x:.3f}" y="{y:.3f}" font-family="{ff}" font-size="{size:.3f}" '
                    f'font-weight="{weight}" fill="{c[fill]}" text-anchor="{anchor}"{lsa}>{t}</text>')

    def line(self, x1, y1, x2, y2, w, col):
        self.append(f'<line x1="{x1:.3f}" y1="{y1:.3f}" x2="{x2:.3f}" y2="{y2:.3f}" '
                    f'stroke="{col}" stroke-width="{w:.3f}"/>')

    def rect(self, x, y, w, h, fill, stroke=None, sw=0.0):
        st = f' stroke="{stroke}" stroke-width="{sw:.3f}"' if stroke else ""
        self.append(f'<rect x="{x:.3f}" y="{y:.3f}" width="{w:.3f}" height="{h:.3f}" fill="{fill}"{st}/>')

    def poly(self, pts, fill):
        d = "M" + "L".join(f"{x:.3f} {y:.3f}" for x, y in pts) + "Z"
        self.append(f'<path d="{d}" fill="{fill}"/>')

    def week_mark(self, x, y, w, col, s):
        """Angular week-start rule on y: a thin trace across the cell, a heavy bar rising
        above it on the left with a 45° chamfer. Everything
        that is thicker than the trace grows upwards, away from the next day's labels."""
        th, T = 0.5 * s, 1.6 * s                       # trace and bar thickness
        yb, yt = y + th / 2, y + th / 2 - T            # shared bottom edge, bar top
        ch = T - th                                    # chamfer run (45°)
        xa = x + 0.42 * w                              # end of the bar's flat top
        self.poly([(x, y - th / 2), (x + w, y - th / 2), (x + w, yb), (x, yb)], col)
        self.poly([(x, yt), (xa, yt), (xa + ch, y - th / 2), (xa + ch, yb), (x, yb)], col)

    def logo(self, x, top, h, ring_col, acc_col, ink_col):
        """Hex mark: a pointy-top hexagon ring (upper-left side in the accent colour) with
        the right half of a smaller hexagon filled in the accent colour inside it. A diagonal
        glitch slices through the centre parallel to the hexagon's upper-left and
        lower-right edges; everything below the cut slides along it, and the half-hexagon's
        lower piece can take its own colour (currently the accent too).
        Left edge at x, top at `top`, total height h. Filled polygons only (print-safe)."""
        R = h / 2
        cx, cy = x + R * math.sqrt(3) / 2, top + R
        t = 0.075 * h                                   # ring thickness (measured radially)
        ri = R - t / math.cos(math.radians(30))         # inner radius giving constant side width
        ang = [math.radians(-90 + 60 * k) for k in range(7)]
        P = lambda r, a: (cx + r * math.cos(a), cy + r * math.sin(a))
        shapes = []
        for k in range(6):
            col = acc_col if k == 5 else ring_col       # upper-left side picks up the accent
            shapes.append(([P(R, ang[k]), P(R, ang[k + 1]), P(ri, ang[k + 1]), P(ri, ang[k])], col, col))
        rh = ri - 0.068 * h                             # inner half-hexagon, inset from the ring
        shapes.append(([P(rh, math.radians(a)) for a in (-90, -30, 30, 90)], acc_col, ink_col))

        # glitch: a cut rising to the right through the lower half
        th = math.radians(-30)                          # parallel to a hex edge (y is down)
        p0 = (cx, cy)                                   # cut passes through the centre
        nx, ny = -math.sin(th), math.cos(th)            # unit normal, pointing down-ish
        gap, shift = t, 0.081 * h                       # slit width = ring thickness; slide along cut
        dist = lambda q: (q[0] - p0[0]) * nx + (q[1] - p0[1]) * ny

        def clip(poly, keep):                           # Sutherland-Hodgman against keep(q) >= 0
            out = []
            for i, a in enumerate(poly):
                b = poly[(i + 1) % len(poly)]
                fa, fb = keep(a), keep(b)
                if fa >= 0:
                    out.append(a)
                if (fa >= 0) != (fb >= 0):
                    u = fa / (fa - fb)
                    out.append((a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u))
            return out

        for poly, col_up, col_lo in shapes:             # colours above / below the cut
            upper = clip(poly, lambda q: -gap / 2 - dist(q))
            lower = clip(poly, lambda q: dist(q) - gap / 2)
            if len(upper) >= 3:
                self.poly(upper, col_up)
            if len(lower) >= 3:
                self.poly([(qx + shift * math.cos(th), qy + shift * math.sin(th)) for qx, qy in lower], col_lo)

    def digit(self, d, x, top, w, h, T, col, angle=45):
        """Angular display-style digit drawn as continuous strokes of width T: square
        corners, with chamfers at `angle` on the top-left and bottom-right corners. Every
        diagonal shares that angle: the 2's diagonal runs from the right side
        almost into the bottom-left corner (a short upright keeps the joint from spiking)."""
        x0, x1 = x + T / 2, x + w - T / 2               # stroke centre lines
        y0, ym, y1 = top + T / 2, top + h / 2, top + h - T / 2
        c = 0.42 * w                                    # chamfer run
        slope = math.tan(math.radians(angle))           # every diagonal shares this angle
        dc = c * slope                                  # chamfer drop
        tl = [(x0, y0 + dc), (x0 + c, y0)]              # top-left chamfer, left side -> top
        br = [(x1, y1 - dc), (x1 - c, y1)]              # bottom-right chamfer, right side -> bottom
        foot = T * 0.9                                  # short upright at the 2's bottom-left
        c5 = min(c, (y1 - dc - T - ym) / slope)         # 5's mid chamfer, kept clear of the bottom one
        paths = {
            "0": [[*tl, (x1, y0), *br, (x0, y1), tl[0]]],
            "1": [[(x1 - c, y0), (x1, y0), (x1, y1)]],
            "2": [[*tl, (x1, y0), (x1, y1 - foot - (x1 - x0) * slope), (x0, y1 - foot), (x0, y1), (x1, y1)]],
            "3": [[(x0, y0), (x1, y0), *br, (x0, y1)], [(x0 + 0.35 * w, ym - 0.07 * h), (x1, ym - 0.07 * h)]],
            "4": [[(x0, y0), (x0, ym), (x1, ym)], [(x1, y0 + dc), (x1, y1)]],
            "5": [[(x1, y0), (x0, y0), (x0, ym), (x1 - c5, ym), (x1, ym + c5 * slope), *br, (x0, y1)]],
            "6": [[(x1, y0), *tl[::-1], (x0, y1), *br[::-1], (x1, ym), (x0, ym)]],
            "7": [[*tl, (x1, y0), (x1, y1)]],
            "8": [[*tl, (x1, y0), *br, (x0, y1), tl[0]], [(x0, ym), (x1, ym)]],
            "9": [[(x1, ym), (x0, ym), *tl, (x1, y0), *br, (x0, y1)]],
        }[d]
        for pts in paths:
            closed = pts[0] == pts[-1]
            dpath = "M" + "L".join(f"{px:.3f} {py:.3f}" for px, py in (pts[:-1] if closed else pts))
            self.append(f'<path d="{dpath}{"Z" if closed else ""}" fill="none" stroke="{col}" '
                        f'stroke-width="{T:.3f}" stroke-linejoin="miter" stroke-miterlimit="10" '
                        f'stroke-linecap="square"/>')

    def bar_rule(self, x, edge, w, bar_len, T, th, bar_col, trace_col, flip=False):
        """Header rule, a large cousin of the week-start rule: a thin trace of width w with
        one edge on `edge`, and a heavy bar of length bar_len growing away from that edge,
        ending in a 45° chamfer. By default the edge is the bottom and the bar sits on the
        left; flip=True mirrors both (edge on top, bar on the right)."""
        ch = T - th
        trace = [(0, -th), (w, -th), (w, 0), (0, 0)]
        bar = [(0, -T), (bar_len, -T), (bar_len + ch, -th), (bar_len + ch, 0), (0, 0)]
        place = (lambda u, v: (x + w - u, edge - v)) if flip else (lambda u, v: (x + u, edge + v))
        self.poly([place(u, v) for u, v in trace], trace_col)
        self.poly([place(u, v) for u, v in bar], bar_col)

    def hatch(self, x0, y0, w, h, col, sw, step):
        """45° '/' hatching as plain vector lines (SVG patterns get rasterised in the PDF).
        Lines are x + y = k on a page-wide grid so neighbouring cells line up."""
        x1, y1 = x0 + w, y0 + h
        dk = step * 2 ** 0.5
        k = (int((x0 + y0) / dk) + 1) * dk
        d = []
        while k < x1 + y1:
            xa, xb = max(x0, k - y1), min(x1, k - y0)
            if xb > xa:
                d.append(f"M{xa:.3f} {k - xa:.3f}L{xb:.3f} {k - xb:.3f}")
            k += dk
        self.append(f'<path d="{"".join(d)}" stroke="{col}" stroke-width="{sw:.3f}" fill="none"/>')


def build_svg(year, size, theme, bleed=0.0, week_start=0, font_base="fonts", title=""):
    """Return SVG markup; user units are mm. week_start: 0=Monday, 6=Sunday."""
    c = dict(THEMES[theme])
    c["year_lo"] = mix(c["ink"], c["bg"], 0.7)    # faded first half of the year
    c["month"] = mix(c["ink"], c["bg"], 0.55)     # month names: grey, a touch stronger
    W, H = SIZES[size]
    s = W / 594.0  # everything is designed at A1 and scaled
    PW, PH = W + 2 * bleed, H + 2 * bleed

    m = 18 * s          # page margin
    hh = 22 * s         # height of the header row (logo, title, year)
    head_h = hh + 10 * s  # header block, down to the month names
    mhead_h = 14 * s    # month-name row
    foot_h = 11 * s

    gx0, gx1 = m, W - m
    col_w = (gx1 - gx0) / 12
    hb = m + head_h                 # bottom of header
    gy0 = hb + mhead_h              # top of day rows
    gy1 = H - m - foot_h            # bottom of day rows
    row_h = (gy1 - gy0) / 31

    fills, rules, labels = Layer(), Layer(), Layer()
    hatch_step = 2.2 * s            # "no date" hatching pitch
    pad = 1.8 * s                   # inner cell padding
    dsz = 3.7 * s                   # day-number size
    label_y = lambda y: y + 1.4 * s + dsz * 0.73   # baseline of labels in a row starting at y

    # ---------- header ----------
    # logo | year, sharing top and bottom lines, framed between them by two rules: the bottom
    # one with its heavier bar at the logo end, the top one mirrored with its bar at the year end (all grey)
    hy0, hy1 = m, m + hh
    labels.logo(m, hy0, hh, c["year_lo"], c["accent"], c["accent"])
    logo_w = hh * math.sqrt(3) / 2 + 0.081 * hh * math.cos(math.radians(30))

    ys = str(year)                               # year in angular digits, second half in the accent
    dw, dgap = 0.56 * hh, 0.2 * hh               # digit width, space between digits
    year_x = W - m - len(ys) * dw - (len(ys) - 1) * dgap
    for i, ch in enumerate(ys):
        col = c["year_lo"] if i < len(ys) // 2 else c["accent"]
        labels.digit(ch, year_x + i * (dw + dgap), hy0, dw, hh, 0.1 * hh, col)

    rx0 = m + logo_w + 5 * s                     # connector from the logo to the year
    rx1 = year_x - 6 * s
    bar_T, trace_th = 2.0 * s, 0.6 * s
    bar_len = 60 * s
    rules.bar_rule(rx0, hy1, rx1 - rx0, bar_len, bar_T, trace_th, c["year_lo"], c["year_lo"])
    rules.bar_rule(rx0, hy0, rx1 - rx0, bar_len, bar_T, trace_th, c["year_lo"], c["year_lo"], flip=True)
    if title:                                    # optional title, sitting on the bottom bar
        tsz = 9 * s
        labels.text(rx0 - 0.04 * tsz, hy1 - bar_T - 3.2 * s, title, tsz, c, weight=600, fill="ink")

    # ---------- month names ----------
    for mi in range(12):
        x = gx0 + mi * col_w
        labels.text(x + pad, hb + 10.4 * s, MONTHS[mi], 8.6 * s, c, weight=600, fill="month")
        labels.text(x + col_w - pad, hb + 10.4 * s, f"{mi + 1:02d}", 3.0 * s, c,
                    weight=500, fill="muted", anchor="end")

    # ---------- day cells ----------
    week_lines = []
    for mi in range(12):
        x = gx0 + mi * col_w
        dim = cal.monthrange(year, mi + 1)[1]
        for d in range(1, 32):
            y = gy0 + (d - 1) * row_h
            if d > dim:
                fills.hatch(x, y, col_w, row_h, c["hatch"], 0.35 * s, hatch_step)
                continue
            date = dt.date(year, mi + 1, d)
            wd = date.weekday()
            if wd >= 5:
                fills.rect(x, y, col_w, row_h, c["weekend"])
            if wd == week_start and d > 1:
                week_lines.append((x, y))
            ty = label_y(y)
            labels.text(x + pad, ty, WD[wd], 2.6 * s, c, weight=500, fill="ink" if wd >= 5 else "muted")
            labels.text(x + col_w - pad, ty, f"{d:02d}", dsz, c, weight=700, anchor="end")

    for r in range(0, 32):                       # row hairlines, incl. under month names and grid bottom
        y = gy0 + r * row_h
        rules.line(gx0, y, gx1, y, 0.15 * s, c["rule"])
    for mi in range(0, 13):                      # column hairlines
        x = gx0 + mi * col_w
        rules.line(x, gy0, x, gy1, 0.15 * s, c["rule"])
    for x, y in week_lines:                      # week-start rules
        rules.week_mark(x, y, col_w, c["accent"], s)

    # ---------- footer ----------
    fy, fsz = H - m, 2.7 * s
    sw, sh = 6 * s, 3.2 * s
    lx = W - m
    for kind, label in [("rule", "week start"), ("hatch", "no date"), ("weekend", "weekend")]:
        labels.text(lx, fy, label, fsz, c, weight=500, fill="muted", anchor="end", ls=0.04 * fsz)
        lx -= len(label) * fsz * (MONO_ADV + 0.04) + 2 * s
        bx, by = lx - sw, fy - sh + 0.3 * s
        if kind == "rule":
            bx = lx - 16 * s
            rules.week_mark(bx, by + sh / 2, 16 * s, c["accent"], s)
        else:
            if kind == "hatch":
                fills.hatch(bx, by, sw, sh, c["hatch"], 0.35 * s, hatch_step)
            fills.rect(bx, by, sw, sh, c["weekend"] if kind == "weekend" else "none",
                       stroke=c["rule"], sw=0.15 * s)
        lx = bx - 6 * s

    head = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{PW:g}mm" height="{PH:g}mm" viewBox="0 0 {PW:g} {PH:g}">',
        "<defs><style>"
        f"@font-face{{font-family:'Cal Mono';src:url('{font_base}/JetBrainsMono.ttf');font-weight:100 800}}"
        "text{font-variant-numeric:tabular-nums}</style></defs>",
        f'<rect width="{PW:g}" height="{PH:g}" fill="{c["bg"]}"/>',
        f'<g transform="translate({bleed:g},{bleed:g})">',
    ]
    return "\n".join(head + fills + rules + labels + ["</g></svg>"])


def find_chrome():
    for p in (os.environ.get("CHROME"),
              "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
              "/Applications/Chromium.app/Contents/MacOS/Chromium",
              shutil.which("google-chrome"), shutil.which("chromium"), shutil.which("chromium-browser")):
        if p and Path(p).exists():
            return p
    return None


def html_page(svg, pw, ph, px_per_mm=None):
    if px_per_mm:   # on-screen preview
        dims, page = f"width:{pw * px_per_mm:g}px;height:{ph * px_per_mm:g}px", ""
    else:           # print
        dims, page = f"width:{pw:g}mm;height:{ph:g}mm", f"@page{{size:{pw:g}mm {ph:g}mm;margin:0}}"
    svg = svg.replace(f'width="{pw:g}mm" height="{ph:g}mm"', f'style="{dims};display:block"', 1)
    return (f"<!doctype html><html><head><meta charset='utf-8'><style>{page}"
            f"html,body{{margin:0;padding:0;overflow:hidden}}</style></head><body>{svg}</body></html>")


def run_chrome(chrome_bin, args):
    subprocess.run([chrome_bin, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                    "--virtual-time-budget=8000", *args],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# How Ghostscript's default CMYK profile renders black ink alone, as the sRGB grey it
# looks like, for K = 0, 0.05 ... 1. Used to pick the K that prints each design grey
# at the same lightness as on screen.
K_CURVE = [255, 241, 230, 220, 209, 199, 188, 177, 167, 157, 147, 138, 128, 119, 109, 99, 88, 77, 65, 51, 35]
# Same for full black plus a neutral underlay t * (C .4, M .3, Y .3), t = 0, 0.1 ... 1,
# for greys darker than black ink alone can reach (the dark background).
RICH_CURVE = [33, 29, 26, 24, 20, 18, 15, 11, 8, 4, 2]
UNDERLAY = (0.4, 0.3, 0.3)


def _inverse(curve, v, step):
    """Position along a descending curve where it reaches grey level v."""
    if v >= curve[0]:
        return 0.0
    for i in range(len(curve) - 1):
        hi, lo = curve[i], curve[i + 1]
        if hi >= v >= lo:
            return (i + (hi - v) / (hi - lo)) * step
    return (len(curve) - 1) * step


def print_cmyk(v, rich=False):
    """CMYK that prints sRGB grey level v (0-255) neutrally: black ink only, or for
    greys darker than K alone (only if `rich`), K100 plus a light neutral underlay."""
    if v < K_CURVE[-1] and rich:
        u = _inverse(RICH_CURVE, v, 0.1)
        return (UNDERLAY[0] * u, UNDERLAY[1] * u, UNDERLAY[2] * u, 1.0)
    return (0, 0, 0, _inverse(K_CURVE, v, 0.05))


def fmt_cmyk(vals):
    return " ".join(f"{v:.3f}".rstrip("0").rstrip(".") or "0" for v in vals).encode()


def gray_to_k(pdf_bytes, remap=()):
    """Rewrite neutral CMYK fills/strokes (C = M = Y, from the plain RGB->CMYK pass) as
    black-ink-only greys of matching lightness, and swap any colour listed in `remap`
    ((from_cmyk, to_cmyk) pairs) for its chosen print value. Replacements are padded
    to the original length, so stream lengths and the xref table stay valid."""
    num = rb"(\d*\.?\d+)"
    pat = re.compile(rb"(?<![\w.])" + rb" ".join([num] * 4) + rb" ([kK])(?![\w])")
    skipped = []

    def sub(mo):
        cur = tuple(float(x) for x in mo.groups()[:4])
        cv, mv, yv, kv = cur
        for src, dst in remap:
            if all(abs(a - b) < 0.01 for a, b in zip(cur, src)):
                new = fmt_cmyk(dst) + b" " + mo.group(5)
                break
        else:
            if not (cv == mv == yv) or cv == 0:
                return mo.group(0)
            new = fmt_cmyk(print_cmyk(255 * (1 - cv) * (1 - kv))) + b" " + mo.group(5)
        old = mo.group(0)
        if len(new) > len(old):
            skipped.append(old)
            return old
        return new + b" " * (len(old) - len(new))
    out = pat.sub(sub, pdf_bytes)
    if skipped:
        print(f"warning: {len(skipped)} colour(s) left unconverted: {set(skipped)}", file=sys.stderr)
    return out


def to_print_pdf(src, dst, w_mm, h_mm, theme):
    """Ghostscript passes for print shops: CMYK with neutral greys printed in black ink
    (tone-matched to the design), text to outlines (Chrome embeds the variable font as
    Type3 glyphs, which some RIPs dislike), exact page size."""
    pt = 72 / 25.4
    gs = ["gs", "-q", "-dBATCH", "-dNOPAUSE", "-dSAFER", "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.6",
          "-dFIXEDMEDIA", f"-dDEVICEWIDTHPOINTS={w_mm * pt:.4f}", f"-dDEVICEHEIGHTPOINTS={h_mm * pt:.4f}"]
    with tempfile.TemporaryDirectory() as td:
        a, b = Path(td) / "a.pdf", Path(td) / "b.pdf"
        # 1: plain (non-ICC) RGB->CMYK, so a neutral RGB grey comes out with C = M = Y exactly
        subprocess.run(gs + ["-sColorConversionStrategy=CMYK", "-dProcessColorModel=/DeviceCMYK",
                             "-dUseFastColor", "-dNoOutputFonts", "-dPDFFitPage", "-dCompressPages=false",
                             f"-sOutputFile={a}", str(src)], check=True)
        # 2: greys -> tone-matched K; accent -> its chosen CMYK; background may use a rich black
        th = THEMES[theme]
        fast = lambda hx: tuple(1 - int(hx[i:i + 2], 16) / 255 for i in (1, 3, 5)) + (0,)
        remap = [(fast(th["accent"]), th["accent_cmyk"]),
                 (fast(th["bg"]), print_cmyk(int(th["bg"][1:3], 16), rich=True))]
        b.write_bytes(gray_to_k(a.read_bytes(), remap))
        # 3: recompress, colours untouched
        subprocess.run(gs + ["-sColorConversionStrategy=LeaveColorUnchanged", f"-sOutputFile={dst}", str(b)],
                       check=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--year", type=int, default=dt.date.today().year)
    ap.add_argument("--size", choices=SIZES, nargs="+", default=["A1"], help="one or more paper sizes")
    ap.add_argument("--theme", choices=[*THEMES, "both"], default="both")
    ap.add_argument("--bleed", type=float, default=0, help="bleed in mm per side (print shops often want 3)")
    ap.add_argument("--week-start", choices=["mon", "sun"], default="mon", help="day that gets the heavier rule")
    ap.add_argument("--out", type=Path, default=HERE / "out")
    ap.add_argument("--no-pdf", action="store_true")
    ap.add_argument("--no-png", action="store_true")
    ap.add_argument("--png-scale", type=float, default=2.0, help="preview pixels per mm")
    ap.add_argument("--rgb", action="store_true", help="skip the Ghostscript CMYK/print pass")
    ap.add_argument("--title", default="", help="optional header title, shown on the bottom rule")
    o = ap.parse_args()

    need_chrome = not (o.no_pdf and o.no_png)
    chrome_bin = find_chrome() if need_chrome else None
    if need_chrome and not chrome_bin:
        sys.exit("Chrome not found: set CHROME=/path/to/chrome, or pass --no-pdf --no-png for SVG only")

    cmyk = not o.no_pdf and not o.rgb and shutil.which("gs")
    if not o.no_pdf and not o.rgb and not cmyk:
        print("note: Ghostscript (gs) not found, PDFs stay RGB (brew install ghostscript)", file=sys.stderr)

    outdir = (o.out / str(o.year)).resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    ws = 0 if o.week_start == "mon" else 6
    for size, th in [(sz, th) for sz in o.size for th in (list(THEMES) if o.theme == "both" else [o.theme])]:
        render(o, size, th, ws, outdir, chrome_bin, cmyk)


def render(o, size, th, ws, outdir, chrome_bin, cmyk):
    W, H = SIZES[size]
    pw, ph = W + 2 * o.bleed, H + 2 * o.bleed
    tag = size + (f"-bleed{o.bleed:g}" if o.bleed else "")
    stem = outdir / f"calendar-{o.year}-{tag}-{th}"
    # SVG file references fonts relatively so it opens in an editor; Chrome gets absolute URIs
    rel = Path(os.path.relpath(FONTS, outdir)).as_posix()
    Path(f"{stem}.svg").write_text(build_svg(o.year, size, th, o.bleed, ws, rel, o.title), encoding="utf-8")
    svg = build_svg(o.year, size, th, o.bleed, ws, FONTS.as_uri(), o.title)
    made = ["svg"]
    with tempfile.TemporaryDirectory() as td:
        if not o.no_pdf:
            hp = Path(td) / "print.html"
            hp.write_text(html_page(svg, pw, ph), encoding="utf-8")
            raw = Path(td) / "chrome.pdf" if cmyk else Path(f"{stem}.pdf")
            run_chrome(chrome_bin, ["--no-pdf-header-footer", f"--print-to-pdf={raw}", hp.as_uri()])
            if cmyk:
                to_print_pdf(raw, f"{stem}.pdf", pw, ph, th)
            made.append("pdf" + (" (cmyk)" if cmyk else " (rgb)"))
        if not o.no_png:
            hp = Path(td) / "preview.html"
            hp.write_text(html_page(svg, pw, ph, o.png_scale), encoding="utf-8")
            run_chrome(chrome_bin, [f"--screenshot={stem}.png",
                                    f"--window-size={round(pw * o.png_scale)},{round(ph * o.png_scale)}",
                                    hp.as_uri()])
            made.append("png")
    print(f"{stem}.{{{','.join(made)}}}")


if __name__ == "__main__":
    main()
