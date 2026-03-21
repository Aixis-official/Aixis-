/**
 * Aixis Chrome Extension — Content Script
 *
 * Observes DOM for user inputs and AI responses on any page.
 * Detects input submission, response generation, and measures timing.
 * Communicates observations to the background service worker.
 */

(() => {
  "use strict";

  // ---------------------------------------------------------------------------
  // State
  // ---------------------------------------------------------------------------

  let isRecording = false;
  let lastInputText = "";
  let lastInputTimestamp = 0;
  let responseObserver = null;
  let responseStabilizeTimer = null;
  let indicator = null;

  const RESPONSE_STABILIZE_MS = 2000; // Wait for response to stop changing

  // ---------------------------------------------------------------------------
  // Recording indicator UI
  // ---------------------------------------------------------------------------

  function createIndicator() {
    if (indicator) return;
    indicator = document.createElement("div");
    indicator.id = "aixis-recording-indicator";
    indicator.innerHTML = '<span class="dot"></span><span>Aixis 記録中</span>';
    indicator.classList.add("idle");
    document.body.appendChild(indicator);
  }

  function showIndicator() {
    if (!indicator) createIndicator();
    indicator.classList.remove("idle");
  }

  function hideIndicator() {
    if (indicator) {
      indicator.classList.add("idle");
    }
  }

  // ---------------------------------------------------------------------------
  // Input detection
  // ---------------------------------------------------------------------------

  /**
   * Find all input elements that could be used for AI tool input.
   */
  function getInputElements() {
    return [
      ...document.querySelectorAll("textarea"),
      ...document.querySelectorAll('input[type="text"]'),
      ...document.querySelectorAll("[contenteditable=true]"),
      ...document.querySelectorAll('[role="textbox"]'),
    ];
  }

  /**
   * Extract text from an input element.
   */
  function getInputText(el) {
    if (el.tagName === "TEXTAREA" || el.tagName === "INPUT") {
      return el.value || "";
    }
    return el.innerText || el.textContent || "";
  }

  /**
   * Detect when user submits input (Enter key or button click).
   */
  function onKeyDown(e) {
    if (!isRecording) return;

    // Enter without Shift typically submits in chat UIs
    if (e.key === "Enter" && !e.shiftKey) {
      const text = getInputText(e.target);
      if (text.trim()) {
        captureInput(text.trim());
      }
    }
  }

  /**
   * Detect submit button clicks.
   */
  function onButtonClick(e) {
    if (!isRecording) return;

    const btn = e.target.closest("button, [role=button], [type=submit]");
    if (!btn) return;

    // Check for nearby input element
    const inputs = getInputElements();
    for (const input of inputs) {
      const text = getInputText(input);
      if (text.trim()) {
        captureInput(text.trim());
        break;
      }
    }
  }

  function captureInput(text) {
    lastInputText = text;
    lastInputTimestamp = Date.now();

    // Start watching for response
    startResponseObservation();
  }

  // ---------------------------------------------------------------------------
  // Response detection via MutationObserver
  // ---------------------------------------------------------------------------

  function startResponseObservation() {
    stopResponseObservation();

    let lastContent = getMainContentSnapshot();
    let lastChangeTimestamp = Date.now();

    responseObserver = new MutationObserver(() => {
      const currentContent = getMainContentSnapshot();
      if (currentContent !== lastContent) {
        lastContent = currentContent;
        lastChangeTimestamp = Date.now();

        // Reset stabilization timer
        clearTimeout(responseStabilizeTimer);
        responseStabilizeTimer = setTimeout(() => {
          // Content has been stable for RESPONSE_STABILIZE_MS
          onResponseStabilized(currentContent);
        }, RESPONSE_STABILIZE_MS);
      }
    });

    responseObserver.observe(document.body, {
      childList: true,
      subtree: true,
      characterData: true,
    });

    // Fallback: if no response within 60s, stop observing
    setTimeout(() => {
      if (responseObserver) {
        stopResponseObservation();
      }
    }, 60000);
  }

  function stopResponseObservation() {
    if (responseObserver) {
      responseObserver.disconnect();
      responseObserver = null;
    }
    clearTimeout(responseStabilizeTimer);
  }

  /**
   * Get a snapshot of the main content area for change detection.
   * Focuses on likely response containers.
   */
  function getMainContentSnapshot() {
    // Common AI tool response selectors
    const selectors = [
      '[class*="response"]',
      '[class*="message"]',
      '[class*="answer"]',
      '[class*="output"]',
      '[class*="chat"]',
      '[class*="conversation"]',
      '[role="log"]',
      '[role="main"]',
      "main",
      "#__next",
      "#app",
    ];

    for (const sel of selectors) {
      const el = document.querySelector(sel);
      if (el) {
        return el.innerText || "";
      }
    }

    return document.body.innerText || "";
  }

  /**
   * Extract the most recent response text from the page.
   */
  function extractLatestResponse() {
    // Look for the last message-like element
    const messageSelectors = [
      '[class*="assistant"]',
      '[class*="bot-message"]',
      '[class*="ai-message"]',
      '[class*="response"]:last-child',
      '[data-message-author-role="assistant"]',
      '[class*="message"]:last-child',
    ];

    for (const sel of messageSelectors) {
      const elements = document.querySelectorAll(sel);
      if (elements.length > 0) {
        const lastEl = elements[elements.length - 1];
        const text = lastEl.innerText || lastEl.textContent || "";
        if (text.trim()) return text.trim();
      }
    }

    // Fallback: get last significant text block that appeared after input
    return null;
  }

  function onResponseStabilized(contentSnapshot) {
    stopResponseObservation();

    if (!lastInputText || !lastInputTimestamp) return;

    const responseTimeMs = Date.now() - lastInputTimestamp;
    const responseText = extractLatestResponse() || contentSnapshot.slice(-2000);

    // Send to background script
    chrome.runtime.sendMessage({
      type: "INTERACTION_COMPLETE",
      prompt: lastInputText,
      response: responseText,
      responseTimeMs,
      pageUrl: window.location.href,
      metadata: {
        pageTitle: document.title,
        timestamp: new Date().toISOString(),
      },
    });

    // Reset
    lastInputText = "";
    lastInputTimestamp = 0;
  }

  // ---------------------------------------------------------------------------
  // Event listeners
  // ---------------------------------------------------------------------------

  function attachListeners() {
    document.addEventListener("keydown", onKeyDown, true);
    document.addEventListener("click", onButtonClick, true);
  }

  function detachListeners() {
    document.removeEventListener("keydown", onKeyDown, true);
    document.removeEventListener("click", onButtonClick, true);
    stopResponseObservation();
  }

  // ---------------------------------------------------------------------------
  // Messages from background
  // ---------------------------------------------------------------------------

  chrome.runtime.onMessage.addListener((message) => {
    switch (message.type) {
      case "RECORDING_STARTED":
        isRecording = true;
        attachListeners();
        showIndicator();
        break;

      case "RECORDING_STOPPED":
        isRecording = false;
        detachListeners();
        hideIndicator();
        break;
    }
  });

  // ---------------------------------------------------------------------------
  // Initialize
  // ---------------------------------------------------------------------------

  // Check if we should already be recording (service worker may have restarted)
  chrome.runtime.sendMessage({ type: "GET_STATE" }, (response) => {
    if (response && response.isRecording) {
      isRecording = true;
      attachListeners();
      showIndicator();
    }
  });
})();
