"""Ranking renderer for category-level tool comparisons."""

from pathlib import Path

from jinja2 import Environment, BaseLoader


RANKING_TEMPLATE = r"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Aixis ツールランキング - {{ category_name_jp }}</title>
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
    --success: #38a169;
    --warning: #d69e2e;
    --danger: #e53e3e;
    --gold: #d4a017;
    --silver: #9ca3af;
    --bronze: #b87333;
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

.grade-badge {
    display: inline-block;
    padding: 2px 12px;
    border-radius: 12px;
    font-size: 0.85rem;
    font-weight: 700;
    letter-spacing: 0.5px;
}
.grade-S { background: linear-gradient(135deg, #fefcbf, #f6e05e); color: #744210; border: 1px solid #d69e2e; }
.grade-A { background: #c6f6d5; color: #22543d; }
.grade-B { background: #bee3f8; color: #2a4365; }
.grade-C { background: #feebc8; color: #7b341e; }
.grade-D { background: #fed7d7; color: #742a2a; }

.rank-medal {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 32px;
    height: 32px;
    border-radius: 50%;
    font-weight: 900;
    font-size: 0.9rem;
}
.rank-1 { background: linear-gradient(135deg, #f6e05e, #d69e2e); color: #744210; box-shadow: 0 2px 6px rgba(212,160,23,0.4); }
.rank-2 { background: linear-gradient(135deg, #e2e8f0, #a0aec0); color: #2d3748; box-shadow: 0 2px 6px rgba(160,174,192,0.4); }
.rank-3 { background: linear-gradient(135deg, #edcba0, #b87333); color: #fff; box-shadow: 0 2px 6px rgba(184,115,51,0.4); }
.rank-other { background: var(--bg); color: var(--text-light); }

.podium { display: flex; justify-content: center; align-items: flex-end; gap: 16px; margin: 30px 0 20px; }
.podium-item { text-align: center; border-radius: 12px; background: var(--card-bg); box-shadow: 0 2px 8px rgba(0,0,0,0.08); padding: 20px 24px; min-width: 180px; }
.podium-item.first { order: 2; transform: translateY(-20px); border-top: 4px solid var(--gold); }
.podium-item.second { order: 1; border-top: 4px solid var(--silver); }
.podium-item.third { order: 3; border-top: 4px solid var(--bronze); }
.podium-rank { font-size: 2rem; font-weight: 900; margin-bottom: 4px; }
.podium-name { font-weight: 700; font-size: 1.05rem; margin-bottom: 6px; color: var(--primary); }
.podium-score { font-size: 1.4rem; font-weight: 800; color: var(--secondary); }
.podium-grade { margin-top: 4px; }

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
    <h1>ツールランキング</h1>
    <div class="subtitle">{{ category_name_jp }}</div>
    <div class="meta">対象ツール数: {{ rankings|length }}件</div>
</div>

<!-- Podium (Top 3) -->
{% if rankings|length >= 3 %}
<div class="card">
    <h2>トップ3</h2>
    <div class="podium">
        <div class="podium-item second">
            <div class="podium-rank" style="color: var(--silver);">2</div>
            <div class="podium-name">{{ rankings[1].tool_name }}</div>
            <div class="podium-score">{{ "%.2f"|format(rankings[1].overall_score) }}</div>
            <div class="podium-grade"><span class="grade-badge grade-{{ rankings[1].overall_grade }}">{{ rankings[1].overall_grade }}</span></div>
        </div>
        <div class="podium-item first">
            <div class="podium-rank" style="color: var(--gold);">1</div>
            <div class="podium-name">{{ rankings[0].tool_name }}</div>
            <div class="podium-score">{{ "%.2f"|format(rankings[0].overall_score) }}</div>
            <div class="podium-grade"><span class="grade-badge grade-{{ rankings[0].overall_grade }}">{{ rankings[0].overall_grade }}</span></div>
        </div>
        <div class="podium-item third">
            <div class="podium-rank" style="color: var(--bronze);">3</div>
            <div class="podium-name">{{ rankings[2].tool_name }}</div>
            <div class="podium-score">{{ "%.2f"|format(rankings[2].overall_score) }}</div>
            <div class="podium-grade"><span class="grade-badge grade-{{ rankings[2].overall_grade }}">{{ rankings[2].overall_grade }}</span></div>
        </div>
    </div>
</div>
{% endif %}

<!-- Bar Chart -->
<div class="card">
    <h2>総合スコアランキング</h2>
    <div id="ranking-chart" class="chart-container"></div>
</div>

<!-- Full Ranking Table -->
<div class="card">
    <h2>全ツール詳細スコア</h2>
    <table>
        <tr>
            <th>順位</th>
            <th>ツール名</th>
            <th>総合</th>
            <th>グレード</th>
            <th>実用性</th>
            <th>コスト効率</th>
            <th>日本語対応</th>
            <th>安全性</th>
            <th>独自性</th>
        </tr>
        {% for r in rankings %}
        <tr>
            <td>
                {% if r.rank <= 3 %}
                <span class="rank-medal rank-{{ r.rank }}">{{ r.rank }}</span>
                {% else %}
                <span class="rank-medal rank-other">{{ r.rank }}</span>
                {% endif %}
            </td>
            <td><strong>{{ r.tool_name }}</strong></td>
            <td><strong>{{ "%.2f"|format(r.overall_score) }}</strong></td>
            <td><span class="grade-badge grade-{{ r.overall_grade }}">{{ r.overall_grade }}</span></td>
            <td>{{ "%.2f"|format(r.practicality) }}</td>
            <td>{{ "%.2f"|format(r.cost_performance) }}</td>
            <td>{{ "%.2f"|format(r.localization) }}</td>
            <td>{{ "%.2f"|format(r.safety) }}</td>
            <td>{{ "%.2f"|format(r.uniqueness) }}</td>
        </tr>
        {% endfor %}
    </table>
</div>

<!-- Footer -->
<div class="footer">
    <p>本レポートはAixis破壊的テスト自動化エージェントにより生成されました。</p>
    <p><a href="https://aixis.jp">Aixis</a> - AI実装の投資判断を科学する、独立系AI調査・検証機関</p>
    <p>&copy; 2025 Aixis. All rights reserved.</p>
</div>

</div>

<script>
const toolNames = [{% for r in rankings|reverse %}'{{ r.tool_name }}',{% endfor %}];
const scores = [{% for r in rankings|reverse %}{{ r.overall_score }},{% endfor %}];
const ranks = [{% for r in rankings|reverse %}{{ r.rank }},{% endfor %}];

const barColors = ranks.map(rank => {
    if (rank === 1) return '#d4a017';
    if (rank === 2) return '#9ca3af';
    if (rank === 3) return '#b87333';
    return '#2b6cb0';
});

Plotly.newPlot('ranking-chart', [{
    type: 'bar',
    x: scores,
    y: toolNames,
    orientation: 'h',
    marker: {
        color: barColors,
        line: { color: barColors.map(c => c), width: 1 },
    },
    text: scores.map((s, i) => {
        const rank = ranks[i];
        const medal = rank === 1 ? ' \ud83e\udd47' : rank === 2 ? ' \ud83e\udd48' : rank === 3 ? ' \ud83e\udd49' : '';
        return s.toFixed(2) + medal;
    }),
    textposition: 'outside',
    hovertemplate: '%{y}<br>スコア: %{x:.2f}<extra></extra>',
}], {
    xaxis: { range: [0, 5.3], title: '総合スコア', dtick: 0.5 },
    margin: { t: 20, b: 50, l: 160, r: 60 },
    height: Math.max(300, toolNames.length * 40 + 100),
    shapes: [
        { type: 'rect', xref: 'x', x0: 4.5, x1: 5.0, yref: 'paper', y0: 0, y1: 1, fillcolor: 'rgba(255,215,0,0.08)', line: { width: 0 }, layer: 'below' },
        { type: 'rect', xref: 'x', x0: 3.5, x1: 4.5, yref: 'paper', y0: 0, y1: 1, fillcolor: 'rgba(56,161,105,0.06)', line: { width: 0 }, layer: 'below' },
        { type: 'rect', xref: 'x', x0: 2.5, x1: 3.5, yref: 'paper', y0: 0, y1: 1, fillcolor: 'rgba(43,108,176,0.05)', line: { width: 0 }, layer: 'below' },
        { type: 'rect', xref: 'x', x0: 1.5, x1: 2.5, yref: 'paper', y0: 0, y1: 1, fillcolor: 'rgba(237,137,54,0.06)', line: { width: 0 }, layer: 'below' },
        { type: 'rect', xref: 'x', x0: 0, x1: 1.5, yref: 'paper', y0: 0, y1: 1, fillcolor: 'rgba(229,62,62,0.05)', line: { width: 0 }, layer: 'below' },
    ],
    annotations: [
        { xref: 'x', yref: 'paper', x: 4.75, y: 1.05, text: 'S', showarrow: false, font: { size: 11, color: '#b7791f' } },
        { xref: 'x', yref: 'paper', x: 3.8, y: 1.05, text: 'A', showarrow: false, font: { size: 11, color: '#276749' } },
        { xref: 'x', yref: 'paper', x: 3.0, y: 1.05, text: 'B', showarrow: false, font: { size: 11, color: '#2b6cb0' } },
        { xref: 'x', yref: 'paper', x: 2.0, y: 1.05, text: 'C', showarrow: false, font: { size: 11, color: '#c05621' } },
        { xref: 'x', yref: 'paper', x: 0.75, y: 1.05, text: 'D', showarrow: false, font: { size: 11, color: '#c53030' } },
    ],
}, { responsive: true });
</script>
</body>
</html>"""


class RankingRenderer:
    """Renders category-level tool rankings as a self-contained HTML file."""

    def render(
        self,
        category_name_jp: str,
        rankings: list[dict],
        output_path: Path,
    ) -> Path:
        """Render a ranking chart HTML report.

        Args:
            category_name_jp: Japanese display name for the category.
            rankings: List of tool ranking dicts sorted by rank, each containing:
                - rank: int
                - tool_name: str
                - tool_slug: str
                - overall_score: float (0-5.0)
                - overall_grade: str (S/A/B/C/D)
                - practicality: float (0-5.0)
                - cost_performance: float (0-5.0)
                - localization: float (0-5.0)
                - safety: float (0-5.0)
                - uniqueness: float (0-5.0)
            output_path: Desired output file path.

        Returns:
            Path to the generated HTML file.
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        html_path = output_path.with_suffix(".html")

        sorted_rankings = sorted(rankings, key=lambda r: r["rank"])

        env = Environment(loader=BaseLoader(), autoescape=True)
        template = env.from_string(RANKING_TEMPLATE)
        html_content = template.render(
            category_name_jp=category_name_jp,
            rankings=sorted_rankings,
        )

        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_content)

        return html_path
