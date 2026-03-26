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
  // Per-test screenshot tracking: { [testIndex]: [{ url, timestamp }] }
  testScreenshots: {},
  // Timer state (manual start/stop)
  timerRunning: false,
  timerStartedAt: null, // Date.now() when started
  timerElapsedMs: 0, // accumulated elapsed time before latest start
};

// Restore state from storage on service worker wake
chrome.storage.local.get(["sessionState"], (data) => {
  if (data.sessionState) {
    Object.assign(state, data.sessionState);
  }
});

function persistState() {
  chrome.storage.local.set({ sessionState: { ...state } });
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
  switch (message.type) {
    // --- Session management ---
    case "CREATE_SESSION":
      return await createSession(message);

    case "COMPLETE_SESSION":
      return await completeSession();

    case "GET_STATE":
      return { ...state };

    case "GET_CURRENT_TEST":
      return getCurrentTest();

    case "NEXT_TEST":
      return await advanceTest(message);

    case "SKIP_TEST":
      return await skipTest(message);

    case "PREV_TEST":
      return goToPrevTest();

    case "GET_TEST_SCREENSHOTS":
      return getTestScreenshots(message.testIndex);

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
  state.timerRunning = false;
  state.timerStartedAt = null;
  state.timerElapsedMs = 0;

  persistState();
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

  state.isRecording = false;
  state.timerRunning = false;
  state.timerStartedAt = null;
  broadcastToContentScripts({ type: "RECORDING_STOPPED" });

  try {
    const result = await AixisAPI.completeSession(state.currentSession.id);
    state.currentSession.status = result.status;
    persistState();
    return result;
  } catch (err) {
    return { error: err.message };
  }
}

function resetSession() {
  state.currentSession = null;
  state.testCases = [];
  state.currentTestIndex = 0;
  state.isRecording = false;
  state.observationCount = 0;
  state.captureCount = 0;
  state.testScreenshots = {};
  state.timerRunning = false;
  state.timerStartedAt = null;
  state.timerElapsedMs = 0;
  persistState();
  broadcastToContentScripts({ type: "RECORDING_STOPPED" });
  return { ok: true };
}

// ---------------------------------------------------------------------------
// Test case navigation
// ---------------------------------------------------------------------------

function getCurrentTest() {
  if (!state.testCases.length) {
    return { test: null, index: 0, total: 0 };
  }
  const test = state.testCases[state.currentTestIndex] || null;
  return {
    test,
    index: state.currentTestIndex,
    total: state.testCases.length,
  };
}

function goToPrevTest() {
  if (!state.currentSession) {
    return { error: "アクティブなセッションがありません" };
  }
  if (state.currentTestIndex <= 0) {
    return { error: "最初のテストです" };
  }

  state.currentTestIndex--;
  // Restore capture count for this test
  const screenshots = state.testScreenshots[state.currentTestIndex] || [];
  state.captureCount = screenshots.length;
  // Reset timer for the test we're returning to
  state.timerRunning = false;
  state.timerStartedAt = null;
  state.timerElapsedMs = 0;
  persistState();

  return getCurrentTest();
}

function getTestScreenshots(testIndex) {
  const idx = testIndex ?? state.currentTestIndex;
  return { screenshots: state.testScreenshots[idx] || [] };
}

async function advanceTest({ observation }) {
  if (!state.currentSession) {
    return { error: "アクティブなセッションがありません" };
  }

  const currentTest = state.testCases[state.currentTestIndex];

  const obsData = {
    test_case_id: currentTest?.id || null,
    prompt_text: currentTest?.prompt || "",
    response_text: observation?.responseText || null,
    response_time_ms: observation?.responseTimeMs || 0,
    page_url: null,
    screenshot_base64: null,
    metadata: {
      category: currentTest?.category || "protocol",
      test_index: state.currentTestIndex,
    },
  };

  try {
    await AixisAPI.uploadObservation(state.currentSession.id, obsData);
    state.observationCount++;
  } catch (err) {
    console.error("Observation upload failed:", err);
    return { error: err.message };
  }

  state.currentTestIndex++;
  // Set captureCount for the new test (may have screenshots if navigated back before)
  const nextScreenshots = state.testScreenshots[state.currentTestIndex] || [];
  state.captureCount = nextScreenshots.length;
  state.timerRunning = false;
  state.timerStartedAt = null;
  state.timerElapsedMs = 0;
  persistState();

  if (state.currentTestIndex >= state.testCases.length) {
    return { done: true, index: state.currentTestIndex, total: state.testCases.length };
  }

  return getCurrentTest();
}

async function skipTest({ reason }) {
  if (!state.currentSession) {
    return { error: "アクティブなセッションがありません" };
  }

  const currentTest = state.testCases[state.currentTestIndex];

  const obsData = {
    test_case_id: currentTest?.id || null,
    prompt_text: currentTest?.prompt || "(スキップ)",
    response_text: null,
    response_time_ms: 0,
    metadata: { skipped: true, reason: reason || "" },
  };

  try {
    await AixisAPI.uploadObservation(state.currentSession.id, obsData);
    state.observationCount++;
  } catch (err) {
    console.error("Skip observation upload failed:", err);
  }

  state.currentTestIndex++;
  const nextScreenshots = state.testScreenshots[state.currentTestIndex] || [];
  state.captureCount = nextScreenshots.length;
  state.timerRunning = false;
  state.timerStartedAt = null;
  state.timerElapsedMs = 0;
  persistState();

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

  let screenshotBase64 = null;
  let pageUrl = null;
  let pageTitle = null;

  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (tab) {
      pageUrl = tab.url;
      pageTitle = tab.title;
      const dataUrl = await chrome.tabs.captureVisibleTab(null, { format: "png" });
      screenshotBase64 = dataUrl.replace(/^data:image\/png;base64,/, "");
    }
  } catch (err) {
    console.warn("Screenshot capture failed:", err);
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

    // Track screenshot per test for thumbnail display
    const idx = state.currentTestIndex;
    if (!state.testScreenshots[idx]) state.testScreenshots[idx] = [];
    state.testScreenshots[idx].push({
      dataUrl: "data:image/png;base64," + screenshotBase64.substring(0, 200) + "...",  // Don't store full image in state
      timestamp: new Date().toISOString(),
      type: "full",
      pageUrl: pageUrl,
      pageTitle: pageTitle,
    });

    persistState();
    broadcastToContentScripts({
      type: "CAPTURE_COUNT_UPDATE",
      count: state.captureCount,
      screenshots: state.testScreenshots[idx],
    });
    return { ok: true, captureCount: state.captureCount, screenshots: state.testScreenshots[idx] };
  } catch (err) {
    console.error("Screenshot upload failed:", err);
    return { error: err.message };
  }
}

// ---------------------------------------------------------------------------
// Partial screenshot capture
// ---------------------------------------------------------------------------

async function startPartialCapture() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab) {
    return { error: "アクティブなタブが見つかりません" };
  }

  try {
    await chrome.tabs.sendMessage(tab.id, { type: "INJECT_SELECTION_OVERLAY" });
  } catch {
    await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      files: ["content/content.js"],
    });
    await chrome.tabs.sendMessage(tab.id, { type: "INJECT_SELECTION_OVERLAY" });
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

    // Track per-test
    const idx = state.currentTestIndex;
    if (!state.testScreenshots[idx]) state.testScreenshots[idx] = [];
    state.testScreenshots[idx].push({
      timestamp: new Date().toISOString(),
      type: "partial",
      pageUrl: pageUrl,
      pageTitle: pageTitle,
    });

    persistState();
    broadcastToContentScripts({
      type: "PARTIAL_CAPTURE_DONE",
      captureCount: state.captureCount,
      screenshots: state.testScreenshots[idx],
    });
    return { ok: true, captureCount: state.captureCount, screenshots: state.testScreenshots[idx] };
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
  const existingContexts = await chrome.runtime.getContexts({
    contextTypes: ["OFFSCREEN_DOCUMENT"],
  });

  if (existingContexts.length > 0) return;

  if (offscreenCreating) {
    await offscreenCreating;
    return;
  }

  offscreenCreating = chrome.offscreen.createDocument({
    url: "offscreen/offscreen.html",
    reasons: ["CANVAS"],
    justification: "Crop partial screenshots using canvas",
  });

  await offscreenCreating;
  offscreenCreating = null;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function broadcastToContentScripts(message) {
  chrome.tabs.query({}, (tabs) => {
    for (const tab of tabs) {
      try {
        chrome.tabs.sendMessage(tab.id, message);
      } catch {}
    }
  });
}
