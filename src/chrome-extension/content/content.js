/**
 * Aixis Chrome Extension v2 — Content Script
 *
 * Simplified: no DOM observation.
 * Only handles:
 * - Recording indicator (floating badge)
 * - Partial screenshot selection overlay
 */

(() => {
  "use strict";

  let indicator = null;

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
  // Partial screenshot selection overlay
  // ---------------------------------------------------------------------------

  function injectSelectionOverlay() {
    // Remove any existing overlay
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

      // Only send if selection has meaningful size
      if (rect.w > 10 && rect.h > 10) {
        chrome.runtime.sendMessage({
          type: "PARTIAL_CAPTURE_COORDS",
          rect: rect,
          devicePixelRatio: window.devicePixelRatio || 1,
        });
      }
    }

    function onKeyDown(e) {
      if (e.key === "Escape") {
        removeSelectionOverlay();
      }
    }

    overlay.addEventListener("mousedown", onMouseDown);
    overlay.addEventListener("mousemove", onMouseMove);
    overlay.addEventListener("mouseup", onMouseUp);
    document.addEventListener("keydown", onKeyDown, { once: true });
  }

  function removeSelectionOverlay() {
    const existing = document.getElementById("aixis-selection-overlay");
    if (existing) {
      existing.remove();
    }
  }

  // ---------------------------------------------------------------------------
  // Messages from background
  // ---------------------------------------------------------------------------

  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    switch (message.type) {
      case "RECORDING_STARTED":
        showIndicator();
        break;

      case "RECORDING_STOPPED":
        hideIndicator();
        break;

      case "INJECT_SELECTION_OVERLAY":
        injectSelectionOverlay();
        break;
    }
  });

  // ---------------------------------------------------------------------------
  // Initialize — check if we should show indicator
  // ---------------------------------------------------------------------------

  chrome.runtime.sendMessage({ type: "GET_STATE" }, (response) => {
    if (response && response.isRecording) {
      showIndicator();
    }
  });
})();
