/**
 * Aixis Chrome Extension — Popup UI Logic
 *
 * Manages the popup state machine:
 * Settings → Setup → Protocol/Freeform Recording → Complete
 */

// ---------------------------------------------------------------------------
// DOM references
// ---------------------------------------------------------------------------

const $ = (sel) => document.querySelector(sel);
const sections = {
  settings: $("#settingsSection"),
  setup: $("#setupSection"),
  protocol: $("#protocolSection"),
  freeform: $("#freeformSection"),
  complete: $("#completeSection"),
};

const statusBadge = $("#statusBadge");
const errorMsg = $("#errorMsg");

// ---------------------------------------------------------------------------
// Section management
// ---------------------------------------------------------------------------

function showSection(name) {
  Object.values(sections).forEach((s) => s.classList.remove("active"));
  if (sections[name]) sections[name].classList.add("active");
}

function setBadge(text, className) {
  statusBadge.textContent = text;
  statusBadge.className = `badge ${className}`;
}

function showError(msg) {
  // Provide helpful hints for common errors
  let displayMsg = msg;
  if (msg.includes("401") || msg.includes("Unauthorized")) {
    displayMsg = "認証エラー: APIキーが無効です。正しいキーを設定してください。";
  } else if (msg.includes("403") || msg.includes("agent:write")) {
    displayMsg = "権限エラー: APIキーに agent:write スコープが必要です。ダッシュボードで再発行してください。";
  } else if (msg.includes("500") || msg.includes("Internal server")) {
    displayMsg = "サーバーエラー: プラットフォームに接続できません。URLとAPIキーを確認してください。";
  } else if (msg.includes("Failed to fetch") || msg.includes("NetworkError")) {
    displayMsg = "ネットワークエラー: プラットフォームURLに接続できません。URLを確認してください。";
  }

  errorMsg.textContent = displayMsg;
  errorMsg.classList.add("visible");
  setTimeout(() => errorMsg.classList.remove("visible"), 8000);
}

// ---------------------------------------------------------------------------
// Helper: send message to background
// ---------------------------------------------------------------------------

function sendBg(message) {
  return new Promise((resolve, reject) => {
    chrome.runtime.sendMessage(message, (response) => {
      if (chrome.runtime.lastError) {
        reject(new Error(chrome.runtime.lastError.message));
      } else if (response?.error) {
        reject(new Error(response.error));
      } else {
        resolve(response);
      }
    });
  });
}

// ---------------------------------------------------------------------------
// Settings
// ---------------------------------------------------------------------------

async function loadSettings() {
  const settings = await sendBg({ type: "GET_SETTINGS" });
  $("#apiKeyInput").value = settings.apiKey || "";
  $("#platformUrlInput").value = settings.platformUrl || "https://platform.aixis.jp";
}

async function saveSettings() {
  const apiKey = $("#apiKeyInput").value.trim();
  const platformUrl = $("#platformUrlInput").value.trim() || "https://platform.aixis.jp";

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

// ---------------------------------------------------------------------------
// Tool picker (searchable list)
// ---------------------------------------------------------------------------

let allTools = [];
let selectedToolId = "";

async function loadToolList() {
  const toolList = $("#toolList");
  const toolSearch = $("#toolSearch");
  try {
    allTools = await sendBg({ type: "FETCH_TOOLS" });

    if (!allTools || allTools.length === 0) {
      toolList.innerHTML = '<div class="tool-list-empty">ツールが登録されていません。<br>ダッシュボードのツール管理から追加してください。</div>';
      return;
    }

    toolSearch.value = "";
    renderToolList(allTools);
  } catch (err) {
    toolList.innerHTML = '<div class="tool-list-empty" style="color: #991b1b;">ツール一覧の取得に失敗しました</div>';
    showError(err.message);
  }
}

function renderToolList(tools) {
  const toolList = $("#toolList");

  if (tools.length === 0) {
    toolList.innerHTML = '<div class="tool-list-empty">一致するツールがありません</div>';
    return;
  }

  toolList.innerHTML = tools.map(t => {
    const isSelected = t.id === selectedToolId;
    const name = t.name_jp || t.name;
    const meta = [t.vendor, t.category_name_jp].filter(Boolean).join(" · ");
    return `<div class="tool-list-item${isSelected ? ' selected' : ''}" data-tool-id="${t.id}">
      <div class="tool-check"></div>
      <div class="tool-name">${escapeHtml(name)}</div>
      ${meta ? `<div class="tool-meta">${escapeHtml(meta)}</div>` : ''}
    </div>`;
  }).join("");

  // Add count footer
  if (allTools.length > 5) {
    toolList.innerHTML += `<div class="tool-count">${tools.length} / ${allTools.length} 件表示</div>`;
  }

  // Click handlers
  toolList.querySelectorAll(".tool-list-item").forEach(item => {
    item.addEventListener("click", () => {
      selectedToolId = item.dataset.toolId;
      $("#toolSelectValue").value = selectedToolId;
      renderToolList(getFilteredTools());
    });
  });
}

function getFilteredTools() {
  const q = ($("#toolSearch").value || "").toLowerCase().trim();
  if (!q) return allTools;
  return allTools.filter(t =>
    (t.name || "").toLowerCase().includes(q) ||
    (t.name_jp || "").toLowerCase().includes(q) ||
    (t.vendor || "").toLowerCase().includes(q) ||
    (t.category_name_jp || "").toLowerCase().includes(q)
  );
}

function escapeHtml(str) {
  if (!str) return "";
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

// Search input handler
$("#toolSearch").addEventListener("input", () => {
  renderToolList(getFilteredTools());
});

// ---------------------------------------------------------------------------
// Session start
// ---------------------------------------------------------------------------

async function startSession() {
  const toolId = selectedToolId || $("#toolSelectValue").value;
  if (!toolId) {
    showError("ツールを選択してください");
    return;
  }

  const mode = document.querySelector('input[name="mode"]:checked').value;

  try {
    setBadge("接続中...", "badge-scoring");
    const result = await sendBg({
      type: "CREATE_SESSION",
      toolId,
      profileId: "",
      recordingMode: mode,
    });

    if (mode === "protocol") {
      setBadge("記録中", "badge-recording");
      showProtocolTest(result);
      showSection("protocol");
    } else {
      setBadge("記録中", "badge-recording");
      showSection("freeform");
      startFreeformPolling();
    }
  } catch (err) {
    setBadge("待機中", "badge-idle");
    showError(err.message);
  }
}

// ---------------------------------------------------------------------------
// Protocol recording
// ---------------------------------------------------------------------------

function showProtocolTest(stateData) {
  const { test, index, total } = stateData.test
    ? stateData
    : { test: stateData.testCases?.[0], index: 0, total: stateData.totalCases || 0 };

  if (!test) {
    // All tests done
    endSession();
    return;
  }

  const pct = total > 0 ? ((index / total) * 100).toFixed(0) : 0;
  $("#progressFill").style.width = `${pct}%`;
  $("#progressText").textContent = `${index + 1} / ${total}`;

  const CATEGORY_NAMES = {
    dialect: "方言", long_input: "長文", contradictory: "矛盾",
    ambiguous: "曖昧", keigo_mixing: "敬語混合", unicode_edge: "Unicode",
    business_jp: "商習慣", multi_step: "複合指示", broken_grammar: "文法破壊",
    freeform: "フリー",
  };

  $("#testCategory").textContent = CATEGORY_NAMES[test.category] || test.category;
  $("#testPrompt").textContent = test.prompt;

  const expectedList = $("#testExpected");
  expectedList.innerHTML = "";
  if (test.expected_behaviors?.length) {
    const title = document.createElement("div");
    title.style.fontWeight = "600";
    title.style.marginBottom = "4px";
    title.textContent = "期待される動作:";
    expectedList.appendChild(title);

    const ul = document.createElement("ul");
    ul.style.paddingLeft = "0";
    for (const b of test.expected_behaviors.slice(0, 5)) {
      const li = document.createElement("li");
      li.textContent = b;
      ul.appendChild(li);
    }
    expectedList.appendChild(ul);
  }
}

async function nextTest() {
  const btn = $("#nextTestBtn");
  const skipBtn = $("#skipTestBtn");
  btn.disabled = true;
  skipBtn.disabled = true;
  btn.textContent = "移動中...";
  try {
    const result = await sendBg({ type: "NEXT_TEST", observation: {} });

    if (result.done) {
      await endSession();
    } else {
      showProtocolTest(result);
    }
  } catch (err) {
    showError(err.message);
  } finally {
    btn.disabled = false;
    skipBtn.disabled = false;
    btn.textContent = "次へ";
  }
}

async function skipTest() {
  const btn = $("#skipTestBtn");
  btn.disabled = true;
  try {
    const result = await sendBg({ type: "SKIP_TEST", reason: "テスターがスキップ" });
    if (result.done) {
      await endSession();
    } else {
      showProtocolTest(result);
    }
  } catch (err) {
    showError(err.message);
  } finally {
    btn.disabled = false;
  }
}

function copyPrompt() {
  const prompt = $("#testPrompt").textContent;
  navigator.clipboard.writeText(prompt).then(() => {
    const btn = $("#copyPromptBtn");
    btn.textContent = "コピー済み!";
    setTimeout(() => (btn.textContent = "プロンプトをコピー"), 1500);
  });
}

// ---------------------------------------------------------------------------
// Freeform recording
// ---------------------------------------------------------------------------

let freeformInterval = null;

function startFreeformPolling() {
  updateFreeformCount();
  freeformInterval = setInterval(updateFreeformCount, 2000);
}

async function updateFreeformCount() {
  try {
    const state = await sendBg({ type: "GET_STATE" });
    $("#freeformCount").textContent = state.observationCount || 0;
  } catch {}
}

async function stopFreeform() {
  clearInterval(freeformInterval);
  await endSession();
}

// ---------------------------------------------------------------------------
// Session completion
// ---------------------------------------------------------------------------

async function endSession() {
  try {
    setBadge("スコアリング中...", "badge-scoring");
    const result = await sendBg({ type: "COMPLETE_SESSION" });

    setBadge("完了", "badge-done");

    const state = await sendBg({ type: "GET_STATE" });
    $("#summaryTotal").textContent = state.observationCount || 0;
    $("#summaryStatus").textContent = result.status === "scoring" ? "採点中" : result.status;

    // Dashboard link
    const settings = await sendBg({ type: "GET_SETTINGS" });
    if (state.currentSession) {
      $("#dashboardLink").href =
        `${settings.platformUrl}/dashboard/audits/${state.currentSession.id}`;
    }

    showSection("complete");
  } catch (err) {
    showError(err.message);
    setBadge("エラー", "badge-idle");
  }
}

async function newSession() {
  await sendBg({ type: "RESET_SESSION" });
  selectedToolId = "";
  setBadge("待機中", "badge-idle");
  await loadToolList();
  showSection("setup");
}

// ---------------------------------------------------------------------------
// Manual screenshot capture
// ---------------------------------------------------------------------------

async function captureManualScreenshot(btn) {
  const origText = btn.textContent;
  btn.disabled = true;
  btn.textContent = "📸 撮影中...";
  btn.classList.add("capturing");

  try {
    const result = await sendBg({
      type: "MANUAL_SCREENSHOT",
      label: "",
    });

    btn.textContent = "✓ 記録しました";
    setTimeout(() => {
      btn.textContent = origText;
      btn.classList.remove("capturing");
      btn.disabled = false;
    }, 1200);
  } catch (err) {
    showError(err.message);
    btn.textContent = origText;
    btn.classList.remove("capturing");
    btn.disabled = false;
  }
}

// ---------------------------------------------------------------------------
// Event listeners
// ---------------------------------------------------------------------------

$("#saveSettingsBtn").addEventListener("click", saveSettings);
$("#startSessionBtn").addEventListener("click", startSession);
$("#changeSettingsBtn").addEventListener("click", () => showSection("settings"));
$("#nextTestBtn").addEventListener("click", nextTest);
$("#skipTestBtn").addEventListener("click", skipTest);
$("#copyPromptBtn").addEventListener("click", copyPrompt);
$("#endProtocolBtn").addEventListener("click", endSession);
$("#stopFreeformBtn").addEventListener("click", stopFreeform);
$("#newSessionBtn").addEventListener("click", newSession);
$("#manualCaptureBtn1").addEventListener("click", (e) => captureManualScreenshot(e.target));
$("#manualCaptureBtn2").addEventListener("click", (e) => captureManualScreenshot(e.target));

// ---------------------------------------------------------------------------
// Initialize
// ---------------------------------------------------------------------------

async function init() {
  await loadSettings();

  // Check if there's an active session
  const state = await sendBg({ type: "GET_STATE" });

  if (state.currentSession) {
    // Resume active session
    if (state.currentSession.status === "completed" || state.currentSession.status === "scoring") {
      setBadge(state.currentSession.status === "scoring" ? "採点中" : "完了", "badge-done");
      $("#summaryTotal").textContent = state.observationCount || 0;
      $("#summaryStatus").textContent = state.currentSession.status === "scoring" ? "採点中" : "完了";
      const settings = await sendBg({ type: "GET_SETTINGS" });
      $("#dashboardLink").href = `${settings.platformUrl}/dashboard/audits/${state.currentSession.id}`;
      showSection("complete");
    } else if (state.currentSession.recordingMode === "protocol") {
      setBadge("記録中", "badge-recording");
      const testData = await sendBg({ type: "GET_CURRENT_TEST" });
      showProtocolTest(testData);
      showSection("protocol");
    } else {
      setBadge("記録中", "badge-recording");
      showSection("freeform");
      startFreeformPolling();
    }
  } else {
    // Check if API key is configured
    const settings = await sendBg({ type: "GET_SETTINGS" });
    if (settings.apiKey) {
      await loadToolList();
      showSection("setup");
    } else {
      showSection("settings");
    }
  }
}

init();
