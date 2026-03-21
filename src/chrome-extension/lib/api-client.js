/**
 * Aixis Platform API client for Chrome extension.
 * Handles all communication with the FastAPI backend.
 */

const AixisAPI = {
  /**
   * Get stored settings (apiKey, platformUrl).
   */
  async getSettings() {
    const data = await chrome.storage.local.get(["apiKey", "platformUrl"]);
    return {
      apiKey: data.apiKey || "",
      platformUrl: data.platformUrl || "https://platform.aixis.jp",
    };
  },

  /**
   * Make an authenticated API request.
   */
  async request(method, path, body = null) {
    const { apiKey, platformUrl } = await this.getSettings();
    if (!apiKey) {
      throw new Error("APIキーが設定されていません");
    }

    const url = `${platformUrl}/api/v1/extension${path}`;
    const options = {
      method,
      headers: {
        "X-API-Key": apiKey,
        "Content-Type": "application/json",
      },
    };
    if (body) {
      options.body = JSON.stringify(body);
    }

    const response = await fetch(url, options);

    if (!response.ok) {
      let detail = `API error: ${response.status}`;
      try {
        const errData = await response.json();
        detail = errData.detail || detail;
      } catch {}
      throw new Error(detail);
    }

    return response.json();
  },

  // --- Session APIs ---

  async createSession(toolId, profileId, recordingMode, categories = null) {
    return this.request("POST", "/sessions", {
      tool_id: toolId,
      profile_id: profileId,
      recording_mode: recordingMode,
      categories,
    });
  },

  async getTestCases(sessionId) {
    return this.request("GET", `/sessions/${sessionId}/test-cases`);
  },

  async uploadObservation(sessionId, observation) {
    return this.request("POST", `/sessions/${sessionId}/observations`, observation);
  },

  async completeSession(sessionId) {
    return this.request("POST", `/sessions/${sessionId}/complete`);
  },

  async getProgress(sessionId) {
    return this.request("GET", `/sessions/${sessionId}/progress`);
  },

  // --- Tool APIs ---

  async listTools() {
    return this.request("GET", "/tools");
  },
};

// Export for service worker
if (typeof globalThis !== "undefined") {
  globalThis.AixisAPI = AixisAPI;
}
