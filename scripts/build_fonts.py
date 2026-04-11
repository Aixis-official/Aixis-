"""Build subsetted Noto Serif JP woff2 files for self-hosting.

Source: /Users/shiro/Library/Fonts/NotoSerifJP-*.ttf (must be installed)
Output: src/aixis_web/static/fonts/NotoSerifJP-{weight}.woff2

Subset coverage (≈ JIS X 0208 第一+第二水準 + 必要記号):
  - ASCII basic               (U+0020 - U+007E)
  - Latin-1 supplement core   (U+00A0 - U+00FF)
  - General punctuation       (U+2000 - U+206F)
  - Letterlike / arrows / math symbols used in 本文
  - CJK symbols & punctuation (U+3000 - U+303F)
  - Hiragana                  (U+3040 - U+309F)
  - Katakana                  (U+30A0 - U+30FF)
  - Halfwidth & fullwidth     (U+FF00 - U+FFEF)
  - All kanji encodable in cp932 (≈ JIS X 0208 + Microsoft 拡張)

Run: .venv/bin/python scripts/build_fonts.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from fontTools.subset import Subsetter, Options
from fontTools.ttLib import TTFont

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SOURCE_DIR = Path("/Users/shiro/Library/Fonts")

# (input filename, output filename, css font-weight)
WEIGHTS: list[tuple[str, str, int]] = [
    ("NotoSerifJP-Regular.ttf",  "NotoSerifJP-Regular.woff2",  400),
    ("NotoSerifJP-Medium.ttf",   "NotoSerifJP-Medium.woff2",   500),
    ("NotoSerifJP-SemiBold.ttf", "NotoSerifJP-SemiBold.woff2", 600),
    ("NotoSerifJP-Bold.ttf",     "NotoSerifJP-Bold.woff2",     700),
    ("NotoSerifJP-ExtraBold.ttf","NotoSerifJP-ExtraBold.woff2",800),
]

OUTPUT_DIRS = [
    Path("/Users/shiro/Downloads/platform.aixis.jp/src/aixis_web/static/fonts"),
    Path("/Users/shiro/Downloads/aixis.jp/src/aixis_corp/static/fonts"),
]


# ---------------------------------------------------------------------------
# Build the Unicode codepoint set
# ---------------------------------------------------------------------------

def _build_unicodes() -> set[int]:
    cps: set[int] = set()

    # Basic ranges (always include for Latin/digits/punctuation/kana)
    ranges = [
        (0x0020, 0x007E),  # ASCII basic
        (0x00A0, 0x00FF),  # Latin-1 supplement
        (0x2000, 0x206F),  # General punctuation (em dash, smart quotes, etc.)
        (0x2070, 0x209F),  # Super/subscripts
        (0x20A0, 0x20CF),  # Currency
        (0x2100, 0x214F),  # Letterlike (™, №)
        (0x2150, 0x218F),  # Number forms
        (0x2190, 0x21FF),  # Arrows
        (0x2200, 0x22FF),  # Math operators
        (0x2300, 0x23FF),  # Misc technical
        (0x2460, 0x24FF),  # Enclosed alphanumerics (①②...)
        (0x2500, 0x257F),  # Box drawing
        (0x25A0, 0x25FF),  # Geometric shapes
        (0x2600, 0x26FF),  # Misc symbols (★☆)
        (0x3000, 0x303F),  # CJK symbols and punctuation (、。「」)
        (0x3040, 0x309F),  # Hiragana
        (0x30A0, 0x30FF),  # Katakana
        (0x3200, 0x32FF),  # Enclosed CJK letters and months
        (0x3300, 0x33FF),  # CJK compatibility
        (0xFB00, 0xFB06),  # Latin ligatures (fi, fl)
        (0xFE30, 0xFE4F),  # CJK compatibility forms
        (0xFF00, 0xFFEF),  # Halfwidth and fullwidth forms
    ]
    for lo, hi in ranges:
        cps.update(range(lo, hi + 1))

    # All Kanji encodable in cp932 (≈ JIS X 0208 第一+第二水準 + Microsoft 拡張)
    # cp932 covers ~6,879 kanji which fully includes JIS X 0208 (6,355 kanji).
    for code in range(0x4E00, 0xA000):  # CJK Unified Ideographs block
        try:
            ch = chr(code)
            ch.encode("cp932")
            cps.add(code)
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass

    # Some commonly-used 第三/第四水準 kanji that aren't in cp932
    # but are used in modern Japanese (人名漢字 etc.)
    extras = [
        0x9DCF,  # 鷏
        0x9DD7,  # 鷗 (already in cp932 most of the time)
        0x9F8D,  # 龍 (already)
        0xFA10,  # 塚 compatibility
        0xFA1F,  # 神 compatibility
        0xFA22,  # 福 compatibility
        0xFA26,  # 都 compatibility
        0xFA30,  # 侮 compatibility
        # Add more as needed; safe to include even if absent in source
    ]
    cps.update(extras)

    return cps


# ---------------------------------------------------------------------------
# Subsetter
# ---------------------------------------------------------------------------

def _subset_one(src: Path, dst: Path, unicodes: set[int]) -> tuple[int, int]:
    if not src.exists():
        print(f"  SKIP (missing source): {src}", file=sys.stderr)
        return 0, 0

    options = Options()
    options.flavor = "woff2"
    options.with_zopfli = False
    options.desubroutinize = False
    # Drop hinting / vertical metrics we don't need on the web
    options.hinting = False
    options.drop_tables = ["VORG", "vhea", "vmtx"]
    # Keep features useful for Japanese text shaping
    options.layout_features = [
        "kern", "liga", "calt", "ccmp",
        "locl", "mark", "mkmk",
        "vert", "vrt2",  # vertical alternates
        "hwid", "fwid",  # half/full width forms
        "palt",          # proportional alternates
    ]
    options.name_IDs = ["*"]
    options.name_legacy = True
    options.name_languages = ["*"]
    options.recalc_timestamp = False

    font = TTFont(str(src))
    subsetter = Subsetter(options=options)
    subsetter.populate(unicodes=sorted(unicodes))
    subsetter.subset(font)
    dst.parent.mkdir(parents=True, exist_ok=True)
    font.flavor = "woff2"
    font.save(str(dst))

    src_size = src.stat().st_size
    dst_size = dst.stat().st_size
    return src_size, dst_size


def main() -> int:
    if not SOURCE_DIR.exists():
        print(f"Source dir not found: {SOURCE_DIR}", file=sys.stderr)
        return 1

    unicodes = _build_unicodes()
    print(f"Subset codepoint count: {len(unicodes):,}")
    print()

    for out_dir in OUTPUT_DIRS:
        print(f"=== {out_dir} ===")
        total_src = 0
        total_dst = 0
        for src_name, dst_name, weight in WEIGHTS:
            src = SOURCE_DIR / src_name
            dst = out_dir / dst_name
            src_size, dst_size = _subset_one(src, dst, unicodes)
            if dst_size:
                ratio = (1 - dst_size / src_size) * 100 if src_size else 0
                print(
                    f"  weight {weight:>3}: "
                    f"{src_size/1024:>8,.1f} KB  →  "
                    f"{dst_size/1024:>8,.1f} KB  "
                    f"(-{ratio:.1f}%)"
                )
                total_src += src_size
                total_dst += dst_size
        if total_src:
            ratio = (1 - total_dst / total_src) * 100
            print(
                f"  ----- total: "
                f"{total_src/1024/1024:.2f} MB  →  "
                f"{total_dst/1024/1024:.2f} MB  "
                f"(-{ratio:.1f}%)"
            )
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
