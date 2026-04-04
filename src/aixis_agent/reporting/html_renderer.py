"""HTML report renderer with Plotly charts."""

from pathlib import Path

from jinja2 import Environment, BaseLoader

from ..core.interfaces import ReportRenderer
from ..core.models import AuditReport


# 5-axis names in canonical order
AXIS_NAMES_JP = ["実務適性", "費用対効果", "日本語能力", "信頼性・安全性", "革新性"]

REPORT_TEMPLATE = r"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Aixis 破壊的テスト監査レポート - {{ report.target_tool }}</title>
<script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
<style>
:root {
    --primary: #1a365d;
    --secondary: #2b6cb0;
    --accent: #ed8936;
    --bg: #f7fafc;
    --card-bg: #ffffff;
    --text: #2d3748;
    --text-light: #718096;
    --border: #e2e8f0;
    --success: #8BA8C4;
    --warning: #C9A84C;
    --danger: #8A5A5A;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: "Noto Serif JP", "Hiragino Sans", serif; background: var(--bg); color: var(--text); line-height: 1.7; }
.container { max-width: 1100px; margin: 0 auto; padding: 40px 24px; }
.cover { background: linear-gradient(135deg, var(--primary), var(--secondary)); color: white; padding: 60px 40px; border-radius: 12px; margin-bottom: 40px; text-align: center; }
.cover h1 { font-size: 2.2rem; margin-bottom: 8px; }
.cover .subtitle { font-size: 1.1rem; opacity: 0.9; }
.cover .grade-badge { display: inline-block; font-size: 4rem; font-weight: 900; margin: 24px 0; padding: 16px 32px; border: 4px solid rgba(255,255,255,0.5); border-radius: 16px; }
.cover .score { font-size: 1.4rem; opacity: 0.9; }
.cover .meta { margin-top: 20px; font-size: 0.9rem; opacity: 0.7; }

.card { background: var(--card-bg); border-radius: 12px; padding: 32px; margin-bottom: 24px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }
.card h2 { color: var(--primary); font-size: 1.4rem; margin-bottom: 16px; padding-bottom: 8px; border-bottom: 2px solid var(--accent); }
.card h3 { color: var(--secondary); font-size: 1.1rem; margin: 16px 0 8px; }

.summary-text { white-space: pre-line; line-height: 1.8; }
.chart-container { width: 100%; margin: 20px 0; }

table { width: 100%; border-collapse: collapse; margin: 16px 0; }
th, td { padding: 10px 14px; text-align: left; border-bottom: 1px solid var(--border); }
th { background: var(--bg); color: var(--primary); font-weight: 600; font-size: 0.9rem; }
td { font-size: 0.95rem; }
tr:hover { background: #f0f4f8; }

.tag { display: inline-block; padding: 2px 10px; border-radius: 12px; font-size: 0.8rem; font-weight: 600; }
.tag-success { background: #c6f6d5; color: #22543d; }
.tag-warning { background: #fefcbf; color: #744210; }
.tag-danger { background: #fed7d7; color: #742a2a; }
.tag-muted { background: #e2e8f0; color: #4a5568; }

.strength { color: var(--success); }
.strength::before { content: "✓ "; }
.risk { color: var(--danger); }
.risk::before { content: "⚠ "; }

.score-bar { height: 8px; border-radius: 4px; background: var(--border); overflow: hidden; }
.score-bar-fill { height: 100%; border-radius: 4px; transition: width 0.3s; }

.manual-pending { font-style: italic; color: var(--text-light); }

details { margin: 8px 0; }
details summary { cursor: pointer; padding: 8px; background: var(--bg); border-radius: 6px; font-weight: 600; }
details[open] summary { margin-bottom: 8px; }

.footer { text-align: center; padding: 40px 0 20px; color: var(--text-light); font-size: 0.85rem; }
.footer a { color: var(--secondary); text-decoration: none; }

@media print {
    .container { padding: 20px; }
    .card { break-inside: avoid; box-shadow: none; border: 1px solid var(--border); }
    .cover { break-after: page; }
}
</style>
</head>
<body>
<div class="container">

<!-- Cover -->
<div class="cover">
    <div class="meta">Aixis 独立AI検証監査レポート</div>
    <h1>破壊的テスト監査レポート</h1>
    <div class="subtitle">{{ report.target_tool }}</div>
    <div class="grade-badge">{{ report.overall_grade.value }}</div>
    <div class="score">総合スコア: {{ "%.1f"|format(report.overall_score) }} / 5.0</div>
    <div class="meta">
        監査日: {{ report.generated_at.strftime('%Y年%m月%d日') }}<br>
        テスト総数: {{ report.total_tests }}件 | レポートID: {{ report.report_id }}
    </div>
</div>

<!-- Executive Summary -->
<div class="card">
    <h2>エグゼクティブサマリー</h2>
    <div class="summary-text">{{ report.executive_summary_jp }}</div>
    {% if report.executive_summary_en %}
    <details>
        <summary>English Summary</summary>
        <div class="summary-text" style="margin-top:8px;">{{ report.executive_summary_en }}</div>
    </details>
    {% endif %}
</div>

<!-- Score Dashboard -->
<div class="card">
    <h2>総合スコアダッシュボード（5軸評価）</h2>
    <div id="radar-chart" class="chart-container"></div>
    <div id="bar-chart" class="chart-container"></div>
</div>

<!-- Per-Axis Details -->
{% for axis in report.axis_scores %}
<div class="card">
    {% if axis.confidence == 0 %}
    <h2>{{ axis.axis_name_jp }} <span class="tag tag-muted">手動評価待ち</span></h2>
    <p class="manual-pending">この軸はまだ自動評価されていません。手動評価待ちです。</p>
    {% else %}
    <h2>{{ axis.axis_name_jp }} ({{ "%.2f"|format(axis.score) }} / 5.0)</h2>

    <div class="score-bar" style="margin-bottom: 16px;">
        <div class="score-bar-fill" style="width: {{ (axis.score / 5.0 * 100)|round(1) }}%; background: {% if axis.score >= 4.0 %}var(--success){% elif axis.score >= 2.5 %}var(--warning){% else %}var(--danger){% endif %};"></div>
    </div>

    {% if axis.strengths %}
    <h3>強み</h3>
    <ul>{% for s in axis.strengths %}<li class="strength">{{ s }}</li>{% endfor %}</ul>
    {% endif %}

    {% if axis.risks %}
    <h3>リスク</h3>
    <ul>{% for r in axis.risks %}<li class="risk">{{ r }}</li>{% endfor %}</ul>
    {% endif %}

    {% if axis.details %}
    <h3>ルール別詳細</h3>
    <table>
        <tr><th>ルール</th><th>スコア</th><th>重み</th><th>重要度</th><th>詳細</th></tr>
        {% for d in axis.details %}
        <tr>
            <td>{{ d.rule_name_jp }}</td>
            <td>{{ "%.2f"|format(d.score) }} / 5.0</td>
            <td>{{ d.weight }}</td>
            <td><span class="tag {% if d.severity.value == 'critical' %}tag-danger{% elif d.severity.value == 'high' %}tag-warning{% else %}tag-success{% endif %}">{{ d.severity.value }}</span></td>
            <td>{{ d.evidence }}</td>
        </tr>
        {% endfor %}
    </table>
    {% endif %}
    {% endif %}
</div>
{% endfor %}

<!-- Category Breakdowns -->
<div class="card">
    <h2>カテゴリ別分析</h2>
    <div id="category-chart" class="chart-container"></div>
    <table>
        <tr><th>カテゴリ</th><th>テスト数</th><th>成功</th><th>失敗</th><th>エラー</th><th>成功率</th><th>平均応答時間</th></tr>
        {% for cat_key, cat in report.category_breakdowns.items() %}
        <tr>
            <td>{{ cat.category_name_jp }}</td>
            <td>{{ cat.total_tests }}</td>
            <td>{{ cat.passed_tests }}</td>
            <td>{{ cat.failed_tests }}</td>
            <td>{{ cat.error_tests }}</td>
            <td><span class="tag {% if cat.pass_rate >= 0.8 %}tag-success{% elif cat.pass_rate >= 0.5 %}tag-warning{% else %}tag-danger{% endif %}">{{ "%.0f"|format(cat.pass_rate * 100) }}%</span></td>
            <td>{{ "%.0f"|format(cat.avg_response_time_ms) }}ms</td>
        </tr>
        {% endfor %}
    </table>
</div>

<!-- Raw Data Sample -->
{% if report.raw_results %}
<div class="card">
    <h2>テスト結果サンプル</h2>
    <details>
        <summary>最初の{{ [report.raw_results|length, 20]|min }}件を表示</summary>
        <table>
            <tr><th>ID</th><th>カテゴリ</th><th>プロンプト（抜粋）</th><th>応答（抜粋）</th><th>時間</th><th>状態</th></tr>
            {% for r in report.raw_results[:20] %}
            <tr>
                <td style="font-size:0.8rem;">{{ r.test_case_id[:30] }}</td>
                <td>{{ r.category.value }}</td>
                <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{{ r.prompt_sent[:60] }}</td>
                <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{{ (r.response_raw or "N/A")[:60] }}</td>
                <td>{{ "%.0f"|format(r.response_time_ms) }}ms</td>
                <td>{% if r.error %}<span class="tag tag-danger">エラー</span>{% else %}<span class="tag tag-success">OK</span>{% endif %}</td>
            </tr>
            {% endfor %}
        </table>
    </details>
</div>
{% endif %}

<!-- Footer -->
<div class="footer">
    <p>本レポートはAixis破壊的テスト自動化エージェントにより生成されました。</p>
    <p><a href="https://aixis.jp">Aixis</a> - AI実装の投資判断を科学する、独立系AI調査・検証機関</p>
    <p>© {{ report.generated_at.strftime('%Y') }} Aixis. All rights reserved.</p>
</div>

</div>

<script>
// 5-axis canonical names
const canonicalAxes = ['実務適性', '費用対効果', '日本語能力', '信頼性・安全性', '革新性'];

// Axis data from report
const axisNames = [{% for a in report.axis_scores %}'{{ a.axis_name_jp }}',{% endfor %}];
const axisScores = [{% for a in report.axis_scores %}{{ "%.2f"|format(a.score) }},{% endfor %}];
const axisConfidences = [{% for a in report.axis_scores %}{{ a.confidence }},{% endfor %}];

// Build ordered arrays matching the 5-axis pentagon layout
const radarNames = [];
const radarScores = [];
for (const name of canonicalAxes) {
    const idx = axisNames.indexOf(name);
    radarNames.push(idx >= 0 ? name : name);
    radarScores.push(idx >= 0 ? axisScores[idx] : 0);
}

// Pentagon Radar Chart (5-point polygon, 0-5 scale)
Plotly.newPlot('radar-chart', [{
    type: 'scatterpolar',
    r: [...radarScores, radarScores[0]],
    theta: [...radarNames, radarNames[0]],
    fill: 'toself',
    fillcolor: 'rgba(43,108,176,0.2)',
    line: { color: '#2b6cb0', width: 2 },
    marker: { size: 8 },
}], {
    polar: {
        radialaxis: { visible: true, range: [0, 5], dtick: 1 },
        angularaxis: { direction: 'clockwise' },
    },
    showlegend: false,
    margin: { t: 30, b: 30, l: 60, r: 60 },
    height: 400,
}, { responsive: true });

// Bar Chart (0-5 scale) — only axes with confidence > 0
const barNames = [];
const barScores = [];
for (let i = 0; i < axisNames.length; i++) {
    if (axisConfidences[i] > 0) {
        barNames.push(axisNames[i]);
        barScores.push(axisScores[i]);
    } else {
        barNames.push(axisNames[i] + '（手動評価待ち）');
        barScores.push(0);
    }
}

Plotly.newPlot('bar-chart', [{
    type: 'bar',
    x: barScores,
    y: barNames,
    orientation: 'h',
    marker: {
        color: barScores.map((s, i) => axisConfidences[i] === 0 ? '#cbd5e0' : s >= 4.0 ? '#8BA8C4' : s >= 2.5 ? '#8A7A6B' : '#8A5A5A'),
    },
    text: barScores.map((s, i) => axisConfidences[i] === 0 ? '手動評価待ち' : s.toFixed(2)),
    textposition: 'inside',
}], {
    xaxis: { range: [0, 5], title: 'スコア (0.0–5.0)', dtick: 1 },
    margin: { t: 10, b: 40, l: 160, r: 20 },
    height: 220,
}, { responsive: true });

// Category Chart
{% if report.category_breakdowns %}
const catNames = [{% for k, c in report.category_breakdowns.items() %}'{{ c.category_name_jp }}',{% endfor %}];
const catRates = [{% for k, c in report.category_breakdowns.items() %}{{ "%.1f"|format(c.pass_rate * 100) }},{% endfor %}];

Plotly.newPlot('category-chart', [{
    type: 'bar',
    x: catNames,
    y: catRates,
    marker: {
        color: catRates.map(r => r >= 80 ? '#8BA8C4' : r >= 50 ? '#8A7A6B' : '#8A5A5A'),
    },
    text: catRates.map(r => r.toFixed(0) + '%'),
    textposition: 'outside',
}], {
    yaxis: { range: [0, 105], title: '成功率 (%)' },
    margin: { t: 20, b: 100, l: 60, r: 20 },
    height: 300,
}, { responsive: true });
{% endif %}
</script>
</body>
</html>"""


class HTMLRenderer(ReportRenderer):
    """Renders audit report as a single self-contained HTML file."""

    def render(self, report: AuditReport, output_path: Path) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        html_path = output_path.with_suffix(".html")

        env = Environment(loader=BaseLoader(), autoescape=True)
        template = env.from_string(REPORT_TEMPLATE)
        html_content = template.render(report=report)

        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_content)

        return html_path
