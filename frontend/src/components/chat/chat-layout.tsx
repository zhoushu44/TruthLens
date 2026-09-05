import { ChatInput } from "./chat-input";
import { ChatMessageList } from "./chat-message-list";
import type { ChatPaneData, ModelId } from "./chat-container";
import type { BackendModel } from "@/lib/api";
import { Plus, X, ChevronRight, KeyRound } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ModeToggle } from "@/components/mode-toggle";
import { openProviderSettings } from "@/lib/api";

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";


interface ChatLayoutProps {
  panes: ChatPaneData[];
  isThinking?: boolean;
  onSendMessage: (msg: string, documentIds: string[]) => void;
  onAddPane: () => void;
  onChangeModel: (paneId: string, newModelId: ModelId) => void;
  onRemovePane: (paneId: string) => void;
  canAddPane?: boolean;
  chatTitle?: string;
  models?: BackendModel[];
}

export function ChatLayout({
  panes,
  isThinking,
  onSendMessage,
  onAddPane,
  onChangeModel,
  onRemovePane,
  canAddPane = true,
  chatTitle = "Current Session",
  models = [],
}: ChatLayoutProps) {
  const isCompact = panes.length > 1;

  // Derive a display-friendly label for a model id
  const getModelLabel = (modelId: string) =>
    models.find((m) => m.id === modelId)?.name ?? modelId;

  return (
    <div className="flex-1 flex flex-col h-screen relative bg-transparent p-4 gap-4 overflow-hidden w-full max-w-[1800px] mx-auto">
      
      {/* Top Navigation / Breadcrumbs & Theme Toggle */}
      <div className="flex-shrink-0 flex items-center justify-between z-10 w-full animate-in fade-in slide-in-from-top-4 duration-500 pt-1 px-2">
        <div className="flex items-center gap-2 text-xs font-medium">
          <span className="text-mut hover:text-pri transition-colors cursor-pointer">Sessions</span>
          <ChevronRight className="h-3.5 w-3.5 text-mut/50" />
          <span className="text-pri truncate max-w-[200px] sm:max-w-md">{chatTitle}</span>
        </div>
        
        <div className="flex items-center gap-1 mr-2">
          <Button variant="ghost" size="icon" onClick={openProviderSettings} title="模型与搜索 Key 设置" className="h-8 w-8 rounded-lg text-mut hover:text-pri">
            <KeyRound className="h-4 w-4" />
          </Button>
          <ModeToggle />
        </div>
      </div>

      {/* Dynamic Pane Flexbox Layout */}
      <div className="flex-1 flex gap-4 overflow-hidden min-h-0 w-full z-10">
        {panes.every(p => p.messages.length === 0) ? (
           <div className="flex-1 flex flex-col items-center justify-center p-8 relative rounded-2xl overflow-hidden border border-subtle shadow-xl bg-pane group">
              <div
                className="absolute inset-0 z-0 pointer-events-none opacity-50"
                style={{
                  backgroundImage: "radial-gradient(#2e2e33 1px, transparent 1px)",
                  backgroundSize: "22px 22px",
                }}
              />
              <div
                className="absolute inset-0 z-0 pointer-events-none bg-pane"
                style={{
                  maskImage: "radial-gradient(ellipse 60% 60% at 50% 50%, transparent 40%, black 100%)",
                  WebkitMaskImage: "radial-gradient(ellipse 60% 60% at 50% 50%, transparent 40%, black 100%)",
                }}
              />
              <div className="relative z-10 flex flex-col items-center max-w-2xl text-center space-y-6">
                <h1 className="text-4xl sm:text-5xl font-bold bg-gradient-to-br from-white to-mut bg-clip-text text-transparent tracking-tight">
                  Hallucination Detection
                </h1>
                <p className="text-base sm:text-lg text-mut font-light">
                  Start a conversation to analyze model outputs in parallel. Select models below or just type your first prompt to begin.
                </p>
                <div className="flex flex-wrap items-center justify-center gap-3 mt-4">
                  {panes.map((pane) => (
                    <div key={pane.id} className="flex relative items-center bg-msg-user border border-strong rounded-xl px-2 py-1 shadow-sm">
                      <Select
                        value={pane.modelId}
                        onValueChange={(val) => onChangeModel(pane.id, val as ModelId)}
                      >
                        <SelectTrigger className="w-[180px] h-9 bg-transparent border-none text-xs font-semibold focus:ring-0 cursor-pointer shadow-none">
                          <SelectValue placeholder="Select Model">
                            {getModelLabel(pane.modelId)}
                          </SelectValue>
                        </SelectTrigger>
                        <SelectContent className="bg-tooltip border-strong text-pri">
                          {models.map((m) => (
                            <SelectItem key={m.id} value={m.id} className="text-xs hover:bg-hover focus:bg-hover">
                              <span className="font-medium mr-2">{m.name}</span>
                              <span className="text-[10px] text-mut uppercase">{m.provider}</span>
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                      {panes.length > 1 && (
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-6 w-6 rounded-md hover:bg-hover text-mut hover:text-pri ml-1 snap-end"
                          onClick={() => onRemovePane(pane.id)}
                        >
                          <X className="h-4 w-4" />
                        </Button>
                      )}
                    </div>
                  ))}
                  {canAddPane && panes.length < 3 && (
                    <Button
                      variant="outline"
                      className="h-11 rounded-xl border-dashed border-strong bg-transparent hover:bg-hover text-sec transition-all gap-2 px-4 shadow-none"
                      onClick={onAddPane}
                    >
                      <Plus className="h-4 w-4" />
                      <span className="text-xs">Add Model</span>
                    </Button>
                  )}
                </div>
              </div>
           </div>
        ) : (
        panes.map((pane) => (
          <div
            key={pane.id}
            className="flex-1 flex flex-col relative h-full bg-pane rounded-2xl border border-subtle shadow-[0_2px_12px_rgba(120,90,60,0.14)] dark:shadow-[0_8px_30px_rgba(0,0,0,0.5)] overflow-hidden min-h-0 transition-all duration-300"
          >
            {/* Aceternity Dot Background */}
            <div
              className="absolute inset-0 z-0 pointer-events-none"
              style={{
                backgroundImage: "radial-gradient(#2e2e33 1px, transparent 1px)",
                backgroundSize: "22px 22px",
              }}
            />
            {/* Radial fade — edges melt into pane bg */}
            <div
              className="absolute inset-0 z-0 pointer-events-none bg-pane"
              style={{
                maskImage: "radial-gradient(ellipse 80% 80% at 50% 50%, transparent 55%, black 100%)",
                WebkitMaskImage: "radial-gradient(ellipse 80% 80% at 50% 50%, transparent 55%, black 100%)",
              }}
            />

            {/* Header / Toolbar */}
            <div className="relative z-10 flex flex-col sm:flex-row sm:items-center justify-between p-3 border-b border-subtle bg-msg-ai bg-opacity-90 backdrop-blur-md shrink-0 gap-2">
              <Select
                value={pane.modelId}
                onValueChange={(val) => onChangeModel(pane.id, val as ModelId)}
              >
                <SelectTrigger className="w-[200px] h-8 bg-msg-user border-strong text-xs font-semibold focus:ring-1 focus:ring-strong">
                  <SelectValue placeholder="Select Model">
                    {getModelLabel(pane.modelId)}
                  </SelectValue>
                </SelectTrigger>
                <SelectContent className="bg-tooltip border-strong text-pri">
                  {models.map((m) => (
                    <SelectItem key={m.id} value={m.id} className="text-xs hover:bg-hover focus:bg-hover">
                      <span className="font-medium mr-2">{m.name}</span>
                      <span className="text-[10px] text-mut uppercase">{m.provider}</span>
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>

              {/* Close Panel Button */}
              {isCompact && (
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-6 w-6 rounded-md hover:bg-hover text-mut hover:text-pri"
                  onClick={() => onRemovePane(pane.id)}
                >
                  <X className="h-4 w-4" />
                </Button>
              )}
            </div>

            {/* Scrollable Message Content */}
            <div className="flex-1 overflow-hidden min-h-0 relative z-10">
              <ChatMessageList messages={pane.messages} compactMode={isCompact} />
            </div>
          </div>
        )))}

        {!(panes.every(p => p.messages.length === 0)) && canAddPane && panes.length < 3 && (
          <div className="h-full flex-shrink-0 w-12 sm:w-16 flex items-center justify-center">
            <Button
              variant="outline"
              size="icon"
              className="h-full border-t border-b border-l-0 border-r-0 sm:border rounded-none sm:rounded-xl border-dashed border-strong bg-transparent hover:bg-hover text-sec transition-all"
              onClick={onAddPane}
              title="Add Model for Comparison"
            >
              <Plus className="h-6 w-6" />
            </Button>
          </div>
        )}
      </div>

      {/* Floating Sticky Bottom Input */}
      <div className="flex-shrink-0 w-full z-20 pt-4 pb-6 px-4">
        <ChatInput onSendMessage={onSendMessage} isLoading={isThinking} />
        <div className="mt-2 text-center text-[10px] text-mut uppercase tracking-wider font-light w-full">
          Answers are generated autonomously. Verify critical diagnostics.
        </div>
      </div>
    </div>
  );
}



