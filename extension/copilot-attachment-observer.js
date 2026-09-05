(function attachCopilotAttachmentObserver() {
  if (window.__hdCopilotAttachmentObserverAttached) return;
  window.__hdCopilotAttachmentObserverAttached = true;

  const CONTENT_SCRIPT_BUILD_TAG = "copilot-attachment-observer-v1";
  const RECENT_CAPTURE_TTL_MS = 4000;
  const FILE_INPUT_SELECTOR = "input[type='file']";
  const wiredInputs = new WeakSet();
  const recentCaptureKeys = new Map();

  function normalizeText(value) {
    return String(value || "").replace(/\u00a0/g, " ").replace(/\r/g, "")
      .replace(/[ \t]+\n/g, "\n").replace(/\n{3,}/g, "\n\n").replace(/[ \t]{2,}/g, " ").trim();
  }

  function getConversationId() { return window.location.pathname.match(/\/(?:chat|chats?)\/([^/?#]+)/)?.[1] || null; }
  function getConversationTitle() { return normalizeText(document.title.replace(/\s*[-–|]\s*(Microsoft\s+)?Copilot\s*$/i, "")) || null; }
  function getConversationContext() { return { platform: "copilot", id: getConversationId(), url: window.location.href, title: getConversationTitle() }; }

  function pruneRecentCaptureKeys(now) {
    for (const [key, ts] of recentCaptureKeys.entries()) { if (now - ts > RECENT_CAPTURE_TTL_MS) recentCaptureKeys.delete(key); }
  }
  function buildCaptureKey(file, cid) { return [cid || "draft", file.name || "", String(file.size || 0), String(file.lastModified || 0)].join("::"); }
  function shouldCaptureFile(file, cid) {
    const now = Date.now(); const key = buildCaptureKey(file, cid); pruneRecentCaptureKeys(now);
    if (recentCaptureKeys.has(key)) return false; recentCaptureKeys.set(key, now); return true;
  }

  function sendRuntimeMessage(message) {
    return new Promise((resolve) => {
      try {
        chrome.runtime.sendMessage(message, (response) => {
          if (chrome.runtime.lastError) { resolve({ __runtimeError: chrome.runtime.lastError.message || "Unknown runtime error" }); return; }
          resolve(response || null);
        });
      } catch { resolve({ __runtimeError: "Failed to call chrome.runtime.sendMessage" }); }
    });
  }

  async function pingBackgroundServiceWorker() {
    const response = await sendRuntimeMessage({ type: "chatgpt_background_ping" });
    console.log("[Copilot Extractor] Content script booted:", { build: CONTENT_SCRIPT_BUILD_TAG, background: response });
  }

  function uint8ArrayToBase64(bytes) {
    const cs = 0x8000; let b = "";
    for (let i = 0; i < bytes.length; i += cs) b += String.fromCharCode(...bytes.subarray(i, i + cs));
    return btoa(b);
  }

  async function serializeFileForBackgroundUpload(file) {
    const ab = await file.arrayBuffer(); const bytes = new Uint8Array(ab);
    return { name: file.name || "upload.bin", type: file.type || "application/octet-stream", size: Number.isFinite(file.size) ? file.size : bytes.length, base64: uint8ArrayToBase64(bytes) };
  }

  function buildAttachmentMetadata(file, source, conversation) {
    return { source, capturedAt: new Date().toISOString(), conversation, file: { name: file.name || null, type: file.type || null, size: Number.isFinite(file.size) ? file.size : null, lastModified: file.lastModified ? new Date(file.lastModified).toISOString() : null } };
  }

  function isTextLikeFile(file) {
    const mt = (file.type || "").toLowerCase(); const fn = (file.name || "").toLowerCase();
    if (mt.startsWith("text/")) return true;
    return [".txt",".md",".json",".csv",".tsv",".js",".ts",".jsx",".tsx",".html",".css",".xml",".yml",".yaml"].some(e => fn.endsWith(e));
  }

  async function buildDebugPreview(file) {
    try {
      if (isTextLikeFile(file)) { const t = await file.text(); return { kind: "text", characterCount: t.length, preview: t.slice(0, 2000) }; }
      const b = new Uint8Array(await file.arrayBuffer());
      return { kind: "binary", byteCount: b.length, previewHex: Array.from(b.slice(0,32)).map(x=>x.toString(16).padStart(2,"0")).join(" ") };
    } catch (e) { return { kind: "unavailable", error: String(e) }; }
  }

  async function forwardAttachmentToBackend(file, metadata) {
    try {
      const fileData = await serializeFileForBackgroundUpload(file);
      const response = await sendRuntimeMessage({ type: "chatgpt_upload_attachment", payload: { metadata, fileData } });
      if (response?.__runtimeError) return { attempted: true, ok: false, status: "extension_error", error: response.__runtimeError };
      if (!response) return { attempted: true, ok: false, status: "extension_error", error: "No response from background." };
      return response;
    } catch (e) { return { attempted: true, ok: false, status: "serialization_error", error: String(e) }; }
  }

  async function captureFiles(fileLikeList, source) {
    const files = Array.from(fileLikeList || []).filter(f => f instanceof File);
    if (!files.length) return;
    const conversation = getConversationContext();
    for (const file of files) {
      if (!shouldCaptureFile(file, conversation.id)) continue;
      const metadata = buildAttachmentMetadata(file, source, conversation);
      const debugPreview = await buildDebugPreview(file);
      const backendForwarding = await forwardAttachmentToBackend(file, metadata);
      const payload = { ...metadata, debugPreview, backendForwarding };
      console.log("[Copilot Extractor] Captured Copilot attachment:", payload);
      await sendRuntimeMessage({ type: "chatgpt_attachment_captured", payload });
    }
  }

  function wireFileInput(input) {
    if (!(input instanceof HTMLInputElement) || wiredInputs.has(input)) return;
    wiredInputs.add(input);
    input.addEventListener("change", (e) => { const t = e.currentTarget; if (t instanceof HTMLInputElement) void captureFiles(t.files, "file_input"); }, true);
  }

  function wireExistingFileInputs() { document.querySelectorAll(FILE_INPUT_SELECTOR).forEach(wireFileInput); }

  new MutationObserver(wireExistingFileInputs).observe(document.documentElement, { childList: true, subtree: true });
  document.addEventListener("drop", (e) => { if (e.dataTransfer?.files?.length) void captureFiles(e.dataTransfer.files, "drag_and_drop"); }, true);
  document.addEventListener("paste", (e) => {
    const files = Array.from(e.clipboardData?.items || []).filter(i => i.kind === "file").map(i => i.getAsFile()).filter(Boolean);
    if (files.length) void captureFiles(files, "paste");
  }, true);

  wireExistingFileInputs();
  void pingBackgroundServiceWorker();
})();
