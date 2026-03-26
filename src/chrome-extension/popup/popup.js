/**
 * Aixis Chrome Extension v3 — Minimal Popup
 * Shows a message directing the user to the floating panel on the page.
 * Provides a button to inject/show the panel if it is not visible.
 */

document.getElementById("showPanelBtn").addEventListener("click", async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab) {
    document.getElementById("showPanelBtn").textContent = "タブが見つかりません";
    return;
  }

  // Check if it's a chrome:// or extension page (can't inject scripts there)
  if (tab.url && (tab.url.startsWith("chrome://") || tab.url.startsWith("chrome-extension://") || tab.url.startsWith("about:"))) {
    document.getElementById("showPanelBtn").textContent = "このページでは使用できません";
    return;
  }

  try {
    // Try sending message to existing content script first
    await chrome.tabs.sendMessage(tab.id, { type: "SHOW_PANEL" });
  } catch {
    // Content script not loaded — inject it
    try {
      await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        files: ["content/content.js"],
      });
      // Wait for content script to initialize
      await new Promise(r => setTimeout(r, 500));
      // Now send SHOW_PANEL
      try {
        await chrome.tabs.sendMessage(tab.id, { type: "SHOW_PANEL" });
      } catch {}
    } catch (err) {
      document.getElementById("showPanelBtn").textContent = "注入に失敗: " + (err.message || "不明なエラー");
      return;
    }
  }

  window.close();
});
