/**
 * Aixis Chrome Extension v3 — Content Script
 *
 * Injects a draggable floating panel into the page using Shadow DOM.
 * All UI logic: test display, timer, screenshots, session management.
 * Communicates with service-worker via chrome.runtime.sendMessage.
 */

(() => {
  "use strict";

  // Prevent double-injection
  if (document.getElementById("aixis-panel-host")) return;

  // -------------------------------------------------------------------------
  // Constants
  // -------------------------------------------------------------------------

  const CATEGORY_NAMES = {
    slide_basic: "基本作成",
    slide_structure: "構成力",
    slide_japanese: "日本語",
    slide_accuracy: "正確性",
    slide_advanced: "応用機能",
    dialect: "方言",
    long_input: "長文",
    contradictory: "矛盾",
    ambiguous: "曖昧",
    keigo_mixing: "敬語混合",
    unicode_edge: "Unicode",
    business_jp: "商習慣",
    multi_step: "複合指示",
    broken_grammar: "文法破壊",
    freeform: "フリー",
    protocol: "プロトコル",
  };

  const CATEGORY_COLORS = {
    slide_basic: "#4f46e5",
    slide_structure: "#059669",
    slide_japanese: "#d97706",
    slide_accuracy: "#dc2626",
    slide_advanced: "#7c3aed",
    dialect: "#0891b2",
    long_input: "#be185d",
    contradictory: "#b91c1c",
    ambiguous: "#6d28d9",
    keigo_mixing: "#0d9488",
    unicode_edge: "#9333ea",
    business_jp: "#ca8a04",
    multi_step: "#2563eb",
    broken_grammar: "#e11d48",
    freeform: "#64748b",
    protocol: "#475569",
  };

  // -------------------------------------------------------------------------
  // State
  // -------------------------------------------------------------------------

  let panelHost = null;
  let shadow = null;
  let panelEl = null;
  let collapsed = false;
  let timerInterval = null;
  let allTools = [];
  let selectedToolId = "";

  // -------------------------------------------------------------------------
  // Helper: send message to background
  // -------------------------------------------------------------------------

  function sendBg(message) {
    return new Promise((resolve, reject) => {
      try {
        if (!chrome.runtime?.id) {
          reject(new Error("拡張機能のコンテキストが無効です。ページをリロードしてください。"));
          return;
        }
        chrome.runtime.sendMessage(message, (response) => {
          if (chrome.runtime.lastError) {
            reject(new Error(chrome.runtime.lastError.message));
          } else if (response && response.error) {
            reject(new Error(response.error));
          } else {
            resolve(response || {});
          }
        });
      } catch (err) {
        reject(new Error("拡張機能との通信に失敗しました: " + (err.message || "")));
      }
    });
  }

  // -------------------------------------------------------------------------
  // Shadow DOM panel creation
  // -------------------------------------------------------------------------

  function createPanel() {
    panelHost = document.createElement("div");
    panelHost.id = "aixis-panel-host";
    panelHost.style.cssText = "position:fixed;bottom:20px;right:20px;z-index:999999;font-size:initial;line-height:initial;pointer-events:none;";

    shadow = panelHost.attachShadow({ mode: "open" });

    const style = document.createElement("style");
    style.textContent = getPanelCSS();
    shadow.appendChild(style);

    panelEl = document.createElement("div");
    panelEl.className = "aixis-panel";
    panelEl.innerHTML = getPanelHTML();
    shadow.appendChild(panelEl);

    document.documentElement.appendChild(panelHost);

    setupDragging();
    setupEventListeners();
    setupKeyboardShortcut();
    restorePanelPosition();
  }

  // -------------------------------------------------------------------------
  // Panel HTML
  // -------------------------------------------------------------------------

  function getPanelHTML() {
    return `
      <div class="panel-header" id="panelHeader">
        <span class="panel-title">Aixis</span>
        <div class="panel-header-buttons">
          <button class="header-btn" id="collapseBtn" title="最小化">−</button>
          <button class="header-btn" id="closeBtn" title="閉じる">×</button>
        </div>
      </div>
      <div class="panel-body" id="panelBody">

        <!-- Error display -->
        <div class="error-msg" id="errorMsg"></div>

        <!-- Section: Settings -->
        <div class="section" id="settingsSection">
          <label>APIキー</label>
          <input type="password" id="apiKeyInput" placeholder="axk_...">
          <label>プラットフォームURL</label>
          <input type="text" id="platformUrlInput" placeholder="https://platform.aixis.jp">
          <button class="btn btn-primary" id="saveSettingsBtn">接続</button>
        </div>

        <!-- Section: Setup -->
        <div class="section" id="setupSection">
          <label>対象ツール</label>
          <div class="tool-picker">
            <input type="text" id="toolSearch" class="tool-search" placeholder="ツール名で検索...">
            <div class="tool-list" id="toolList">
              <div class="tool-list-empty">読み込み中...</div>
            </div>
          </div>
          <button class="btn btn-primary" id="startSessionBtn">セッション開始</button>
          <button class="btn btn-text" id="changeSettingsBtn">設定変更</button>
        </div>

        <!-- Section: Protocol Test -->
        <div class="section" id="protocolSection">
          <!-- Progress -->
          <div class="progress-row">
            <div class="progress-bar">
              <div class="progress-fill" id="progressFill"></div>
            </div>
            <span class="progress-text" id="progressText">0 / 0</span>
          </div>

          <!-- Test card -->
          <div class="test-card" id="testCard">
            <div class="test-card-top">
              <span class="category-badge" id="testCategory"></span>
            </div>
            <div class="prompt" id="testPrompt"></div>
            <div class="expected" id="testExpected"></div>
          </div>

          <button class="btn btn-copy" id="copyPromptBtn">コピー</button>

          <!-- Timer -->
          <div class="timer-row">
            <span class="timer-icon">&#9201;</span>
            <span class="timer-value" id="timerDisplay">00:00.0</span>
            <button class="btn btn-timer-start" id="startTimerBtn">開始</button>
            <button class="btn btn-timer-stop" id="stopTimerBtn">停止</button>
            <button class="btn btn-timer-reset" id="resetTimerBtn" title="タイマーをリセット">↺</button>
          </div>

          <!-- Screenshots -->
          <div class="screenshot-row">
            <button class="btn btn-screenshot" id="fullScreenshotBtn">&#128247; 全画面</button>
            <button class="btn btn-screenshot" id="partialScreenshotBtn">&#9986; 部分</button>
            <span class="capture-count" id="captureCount">(0)</span>
          </div>

          <!-- Screenshot thumbnails -->
          <div class="screenshot-thumbs" id="screenshotThumbs"></div>

          <!-- Navigation -->
          <div class="nav-row">
            <button class="btn btn-ghost btn-prev" id="prevTestBtn">← 戻る</button>
            <button class="btn btn-primary btn-next" id="nextTestBtn">次へ</button>
            <button class="btn btn-ghost" id="skipTestBtn">スキップ</button>
          </div>

          <!-- Session end -->
          <div class="session-end">
            <div class="shortcut-hint">Alt+A でパネルの表示/非表示</div>
          <button class="btn btn-text btn-danger" id="endProtocolBtn">セッションを終了する</button>
          </div>
        </div>

        <!-- Section: Complete -->
        <div class="section" id="completeSection">
          <div class="complete-icon">&#10003;</div>
          <div class="complete-title">セッション完了</div>
          <div class="summary-stats">
            <div class="stat-card">
              <div class="stat-value" id="summaryTotal">0</div>
              <div class="stat-label">記録数</div>
            </div>
            <div class="stat-card">
              <div class="stat-value" id="summaryStatus">-</div>
              <div class="stat-label">ステータス</div>
            </div>
          </div>
          <a class="dashboard-link" id="dashboardLink" href="#" target="_blank">ダッシュボードで確認 →</a>
          <button class="btn btn-secondary" id="newSessionBtn">新しいセッション</button>
        </div>

      </div>
    `;
  }

  // -------------------------------------------------------------------------
  // Panel CSS (inside shadow DOM)
  // -------------------------------------------------------------------------

  function getPanelCSS() {
    return `
      :host {
        all: initial;
      }

      * {
        margin: 0;
        padding: 0;
        box-sizing: border-box;
      }

      .aixis-panel {
        width: 320px;
        background: #fff;
        border-radius: 10px;
        box-shadow: 0 8px 32px rgba(0,0,0,0.18), 0 2px 8px rgba(0,0,0,0.08);
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans JP", sans-serif;
        font-size: 13px;
        color: #1e293b;
        overflow: hidden;
        user-select: none;
        -webkit-user-select: none;
        pointer-events: auto;
      }

      /* Header */
      .panel-header {
        background: #0f172a;
        color: #fff;
        padding: 8px 12px;
        display: flex;
        align-items: center;
        justify-content: space-between;
        cursor: grab;
      }

      .panel-header:active {
        cursor: grabbing;
      }

      .panel-title {
        font-size: 13px;
        font-weight: 700;
        letter-spacing: 0.02em;
      }

      .panel-header-buttons {
        display: flex;
        gap: 4px;
      }

      .header-btn {
        background: rgba(255,255,255,0.15);
        border: none;
        color: #fff;
        width: 22px;
        height: 22px;
        border-radius: 4px;
        cursor: pointer;
        font-size: 14px;
        display: flex;
        align-items: center;
        justify-content: center;
        transition: background 0.15s;
        line-height: 1;
      }

      .header-btn:hover {
        background: rgba(255,255,255,0.25);
      }

      /* Body */
      .panel-body {
        max-height: calc(100vh - 100px);
        overflow-y: auto;
        transition: max-height 0.2s ease;
      }

      .panel-body.collapsed {
        max-height: 0;
        overflow: hidden;
      }

      /* Sections */
      .section {
        display: none;
        padding: 12px;
      }

      .section.active {
        display: block;
      }

      /* Error */
      .error-msg {
        background: #fef2f2;
        color: #991b1b;
        padding: 8px 12px;
        font-size: 12px;
        display: none;
      }

      .error-msg.visible {
        display: block;
      }

      /* Forms */
      label {
        display: block;
        font-size: 11px;
        font-weight: 600;
        color: #94a3b8;
        margin-bottom: 4px;
        letter-spacing: 0.03em;
      }

      input[type="text"],
      input[type="password"] {
        width: 100%;
        padding: 7px 10px;
        border: 1px solid #e2e8f0;
        border-radius: 6px;
        font-size: 13px;
        margin-bottom: 10px;
        background: #fff;
        color: #1e293b;
        transition: border-color 0.15s;
        font-family: inherit;
      }

      input:focus {
        outline: none;
        border-color: #6366f1;
        box-shadow: 0 0 0 2px rgba(99,102,241,0.1);
      }

      /* Buttons */
      .btn {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        gap: 4px;
        padding: 7px 12px;
        font-size: 12px;
        font-weight: 600;
        border: none;
        border-radius: 6px;
        cursor: pointer;
        transition: all 0.15s;
        width: 100%;
        font-family: inherit;
      }

      .btn:disabled {
        opacity: 0.5;
        cursor: not-allowed;
      }

      .btn-primary {
        background: #4f46e5;
        color: #fff;
      }
      .btn-primary:hover:not(:disabled) {
        background: #4338ca;
      }

      .btn-secondary {
        background: #f1f5f9;
        color: #334155;
      }
      .btn-secondary:hover {
        background: #e2e8f0;
      }

      .btn-ghost {
        background: none;
        color: #64748b;
        border: 1px solid #e2e8f0;
        padding: 7px 10px;
      }
      .btn-ghost:hover {
        background: #f8fafc;
      }

      .btn-text {
        background: none;
        color: #94a3b8;
        font-size: 11px;
        padding: 4px;
        width: auto;
        margin-top: 4px;
      }
      .btn-text:hover {
        color: #64748b;
      }

      .btn-danger {
        color: #dc2626;
      }
      .btn-danger:hover {
        color: #b91c1c;
        background: #fef2f2;
      }

      .btn-copy {
        background: #eef2ff;
        color: #4f46e5;
        border: 1px solid #c7d2fe;
        margin: 8px 0;
      }
      .btn-copy:hover {
        background: #e0e7ff;
      }

      /* Timer */
      .timer-row {
        display: flex;
        align-items: center;
        gap: 8px;
        padding: 8px 0;
      }

      .timer-icon {
        font-size: 16px;
      }

      .timer-value {
        font-size: 22px;
        font-weight: 700;
        font-variant-numeric: tabular-nums;
        color: #0f172a;
        font-family: "SF Mono", "Menlo", "Consolas", monospace;
        letter-spacing: -0.02em;
        flex: 1;
      }

      .timer-value.running {
        color: #4f46e5;
      }

      .btn-timer-start {
        font-size: 11px;
        padding: 4px 10px;
        background: #eef2ff;
        color: #4f46e5;
        border: 1px solid #c7d2fe;
        border-radius: 4px;
        cursor: pointer;
        font-weight: 600;
        transition: all 0.15s;
        width: auto;
        font-family: inherit;
      }
      .btn-timer-start:hover {
        background: #e0e7ff;
      }
      .btn-timer-start:disabled {
        opacity: 0.4;
        cursor: not-allowed;
      }

      .btn-timer-stop {
        font-size: 11px;
        padding: 4px 10px;
        background: #fef2f2;
        color: #dc2626;
        border: 1px solid #fecaca;
        border-radius: 4px;
        cursor: pointer;
        font-weight: 600;
        transition: all 0.15s;
        width: auto;
        font-family: inherit;
      }
      .btn-timer-stop:hover {
        background: #fee2e2;
      }
      .btn-timer-stop:disabled {
        opacity: 0.4;
        cursor: not-allowed;
      }

      .btn-timer-reset {
        font-size: 13px;
        padding: 3px 8px;
        background: #f8fafc;
        color: #64748b;
        border: 1px solid #e2e8f0;
        border-radius: 4px;
        cursor: pointer;
        transition: all 0.15s;
        width: auto;
        font-family: inherit;
        line-height: 1;
      }
      .btn-timer-reset:hover {
        background: #f1f5f9;
        color: #334155;
      }

      .shortcut-hint {
        font-size: 10px;
        color: #94a3b8;
        text-align: center;
        margin-bottom: 4px;
      }

      /* Screenshots */
      .screenshot-row {
        display: flex;
        align-items: center;
        gap: 6px;
        margin: 6px 0;
      }

      .btn-screenshot {
        font-size: 11px;
        padding: 5px 8px;
        background: #f1f5f9;
        color: #334155;
        border: 1px solid #e2e8f0;
        border-radius: 5px;
        cursor: pointer;
        font-weight: 500;
        transition: all 0.15s;
        white-space: nowrap;
        width: auto;
        font-family: inherit;
      }
      .btn-screenshot:hover {
        background: #e2e8f0;
      }
      .btn-screenshot:disabled {
        opacity: 0.5;
        cursor: not-allowed;
      }

      .capture-count {
        font-size: 12px;
        font-weight: 700;
        color: #4f46e5;
        margin-left: auto;
      }

      /* Screenshot thumbnails */
      .screenshot-thumbs {
        display: flex;
        gap: 4px;
        flex-wrap: wrap;
        margin-top: 6px;
        min-height: 0;
      }
      .screenshot-thumbs:empty {
        display: none;
      }
      .thumb-item {
        width: 52px;
        height: 36px;
        border-radius: 4px;
        background: #f1f5f9;
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 9px;
        color: #64748b;
        border: 1px solid #e2e8f0;
        cursor: default;
        position: relative;
        overflow: hidden;
      }
      .thumb-item.has-preview {
        cursor: pointer;
        border-color: #c7d2fe;
      }
      .thumb-item.has-preview:hover {
        border-color: #6366f1;
        box-shadow: 0 0 0 2px rgba(99,102,241,0.2);
      }
      .thumb-item .thumb-img {
        width: 100%;
        height: 100%;
        object-fit: cover;
      }
      .thumb-item .thumb-type {
        font-size: 8px;
        line-height: 1;
      }
      .thumb-item .thumb-time {
        position: absolute;
        bottom: 1px;
        right: 2px;
        font-size: 7px;
        color: #fff;
        background: rgba(0,0,0,0.4);
        padding: 0 2px;
        border-radius: 2px;
      }
      .thumb-item .thumb-delete {
        position: absolute;
        top: -4px;
        right: -4px;
        width: 14px;
        height: 14px;
        border-radius: 50%;
        background: #ef4444;
        color: #fff;
        border: none;
        font-size: 9px;
        line-height: 14px;
        text-align: center;
        cursor: pointer;
        padding: 0;
        opacity: 0;
        transition: opacity 0.15s;
        pointer-events: auto;
      }
      .thumb-item:hover .thumb-delete {
        opacity: 1;
      }

      /* Navigation */
      .nav-row {
        display: flex;
        gap: 6px;
        margin-top: 8px;
      }

      .nav-row .btn-prev {
        flex: 0 0 auto;
        font-size: 11px;
        padding: 6px 10px;
      }

      .nav-row .btn-next {
        flex: 2;
      }

      .nav-row .btn-ghost {
        flex: 1;
        width: auto;
      }

      /* Session end */
      .session-end {
        margin-top: 12px;
        padding-top: 10px;
        border-top: 1px solid #f1f5f9;
        text-align: center;
      }

      /* Progress */
      .progress-row {
        display: flex;
        align-items: center;
        gap: 8px;
        margin-bottom: 8px;
      }

      .progress-bar {
        flex: 1;
        background: #f1f5f9;
        border-radius: 3px;
        height: 4px;
        overflow: hidden;
      }

      .progress-fill {
        background: #6366f1;
        height: 100%;
        border-radius: 3px;
        transition: width 0.3s;
        width: 0%;
      }

      .progress-text {
        font-size: 11px;
        color: #94a3b8;
        white-space: nowrap;
      }

      /* Test card */
      .test-card {
        background: #fafafa;
        border: 1px solid #e2e8f0;
        border-radius: 8px;
        padding: 10px;
        max-height: 280px;
        overflow-y: auto;
      }

      .test-card-top {
        display: flex;
        align-items: center;
        justify-content: space-between;
        margin-bottom: 6px;
      }

      .category-badge {
        font-size: 10px;
        font-weight: 700;
        color: #fff;
        background: #6366f1;
        padding: 2px 8px;
        border-radius: 4px;
        letter-spacing: 0.05em;
      }

      .test-card .prompt {
        font-size: 13px;
        line-height: 1.5;
        color: #0f172a;
        margin-bottom: 6px;
        max-height: 100px;
        overflow-y: auto;
        white-space: pre-wrap;
        word-break: break-word;
        user-select: text;
        -webkit-user-select: text;
      }

      .test-card .expected {
        font-size: 11px;
        color: #64748b;
        padding-top: 6px;
        border-top: 1px solid #f1f5f9;
      }

      .test-card .expected ul {
        padding-left: 0;
        list-style: none;
      }

      .test-card .expected li {
        margin-bottom: 2px;
      }

      .test-card .expected li::before {
        content: "→ ";
        color: #94a3b8;
      }

      /* Tool picker */
      .tool-picker {
        margin-bottom: 10px;
      }

      .tool-search {
        width: 100%;
        padding: 7px 10px;
        border: 1px solid #e2e8f0;
        border-radius: 6px 6px 0 0;
        font-size: 12px;
        margin-bottom: 0;
        background: #fff;
        color: #1e293b;
        font-family: inherit;
      }

      .tool-search:focus {
        outline: none;
        border-color: #6366f1;
        box-shadow: 0 0 0 2px rgba(99,102,241,0.1);
      }

      .tool-list {
        max-height: 140px;
        overflow-y: auto;
        border: 1px solid #e2e8f0;
        border-top: none;
        border-radius: 0 0 6px 6px;
        background: #fff;
      }

      .tool-list-item {
        padding: 6px 10px;
        cursor: pointer;
        display: flex;
        align-items: center;
        gap: 8px;
        border-bottom: 1px solid #f8fafc;
        transition: background 0.1s;
      }

      .tool-list-item:last-child {
        border-bottom: none;
      }

      .tool-list-item:hover {
        background: #f8fafc;
      }

      .tool-list-item.selected {
        background: #eef2ff;
        border-left: 2px solid #6366f1;
        padding-left: 8px;
      }

      .tool-name {
        font-size: 12px;
        font-weight: 600;
        color: #0f172a;
        flex: 1;
        min-width: 0;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }

      .tool-meta {
        font-size: 10px;
        color: #94a3b8;
        flex-shrink: 0;
      }

      .tool-check {
        width: 14px;
        height: 14px;
        border-radius: 50%;
        border: 2px solid #cbd5e1;
        flex-shrink: 0;
      }

      .tool-list-item.selected .tool-check {
        border-color: #6366f1;
        background: #6366f1;
        position: relative;
      }

      .tool-list-item.selected .tool-check::after {
        content: "";
        position: absolute;
        top: 2px;
        left: 4px;
        width: 4px;
        height: 6px;
        border: solid #fff;
        border-width: 0 1.5px 1.5px 0;
        transform: rotate(45deg);
      }

      .tool-list-empty {
        padding: 14px;
        text-align: center;
        color: #94a3b8;
        font-size: 12px;
      }

      .tool-count {
        font-size: 10px;
        color: #94a3b8;
        padding: 4px 10px;
        background: #f8fafc;
        border-top: 1px solid #f1f5f9;
      }

      /* Complete section */
      .complete-icon {
        width: 44px;
        height: 44px;
        margin: 8px auto 10px;
        background: #dcfce7;
        color: #16a34a;
        border-radius: 50%;
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 22px;
        font-weight: 700;
      }

      .complete-title {
        text-align: center;
        font-size: 15px;
        font-weight: 700;
        color: #0f172a;
        margin-bottom: 12px;
      }

      .summary-stats {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 8px;
        margin-bottom: 10px;
      }

      .stat-card {
        background: #fafafa;
        border: 1px solid #e2e8f0;
        border-radius: 6px;
        padding: 8px;
        text-align: center;
      }

      .stat-value {
        font-size: 18px;
        font-weight: 800;
        color: #0f172a;
      }

      .stat-label {
        font-size: 10px;
        color: #94a3b8;
        margin-top: 2px;
      }

      .dashboard-link {
        display: block;
        text-align: center;
        color: #6366f1;
        font-size: 12px;
        font-weight: 600;
        margin-bottom: 8px;
        text-decoration: none;
      }

      .dashboard-link:hover {
        text-decoration: underline;
      }

      /* Scrollbar */
      .panel-body::-webkit-scrollbar,
      .tool-list::-webkit-scrollbar,
      .prompt::-webkit-scrollbar {
        width: 4px;
      }

      .panel-body::-webkit-scrollbar-thumb,
      .tool-list::-webkit-scrollbar-thumb,
      .prompt::-webkit-scrollbar-thumb {
        background: #cbd5e1;
        border-radius: 2px;
      }
    `;
  }

  // -------------------------------------------------------------------------
  // Shadow DOM helper: query within shadow
  // -------------------------------------------------------------------------

  function $(sel) {
    return shadow.querySelector(sel);
  }

  // -------------------------------------------------------------------------
  // Section management
  // -------------------------------------------------------------------------

  function showSection(name) {
    const sectionIds = {
      settings: "settingsSection",
      setup: "setupSection",
      protocol: "protocolSection",
      complete: "completeSection",
    };

    for (const id of Object.values(sectionIds)) {
      const el = shadow.getElementById(id);
      if (el) el.classList.remove("active");
    }

    const targetId = sectionIds[name];
    if (targetId) {
      const el = shadow.getElementById(targetId);
      if (el) el.classList.add("active");
    }
  }

  function showError(msg) {
    if (!shadow) return;
    let displayMsg = msg || "不明なエラーが発生しました";
    if (msg && msg.includes("401") || msg && msg.includes("Unauthorized")) {
      displayMsg = "認証エラー: APIキーが無効です。正しいキーを設定してください。";
    } else if (msg && msg.includes("403") || msg && msg.includes("agent:write")) {
      displayMsg = "権限エラー: APIキーに agent:write スコープが必要です。";
    } else if (msg && msg.includes("500") || msg && msg.includes("Internal server")) {
      displayMsg = "サーバーエラー: プラットフォームに接続できません。";
    } else if (msg && (msg.includes("Failed to fetch") || msg.includes("NetworkError") || msg.includes("ネットワークエラー"))) {
      displayMsg = "ネットワークエラー: プラットフォームURLに接続できません。";
    } else if (msg && msg.includes("タイムアウト")) {
      displayMsg = "タイムアウト: APIリクエストに時間がかかりすぎています。再試行してください。";
    }

    const el = shadow.getElementById("errorMsg");
    if (!el) return;
    el.textContent = displayMsg;
    el.classList.add("visible");
    setTimeout(() => {
      try { el.classList.remove("visible"); } catch {}
    }, 8000);
  }

  function escapeHtml(str) {
    if (!str) return "";
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
  }

  // -------------------------------------------------------------------------
  // Dragging
  // -------------------------------------------------------------------------

  function setupDragging() {
    const header = shadow.getElementById("panelHeader");
    let isDragging = false;
    let offsetX = 0;
    let offsetY = 0;

    header.addEventListener("mousedown", (e) => {
      // Don't start drag on button clicks
      if (e.target.closest(".header-btn")) return;
      isDragging = true;
      const rect = panelHost.getBoundingClientRect();
      offsetX = e.clientX - rect.left;
      offsetY = e.clientY - rect.top;
      e.preventDefault();
    });

    document.addEventListener("mousemove", (e) => {
      if (!isDragging) return;
      e.preventDefault();
      const x = e.clientX - offsetX;
      const y = e.clientY - offsetY;

      // Clamp to viewport
      const maxX = window.innerWidth - 40;
      const maxY = window.innerHeight - 40;
      const clampedX = Math.max(0, Math.min(x, maxX));
      const clampedY = Math.max(0, Math.min(y, maxY));

      panelHost.style.left = clampedX + "px";
      panelHost.style.top = clampedY + "px";
      panelHost.style.right = "auto";
      panelHost.style.bottom = "auto";
    });

    document.addEventListener("mouseup", () => {
      if (isDragging) {
        isDragging = false;
        // Save position to storage for persistence across navigations
        const rect = panelHost.getBoundingClientRect();
        chrome.storage.local.set({ panelPosition: { x: rect.left, y: rect.top } });
      }
    });
  }

  // -------------------------------------------------------------------------
  // Position persistence
  // -------------------------------------------------------------------------

  function restorePanelPosition() {
    chrome.storage.local.get("panelPosition", (data) => {
      if (data.panelPosition && panelHost) {
        const { x, y } = data.panelPosition;
        // Clamp to current viewport
        const maxX = window.innerWidth - 40;
        const maxY = window.innerHeight - 40;
        const clampedX = Math.max(0, Math.min(x, maxX));
        const clampedY = Math.max(0, Math.min(y, maxY));
        panelHost.style.left = clampedX + "px";
        panelHost.style.top = clampedY + "px";
        panelHost.style.right = "auto";
        panelHost.style.bottom = "auto";
      }
    });
  }

  // -------------------------------------------------------------------------
  // Keyboard shortcut: Alt+A to toggle panel visibility
  // -------------------------------------------------------------------------

  function setupKeyboardShortcut() {
    document.addEventListener("keydown", (e) => {
      if (e.altKey && (e.key === "a" || e.key === "A") && !e.ctrlKey && !e.metaKey && !e.shiftKey) {
        e.preventDefault();
        if (panelHost) {
          if (panelHost.style.display === "none") {
            panelHost.style.display = "";
          } else {
            panelHost.style.display = "none";
          }
        }
      }
    });
  }

  // -------------------------------------------------------------------------
  // Collapse / Close
  // -------------------------------------------------------------------------

  function toggleCollapse() {
    collapsed = !collapsed;
    const body = shadow.getElementById("panelBody");
    const btn = shadow.getElementById("collapseBtn");
    if (collapsed) {
      body.classList.add("collapsed");
      btn.textContent = "+";
      btn.title = "展開";
    } else {
      body.classList.remove("collapsed");
      btn.textContent = "\u2212";
      btn.title = "最小化";
    }
  }

  function closePanel() {
    if (panelHost) {
      panelHost.style.display = "none";
    }
  }

  function showPanel() {
    if (panelHost) {
      panelHost.style.display = "";
    }
  }

  // -------------------------------------------------------------------------
  // Timer
  // -------------------------------------------------------------------------

  function startTimer() {
    sendBg({ type: "TIMER_START" }).catch(err => {
      console.warn("Timer start failed:", err);
      showError("タイマーの開始に失敗しました");
    });
    const timerDisplay = shadow.getElementById("timerDisplay");
    const startBtn = shadow.getElementById("startTimerBtn");
    const stopBtn = shadow.getElementById("stopTimerBtn");
    if (timerDisplay) timerDisplay.classList.add("running");
    if (startBtn) startBtn.disabled = true;
    if (stopBtn) stopBtn.disabled = false;
    startTimerDisplay();
  }

  async function stopTimer() {
    await sendBg({ type: "TIMER_STOP" });
    clearInterval(timerInterval);
    timerInterval = null;
    shadow.getElementById("timerDisplay").classList.remove("running");
    shadow.getElementById("startTimerBtn").disabled = false;
    shadow.getElementById("stopTimerBtn").disabled = true;
    // Update display one final time from background state
    try {
      const bgState = await sendBg({ type: "GET_STATE" });
      renderTimer(bgState.timerElapsedMs || 0);
    } catch {}
  }

  function resetTimer() {
    sendBg({ type: "TIMER_RESET" }).catch(() => {});
    clearInterval(timerInterval);
    timerInterval = null;
    const timerDisplay = shadow.getElementById("timerDisplay");
    const startBtn = shadow.getElementById("startTimerBtn");
    const stopBtn = shadow.getElementById("stopTimerBtn");
    if (timerDisplay) timerDisplay.classList.remove("running");
    if (startBtn) startBtn.disabled = false;
    if (stopBtn) stopBtn.disabled = true;
    renderTimer(0);
  }

  // Restore timer display from background state (used when navigating between tests)
  async function restoreTimerFromState() {
    clearInterval(timerInterval);
    timerInterval = null;
    try {
      const bgState = await sendBg({ type: "GET_STATE" });
      const elapsed = bgState.timerElapsedMs || 0;
      renderTimer(elapsed);
      shadow.getElementById("timerDisplay").classList.remove("running");
      shadow.getElementById("startTimerBtn").disabled = false;
      shadow.getElementById("stopTimerBtn").disabled = true;
      if (bgState.timerRunning) {
        shadow.getElementById("timerDisplay").classList.add("running");
        shadow.getElementById("startTimerBtn").disabled = true;
        shadow.getElementById("stopTimerBtn").disabled = false;
        startTimerDisplay();
      }
    } catch (err) {
      renderTimer(0);
    }
  }

  function startTimerDisplay() {
    clearInterval(timerInterval);
    // Cache the start values from background to avoid polling on every tick
    let cachedElapsedMs = 0;
    let cachedStartedAt = Date.now();
    let pollCounter = 0;

    // Fetch initial values
    sendBg({ type: "GET_STATE" }).then(bgState => {
      if (bgState && bgState.timerRunning && bgState.timerStartedAt) {
        cachedElapsedMs = bgState.timerElapsedMs || 0;
        cachedStartedAt = bgState.timerStartedAt;
      }
    }).catch(() => {});

    timerInterval = setInterval(() => {
      try {
        if (!shadow) {
          clearInterval(timerInterval);
          timerInterval = null;
          return;
        }
        // Calculate locally most of the time for smooth display
        const elapsed = cachedElapsedMs + (Date.now() - cachedStartedAt);
        renderTimer(elapsed);

        // Sync with background every 2 seconds (20 ticks at 100ms) to stay accurate
        pollCounter++;
        if (pollCounter >= 20) {
          pollCounter = 0;
          sendBg({ type: "GET_STATE" }).then(bgState => {
            if (!bgState) return;
            if (bgState.timerRunning && bgState.timerStartedAt) {
              cachedElapsedMs = bgState.timerElapsedMs || 0;
              cachedStartedAt = bgState.timerStartedAt;
            } else {
              renderTimer(bgState.timerElapsedMs || 0);
              if (!bgState.timerRunning) {
                clearInterval(timerInterval);
                timerInterval = null;
              }
            }
          }).catch(() => {
            clearInterval(timerInterval);
            timerInterval = null;
          });
        }
      } catch {
        // Extension context invalidated, stop polling
        clearInterval(timerInterval);
        timerInterval = null;
      }
    }, 100);
  }

  function renderTimer(ms) {
    const totalSec = Math.floor(ms / 1000);
    const mins = String(Math.floor(totalSec / 60)).padStart(2, "0");
    const secs = String(totalSec % 60).padStart(2, "0");
    const tenths = Math.floor((ms % 1000) / 100);
    const el = shadow.getElementById("timerDisplay");
    if (el) el.textContent = `${mins}:${secs}.${tenths}`;
  }

  async function resumeTimerIfRunning() {
    try {
      const bgState = await sendBg({ type: "GET_STATE" });
      if (bgState.timerRunning) {
        shadow.getElementById("timerDisplay").classList.add("running");
        shadow.getElementById("startTimerBtn").disabled = true;
        shadow.getElementById("stopTimerBtn").disabled = false;
        startTimerDisplay();
      } else {
        renderTimer(bgState.timerElapsedMs || 0);
        shadow.getElementById("startTimerBtn").disabled = false;
        shadow.getElementById("stopTimerBtn").disabled = true;
      }
    } catch {}
  }

  function getCurrentTimerMs() {
    return sendBg({ type: "GET_STATE" }).then((bgState) => {
      if (bgState.timerRunning && bgState.timerStartedAt) {
        return (bgState.timerElapsedMs || 0) + (Date.now() - bgState.timerStartedAt);
      }
      return bgState.timerElapsedMs || 0;
    }).catch(() => 0);
  }

  // -------------------------------------------------------------------------
  // Copy prompt
  // -------------------------------------------------------------------------

  function copyPrompt() {
    const prompt = shadow.getElementById("testPrompt").textContent;
    navigator.clipboard.writeText(prompt).then(() => {
      const btn = shadow.getElementById("copyPromptBtn");
      btn.textContent = "✓ コピー済み";
      btn.style.background = "#dcfce7";
      btn.style.color = "#16a34a";
      btn.style.borderColor = "#86efac";
      setTimeout(() => {
        btn.textContent = "コピー";
        btn.style.background = "";
        btn.style.color = "";
        btn.style.borderColor = "";
      }, 2000);
    }).catch(() => {
      showError("クリップボードへのコピーに失敗しました");
    });
  }

  // -------------------------------------------------------------------------
  // Screenshots
  // -------------------------------------------------------------------------

  function updateCaptureCount(count) {
    const el = shadow.getElementById("captureCount");
    if (el) el.textContent = `(${count})`;
  }

  async function captureFullScreenshot() {
    const btn = shadow.getElementById("fullScreenshotBtn");
    btn.disabled = true;
    btn.textContent = "\uD83D\uDCF8 撮影中...";
    try {
      const result = await sendBg({ type: "FULL_SCREENSHOT" });
      updateCaptureCount(result.captureCount || 0);
      renderScreenshotThumbs(result.screenshots || []);
      btn.textContent = "\u2713 記録";
      setTimeout(() => {
        btn.textContent = "\uD83D\uDCF7 全画面";
        btn.disabled = false;
      }, 1000);
    } catch (err) {
      showError(err.message);
      btn.textContent = "\uD83D\uDCF7 全画面";
      btn.disabled = false;
    }
  }

  async function capturePartialScreenshot() {
    const btn = shadow.getElementById("partialScreenshotBtn");
    if (!btn) return;
    btn.disabled = true;
    btn.textContent = "\u2702 選択中...";

    // Hide panel temporarily so it's not in the screenshot
    if (panelHost) panelHost.style.display = "none";

    try {
      injectSelectionOverlay();
    } catch (err) {
      console.warn("Selection overlay injection failed:", err);
      if (panelHost) panelHost.style.display = "";
      showError("範囲選択の開始に失敗しました");
    }

    btn.textContent = "\u2702 部分";
    btn.disabled = false;
  }

  // -------------------------------------------------------------------------
  // Partial screenshot selection overlay (injected into main DOM)
  // -------------------------------------------------------------------------

  function injectSelectionOverlay() {
    removeSelectionOverlay();

    const overlay = document.createElement("div");
    overlay.id = "aixis-selection-overlay";
    overlay.classList.add("aixis-selection-overlay");

    const selectionBox = document.createElement("div");
    selectionBox.id = "aixis-selection-box";
    selectionBox.classList.add("aixis-selection-box");
    overlay.appendChild(selectionBox);

    const hint = document.createElement("div");
    hint.classList.add("aixis-selection-hint");
    hint.textContent = "ドラッグで範囲を選択してください（Escでキャンセル）";
    overlay.appendChild(hint);

    document.body.appendChild(overlay);

    let startX = 0;
    let startY = 0;
    let isSelecting = false;

    function onMouseDown(e) {
      e.preventDefault();
      isSelecting = true;
      startX = e.clientX;
      startY = e.clientY;
      selectionBox.style.left = startX + "px";
      selectionBox.style.top = startY + "px";
      selectionBox.style.width = "0";
      selectionBox.style.height = "0";
      selectionBox.style.display = "block";
    }

    function onMouseMove(e) {
      if (!isSelecting) return;
      e.preventDefault();
      const currentX = e.clientX;
      const currentY = e.clientY;
      const left = Math.min(startX, currentX);
      const top = Math.min(startY, currentY);
      const width = Math.abs(currentX - startX);
      const height = Math.abs(currentY - startY);
      selectionBox.style.left = left + "px";
      selectionBox.style.top = top + "px";
      selectionBox.style.width = width + "px";
      selectionBox.style.height = height + "px";
    }

    function onMouseUp(e) {
      if (!isSelecting) return;
      isSelecting = false;
      const currentX = e.clientX;
      const currentY = e.clientY;
      const rect = {
        x: Math.min(startX, currentX),
        y: Math.min(startY, currentY),
        w: Math.abs(currentX - startX),
        h: Math.abs(currentY - startY),
      };
      removeSelectionOverlay();

      // Show panel again
      if (panelHost) panelHost.style.display = "";

      if (rect.w > 10 && rect.h > 10) {
        sendBg({
          type: "PARTIAL_CAPTURE_COORDS",
          rect: rect,
          devicePixelRatio: window.devicePixelRatio || 1,
        }).then(result => {
          if (result && result.captureCount != null) {
            updateCaptureCount(result.captureCount);
          }
          if (result && result.screenshots) {
            renderScreenshotThumbs(result.screenshots);
          }
        }).catch(err => {
          showError("部分スクリーンショットの処理に失敗しました: " + err.message);
        });
      }
    }

    function onKeyDown(e) {
      if (e.key === "Escape") {
        removeSelectionOverlay();
        if (panelHost) panelHost.style.display = "";
      }
    }

    overlay.addEventListener("mousedown", onMouseDown);
    overlay.addEventListener("mousemove", onMouseMove);
    overlay.addEventListener("mouseup", onMouseUp);
    document.addEventListener("keydown", onKeyDown, { once: true });
  }

  function removeSelectionOverlay() {
    const existing = document.getElementById("aixis-selection-overlay");
    if (existing) existing.remove();
  }

  // -------------------------------------------------------------------------
  // Settings
  // -------------------------------------------------------------------------

  async function loadSettings() {
    try {
      const settings = await sendBg({ type: "GET_SETTINGS" });
      shadow.getElementById("apiKeyInput").value = settings.apiKey || "";
      shadow.getElementById("platformUrlInput").value = settings.platformUrl || "https://platform.aixis.jp";
      return settings;
    } catch {
      return { apiKey: "", platformUrl: "https://platform.aixis.jp" };
    }
  }

  async function saveSettings() {
    const apiKey = shadow.getElementById("apiKeyInput").value.trim();
    const platformUrl = shadow.getElementById("platformUrlInput").value.trim() || "https://platform.aixis.jp";

    if (!apiKey) {
      showError("APIキーを入力してください");
      return;
    }
    if (!apiKey.startsWith("axk_")) {
      showError("APIキーの形式が正しくありません（axk_... で始まる必要があります）");
      return;
    }

    await sendBg({ type: "SAVE_SETTINGS", apiKey, platformUrl });
    await loadToolList();
    showSection("setup");
  }

  // -------------------------------------------------------------------------
  // Tool picker
  // -------------------------------------------------------------------------

  async function loadToolList() {
    const toolList = shadow.getElementById("toolList");
    try {
      allTools = await sendBg({ type: "FETCH_TOOLS" });
      if (!allTools || allTools.length === 0) {
        toolList.innerHTML = '<div class="tool-list-empty">ツールが登録されていません。<br>ダッシュボードから追加してください。</div>';
        return;
      }
      shadow.getElementById("toolSearch").value = "";
      renderToolList(allTools);
    } catch (err) {
      toolList.innerHTML = '<div class="tool-list-empty" style="color:#991b1b;">ツール一覧の取得に失敗しました</div>';
      showError(err.message);
    }
  }

  function renderToolList(tools) {
    const toolList = shadow.getElementById("toolList");
    if (tools.length === 0) {
      toolList.innerHTML = '<div class="tool-list-empty">一致するツールがありません</div>';
      return;
    }

    toolList.innerHTML = tools.map((t) => {
      const isSelected = t.id === selectedToolId;
      const name = t.name_jp || t.name;
      const meta = [t.vendor, t.category_name_jp].filter(Boolean).join(" \u00B7 ");
      return `<div class="tool-list-item${isSelected ? " selected" : ""}" data-tool-id="${t.id}">
        <div class="tool-check"></div>
        <div class="tool-name">${escapeHtml(name)}</div>
        ${meta ? `<div class="tool-meta">${escapeHtml(meta)}</div>` : ""}
      </div>`;
    }).join("");

    if (allTools.length > 5) {
      toolList.innerHTML += `<div class="tool-count">${tools.length} / ${allTools.length} 件表示</div>`;
    }

    toolList.querySelectorAll(".tool-list-item").forEach((item) => {
      item.addEventListener("click", () => {
        selectedToolId = item.dataset.toolId;
        renderToolList(getFilteredTools());
      });
    });
  }

  function getFilteredTools() {
    const q = (shadow.getElementById("toolSearch").value || "").toLowerCase().trim();
    if (!q) return allTools;
    return allTools.filter((t) =>
      (t.name || "").toLowerCase().includes(q) ||
      (t.name_jp || "").toLowerCase().includes(q) ||
      (t.vendor || "").toLowerCase().includes(q) ||
      (t.category_name_jp || "").toLowerCase().includes(q)
    );
  }

  // -------------------------------------------------------------------------
  // Session management
  // -------------------------------------------------------------------------

  async function startSession() {
    if (!selectedToolId) {
      showError("ツールを選択してください");
      return;
    }

    const btn = shadow.getElementById("startSessionBtn");
    btn.disabled = true;
    btn.textContent = "接続中...";

    try {
      const result = await sendBg({
        type: "CREATE_SESSION",
        toolId: selectedToolId,
        profileId: "",
        recordingMode: "protocol",
      });

      showProtocolTest(result);
      showSection("protocol");
      resetTimer();
    } catch (err) {
      showError(err.message);
    } finally {
      btn.disabled = false;
      btn.textContent = "セッション開始";
    }
  }

  // -------------------------------------------------------------------------
  // Protocol test display
  // -------------------------------------------------------------------------

  function showProtocolTest(stateData) {
    let test, index, total;
    if (stateData.test !== undefined) {
      // From getCurrentTest / advanceTest / goToPrevTest / skipTest: { test, index, total }
      test = stateData.test;
      index = stateData.index || 0;
      total = stateData.total || 0;
    } else if (stateData.testCases) {
      // From createSession: { session, testCases, totalCases }
      test = stateData.testCases[0] || null;
      index = 0;
      total = stateData.totalCases || stateData.testCases.length || 0;
    } else {
      test = null;
      index = 0;
      total = 0;
    }

    if (!test) {
      updateProgress(index, total);
      shadow.getElementById("testCategory").textContent = "---";
      let msg;
      if (total === 0) {
        msg = "テストケースの読み込みに失敗しました。セッションを終了して再試行してください。";
      } else if (index >= total) {
        msg = "すべてのテストが完了しました。「セッションを終了する」を押してください。";
      } else {
        msg = "テストデータの読み込み中です。しばらくお待ちください...";
        // Try to reload test data
        sendBg({ type: "GET_CURRENT_TEST" }).then(data => {
          if (data && data.test) showProtocolTest(data);
        }).catch(() => {});
      }
      shadow.getElementById("testPrompt").textContent = msg;
      shadow.getElementById("testExpected").innerHTML = "";
      return;
    }

    updateProgress(index, total);

    // Enable/disable prev button
    const prevBtn = shadow.getElementById("prevTestBtn");
    if (prevBtn) prevBtn.disabled = index <= 0;

    const categoryBadge = shadow.getElementById("testCategory");
    categoryBadge.textContent = CATEGORY_NAMES[test.category] || test.category;
    categoryBadge.style.background = CATEGORY_COLORS[test.category] || "#6366f1";

    shadow.getElementById("testPrompt").textContent = test.prompt;

    const expectedEl = shadow.getElementById("testExpected");
    expectedEl.innerHTML = "";
    if (test.expected_behaviors && test.expected_behaviors.length) {
      const title = document.createElement("div");
      title.style.fontWeight = "600";
      title.style.marginBottom = "4px";
      title.textContent = "期待される動作:";
      expectedEl.appendChild(title);

      const ul = document.createElement("ul");
      for (const b of test.expected_behaviors.slice(0, 5)) {
        const li = document.createElement("li");
        li.textContent = b;
        ul.appendChild(li);
      }
      expectedEl.appendChild(ul);
    }
  }

  function updateProgress(current, total) {
    const pct = total > 0 ? (Math.min(current, total) / total * 100).toFixed(0) : 0;
    shadow.getElementById("progressFill").style.width = pct + "%";
    // Display 1-indexed but cap at total so it never shows e.g. "18 / 17"
    const displayNum = Math.min(current + 1, total);
    shadow.getElementById("progressText").textContent = `${displayNum} / ${total}`;
  }

  // -------------------------------------------------------------------------
  // Next / Skip test
  // -------------------------------------------------------------------------

  async function nextTest() {
    const btn = shadow.getElementById("nextTestBtn");
    const skipBtn = shadow.getElementById("skipTestBtn");
    btn.disabled = true;
    skipBtn.disabled = true;
    btn.textContent = "送信中...";

    try {
      const elapsedMs = await getCurrentTimerMs();

      const result = await sendBg({
        type: "NEXT_TEST",
        observation: {
          responseText: null,
          responseTimeMs: elapsedMs,
        },
      });

      if (result.done) {
        resetTimer();
        await endSessionDirect();
      } else {
        showProtocolTest(result);
        await restoreTimerFromState();
        await refreshScreenshotThumbs();
      }
    } catch (err) {
      showError(err.message);
    } finally {
      btn.disabled = false;
      skipBtn.disabled = false;
      btn.textContent = "次へ";
    }
  }

  async function prevTest() {
    const btn = shadow.getElementById("prevTestBtn");
    btn.disabled = true;

    try {
      const result = await sendBg({ type: "PREV_TEST" });
      if (result.error) {
        return;
      }
      showProtocolTest(result);
      await restoreTimerFromState();
      await refreshScreenshotThumbs();
    } catch (err) {
      showError(err.message);
    } finally {
      btn.disabled = false;
    }
  }

  async function skipTest() {
    const btn = shadow.getElementById("skipTestBtn");
    btn.disabled = true;

    try {
      const result = await sendBg({ type: "SKIP_TEST", reason: "テスターがスキップ" });
      if (result.done) {
        resetTimer();
        await endSessionDirect();
      } else {
        showProtocolTest(result);
        await restoreTimerFromState();
        await refreshScreenshotThumbs();
      }
    } catch (err) {
      showError(err.message);
    } finally {
      btn.disabled = false;
    }
  }

  // Update screenshot thumbnails for current test
  async function refreshScreenshotThumbs() {
    try {
      const result = await sendBg({ type: "GET_TEST_SCREENSHOTS" });
      const screenshots = result.screenshots || [];
      updateCaptureCount(screenshots.length);
      renderScreenshotThumbs(screenshots);
    } catch (err) {
      console.warn("Failed to refresh screenshots:", err);
    }
  }

  // Store screenshots data for preview
  let _currentScreenshots = [];

  function renderScreenshotThumbs(screenshots) {
    const container = shadow.getElementById("screenshotThumbs");
    if (!container) return;
    _currentScreenshots = screenshots;

    if (!screenshots.length) {
      container.innerHTML = "";
      return;
    }

    container.innerHTML = screenshots.map((s, i) => {
      const time = new Date(s.timestamp).toLocaleTimeString("ja-JP", { hour: "2-digit", minute: "2-digit" });
      const hasThumb = !!s.thumbDataUrl;
      return `<div class="thumb-item ${hasThumb ? 'has-preview' : ''}" data-idx="${i}" title="${s.pageTitle || ''}">
        ${hasThumb ? `<img src="${s.thumbDataUrl}" class="thumb-img" alt="">` : `<span class="thumb-type">📷${i + 1}</span>`}
        <span class="thumb-time">${time}</span>
        <button class="thumb-delete" data-idx="${i}" title="削除">×</button>
      </div>`;
    }).join("");

    // Add click handlers for preview (on the image/text, not the delete button)
    container.querySelectorAll(".thumb-item").forEach(el => {
      el.addEventListener("click", (e) => {
        if (e.target.classList.contains("thumb-delete")) return; // handled separately
        const idx = parseInt(el.dataset.idx);
        if (_currentScreenshots[idx]?.thumbDataUrl) {
          showScreenshotPreview(_currentScreenshots[idx]);
        }
      });
    });

    // Add click handlers for delete
    container.querySelectorAll(".thumb-delete").forEach(btn => {
      btn.addEventListener("click", async (e) => {
        e.stopPropagation();
        const idx = parseInt(btn.dataset.idx);
        await deleteScreenshot(idx);
      });
    });
  }

  async function deleteScreenshot(idx) {
    try {
      const result = await sendBg({ type: "DELETE_SCREENSHOT", index: idx });
      if (!result || result.error) {
        showError((result && result.error) || "スクリーンショットの削除に失敗しました");
        return;
      }
      updateCaptureCount(result.captureCount || 0);
      renderScreenshotThumbs(result.screenshots || []);
    } catch (err) {
      showError(err.message || "スクリーンショットの削除に失敗しました");
    }
  }

  function showScreenshotPreview(screenshot) {
    if (!screenshot?.thumbDataUrl) return;

    // Remove any existing overlay
    const existing = shadow.getElementById("screenshotPreview");
    if (existing) existing.remove();

    // Create overlay inside shadow root
    const overlay = document.createElement("div");
    overlay.id = "screenshotPreview";
    overlay.style.cssText = "position:fixed;top:0;left:0;width:100vw;height:100vh;background:rgba(0,0,0,0.85);z-index:1000000;display:flex;align-items:center;justify-content:center;flex-direction:column;cursor:pointer;pointer-events:auto;";

    const time = new Date(screenshot.timestamp).toLocaleTimeString("ja-JP", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    const typeLabel = screenshot.type === "partial" ? "部分" : "全画面";

    // Build image
    const img = document.createElement("img");
    img.src = screenshot.thumbDataUrl;
    img.style.cssText = "max-width:85vw;max-height:80vh;border-radius:8px;box-shadow:0 4px 20px rgba(0,0,0,0.5);pointer-events:none;";

    // Build caption
    const caption = document.createElement("div");
    caption.style.cssText = "color:#fff;text-align:center;margin-top:10px;font-size:13px;pointer-events:none;";
    caption.textContent = `${typeLabel} — ${time} — ${screenshot.pageTitle || ""}`;

    // Close button
    const closeBtn = document.createElement("div");
    closeBtn.style.cssText = "position:absolute;top:16px;right:20px;color:#fff;font-size:28px;cursor:pointer;width:40px;height:40px;display:flex;align-items:center;justify-content:center;background:rgba(255,255,255,0.15);border-radius:50%;";
    closeBtn.textContent = "✕";

    overlay.appendChild(img);
    overlay.appendChild(caption);
    overlay.appendChild(closeBtn);

    // Close on any click on overlay or close button
    const closeOverlay = () => overlay.remove();
    overlay.addEventListener("click", closeOverlay);
    closeBtn.addEventListener("click", closeOverlay);

    shadow.appendChild(overlay);
  }

  // -------------------------------------------------------------------------
  // Session end
  // -------------------------------------------------------------------------

  async function endSession() {
    if (!confirm("セッションを終了しますか？")) return;
    await endSessionDirect();
  }

  async function endSessionDirect() {
    try {
      let result = {};
      try {
        result = await sendBg({ type: "COMPLETE_SESSION" });
      } catch (err) {
        console.warn("Complete session API call failed:", err);
        // Continue to show completion screen even if API fails
        // The session data is already saved locally
      }

      let bgState = {};
      try {
        bgState = await sendBg({ type: "GET_STATE" });
      } catch {
        bgState = {};
      }

      // Show total tests executed (currentTestIndex tracks how many tests were advanced through)
      const totalExecuted = bgState.currentTestIndex || bgState.observationCount || 0;
      const summaryTotalEl = shadow.getElementById("summaryTotal");
      if (summaryTotalEl) summaryTotalEl.textContent = totalExecuted;

      const summaryStatusEl = shadow.getElementById("summaryStatus");
      if (summaryStatusEl) {
        const status = result.status || bgState.currentSession?.status || "完了";
        summaryStatusEl.textContent = status === "scoring" ? "採点中" : status;
      }

      try {
        const settings = await sendBg({ type: "GET_SETTINGS" });
        if (bgState.currentSession) {
          const dashLink = shadow.getElementById("dashboardLink");
          if (dashLink) {
            dashLink.href = `${settings.platformUrl}/dashboard/audits/${bgState.currentSession.id}`;
          }
        }
      } catch {}

      showSection("complete");
    } catch (err) {
      showError(err.message);
    }
  }

  async function newSession() {
    await sendBg({ type: "RESET_SESSION" });
    selectedToolId = "";
    resetTimer();
    updateCaptureCount(0);
    await loadToolList();
    showSection("setup");
  }

  // -------------------------------------------------------------------------
  // Event listeners
  // -------------------------------------------------------------------------

  function setupEventListeners() {
    shadow.getElementById("collapseBtn").addEventListener("click", toggleCollapse);
    shadow.getElementById("closeBtn").addEventListener("click", closePanel);
    shadow.getElementById("saveSettingsBtn").addEventListener("click", saveSettings);
    shadow.getElementById("startSessionBtn").addEventListener("click", startSession);
    shadow.getElementById("changeSettingsBtn").addEventListener("click", () => showSection("settings"));
    shadow.getElementById("copyPromptBtn").addEventListener("click", copyPrompt);
    shadow.getElementById("startTimerBtn").addEventListener("click", startTimer);
    shadow.getElementById("stopTimerBtn").addEventListener("click", stopTimer);
    shadow.getElementById("resetTimerBtn").addEventListener("click", async () => {
      await sendBg({ type: "TIMER_RESET" });
      resetTimer();
    });
    shadow.getElementById("fullScreenshotBtn").addEventListener("click", captureFullScreenshot);
    shadow.getElementById("partialScreenshotBtn").addEventListener("click", capturePartialScreenshot);
    shadow.getElementById("prevTestBtn").addEventListener("click", prevTest);
    shadow.getElementById("nextTestBtn").addEventListener("click", nextTest);
    shadow.getElementById("skipTestBtn").addEventListener("click", skipTest);
    shadow.getElementById("endProtocolBtn").addEventListener("click", endSession);
    shadow.getElementById("newSessionBtn").addEventListener("click", newSession);

    shadow.getElementById("toolSearch").addEventListener("input", () => {
      renderToolList(getFilteredTools());
    });
  }

  // -------------------------------------------------------------------------
  // Messages from background / popup
  // -------------------------------------------------------------------------

  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    switch (message.type) {
      case "SHOW_PANEL":
        showPanel();
        sendResponse({ ok: true });
        break;

      case "INJECT_SELECTION_OVERLAY":
        injectSelectionOverlay();
        sendResponse({ ok: true });
        break;

      case "CAPTURE_COUNT_UPDATE":
        updateCaptureCount(message.count);
        if (message.screenshots) renderScreenshotThumbs(message.screenshots);
        sendResponse({ ok: true });
        break;

      case "PARTIAL_CAPTURE_DONE":
        updateCaptureCount(message.captureCount || 0);
        if (message.screenshots) renderScreenshotThumbs(message.screenshots);
        sendResponse({ ok: true });
        break;

      case "HIDE_PANEL":
        if (panelHost) panelHost.style.display = "none";
        sendResponse({ ok: true });
        break;

      case "RECORDING_STARTED":
        // Panel is already visible; just ensure it shows protocol section
        sendResponse({ ok: true });
        break;

      case "RECORDING_STOPPED":
        sendResponse({ ok: true });
        break;
    }
    return true;
  });

  // -------------------------------------------------------------------------
  // Initialize
  // -------------------------------------------------------------------------

  async function init() {
    createPanel();

    try {
      const bgState = await sendBg({ type: "GET_STATE" });

      if (bgState.currentSession) {
        if (bgState.currentSession.status === "completed" || bgState.currentSession.status === "scoring") {
          const settings = await sendBg({ type: "GET_SETTINGS" });
          shadow.getElementById("summaryTotal").textContent = bgState.currentTestIndex || bgState.observationCount || 0;
          shadow.getElementById("summaryStatus").textContent = bgState.currentSession.status === "scoring" ? "採点中" : "完了";
          shadow.getElementById("dashboardLink").href = `${settings.platformUrl}/dashboard/audits/${bgState.currentSession.id}`;
          showSection("complete");
        } else {
          const testData = await sendBg({ type: "GET_CURRENT_TEST" });
          showProtocolTest(testData);
          updateCaptureCount(bgState.captureCount || 0);
          showSection("protocol");
          try { await resumeTimerIfRunning(); } catch {}
          try { await refreshScreenshotThumbs(); } catch {}
        }
      } else {
        const settings = await loadSettings();
        if (settings.apiKey) {
          await loadToolList();
          showSection("setup");
        } else {
          showSection("settings");
        }
      }
    } catch {
      const settings = await loadSettings();
      if (settings.apiKey) {
        showSection("setup");
      } else {
        showSection("settings");
      }
    }
  }

  init();
})();
