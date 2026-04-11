"""Phase D-2: Build the Aixis audit whitepaper PDF.

Regenerate whenever the audit methodology version or site content changes:

    .venv/bin/python -m pip install reportlab   # first time only
    .venv/bin/python scripts/build_whitepaper.py

Output → src/aixis_web/static/pdf/aixis-audit-whitepaper.pdf

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
OUT_PATH = OUT_DIR / "aixis-audit-whitepaper.pdf"

# Methodology version and publication date — mirrors the public
# /score-changelog page (latest published minor version).
METHODOLOGY_VERSION = "v1.1.0"
PUBLISHED_ON = date(2026, 4, 1)

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
            textColor=INK_DIM, alignment=TA_LEFT, spaceAfter=10,
            # generous letter-spacing via a trailing character trick isn't
            # available in ReportLab, so we rely on the uppercase Latin
            # kicker for visual contrast with the serif title below.
        ),
        "cover_title": ParagraphStyle(
            "cover_title", parent=base,
            fontName=f["serif_bold"], fontSize=30, leading=42,
            textColor=INK, alignment=TA_LEFT, spaceAfter=14,
        ),
        "cover_sub": ParagraphStyle(
            "cover_sub", parent=base,
            fontName=f["serif"], fontSize=11.5, leading=22,
            textColor=INK_DIM, alignment=TA_LEFT, spaceAfter=24,
        ),
        "cover_meta_label": ParagraphStyle(
            "cover_meta_label", parent=base,
            fontName=f["sans_bold"], fontSize=7, leading=12,
            textColor=INK_MUTED, alignment=TA_LEFT, spaceAfter=0,
        ),
        "cover_meta_value": ParagraphStyle(
            "cover_meta_value", parent=base,
            fontName=f["serif"], fontSize=11, leading=18,
            textColor=INK, alignment=TA_LEFT, spaceAfter=4,
        ),
        # Running body
        "h1_number": ParagraphStyle(
            "h1_number", parent=base,
            fontName=f["sans_bold"], fontSize=8, leading=12,
            textColor=INK_MUTED, spaceBefore=16, spaceAfter=2,
        ),
        "h1": ParagraphStyle(
            "h1", parent=base,
            fontName=f["serif_bold"], fontSize=17, leading=26,
            textColor=INK, spaceBefore=0, spaceAfter=12, keepWithNext=True,
        ),
        "h2": ParagraphStyle(
            "h2", parent=base,
            fontName=f["serif_bold"], fontSize=12, leading=20,
            textColor=INK, spaceBefore=14, spaceAfter=6, keepWithNext=True,
        ),
        "body": ParagraphStyle(
            "body", parent=base,
            fontName=f["serif"], fontSize=10.5, leading=19,
            textColor=INK, alignment=TA_JUSTIFY, spaceAfter=9,
            firstLineIndent=0,
        ),
        "quote": ParagraphStyle(
            "quote", parent=base,
            fontName=f["serif"], fontSize=10, leading=18,
            textColor=INK_DIM, alignment=TA_LEFT, spaceAfter=9,
            leftIndent=14, rightIndent=14,
            borderPadding=(0, 0, 0, 0),
        ),
        "bullet": ParagraphStyle(
            "bullet", parent=base,
            fontName=f["serif"], fontSize=10.5, leading=19,
            textColor=INK, alignment=TA_LEFT, spaceAfter=4,
            leftIndent=16, firstLineIndent=-10,
        ),
        "formula": ParagraphStyle(
            "formula", parent=base,
            fontName=f["sans"], fontSize=10, leading=18,
            textColor=INK, alignment=TA_LEFT, spaceAfter=9,
            leftIndent=14,
        ),
        "caption": ParagraphStyle(
            "caption", parent=base,
            fontName=f["sans"], fontSize=7.5, leading=12,
            textColor=INK_MUTED, alignment=TA_LEFT, spaceBefore=4, spaceAfter=14,
        ),
        "toc_row": ParagraphStyle(
            "toc_row", parent=base,
            fontName=f["serif"], fontSize=10.5, leading=22,
            textColor=INK, alignment=TA_LEFT,
        ),
    }


# ---------------------------------------------------------------------------
# Page chrome
# ---------------------------------------------------------------------------

def _draw_body(canvas, doc, f: dict[str, str]) -> None:
    """Running header + footer for every non-cover page."""
    width, height = A4
    canvas.saveState()

    # Top hairline with tiny brand mark on the left and context on the right.
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.3)
    canvas.line(25 * mm, height - 20 * mm, width - 25 * mm, height - 20 * mm)

    canvas.setFont(f["sans_bold"], 7.5)
    canvas.setFillColor(INK_MUTED)
    canvas.drawString(25 * mm, height - 15 * mm, "AIXIS")
    canvas.setFont(f["sans"], 7.5)
    canvas.drawRightString(
        width - 25 * mm,
        height - 15 * mm,
        f"監査ホワイトペーパー  ·  方法論 {METHODOLOGY_VERSION}",
    )

    # Bottom hairline + page number + copyright.
    canvas.setStrokeColor(RULE)
    canvas.line(25 * mm, 20 * mm, width - 25 * mm, 20 * mm)
    canvas.setFont(f["sans"], 7.5)
    canvas.setFillColor(INK_MUTED)
    canvas.drawString(
        25 * mm, 14 * mm,
        f"© 2026 株式会社Aixis   発行日 {PUBLISHED_ON.isoformat()}",
    )
    canvas.drawRightString(
        width - 25 * mm, 14 * mm, f"— {doc.page - 1} —"
    )
    canvas.restoreState()


def _draw_cover(canvas, doc, f: dict[str, str]) -> None:
    """Cover page — a single thin top rule, no running chrome."""
    width, height = A4
    canvas.saveState()

    # Thin serif rule near the top, flush with the text frame.
    canvas.setStrokeColor(INK)
    canvas.setLineWidth(0.6)
    canvas.line(25 * mm, height - 25 * mm, 25 * mm + 45 * mm, height - 25 * mm)

    # Tiny sans wordmark above the rule.
    canvas.setFont(f["sans_bold"], 8.5)
    canvas.setFillColor(INK)
    canvas.drawString(25 * mm, height - 21 * mm, "AIXIS")

    # Footer: a single slate-900 hairline + meta strip.
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.3)
    canvas.line(25 * mm, 25 * mm, width - 25 * mm, 25 * mm)
    canvas.setFont(f["sans"], 7.5)
    canvas.setFillColor(INK_MUTED)
    canvas.drawString(
        25 * mm, 19 * mm,
        f"方法論 {METHODOLOGY_VERSION}    発行日 {PUBLISHED_ON.isoformat()}",
    )
    canvas.drawRightString(
        width - 25 * mm, 19 * mm, "platform.aixis.jp"
    )
    canvas.restoreState()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bullet(text: str, styles) -> Paragraph:
    return Paragraph(f"・&nbsp;{text}", styles["bullet"])


def _axis_table(f, styles) -> Table:
    """5軸評価フレームワーク.

    Content from /audit-protocol.html (meta description + axis breakdown).
    Weights are *均等* (equal) per the site's explicit statement.
    """
    data = [
        ["軸", "評価対象", "重み"],
        ["実務適性",
         "実際の業務フローにおける到達点・UX・統合容易性",
         "20%"],
        ["費用対効果",
         "価格の透明性・無償枠・ROI",
         "20%"],
        ["日本語能力",
         "UI・敬語・文書・サポートの日本語品質",
         "20%"],
        ["信頼性・安全性",
         "セキュリティ・個人情報保護法対応・監査ログ",
         "20%"],
        ["革新性",
         "差別化技術・ロードマップ・依存リスク",
         "20%"],
    ]
    t = Table(
        data,
        colWidths=[32 * mm, 95 * mm, 18 * mm],
        hAlign="LEFT",
    )
    t.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), f["sans_bold"]),
        ("FONTSIZE", (0, 0), (-1, 0), 8),
        ("TEXTCOLOR", (0, 0), (-1, 0), INK_MUTED),
        ("FONTNAME", (0, 1), (0, -1), f["serif_bold"]),
        ("FONTNAME", (1, 1), (2, -1), f["serif"]),
        ("FONTSIZE", (0, 1), (-1, -1), 10),
        ("LEADING", (0, 1), (-1, -1), 16),
        ("TEXTCOLOR", (0, 1), (-1, -1), INK),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, INK),
        ("LINEBELOW", (0, 1), (-1, -2), 0.2, RULE),
        ("LINEBELOW", (0, -1), (-1, -1), 0.6, INK),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    return t


def _grade_table(f) -> Table:
    """グレードスケール. Thresholds copied verbatim from /audit-protocol."""
    data = [
        ["グレード", "総合スコア", "評価"],
        ["S", "4.5 – 5.0", "最高評価"],
        ["A", "3.8 – 4.4", "高品質・推奨"],
        ["B", "3.0 – 3.7", "標準的"],
        ["C", "2.0 – 2.9", "改善の余地あり"],
        ["D", "0.0 – 1.9", "要注意"],
    ]
    t = Table(
        data,
        colWidths=[24 * mm, 36 * mm, 85 * mm],
        hAlign="LEFT",
    )
    t.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), f["sans_bold"]),
        ("FONTSIZE", (0, 0), (-1, 0), 8),
        ("TEXTCOLOR", (0, 0), (-1, 0), INK_MUTED),
        ("FONTNAME", (0, 1), (0, -1), f["serif_bold"]),
        ("FONTNAME", (1, 1), (-1, -1), f["serif"]),
        ("FONTSIZE", (0, 1), (-1, -1), 10.5),
        ("LEADING", (0, 1), (-1, -1), 18),
        ("TEXTCOLOR", (0, 1), (-1, -1), INK),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, INK),
        ("LINEBELOW", (0, 1), (-1, -2), 0.2, RULE),
        ("LINEBELOW", (0, -1), (-1, -1), 0.6, INK),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
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
    s.append(Spacer(1, 70 * mm))
    s.append(Paragraph("AIXIS AUDIT METHODOLOGY", styles["cover_kicker"]))
    s.append(
        Paragraph(
            "AIツール監査<br/>ホワイトペーパー",
            styles["cover_title"],
        )
    )
    s.append(
        Paragraph(
            "独立監査による5軸評価で、<br/>"
            "AI導入の意思決定をデータに変える。",
            styles["cover_sub"],
        )
    )
    s.append(Spacer(1, 30 * mm))

    # Cover meta grid
    meta = [
        [
            Paragraph("METHODOLOGY", styles["cover_meta_label"]),
            Paragraph("PUBLISHED", styles["cover_meta_label"]),
            Paragraph("PUBLISHER", styles["cover_meta_label"]),
        ],
        [
            Paragraph(METHODOLOGY_VERSION, styles["cover_meta_value"]),
            Paragraph(PUBLISHED_ON.isoformat(), styles["cover_meta_value"]),
            Paragraph("株式会社Aixis", styles["cover_meta_value"]),
        ],
    ]
    meta_tbl = Table(meta, colWidths=[55 * mm, 55 * mm, 55 * mm], hAlign="LEFT")
    meta_tbl.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 3),
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
        "3.  スコア算出ロジック",
        "4.  グレードスケール",
        "5.  監査ライフサイクル",
        "6.  再監査サイクル",
        "7.  方法論バージョニング",
        "8.  独立性4原則",
        "9.  参考リンク",
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
        "国内のAIツール市場は、ベンダー自身による発信、有償レビューサイト、"
        "個人ブログが入り混じり、購買担当者が中立的な比較情報にたどり着くこと"
        "が困難になっている。Aixisは、評価対象ベンダーから一切の金銭・成果"
        "報酬・紹介手数料・広告費を受け取らないことを公開ポリシーとして"
        "宣言する独立監査プラットフォームである。",
        styles["body"],
    ))
    s.append(Paragraph(
        "本ホワイトペーパーは、platform.aixis.jp で公開されている監査プロトコル・"
        "監査プロセス・透明性ポリシー・スコア改訂履歴の各公開ページに記載された"
        "内容を、単一の参照文書として整理したものである。本書は購買担当者・"
        "情報システム部門・法務部門が同時に確認できる一次資料として、社内稟議・"
        "ベンダー選定資料への引用を想定している。",
        styles["body"],
    ))
    s.append(Paragraph(
        "本書に記載される数値・手順・ポリシーはすべて platform.aixis.jp 上で"
        "公開されている内容と一致する。相違がある場合は、常にウェブ上の"
        "最新ページを正とする。",
        styles["body"],
    ))

    # ======================================================================
    # 2. Five axes
    # ======================================================================
    s.append(Paragraph("SECTION 02", styles["h1_number"]))
    s.append(Paragraph("5軸評価フレームワーク", styles["h1"]))

    s.append(Paragraph(
        "Aixisはすべての監査対象ツールを、実務適性・費用対効果・日本語能力・"
        "信頼性/安全性・革新性の5つの独立した評価軸で採点する。各軸は"
        "0.0〜5.0のスコアを持ち、標準の総合スコアでは5軸を均等に扱う。",
        styles["body"],
    ))
    s.append(KeepTogether(_axis_table(f, styles)))
    s.append(Paragraph(
        "なお業界別ランキングでは、ユースケースに応じた重み付けが適用される"
        "場合がある。重み付けが適用される場合は、ランキングページ上でその旨を"
        "明示する。",
        styles["caption"],
    ))

    # ======================================================================
    # 3. Scoring formula
    # ======================================================================
    s.append(Paragraph("SECTION 03", styles["h1_number"]))
    s.append(Paragraph("スコア算出ロジック", styles["h1"]))

    s.append(Paragraph(
        "各評価軸のスコアは、実環境での自動テスト結果と、監査担当者による"
        "AI定性評価の加重平均で算出される。テストと定性評価の比率は固定で、"
        "それぞれ60%と40%である。",
        styles["body"],
    ))
    s.append(Paragraph("3.1  軸スコア", styles["h2"]))
    s.append(Paragraph(
        "軸スコア = 自動テスト結果 × 0.6 + AI定性評価 × 0.4",
        styles["formula"],
    ))
    s.append(Paragraph(
        "自動テスト結果・AI定性評価はいずれも0.0〜5.0の範囲に正規化された"
        "のちに合成される。",
        styles["body"],
    ))
    s.append(Paragraph("3.2  総合スコア", styles["h2"]))
    s.append(Paragraph(
        "総合スコア = (軸1 + 軸2 + 軸3 + 軸4 + 軸5) ÷ 5",
        styles["formula"],
    ))
    s.append(Paragraph(
        "5軸の算術平均が総合スコアとなる。総合スコアは第4節で定めるグレード"
        "スケールに従い、S〜Dの5段階のグレードにマッピングされる。",
        styles["body"],
    ))

    # ======================================================================
    # 4. Grade scale
    # ======================================================================
    s.append(Paragraph("SECTION 04", styles["h1_number"]))
    s.append(Paragraph("グレードスケール", styles["h1"]))

    s.append(Paragraph(
        "総合スコアは次の閾値でS〜Dの5段階に区分される。いずれの閾値も"
        "platform.aixis.jp/audit-protocol に公開されている確定値である。",
        styles["body"],
    ))
    s.append(KeepTogether(_grade_table(f)))
    s.append(Paragraph(
        "グレードは総合スコアのみならず、各軸の最低スコアも考慮して判定される。"
        "特定の軸が極端に低い場合、総合スコアが高くても上位グレードに判定され"
        "ないことがある。",
        styles["body"],
    ))

    # ======================================================================
    # 5. Audit lifecycle
    # ======================================================================
    s.append(Paragraph("SECTION 05", styles["h1_number"]))
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
    # 6. Re-audit cadence
    # ======================================================================
    s.append(Paragraph("SECTION 06", styles["h1_number"]))
    s.append(Paragraph("再監査サイクル", styles["h1"]))

    s.append(Paragraph(
        "Aixisは全ツールを原則として90日ごとに再監査し、スコアを更新する。"
        "ツールの大幅なバージョンアップや重大なセキュリティインシデントが"
        "確認された場合は、サイクルを待たず臨時再評価を実施する。",
        styles["body"],
    ))
    s.append(Paragraph(
        "再監査の対象となる方法論の変更は、スコア改訂履歴ページ"
        "(platform.aixis.jp/score-changelog)で時系列に公開される。各"
        "リビジョンで何が変わり、どの範囲が再監査対象となったかを、"
        "改訂と同時に明示する。",
        styles["body"],
    ))

    # ======================================================================
    # 7. Versioning
    # ======================================================================
    s.append(Paragraph("SECTION 07", styles["h1_number"]))
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
    # 8. Independence
    # ======================================================================
    s.append(Paragraph("SECTION 08", styles["h1_number"]))
    s.append(Paragraph("独立性4原則", styles["h1"]))

    s.append(Paragraph(
        "Aixisは監査の独立性を制度的に担保するために、以下の4原則を"
        "透明性ポリシー(platform.aixis.jp/transparency)として公開している。",
        styles["body"],
    ))

    s.append(Paragraph("8.1  ベンダーからの報酬受領の完全禁止", styles["h2"]))
    s.append(Paragraph(
        "紹介手数料、広告費、掲載料、スポンサーシップその他いかなる名目に"
        "おいても、評価対象ベンダーからの報酬を受け取らない。",
        styles["body"],
    ))

    s.append(Paragraph("8.2  収益源の限定と公開", styles["h2"]))
    s.append(Paragraph(
        "収益は利用者組織からのサブスクリプション契約および個別監査レポート"
        "の販売に限定される。収益モデルは常に公開され、変更がある場合は"
        "事前に告知する。",
        styles["body"],
    ))

    s.append(Paragraph("8.3  掲載順序の中立性", styles["h2"]))
    s.append(Paragraph(
        "ツールの掲載順序はスコアに基づいてのみ決定される。有料枠、優先掲載、"
        "スポンサード表示は一切存在しない。",
        styles["body"],
    ))

    s.append(Paragraph("8.4  評価者の利益相反チェック", styles["h2"]))
    s.append(Paragraph(
        "手動評価を行う評価者は、評価対象ベンダーとの利害関係がないことを"
        "事前に申告・確認する。",
        styles["body"],
    ))

    # ======================================================================
    # 9. References
    # ======================================================================
    s.append(Paragraph("SECTION 09", styles["h1_number"]))
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
        leftMargin=25 * mm,
        rightMargin=25 * mm,
        topMargin=25 * mm,
        bottomMargin=25 * mm,
        title="Aixis AIツール監査ホワイトペーパー",
        author="株式会社Aixis",
        subject=f"AIツール監査メソドロジー {METHODOLOGY_VERSION}",
        creator="scripts/build_whitepaper.py",
        keywords="Aixis, AI監査, 独立監査, 5軸評価, whitepaper",
    )

    cover_frame = Frame(
        25 * mm, 28 * mm,
        A4[0] - 50 * mm, A4[1] - 56 * mm,
        showBoundary=0,
        leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0,
    )
    body_frame = Frame(
        25 * mm, 24 * mm,
        A4[0] - 50 * mm, A4[1] - 48 * mm,
        showBoundary=0,
        leftPadding=0, rightPadding=0, topPadding=2, bottomPadding=2,
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
