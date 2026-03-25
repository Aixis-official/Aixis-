/**
 * Aixis Chrome Extension v2 — Popup UI Logic
 *
 * Manages the popup state machine:
 * Settings -> Setup -> Protocol Test -> Complete
 *
 * Features: timer, screenshots (full/partial/auto), response text capture.
 */

// ---------------------------------------------------------------------------
// DOM helpers
// ---------------------------------------------------------------------------

const $ = (sel) => document.querySelector(sel);
const sections = {
  settings: $("#settingsSection"),
  setup: $("#setupSection"),
  protocol: $("#protocolSection"),
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
  let displayMsg = msg;
  if (msg.includes("401") || msg.includes("Unauthorized")) {
    displayMsg = "認証エラー: APIキーが無効です。正しいキーを設定してください。";
  } else if (msg.includes("403") || msg.includes("agent:write")) {
    displayMsg = "権限エラー: APIキーに agent:write スコープが必要です。";
  } else if (msg.includes("500") || msg.includes("Internal server")) {
    displayMsg = "サーバーエラー: プラットフォームに接続できません。";
  } else if (msg.includes("Failed to fetch") || msg.includes("NetworkError")) {
    displayMsg = "ネットワークエラー: プラットフォームURLに接続できません。";
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
// Timer (local display — timerStart stored in background for persistence)
// ---------------------------------------------------------------------------

let timerStart = null;
let timerInterval = null;
let elapsedMs = 0;

function startTimer() {
  timerStart = Date.now();
  elapsedMs = 0;
  // Store timer start in background for persistence across popup close/open
  sendBg({ type: "SET_TIMER_START", timestamp: timerStart });

  $("#timerDisplay").classList.add("running");
  $("#stopTimerBtn").disabled = false;

  clearInterval(timerInterval);
  timerInterval = setInterval(() => {
    elapsedMs = Date.now() - timerStart;
    renderTimer(elapsedMs);
  }, 100);
}

function stopTimer() {
  if (timerStart) {
    elapsedMs = Date.now() - timerStart;
  }
  clearInterval(timerInterval);
  timerInterval = null;
  $("#timerDisplay").classList.remove("running");
  $("#stopTimerBtn").disabled = true;
  renderTimer(elapsedMs);
}

function resetTimer() {
  clearInterval(timerInterval);
  timerInterval = null;
  timerStart = null;
  elapsedMs = 0;
  sendBg({ type: "SET_TIMER_START", timestamp: null });
  $("#timerDisplay").classList.remove("running");
  $("#stopTimerBtn").disabled = true;
  renderTimer(0);
}

function renderTimer(ms) {
  const totalSec = Math.floor(ms / 1000);
  const mins = String(Math.floor(totalSec / 60)).padStart(2, "0");
  const secs = String(totalSec % 60).padStart(2, "0");
  const tenths = Math.floor((ms % 1000) / 100);
  $("#timerDisplay").textContent = `${mins}:${secs}.${tenths}`;
}

// Resume timer if popup was closed and reopened while timer was running
async function resumeTimer() {
  try {
    const bgState = await sendBg({ type: "GET_STATE" });
    if (bgState.timerStart) {
      timerStart = bgState.timerStart;
      elapsedMs = Date.now() - timerStart;
      $("#timerDisplay").classList.add("running");
      $("#stopTimerBtn").disabled = false;

      clearInterval(timerInterval);
      timerInterval = setInterval(() => {
        elapsedMs = Date.now() - timerStart;
        renderTimer(elapsedMs);
      }, 100);
    }
  } catch {}
}

// ---------------------------------------------------------------------------
// Copy prompt — auto starts timer
// ---------------------------------------------------------------------------

function copyPrompt() {
  const prompt = $("#testPrompt").textContent;
  navigator.clipboard.writeText(prompt).then(() => {
    const btn = $("#copyPromptBtn");
    btn.textContent = "コピー済み!";
    setTimeout(() => (btn.textContent = "コピー"), 1500);
    startTimer();
  });
}

// ---------------------------------------------------------------------------
// Screenshots
// ---------------------------------------------------------------------------

let captureCount = 0;
let autoCaptureActive = false;

function updateCaptureCount(count) {
  captureCount = count;
  const badge = $("#captureCountBadge");
  badge.textContent = count;
  if (count > 0) {
    badge.classList.add("has-captures");
  } else {
    badge.classList.remove("has-captures");
  }
}

async function captureFullScreenshot() {
  const btn = $("#fullScreenshotBtn");
  btn.disabled = true;
  btn.textContent = "📸 撮影中...";
  try {
    const result = await sendBg({ type: "FULL_SCREENSHOT" });
    updateCaptureCount(result.captureCount || captureCount + 1);
    btn.textContent = "✓ 記録";
    setTimeout(() => { btn.textContent = "📷 全画面"; btn.disabled = false; }, 1000);
  } catch (err) {
    showError(err.message);
    btn.textContent = "📷 全画面";
    btn.disabled = false;
  }
}

async function capturePartialScreenshot() {
  const btn = $("#partialScreenshotBtn");
  btn.disabled = true;
  btn.textContent = "✂️ 選択中...";
  try {
    await sendBg({ type: "START_PARTIAL_CAPTURE" });
    // The content script will handle the selection overlay.
    // When complete, background receives PARTIAL_CAPTURE_COORDS and does the crop.
    // We wait for the result via a listener.
    btn.textContent = "✂️ 部分";
    btn.disabled = false;
  } catch (err) {
    showError(err.message);
    btn.textContent = "✂️ 部分";
    btn.disabled = false;
  }
}

function toggleAutoCapture() {
  const toggle = $(".auto-capture-toggle");
  const checkbox = $("#autoCaptureToggle");

  if (autoCaptureActive) {
    // Stop auto-capture
    autoCaptureActive = false;
    checkbox.checked = false;
    toggle.classList.remove("active");
    sendBg({ type: "STOP_AUTO_CAPTURE" });
  } else {
    // Start auto-capture (5-second interval in background)
    autoCaptureActive = true;
    checkbox.checked = true;
    toggle.classList.add("active");
    sendBg({ type: "START_AUTO_CAPTURE", intervalMs: 5000 });
  }
}

// Listen for capture count updates from background
chrome.runtime.onMessage.addListener((message) => {
  if (message.type === "CAPTURE_COUNT_UPDATE") {
    updateCaptureCount(message.count);
  }
  if (message.type === "PARTIAL_CAPTURE_DONE") {
    updateCaptureCount(message.captureCount || captureCount + 1);
  }
});

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
      toolList.innerHTML = '<div class="tool-list-empty">ツールが登録されていません。<br>ダッシュボードから追加してください。</div>';
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

  if (allTools.length > 5) {
    toolList.innerHTML += `<div class="tool-count">${tools.length} / ${allTools.length} 件表示</div>`;
  }

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

$("#toolSearch").addEventListener("input", () => {
  renderToolList(getFilteredTools());
});

// ---------------------------------------------------------------------------
// Session start (protocol only)
// ---------------------------------------------------------------------------

async function startSession() {
  const toolId = selectedToolId || $("#toolSelectValue").value;
  if (!toolId) {
    showError("ツールを選択してください");
    return;
  }

  try {
    setBadge("接続中...", "badge-scoring");
    const result = await sendBg({
      type: "CREATE_SESSION",
      toolId,
      profileId: "",
      recordingMode: "protocol",
    });

    setBadge("記録中", "badge-recording");
    showProtocolTest(result);
    showSection("protocol");
  } catch (err) {
    setBadge("待機中", "badge-idle");
    showError(err.message);
  }
}

// ---------------------------------------------------------------------------
// Protocol test display
// ---------------------------------------------------------------------------

const CATEGORY_NAMES = {
  slide_basic: "基本作成", slide_structure: "構成力", slide_japanese: "日本語",
  slide_accuracy: "正確性", slide_advanced: "応用機能",
  dialect: "方言", long_input: "長文", contradictory: "矛盾",
  ambiguous: "曖昧", keigo_mixing: "敬語混合", unicode_edge: "Unicode",
  business_jp: "商習慣", multi_step: "複合指示", broken_grammar: "文法破壊",
  freeform: "フリー", protocol: "プロトコル",
};

function showProtocolTest(stateData) {
  const { test, index, total } = stateData.test
    ? stateData
    : { test: stateData.testCases?.[0], index: 0, total: stateData.totalCases || 0 };

  if (!test) {
    // No test available — show message instead of auto-ending
    updateProgress(index, total);
    $("#testCategory").textContent = "---";
    $("#testPrompt").textContent = total === 0
      ? "テストケースの読み込みに失敗しました。セッションを終了して再試行してください。"
      : "すべてのテストが完了しました。";
    return;
  }

  updateProgress(index, total);

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

function updateProgress(current, total) {
  const pct = total > 0 ? ((current / total) * 100).toFixed(0) : 0;
  $("#progressFill").style.width = `${pct}%`;
  $("#progressText").textContent = `${current + 1} / ${total}`;
}

// ---------------------------------------------------------------------------
// Next / Skip test
// ---------------------------------------------------------------------------

async function nextTest() {
  stopTimer();

  const btn = $("#nextTestBtn");
  const skipBtn = $("#skipTestBtn");
  btn.disabled = true;
  skipBtn.disabled = true;
  btn.textContent = "送信中...";

  try {
    const responseText = $("#responseText").value.trim();

    const result = await sendBg({
      type: "NEXT_TEST",
      observation: {
        responseText: responseText || null,
        responseTimeMs: elapsedMs,
      },
    });

    resetTimer();
    $("#responseText").value = "";
    updateCaptureCount(0);

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
    resetTimer();
    $("#responseText").value = "";
    updateCaptureCount(0);

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

// ---------------------------------------------------------------------------
// Session end / completion
// ---------------------------------------------------------------------------

async function endSession() {
  const confirmed = confirm("セッションを終了しますか？");
  if (!confirmed) return;

  // Stop auto-capture if running
  if (autoCaptureActive) {
    autoCaptureActive = false;
    $("#autoCaptureToggle").checked = false;
    $(".auto-capture-toggle").classList.remove("active");
    sendBg({ type: "STOP_AUTO_CAPTURE" });
  }

  try {
    setBadge("スコアリング中...", "badge-scoring");
    const result = await sendBg({ type: "COMPLETE_SESSION" });

    setBadge("完了", "badge-done");

    const state = await sendBg({ type: "GET_STATE" });
    $("#summaryTotal").textContent = state.observationCount || 0;
    $("#summaryStatus").textContent = result.status === "scoring" ? "採点中" : result.status;

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

// endSession without confirm (for internal calls when all tests done)
async function endSessionDirect() {
  if (autoCaptureActive) {
    autoCaptureActive = false;
    sendBg({ type: "STOP_AUTO_CAPTURE" });
  }

  try {
    setBadge("スコアリング中...", "badge-scoring");
    const result = await sendBg({ type: "COMPLETE_SESSION" });
    setBadge("完了", "badge-done");

    const state = await sendBg({ type: "GET_STATE" });
    $("#summaryTotal").textContent = state.observationCount || 0;
    $("#summaryStatus").textContent = result.status === "scoring" ? "採点中" : result.status;

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
  resetTimer();
  updateCaptureCount(0);
  autoCaptureActive = false;
  $("#autoCaptureToggle").checked = false;
  $(".auto-capture-toggle").classList.remove("active");
  setBadge("待機中", "badge-idle");
  await loadToolList();
  showSection("setup");
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
$("#newSessionBtn").addEventListener("click", newSession);
$("#fullScreenshotBtn").addEventListener("click", captureFullScreenshot);
$("#partialScreenshotBtn").addEventListener("click", capturePartialScreenshot);
$(".auto-capture-toggle").addEventListener("click", toggleAutoCapture);
$("#stopTimerBtn").addEventListener("click", stopTimer);

// ---------------------------------------------------------------------------
// Initialize
// ---------------------------------------------------------------------------

async function init() {
  await loadSettings();

  const state = await sendBg({ type: "GET_STATE" });

  if (state.currentSession) {
    if (state.currentSession.status === "completed" || state.currentSession.status === "scoring") {
      setBadge(state.currentSession.status === "scoring" ? "採点中" : "完了", "badge-done");
      $("#summaryTotal").textContent = state.observationCount || 0;
      $("#summaryStatus").textContent = state.currentSession.status === "scoring" ? "採点中" : "完了";
      const settings = await sendBg({ type: "GET_SETTINGS" });
      $("#dashboardLink").href = `${settings.platformUrl}/dashboard/audits/${state.currentSession.id}`;
      showSection("complete");
    } else {
      setBadge("記録中", "badge-recording");
      const testData = await sendBg({ type: "GET_CURRENT_TEST" });
      showProtocolTest(testData);
      updateCaptureCount(state.captureCount || 0);

      // Resume auto-capture state
      if (state.autoCaptureActive) {
        autoCaptureActive = true;
        $("#autoCaptureToggle").checked = true;
        $(".auto-capture-toggle").classList.add("active");
      }

      showSection("protocol");
      await resumeTimer();
    }
  } else {
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
