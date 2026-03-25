/**
 * Aixis Chrome Extension v3 — Minimal Popup
 * Shows a message directing the user to the floating panel on the page.
 * Provides a button to inject/show the panel if it is not visible.
 */

document.getElementById("showPanelBtn").addEventListener("click", async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab) return;

  try {
    await chrome.tabs.sendMessage(tab.id, { type: "SHOW_PANEL" });
  } catch {
    // Content script might not be loaded yet — inject it
    await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      files: ["content/content.js"],
    });
    await chrome.scripting.insertCSS({
      target: { tabId: tab.id },
      files: ["content/content.css"],
    });
  }

  window.close();
});
