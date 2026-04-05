"""Trend chart renderer for score history over time."""

from pathlib import Path

from jinja2 import Environment, BaseLoader


TREND_TEMPLATE = r"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Aixis スコア推移レポート - {{ tool_name }}</title>
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
    --success: #8BB2CA;
    --warning: #DDC67D;
    --danger: #B98D8D;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: "Noto Serif JP", "Hiragino Sans", serif; background: var(--bg); color: var(--text); line-height: 1.7; }
.container { max-width: 1100px; margin: 0 auto; padding: 40px 24px; }
.cover { background: linear-gradient(135deg, var(--primary), var(--secondary)); color: white; padding: 60px 40px; border-radius: 12px; margin-bottom: 40px; text-align: center; }
.cover h1 { font-size: 2.2rem; margin-bottom: 8px; }
.cover .subtitle { font-size: 1.1rem; opacity: 0.9; }
.cover .meta { margin-top: 20px; font-size: 0.9rem; opacity: 0.7; }

.card { background: var(--card-bg); border-radius: 12px; padding: 32px; margin-bottom: 24px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }
.card h2 { color: var(--primary); font-size: 1.4rem; margin-bottom: 16px; padding-bottom: 8px; border-bottom: 2px solid var(--accent); }

.chart-container { width: 100%; margin: 20px 0; }

table { width: 100%; border-collapse: collapse; margin: 16px 0; }
th, td { padding: 10px 14px; text-align: left; border-bottom: 1px solid var(--border); }
th { background: var(--bg); color: var(--primary); font-weight: 600; font-size: 0.9rem; }
td { font-size: 0.95rem; }
tr:hover { background: #f0f4f8; }

.score-bar { height: 8px; border-radius: 4px; background: var(--border); overflow: hidden; }
.score-bar-fill { height: 100%; border-radius: 4px; }

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
    <h1>スコア推移レポート</h1>
    <div class="subtitle">{{ tool_name }}</div>
    <div class="meta">
        期間: {{ snapshots[0].date }} ~ {{ snapshots[-1].date }}<br>
        データポイント数: {{ snapshots|length }}件
    </div>
</div>

<!-- Trend Chart -->
<div class="card">
    <h2>スコア推移チャート</h2>
    <div id="trend-chart" class="chart-container"></div>
</div>

<!-- Data Table -->
<div class="card">
    <h2>スコア履歴</h2>
    <table>
        <tr>
            <th>日付</th>
            <th>総合</th>
            <th>実用性</th>
            <th>コスト効率</th>
            <th>日本語対応</th>
            <th>安全性</th>
            <th>独自性</th>
            <th>バージョン</th>
        </tr>
        {% for s in snapshots %}
        <tr>
            <td>{{ s.date }}</td>
            <td><strong>{{ "%.2f"|format(s.overall_score) }}</strong></td>
            <td>{{ "%.2f"|format(s.practicality) }}</td>
            <td>{{ "%.2f"|format(s.cost_performance) }}</td>
            <td>{{ "%.2f"|format(s.localization) }}</td>
            <td>{{ "%.2f"|format(s.safety) }}</td>
            <td>{{ "%.2f"|format(s.uniqueness) }}</td>
            <td>v{{ s.version }}</td>
        </tr>
        {% endfor %}
    </table>
</div>

<!-- Latest Score Bars -->
{% set latest = snapshots[-1] %}
<div class="card">
    <h2>最新スコア内訳</h2>
    {% set axes = [
        ("実用性 (Practicality)", latest.practicality),
        ("コスト効率 (Cost Performance)", latest.cost_performance),
        ("日本語対応 (Localization)", latest.localization),
        ("安全性 (Safety)", latest.safety),
        ("独自性 (Uniqueness)", latest.uniqueness),
    ] %}
    {% for name, score in axes %}
    <div style="margin-bottom: 12px;">
        <div style="display: flex; justify-content: space-between; margin-bottom: 4px;">
            <span>{{ name }}</span>
            <span><strong>{{ "%.2f"|format(score) }}</strong> / 5.0</span>
        </div>
        <div class="score-bar">
            <div class="score-bar-fill" style="width: {{ (score / 5.0 * 100)|round(1) }}%; background: {% if score >= 4.0 %}var(--success){% elif score >= 2.5 %}var(--warning){% else %}var(--danger){% endif %};"></div>
        </div>
    </div>
    {% endfor %}
</div>

<!-- Footer -->
<div class="footer">
    <p>本レポートはAixis破壊的テスト自動化エージェントにより生成されました。</p>
    <p><a href="https://aixis.jp">Aixis</a> - AI実装の投資判断を科学する、独立系AI調査・検証機関</p>
    <p>&copy; 2025 Aixis. All rights reserved.</p>
</div>

</div>

<script>
const dates = [{% for s in snapshots %}'{{ s.date }}',{% endfor %}];
const overall = [{% for s in snapshots %}{{ s.overall_score }},{% endfor %}];
const practicality = [{% for s in snapshots %}{{ s.practicality }},{% endfor %}];
const costPerformance = [{% for s in snapshots %}{{ s.cost_performance }},{% endfor %}];
const localization = [{% for s in snapshots %}{{ s.localization }},{% endfor %}];
const safety = [{% for s in snapshots %}{{ s.safety }},{% endfor %}];
const uniqueness = [{% for s in snapshots %}{{ s.uniqueness }},{% endfor %}];

const traces = [
    {
        x: dates, y: overall, name: '総合スコア',
        mode: 'lines+markers', line: { width: 4, color: '#1a365d' },
        marker: { size: 10 },
        hovertemplate: '総合スコア: %{y:.2f}<br>%{x}<extra></extra>',
    },
    {
        x: dates, y: practicality, name: '実用性',
        mode: 'lines+markers', line: { width: 2, color: '#2b6cb0' },
        marker: { size: 7 },
        hovertemplate: '実用性: %{y:.2f}<br>%{x}<extra></extra>',
    },
    {
        x: dates, y: costPerformance, name: 'コスト効率',
        mode: 'lines+markers', line: { width: 2, color: '#38a169' },
        marker: { size: 7 },
        hovertemplate: 'コスト効率: %{y:.2f}<br>%{x}<extra></extra>',
    },
    {
        x: dates, y: localization, name: '日本語対応',
        mode: 'lines+markers', line: { width: 2, color: '#d69e2e' },
        marker: { size: 7 },
        hovertemplate: '日本語対応: %{y:.2f}<br>%{x}<extra></extra>',
    },
    {
        x: dates, y: safety, name: '安全性',
        mode: 'lines+markers', line: { width: 2, color: '#e53e3e' },
        marker: { size: 7 },
        hovertemplate: '安全性: %{y:.2f}<br>%{x}<extra></extra>',
    },
    {
        x: dates, y: uniqueness, name: '独自性',
        mode: 'lines+markers', line: { width: 2, color: '#ed8936' },
        marker: { size: 7 },
        hovertemplate: '独自性: %{y:.2f}<br>%{x}<extra></extra>',
    },
];

const layout = {
    xaxis: { title: '日付', type: 'date' },
    yaxis: { title: 'スコア', range: [0, 5.0], dtick: 0.5 },
    legend: { orientation: 'h', y: -0.2, x: 0.5, xanchor: 'center' },
    margin: { t: 30, b: 80, l: 60, r: 30 },
    height: 480,
    shapes: [
        { type: 'rect', xref: 'paper', x0: 0, x1: 1, yref: 'y', y0: 4.5, y1: 5.0, fillcolor: 'rgba(221,198,125,0.08)', line: { width: 0 }, layer: 'below' },
        { type: 'rect', xref: 'paper', x0: 0, x1: 1, yref: 'y', y0: 3.5, y1: 4.5, fillcolor: 'rgba(139,178,202,0.06)', line: { width: 0 }, layer: 'below' },
        { type: 'rect', xref: 'paper', x0: 0, x1: 1, yref: 'y', y0: 2.5, y1: 3.5, fillcolor: 'rgba(157,185,173,0.05)', line: { width: 0 }, layer: 'below' },
        { type: 'rect', xref: 'paper', x0: 0, x1: 1, yref: 'y', y0: 1.5, y1: 2.5, fillcolor: 'rgba(185,171,160,0.06)', line: { width: 0 }, layer: 'below' },
        { type: 'rect', xref: 'paper', x0: 0, x1: 1, yref: 'y', y0: 0, y1: 1.5, fillcolor: 'rgba(185,141,141,0.05)', line: { width: 0 }, layer: 'below' },
    ],
    annotations: [
        { xref: 'paper', yref: 'y', x: 1.01, y: 4.75, text: 'S', showarrow: false, font: { size: 11, color: '#B8A04A' }, xanchor: 'left' },
        { xref: 'paper', yref: 'y', x: 1.01, y: 3.8, text: 'A', showarrow: false, font: { size: 11, color: '#88A8BE' }, xanchor: 'left' },
        { xref: 'paper', yref: 'y', x: 1.01, y: 3.0, text: 'B', showarrow: false, font: { size: 11, color: '#9DB9AD' }, xanchor: 'left' },
        { xref: 'paper', yref: 'y', x: 1.01, y: 2.0, text: 'C', showarrow: false, font: { size: 11, color: '#B9ABA0' }, xanchor: 'left' },
        { xref: 'paper', yref: 'y', x: 1.01, y: 0.75, text: 'D', showarrow: false, font: { size: 11, color: '#B98D8D' }, xanchor: 'left' },
    ],
};

Plotly.newPlot('trend-chart', traces, layout, { responsive: true });
</script>
</body>
</html>"""


class TrendRenderer:
    """Renders score trend/history as a self-contained HTML file."""

    def render(
        self,
        tool_name: str,
        snapshots: list[dict],
        output_path: Path,
    ) -> Path:
        """Render a trend chart HTML report.

        Args:
            tool_name: Display name of the tool.
            snapshots: List of score snapshot dicts, each containing:
                - date: str (e.g. "2025-01-15")
                - overall_score: float (0-5.0)
                - practicality: float (0-5.0)
                - cost_performance: float (0-5.0)
                - localization: float (0-5.0)
                - safety: float (0-5.0)
                - uniqueness: float (0-5.0)
                - version: int
            output_path: Desired output file path.

        Returns:
            Path to the generated HTML file.
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        html_path = output_path.with_suffix(".html")

        sorted_snapshots = sorted(snapshots, key=lambda s: s["date"])

        env = Environment(loader=BaseLoader(), autoescape=True)
        template = env.from_string(TREND_TEMPLATE)
        html_content = template.render(
            tool_name=tool_name,
            snapshots=sorted_snapshots,
        )

        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_content)

        return html_path
