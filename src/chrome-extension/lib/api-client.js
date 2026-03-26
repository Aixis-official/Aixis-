/**
 * Aixis Platform API client for Chrome extension v2.
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
   * Make an authenticated API request with timeout and robust error handling.
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

    // Add 30-second timeout to prevent hung requests blocking the session
    const controller = new AbortController();
    options.signal = controller.signal;
    const timeoutId = setTimeout(() => controller.abort(), 30000);

    let response;
    try {
      response = await fetch(url, options);
    } catch (err) {
      clearTimeout(timeoutId);
      if (err.name === "AbortError") {
        throw new Error("APIリクエストがタイムアウトしました（30秒）");
      }
      // Network error (offline, DNS failure, CORS, etc.)
      throw new Error("ネットワークエラー: " + (err.message || "接続できません"));
    } finally {
      clearTimeout(timeoutId);
    }

    if (!response.ok) {
      let detail = `API error: ${response.status}`;
      try {
        const errData = await response.json();
        detail = errData.detail || detail;
      } catch {}
      throw new Error(detail);
    }

    // Handle empty or non-JSON responses safely
    const contentType = response.headers.get("content-type") || "";
    if (!contentType.includes("application/json")) {
      // Some endpoints may return 200 with no body
      const text = await response.text();
      if (!text || text.trim() === "") {
        return { ok: true };
      }
      // Try parsing as JSON anyway (some servers don't set content-type correctly)
      try {
        return JSON.parse(text);
      } catch {
        throw new Error("サーバーが不正なレスポンスを返しました");
      }
    }

    try {
      return await response.json();
    } catch (err) {
      throw new Error("JSONパースエラー: レスポンスの解析に失敗しました");
    }
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
    // Ensure response_time_ms is sent correctly
    const payload = {
      ...observation,
      response_time_ms: observation.response_time_ms || 0,
    };
    return this.request("POST", `/sessions/${sessionId}/observations`, payload);
  },

  async completeSession(sessionId) {
    return this.request("POST", `/sessions/${sessionId}/complete`);
  },

  async getProgress(sessionId) {
    return this.request("GET", `/sessions/${sessionId}/progress`);
  },

  async advanceProgress(sessionId, data) {
    return this.request("POST", `/sessions/${sessionId}/advance`, data);
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
