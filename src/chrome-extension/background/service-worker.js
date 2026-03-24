/**
 * Aixis Chrome Extension v2 — Background Service Worker
 *
 * Manages session state, screenshot capture (full/partial/auto),
 * timer persistence, and API communication.
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
  timerStart: null,
  captureCount: 0,
  autoCaptureActive: false,
  autoCaptureIntervalId: null,
};

// Restore state from storage on service worker wake
chrome.storage.local.get(["sessionState"], (data) => {
  if (data.sessionState) {
    Object.assign(state, data.sessionState);
    // Don't persist interval IDs — they're invalid after restart
    state.autoCaptureIntervalId = null;
    // Resume auto-capture if it was active
    if (state.autoCaptureActive && state.currentSession) {
      startAutoCaptureInterval(5000);
    }
  }
});

function persistState() {
  // Don't persist interval ID
  const toSave = { ...state };
  delete toSave.autoCaptureIntervalId;
  chrome.storage.local.set({ sessionState: toSave });
}

// ---------------------------------------------------------------------------
// Message handling
// ---------------------------------------------------------------------------

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
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
      return { ...state, autoCaptureIntervalId: undefined };

    case "GET_CURRENT_TEST":
      return getCurrentTest();

    case "NEXT_TEST":
      return await advanceTest(message);

    case "SKIP_TEST":
      return await skipTest(message);

    case "RESET_SESSION":
      return resetSession();

    // --- Timer ---
    case "SET_TIMER_START":
      state.timerStart = message.timestamp;
      persistState();
      return { ok: true };

    // --- Screenshots ---
    case "FULL_SCREENSHOT":
      return await captureFullScreenshot();

    case "START_PARTIAL_CAPTURE":
      return await startPartialCapture();

    case "PARTIAL_CAPTURE_COORDS":
      return await handlePartialCaptureCoords(message);

    case "START_AUTO_CAPTURE":
      return startAutoCapture(message.intervalMs || 5000);

    case "STOP_AUTO_CAPTURE":
      return stopAutoCapture();

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
  state.timerStart = null;
  state.captureCount = 0;
  state.autoCaptureActive = false;

  persistState();
  broadcastToContentScripts({ type: "RECORDING_STARTED" });

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
  stopAutoCaptureInternal();
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
  state.timerStart = null;
  state.captureCount = 0;
  state.autoCaptureActive = false;
  stopAutoCaptureInternal();
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
  state.timerStart = null;
  state.captureCount = 0;
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
  state.timerStart = null;
  state.captureCount = 0;
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
    await AixisAPI.uploadObservation(state.currentSession.id, obsData);
    state.captureCount++;
    persistState();
    notifyPopup({ type: "CAPTURE_COUNT_UPDATE", count: state.captureCount });
    return { ok: true, captureCount: state.captureCount };
  } catch (err) {
    console.error("Screenshot upload failed:", err);
    return { error: err.message };
  }
}

// ---------------------------------------------------------------------------
// Partial screenshot capture
// ---------------------------------------------------------------------------

async function startPartialCapture() {
  // Inject the selection overlay into the active tab
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab) {
    return { error: "アクティブなタブが見つかりません" };
  }

  try {
    await chrome.tabs.sendMessage(tab.id, { type: "INJECT_SELECTION_OVERLAY" });
  } catch {
    // Content script might not be loaded, inject it
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

  // Step 1: Capture the full visible tab
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

  // Step 2: Crop via offscreen document
  let croppedBase64 = fullImageBase64;
  try {
    await ensureOffscreenDocument();
    const cropResult = await chrome.runtime.sendMessage({
      type: "CROP_IMAGE",
      target: "offscreen",
      imageBase64: fullImageBase64,
      rect: rect,
      devicePixelRatio: devicePixelRatio || 1,
    });
    if (cropResult?.croppedBase64) {
      croppedBase64 = cropResult.croppedBase64;
    }
  } catch (err) {
    console.warn("Crop failed, using full image:", err);
  }

  // Step 3: Upload
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
    persistState();
    notifyPopup({ type: "PARTIAL_CAPTURE_DONE", captureCount: state.captureCount });
    return { ok: true, captureCount: state.captureCount };
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
// Auto-capture (runs in service worker)
// ---------------------------------------------------------------------------

function startAutoCapture(intervalMs) {
  state.autoCaptureActive = true;
  persistState();
  startAutoCaptureInterval(intervalMs);
  return { ok: true };
}

function stopAutoCapture() {
  state.autoCaptureActive = false;
  stopAutoCaptureInternal();
  persistState();
  return { ok: true };
}

function startAutoCaptureInterval(intervalMs) {
  stopAutoCaptureInternal();
  state.autoCaptureIntervalId = setInterval(async () => {
    if (!state.currentSession || !state.autoCaptureActive) {
      stopAutoCaptureInternal();
      return;
    }
    try {
      await captureFullScreenshot();
    } catch (err) {
      console.warn("Auto-capture failed:", err);
    }
  }, intervalMs);
}

function stopAutoCaptureInternal() {
  if (state.autoCaptureIntervalId) {
    clearInterval(state.autoCaptureIntervalId);
    state.autoCaptureIntervalId = null;
  }
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

function notifyPopup(message) {
  try {
    chrome.runtime.sendMessage(message);
  } catch {}
}
