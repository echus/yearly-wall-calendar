# Yearly wall calendar

A vertical year planner with 12 month columns and 31 day rows, so every day gets its own writable cell. It comes in two themes: `dark` and `grey`.

```bash
python3 make_calendar.py --year 2027 --size A0 A1 B1
```

This writes `out/<year>/calendar-<year>-<size>-<theme>.{svg,pdf,png}`:

- **pdf**: the print file. It is CMYK, with text converted to outlines, and the page is exactly the paper size.
  - Every grey prints in black ink only, so it stays truly neutral. Each one is tone-matched to look as light or dark as it does on screen.
  - The dark background is the one exception: full black plus a light neutral C/M/Y underlay, since black ink alone can't print that dark.
  - The accent prints as a fixed CMYK colour, set by `accent_cmyk` in `THEMES`.
- **svg**: editable in Illustrator, Figma or Affinity. Its fonts load from `fonts/`.
- **png**: a low-resolution RGB preview.

| option | default | |
|---|---|---|
| `--year` | current year | |
| `--size` | `A1` | one or more of `A0` `A1` `A2` `A3` `B1`. Everything scales with the page width |
| `--theme` | `both` | `dark`, `grey` or `both` |
| `--bleed` | `0` | mm added on each side. Leave at 0 for Officeworks |
| `--week-start` | `mon` | the day that gets the heavier rule (`sun` is also accepted) |
| `--png-scale` | `2` | preview pixels per mm |
| `--rgb` | off | skip the CMYK print conversion and keep Chrome's RGB PDF |
| `--title` | none | optional title text, shown just after the logo's header panel |

## Printing at Officeworks

The settings below match the Officeworks poster templates in `officeworks-templates/`:

- **Sizes:** A1 is 594 × 841mm, A0 is 841 × 1189mm and B1 is 707 × 1000mm.
- **Page and bleed:** the printable area is the whole page, so don't add bleed.
- **Colour:** files are CMYK. They're vector, so the 300dpi rule doesn't apply.
- **Safe area:** about 5mm in from each edge. The template files don't draw this line; the figure comes from their original file names (e.g. `A1-584x831`). The calendar keeps everything at least 18mm from the edge.
- **Ordering:** choose **Colour Poster**, not Plan Print. Plan Prints have to be 85% white or more, and both themes are coloured to the edge.

## Requirements

- **Python:** the script uses only the standard library.
- **Chrome:** renders the PDF and PNG. If Chrome is somewhere other than `/Applications`, set `CHROME=/path/to/chrome`.
- **Ghostscript:** does the CMYK print conversion. Install it with `brew install ghostscript`. Without it, the PDF stays RGB and the script prints a warning.

The only font is JetBrains Mono (OFL license), stored in `fonts/`.
