"""HTML comparison report renderer for side-by-side multi-tool analysis."""

from pathlib import Path

from jinja2 import Environment, BaseLoader

from ..core.models import ComparisonReport


# Plotly tool colors for up to 8 tools
_TOOL_COLORS = [
    "#2b6cb0",  # blue
    "#e53e3e",  # red
    "#38a169",  # green
    "#d69e2e",  # yellow
    "#805ad5",  # purple
    "#ed8936",  # orange
    "#319795",  # teal
    "#d53f8c",  # pink
]

_TOOL_COLORS_ALPHA = [
    "rgba(43,108,176,0.15)",
    "rgba(229,62,62,0.15)",
    "rgba(56,161,105,0.15)",
    "rgba(214,158,46,0.15)",
    "rgba(128,90,213,0.15)",
    "rgba(237,137,54,0.15)",
    "rgba(49,151,149,0.15)",
    "rgba(213,63,140,0.15)",
]

COMPARISON_TEMPLATE = r"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Aixis ツール比較レポート - {{ report.category_name_jp }}</title>
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
    --success: #A3BFD6;
    --warning: #D4B85C;
    --danger: #A87070;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: "Noto Serif JP", "Hiragino Sans", serif; background: var(--bg); color: var(--text); line-height: 1.7; }
.container { max-width: 1100px; margin: 0 auto; padding: 40px 24px; }
.cover { background: linear-gradient(135deg, var(--primary), var(--secondary)); color: white; padding: 60px 40px; border-radius: 12px; margin-bottom: 40px; text-align: center; }
.cover h1 { font-size: 2.2rem; margin-bottom: 8px; }
.cover .subtitle { font-size: 1.1rem; opacity: 0.9; }
.cover .meta { margin-top: 20px; font-size: 0.9rem; opacity: 0.7; }
.cover .tools-list { margin-top: 16px; font-size: 1.0rem; opacity: 0.85; }

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

.cell-green { background: #DCE8F0; color: #4A6A80; font-weight: 600; }
.cell-yellow { background: #F0E0B0; color: #5C4A1E; font-weight: 600; }
.cell-red { background: #E0D0D0; color: #5A3030; font-weight: 600; }

.rank-badge { display: inline-block; width: 28px; height: 28px; line-height: 28px; text-align: center; border-radius: 50%; font-weight: 700; font-size: 0.85rem; color: white; }
.rank-1 { background: #D4B85C; }
.rank-2 { background: #A3BFD6; }
.rank-3 { background: #85A898; }
.rank-other { background: #cbd5e0; color: var(--text); }

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
    <h1>ツール比較レポート</h1>
    <div class="subtitle">{{ report.category_name_jp }}</div>
    <div class="tools-list">比較対象: {{ report.tools | join(' / ') }}</div>
    <div class="meta">
        生成日: {{ report.generated_at.strftime('%Y年%m月%d日') }}<br>
        レポートID: {{ report.report_id }}
    </div>
</div>

<!-- Summary -->
{% if report.summary_jp %}
<div class="card">
    <h2>比較サマリー</h2>
    <div class="summary-text">{{ report.summary_jp }}</div>
</div>
{% endif %}

<!-- Radar Chart (Overlapping) -->
<div class="card">
    <h2>レーダーチャート比較</h2>
    <div id="radar-chart" class="chart-container"></div>
</div>

<!-- Bar Chart per Axis -->
<div class="card">
    <h2>軸別バーチャート比較</h2>
    <div id="bar-chart" class="chart-container"></div>
</div>

<!-- Scores Table -->
<div class="card">
    <h2>スコア一覧</h2>
    <table>
        <tr>
            <th>ツール</th>
            {% for axis in axes %}
            <th>{{ axis_names[axis] }}</th>
            {% endfor %}
        </tr>
        {% for tool in report.tools %}
        <tr>
            <td><strong>{{ tool }}</strong></td>
            {% for axis in axes %}
            {% set sc = report.tool_scores.get(tool, {}).get(axis, 0.0) %}
            <td class="{% if sc >= 4.0 %}cell-green{% elif sc >= 2.5 %}cell-yellow{% else %}cell-red{% endif %}">{{ "%.1f"|format(sc) }}</td>
            {% endfor %}
        </tr>
        {% endfor %}
    </table>
</div>

<!-- Rankings per Axis -->
<div class="card">
    <h2>軸別ランキング</h2>
    {% for axis in axes %}
    {% if axis in report.rankings %}
    <h3>{{ axis_names[axis] }}</h3>
    <table>
        <tr><th style="width:60px;">順位</th><th>ツール</th><th>スコア</th></tr>
        {% for tool in report.rankings[axis] %}
        {% set rank = loop.index %}
        <tr>
            <td><span class="rank-badge {% if rank == 1 %}rank-1{% elif rank == 2 %}rank-2{% elif rank == 3 %}rank-3{% else %}rank-other{% endif %}">{{ rank }}</span></td>
            <td>{{ tool }}</td>
            {% set sc = report.tool_scores.get(tool, {}).get(axis, 0.0) %}
            <td class="{% if sc >= 4.0 %}cell-green{% elif sc >= 2.5 %}cell-yellow{% else %}cell-red{% endif %}">{{ "%.1f"|format(sc) }}</td>
        </tr>
        {% endfor %}
    </table>
    {% endif %}
    {% endfor %}
</div>

<!-- Footer -->
<div class="footer">
    <p>本レポートはAixis破壊的テスト自動化エージェントにより生成されました。</p>
    <p><a href="https://aixis.jp">Aixis</a> - AI実装の投資判断を科学する、独立系AI調査・検証機関</p>
    <p>&copy; {{ report.generated_at.strftime('%Y') }} Aixis. All rights reserved.</p>
</div>

</div>

<script>
// --- Data ---
const tools = {{ tools_json }};
const axes = {{ axes_json }};
const axisLabels = {{ axis_labels_json }};
const toolScores = {{ tool_scores_json }};
const colors = {{ colors_json }};
const colorsAlpha = {{ colors_alpha_json }};

// --- Radar Chart (overlapping) ---
const radarTraces = tools.map((tool, i) => {
    const values = axes.map(a => toolScores[tool][a] || 0);
    return {
        type: 'scatterpolar',
        name: tool,
        r: [...values, values[0]],
        theta: [...axisLabels, axisLabels[0]],
        fill: 'toself',
        fillcolor: colorsAlpha[i % colorsAlpha.length],
        line: { color: colors[i % colors.length], width: 2 },
        marker: { size: 6 },
    };
});

Plotly.newPlot('radar-chart', radarTraces, {
    polar: {
        radialaxis: { visible: true, range: [0, 5], dtick: 1 },
    },
    showlegend: true,
    legend: { orientation: 'h', y: -0.15 },
    margin: { t: 30, b: 60, l: 60, r: 60 },
    height: 420,
}, { responsive: true });

// --- Grouped Bar Chart per Axis ---
const barTraces = tools.map((tool, i) => ({
    type: 'bar',
    name: tool,
    x: axisLabels,
    y: axes.map(a => toolScores[tool][a] || 0),
    marker: { color: colors[i % colors.length] },
    text: axes.map(a => (toolScores[tool][a] || 0).toFixed(1)),
    textposition: 'outside',
}));

Plotly.newPlot('bar-chart', barTraces, {
    barmode: 'group',
    yaxis: { range: [0, 5.5], title: 'スコア (0-5)', dtick: 1 },
    legend: { orientation: 'h', y: -0.2 },
    margin: { t: 20, b: 80, l: 60, r: 20 },
    height: 350,
}, { responsive: true });
</script>
</body>
</html>"""


class ComparisonRenderer:
    """Renders a ComparisonReport as a self-contained HTML comparison page."""

    def render(self, report: ComparisonReport, output_path: Path) -> Path:
        import json

        output_path.parent.mkdir(parents=True, exist_ok=True)
        html_path = output_path.with_suffix(".html")

        # Determine axes present in the data
        all_axes: list[str] = []
        for tool_scores in report.tool_scores.values():
            for axis in tool_scores:
                if axis not in all_axes:
                    all_axes.append(axis)

        # Fall back to canonical order if nothing found
        if not all_axes:
            all_axes = [
                "practicality",
                "cost_performance",
                "localization",
                "safety",
                "uniqueness",
            ]

        # Japanese labels for axes
        axis_jp_map = {
            "practicality": "実務適性",
            "cost_performance": "費用対効果",
            "localization": "日本語能力",
            "safety": "信頼性・安全性",
            "uniqueness": "革新性",
        }
        axis_names = {a: axis_jp_map.get(a, a) for a in all_axes}
        axis_labels = [axis_names[a] for a in all_axes]

        num_tools = len(report.tools)
        colors = _TOOL_COLORS[:num_tools]
        colors_alpha = _TOOL_COLORS_ALPHA[:num_tools]

        # Build JSON blobs for JS
        tools_json = json.dumps(report.tools, ensure_ascii=False)
        axes_json = json.dumps(all_axes, ensure_ascii=False)
        axis_labels_json = json.dumps(axis_labels, ensure_ascii=False)
        tool_scores_json = json.dumps(report.tool_scores, ensure_ascii=False)
        colors_json = json.dumps(colors)
        colors_alpha_json = json.dumps(colors_alpha)

        env = Environment(loader=BaseLoader(), autoescape=True)
        template = env.from_string(COMPARISON_TEMPLATE)
        html_content = template.render(
            report=report,
            axes=all_axes,
            axis_names=axis_names,
            tools_json=tools_json,
            axes_json=axes_json,
            axis_labels_json=axis_labels_json,
            tool_scores_json=tool_scores_json,
            colors_json=colors_json,
            colors_alpha_json=colors_alpha_json,
        )

        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_content)

        return html_path
