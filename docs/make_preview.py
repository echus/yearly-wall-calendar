#!/usr/bin/env python3
"""Build docs/preview.png: close-ups of the dark (left, logo corner) and grey (right,
year corner) A1 calendars, joined by a slanted cut with a small gap.

    python3 docs/make_preview.py [--year 2077]
"""

import argparse
import importlib.util
import subprocess
import tempfile
from pathlib import Path

DOCS = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("mc", DOCS.parent / "make_calendar.py")
mc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mc)

OUT_W = 1600        # image width, px
ASPECT = 2.5        # width : height
SLANT = 120         # horizontal run of the cut, px ("/" direction)
GAP = 14            # gap between the halves, px


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=2077)
    year = ap.parse_args().year

    W, H = mc.SIZES["A1"]
    row_h = (H - 100) / 31                       # A1 day-row height; grid starts at 71 mm
    y0, y1 = 6, 71 + 2 * row_h + 0.4             # page band: header, month row, two day rows
    out_h = round(OUT_W / ASPECT)
    px = out_h / (y1 - y0)                       # px per mm
    half = OUT_W / 2
    side = half + SLANT / 2 + GAP                # px each half needs before clipping
    side_mm = side / px

    def crop(theme, x_mm, left_px, clip):
        svg = mc.build_svg(year, "A1", theme, 0, 0, mc.FONTS.as_uri())
        svg = svg.replace(f'width="{W}mm" height="{H}mm" viewBox="0 0 {W} {H}"',
                          f'width="{side:.0f}" height="{out_h}" viewBox="{x_mm:.3f} {y0} {side_mm:.3f} {y1 - y0:.3f}"'
                          f' style="position:absolute;left:{left_px:.0f}px;top:0;clip-path:polygon({clip})"', 1)
        return svg

    a, b = half + SLANT / 2, half - SLANT / 2    # cut x at top and bottom
    g = GAP / 2
    left = crop("dark", 0, 0, f"0 0,{a - g:.0f}px 0,{b - g:.0f}px 100%,0 100%")
    lx = OUT_W - side                            # right half's left edge in the image
    right = crop("grey", W - side_mm, lx,
                 f"{a + g - lx:.0f}px 0,100% 0,100% 100%,{b + g - lx:.0f}px 100%")
    html = (f"<!doctype html><html><head><meta charset='utf-8'><style>html,body{{margin:0;background:transparent}}"
            f".wrap{{position:relative;width:{OUT_W}px;height:{out_h}px;overflow:hidden}}</style></head>"
            f"<body><div class='wrap'>{left}{right}</div></body></html>")

    chrome = mc.find_chrome()
    with tempfile.TemporaryDirectory() as td:
        page = Path(td) / "preview.html"
        page.write_text(html, encoding="utf-8")
        subprocess.run([chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                        "--virtual-time-budget=8000", "--default-background-color=00000000",
                        "--force-device-scale-factor=1", f"--window-size={OUT_W},{out_h}",
                        f"--screenshot={DOCS / 'preview.png'}", page.as_uri()],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(DOCS / "preview.png")


if __name__ == "__main__":
    main()
