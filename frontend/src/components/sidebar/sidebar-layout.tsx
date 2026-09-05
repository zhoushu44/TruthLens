import { Plus, Eye, BookKey, BarChart2, Trash2, KeyRound, BookOpen } from "lucide-react";
import {
  Sidebar,
  SidebarContent,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuItem,
  SidebarMenuButton,
  SidebarFooter,
} from "@/components/animate-ui/components/radix/sidebar";
import { Separator } from "@/components/ui/separator";
import type { RiskLevel } from "./sidebar-item";
import { SidebarItem } from "./sidebar-item";
import { openProviderSettings } from "@/lib/api";

interface Chat {
  id: string;
  title: string;
  snippet?: string;
  riskLevel?: RiskLevel;
}

interface SidebarLayoutProps {
  chats: Chat[];
  activeChatId?: string;
  onNewChat: () => void;
  onSelectChat: (id: string) => void;
  onDeleteChat: (id: string) => void;
  onOpenKnowledgeBase: () => void;
}

export function SidebarLayout({
  chats,
  activeChatId,
  onNewChat,
  onSelectChat,
  onDeleteChat,
  onOpenKnowledgeBase,
}: SidebarLayoutProps) {
  return (
    <Sidebar variant="sidebar" className="border-r border-subtle bg-pane text-pri font-sans">
      <SidebarHeader className="p-4 flex flex-col gap-5">
        {/* App Branding (TruthLens) */}
        <div className="flex items-center gap-3 px-1">
          <div className="flex items-center justify-center w-8 h-8 rounded-[8px] bg-msg-user shadow-sm ring-1 ring-strong">
            <Eye className="w-5 h-5 text-pri" />
          </div>
          <span className="font-bold text-pri text-[17px] tracking-tight">TruthLens</span>
        </div>

        {/* Action Buttons */}
        <SidebarMenu className="gap-1">
          <SidebarMenuItem>
            <SidebarMenuButton size="default" onClick={onNewChat} className="text-pri font-medium hover:bg-hover active:scale-[0.98] transition-all">
              <Plus className="w-4 h-4 mr-0.5" />
              <span>New Session</span>
            </SidebarMenuButton>
          </SidebarMenuItem>
          <SidebarMenuItem>
            <SidebarMenuButton 
              size="default" 
              onClick={() => onSelectChat("analytics")}
              isActive={activeChatId === "analytics"}
              className="text-sec hover:text-pri hover:bg-hover data-[active=true]:bg-hover data-[active=true]:text-amber-500 font-medium active:scale-[0.98] transition-all"
            >
              <BarChart2 className="w-4 h-4 mr-0.5" />
              <span>Analytics</span>
              </SidebarMenuButton>
            </SidebarMenuItem>
            <SidebarMenuItem>
              <SidebarMenuButton
                size="default"
                onClick={() => onSelectChat("apikeys")}
                isActive={activeChatId === "apikeys"}
                className="text-sec hover:text-pri hover:bg-hover data-[active=true]:bg-hover data-[active=true]:text-amber-500 font-medium active:scale-[0.98] transition-all"
              >
                <KeyRound className="w-4 h-4 mr-0.5" />
                <span>API Key</span>
              </SidebarMenuButton>
            </SidebarMenuItem>
            <SidebarMenuItem>
              <SidebarMenuButton
                size="default"
                onClick={() => onSelectChat("apidocs")}
                isActive={activeChatId === "apidocs"}
                className="text-sec hover:text-pri hover:bg-hover data-[active=true]:bg-hover data-[active=true]:text-amber-500 font-medium active:scale-[0.98] transition-all"
              >
                <BookOpen className="w-4 h-4 mr-0.5" />
                <span>API 文档</span>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
        
        <Separator className="bg-border-subtle mt-1 mx-2 w-auto" />
      </SidebarHeader>

      <SidebarContent className="px-2">
        <SidebarGroup>
          <SidebarGroupLabel className="text-mut uppercase tracking-widest text-[11px] mb-1">
            Recent Sessions
          </SidebarGroupLabel>
          <SidebarGroupContent>
            {chats.length === 0 ? (
              <div className="text-center py-6 text-[13px] text-mut font-medium">
                No recent sessions.
              </div>
            ) : (
              <SidebarMenu>
                {chats.map((chat) => (
                  <SidebarMenuItem key={chat.id}>
                    <div className="group flex items-start gap-1 rounded-lg hover:bg-hover data-[active=true]:bg-hover transition-all px-1">
                      <SidebarMenuButton
                        asChild
                        isActive={chat.id === activeChatId}
                        onClick={() => onSelectChat(chat.id)}
                        className="px-2.5 py-2.5 h-auto flex-1 hover:bg-transparent data-[active=true]:bg-transparent shadow-none rounded-lg transition-all"
                      >
                        <SidebarItem
                          title={chat.title}
                          snippet={chat.snippet}
                          riskLevel={chat.riskLevel}
                        />
                      </SidebarMenuButton>

                      <button
                        type="button"
                        title="Delete session"
                        className="mt-2 mr-1 opacity-0 group-hover:opacity-100 transition-opacity text-mut hover:text-red-500"
                        onClick={(e) => {
                          e.preventDefault();
                          e.stopPropagation();
                          onDeleteChat(chat.id);
                        }}
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  </SidebarMenuItem>
                ))}
              </SidebarMenu>
            )}
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>

      <SidebarFooter className="p-3">
        <Separator className="bg-border-subtle mb-2 mx-auto" />
        <SidebarMenu className="flex-row justify-center w-full mt-2">
          <SidebarMenuItem>
            <SidebarMenuButton
                onClick={onOpenKnowledgeBase}
                title="Knowledge Base"
                className="w-10 h-10 flex justify-center items-center mx-auto text-mut hover:text-pri"
              >
                <BookKey className="w-[16px] h-[16px]" />
              </SidebarMenuButton>
            </SidebarMenuItem>
          <SidebarMenuItem>
            <SidebarMenuButton
                onClick={openProviderSettings}
                title="模型与搜索 Key 设置"
                className="w-10 h-10 flex justify-center items-center mx-auto text-mut hover:text-pri"
              >
                <KeyRound className="w-[16px] h-[16px]" />
              </SidebarMenuButton>
            </SidebarMenuItem>
        </SidebarMenu>
      </SidebarFooter>
    </Sidebar>
  );
}




