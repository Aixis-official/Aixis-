/**
 * Aixis AI監査プラットフォーム — Shared JavaScript Utilities
 */

// ===== 5-AXIS CONSTANTS =====
window.AIXIS = {
  // Canonical axis keys (order matters for radar charts)
  AXIS_KEYS: ['practicality', 'cost_performance', 'localization', 'safety', 'uniqueness'],

  // Japanese labels for each axis
  AXIS_LABELS: {
    practicality: '実務適性',
    cost_performance: '費用対効果',
    localization: '日本語能力',
    safety: '信頼性・安全性',
    uniqueness: '革新性'
  },

  // Short labels for compact display
  AXIS_SHORT: {
    practicality: '実務',
    cost_performance: 'コスパ',
    localization: '日本語',
    safety: '安全性',
    uniqueness: '革新性'
  },

  // Auto:Manual ratio per axis (must match score_service.py AXIS_MIX)
  AXIS_MIX: {
    practicality: { auto: 40, manual: 60 },
    cost_performance: { auto: 0, manual: 100 },
    localization: { auto: 70, manual: 30 },
    safety: { auto: 35, manual: 65 },
    uniqueness: { auto: 0, manual: 100 }
  },

  // Colors for up to 8 tools in comparison charts
  TOOL_COLORS: [
    '#6366f1', '#ec4899', '#14b8a6', '#f59e0b',
    '#8b5cf6', '#06b6d4', '#f43f5e', '#84cc16'
  ],

  // Grade thresholds
  GRADES: [
    { min: 4.5, grade: 'S', label: 'S', color: '#d4af37' },
    { min: 3.5, grade: 'A', label: 'A', color: '#38a169' },
    { min: 2.5, grade: 'B', label: 'B', color: '#2b6cb0' },
    { min: 1.5, grade: 'C', label: 'C', color: '#ed8936' },
    { min: 0,   grade: 'D', label: 'D', color: '#e53e3e' }
  ],

  // Special grade for insufficient test completion
  GRADE_NA: { grade: 'N/A', label: '評価不十分', color: '#a0aec0' },

  MAX_SCORE: 5.0
};


// ===== GRADE UTILITIES =====

function getGrade(score, gradeOverride) {
  // Handle N/A grade from backend (insufficient test completion)
  if (gradeOverride === 'N/A') return AIXIS.GRADE_NA;
  for (const g of AIXIS.GRADES) {
    if (score >= g.min) return g;
  }
  return AIXIS.GRADES[AIXIS.GRADES.length - 1];
}

function gradeClass(score) {
  return 'grade-' + getGrade(score).grade;
}

function gradeColor(score) {
  return getGrade(score).color;
}


// ===== SCORE UTILITIES =====

function scoreLevel(score) {
  if (score >= 4.0) return 'excellent';
  if (score >= 3.0) return 'good';
  if (score >= 2.0) return 'average';
  return 'poor';
}

function scoreLevelColor(score) {
  if (score >= 4.0) return '#38a169';
  if (score >= 3.0) return '#2b6cb0';
  if (score >= 2.0) return '#d69e2e';
  return '#e53e3e';
}

function formatScore(score) {
  return score != null ? score.toFixed(2) : '---';
}


// ===== THEME UTILITIES =====

function isDarkMode() {
  return document.documentElement.classList.contains('dark');
}

function toggleTheme() {
  const html = document.documentElement;
  const isDark = html.classList.toggle('dark');
  localStorage.setItem('aixis-theme', isDark ? 'dark' : 'light');
  // Dispatch custom event for Plotly chart re-rendering
  window.dispatchEvent(new CustomEvent('aixis-theme-change', { detail: { dark: isDark } }));
}

// Expose toggleTheme on AIXIS namespace
AIXIS.toggleTheme = toggleTheme;
AIXIS.isDarkMode = isDarkMode;


// ===== PLOTLY THEME =====

function getPlotlyLayout(overrides = {}) {
  const dark = isDarkMode();
  const textColor = dark ? '#e2e8f0' : '#2d3748';
  const gridColor = dark ? '#2d3748' : '#e2e8f0';
  const bgColor = 'rgba(0,0,0,0)';

  const base = {
    paper_bgcolor: bgColor,
    plot_bgcolor: bgColor,
    font: {
      family: 'Inter, Noto Sans JP, sans-serif',
      color: textColor,
      size: 13
    },
    margin: { t: 30, r: 30, b: 30, l: 30 },
    showlegend: true,
    legend: {
      font: { color: textColor, size: 12 },
      bgcolor: 'rgba(0,0,0,0)'
    }
  };

  return { ...base, ...overrides };
}

function getRadarLayout(overrides = {}) {
  const dark = isDarkMode();
  const textColor = dark ? '#e2e8f0' : '#2d3748';
  const gridColor = dark ? '#374151' : '#e2e8f0';

  const base = getPlotlyLayout({
    polar: {
      radialaxis: {
        visible: true,
        range: [0, 5],
        dtick: 1,
        gridcolor: gridColor,
        linecolor: gridColor,
        tickfont: { color: textColor, size: 11 }
      },
      angularaxis: {
        direction: 'clockwise',
        gridcolor: gridColor,
        linecolor: gridColor,
        tickfont: { color: textColor, size: 12 }
      },
      bgcolor: 'rgba(0,0,0,0)'
    }
  });

  return { ...base, ...overrides };
}

// Re-render all Plotly charts when theme changes
window.addEventListener('aixis-theme-change', () => {
  document.querySelectorAll('.js-plotly-plot').forEach(el => {
    if (el._renderChart) {
      el._renderChart();
    }
  });
});


// ===== DATE UTILITIES =====

function _parseUTC(isoString) {
  // DB stores UTC without timezone suffix — append Z so JS parses as UTC correctly
  if (!isoString) return null;
  return new Date(isoString.endsWith('Z') || isoString.includes('+') ? isoString : isoString + 'Z');
}

function formatDate(isoString) {
  const d = _parseUTC(isoString);
  if (!d || isNaN(d)) return '---';
  return d.toLocaleDateString('ja-JP', { year: 'numeric', month: '2-digit', day: '2-digit', timeZone: 'Asia/Tokyo' });
}

function formatDateTime(isoString) {
  const d = _parseUTC(isoString);
  if (!d || isNaN(d)) return '---';
  return d.toLocaleDateString('ja-JP', {
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit',
    timeZone: 'Asia/Tokyo'
  });
}


// ===== CSRF HELPER =====

function getCSRFToken() {
  const match = document.cookie.match(/(?:^|;\s*)aixis_csrf=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : '';
}

// Configure htmx to send CSRF token on every non-GET request
document.addEventListener('DOMContentLoaded', function() {
  document.body.addEventListener('htmx:configRequest', function(evt) {
    if (evt.detail.verb !== 'get') {
      evt.detail.headers['X-CSRF-Token'] = getCSRFToken();
    }
  });
});


// ===== API HELPER =====

async function aixisAPI(path, options = {}) {
  const token = localStorage.getItem('aixis_token');
  const headers = { 'Content-Type': 'application/json', ...options.headers };
  if (token) headers['Authorization'] = `Bearer ${token}`;
  // Include CSRF token for state-changing requests
  const method = (options.method || 'GET').toUpperCase();
  if (method !== 'GET' && method !== 'HEAD') {
    headers['X-CSRF-Token'] = getCSRFToken();
  }

  const response = await fetch(`/api/v1${path}`, { ...options, headers });

  if (response.status === 401) {
    localStorage.removeItem('aixis_token');
    if (!window.location.pathname.startsWith('/login')) {
      window.location.href = '/login';
    }
    throw new Error('Unauthorized');
  }

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  // 204 No Content — no body to parse
  if (response.status === 204) return null;

  return response.json();
}


// ===== UI HELPERS =====

function createGradeBadge(grade, size = '') {
  const sizeClass = size === 'lg' ? 'grade-badge-lg' : '';
  return `<span class="grade-badge ${sizeClass} grade-${grade}">${grade}</span>`;
}

function createScoreBar(score, maxScore = 5.0) {
  const pct = (score / maxScore * 100).toFixed(1);
  const level = scoreLevel(score);
  return `<div class="score-bar"><div class="score-bar-fill ${level}" style="width:${pct}%"></div></div>`;
}

function createStatusBadge(status) {
  const labels = {
    pending: '待機中',
    running: '記録中',
    scoring: 'スコアリング中',
    aborting: '中止中...',
    waiting_login: '記録中',
    awaiting_manual: '手動評価待ち',
    completed: '完了',
    failed: '失敗',
    cancelled: 'キャンセル',
    aborted: '中止済み'
  };
  return `<span class="status-badge status-${status}">${labels[status] || status}</span>`;
}


// ===== MOBILE NAV =====

function toggleMobileNav() {
  const menu = document.getElementById('mobile-menu');
  if (menu) {
    menu.classList.toggle('hidden');
  }
}


// ===== COUNTUP ANIMATION =====
function countUp(el, target, duration) {
    duration = duration || 1200;
    var start = 0;
    var startTime = null;
    var isFloat = String(target).indexOf('.') !== -1;
    var decimalPlaces = isFloat ? (String(target).split('.')[1] || '').length : 0;

    function step(timestamp) {
        if (!startTime) startTime = timestamp;
        var progress = Math.min((timestamp - startTime) / duration, 1);
        var eased = 1 - Math.pow(1 - progress, 3);
        var current = start + (target - start) * eased;
        el.textContent = isFloat ? current.toFixed(decimalPlaces) : Math.floor(current).toLocaleString('ja-JP');
        if (progress < 1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
}

function initCountUpOnLoad() {
    var els = document.querySelectorAll('[data-countup]');
    if (!els.length) return;

    var observer = new IntersectionObserver(function(entries) {
        entries.forEach(function(entry) {
            if (entry.isIntersecting) {
                var el = entry.target;
                var target = parseFloat(el.dataset.countup);
                if (!isNaN(target) && !el.dataset.counted) {
                    el.dataset.counted = 'true';
                    countUp(el, target, 1200);
                }
                observer.unobserve(el);
            }
        });
    }, { threshold: 0.3 });

    els.forEach(function(el) { observer.observe(el); });
}

document.addEventListener('DOMContentLoaded', initCountUpOnLoad);
AIXIS.countUp = countUp;
