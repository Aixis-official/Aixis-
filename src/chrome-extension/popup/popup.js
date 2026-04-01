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
    const resp = await chrome.tabs.sendMessage(tab.id, { type: "SHOW_PANEL" });
    // If we got a response but the content script is orphaned, it won't have resp.ok
    if (!resp || !resp.ok) throw new Error("stale");
  } catch {
    // Content script not loaded or orphaned — inject fresh
    try {
      // Set a DOM flag so the content script knows to replace the stale panel
      // (popup only reaches this path when SHOW_PANEL failed → old script is orphaned)
      await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        func: () => { window.__aixis_force_reinject = true; },
      });
      // Inject both CSS and JS (CSS is needed for selection overlay)
      await chrome.scripting.insertCSS({
        target: { tabId: tab.id },
        files: ["content/content.css"],
      });
      await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        files: ["content/content.js"],
      });
      // Wait for content script to initialize
      await new Promise(r => setTimeout(r, 600));
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
