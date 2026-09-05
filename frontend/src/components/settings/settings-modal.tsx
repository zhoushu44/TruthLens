import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Tooltip, TooltipTrigger, TooltipContent, TooltipProvider } from "@/components/ui/tooltip";
import { KeyRound, Eye, EyeOff, CheckCircle2, CircleDashed, Loader2, Zap, HelpCircle, Trash2, Undo2 } from "lucide-react";
import {
  fetchProviderSettings,
  saveProviderSettings,
  testProviderKey,
  type ProviderSettingItem,
  type EffectiveSummary,
  type ProviderTestResult,
} from "@/lib/api";

function RequiredBadge({ level, text }: { level: string; text: string }) {
  const variant =
    level === "group-required" ? "destructive" : level === "recommended" ? "default" : "secondary";
  return (
    <Badge variant={variant} className="text-[10px]">
      {text}
    </Badge>
  );
}

interface FieldGroup {
  id: string;
  title: string;
  keys: string[];
  required: string;
  required_level: string;
  description: string;
  /** 为 true 时标题后追加该组自定义模型名（如 MiMo 显示 mimo-v2.5） */
  showModelInTitle?: string;
  /** 多字段成组时每行的行标签；单字段组不传则不显示行标签 */
  fieldLabels?: Record<string, string>;
}

// 一个模型 = 一个框。MiMo 的 Key/网关地址/模型名收进同一个框。
const SECTIONS: { title: string; groups: FieldGroup[] }[] = [
  {
    title: "对话与声明提取",
    groups: [
      { id: "groq", title: "Groq", keys: ["GROQ_API_KEY"], required: "三选一必填", required_level: "group-required", description: "聊天（gpt-oss 120B / 20B）+ 声明提取默认通道，速度最快优先使用。不填则 Groq 模型不可用，声明提取自动降级到 NVIDIA / OpenRouter。" },
      { id: "nvidia", title: "NVIDIA NIM", keys: ["NVIDIA_API_KEY"], required: "三选一必填", required_level: "group-required", description: "聊天（Llama 3.1 70B / Mistral 7B / Gemma 2 9B）+ 声明提取备用通道。Groq 不可用时自动切换。" },
      { id: "openrouter", title: "OpenRouter", keys: ["OPENROUTER_API_KEY"], required: "三选一必填", required_level: "group-required", description: "聊天（Llama 3.3 70B 免费版）+ 声明提取兜底。三者至少填一个，否则只能走启发式兜底。" },
    ],
  },
  {
    title: "裁决（终判）",
    groups: [
      { id: "gemini", title: "Gemini", keys: ["GEMINI_API_KEY"], required: "二选一推荐", required_level: "recommended", description: "最终裁决首选。对每条声明输出 VERIFIED / CONTRADICTED 等结论 + 中文理由。不填则用 MiMo 通道，两者都不填降级为 NLI 分数启发式。" },
      {
        id: "mimo", title: "自定义模型", keys: ["ZEN_API_KEY", "ZEN_BASE_URL", "ZEN_MODEL"],
        required: "二选一推荐", required_level: "recommended",
        description: "最终裁决备用通道，也可直接聊天。模型名可自定义，保存后聊天模型下拉里即显示该名字。测试 Key 会连网关模型列表。",
        showModelInTitle: "ZEN_MODEL",
        fieldLabels: { ZEN_API_KEY: "Key", ZEN_BASE_URL: "网关地址", ZEN_MODEL: "模型名" },
      },
    ],
  },
  {
    title: "联网搜索验证",
    groups: [
      { id: "tavily", title: "Tavily 搜索", keys: ["TAVILY_API_KEY"], required: "推荐必填", required_level: "recommended", description: "联网验证主力，返回整页内容最适合 NLI 判定。不填则通用事实容易判为 UNVERIFIED。测试为最小真实请求，会消耗 1 次查询额度。" },
      { id: "serper", title: "Serper 搜索", keys: ["SERPER_API_KEY"], required: "选填", required_level: "optional", description: "Google 搜索聚合，用于域名限定检索。不填则走 Tavily 单通道。测试为最小真实请求，会消耗 1 次查询额度。" },
      { id: "factcheck", title: "Google FactCheck", keys: ["GOOGLE_FACTCHECK_API_KEY"], required: "选填", required_level: "optional", description: "Google 事实核查库补充，命中时可信度高但覆盖小。不填不影响主流程。" },
    ],
  },
  {
    title: "模型微调",
    groups: [
      { id: "nli-model", title: "NLI 判定模型", keys: ["NLI_GROQ_MODEL"], required: "选填", required_level: "optional", description: "NLI 判定（Groq）用的模型名，保存后判定链路即时生效，无需重启。" },
      { id: "extract-model", title: "声明提取模型", keys: ["CLAIM_EXTRACTION_MODEL"], required: "选填", required_level: "optional", description: "覆盖当前提取通道的模型名，留空用各通道默认。保存后即时生效。" },
    ],
  },
];

export function ProviderSettingsModal() {
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<ProviderSettingItem[]>([]);
  const [effective, setEffective] = useState<EffectiveSummary | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedMsg, setSavedMsg] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [cleared, setCleared] = useState<Record<string, boolean>>({});
  const [showSecret, setShowSecret] = useState<Record<string, boolean>>({});
  const [testingKey, setTestingKey] = useState<string | null>(null);
  const [testResults, setTestResults] = useState<Record<string, ProviderTestResult>>({});

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchProviderSettings();
      setItems(data.items);
      setEffective(data.effective);
      setDrafts({});
      setCleared({});
      setTestResults({});
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败，请确认后端已启动");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const handler = () => {
      setOpen(true);
      setSavedMsg(null);
      void load();
    };
    window.addEventListener("open-provider-settings", handler);
    return () => window.removeEventListener("open-provider-settings", handler);
  }, [load]);

  const byKey = useMemo(() => Object.fromEntries(items.map((it) => [it.key, it])), [items]);

  const dirtyCount = Object.keys(drafts).filter((k) => drafts[k] !== "" || cleared[k]).length;

  const handleTest = async (it: ProviderSettingItem) => {
    if (cleared[it.key]) return;
    const draft = (drafts[it.key] ?? "").trim();
    if (!draft && !it.configured) {
      setTestResults((p) => ({
        ...p,
        [it.key]: { ok: false, detail: "请先在输入框填入 Key 再测试", latency_ms: 0, tested_with: "none" },
      }));
      return;
    }
    setTestingKey(it.key);
    try {
      // 有草稿测草稿（不保存），没草稿测已保存值
      const res = await testProviderKey(it.key, draft || undefined);
      setTestResults((p) => ({ ...p, [it.key]: res }));
    } catch (e) {
      setTestResults((p) => ({
        ...p,
        [it.key]: { ok: false, detail: e instanceof Error ? e.message : "测试失败", latency_ms: 0, tested_with: "none" },
      }));
    } finally {
      setTestingKey(null);
    }
  };

  const handleSave = async () => {
    const values: Record<string, string> = {};
    for (const k of Object.keys(drafts)) {
      if (cleared[k]) values[k] = "";
      else if (drafts[k] !== "") values[k] = drafts[k];
    }
    if (Object.keys(values).length === 0) return;
    setSaving(true);
    setError(null);
    setSavedMsg(null);
    try {
      const res = await saveProviderSettings(values);
      setEffective(res.effective);
      setSavedMsg(`已保存 ${res.updated.length} 项并自动生效，无需重启后端。`);
      setDrafts({});
      setCleared({});
      const data = await fetchProviderSettings();
      setItems(data.items);
      setEffective(data.effective);
      try {
        window.dispatchEvent(new CustomEvent("provider-settings-saved"));
      } catch {}
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent className="sm:max-w-[840px] max-h-[85vh] overflow-y-auto bg-pane text-foreground border-border-subtle font-sans">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-xl font-semibold">
            <KeyRound className="w-5 h-5 text-pri" /> 模型与搜索 Key 设置
          </DialogTitle>
          <DialogDescription className="text-sec">
            填入后点保存即自动生效，无需重启后端。Key 只显示脱敏状态（****后4位），明文不会回传；输入框留空表示不改该项。点「测试」可先验证 Key 是否有效（有草稿测草稿，否则测已保存值）。
          </DialogDescription>
        </DialogHeader>
        <TooltipProvider delay={200}>

        {/* 生效总览 */}
        {effective && (
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mt-2 text-xs">
            <div className="rounded-lg border border-border-subtle bg-hover/40 px-3 py-2">
              <div className="text-mut">声明提取</div>
              <div className={`font-semibold ${effective.claim_ready ? "text-green-500" : "text-red-500"}`}>
                {effective.claim_ready ? "● 可用" : "● 不可用"}
              </div>
              <div className="text-mut truncate" title={effective.claim_channel}>{effective.claim_channel}</div>
            </div>
            <div className="rounded-lg border border-border-subtle bg-hover/40 px-3 py-2">
              <div className="text-mut">最终裁决</div>
              <div className="font-semibold text-pri truncate" title={effective.adjudicator}>{effective.adjudicator}</div>
              <div className="text-mut">gemini / zen / 启发式</div>
            </div>
            <div className="rounded-lg border border-border-subtle bg-hover/40 px-3 py-2">
              <div className="text-mut">可聊天模型</div>
              <div className="font-semibold text-pri">{effective.chat_models_count} 个</div>
              <div className="text-mut truncate" title={effective.chat_models.join(", ")}>保存后模型下拉自动更新</div>
            </div>
            <div className="rounded-lg border border-border-subtle bg-hover/40 px-3 py-2">
              <div className="text-mut">联网搜索</div>
              <div className={`font-semibold ${effective.web_search ? "text-green-500" : "text-mut"}`}>
                {effective.web_search ? `● ${effective.web_providers.join(" + ")}` : "● 未配置"}
              </div>
              <div className="text-mut">tavily / serper / factcheck</div>
            </div>
          </div>
        )}

        {loading && (
          <div className="flex items-center gap-2 text-sm text-mut py-6 justify-center">
            <Loader2 className="w-4 h-4 animate-spin" /> 正在读取后端配置…
          </div>
        )}
        {error && (
          <div className="text-xs text-red-500 bg-red-500/10 border border-red-500/20 rounded-md px-3 py-2 mt-2">
            {error}
          </div>
        )}
        {savedMsg && (
          <div className="text-xs text-green-600 bg-green-500/10 border border-green-500/20 rounded-md px-3 py-2 mt-2">
            {savedMsg}
          </div>
        )}

        {!loading &&
          SECTIONS.map((sec) => (
            <div key={sec.title} className="mt-3">
              <h4 className="font-semibold text-sec text-sm mb-1.5 uppercase tracking-wide">{sec.title}</h4>
              <div className="flex flex-col gap-2">
                {sec.groups.map((g) => {
                  const fields = g.keys
                    .map((k) => byKey[k])
                    .filter((x): x is ProviderSettingItem => Boolean(x));
                  if (fields.length === 0) return null;
                  // 自定义模型名显示在标题上：草稿优先，其次已保存值
                  const modelDraft = g.showModelInTitle ? (drafts[g.showModelInTitle] ?? "").trim() : "";
                  const modelSaved = g.showModelInTitle ? (byKey[g.showModelInTitle]?.value ?? "") : "";
                  const modelName = modelDraft || modelSaved;
                  const title = modelName ? `${g.title} · ${modelName}` : g.title;
                  const statusField = fields.find((f) => f.secret) ?? fields[0];
                  return (
                    <div key={g.id} className="rounded-lg border border-border-subtle bg-hover/30 px-3 py-2">
                      {/* 框头：一个模型一行 */}
                      <div className="flex items-center gap-1.5 flex-wrap">
                        <span className="font-medium text-pri text-sm">{title}</span>
                        <Tooltip>
                          <TooltipTrigger className="text-mut hover:text-pri shrink-0 cursor-help">
                            <HelpCircle className="w-3.5 h-3.5" />
                          </TooltipTrigger>
                          <TooltipContent side="top" className="max-w-xs whitespace-normal text-left leading-relaxed">
                            {g.description}
                          </TooltipContent>
                        </Tooltip>
                        <RequiredBadge level={g.required_level} text={g.required} />
                        {fields.some((f) => f.configured) ? (
                          <span className="inline-flex items-center gap-1 text-[11px] text-green-500">
                            <CheckCircle2 className="w-3 h-3" /> 已配置{statusField.secret && statusField.masked ? ` ${statusField.masked}` : ""}
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 text-[11px] text-mut">
                            <CircleDashed className="w-3 h-3" /> 未配置
                          </span>
                        )}
                      </div>
                      {/* 框内字段 */}
                      <div className="flex flex-col gap-1.5 mt-1.5">
                        {fields.map((it) => {
                          const draft = drafts[it.key] ?? "";
                          const isCleared = !!cleared[it.key];
                          const fieldLabel = g.fieldLabels?.[it.key];
                          return (
                            <div key={it.key}>
                              <div className="flex items-center gap-2">
                                {fieldLabel && (
                                  <span className="w-14 shrink-0 text-xs text-sec">{fieldLabel}</span>
                                )}
                                <div className="relative flex-1 min-w-0">
                            <Input
                              type={it.secret && !showSecret[it.key] ? "password" : "text"}
                              placeholder={isCleared ? "将清空该 Key" : it.secret ? `留空=不改（${it.placeholder}）` : it.placeholder}
                              value={isCleared ? "" : draft}
                              disabled={isCleared}
                              onChange={(e) => {
                                setDrafts((p) => ({ ...p, [it.key]: e.target.value }));
                                setTestResults((p) => {
                                  if (!(it.key in p)) return p;
                                  const n = { ...p };
                                  delete n[it.key];
                                  return n;
                                });
                              }}
                              className="pr-9"
                            />
                            {it.secret && (
                              <button
                                type="button"
                                className="absolute right-2 top-1/2 -translate-y-1/2 text-mut hover:text-pri"
                                onClick={() => setShowSecret((p) => ({ ...p, [it.key]: !p[it.key] }))}
                                title={showSecret[it.key] ? "隐藏" : "显示"}
                              >
                                {showSecret[it.key] ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                              </button>
                            )}
                          </div>
                          {(draft !== "" || isCleared) && (
                            <Tooltip>
                              <TooltipTrigger
                                render={<Button variant="ghost" size="icon" title="撤销改动" />}
                                onClick={() => {
                                  setDrafts((p) => {
                                    const n = { ...p };
                                    delete n[it.key];
                                    return n;
                                  });
                                  setCleared((p) => {
                                    const n = { ...p };
                                    delete n[it.key];
                                    return n;
                                  });
                                }}
                              >
                                <Undo2 className="w-4 h-4" />
                              </TooltipTrigger>
                              <TooltipContent side="top">撤销改动</TooltipContent>
                            </Tooltip>
                          )}
                          {it.configured && !isCleared && (
                            <Tooltip>
                              <TooltipTrigger
                                render={<Button variant="ghost" size="icon" className="text-red-500 hover:text-red-600" title="清空该 Key" />}
                                onClick={() => {
                                  setCleared((p) => ({ ...p, [it.key]: true }));
                                  setDrafts((p) => ({ ...p, [it.key]: "" }));
                                }}
                              >
                                <Trash2 className="w-4 h-4" />
                              </TooltipTrigger>
                              <TooltipContent side="top">清空该 Key（点保存后生效）</TooltipContent>
                            </Tooltip>
                          )}
                          {!isCleared && (
                            <Tooltip>
                              <TooltipTrigger
                                render={
                                  <Button
                                    variant="outline"
                                    size="icon"
                                    disabled={testingKey === it.key}
                                    title={
                                      draft
                                        ? "测试输入框中的值（不保存）"
                                        : it.configured
                                          ? "测试已保存的值"
                                          : "请先输入 Key"
                                    }
                                  />
                                }
                                onClick={() => void handleTest(it)}
                              >
                                {testingKey === it.key ? (
                                  <Loader2 className="w-4 h-4 animate-spin" />
                                ) : (
                                  <Zap className="w-4 h-4" />
                                )}
                              </TooltipTrigger>
                              <TooltipContent side="top">
                                {draft
                                  ? "测试输入框中的值（不保存）"
                                  : it.configured
                                    ? "测试已保存的值"
                                    : "请先输入 Key"}
                              </TooltipContent>
                            </Tooltip>
                          )}
                        </div>
                        {testResults[it.key] && (
                          <p
                            className={`text-xs mt-1.5 leading-relaxed ${
                              testResults[it.key].ok ? "text-green-500" : "text-red-500"
                            }`}
                          >
                            {testResults[it.key].ok ? "✅" : "❌"} {testResults[it.key].detail}
                            {testResults[it.key].latency_ms > 0 && `（${testResults[it.key].latency_ms}ms，测的是${testResults[it.key].tested_with === "draft" ? "输入框草稿" : "已保存值"}）`}
                          </p>
                        )}
                      </div>
                    );
                  })}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          ))}

        </TooltipProvider>

        <div className="flex items-center justify-between gap-2 mt-5 sticky bottom-0 bg-pane py-2">
          <span className="text-xs text-mut">
            {dirtyCount > 0 ? `待保存 ${dirtyCount} 项` : "无改动"}
          </span>
          <div className="flex gap-2">
            <Button variant="outline" onClick={() => setOpen(false)}>
              关闭
            </Button>
            <Button onClick={handleSave} disabled={saving || dirtyCount === 0}>
              {saving ? "保存中…" : "保存并生效"}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
