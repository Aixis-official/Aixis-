"""Phase D-2 / Phase E-2: Build the Aixis 監査方法論書 PDF.

Regenerate whenever the audit methodology version or site content changes:

    .venv/bin/python -m pip install reportlab   # first time only
    .venv/bin/python scripts/build_whitepaper.py

Output → src/aixis_web/static/pdf/aixis-audit-methodology-v1_0.pdf

Design principles
-----------------
- Noto Serif JP (明朝体) for every piece of running text and every heading,
  to match the brand typography used on platform.aixis.jp.
- Noto Sans JP for the tiny running header / footer chrome only.
- ALL factual claims in this document come **verbatim** from publicly-stated
  content on platform.aixis.jp — specifically /audit-protocol, /audit-process,
  /transparency, /independence, /score-changelog and /faq.  Do not introduce
  numbers or policies that are not already on the site.

Fonts
-----
Noto Serif JP TTF files are large (≈7.7 MB each) so we do not commit them
to the repo.  The script resolves them in this order:

  1. ``scripts/.build-cache/NotoSerifJP-{Regular,SemiBold}.ttf``  (gitignored)
  2. ``~/Library/Fonts/NotoSerifJP-{Regular,SemiBold}.ttf``       (macOS)
  3. Fails loudly with a one-line instruction to populate the cache.
"""
from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    KeepTogether,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SANS_FONT_DIR = REPO_ROOT / "src" / "aixis_web" / "static" / "fonts"
SERIF_CACHE_DIR = REPO_ROOT / "scripts" / ".build-cache"
MACOS_FONT_DIR = Path.home() / "Library" / "Fonts"
OUT_DIR = REPO_ROOT / "src" / "aixis_web" / "static" / "pdf"
# Versioned filename ensures long-lived CDN / browser caches do not serve
# a stale PDF after methodology updates.
OUT_PATH = OUT_DIR / "aixis-audit-methodology-v1_0.pdf"

# Methodology version and publication date — mirrors the public
# /score-changelog page.
METHODOLOGY_VERSION = "v1.0.0"
PUBLISHED_ON = date(2026, 4, 11)


def _format_jp_date(d: date) -> str:
    """Render a date as ``2026年4月11日`` (no zero-padding on month/day)."""
    return f"{d.year}年{d.month}月{d.day}日"

# Design tokens — identical palette to the web UI.
INK = HexColor("#0f172a")   # slate-900 — body
INK_DIM = HexColor("#475569")  # slate-600 — captions
INK_MUTED = HexColor("#94a3b8")  # slate-400 — chrome
RULE = HexColor("#cbd5e1")  # slate-300 — hairlines
PAPER = HexColor("#ffffff")


# ---------------------------------------------------------------------------
# Fonts
# ---------------------------------------------------------------------------

def _resolve_serif(weight: str) -> Path:
    """Find NotoSerifJP-<weight>.ttf in one of the supported locations."""
    name = f"NotoSerifJP-{weight}.ttf"
    for root in (SERIF_CACHE_DIR, MACOS_FONT_DIR):
        candidate = root / name
        if candidate.exists():
            return candidate
    raise SystemExit(
        f"Missing font {name}. Download NotoSerifJP from\n"
        "  https://fonts.google.com/noto/specimen/Noto+Serif+JP\n"
        f"and place Regular + SemiBold TTF files into {SERIF_CACHE_DIR}"
    )


def _register_fonts() -> dict[str, str]:
    serif_reg = _resolve_serif("Regular")
    serif_bold = _resolve_serif("SemiBold")
    pdfmetrics.registerFont(TTFont("SerifJP", str(serif_reg)))
    pdfmetrics.registerFont(TTFont("SerifJP-Bold", str(serif_bold)))
    pdfmetrics.registerFont(
        TTFont("SansJP", str(SANS_FONT_DIR / "NotoSansJP-Medium.ttf"))
    )
    pdfmetrics.registerFont(
        TTFont("SansJP-Bold", str(SANS_FONT_DIR / "NotoSansJP-Bold.ttf"))
    )
    return {
        "serif": "SerifJP",
        "serif_bold": "SerifJP-Bold",
        "sans": "SansJP",
        "sans_bold": "SansJP-Bold",
    }


# ---------------------------------------------------------------------------
# Paragraph styles — all built against SerifJP (the default body font)
# ---------------------------------------------------------------------------

def _make_styles(f: dict[str, str]) -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()["Normal"]
    return {
        # Cover page
        "cover_kicker": ParagraphStyle(
            "cover_kicker", parent=base,
            fontName=f["sans_bold"], fontSize=9, leading=14,
            textColor=INK_DIM, alignment=TA_LEFT, spaceAfter=18,
        ),
        "cover_title": ParagraphStyle(
            "cover_title", parent=base,
            fontName=f["serif_bold"], fontSize=32, leading=52,
            textColor=INK, alignment=TA_LEFT, spaceAfter=22,
        ),
        "cover_sub": ParagraphStyle(
            "cover_sub", parent=base,
            fontName=f["serif"], fontSize=11.5, leading=26,
            textColor=INK_DIM, alignment=TA_LEFT, spaceAfter=32,
        ),
        "cover_meta_label": ParagraphStyle(
            "cover_meta_label", parent=base,
            fontName=f["sans_bold"], fontSize=7, leading=12,
            textColor=INK_MUTED, alignment=TA_LEFT, spaceAfter=4,
        ),
        "cover_meta_value": ParagraphStyle(
            "cover_meta_value", parent=base,
            fontName=f["serif"], fontSize=11, leading=22,
            textColor=INK, alignment=TA_LEFT, spaceAfter=6,
        ),
        # Running body. `h1_number` carries the generous top air before a
        # new section starts; `h1` sits flush below it with no extra top
        # space so kicker + title read as a unit.
        "h1_number": ParagraphStyle(
            "h1_number", parent=base,
            fontName=f["sans_bold"], fontSize=8, leading=12,
            textColor=INK_MUTED, spaceBefore=44, spaceAfter=6,
        ),
        "h1": ParagraphStyle(
            "h1", parent=base,
            fontName=f["serif_bold"], fontSize=18, leading=30,
            textColor=INK, spaceBefore=0, spaceAfter=24, keepWithNext=True,
        ),
        "h2": ParagraphStyle(
            "h2", parent=base,
            fontName=f["serif_bold"], fontSize=12, leading=24,
            textColor=INK, spaceBefore=24, spaceAfter=12, keepWithNext=True,
        ),
        "body": ParagraphStyle(
            "body", parent=base,
            fontName=f["serif"], fontSize=10.5, leading=22,
            textColor=INK, alignment=TA_JUSTIFY, spaceAfter=14,
            firstLineIndent=0,
        ),
        "quote": ParagraphStyle(
            "quote", parent=base,
            fontName=f["serif"], fontSize=10, leading=20,
            textColor=INK_DIM, alignment=TA_LEFT, spaceAfter=14,
            leftIndent=16, rightIndent=16,
            borderPadding=(0, 0, 0, 0),
        ),
        "bullet": ParagraphStyle(
            "bullet", parent=base,
            fontName=f["serif"], fontSize=10.5, leading=22,
            textColor=INK, alignment=TA_LEFT, spaceAfter=8,
            leftIndent=18, firstLineIndent=-11,
        ),
        "formula": ParagraphStyle(
            "formula", parent=base,
            fontName=f["sans"], fontSize=10, leading=22,
            textColor=INK, alignment=TA_LEFT, spaceAfter=18,
            spaceBefore=8,
            leftIndent=16,
        ),
        "caption": ParagraphStyle(
            "caption", parent=base,
            fontName=f["sans"], fontSize=7.5, leading=14,
            textColor=INK_MUTED, alignment=TA_LEFT, spaceBefore=22, spaceAfter=26,
        ),
        "toc_row": ParagraphStyle(
            "toc_row", parent=base,
            fontName=f["serif"], fontSize=11, leading=30,
            textColor=INK, alignment=TA_LEFT,
        ),
    }


# ---------------------------------------------------------------------------
# Page chrome
# ---------------------------------------------------------------------------

PAGE_MARGIN = 30 * mm   # outer page margin (left/right)
HEADER_Y = 20 * mm      # distance from top to the running header hairline
FOOTER_Y = 20 * mm      # distance from bottom to the running footer hairline


def _draw_body(canvas, doc, f: dict[str, str]) -> None:
    """Running header + footer for every non-cover page."""
    width, height = A4
    canvas.saveState()

    # Top hairline with tiny brand mark on the left and context on the right.
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.3)
    canvas.line(
        PAGE_MARGIN, height - HEADER_Y,
        width - PAGE_MARGIN, height - HEADER_Y,
    )

    canvas.setFont(f["sans_bold"], 7.5)
    canvas.setFillColor(INK_MUTED)
    canvas.drawString(PAGE_MARGIN, height - HEADER_Y + 5, "AIXIS")
    canvas.setFont(f["sans"], 7.5)
    canvas.drawRightString(
        width - PAGE_MARGIN,
        height - HEADER_Y + 5,
        f"監査方法論書  ·  Audit Methodology  {METHODOLOGY_VERSION}",
    )

    # Bottom hairline + page number + copyright.
    canvas.setStrokeColor(RULE)
    canvas.line(
        PAGE_MARGIN, FOOTER_Y,
        width - PAGE_MARGIN, FOOTER_Y,
    )
    canvas.setFont(f["sans"], 7.5)
    canvas.setFillColor(INK_MUTED)
    canvas.drawString(
        PAGE_MARGIN, FOOTER_Y - 6,
        f"© 2026 株式会社Aixis   発行日 {_format_jp_date(PUBLISHED_ON)}",
    )
    canvas.drawRightString(
        width - PAGE_MARGIN, FOOTER_Y - 6, f"— {doc.page - 1} —"
    )
    canvas.restoreState()


def _draw_cover(canvas, doc, f: dict[str, str]) -> None:
    """Cover page — a single thin top rule, no running chrome."""
    width, height = A4
    canvas.saveState()

    # Thin serif rule near the top, flush with the text frame.
    canvas.setStrokeColor(INK)
    canvas.setLineWidth(0.6)
    canvas.line(
        PAGE_MARGIN, height - 27 * mm,
        PAGE_MARGIN + 48 * mm, height - 27 * mm,
    )

    # Tiny sans wordmark above the rule.
    canvas.setFont(f["sans_bold"], 8.5)
    canvas.setFillColor(INK)
    canvas.drawString(PAGE_MARGIN, height - 23 * mm, "AIXIS")

    # Footer: a single slate-900 hairline + meta strip.
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.3)
    canvas.line(
        PAGE_MARGIN, 27 * mm,
        width - PAGE_MARGIN, 27 * mm,
    )
    canvas.setFont(f["sans"], 7.5)
    canvas.setFillColor(INK_MUTED)
    canvas.drawString(
        PAGE_MARGIN, 21 * mm,
        f"方法論 {METHODOLOGY_VERSION}    発行日 {_format_jp_date(PUBLISHED_ON)}",
    )
    canvas.drawRightString(
        width - PAGE_MARGIN, 21 * mm, "platform.aixis.jp"
    )
    canvas.restoreState()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bullet(text: str, styles) -> Paragraph:
    return Paragraph(f"・&nbsp;{text}", styles["bullet"])


def _axis_table(f, styles) -> Table:
    """5軸評価フレームワーク.

    Content from /audit-protocol.html. Weights are equal (均等) per the
    site's explicit statement; the English axis labels are used verbatim.
    """
    data = [
        ["軸", "評価観点", "重み"],
        ["実務適性\nPracticality",
         "業務フローにおける到達点、UXと操作性、既存環境への\n"
         "統合容易性、出力品質の一貫性",
         "均等"],
        ["費用対効果\nCost Performance",
         "料金体系の透明性、無償枠の実用性、有料プランの妥当性、\n"
         "乗り換えコスト、応答速度",
         "均等"],
        ["日本語能力\nJapanese Readiness",
         "UI日本語化、ビジネス日本語の適切性、日本語ドキュメント、\n"
         "レイアウト・フォント対応",
         "均等"],
        ["信頼性・安全性\nSafety",
         "データ保護とアクセス制御、監査ログ、保存場所と越境移転、\n"
         "インシデント対応体制",
         "均等"],
        ["革新性\nUniqueness",
         "独自技術・差別化機能、エコシステム、ロードマップ透明性、\n"
         "APIラッパー依存度",
         "均等"],
    ]
    t = Table(
        data,
        colWidths=[36 * mm, 93 * mm, 16 * mm],
        hAlign="LEFT",
    )
    t.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), f["sans_bold"]),
        ("FONTSIZE", (0, 0), (-1, 0), 8),
        ("TEXTCOLOR", (0, 0), (-1, 0), INK_MUTED),
        ("FONTNAME", (0, 1), (0, -1), f["serif_bold"]),
        ("FONTNAME", (1, 1), (2, -1), f["serif"]),
        ("FONTSIZE", (0, 1), (-1, -1), 10),
        ("LEADING", (0, 1), (-1, -1), 20),
        ("TEXTCOLOR", (0, 1), (-1, -1), INK),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, INK),
        ("LINEBELOW", (0, 1), (-1, -2), 0.2, RULE),
        ("LINEBELOW", (0, -1), (-1, -1), 0.6, INK),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 12),
        ("TOPPADDING", (0, 0), (-1, 0), 4),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 14),
        ("TOPPADDING", (0, 1), (-1, -1), 14),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    return t


def _grade_table(f) -> Table:
    """グレードスケール. Thresholds and 判定基準 copied verbatim from
    /audit-protocol."""
    data = [
        ["グレード", "総合スコア", "評価", "判定基準"],
        ["S", "4.5 – 5.0", "最高評価", "全軸3.0以上 かつ 総合4.5以上"],
        ["A", "3.8 – 4.4", "高品質・推奨", "全軸2.0以上 かつ 総合3.8以上"],
        ["B", "3.0 – 3.7", "標準的", "致命的欠陥なし かつ 総合3.0以上"],
        ["C", "2.0 – 2.9", "改善の余地あり", "複数軸で基準未達"],
        ["D", "0.0 – 1.9", "要注意", "重大な品質問題あり"],
    ]
    t = Table(
        data,
        colWidths=[18 * mm, 26 * mm, 32 * mm, 69 * mm],
        hAlign="LEFT",
    )
    t.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), f["sans_bold"]),
        ("FONTSIZE", (0, 0), (-1, 0), 8),
        ("TEXTCOLOR", (0, 0), (-1, 0), INK_MUTED),
        ("FONTNAME", (0, 1), (0, -1), f["serif_bold"]),
        ("FONTNAME", (1, 1), (-1, -1), f["serif"]),
        ("FONTSIZE", (0, 1), (-1, -1), 10.5),
        ("LEADING", (0, 1), (-1, -1), 20),
        ("TEXTCOLOR", (0, 1), (-1, -1), INK),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, INK),
        ("LINEBELOW", (0, 1), (-1, -2), 0.2, RULE),
        ("LINEBELOW", (0, -1), (-1, -1), 0.6, INK),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 12),
        ("TOPPADDING", (0, 0), (-1, 0), 4),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 14),
        ("TOPPADDING", (0, 1), (-1, -1), 14),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    return t


# ---------------------------------------------------------------------------
# Story (the actual content)
# ---------------------------------------------------------------------------

def _build_story(styles: dict[str, ParagraphStyle], f: dict[str, str]) -> list:
    s: list = []

    # ======================================================================
    # Cover page
    # ======================================================================
    s.append(Spacer(1, 84 * mm))
    s.append(Paragraph("AIXIS AUDIT METHODOLOGY", styles["cover_kicker"]))
    s.append(
        Paragraph(
            "監査方法論書",
            styles["cover_title"],
        )
    )
    s.append(
        Paragraph(
            "独立系AI監査機関 Aixis が用いる<br/>"
            "評価フレームワーク、採点ロジック、品質保証プロセスの全容。",
            styles["cover_sub"],
        )
    )
    s.append(Spacer(1, 44 * mm))

    # Cover meta grid
    meta = [
        [
            Paragraph("METHODOLOGY", styles["cover_meta_label"]),
            Paragraph("PUBLISHED", styles["cover_meta_label"]),
            Paragraph("PUBLISHER", styles["cover_meta_label"]),
        ],
        [
            Paragraph(METHODOLOGY_VERSION, styles["cover_meta_value"]),
            Paragraph(_format_jp_date(PUBLISHED_ON), styles["cover_meta_value"]),
            Paragraph("株式会社Aixis", styles["cover_meta_value"]),
        ],
    ]
    meta_tbl = Table(meta, colWidths=[50 * mm, 50 * mm, 50 * mm], hAlign="LEFT")
    meta_tbl.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
        ("TOPPADDING", (0, 1), (-1, 1), 0),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    s.append(meta_tbl)
    s.append(PageBreak())

    # ======================================================================
    # Table of contents (simple static list — page numbers kept implicit to
    # avoid a second rendering pass).
    # ======================================================================
    s.append(Paragraph("CONTENTS", styles["h1_number"]))
    s.append(Paragraph("目次", styles["h1"]))

    toc_lines = [
        "1.  はじめに — なぜ独立監査か",
        "2.  5軸評価フレームワーク",
        "3.  スコア算出ロジックと採点式",
        "4.  グレードスケールと判定基準",
        "5.  品質保証プロセス",
        "6.  監査信頼度メタ評価（BenchRisk準拠）",
        "7.  監査ライフサイクル",
        "8.  再監査サイクルと臨時トリガー",
        "9.  方法論バージョニング",
        "10.  独立性4原則",
        "11.  参考リンク",
    ]
    for line in toc_lines:
        s.append(Paragraph(line, styles["toc_row"]))
    s.append(PageBreak())

    # ======================================================================
    # 1. はじめに
    # ======================================================================
    s.append(Paragraph("SECTION 01", styles["h1_number"]))
    s.append(Paragraph("はじめに — なぜ独立監査か", styles["h1"]))

    s.append(Paragraph(
        "国内のAIツール市場では、ベンダー自身による発信、有償レビューサイト、"
        "個人ブログが入り混じり、購買担当者が中立的な比較情報に到達することが"
        "難しくなっている。多くのレビューサイトは紹介手数料や広告費を主要収益と"
        "しており、「高評価を付けるほど収益が増える」構造的な利益相反を抱えている。",
        styles["body"],
    ))
    s.append(Paragraph(
        "Aixisはこの構造的課題を解決するために設計された独立系AI監査機関である。"
        "評価対象ベンダーから紹介手数料、広告費、掲載料、スポンサー費用を含む"
        "いかなる名目の報酬も一切受け取らないことを公開ポリシーとして宣言し、"
        "収益源を利用者組織からのサブスクリプション契約および個別監査レポートの"
        "販売のみに限定している。",
        styles["body"],
    ))
    s.append(Paragraph(
        "本書は、platform.aixis.jp で公開している監査プロトコル、監査プロセス、"
        "透明性ポリシー、独立性宣言、スコア改訂履歴の各ページに記載された内容を、"
        "単一の参照文書として再構成したものである。購買担当者・情報システム部門・"
        "法務部門のそれぞれが社内稟議やベンダー選定資料の一次資料として引用できる"
        "ことを意図している。",
        styles["body"],
    ))
    s.append(Paragraph(
        "本書に記載されている数値・手順・ポリシーはすべて platform.aixis.jp 上で"
        "公開されている内容と一致する。相違がある場合は、常にウェブ上の最新ページを"
        "正として扱う。",
        styles["body"],
    ))

    # ======================================================================
    # 2. Five axes
    # ======================================================================
    s.append(Paragraph("SECTION 02", styles["h1_number"]))
    s.append(Paragraph("5軸評価フレームワーク", styles["h1"]))

    s.append(Paragraph(
        "Aixisはすべての監査対象ツールを、実務適性・費用対効果・日本語能力・"
        "信頼性/安全性・革新性の5つの独立した評価軸で採点する。各軸は0.0から"
        "5.0までの連続スコアを持ち、標準の総合スコアでは5軸を均等に扱う。",
        styles["body"],
    ))
    s.append(Spacer(1, 6 * mm))
    s.append(KeepTogether(_axis_table(f, styles)))
    s.append(Paragraph(
        "業界別ランキングでは、ユースケースに応じた重み付けが適用される場合が"
        "ある。重み付けが適用される場合は、該当ランキングページ上でその旨を"
        "明示する。",
        styles["caption"],
    ))

    # ======================================================================
    # 3. Scoring formula
    # ======================================================================
    s.append(Paragraph("SECTION 03", styles["h1_number"]))
    s.append(Paragraph("スコア算出ロジックと採点式", styles["h1"]))

    s.append(Paragraph(
        "各評価軸のスコアは、実環境での自動テスト結果と、AI定性評価による加重"
        "平均で算出する。自動テストと定性評価の比率は固定で、それぞれ60%と"
        "40%である。両者はいずれも0.0から5.0の範囲に正規化されたうえで合成"
        "される。",
        styles["body"],
    ))
    s.append(Paragraph("3.1  軸スコア", styles["h2"]))
    s.append(Paragraph(
        "軸スコア = 自動テスト結果 × 0.6 + AI定性評価 × 0.4",
        styles["formula"],
    ))
    s.append(Paragraph(
        "自動テストは実環境でのプロトコル実行により、応答速度・成功率・UI操作性・"
        "日本語処理精度などの定量データを取得する。定性評価は主としてLLMによる"
        "自動評価を基盤とし、必要に応じて手動チェックリストで補完する。",
        styles["body"],
    ))
    s.append(Paragraph("3.2  総合スコア", styles["h2"]))
    s.append(Paragraph(
        "総合スコア = (軸1 + 軸2 + 軸3 + 軸4 + 軸5) ÷ 5",
        styles["formula"],
    ))
    s.append(Paragraph(
        "5軸の算術平均が総合スコアとなる。総合スコアは第4節で定めるグレード"
        "スケールに従い、S〜Dの5段階にマッピングされる。",
        styles["body"],
    ))

    # ======================================================================
    # 4. Grade scale
    # ======================================================================
    s.append(Paragraph("SECTION 04", styles["h1_number"]))
    s.append(Paragraph("グレードスケールと判定基準", styles["h1"]))

    s.append(Paragraph(
        "総合スコアは次の閾値でS〜Dの5段階に区分される。いずれの閾値および"
        "判定基準も、platform.aixis.jp/audit-protocol に公開されている確定"
        "値を転載したものである。",
        styles["body"],
    ))
    s.append(Spacer(1, 6 * mm))
    s.append(KeepTogether(_grade_table(f)))
    s.append(Spacer(1, 8 * mm))
    s.append(Paragraph(
        "グレードは総合スコアだけでなく、各軸の最低スコアも考慮して判定する。"
        "特定の軸が極端に低い場合、総合スコアが高くても上位グレードに判定され"
        "ないことがある。",
        styles["body"],
    ))

    # ======================================================================
    # 5. Quality Assurance
    # ======================================================================
    s.append(Paragraph("SECTION 05", styles["h1_number"]))
    s.append(Paragraph("品質保証プロセス", styles["h1"]))

    s.append(Paragraph(
        "監査結果の品質は次の5段階の工程によって担保される。各工程は監査プロ"
        "トコル（/audit-protocol）に「品質保証プロセス」として公開されている。",
        styles["body"],
    ))
    qa_steps = [
        ("5.1  独立評価者の選定",
         "評価対象ベンダーとの利害関係がない評価者を選定する。利益相反チェック"
         "を事前に実施し、評価を開始する前段階で構造的中立性を確認する。"),
        ("5.2  ダブルチェック評価",
         "手動評価結果はLLMによる自動評価との整合性を確認する。大きな乖離が"
         "検出された場合はレビューを実施する。将来的に複数評価者体制への移行"
         "を予定している。"),
        ("5.3  自動テスト検証",
         "LLMによる評価基準（ルーブリック）は定期的に検証・改善し、評価の一貫"
         "性を確保する。"),
        ("5.4  最終レビュー",
         "シニアアナリストがすべてのスコアを最終レビューし、異常値の検出と"
         "データ整合性の確認を行う。"),
        ("5.5  公開前チェック",
         "公開直前に、スコア・判定・コメントが監査プロトコルに準拠しているか"
         "を最終確認したうえでデータベースに反映する。"),
    ]
    for head, body in qa_steps:
        s.append(Paragraph(head, styles["h2"]))
        s.append(Paragraph(body, styles["body"]))

    # ======================================================================
    # 6. Reliability meta-evaluation (BenchRisk-inspired)
    # ======================================================================
    s.append(Paragraph("SECTION 06", styles["h1_number"]))
    s.append(Paragraph("監査信頼度メタ評価", styles["h1"]))

    s.append(Paragraph(
        "Aixisは評価結果そのものの信頼性についても定量的に検証する。"
        "AVERI が提唱する BenchRisk フレームワークに着想を得た4次元の信頼度"
        "指標を各監査セッションに対して自動算出し、監査プロトコル上で公開"
        "している。",
        styles["body"],
    ))
    reliability_dims = [
        ("6.1  再現性 (Consistency)",
         "同一条件での再実行時にスコアが安定するか。応答時間の変動係数と"
         "エラー率で計測する。手動評価比率が高い軸は構造的に低くなる傾向が"
         "あるため、手動評価の必要性の根拠にもなる。"),
        ("6.2  正確性 (Correctness)",
         "評価エンジンの確信度分布と、有効なエビデンス（非エラー応答）の"
         "割合で計測する。自動スコアの信頼区間を定量化する役割を持つ。"),
        ("6.3  網羅性 (Comprehensiveness)",
         "テスト計画の完遂率とカテゴリカバー率で計測する。基本作成・構成力・"
         "日本語品質・正確性・応用機能の各カテゴリを網羅的に実行したかを"
         "評価する。"),
        ("6.4  解釈性 (Intelligibility)",
         "結果の解釈しやすさを評価する。応答データの充実度、軸スコアの詳細・"
         "強み・リスク情報の付与率で計測する。"),
    ]
    for head, body in reliability_dims:
        s.append(Paragraph(head, styles["h2"]))
        s.append(Paragraph(body, styles["body"]))

    # ======================================================================
    # 7. Audit lifecycle
    # ======================================================================
    s.append(Paragraph("SECTION 07", styles["h1_number"]))
    s.append(Paragraph("監査ライフサイクル", styles["h1"]))

    s.append(Paragraph(
        "Aixisの監査プロトコルは次の5段階で構成される。各段階の成果物は"
        "監査担当者間のレビューを経て次段階に進む。",
        styles["body"],
    ))
    steps = [
        ("01  ツール登録・選定",
         "市場のAIツールを網羅的にリストアップし、監査対象として登録する。"
         "カテゴリ分類・基本情報の収集を実施し、評価準備を整える。"),
        ("02  実環境テスト実行",
         "実際の利用環境でテストプロトコルに基づく操作を実行し、客観的な"
         "定量データを収集する。応答速度・成功率・UI操作性・日本語処理精度"
         "などを網羅する。"),
        ("03  AI品質評価",
         "定量テストで捕捉できない品質を、AI解析による自動評価を基盤とし、"
         "必要に応じて手動チェックリストでの補完評価を実施する。UX品質・"
         "ドキュメント・サポート・セキュリティ体制を精査する。"),
        ("04  スコア算出・グレーディング",
         "自動テストとAI定性評価の結果を統合し、第3節のロジックで5軸スコア"
         "を算出する。0.0〜5.0の精密スコアに基づき第4節のグレードを付与し、"
         "詳細分析レポートを作成する。"),
        ("05  データベース公開",
         "スコア・グレード・詳細分析を監査データベース(platform.aixis.jp/tools)"
         "に反映し、利用者が閲覧・比較できる状態で公開する。"),
    ]
    for head, body in steps:
        s.append(Paragraph(head, styles["h2"]))
        s.append(Paragraph(body, styles["body"]))

    # ======================================================================
    # 8. Re-audit cadence
    # ======================================================================
    s.append(Paragraph("SECTION 08", styles["h1_number"]))
    s.append(Paragraph("再監査サイクルと臨時トリガー", styles["h1"]))

    s.append(Paragraph(
        "すべての監査対象ツールは原則として90日サイクルで定期再監査の対象と"
        "なる。再監査では前回と同一のテストケースに加え、新たに追加されたテスト"
        "ケースも適用される。",
        styles["body"],
    ))
    s.append(Paragraph(
        "定期サイクルを待たずに臨時再監査を実施するトリガーとして、AIモデルの"
        "大幅な変更（基盤モデルの切り替え等）、重大なセキュリティインシデント、"
        "料金体系・ポリシーの大幅な変更が挙げられる。",
        styles["body"],
    ))
    s.append(Paragraph(
        "再監査の対象となる方法論の変更は、スコア改訂履歴ページ"
        "(platform.aixis.jp/score-changelog) に時系列で公開される。各リビジョン"
        "で何が変わり、どの範囲が再監査対象となったかを、改訂と同時に明示する。",
        styles["body"],
    ))

    # ======================================================================
    # 9. Versioning
    # ======================================================================
    s.append(Paragraph("SECTION 09", styles["h1_number"]))
    s.append(Paragraph("方法論バージョニング", styles["h1"]))

    s.append(Paragraph(
        "監査方法論はセマンティックバージョニングに準拠する3区分で管理される。"
        "公開されるすべてのスコアには、算出に用いた方法論バージョンが明示される。",
        styles["body"],
    ))
    s.append(_bullet(
        "<b>Major</b> &nbsp;— 評価軸の追加・削除、配分比率の大幅変更。全ツールが"
        "再監査対象となる。", styles))
    s.append(_bullet(
        "<b>Minor</b> &nbsp;— 計測項目の追加・テストプロトコルの改善。該当カテゴリ"
        "のみが再監査対象となる。", styles))
    s.append(_bullet(
        "<b>Patch</b> &nbsp;— 表記揺れ・文言の整理・誤記訂正。スコアへの影響は"
        "ない。", styles))
    s.append(Spacer(1, 4))
    s.append(Paragraph(
        "スコア改訂履歴ページは追記専用（append-only）で運用される。過去"
        "エントリの内容は、訂正注記を伴う形でのみ更新される。",
        styles["body"],
    ))

    # ======================================================================
    # 10. Independence
    # ======================================================================
    s.append(Paragraph("SECTION 10", styles["h1_number"]))
    s.append(Paragraph("独立性4原則", styles["h1"]))

    s.append(Paragraph(
        "Aixisは監査の独立性を制度的に担保するために、以下の4原則を"
        "透明性ポリシー(platform.aixis.jp/transparency)として公開している。",
        styles["body"],
    ))

    s.append(Paragraph("10.1  ベンダーからの報酬受領の完全禁止", styles["h2"]))
    s.append(Paragraph(
        "紹介手数料、広告費、掲載料、スポンサーシップその他いかなる名目に"
        "おいても、評価対象ベンダーからの報酬を受け取らない。",
        styles["body"],
    ))

    s.append(Paragraph("10.2  収益源の限定と公開", styles["h2"]))
    s.append(Paragraph(
        "収益は利用者組織からのサブスクリプション契約および個別監査レポート"
        "の販売に限定される。収益モデルは常に公開され、変更がある場合は"
        "事前に告知する。",
        styles["body"],
    ))

    s.append(Paragraph("10.3  掲載順序の中立性", styles["h2"]))
    s.append(Paragraph(
        "ツールの掲載順序はスコアに基づいてのみ決定される。有料枠、優先掲載、"
        "スポンサード表示は一切存在しない。",
        styles["body"],
    ))

    s.append(Paragraph("10.4  評価者の利益相反チェック", styles["h2"]))
    s.append(Paragraph(
        "手動評価を行う評価者は、評価対象ベンダーとの利害関係がないことを"
        "事前に申告・確認する。",
        styles["body"],
    ))

    # ======================================================================
    # 11. References
    # ======================================================================
    s.append(Paragraph("SECTION 11", styles["h1_number"]))
    s.append(Paragraph("参考リンク", styles["h1"]))

    s.append(Paragraph(
        "本書の各章は、以下のページの公開内容を出典としている。",
        styles["body"],
    ))
    refs = [
        ("監査プロセス", "platform.aixis.jp/audit-process"),
        ("監査プロトコル詳細", "platform.aixis.jp/audit-protocol"),
        ("透明性ポリシー", "platform.aixis.jp/transparency"),
        ("独立性宣言", "platform.aixis.jp/independence"),
        ("スコア改訂履歴", "platform.aixis.jp/score-changelog"),
        ("よくある質問", "platform.aixis.jp/faq"),
        ("監査データベース", "platform.aixis.jp/tools"),
        ("コーポレートサイト", "aixis.jp"),
    ]
    for label, url in refs:
        s.append(_bullet(f"<b>{label}</b> &nbsp;— {url}", styles))

    s.append(Spacer(1, 14))
    s.append(Paragraph(
        "本書の内容についてのお問い合わせ先：<br/>"
        "株式会社Aixis &nbsp; info@aixis.jp &nbsp;·&nbsp; platform.aixis.jp/contact",
        styles["caption"],
    ))

    return s


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    f = _register_fonts()
    styles = _make_styles(f)

    doc = BaseDocTemplate(
        str(OUT_PATH),
        pagesize=A4,
        leftMargin=PAGE_MARGIN,
        rightMargin=PAGE_MARGIN,
        topMargin=34 * mm,
        bottomMargin=34 * mm,
        title="Aixis 監査方法論書 — Audit Methodology",
        author="株式会社Aixis",
        subject=f"Aixis Audit Methodology {METHODOLOGY_VERSION}",
        creator="scripts/build_whitepaper.py",
        keywords="Aixis, AI監査, 独立監査, 5軸評価, 監査方法論書, methodology",
    )

    # Cover frame: sits comfortably between the top wordmark and the
    # bottom meta strip with generous breathing room on both ends.
    cover_frame = Frame(
        PAGE_MARGIN, 34 * mm,
        A4[0] - 2 * PAGE_MARGIN, A4[1] - 68 * mm,
        showBoundary=0,
        leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0,
    )
    # Body frame: leaves generous clearance between the running header/footer
    # hairlines and the text column so the page reads like a proper monograph.
    body_frame = Frame(
        PAGE_MARGIN, 34 * mm,
        A4[0] - 2 * PAGE_MARGIN, A4[1] - 68 * mm,
        showBoundary=0,
        leftPadding=0, rightPadding=0, topPadding=10, bottomPadding=10,
    )

    doc.addPageTemplates([
        PageTemplate(
            id="cover",
            frames=[cover_frame],
            onPage=lambda c, d: _draw_cover(c, d, f),
        ),
        PageTemplate(
            id="body",
            frames=[body_frame],
            onPage=lambda c, d: _draw_body(c, d, f),
        ),
    ])

    story = _build_story(styles, f)
    doc.build(story)

    size = OUT_PATH.stat().st_size
    print(f"wrote {OUT_PATH.relative_to(REPO_ROOT)} ({size:,} bytes)")


if __name__ == "__main__":
    main()
