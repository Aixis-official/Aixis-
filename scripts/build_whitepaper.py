"""Phase D-2: Build the Aixis audit whitepaper PDF.

Run once when the audit methodology version changes:

    .venv/bin/python scripts/build_whitepaper.py

Output: src/aixis_web/static/pdf/aixis-audit-whitepaper.pdf

This is a build-time script. ReportLab is not a runtime dependency of the
web app — install it locally when regenerating the whitepaper:

    .venv/bin/python -m pip install reportlab
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from reportlab.lib.colors import HexColor, black
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
FONT_DIR = REPO_ROOT / "src" / "aixis_web" / "static" / "fonts"
OUT_DIR = REPO_ROOT / "src" / "aixis_web" / "static" / "pdf"
OUT_PATH = OUT_DIR / "aixis-audit-whitepaper.pdf"

# Methodology version printed on the cover and in the running footer.
METHODOLOGY_VERSION = "v1.1.0"
PUBLISHED_ON = date(2026, 4, 1)

INK = HexColor("#0f172a")  # slate-900
INK_DIM = HexColor("#475569")  # slate-600
RULE = HexColor("#cbd5e1")  # slate-300
ACCENT = HexColor("#0ea5e9")  # sky-500


def _register_fonts() -> tuple[str, str]:
    pdfmetrics.registerFont(
        TTFont("NotoSansJP", str(FONT_DIR / "NotoSansJP-Medium.ttf"))
    )
    pdfmetrics.registerFont(
        TTFont("NotoSansJP-Bold", str(FONT_DIR / "NotoSansJP-Bold.ttf"))
    )
    return "NotoSansJP", "NotoSansJP-Bold"


def _styles(body_font: str, bold_font: str) -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()["Normal"]
    return {
        "cover_kicker": ParagraphStyle(
            "cover_kicker",
            parent=base,
            fontName=bold_font,
            fontSize=10,
            textColor=INK_DIM,
            leading=14,
            alignment=TA_LEFT,
            spaceAfter=8,
        ),
        "cover_title": ParagraphStyle(
            "cover_title",
            parent=base,
            fontName=bold_font,
            fontSize=28,
            leading=36,
            textColor=INK,
            alignment=TA_LEFT,
            spaceAfter=14,
        ),
        "cover_sub": ParagraphStyle(
            "cover_sub",
            parent=base,
            fontName=body_font,
            fontSize=12,
            leading=20,
            textColor=INK_DIM,
            alignment=TA_LEFT,
            spaceAfter=24,
        ),
        "cover_meta": ParagraphStyle(
            "cover_meta",
            parent=base,
            fontName=body_font,
            fontSize=9,
            leading=14,
            textColor=INK_DIM,
            alignment=TA_LEFT,
        ),
        "h1": ParagraphStyle(
            "h1",
            parent=base,
            fontName=bold_font,
            fontSize=16,
            leading=24,
            textColor=INK,
            spaceBefore=18,
            spaceAfter=10,
        ),
        "h2": ParagraphStyle(
            "h2",
            parent=base,
            fontName=bold_font,
            fontSize=12,
            leading=18,
            textColor=INK,
            spaceBefore=12,
            spaceAfter=6,
        ),
        "body": ParagraphStyle(
            "body",
            parent=base,
            fontName=body_font,
            fontSize=10,
            leading=18,
            textColor=INK,
            alignment=TA_JUSTIFY,
            spaceAfter=8,
        ),
        "bullet": ParagraphStyle(
            "bullet",
            parent=base,
            fontName=body_font,
            fontSize=10,
            leading=18,
            textColor=INK,
            leftIndent=14,
            bulletIndent=2,
            spaceAfter=4,
        ),
        "caption": ParagraphStyle(
            "caption",
            parent=base,
            fontName=body_font,
            fontSize=8,
            leading=12,
            textColor=INK_DIM,
            alignment=TA_LEFT,
            spaceAfter=10,
        ),
    }


def _draw_chrome(canvas, doc, body_font: str, bold_font: str) -> None:
    """Header rule and footer (page number + version) on every non-cover page."""
    width, height = A4
    canvas.saveState()
    # Top rule
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.4)
    canvas.line(20 * mm, height - 18 * mm, width - 20 * mm, height - 18 * mm)
    canvas.setFont(bold_font, 8)
    canvas.setFillColor(INK_DIM)
    canvas.drawString(20 * mm, height - 14 * mm, "AIXIS")
    canvas.setFont(body_font, 8)
    canvas.drawRightString(
        width - 20 * mm,
        height - 14 * mm,
        f"監査ホワイトペーパー  /  Methodology {METHODOLOGY_VERSION}",
    )
    # Footer rule + page number
    canvas.line(20 * mm, 18 * mm, width - 20 * mm, 18 * mm)
    canvas.setFont(body_font, 8)
    canvas.drawString(
        20 * mm,
        12 * mm,
        f"© 2026 Aixis Inc.  /  Published {PUBLISHED_ON.isoformat()}",
    )
    canvas.drawRightString(
        width - 20 * mm, 12 * mm, f"p. {doc.page - 1}"
    )
    canvas.restoreState()


def _draw_cover(canvas, doc, body_font: str, bold_font: str) -> None:
    """Cover page chrome — no header/footer rules, just a brand band."""
    width, height = A4
    canvas.saveState()
    # Top accent bar
    canvas.setFillColor(INK)
    canvas.rect(0, height - 12 * mm, width, 12 * mm, stroke=0, fill=1)
    canvas.setFont(bold_font, 10)
    canvas.setFillColor(HexColor("#ffffff"))
    canvas.drawString(20 * mm, height - 8.5 * mm, "AIXIS  /  独立系AI監査プラットフォーム")
    # Footer band
    canvas.setFillColor(INK)
    canvas.rect(0, 0, width, 14 * mm, stroke=0, fill=1)
    canvas.setFont(body_font, 8)
    canvas.setFillColor(HexColor("#cbd5e1"))
    canvas.drawString(
        20 * mm,
        5 * mm,
        f"Methodology {METHODOLOGY_VERSION}  /  発行 {PUBLISHED_ON.isoformat()}",
    )
    canvas.drawRightString(
        width - 20 * mm, 5 * mm, "platform.aixis.jp"
    )
    canvas.restoreState()


def _bullet(text: str, styles: dict[str, ParagraphStyle]) -> Paragraph:
    return Paragraph(f"•&nbsp;&nbsp;{text}", styles["bullet"])


def _build_story(styles: dict[str, ParagraphStyle], body_font: str, bold_font: str) -> list:
    story: list = []

    # ===== Cover =====
    story.append(Spacer(1, 60 * mm))
    story.append(Paragraph("AIXIS AUDIT METHODOLOGY", styles["cover_kicker"]))
    story.append(
        Paragraph(
            "AIツール監査<br/>ホワイトペーパー",
            styles["cover_title"],
        )
    )
    story.append(
        Paragraph(
            "5軸の独立評価で、AI導入の意思決定をデータに変える。<br/>"
            "ベンダーから報酬を受け取らない、再現可能な監査プロトコル。",
            styles["cover_sub"],
        )
    )
    story.append(Spacer(1, 30 * mm))
    story.append(
        Paragraph(
            f"Methodology Version &nbsp;&nbsp;<b>{METHODOLOGY_VERSION}</b><br/>"
            f"発行日 &nbsp;&nbsp;{PUBLISHED_ON.isoformat()}<br/>"
            "発行 &nbsp;&nbsp;株式会社Aixis<br/>"
            "URL &nbsp;&nbsp;platform.aixis.jp",
            styles["cover_meta"],
        )
    )
    story.append(PageBreak())

    # ===== 1. Why independent =====
    story.append(Paragraph("1.  独立監査が必要な理由", styles["h1"]))
    story.append(
        Paragraph(
            "国内のAIツール市場は、ベンダー自身による発信、有償レビューサイト、"
            "個人ブログが入り混じり、購買担当者が中立的な比較情報にたどり着くこと"
            "が困難になっている。Aixisは、ベンダーから一切の金銭・成果報酬・"
            "アフィリエイト収益を受け取らないことを契約レベルで宣言した独立監査"
            "プラットフォームである。",
            styles["body"],
        )
    )
    story.append(
        Paragraph(
            "私たちの収益源は、購買側企業が支払うサブスクリプションおよび個別"
            "コンサルティング料金のみで構成される。これにより、不利な評価を"
            "公開しても利益相反が生じない構造を制度的に担保している。",
            styles["body"],
        )
    )

    story.append(Paragraph("1.1  本ドキュメントの位置付け", styles["h2"]))
    story.append(
        Paragraph(
            "本ホワイトペーパーは、Aixisが採用する5軸監査プロトコルの全体像、"
            "スコア算出ロジック、再監査サイクル、ガバナンス体制を、購買担当者・"
            "情報システム部門・法務部門が同時に確認できる単一参照文書として整理"
            "したものである。社内稟議資料への引用は無償で許諾する。",
            styles["body"],
        )
    )

    # ===== 2. Five axes =====
    story.append(Paragraph("2.  5軸評価フレームワーク", styles["h1"]))
    story.append(
        Paragraph(
            "Aixisは、すべての監査対象ツールを以下の5つの独立した評価軸で採点する。"
            "各軸は0.0〜5.0のスコアを持ち、5軸の重み付き平均がそのツールの総合"
            "スコアを構成する。",
            styles["body"],
        )
    )

    axes_data = [
        ["軸", "正式名", "評価対象", "重み"],
        ["01", "実用性 (Practicality)", "実務での到達点・UX・統合容易性", "25%"],
        ["02", "コストパフォーマンス", "価格透明性・無償枠・ROI", "20%"],
        ["03", "日本語対応 (Localization)", "UI訳・敬語・文書・サポート", "20%"],
        ["04", "安全性 (Safety)", "セキュリティ・個人情報保護法対応・監査ログ", "20%"],
        ["05", "独自性 (Uniqueness)", "差別化技術・ロードマップ・依存リスク", "15%"],
    ]
    axes_table = Table(
        axes_data,
        colWidths=[12 * mm, 42 * mm, 80 * mm, 18 * mm],
        hAlign="LEFT",
    )
    axes_table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, 0), bold_font),
                ("FONTNAME", (0, 1), (-1, -1), body_font),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("LEADING", (0, 0), (-1, -1), 14),
                ("TEXTCOLOR", (0, 0), (-1, 0), INK),
                ("TEXTCOLOR", (0, 1), (-1, -1), INK),
                ("LINEBELOW", (0, 0), (-1, 0), 0.6, INK),
                ("LINEBELOW", (0, 1), (-1, -2), 0.2, RULE),
                ("LINEBELOW", (0, -1), (-1, -1), 0.6, INK),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    story.append(axes_table)
    story.append(Spacer(1, 6))
    story.append(
        Paragraph(
            "重みは方法論バージョンに紐づいて公開され、変更時は再監査がトリガー"
            "される (§5参照)。",
            styles["caption"],
        )
    )

    # ===== 3. Scoring formula =====
    story.append(Paragraph("3.  スコア算出ロジック", styles["h1"]))
    story.append(
        Paragraph(
            "各軸のスコアは、定量テスト60%・定性分析40%の固定比率で合成される。"
            "定量テストは実環境で実行されるテストプロトコルにより、応答時間・成功率・"
            "UI挙動・日本語処理を機械的に測定する。定性分析はAIによる一次評価と、"
            "監査担当者によるチェックリスト照合を組み合わせて算出する。",
            styles["body"],
        )
    )
    story.append(Paragraph("3.1  軸スコア", styles["h2"]))
    story.append(
        Paragraph(
            "<b>axis_score = 0.6 × quantitative + 0.4 × qualitative</b><br/>"
            "両項とも0.0〜5.0に正規化されたあと合成される。",
            styles["body"],
        )
    )
    story.append(Paragraph("3.2  総合スコアとグレード", styles["h2"]))
    story.append(
        Paragraph(
            "5軸のスコアは§2の重みで加重平均され、総合スコア (0.0〜5.0) が得られる。"
            "総合スコアはS〜Dの5段階グレードに次のようにマッピングされる。",
            styles["body"],
        )
    )
    grade_data = [
        ["グレード", "総合スコア", "意味"],
        ["S", "4.5 〜 5.0", "業界水準を明確に超える"],
        ["A", "4.0 〜 4.4", "実務利用に十分耐える"],
        ["B", "3.0 〜 3.9", "条件付きで採用可"],
        ["C", "2.0 〜 2.9", "用途を限定すべき"],
        ["D", "0.0 〜 1.9", "現状では推奨しない"],
    ]
    grade_table = Table(
        grade_data,
        colWidths=[24 * mm, 38 * mm, 90 * mm],
        hAlign="LEFT",
    )
    grade_table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, 0), bold_font),
                ("FONTNAME", (0, 1), (-1, -1), body_font),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("LEADING", (0, 0), (-1, -1), 14),
                ("LINEBELOW", (0, 0), (-1, 0), 0.6, INK),
                ("LINEBELOW", (0, 1), (-1, -2), 0.2, RULE),
                ("LINEBELOW", (0, -1), (-1, -1), 0.6, INK),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story.append(grade_table)

    # ===== 4. Audit lifecycle =====
    story.append(PageBreak())
    story.append(Paragraph("4.  監査ライフサイクル", styles["h1"]))
    story.append(
        Paragraph(
            "Aixisの監査プロトコルは以下の5段階で構成される。各段階の成果物は"
            "監査担当者間のレビューを経て次段階に進む。",
            styles["body"],
        )
    )
    story.append(_bullet("<b>01 ツール登録・選定</b> ─ 市場全数調査・カテゴリ分類・基礎情報の収集", styles))
    story.append(_bullet("<b>02 実環境テスト</b> ─ 公開済みテストプロトコルによる定量計測 (応答時間・成功率・日本語処理)", styles))
    story.append(_bullet("<b>03 AI品質分析</b> ─ UX・ドキュメント・サポート・セキュリティの定性評価", styles))
    story.append(_bullet("<b>04 スコア算出・グレード付与</b> ─ §3のロジックに基づき5軸スコアと総合グレードを決定", styles))
    story.append(_bullet("<b>05 データベース公開</b> ─ 公開監査データベースへの掲載と通知", styles))
    story.append(Spacer(1, 6))
    story.append(
        Paragraph(
            "再監査は90日サイクルを基本とし、対象ツールのメジャーバージョンアップ"
            "またはセキュリティインシデントが発生した場合は臨時で再監査が実行される。",
            styles["body"],
        )
    )

    # ===== 5. Methodology versioning =====
    story.append(Paragraph("5.  方法論バージョニング", styles["h1"]))
    story.append(
        Paragraph(
            "監査方法論はセマンティックバージョニングに準拠する形で管理される。"
            "すべての公開スコアには、算出に用いた方法論バージョンが明示される。",
            styles["body"],
        )
    )
    story.append(_bullet("<b>Major</b> ─ 軸の追加・削除、重み配分の変更。全対象ツールの全面再監査をトリガー", styles))
    story.append(_bullet("<b>Minor</b> ─ チェックリスト項目の追加・閾値調整。該当カテゴリのみ再監査", styles))
    story.append(_bullet("<b>Patch</b> ─ 文言修正・記載誤り訂正。スコアへの影響なし", styles))
    story.append(Spacer(1, 4))
    story.append(
        Paragraph(
            "改訂履歴の全文は <b>platform.aixis.jp/score-changelog</b> で公開されている。",
            styles["body"],
        )
    )

    # ===== 6. Governance =====
    story.append(Paragraph("6.  ガバナンスと利益相反方針", styles["h1"]))
    story.append(
        Paragraph(
            "Aixisは、監査の独立性を制度的に担保するために以下の方針を定める。"
            "これらの方針は、購買側企業との契約条項にも明記される。",
            styles["body"],
        )
    )
    story.append(_bullet("ベンダーからの広告掲載料・紹介手数料・スポンサーシップを一切受領しない", styles))
    story.append(_bullet("監査担当者個人による監査対象ツールベンダーの株式保有を禁止する", styles))
    story.append(_bullet("ベンダーからの掲載差止要請には、事実誤認の指摘以外応じない", styles))
    story.append(_bullet("購読者から不利な評価を変更するよう要請があっても応じない", styles))
    story.append(_bullet("監査担当者と当該ベンダー出身者との個人的関係は事前申告を必須とする", styles))
    story.append(Spacer(1, 4))
    story.append(
        Paragraph(
            "ベンダーからの異議申立フローの詳細は、公開FAQ "
            "(<b>platform.aixis.jp/faq</b>) を参照のこと。",
            styles["body"],
        )
    )

    # ===== 7. References =====
    story.append(Paragraph("7.  参考リンク", styles["h1"]))
    story.append(_bullet("監査プロセス: platform.aixis.jp/audit-process", styles))
    story.append(_bullet("監査プロトコル詳細: platform.aixis.jp/audit-protocol", styles))
    story.append(_bullet("スコア改訂履歴: platform.aixis.jp/score-changelog", styles))
    story.append(_bullet("監査データベース: platform.aixis.jp/tools", styles))
    story.append(_bullet("FAQ: platform.aixis.jp/faq", styles))
    story.append(_bullet("お問い合わせ: platform.aixis.jp/contact", styles))

    return story


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    body_font, bold_font = _register_fonts()
    styles = _styles(body_font, bold_font)

    doc = BaseDocTemplate(
        str(OUT_PATH),
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=22 * mm,
        bottomMargin=22 * mm,
        title="Aixis 監査ホワイトペーパー",
        author="Aixis Inc.",
        subject=f"AIツール監査メソドロジー {METHODOLOGY_VERSION}",
        creator="scripts/build_whitepaper.py",
    )

    cover_frame = Frame(
        20 * mm, 14 * mm,
        A4[0] - 40 * mm, A4[1] - 26 * mm,
        showBoundary=0,
        leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0,
    )
    body_frame = Frame(
        20 * mm, 22 * mm,
        A4[0] - 40 * mm, A4[1] - 44 * mm,
        showBoundary=0,
        leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0,
    )

    doc.addPageTemplates(
        [
            PageTemplate(
                id="cover",
                frames=[cover_frame],
                onPage=lambda c, d: _draw_cover(c, d, body_font, bold_font),
            ),
            PageTemplate(
                id="body",
                frames=[body_frame],
                onPage=lambda c, d: _draw_chrome(c, d, body_font, bold_font),
            ),
        ]
    )

    story = _build_story(styles, body_font, bold_font)
    doc.build(story)
    print(f"wrote {OUT_PATH} ({OUT_PATH.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
