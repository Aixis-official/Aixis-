/**
 * Aixis Chrome Extension — Background Service Worker
 *
 * Manages session state, coordinates content script observations,
 * handles API communication and screenshot capture.
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
  pendingObservation: null, // Buffered from content script
  observationCount: 0,
};

// Restore state from storage on service worker wake
chrome.storage.local.get(["sessionState"], (data) => {
  if (data.sessionState) {
    Object.assign(state, data.sessionState);
  }
});

function persistState() {
  chrome.storage.local.set({ sessionState: state });
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
      return { ...state };

    case "GET_CURRENT_TEST":
      return getCurrentTest();

    case "NEXT_TEST":
      return await advanceTest(message);

    case "SKIP_TEST":
      return await skipTest(message);

    // --- Recording control ---
    case "START_RECORDING":
      return startRecording();

    case "STOP_RECORDING":
      return await stopRecording();

    // --- Content script observations ---
    case "INTERACTION_COMPLETE":
      return await handleInteraction(message, sender);

    // --- Manual screenshot ---
    case "MANUAL_SCREENSHOT":
      return await captureManualScreenshot(message);

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

    case "RESET_SESSION":
      return resetSession();

    default:
      return { error: `Unknown message type: ${message.type}` };
  }
}

// ---------------------------------------------------------------------------
// Session management
// ---------------------------------------------------------------------------

async function createSession({ toolId, profileId, recordingMode, categories }) {
  const result = await AixisAPI.createSession(
    toolId,
    profileId || "",
    recordingMode || "protocol",
    categories
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
  state.pendingObservation = null;

  persistState();

  // Notify content scripts to start observing
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
  state.pendingObservation = null;
  state.observationCount = 0;
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

  // Upload the observation for the current test
  const currentTest = state.testCases[state.currentTestIndex];

  // Capture screenshot
  let screenshotBase64 = null;
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (tab) {
      const dataUrl = await chrome.tabs.captureVisibleTab(null, { format: "png" });
      screenshotBase64 = dataUrl.replace(/^data:image\/png;base64,/, "");
    }
  } catch (err) {
    console.warn("Screenshot capture failed:", err);
  }

  // Build observation data
  const obsData = {
    test_case_id: currentTest?.id || null,
    prompt_text: observation?.promptText || currentTest?.prompt || "",
    response_text: observation?.responseText || null,
    response_time_ms: observation?.responseTimeMs || 0,
    page_url: observation?.pageUrl || null,
    screenshot_base64: screenshotBase64,
    metadata: observation?.metadata || {},
  };

  try {
    await AixisAPI.uploadObservation(state.currentSession.id, obsData);
    state.observationCount++;
  } catch (err) {
    console.error("Observation upload failed:", err);
    return { error: err.message };
  }

  // Advance to next test
  state.currentTestIndex++;
  persistState();

  // Check if all tests complete
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

  // Upload a skipped observation
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
  persistState();

  if (state.currentTestIndex >= state.testCases.length) {
    return { done: true, index: state.currentTestIndex, total: state.testCases.length };
  }

  return getCurrentTest();
}

// ---------------------------------------------------------------------------
// Freeform recording
// ---------------------------------------------------------------------------

function startRecording() {
  state.isRecording = true;
  persistState();
  broadcastToContentScripts({ type: "RECORDING_STARTED" });
  return { ok: true };
}

async function stopRecording() {
  state.isRecording = false;
  persistState();
  broadcastToContentScripts({ type: "RECORDING_STOPPED" });
  return { ok: true };
}

// ---------------------------------------------------------------------------
// Content script interaction handling
// ---------------------------------------------------------------------------

async function handleInteraction(message, sender) {
  if (!state.isRecording || !state.currentSession) {
    return { ignored: true };
  }

  // Capture screenshot
  let screenshotBase64 = null;
  try {
    if (sender.tab) {
      const dataUrl = await chrome.tabs.captureVisibleTab(
        sender.tab.windowId,
        { format: "png" }
      );
      screenshotBase64 = dataUrl.replace(/^data:image\/png;base64,/, "");
    }
  } catch (err) {
    console.warn("Screenshot capture failed:", err);
  }

  // Determine test_case_id (protocol mode uses current test, freeform uses null)
  let testCaseId = null;
  if (state.currentSession.recordingMode === "protocol" && state.testCases.length) {
    const currentTest = state.testCases[state.currentTestIndex];
    testCaseId = currentTest?.id || null;
  }

  const obsData = {
    test_case_id: testCaseId,
    prompt_text: message.prompt || "",
    response_text: message.response || null,
    response_time_ms: message.responseTimeMs || 0,
    page_url: message.pageUrl || sender.tab?.url || null,
    screenshot_base64: screenshotBase64,
    metadata: message.metadata || {},
  };

  try {
    const result = await AixisAPI.uploadObservation(state.currentSession.id, obsData);
    state.observationCount++;
    persistState();
    return { ok: true, observationId: result.observation_id };
  } catch (err) {
    console.error("Observation upload failed:", err);
    return { error: err.message };
  }
}

// ---------------------------------------------------------------------------
// Manual screenshot capture (UI/settings/error screens)
// ---------------------------------------------------------------------------

async function captureManualScreenshot({ label }) {
  if (!state.currentSession) {
    return { error: "アクティブなセッションがありません" };
  }

  // Capture current visible tab
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
    console.warn("Manual screenshot failed:", err);
    return { error: "スクリーンショットの取得に失敗しました" };
  }

  // Upload as a manual observation (not tied to a specific test case)
  const obsData = {
    test_case_id: null,
    prompt_text: label || "手動スクリーンショット",
    response_text: null,
    response_time_ms: 0,
    page_url: pageUrl,
    screenshot_base64: screenshotBase64,
    metadata: {
      capture_type: "manual_screenshot",
      label: label || "",
      page_title: pageTitle || "",
      timestamp: new Date().toISOString(),
    },
  };

  try {
    const result = await AixisAPI.uploadObservation(state.currentSession.id, obsData);
    state.observationCount++;
    persistState();
    return { ok: true, observationId: result.observation_id };
  } catch (err) {
    console.error("Manual screenshot upload failed:", err);
    return { error: err.message };
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
