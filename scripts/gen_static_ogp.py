"""Generate static OGP brand cards (1200×630).

Refined design v2:
- Side-by-side layout: serif headline on the LEFT, pentagon on the RIGHT
- Noto Serif JP (明朝体) for brand headlines — elegant and matches aixis.jp brand
- Sans-serif for utility text (axis labels, URLs)
- Generous breathing room, no text/pentagon collisions
"""
import sys
sys.path.insert(0, 'src')
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import math

from aixis_web.pages import (
    _og_build_background, _og_draw_pentagon_frame,
    _OG_W, _OG_H, _OG_SS, _TEAL, _FG, _FG_DIM,
)


def _fit_serif(draw, text, font_path, max_w, sizes):
    """Try sizes from largest to smallest, return first that fits."""
    for sz in sizes:
        f = ImageFont.truetype(font_path, sz)
        if draw.textlength(text, font=f) <= max_w:
            return f
    return ImageFont.truetype(font_path, sizes[-1])


def render_brand_card(
    headline_lines: list[str],
    label: str,
    subtitle: str,
    url_label: str,
    fonts_dir: str,
    out_path: str,
):
    """Brand-focused OGP — serif text on left, pentagon on right."""
    S = _OG_SS
    W, H = _OG_W * S, _OG_H * S
    bg = _og_build_background(W, H)
    img = bg.copy()
    draw = ImageDraw.Draw(img, "RGBA")

    _FD = Path(fonts_dir)
    SERIF_BOLD = str(_FD / 'NotoSerifJP-Bold.ttf')
    SERIF_MED = str(_FD / 'NotoSerifJP-Medium.ttf')
    SERIF_REG = str(_FD / 'NotoSerifJP-Regular.ttf')
    SANS_MED = str(_FD / 'NotoSansJP-Medium.ttf')
    SANS_BOLD = str(_FD / 'NotoSansJP-Bold.ttf')

    def _tt(p, sz):
        return ImageFont.truetype(p, sz * S)

    # ── Layout zones ──
    PL = 70 * S  # left padding
    PR = 60 * S  # right padding
    text_col_x = PL
    text_col_w = 700 * S  # left text column width

    pent_col_cx = 900 * S
    pent_cy = H // 2
    pent_r = 140 * S

    # ── Right: Pentagon with axis labels ──
    _og_draw_pentagon_frame(draw, pent_col_cx, pent_cy, pent_r)

    # Axis labels (sans-serif for clarity at small size)
    fax = _tt(SANS_MED, 18)
    AXIS_NAMES = ["実務適性", "費用対効果", "日本語能力", "信頼性・安全性", "革新性"]
    label_r = pent_r + 32 * S
    label_offsets = [
        (0, -1),           # top
        (0.85, -0.2),      # top-right
        (0.55, 1),         # bottom-right
        (-0.55, 1),        # bottom-left
        (-0.85, -0.2),     # top-left
    ]
    for i, name in enumerate(AXIS_NAMES):
        ang = math.radians(-90 + i * 72)
        lx = pent_col_cx + label_r * math.cos(ang)
        ly = pent_cy + label_r * math.sin(ang)
        lb = draw.textbbox((0, 0), name, font=fax)
        lw_, lh_ = lb[2] - lb[0], lb[3] - lb[1]
        ox, oy = label_offsets[i]
        tx = lx - lw_ / 2 + (ox * lw_ / 2) - lb[0]
        ty = ly - lh_ / 2 + (oy * lh_ / 2) - lb[1]
        draw.text((tx, ty), name, fill=_FG_DIM + (255,), font=fax)

    # Inside pentagon: small "5-axis" indicator
    fc = _tt(SANS_MED, 13)
    inner_text = "5-AXIS AI AUDIT"
    ib = draw.textbbox((0, 0), inner_text, font=fc)
    iw = ib[2] - ib[0]
    draw.text(
        (pent_col_cx - iw // 2 - ib[0], pent_cy - 8 * S - ib[1]),
        inner_text, fill=(_TEAL[0], _TEAL[1], _TEAL[2], 200), font=fc,
    )
    # Subtle "Aixis" underneath
    fa = _tt(SANS_BOLD, 16)
    aixis_text = "Aixis"
    ab = draw.textbbox((0, 0), aixis_text, font=fa)
    aw = ab[2] - ab[0]
    draw.text(
        (pent_col_cx - aw // 2 - ab[0], pent_cy + 12 * S - ab[1]),
        aixis_text, fill=_FG + (220,), font=fa,
    )

    # ── Left: Text block ──
    # Small accent label at top
    flbl = ImageFont.truetype(SERIF_MED, 19 * S)
    label_y = 110 * S
    # Tiny accent square
    sq_size = 6 * S
    sq_y = label_y + 6 * S
    draw.rectangle(
        [text_col_x, sq_y, text_col_x + sq_size, sq_y + sq_size],
        fill=_TEAL + (255,),
    )
    draw.text(
        (text_col_x + sq_size + 12 * S, label_y),
        label, fill=_TEAL + (240,), font=flbl,
    )

    # Hairline divider beneath label
    line_y = label_y + 36 * S
    draw.line(
        [(text_col_x, line_y), (text_col_x + 80 * S, line_y)],
        fill=(_TEAL[0], _TEAL[1], _TEAL[2], 180), width=2,
    )

    # Headline (serif, 2 lines, large)
    headline_top = line_y + 30 * S
    headline_size = 70 * S
    # Auto-shrink if first line too wide
    longest = max(headline_lines, key=len)
    f_test = ImageFont.truetype(SERIF_BOLD, headline_size)
    while draw.textlength(longest, font=f_test) > text_col_w - 20 * S and headline_size > 40 * S:
        headline_size -= 4 * S
        f_test = ImageFont.truetype(SERIF_BOLD, headline_size)
    fhead = f_test

    line_height = int(headline_size * 1.18)
    cy = headline_top
    for line in headline_lines:
        # Draw with subtle teal glow underneath
        lb = draw.textbbox((0, 0), line, font=fhead)
        # Soft outer glow
        from PIL import ImageFilter
        glow = Image.new("RGBA", (lb[2] - lb[0] + 60 * S, lb[3] - lb[1] + 60 * S), (0, 0, 0, 0))
        gd = ImageDraw.Draw(glow)
        gd.text(
            (30 * S - lb[0], 30 * S - lb[1]),
            line, fill=(_TEAL[0], _TEAL[1], _TEAL[2], 60), font=fhead,
        )
        glow = glow.filter(ImageFilter.GaussianBlur(radius=8 * S))
        img.paste(glow, (text_col_x - 30 * S, cy - 30 * S), glow)
        draw = ImageDraw.Draw(img, "RGBA")
        # Main text
        draw.text(
            (text_col_x - lb[0], cy - lb[1]),
            line, fill=_FG + (255,), font=fhead,
        )
        cy += line_height

    # Subtitle below headline (serif, smaller, dim)
    fsub = ImageFont.truetype(SERIF_MED, 22 * S)
    sub_y = cy + 14 * S
    sb = draw.textbbox((0, 0), subtitle, font=fsub)
    draw.text(
        (text_col_x - sb[0], sub_y - sb[1]),
        subtitle, fill=_FG_DIM + (255,), font=fsub,
    )

    # ── Footer (full width) ──
    footer_y = H - 64 * S
    draw.line(
        [(PL, footer_y), (W - PR, footer_y)],
        fill=(255, 255, 255, 28), width=1,
    )
    fy = footer_y + 18 * S
    fbr = ImageFont.truetype(SERIF_BOLD, 18 * S)
    fbs = ImageFont.truetype(SANS_MED, 14 * S)
    # Brand text on left
    draw.text((PL, fy - 2 * S), "Aixis", fill=_TEAL + (255,), font=fbr)
    brand_bb = draw.textbbox((0, 0), "Aixis", font=fbr)
    brand_w = brand_bb[2] - brand_bb[0]
    draw.text(
        (PL + brand_w + 12 * S, fy + 5 * S),
        "独立AI監査プラットフォーム",
        fill=_FG_DIM + (255,), font=fbs,
    )
    # URL on right
    urb = draw.textbbox((0, 0), url_label, font=fbs)
    draw.text(
        (W - PR - (urb[2] - urb[0]), fy + 5 * S),
        url_label, fill=_FG_DIM + (255,), font=fbs,
    )

    final = img.convert("RGB").resize((_OG_W, _OG_H), Image.LANCZOS)
    final.save(out_path, "PNG", optimize=True)
    print("saved", out_path)


if __name__ == "__main__":
    fonts = "src/aixis_web/static/fonts"
    # aixis.jp (corporate)
    render_brand_card(
        headline_lines=["AI導入に、", "中立な審判を。"],
        label="株式会社Aixis（アイクシス）",
        subtitle="独立した第三者の立場で、AIツールを定量評価する独立系AI監査機関",
        url_label="aixis.jp",
        fonts_dir=fonts,
        out_path="/tmp/ogp_aixis_corp.png",
    )
    # platform.aixis.jp
    render_brand_card(
        headline_lines=["独立した5軸で、", "AIを監査する。"],
        label="Aixis AI Audit Platform",
        subtitle="AIツール選定を、感覚ではなくデータで支援する独立系AI監査プラットフォーム",
        url_label="platform.aixis.jp",
        fonts_dir=fonts,
        out_path="/tmp/ogp_platform_home.png",
    )
