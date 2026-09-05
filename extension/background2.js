chrome.action.onClicked.addListener(async (tab) => {
  if (!tab?.id) {
    console.warn("No active tab id was found.");
    return;
  }

  const url = tab.url || "";
  const isChatGptPage =
    url.startsWith("https://chatgpt.com/") ||
    url.startsWith("https://chat.openai.com/");

  if (!isChatGptPage) {
    console.warn("Open a ChatGPT conversation tab first.");
    return;
  }

  try {
    const statementsWithMeta = [
      {
        statement: "hello",
        score: "0.62",
        citations: ["Source A", "Source B"],
        note: "Dummy metadata for hover card. Replace this payload with FastAPI response."
      },
      {
        statement: "bye",
        score: "0.18",
        citations: ["Course PDF", "Cloud docs"],
        note: "Low-risk sentence based on current placeholder scoring."
      },
      {
        statement: "see",
        score: "0.75",
        citations: ["Observation only"],
        note: "Potentially broad claim. Verify with concrete setup requirements."
      }
    ];

    await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      files: ["dom.js"]
    });

    const [{ result }] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: (payload) => {
        if (typeof window.__hdApplyHighlights !== "function") {
          return {
            ok: false,
            reason: "Highlighter entrypoint was not found in page context."
          };
        }

        const runResult = window.__hdApplyHighlights(payload);
        return {
          ok: true,
          ...runResult
        };
      },
      args: [statementsWithMeta]
    });

    console.log("[ChatGPT Extractor] Highlight run result:", result);
  } catch (error) {
    console.error("[ChatGPT Extractor] Highlighting failed:", error);
  }
});
