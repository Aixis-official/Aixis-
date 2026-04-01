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
  // Saved total test case count (survives restart when testCases array is not persisted)
  totalTestCases: 0,
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
          // Ensure testCases is always an array
          if (!Array.isArray(state.testCases)) {
            state.testCases = [];
          }
          // Restore screenshot metadata from persisted stripped data
          if (!state.testScreenshots || !Object.keys(state.testScreenshots).length) {
            if (state.testScreenshotsMeta && Object.keys(state.testScreenshotsMeta).length > 0) {
              state.testScreenshots = state.testScreenshotsMeta;
            } else {
              state.testScreenshots = {};
            }
          }
          // Clean up transient field
          delete state.testScreenshotsMeta;
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

// Re-fetch test cases from API ONLY if session exists AND testCases is empty
// This is a fallback — testCases are now persisted in storage
async function ensureTestCases() {
  if (!state.currentSession || state.testCases.length > 0) return;

  console.log("testCases empty, attempting re-fetch from API...");
  // Retry up to 2 times with 500ms delay
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      const cases = await AixisAPI.getTestCases(state.currentSession.id);
      if (cases && cases.length > 0) {
        state.testCases = cases;
        state.totalTestCases = cases.length;
        console.log("Re-fetched test cases:", cases.length);
        return;
      }
    } catch (err) {
      console.warn(`Failed to fetch test cases (attempt ${attempt + 1}/2):`, err);
    }
    if (attempt < 1) await new Promise(r => setTimeout(r, 500));
  }
  // Non-fatal: testCases stays empty, getCurrentTest will return {test: null}
  // but advanceTest will NOT auto-complete (has explicit length check)
  console.warn("Could not load test cases from API — using persisted state");
}

function persistState() {
  // Save all essential state including testCases (text only, ~50KB for 17 tests)
  // Strip ONLY the large binary data (screenshot thumbnails)
  const toSave = {
    currentSession: state.currentSession,
    testCases: state.testCases, // ~50KB text, safe to persist
    currentTestIndex: state.currentTestIndex,
    totalTestCases: state.testCases.length || state.totalTestCases || 0,
    isRecording: state.isRecording,
    observationCount: state.observationCount,
    captureCount: state.captureCount,
    testTimers: state.testTimers,
    submittedTests: state.submittedTests,
    timerRunning: state.timerRunning,
    timerStartedAt: state.timerStartedAt,
    timerElapsedMs: state.timerElapsedMs,
    // Screenshots: save metadata without thumbDataUrl (the large base64 data)
    testScreenshotsMeta: _stripScreenshotThumbs(state.testScreenshots),
  };

  try {
    return chrome.storage.local.set({ sessionState: toSave }).catch(err => {
      console.error("persistState failed:", err);
      // If still too large, try without testCases as last resort
      if (err.message && err.message.includes("QUOTA")) {
        const fallback = { ...toSave, testCases: [], testScreenshotsMeta: {} };
        return chrome.storage.local.set({ sessionState: fallback }).catch(() => {});
      }
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
  state.totalTestCases = state.testCases.length;
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

  // Upload the current test's observation before completing (same logic as advanceTest)
  if (state.testCases.length > 0 && state.currentTestIndex < state.testCases.length) {
    if (!state.submittedTests) state.submittedTests = {};
    const testKey = String(state.currentTestIndex);

    if (!state.submittedTests[testKey]) {
      let elapsed = state.timerElapsedMs;
      if (state.timerRunning && state.timerStartedAt) {
        elapsed += Date.now() - state.timerStartedAt;
      }

      const currentTest = state.testCases[state.currentTestIndex];
      const obsData = {
        test_case_id: currentTest?.id || null,
        prompt_text: currentTest?.prompt || "",
        response_text: null,
        response_time_ms: elapsed,
        page_url: null,
        screenshot_base64: null,
        metadata: {
          type: "test_completion",
          category: currentTest?.category || "protocol",
          test_index: state.currentTestIndex,
          expected_behaviors: currentTest?.expected_behaviors || [],
        },
      };

      try {
        await AixisAPI.uploadObservation(state.currentSession.id, obsData);
        state.submittedTests[testKey] = true;
        state.observationCount++;
      } catch (err) {
        console.error("Final test completion upload failed:", err);
      }
    }
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
  state.totalTestCases = 0;
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

// Strip large thumbDataUrl from screenshots for persistence (keep metadata only)
function _stripScreenshotThumbs(testScreenshots) {
  const stripped = {};
  for (const key in testScreenshots) {
    stripped[key] = (testScreenshots[key] || []).map(s => ({
      timestamp: s.timestamp,
      type: s.type,
      pageUrl: s.pageUrl,
      pageTitle: s.pageTitle,
      // thumbDataUrl deliberately omitted to save storage space
    }));
  }
  return stripped;
}

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

  // Upload a test completion record (primary entry for this test)
  if (!state.submittedTests) state.submittedTests = {};
  const testKey = String(state.currentTestIndex);

  if (!state.submittedTests[testKey]) {
    const obsData = {
      test_case_id: currentTest?.id || null,
      prompt_text: currentTest?.prompt || "",
      response_text: null,
      response_time_ms: elapsed,
      page_url: null,
      screenshot_base64: null,
      metadata: {
        type: "test_completion",
        category: currentTest?.category || "protocol",
        test_index: state.currentTestIndex,
        expected_behaviors: currentTest?.expected_behaviors || [],
      },
    };

    try {
      await AixisAPI.uploadObservation(state.currentSession.id, obsData);
      state.submittedTests[testKey] = true;
      state.observationCount++;
      persistState();
    } catch (err) {
      console.error("Test completion upload failed:", err);
      return { error: "テスト記録のアップロードに失敗しました: " + err.message };
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
    metadata: {
      type: "test_completion",
      skipped: true,
      reason: reason || "",
      category: currentTest?.category || "protocol",
      test_index: state.currentTestIndex,
    },
  };

  if (!state.submittedTests) state.submittedTests = {};
  const testKey = String(state.currentTestIndex);

  try {
    await AixisAPI.uploadObservation(state.currentSession.id, obsData);
    state.observationCount++;
    state.submittedTests[testKey] = true;
    // Persist immediately to prevent double-submit on crash
    persistState();
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
      // Strip data URL prefix — use robust regex with case-insensitive flag
      screenshotBase64 = dataUrl.replace(/^data:image\/[a-zA-Z]+;base64,/, "");
      // Validate: base64 data must be non-empty
      if (!screenshotBase64 || screenshotBase64.length < 100) {
        console.error("Screenshot base64 is too short after prefix strip:", screenshotBase64?.length, "dataUrl length:", dataUrl.length);
        throw new Error("スクリーンショットデータが空です");
      }
      // Use same capture for thumbnail (no second call)
      thumbDataUrl = dataUrl;

      // Show panel again
      try {
        await chrome.tabs.sendMessage(tab.id, { type: "SHOW_PANEL" });
      } catch {}
    } else {
      return { error: "アクティブなタブが見つかりません" };
    }
  } catch (err) {
    console.warn("Screenshot capture failed:", err);
    // Ensure panel is shown even on error
    try {
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
      if (tab) await chrome.tabs.sendMessage(tab.id, { type: "SHOW_PANEL" });
    } catch {}
    return { error: "スクリーンショットの取得に失敗しました: " + (err.message || "") };
  }

  // Safety check: never upload without screenshot data
  if (!screenshotBase64) {
    console.error("screenshotBase64 is null/empty after capture — aborting upload");
    return { error: "スクリーンショットデータが取得できませんでした" };
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
      screenshot_base64_length: screenshotBase64.length,
    },
  };

  // Upload with retry (once) for transient network issues
  let result = null;
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      result = await AixisAPI.uploadObservation(state.currentSession.id, obsData);
      // Verify server actually saved the screenshot
      if (result && result.screenshot_saved === false) {
        console.warn(`Server did NOT save screenshot (attempt ${attempt + 1}). Retrying...`);
        if (attempt < 1) {
          await new Promise(r => setTimeout(r, 500));
          continue;
        }
      }
      break;
    } catch (err) {
      console.error(`Screenshot upload failed (attempt ${attempt + 1}):`, err);
      if (attempt < 1) {
        await new Promise(r => setTimeout(r, 1000));
        continue;
      }
      return { error: err.message };
    }
  }

  if (!result) {
    return { error: "スクリーンショットのアップロードに失敗しました（2回リトライ済み）" };
  }

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
    savedOnServer: result.screenshot_saved !== false,
  });

  await persistState();
  broadcastToContentScripts({
    type: "CAPTURE_COUNT_UPDATE",
    count: state.captureCount,
    screenshots: state.testScreenshots[key],
  });

  // Warn user if screenshot wasn't saved despite successful upload
  if (result.screenshot_saved === false) {
    return {
      ok: true,
      warning: "スクリーンショットがサーバーに保存されませんでした。再度お試しください。",
      captureCount: state.captureCount,
      screenshots: state.testScreenshots[key],
    };
  }

  return { ok: true, captureCount: state.captureCount, screenshots: state.testScreenshots[key] };
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
