(function attachCopilotExtractor() {

  function extractCopilotConversationFromPage() {
    const extractionErrors = [];

    function recordExtractionError(step, error) {
      extractionErrors.push({ step, message: error instanceof Error ? error.message : String(error) });
    }

    function normalizeText(value) {
      return String(value || "")
        .replace(/\u00a0/g, " ").replace(/\r/g, "")
        .replace(/[ \t]+\n/g, "\n").replace(/\n{3,}/g, "\n\n")
        .replace(/[ \t]{2,}/g, " ").trim();
    }

    // Copilot is a SPA — the conversation ID is not reliably in the URL.
    function getConversationId() {
      const pathId = window.location.pathname.match(/\/(?:chat|chats?|conversation|conversations|thread)\/([^/?#]+)/)?.[1];
      if (pathId) {
        return pathId;
      }

      try {
        const url = new URL(window.location.href);
        for (const key of ["conversationId", "chatId", "threadId", "id", "convId"]) {
          const value = url.searchParams.get(key);
          if (value) {
            return value;
          }
        }
      } catch {
        // Ignore URL parsing failures and fall back to DOM attributes.
      }

      for (const selector of ["[data-conversation-id]", "[data-chat-id]", "[data-thread-id]"]) {
        const node = document.querySelector(selector);
        const value = node?.getAttribute("data-conversation-id") || node?.getAttribute("data-chat-id") || node?.getAttribute("data-thread-id");
        if (value) {
          return value;
        }
      }

      return null;
    }

    function getConversationTitle() {
      return normalizeText(document.title.replace(/\s*[-–|]\s*(Microsoft\s+)?Copilot\s*$/i, "")) || null;
    }

    function isVisibleElement(el) {
      if (!(el instanceof HTMLElement)) return false;
      const s = window.getComputedStyle(el);
      return s.display !== "none" && s.visibility !== "hidden" && s.opacity !== "0";
    }

    function keepOutermostNodes(nodes) {
      return nodes.filter(n => !nodes.some(o => o !== n && o.contains(n)));
    }

    // ── Uploaded-file inference ───────────────────────────────────────────────

    function extractUploadedFilesFromText(text) {
      const m = normalizeText(text).match(/\b[^\\/:*?"<>|\n]+\.(pdf|docx?|pptx?|xlsx?|csv|txt)\b/gi);
      return Array.from(new Set((m || []).map(f => normalizeText(f)).filter(Boolean)))
        .map(fileName => ({ fileName, displayName: normalizeText(fileName.replace(/\.[^.]+$/, "")), extension: fileName.split(".").pop()?.toLowerCase() || null }));
    }

    function inferUploadedFilesFromUserMessages(userMessages) {
      const byFileName = new Map();
      for (const message of userMessages || []) {
        for (const file of extractUploadedFilesFromText(message.text)) {
          const key = file.fileName.toLowerCase();
          const existing = byFileName.get(key) || { ...file, attachedInMessages: [] };
          existing.attachedInMessages.push({ messageIndex: message.index, messageId: message.id, roleIndex: message.roleIndex });
          byFileName.set(key, existing);
        }
      }
      return Array.from(byFileName.values()).map(file => ({
        ...file,
        attachedInMessages: file.attachedInMessages.sort((a, b) => (a.messageIndex || 0) - (b.messageIndex || 0)),
        firstAttachedMessageIndex: file.attachedInMessages[0]?.messageIndex ?? null,
        attachmentMentions: file.attachedInMessages.length,
        source: "inferred_from_user_messages"
      }));
    }

    function escapeRegExp(v) { return v.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"); }

    function collectReferencedUploads(text, uploadedFiles) {
      const normalized = normalizeText(text);
      if (!normalized) return [];
      return uploadedFiles.filter(file =>
        [file.fileName, file.displayName].filter(Boolean).some(c =>
          new RegExp(`(^|\\b)${escapeRegExp(c)}(\\b|$)`, "i").test(normalized)
        )
      ).map((file, index) => ({
        index, type: "upload", title: file.fileName, fileName: file.fileName,
        displayName: file.displayName, extension: file.extension,
        url: null, host: null, citationLabel: null, rawUrl: null
      }));
    }

    function mergeSources(webSources, uploadSources) {
      const seen = new Set(); const merged = [];
      for (const s of [...webSources, ...uploadSources]) {
        const key = `${s.type}::${s.url || s.fileName || s.title || ""}`;
        if (!seen.has(key)) { seen.add(key); merged.push(s); }
      }
      return merged.map((s, i) => ({ ...s, index: i }));
    }

    // ── Source helpers ────────────────────────────────────────────────────────

    function unwrapExternalUrl(rawHref) {
      try {
        const u = new URL(rawHref, window.location.href);
        for (const p of ["url", "u", "target", "q"]) {
          const v = u.searchParams.get(p);
          if (v) { try { const n = new URL(v); if (/^https?:$/i.test(n.protocol)) return n.toString(); } catch { continue; } }
        }
        return u.toString();
      } catch { return null; }
    }

    function isSupportedSourceUrl(url) {
      try {
        const p = new URL(url);
        const internal = ["copilot.microsoft.com", "bing.com", "www.bing.com"];
        return /^https?:$/i.test(p.protocol) && !internal.includes(p.hostname);
      } catch { return false; }
    }

    function buildSource(anchor) {
      const rawUrl = anchor.getAttribute("href") || "";
      const normalizedUrl = unwrapExternalUrl(rawUrl);
      if (!normalizedUrl || !isSupportedSourceUrl(normalizedUrl)) return null;
      let host = null;
      try { host = new URL(normalizedUrl).hostname; } catch { /* noop */ }
      const title = normalizeText(anchor.innerText || anchor.textContent || anchor.getAttribute("title") || anchor.getAttribute("aria-label") || "");
      const citationMatch = title.match(/^\[(\d+)\]$/);
      return { title: title || null, url: normalizedUrl, host, citationLabel: citationMatch ? citationMatch[1] : null, rawUrl: rawUrl && rawUrl !== normalizedUrl ? rawUrl : null };
    }

    function collectWebSources(node) {
      // Try shadow root first (Copilot web components), then light DOM
      const roots = [node];
      try { if (node.shadowRoot) roots.push(node.shadowRoot); } catch { /* noop */ }

      const seen = new Set(); const sources = [];
      for (const root of roots) {
        for (const a of Array.from(root.querySelectorAll("a[href]"))) {
          const s = buildSource(a);
          if (!s) continue;
          const key = `${s.url}::${s.title || ""}`;
          if (!seen.has(key)) { seen.add(key); sources.push(s); }
        }
      }
      return sources.map((s, i) => ({ ...s, type: "web", index: i }));
    }

    // ── Copilot DOM selectors ─────────────────────────────────────────────────
    //
    // Copilot renders as Web Components:
    //   cib-message-group[source="user"]  — user turns
    //   cib-message-group[source="bot"]   — assistant turns
    //
    // Newer Copilot builds may also use plain div elements with data-testids.
    // We also handle shadow DOM text extraction for web component content.

    function getNodeText(node) {
      // Try light DOM first
      let text = normalizeText(node.innerText || node.textContent || "");
      // If empty, check shadow root
      if (!text) {
        try {
          if (node.shadowRoot) {
            text = normalizeText(node.shadowRoot.textContent || "");
          }
        } catch { /* noop */ }
      }
      return text || null;
    }

    function queryFirstMatch(selectors) {
      for (const sel of selectors) {
        try { const nodes = Array.from(document.querySelectorAll(sel)); if (nodes.length) return keepOutermostNodes(nodes); } catch { /* skip */ }
      }
      return [];
    }

    const USER_TURN_SELECTORS = [
      'cib-message-group[source="user"]',
      '[data-testid="user-message"]',
      '[class*="user-message"]',
      '[class*="human-message"]',
    ];

    const ASSISTANT_TURN_SELECTORS = [
      'cib-message-group[source="bot"]',
      'cib-message-group[source="assistant"]',
      '[data-testid="assistant-message"]',
      '[class*="bot-message"]',
      '[class*="ai-message"]',
      '[class*="assistant-message"]',
    ];

    function extractTurnText(node) {
      try {
        // Try sub-selectors first (light DOM)
        for (const sel of [".prose", "[class*='prose']", ".markdown", "[class*='markdown']", "p"]) {
          const el = node.querySelector(sel);
          if (el) { const t = normalizeText(el.innerText || el.textContent || ""); if (t) return t; }
        }
        return getNodeText(node);
      } catch (error) {
        recordExtractionError("extract_turn_text", error);
        return getNodeText(node);
      }
    }

    function extractMessages() {
      let userNodes = queryFirstMatch(USER_TURN_SELECTORS);
      let assistantNodes = queryFirstMatch(ASSISTANT_TURN_SELECTORS);

      // Fallback: structural heuristic
      if (!userNodes.length && !assistantNodes.length) {
        const feed =
          document.querySelector("cib-conversation") ||
          document.querySelector('[class*="conversation"]') ||
          document.querySelector("main") ||
          document.body;

        const candidates = keepOutermostNodes(
          Array.from(feed.querySelectorAll("div, article, section, cib-chat-turn")).filter(el => {
            const t = getNodeText(el);
            return t && t.length > 10;
          })
        );

        candidates.forEach((el, i) => {
          if (i % 2 === 0) userNodes.push(el); else assistantNodes.push(el);
        });
      }

      const allTurns = [
        ...userNodes.map(n => ({ node: n, role: "user" })),
        ...assistantNodes.map(n => ({ node: n, role: "assistant" }))
      ].sort((a, b) => {
        const pos = a.node.compareDocumentPosition(b.node);
        if (pos & Node.DOCUMENT_POSITION_FOLLOWING) return -1;
        if (pos & Node.DOCUMENT_POSITION_PRECEDING) return 1;
        return 0;
      });

      const messages = [];
      let globalIndex = 0, userRoleIndex = 0, assistantRoleIndex = 0;
      const seenNodes = new WeakSet(), seenTexts = new Set();

      for (const { node, role } of allTurns) {
        if (seenNodes.has(node)) continue;
        seenNodes.add(node);
        const text = extractTurnText(node);
        if (!text) continue;
        const dk = `${role}:${text}`;
        if (seenTexts.has(dk)) continue;
        seenTexts.add(dk);
        const roleIndex = role === "user" ? userRoleIndex++ : assistantRoleIndex++;
        messages.push({
          index: globalIndex++, id: `${role}-${roleIndex}`, role, roleIndex, text,
          webSources: role === "assistant" ? collectWebSources(node) : []
        });
      }

      if (!messages.length) {
        console.warn("[Copilot Extractor][Page] No messages found. Logging DOM hints:",
          { customElements: Array.from(document.querySelectorAll("cib-message-group, cib-chat-turn")).length,
            testids: Array.from(document.querySelectorAll("[data-testid]")).map(el => el.getAttribute("data-testid")).filter(Boolean).slice(0, 40) }
        );
      }

      return messages;
    }

    // ── Build payload ─────────────────────────────────────────────────────────

    const rawMessages = extractMessages();
    const userMessages = rawMessages.filter(m => m.role === "user");
    const uploadedFiles = inferUploadedFilesFromUserMessages(userMessages);

    const messages = rawMessages.map(m => {
      const uploadSources = m.role === "assistant" ? collectReferencedUploads(m.text, uploadedFiles) : [];
      const sources = m.role === "assistant" ? mergeSources(m.webSources || [], uploadSources) : [];
      const { webSources, ...rest } = m;
      return { ...rest, sources, sourceCount: sources.length };
    });

    const assistantMessages = messages.filter(m => m.role === "assistant");
    const totalSourceCount = assistantMessages.reduce((n, m) => n + m.sourceCount, 0);
    const totalWebSourceCount = assistantMessages.reduce((n, m) => n + m.sources.filter(s => s.type === "web").length, 0);
    const totalUploadReferenceCount = assistantMessages.reduce((n, m) => n + m.sources.filter(s => s.type === "upload").length, 0);

    const payload = {
      schemaVersion: "1.0.0", platform: "copilot",
      extractedAt: new Date().toISOString(),
      conversation: { id: getConversationId(), url: window.location.href, title: getConversationTitle() },
      summary: {
        messageCount: messages.length, userMessageCount: userMessages.length,
        assistantMessageCount: assistantMessages.length, pageCanvasDocumentCount: 0,
        uploadCount: uploadedFiles.length, totalSourceCount, totalWebSourceCount, totalUploadReferenceCount
      },
      uploadedFiles, pageCanvasDocuments: [], messages, extractionErrors
    };

    console.log("[Copilot Extractor][Page] Extracted conversation payload:", payload);
    console.log("[Copilot Extractor][Page] JSON:\n" + JSON.stringify(payload, null, 2));
    return payload;
  }

  window.__hdExtractCopilotConversation = extractCopilotConversationFromPage;
})();
