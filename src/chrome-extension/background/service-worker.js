/**
 * Aixis Chrome Extension v3 — Background Service Worker
 *
 * Manages session state, screenshot capture (full/partial),
 * timer persistence, and API communication.
 *
 * Timer is manual (start/stop buttons in the floating panel).
 * No auto-start on copy. No auto-capture.
 * No response text input — evaluation is screenshot-based only.
 */

importScripts("../lib/api-client.js");

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

let state = {
  currentSession: null, // { id, code, toolId, status, recordingMode }
  testCases: [],
  currentTestIndex: 0,
  isRecording: false,
  observationCount: 0,
  captureCount: 0,
  // Per-test screenshot tracking: { [testIndex]: [{ timestamp, type, thumbDataUrl }] }
  testScreenshots: {},
  // Per-test timer values: { [testIndex]: elapsedMs }
  testTimers: {},
  // Per-test submitted tracking: { [testIndex]: true }
  submittedTests: {},
  // Timer state (manual start/stop)
  timerRunning: false,
  timerStartedAt: null, // Date.now() when started
  timerElapsedMs: 0, // accumulated elapsed time before latest start
};

// Clear any oversized old data on first load
chrome.storage.local.getBytesInUse(null, (bytes) => {
  if (bytes > 5 * 1024 * 1024) { // Over 5MB — something is wrong
    console.warn("Storage is " + (bytes / 1024 / 1024).toFixed(1) + "MB, clearing old data");
    chrome.storage.local.remove(["sessionState"]); // Clear and start fresh
  }
});

// Restore state from storage on service worker wake
let _stateReady = new Promise((resolve) => {
  try {
    chrome.storage.local.get(["sessionState"], (data) => {
      if (chrome.runtime.lastError) {
        console.warn("Failed to restore state:", chrome.runtime.lastError);
        resolve();
        return;
      }
      if (data && data.sessionState) {
        try {
          Object.assign(state, data.sessionState);
          // testCases and testScreenshots are NOT persisted — re-fetch if needed
          if (!state.testCases || !state.testCases.length) {
            state.testCases = [];
          }
          if (!state.testScreenshots) {
            state.testScreenshots = {};
          }
        } catch (e) {
          console.warn("Failed to merge restored state:", e);
        }
      }
      resolve();
    });
  } catch (err) {
    console.warn("Storage access failed during init:", err);
    resolve();
  }
});

// Re-fetch test cases from API if session exists but testCases is empty
async function ensureTestCases() {
  if (!state.currentSession || state.testCases.length > 0) return;

  // Retry up to 3 times with 1s delay
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const cases = await AixisAPI.getTestCases(state.currentSession.id);
      if (cases && cases.length > 0) {
        state.testCases = cases;
        console.log("Re-fetched test cases:", cases.length);
        return;
      }
    } catch (err) {
      console.warn(`Failed to fetch test cases (attempt ${attempt + 1}/3):`, err);
    }
    if (attempt < 2) await new Promise(r => setTimeout(r, 1000));
  }
  console.error("Could not load test cases after 3 attempts");
}

function persistState() {
  // ONLY save small essential data — never save testCases or testScreenshots
  const minimal = {
    currentSession: state.currentSession,
    currentTestIndex: state.currentTestIndex,
    isRecording: state.isRecording,
    observationCount: state.observationCount,
    captureCount: state.captureCount,
    testTimers: state.testTimers,
    submittedTests: state.submittedTests,
    timerRunning: state.timerRunning,
    timerStartedAt: state.timerStartedAt,
    timerElapsedMs: state.timerElapsedMs,
    totalTestCases: state.testCases.length, // Save count only (not full data)
    // testCases: NOT saved (re-fetched from API on wake)
    // testScreenshots: NOT saved (kept in memory only)
  };

  try {
    return chrome.storage.local.set({ sessionState: minimal }).catch(err => {
      console.error("persistState failed:", err);
    });
  } catch (err) {
    console.error("persistState sync error:", err);
    return Promise.resolve();
  }
}

// ---------------------------------------------------------------------------
// Message handling
// ---------------------------------------------------------------------------

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  // Ignore messages targeted at the offscreen document — don't intercept
  if (message.target === "offscreen") {
    return false; // Let offscreen document handle this
  }
  // Ignore messages from offscreen document (responses)
  if (sender.url && sender.url.includes("offscreen")) {
    return false;
  }

  handleMessage(message, sender)
    .then(sendResponse)
    .catch((err) => sendResponse({ error: err.message }));
  return true; // async response
});

async function handleMessage(message, sender) {
  // Ensure state is fully restored from storage before handling any message
  await _stateReady;

  switch (message.type) {
    // --- Session management ---
    case "CREATE_SESSION":
      return await createSession(message);

    case "COMPLETE_SESSION":
      return await completeSession();

    case "GET_STATE":
      return { ...state };

    case "GET_CURRENT_TEST":
      await ensureTestCases();
      return getCurrentTest();

    case "NEXT_TEST":
      await ensureTestCases();
      return await advanceTest(message);

    case "SKIP_TEST":
      await ensureTestCases();
      return await skipTest(message);

    case "PREV_TEST":
      await ensureTestCases();
      return goToPrevTest();

    case "GET_TEST_SCREENSHOTS":
      return getTestScreenshots(message.testIndex);

    case "DELETE_SCREENSHOT":
      return deleteScreenshot(message.index);

    case "RESET_SESSION":
      return resetSession();

    // --- Timer (manual) ---
    case "TIMER_START":
      state.timerRunning = true;
      state.timerStartedAt = Date.now();
      persistState();
      return { ok: true };

    case "TIMER_STOP":
      if (state.timerRunning && state.timerStartedAt) {
        state.timerElapsedMs += Date.now() - state.timerStartedAt;
      }
      state.timerRunning = false;
      state.timerStartedAt = null;
      persistState();
      return { ok: true, elapsedMs: state.timerElapsedMs };

    case "TIMER_RESET":
      state.timerRunning = false;
      state.timerStartedAt = null;
      state.timerElapsedMs = 0;
      persistState();
      return { ok: true };

    // --- Screenshots ---
    case "FULL_SCREENSHOT":
      return await captureFullScreenshot();

    case "START_PARTIAL_CAPTURE":
      return await startPartialCapture();

    case "PARTIAL_CAPTURE_COORDS":
      return await handlePartialCaptureCoords(message);

    // --- Settings ---
    case "SAVE_SETTINGS":
      await chrome.storage.local.set({
        apiKey: message.apiKey,
        platformUrl: message.platformUrl,
      });
      return { ok: true };

    case "GET_SETTINGS":
      return await AixisAPI.getSettings();

    case "FETCH_TOOLS":
      return await AixisAPI.listTools();

    default:
      return { error: `Unknown message type: ${message.type}` };
  }
}

// ---------------------------------------------------------------------------
// Session management
// ---------------------------------------------------------------------------

async function createSession({ toolId, profileId, recordingMode }) {
  const result = await AixisAPI.createSession(
    toolId,
    profileId || "",
    recordingMode || "protocol"
  );

  state.currentSession = {
    id: result.session_id,
    code: result.session_code,
    toolId: result.tool_id,
    status: result.status,
    recordingMode: result.recording_mode,
  };
  state.testCases = result.test_cases || [];
  state.currentTestIndex = 0;
  state.isRecording = true;
  state.observationCount = 0;
  state.captureCount = 0;
  state.testScreenshots = {};
  state.testTimers = {};
  state.submittedTests = {};
  state.timerRunning = false;
  state.timerStartedAt = null;
  state.timerElapsedMs = 0;

  await persistState();
  broadcastToContentScripts({ type: "RECORDING_STARTED" });

  // Pre-create offscreen document for partial screenshot cropping
  ensureOffscreenDocument().catch(() => {});

  return {
    session: state.currentSession,
    testCases: state.testCases,
    totalCases: state.testCases.length,
  };
}

async function completeSession() {
  if (!state.currentSession) {
    return { error: "アクティブなセッションがありません" };
  }

  // Save the current test's timer value before stopping
  if (state.timerRunning && state.timerStartedAt) {
    const key = _ssKey();
    state.testTimers[key] = state.timerElapsedMs + (Date.now() - state.timerStartedAt);
  } else if (state.timerElapsedMs > 0) {
    const key = _ssKey();
    state.testTimers[key] = state.timerElapsedMs;
  }

  state.isRecording = false;
  state.timerRunning = false;
  state.timerStartedAt = null;
  broadcastToContentScripts({ type: "RECORDING_STOPPED" });

  try {
    const result = await AixisAPI.completeSession(state.currentSession.id);
    state.currentSession.status = result.status;
    await persistState();
    return result;
  } catch (err) {
    return { error: err.message };
  }
}

async function resetSession() {
  state.currentSession = null;
  state.testCases = [];
  state.currentTestIndex = 0;
  state.isRecording = false;
  state.observationCount = 0;
  state.captureCount = 0;
  state.testScreenshots = {};
  state.testTimers = {};
  state.submittedTests = {};
  state.timerRunning = false;
  state.timerStartedAt = null;
  state.timerElapsedMs = 0;
  await persistState();
  broadcastToContentScripts({ type: "RECORDING_STOPPED" });
  return { ok: true };
}

// ---------------------------------------------------------------------------
// Test case navigation
// ---------------------------------------------------------------------------

// Helper: get per-test data using string key (chrome.storage serializes keys as strings)
function _ssKey(idx) { return String(idx ?? state.currentTestIndex); }
function _getScreenshots(idx) { return state.testScreenshots[_ssKey(idx)] || []; }
function _getTimer(idx) { return state.testTimers[_ssKey(idx)] || 0; }

function getCurrentTest() {
  const total = state.testCases.length || state.totalTestCases || 0;
  if (!state.testCases.length) {
    return { test: null, index: state.currentTestIndex, total: total };
  }
  const test = state.testCases[state.currentTestIndex] || null;
  return {
    test,
    index: state.currentTestIndex,
    total: total,
  };
}

async function goToPrevTest() {
  if (!state.currentSession) {
    return { error: "アクティブなセッションがありません" };
  }
  if (state.currentTestIndex <= 0) {
    return { error: "最初のテストです" };
  }

  // Save current test's timer value
  const key = _ssKey();
  if (state.timerRunning && state.timerStartedAt) {
    state.testTimers[key] = state.timerElapsedMs + (Date.now() - state.timerStartedAt);
  } else {
    state.testTimers[key] = state.timerElapsedMs;
  }

  state.currentTestIndex--;
  // Restore capture count and timer for previous test
  state.captureCount = _getScreenshots().length;
  state.timerRunning = false;
  state.timerStartedAt = null;
  state.timerElapsedMs = _getTimer();
  await persistState();

  return getCurrentTest();
}

function getTestScreenshots(testIndex) {
  // Default to current test index if not specified
  const idx = testIndex ?? state.currentTestIndex;
  return { screenshots: _getScreenshots(idx) };
}

async function deleteScreenshot(index) {
  const key = _ssKey();
  const screenshots = _getScreenshots();

  if (index < 0 || index >= screenshots.length) {
    return { error: "無効なインデックスです" };
  }

  screenshots.splice(index, 1);
  state.testScreenshots[key] = screenshots;
  state.captureCount = screenshots.length;
  await persistState();

  return {
    ok: true,
    captureCount: state.captureCount,
    screenshots: screenshots,
  };
}

async function advanceTest({ observation }) {
  if (!state.currentSession) {
    return { error: "アクティブなセッションがありません" };
  }

  // Ensure test cases are loaded (may be empty after service worker restart)
  if (state.testCases.length === 0) {
    await ensureTestCases();
    if (state.testCases.length === 0) {
      return { error: "テストケースの読み込みに失敗しました。ページを再読み込みしてください。" };
    }
  }

  if (state.currentTestIndex >= state.testCases.length) {
    return { done: true, index: state.currentTestIndex, total: state.testCases.length };
  }

  // Save the actual timer elapsed time for this test
  let elapsed = state.timerElapsedMs;
  if (state.timerRunning && state.timerStartedAt) {
    elapsed += Date.now() - state.timerStartedAt;
  }

  const currentTest = state.testCases[state.currentTestIndex];

  // Don't upload empty observations — screenshots are the evidence.
  // Just update server-side progress counter
  if (!state.submittedTests) state.submittedTests = {};
  const testKey = String(state.currentTestIndex);

  if (!state.submittedTests[testKey]) {
    try {
      await AixisAPI.advanceProgress(state.currentSession.id, {
        test_index: state.currentTestIndex,
        test_case_id: currentTest?.id || null,
        response_time_ms: elapsed,
      });
      state.submittedTests[testKey] = true;
    } catch (err) {
      // Non-fatal — screenshots are already uploaded
      console.warn("Progress update failed:", err);
    }
  }

  // Save current test's timer
  state.testTimers[_ssKey()] = elapsed;

  state.currentTestIndex++;
  // Restore captureCount and timer for next test
  state.captureCount = _getScreenshots().length;
  state.timerRunning = false;
  state.timerStartedAt = null;
  state.timerElapsedMs = _getTimer();
  await persistState();

  if (state.currentTestIndex >= state.testCases.length) {
    return { done: true, index: state.currentTestIndex, total: state.testCases.length };
  }

  return getCurrentTest();
}

async function skipTest({ reason }) {
  if (!state.currentSession) {
    return { error: "アクティブなセッションがありません" };
  }

  if (state.testCases.length === 0) {
    await ensureTestCases();
    if (state.testCases.length === 0) {
      return { error: "テストケースの読み込みに失敗しました。ページを再読み込みしてください。" };
    }
  }

  if (state.currentTestIndex >= state.testCases.length) {
    return { done: true, index: state.currentTestIndex, total: state.testCases.length };
  }

  const currentTest = state.testCases[state.currentTestIndex];

  const obsData = {
    test_case_id: currentTest?.id || null,
    prompt_text: currentTest?.prompt || "(スキップ)",
    response_text: null,
    response_time_ms: 0,
    metadata: { skipped: true, reason: reason || "" },
  };

  if (!state.submittedTests) state.submittedTests = {};
  const testKey = String(state.currentTestIndex);

  try {
    await AixisAPI.uploadObservation(state.currentSession.id, obsData);
    state.observationCount++;
    state.submittedTests[testKey] = true;
  } catch (err) {
    console.error("Skip observation upload failed:", err);
  }

  // Save current test timer as 0 (skipped)
  state.testTimers[_ssKey()] = 0;

  state.currentTestIndex++;
  state.captureCount = _getScreenshots().length;
  state.timerRunning = false;
  state.timerStartedAt = null;
  state.timerElapsedMs = _getTimer();
  await persistState();

  if (state.currentTestIndex >= state.testCases.length) {
    return { done: true, index: state.currentTestIndex, total: state.testCases.length };
  }

  return getCurrentTest();
}

// ---------------------------------------------------------------------------
// Full screenshot capture
// ---------------------------------------------------------------------------

async function captureFullScreenshot() {
  if (!state.currentSession) {
    return { error: "アクティブなセッションがありません" };
  }

  // Ensure test cases are available for test_case_id linkage
  if (state.testCases.length === 0) {
    await ensureTestCases();
  }

  let screenshotBase64 = null;
  let thumbDataUrl = null;
  let pageUrl = null;
  let pageTitle = null;

  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (tab) {
      pageUrl = tab.url;
      pageTitle = tab.title;

      // Hide the floating panel before capture
      try {
        await chrome.tabs.sendMessage(tab.id, { type: "HIDE_PANEL" });
        await new Promise(r => setTimeout(r, 50)); // Brief wait for DOM update
      } catch {}

      // Single capture as JPEG (faster than PNG, good enough for evaluation)
      const dataUrl = await chrome.tabs.captureVisibleTab(null, { format: "jpeg", quality: 85 });
      if (!dataUrl || typeof dataUrl !== "string") {
        throw new Error("captureVisibleTab returned empty result");
      }
      // Strip any data URL prefix (handle both jpeg and png formats)
      screenshotBase64 = dataUrl.replace(/^data:image\/[a-z]+;base64,/, "");
      // Use same capture for thumbnail (no second call)
      thumbDataUrl = dataUrl;

      // Show panel again
      try {
        await chrome.tabs.sendMessage(tab.id, { type: "SHOW_PANEL" });
      } catch {}
    }
  } catch (err) {
    console.warn("Screenshot capture failed:", err);
    // Ensure panel is shown even on error
    try {
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
      if (tab) await chrome.tabs.sendMessage(tab.id, { type: "SHOW_PANEL" });
    } catch {}
    return { error: "スクリーンショットの取得に失敗しました" };
  }

  const currentTest = state.testCases[state.currentTestIndex];

  const obsData = {
    test_case_id: currentTest?.id || null,
    prompt_text: currentTest?.prompt || "スクリーンショット",
    response_text: null,
    response_time_ms: 0,
    page_url: pageUrl,
    screenshot_base64: screenshotBase64,
    metadata: {
      capture_type: "full_screenshot",
      page_title: pageTitle || "",
      test_index: state.currentTestIndex,
      timestamp: new Date().toISOString(),
    },
  };

  try {
    const result = await AixisAPI.uploadObservation(state.currentSession.id, obsData);
    state.captureCount++;

    // Track screenshot per test with thumbnail for preview (string key for storage compat)
    const key = _ssKey();
    if (!state.testScreenshots[key]) state.testScreenshots[key] = [];
    state.testScreenshots[key].push({
      thumbDataUrl: thumbDataUrl || null,
      timestamp: new Date().toISOString(),
      type: "full",
      pageUrl: pageUrl,
      pageTitle: pageTitle,
    });

    await persistState();
    broadcastToContentScripts({
      type: "CAPTURE_COUNT_UPDATE",
      count: state.captureCount,
      screenshots: state.testScreenshots[key],
    });
    return { ok: true, captureCount: state.captureCount, screenshots: state.testScreenshots[key] };
  } catch (err) {
    console.error("Screenshot upload failed:", err);
    return { error: err.message };
  }
}

// ---------------------------------------------------------------------------
// Partial screenshot capture
// ---------------------------------------------------------------------------

async function startPartialCapture() {
  let tab;
  try {
    const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
    tab = tabs[0];
  } catch (err) {
    return { error: "タブの取得に失敗しました: " + err.message };
  }

  if (!tab) {
    return { error: "アクティブなタブが見つかりません" };
  }

  try {
    await chrome.tabs.sendMessage(tab.id, { type: "INJECT_SELECTION_OVERLAY" });
  } catch {
    try {
      await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        files: ["content/content.js"],
      });
      await chrome.tabs.sendMessage(tab.id, { type: "INJECT_SELECTION_OVERLAY" });
    } catch (err) {
      return { error: "コンテンツスクリプトの注入に失敗しました: " + err.message };
    }
  }

  return { ok: true };
}

async function handlePartialCaptureCoords({ rect, devicePixelRatio }) {
  if (!state.currentSession) {
    return { error: "アクティブなセッションがありません" };
  }

  let fullImageBase64 = null;
  let pageUrl = null;
  let pageTitle = null;

  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (tab) {
      pageUrl = tab.url;
      pageTitle = tab.title;
      const dataUrl = await chrome.tabs.captureVisibleTab(null, { format: "png" });
      fullImageBase64 = dataUrl.replace(/^data:image\/png;base64,/, "");
    }
  } catch (err) {
    console.warn("Partial screenshot capture failed:", err);
    return { error: "スクリーンショットの取得に失敗しました" };
  }

  if (!fullImageBase64) {
    return { error: "スクリーンショットの取得に失敗しました（タブなし）" };
  }

  // Crop via offscreen document (with retry for initialization delay)
  let croppedBase64 = fullImageBase64;
  try {
    await ensureOffscreenDocument();
    // Small delay to ensure offscreen document listener is ready
    await new Promise(r => setTimeout(r, 200));

    let cropResult = null;
    for (let attempt = 0; attempt < 3; attempt++) {
      try {
        cropResult = await chrome.runtime.sendMessage({
          type: "CROP_IMAGE",
          target: "offscreen",
          imageBase64: fullImageBase64,
          rect: rect,
          devicePixelRatio: devicePixelRatio || 1,
        });
        if (cropResult?.croppedBase64) break;
      } catch (retryErr) {
        console.warn(`Crop attempt ${attempt + 1} failed:`, retryErr);
        if (attempt < 2) await new Promise(r => setTimeout(r, 300));
      }
    }
    if (cropResult?.croppedBase64) {
      croppedBase64 = cropResult.croppedBase64;
    }
  } catch (err) {
    console.warn("Crop failed, using full image:", err);
  }

  const currentTest = state.testCases[state.currentTestIndex];

  const obsData = {
    test_case_id: currentTest?.id || null,
    prompt_text: currentTest?.prompt || "部分スクリーンショット",
    response_text: null,
    response_time_ms: 0,
    page_url: pageUrl,
    screenshot_base64: croppedBase64,
    metadata: {
      capture_type: "partial_screenshot",
      page_title: pageTitle || "",
      test_index: state.currentTestIndex,
      crop_rect: rect,
      timestamp: new Date().toISOString(),
    },
  };

  try {
    await AixisAPI.uploadObservation(state.currentSession.id, obsData);
    state.captureCount++;

    // Track per-test (string key for chrome.storage compatibility)
    const key = _ssKey();
    if (!state.testScreenshots[key]) state.testScreenshots[key] = [];
    let partialThumb = null;
    if (croppedBase64 && croppedBase64.length < 50000) {
      partialThumb = "data:image/png;base64," + croppedBase64;
    }
    state.testScreenshots[key].push({
      thumbDataUrl: partialThumb,
      timestamp: new Date().toISOString(),
      type: "partial",
      pageUrl: pageUrl,
      pageTitle: pageTitle,
    });

    await persistState();
    broadcastToContentScripts({
      type: "PARTIAL_CAPTURE_DONE",
      captureCount: state.captureCount,
      screenshots: state.testScreenshots[key],
    });
    return { ok: true, captureCount: state.captureCount, screenshots: state.testScreenshots[key] };
  } catch (err) {
    console.error("Partial screenshot upload failed:", err);
    return { error: err.message };
  }
}

// ---------------------------------------------------------------------------
// Offscreen document management
// ---------------------------------------------------------------------------

let offscreenCreating = null;

async function ensureOffscreenDocument() {
  try {
    const existingContexts = await chrome.runtime.getContexts({
      contextTypes: ["OFFSCREEN_DOCUMENT"],
    });
    if (existingContexts.length > 0) return;
  } catch (err) {
    console.warn("getContexts failed:", err);
    // Proceed to try creating anyway
  }

  if (offscreenCreating) {
    try {
      await offscreenCreating;
    } catch {
      // Previous creation failed, reset and retry below
      offscreenCreating = null;
    }
    // Re-check if it exists now
    try {
      const existingContexts = await chrome.runtime.getContexts({
        contextTypes: ["OFFSCREEN_DOCUMENT"],
      });
      if (existingContexts.length > 0) return;
    } catch {}
  }

  offscreenCreating = chrome.offscreen.createDocument({
    url: "offscreen/offscreen.html",
    reasons: ["CANVAS"],
    justification: "Crop partial screenshots using canvas",
  });

  try {
    await offscreenCreating;
  } catch (err) {
    console.warn("Offscreen document creation failed:", err);
    // May already exist (race condition), which is fine
  } finally {
    offscreenCreating = null;
  }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

async function broadcastToContentScripts(message) {
  try {
    const tabs = await chrome.tabs.query({});
    for (const tab of tabs) {
      if (!tab.id || tab.id < 0) continue; // Skip special tabs (devtools, etc.)
      try { await chrome.tabs.sendMessage(tab.id, message); } catch {}
    }
  } catch {}
}
