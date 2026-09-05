import { useState, useEffect } from "react";
import { ChatLayout } from "./chat-layout";
import type { MessageProps, HallucinationSpan } from "./chat-message-bubble";
import {
  fetchModels,
  sendChatMessageStream,
  detectHallucinations,
  scoreToRisk,
  getConversation,
  addMessageToConversation,
  type BackendModel,
  type ChatMessage,
} from "@/lib/api";

// ModelId is now dynamic — just a string alias
export type ModelId = string;

export interface ChatPaneData {
  id: string;
  modelId: ModelId;
  messages: MessageProps[];
}

interface ChatContainerProps {
  activeChatId: string;
}

export function ChatContainer({ activeChatId }: ChatContainerProps) {
  const [panes, setPanes] = useState<ChatPaneData[]>([]);
  const [isThinking, setIsThinking] = useState(false);
  const [isModelSelectionLocked, setIsModelSelectionLocked] = useState(false);
  const [models, setModels] = useState<BackendModel[]>([]);
  const [modelsLoading, setModelsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // ── Load models from backend on mount (+ 设置页保存后自动刷新) ─────────
  useEffect(() => {
    const load = () => {
      fetchModels()
        .then((data) => {
          const available = data.filter((m) => m.available);
          setModels(available);
        })
        .catch((e) => {
          console.error("Failed to load models:", e);
          setError("Backend unreachable — is the server running on :8000 ?");
        })
        .finally(() => setModelsLoading(false));
    };
    load();
    const onSaved = () => load();
    window.addEventListener("provider-settings-saved", onSaved);
    return () => window.removeEventListener("provider-settings-saved", onSaved);
  }, []);

  // ── Reset panes on new session ──────────────────────────────────────────
  useEffect(() => {
    if (!activeChatId) return;
    
    console.log(`[ChatContainer] Loading conversation ID: ${activeChatId}`);
    
    getConversation(activeChatId).then(conv => {
      const firstModel = models[0]?.id ?? "llama-3.3-70b-versatile";
      
      // If no messages, just clear panes to a blank first model
      if (!conv.messages || conv.messages.length === 0) {
        setPanes([{ id: `pane-root-${Date.now()}`, modelId: firstModel, messages: [] }]);
        setIsModelSelectionLocked(false);
        setIsThinking(false);
        setError(null);
        return;
      }

      // Rebuild per-model panes from persisted messages.
      const assistantModelsInOrder = Array.from(
        new Set(
          conv.messages
            .filter((m) => m.role === "assistant" && !!m.model_id)
            .map((m) => m.model_id as string)
        )
      ).slice(0, 3);

      const paneModelIds = assistantModelsInOrder.length > 0 ? assistantModelsInOrder : [firstModel];
      const messagesByModel = new Map<string, MessageProps[]>();
      paneModelIds.forEach((id) => messagesByModel.set(id, []));

      conv.messages.forEach((m) => {
        const mapped = {
          id: m.id,
          role: m.role,
          content: m.content,
          timestamp: new Date(m.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
          spans: [],
        } as MessageProps;

        if (m.role === "user") {
          paneModelIds.forEach((modelId) => {
            const list = messagesByModel.get(modelId);
            if (list) list.push(mapped);
          });
          return;
        }

        const targetModelId = m.model_id && messagesByModel.has(m.model_id) ? m.model_id : paneModelIds[0];
        const list = messagesByModel.get(targetModelId);
        if (list) list.push(mapped);
      });

      const rebuiltPanes: ChatPaneData[] = paneModelIds.map((modelId, index) => ({
        id: `pane-restored-${modelId}-${index}-${Date.now()}`,
        modelId,
        messages: messagesByModel.get(modelId) ?? [],
      }));

      setPanes(rebuiltPanes);
      setIsModelSelectionLocked(true); // Locked if history exists
      setIsThinking(false);
      setError(null);
      
    }).catch(err => {
      console.error(`Failed to load conversation ${activeChatId}`, err);
    })

  }, [activeChatId, models]);

  const handleAddPane = () => {
    if (isModelSelectionLocked || panes.length >= 3) {
      return;
    }

    const newPaneId = `pane-${Date.now()}`;
    const usedModels = new Set(panes.map((pane) => pane.modelId));
    const nextModel =
      models.find((model) => !usedModels.has(model.id))?.id ||
      models[1]?.id ||
      panes[0]?.modelId ||
      "llama-3.3-70b-versatile";

    setPanes((prev) => [
      ...prev,
      { id: newPaneId, modelId: nextModel, messages: [] },
    ]);
  };

  const handleChangeModel = (paneId: string, newModelId: ModelId) => {
    setPanes((prev) => prev.map((p) => (p.id === paneId ? { ...p, modelId: newModelId } : p)));
  };

  const handleRemovePane = (paneId: string) => {
    setPanes((prev) => prev.filter((p) => p.id !== paneId));
  };

  // ── Send message → chat → detect ────────────────────────────────────────
  const handleSendMessage = async (content: string, documentIds: string[] = []) => {
    setIsModelSelectionLocked(true);

    const timestamp = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    setError(null);

    // 1. Append user message instantly to all panes
    setPanes((prev) =>
      prev.map((pane) => ({
        ...pane,
        messages: [
          ...pane.messages,
          { id: `usr-${pane.id}-${Date.now()}`, role: "user" as const, content, timestamp },
        ],
      }))
    );

    setIsThinking(true);
    
    // Save user message to database (using the activeChatId)
    if (activeChatId) {
      try {
        await addMessageToConversation(activeChatId, "user", content);
        console.log(`[ChatContainer] Saved user message to conversation ${activeChatId}`);
        // Notify sidebar to refresh, in case this is the first message that sets the title
        window.dispatchEvent(new Event("refresh-sidebar"));
      } catch (err) {
        console.error("Failed to save user message to DB", err);
      }
    }

    // 2. Fire a real API call for each pane in parallel
    const currentPanes = panes; // snapshot before state updates

    const paneRequests = currentPanes.map(async (pane) => {
      // Build conversation history from existing messages (excluding the just-added user msg)
      const history: ChatMessage[] = pane.messages
        .filter((m) => m.role === "user" || m.role === "assistant")
        .map((m) => ({ role: m.role as "user" | "assistant", content: m.content }));

      const aiMsgId = `ai-${pane.id}-${Date.now()}`;
      const aiTimestamp = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

      // Pre-add empty AI message to pane
      setPanes((prev) =>
        prev.map((p) => {
          if (p.id !== pane.id) return p;
          return {
            ...p,
            messages: [
              ...p.messages,
              {
                id: aiMsgId,
                role: "assistant" as const,
                content: "",
                timestamp: aiTimestamp,
              },
            ],
          };
        })
      );

      try {
        // Step A: stream chat response
        let streamedResponseText = "";
        
        await sendChatMessageStream(pane.modelId, content, history, (chunk) => {
          // Replace \n, etc if data was JSON encoded, but if backend just returned string chunk:
          const unescapedChunk = chunk.replace(/\\n/g, "\n");
          streamedResponseText += unescapedChunk;
          
          setPanes((prev) =>
            prev.map((p) => {
              if (p.id !== pane.id) return p;
              return {
                ...p,
                messages: p.messages.map(m => 
                  m.id === aiMsgId ? { ...m, content: streamedResponseText } : m
                ),
              };
            })
          );
        });

        const responseText = streamedResponseText;

        // Save AI message before detection so backend can link analytics to this message.
        let assistantMessageId: string | undefined;
        if (activeChatId) {
          try {
            const savedAssistant = await addMessageToConversation(activeChatId, "assistant", responseText, pane.modelId);
            assistantMessageId = savedAssistant?.id;
            console.log(`[ChatContainer] Saved AI message (model: ${pane.modelId}) to conversation ${activeChatId}`);
          } catch (err) {
            console.error("Failed to save AI message to DB", err);
          }
        }

        // Step B: run detection on the response
        const historyWithUser: ChatMessage[] = [
          ...history,
          { role: "user", content },
        ];

        let spans: HallucinationSpan[] | undefined;
        try {
          const detection = await detectHallucinations(
            pane.modelId,
            responseText,
            historyWithUser,
            documentIds,
            activeChatId || undefined,
            assistantMessageId,
          );
          spans = detection.claims
            .filter((c) => c.status !== "SKIPPED")
            .map((claim) => ({
              claimId: claim.id,
              text: claim.text,
              exactQuote: claim.exact_quote,
              domain: claim.domain,
              status: claim.status,
              confidence: claim.confidence,
              riskScore: claim.risk_score,
              risk: scoreToRisk(claim.risk_score),
              reasoning: claim.reasoning,
              suggestion: claim.suggestion,
              suggestedSources: claim.suggested_sources,
              note: claim.note,
              citations: claim.citations,
              verificationDetails: {
                entailmentScore: claim.verification_details?.entailment_score,
                contradictionScore: claim.verification_details?.contradiction_score,
                neutralScore: claim.verification_details?.neutral_score,
                sourceCoverage: claim.verification_details?.source_coverage,
                sourceAgreementVariance: claim.verification_details?.source_agreement_variance,
                sourcesChecked: claim.verification_details?.sources_checked ?? [],
                evidence: (claim.verification_details?.evidence ?? []).map((evidence) => ({
                  sourceType: evidence.source_type,
                  sourceTier: evidence.source_tier,
                  sourceUrl: evidence.source_url,
                  sourceTitle: evidence.source_title,
                  documentName: evidence.document_name,
                  chunkIndex: evidence.chunk_index,
                  messageIndex: evidence.message_index,
                  snippet: evidence.snippet,
                  nliLabel: evidence.nli_label,
                  nliScores: evidence.nli_scores ?? undefined,
                })),
              },
            }));

          const detectionSummary = {
            responseId: detection.response_id,
            overallRiskScore: detection.overall_risk_score,
            riskLevel: detection.risk_level,
            riskColor: detection.risk_color,
            warningMessage: detection.warning_message,
            warnings: detection.warnings.map((warning) => ({
              type: warning.type,
              message: warning.message,
              claimId: warning.claim_id,
              sourceUrl: warning.source_url,
            })),
            metadata: detection.metadata
              ? {
                  processingTimeMs: detection.metadata.processing_time_ms,
                  claimsExtracted: detection.metadata.claims_extracted,
                  claimsVerified: detection.metadata.claims_verified,
                  claimsSkipped: detection.metadata.claims_skipped,
                  sourcesQueried: detection.metadata.sources_queried,
                  platform: detection.metadata.platform,
                  conversationId: detection.metadata.conversation_id,
                }
              : undefined,
            resultsPresent: Array.isArray(detection.results) && detection.results.length > 0,
          };
            
          // Update pane with hallucination spans
          setPanes((prev) =>
            prev.map((p) => {
              if (p.id !== pane.id) return p;
              return {
                ...p,
                messages: p.messages.map(m => 
                  m.id === aiMsgId ? { ...m, spans, detectionSummary } : m
                ),
              };
            })
          );
        } catch (detErr) {
          console.warn("Detection failed (non-fatal):", detErr);
        }

        return { paneId: pane.id, success: true };
      } catch (chatErr) {
        const msg = chatErr instanceof Error ? chatErr.message : String(chatErr);
        
        setPanes((prev) =>
            prev.map((p) => {
              if (p.id !== pane.id) return p;
              return {
                ...p,
                messages: p.messages.map(m => 
                  m.id === aiMsgId ? { ...m, content: `⚠️ Error: ${msg}` } : m
                ),
              };
            })
        );
        return { paneId: pane.id, success: false };
      }
    });

    // 3. As each pane resolves, decrement pending loader
    let pendingCount = paneRequests.length;

    paneRequests.forEach((req) => {
      req.then(() => {
        pendingCount -= 1;
        if (pendingCount === 0) setIsThinking(false);
      });
    });
  };

  if (modelsLoading) {
    return (
      <div className="flex-1 flex items-center justify-center text-mut text-sm">
        Connecting to backend…
      </div>
    );
  }

  return (
    <>
      {error && (
        <div className="fixed top-4 left-1/2 -translate-x-1/2 z-50 bg-red-500/10 border border-red-500/30 text-red-500 text-xs px-4 py-2 rounded-full shadow">
          {error}
        </div>
      )}
      <ChatLayout
        panes={panes}
        isThinking={isThinking}
        onSendMessage={handleSendMessage}
        onAddPane={handleAddPane}
        onChangeModel={handleChangeModel}
        onRemovePane={handleRemovePane}
        canAddPane={!isModelSelectionLocked}
        models={models}
      />
    </>
  );
}

