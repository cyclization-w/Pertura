"""Figure panels (docs/adr/0020, section 7, part 4): a dossier's figures as one panel at Nature's double-column width.

The first figure (the main one, the map) comes first as a; the others follow, left to right and row by row, b, c and
so on. Every figure is placed at the size it was drawn (the modules draw at 300 dpi in the shared style), so fonts and
line widths stay those of the style; only a figure wider than the panel is scaled down to fit. The letters are bold, at
8 pt, in the style's font.
"""
from PIL import Image, ImageDraw, ImageFont

from .question_modules.figures import DOUBLE, STYLE

DPI = 300
GAP = int(round(3 / 25.4 * DPI))      # 3 mm between tiles and rows
LETTER_PT = 8


def _font():
    from matplotlib import font_manager
    path = font_manager.findfont(font_manager.FontProperties(family=STYLE["font.sans-serif"], weight="bold"))
    return ImageFont.truetype(path, int(round(LETTER_PT / 72 * DPI)))


def compose(paths, out, width_mm=None):
    """The panel of the PNGs at `paths` (the first the main figure), saved at `out`; returns the tiles placed:
    [{letter, figure, scaled}]."""
    width = int(round((width_mm / 25.4 if width_mm else DOUBLE) * DPI))
    font = _font()
    letter_h = int(round(LETTER_PT / 72 * DPI * 1.25))
    tiles = []
    for p in paths:
        im = Image.open(p).convert("RGB")
        scaled = im.width > width
        if scaled:
            im = im.resize((width, int(round(im.height * width / im.width))), Image.LANCZOS)
        tiles.append({"figure": p, "image": im, "scaled": scaled})
    rows, row, used = [], [], 0
    for k, tl in enumerate(tiles):  # the main figure alone on its row; the rest fill rows left to right
        w = tl["image"].width
        if row and (k == 1 or used + GAP + w > width):
            rows.append(row)
            row, used = [], 0
        row.append(tl)
        used += (GAP if used else 0) + w
    rows.append(row)
    height = sum(letter_h + max(t["image"].height for t in r) for r in rows) + GAP * (len(rows) - 1)
    canvas = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(canvas)
    y, k = 0, 0
    for r in rows:
        x = 0
        for tl in r:
            tl["letter"] = "abcdefghijklmnop"[k]
            draw.text((x, y), tl["letter"], fill="black", font=font)
            canvas.paste(tl["image"], (x, y + letter_h))
            x += tl["image"].width + GAP
            k += 1
        y += letter_h + max(t["image"].height for t in r) + GAP
    canvas.save(out, dpi=(DPI, DPI))
    return [{"letter": t["letter"], "figure": t["figure"], "scaled": t["scaled"]} for t in tiles]
