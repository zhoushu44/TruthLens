import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, PieChart, Pie, Cell } from "recharts";
import { KeyRound, Server } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  fetchAnalyticsOverview,
  type AnalyticsApiKeyStat,
  type AnalyticsModelStat,
  type AnalyticsOverview,
  type AnalyticsProviderStat,
} from "@/lib/api";

const COLORS = ["#0088FE", "#00C49F", "#FFBB28", "#FF8042"];

const EMPTY_SUMMARY = {
  total_analyses: 0,
  total_claims: 0,
  total_hallucinations: 0,
  average_confidence: 0,
};

const WEEKDAY_CN: Record<string, string> = {
  Mon: "周一",
  Tue: "周二",
  Wed: "周三",
  Thu: "周四",
  Fri: "周五",
  Sat: "周六",
  Sun: "周日",
};

function formatPercent(value: number): string {
  return `${(Math.max(0, Math.min(1, value)) * 100).toFixed(1)}%`;
}

function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "从未调用";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("zh-CN");
}

// 上游服务展示顺序与中文名（0 次也展示，方便确认 Key 是否生效）
const PROVIDER_META: Array<{ id: string; label: string; desc: string }> = [
  { id: "groq", label: "Groq", desc: "声明抽取 / 对话模型" },
  { id: "nvidia", label: "NVIDIA NIM", desc: "声明抽取 / 对话模型" },
  { id: "openrouter", label: "OpenRouter", desc: "声明抽取 / 对话模型" },
  { id: "zen", label: "Zen", desc: "Zen 网关 · 裁决 / 对话模型" },
  { id: "gemini", label: "Gemini", desc: "裁决模型" },
  { id: "tavily", label: "Tavily", desc: "联网搜索验证" },
  { id: "serper", label: "Serper", desc: "Google 搜索验证" },
  { id: "factcheck", label: "FactCheck", desc: "事实核查 API" },
];

export function AnalyticsPage() {
  const [analytics, setAnalytics] = useState<AnalyticsOverview | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [days, setDays] = useState(7);

  useEffect(() => {
    let isCancelled = false;

    const loadAnalytics = async () => {
      setIsLoading(true);
      setError(null);
      try {
        const data = await fetchAnalyticsOverview(days);
        if (!isCancelled) {
          setAnalytics(data);
        }
      } catch (loadError) {
        if (!isCancelled) {
          setError(loadError instanceof Error ? loadError.message : "数据加载失败，请稍后重试。");
          setAnalytics(null);
        }
      } finally {
        if (!isCancelled) {
          setIsLoading(false);
        }
      }
    };

    void loadAnalytics();
    return () => {
      isCancelled = true;
    };
  }, [days]);

  const summary = analytics?.summary ?? EMPTY_SUMMARY;
  const modelStats: AnalyticsModelStat[] = analytics?.models ?? [];
  const apiKeys: AnalyticsApiKeyStat[] = analytics?.api_keys ?? [];
  const totalKeyCalls = apiKeys.reduce((sum, k) => sum + (k.usage_count || 0), 0);
  const providerMap = new Map<string, AnalyticsProviderStat>(
    (analytics?.providers ?? []).map((p) => [p.id, p]),
  );
  const totalProviderCalls = PROVIDER_META.reduce(
    (sum, m) => sum + (providerMap.get(m.id)?.calls || 0),
    0,
  );
  const timeData = (analytics?.timeline ?? []).map((point) => ({
    name: WEEKDAY_CN[point.label] ?? point.label,
    幻觉数: point.hallucinations,
    hallucinations: point.hallucinations,
    confidence: point.confidence,
  }));

  return (
    <div className="relative flex flex-col h-full w-full bg-app overflow-y-auto overflow-x-hidden text-pri p-4 sm:p-8">
      {/* Grid Background */}
      <div className="absolute inset-0 pointer-events-none z-0">
        <div
          className="absolute inset-0 opacity-[0.05] dark:opacity-[0.1]"
          style={{
            backgroundImage: `linear-gradient(to right, #808080 1px, transparent 1px), linear-gradient(to bottom, #808080 1px, transparent 1px)`,
            backgroundSize: `40px 40px`
          }}
        />
        <div className="absolute inset-0 bg-app mask-[radial-gradient(ellipse_at_center,transparent_20%,black)]"></div>
      </div>

      <div className="relative z-10 w-full max-w-7xl mx-auto flex flex-col gap-8">
        <motion.div initial={{ opacity: 0, y: -20 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.6 }}>
          <div className="flex flex-wrap items-end justify-between gap-4">
            <div>
              <h1 className="text-3xl sm:text-4xl font-bold tracking-tight text-pri">数据分析仪表盘</h1>
              <p className="text-mut mt-2">监控各模型的检测量、幻觉率与置信度变化，掌握每个接入 Key 的调用情况。</p>
              {analytics?.generated_at && (
                <p className="text-xs text-mut mt-1">数据更新时间：{new Date(analytics.generated_at).toLocaleString("zh-CN")}</p>
              )}
              {isLoading && <p className="text-xs text-amber-500 mt-2">正在加载实时数据…</p>}
              {error && <p className="text-xs text-red-500 mt-2">{error}</p>}
            </div>
            <div className="flex gap-2">
              {[7, 14, 30].map((d) => (
                <button
                  key={d}
                  onClick={() => setDays(d)}
                  className={`px-3 py-1.5 text-xs rounded-full border transition-colors ${
                    days === d
                      ? "bg-amber-500 text-white border-amber-500"
                      : "bg-pane/80 text-mut border-subtle hover:text-pri"
                  }`}
                >
                  近 {d} 天
                </button>
              ))}
            </div>
          </div>
        </motion.div>

        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
          <Card className="bg-pane/80 backdrop-blur-md border-subtle">
            <CardHeader className="pb-2">
              <CardDescription>总检测次数</CardDescription>
              <CardTitle className="text-2xl">{summary.total_analyses}</CardTitle>
            </CardHeader>
          </Card>
          <Card className="bg-pane/80 backdrop-blur-md border-subtle">
            <CardHeader className="pb-2">
              <CardDescription>声明总数</CardDescription>
              <CardTitle className="text-2xl">{summary.total_claims}</CardTitle>
            </CardHeader>
          </Card>
          <Card className="bg-pane/80 backdrop-blur-md border-subtle">
            <CardHeader className="pb-2">
              <CardDescription>幻觉标记数</CardDescription>
              <CardTitle className="text-2xl text-red-500">{summary.total_hallucinations}</CardTitle>
            </CardHeader>
          </Card>
          <Card className="bg-pane/80 backdrop-blur-md border-subtle">
            <CardHeader className="pb-2">
              <CardDescription>平均置信度</CardDescription>
              <CardTitle className="text-2xl">{formatPercent(summary.average_confidence)}</CardTitle>
            </CardHeader>
          </Card>
        </div>

        {/* Top Charts Grid */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">

          {/* Main Chart: Hallucinations Over Time */}
          <motion.div
             className="md:col-span-2 rounded-xl"
             initial={{ opacity: 0, scale: 0.95 }}
             animate={{ opacity: 1, scale: 1 }}
             transition={{ duration: 0.5, delay: 0.1 }}
          >
            <Card className="h-full bg-pane/80 backdrop-blur-md border-subtle shadow-xl shadow-black/5 dark:shadow-black/20">
              <CardHeader>
                <CardTitle>幻觉数量趋势</CardTitle>
                <CardDescription>每日检出的无依据声明数量</CardDescription>
              </CardHeader>
              <CardContent className="h-75">
                {timeData.length === 0 ? (
                  <div className="h-full w-full flex items-center justify-center text-sm text-mut">
                    暂无趋势数据
                  </div>
                ) : (
                  <ResponsiveContainer width="100%" height="100%">
                    <AreaChart data={timeData} margin={{ top: 10, right: 30, left: -20, bottom: 0 }}>
                      <defs>
                        <linearGradient id="colorHal" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="5%" stopColor="#ef4444" stopOpacity={0.3}/>
                          <stop offset="95%" stopColor="#ef4444" stopOpacity={0}/>
                        </linearGradient>
                      </defs>
                      <XAxis dataKey="name" stroke="#888888" fontSize={12} tickLine={false} axisLine={false} />
                      <YAxis stroke="#888888" fontSize={12} tickLine={false} axisLine={false} allowDecimals={false} />
                      <Tooltip
                        contentStyle={{ backgroundColor: "var(--pane)", borderColor: "var(--border-subtle)", borderRadius: "8px" }}
                        itemStyle={{ color: "var(--pri)" }}
                        labelStyle={{ color: "var(--pri)" }}
                        formatter={(value) => [`${value} 条`, "幻觉数"]}
                        labelFormatter={(label) => `${label}`}
                      />
                      <Area type="monotone" dataKey="hallucinations" name="幻觉数" stroke="#ef4444" fillOpacity={1} fill="url(#colorHal)" />
                    </AreaChart>
                  </ResponsiveContainer>
                )}
              </CardContent>
            </Card>
          </motion.div>

          {/* Pie Chart: Model Distribution */}
          <motion.div
             className="rounded-xl"
             initial={{ opacity: 0, scale: 0.95 }}
             animate={{ opacity: 1, scale: 1 }}
             transition={{ duration: 0.5, delay: 0.2 }}
          >
            <Card className="h-full bg-pane/80 backdrop-blur-md border-subtle shadow-xl shadow-black/5 dark:shadow-black/20">
              <CardHeader>
                <CardTitle>模型来源分布</CardTitle>
                <CardDescription>各模型已验证声明的证据来源占比</CardDescription>
              </CardHeader>
              <CardContent className="h-75 flex items-center justify-center -mt-6">
                {modelStats.length === 0 ? (
                  <div className="h-full w-full flex items-center justify-center text-sm text-mut mt-6">
                    暂无模型数据
                  </div>
                ) : (
                  <ResponsiveContainer width="100%" height="100%">
                    <PieChart>
                      <Pie
                        data={modelStats}
                        cx="50%"
                        cy="50%"
                        innerRadius={60}
                        outerRadius={80}
                        paddingAngle={5}
                        dataKey="sources"
                        nameKey="name"
                      >
                        {modelStats.map((_, index) => (
                          <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
                        ))}
                      </Pie>
                      <Tooltip
                        contentStyle={{ backgroundColor: "var(--pane)", borderColor: "var(--border-subtle)", borderRadius: "8px", color: "var(--pri)" }}
                        itemStyle={{ color: "var(--pri)" }}
                        formatter={(value, _name, props) => [`${value} 条证据`, (props?.payload as AnalyticsModelStat)?.name ?? "模型"]}
                      />
                    </PieChart>
                  </ResponsiveContainer>
                )}
              </CardContent>
            </Card>
          </motion.div>
        </div>

        {/* Model Cards Bento Grid */}
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-6">
          {modelStats.length === 0 ? (
            <Card className="sm:col-span-2 lg:col-span-4 bg-pane/80 backdrop-blur-md border-subtle">
              <CardHeader>
                <CardTitle>暂无数据</CardTitle>
                <CardDescription>
                  去检测页或用浏览器插件跑几次检测，这里就会展示各模型的统计信息。
                </CardDescription>
              </CardHeader>
            </Card>
          ) : (
            modelStats.map((model, idx) => (
              <motion.div
                key={model.id}
                className="rounded-xl flex h-full"
                initial={{ opacity: 0, y: 30 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.6, delay: 0.2 + (idx * 0.1) }}
              >
                <Card className="flex flex-col flex-1 w-full bg-pane/80 backdrop-blur-md border-subtle shadow-lg hover:shadow-xl hover:-translate-y-1 transition-all duration-300 shadow-black/5 dark:shadow-black/20 group">
                  <CardHeader>
                    <CardTitle className="text-lg group-hover:text-amber-500 transition-colors">{model.name}</CardTitle>
                    <CardDescription className="text-xs truncate">{model.id}</CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-5 flex-1">
                    <div className="flex items-center justify-between">
                      <span className="text-mut text-sm">置信度</span>
                      <span className="font-bold text-pri">{formatPercent(model.confidence)}</span>
                    </div>
                    <div className="w-full bg-border-subtle rounded-full h-2 overflow-hidden">
                      <motion.div
                        className="bg-amber-500 h-2 rounded-full"
                        initial={{ width: 0 }}
                        animate={{ width: `${Math.max(0, Math.min(100, model.confidence * 100))}%` }}
                        transition={{ duration: 1, delay: 0.5 + (idx * 0.1) }}
                      />
                    </div>

                    <div className="grid grid-cols-2 gap-4 mt-6">
                      <div className="flex flex-col items-center p-3 bg-app/50 rounded-lg border border-subtle/50">
                        <span className="text-3xl font-black text-red-500 tracking-tighter">{model.hallucinations}</span>
                        <span className="text-[10px] text-mut uppercase font-semibold mt-1">幻觉数</span>
                      </div>
                      <div className="flex flex-col items-center p-3 bg-app/50 rounded-lg border border-subtle/50">
                        <span className="text-3xl font-black text-green-500 tracking-tighter">{model.sources}</span>
                        <span className="text-[10px] text-mut uppercase font-semibold mt-1">证据数</span>
                      </div>
                    </div>
                  </CardContent>
                </Card>
              </motion.div>
            ))
          )}
        </div>

        {/* API Key usage */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5, delay: 0.3 }}
        >
          <Card className="bg-pane/80 backdrop-blur-md border-subtle shadow-xl shadow-black/5 dark:shadow-black/20">
            <CardHeader>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <CardTitle className="flex items-center gap-2">
                    <KeyRound className="w-5 h-5 text-amber-500" />
                    接入 Key 调用统计
                  </CardTitle>
                  <CardDescription className="mt-1">
                    每个接入 Key 的累计调用次数（外部系统携带 Key 调用检测 / 对话接口时计数）
                  </CardDescription>
                </div>
                <Badge variant="secondary" className="text-sm">
                  累计 {totalKeyCalls} 次
                </Badge>
              </div>
            </CardHeader>
            <CardContent>
              {apiKeys.length === 0 ? (
                <div className="py-8 text-center text-sm text-mut">
                  还没有接入 Key，去「API Key 管理」页创建一个，用于 Python 脚本 / n8n / 其他系统调用。
                </div>
              ) : (
                <div className="overflow-x-auto rounded-lg border border-subtle/50">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="bg-app/50 text-mut text-left">
                        <th className="px-4 py-2.5 font-medium whitespace-nowrap">Key 名称</th>
                        <th className="px-4 py-2.5 font-medium whitespace-nowrap">前缀</th>
                        <th className="px-4 py-2.5 font-medium whitespace-nowrap">状态</th>
                        <th className="px-4 py-2.5 font-medium text-right whitespace-nowrap">调用次数</th>
                        <th className="px-4 py-2.5 font-medium whitespace-nowrap">最后调用时间</th>
                      </tr>
                    </thead>
                    <tbody>
                      {apiKeys.map((k) => (
                        <tr key={k.id} className="border-t border-subtle/50 hover:bg-app/40 transition-colors">
                          <td className="px-4 py-2.5 font-medium text-pri">{k.name}</td>
                          <td className="px-4 py-2.5 text-mut font-mono text-xs">{k.prefix}…</td>
                          <td className="px-4 py-2.5">
                            {k.is_active ? (
                              <Badge variant="outline" className="text-green-600 border-green-500/40">正常</Badge>
                            ) : (
                              <Badge variant="outline" className="text-red-500 border-red-500/40">已吊销</Badge>
                            )}
                          </td>
                          <td className="px-4 py-2.5 text-right font-bold tabular-nums">{k.usage_count} 次</td>
                          <td className="px-4 py-2.5 text-mut text-xs whitespace-nowrap">{formatDateTime(k.last_used_at)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              <p className="mt-3 text-xs text-mut">
                说明：只有携带 Key 的外部调用会计数；直接在网页上点检测（未填 Key）不计入此处。主 Key（.env 中的 TRUTHLENS_API_KEY）不计入本表。
              </p>
            </CardContent>
          </Card>
        </motion.div>

        {/* Provider usage */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5, delay: 0.35 }}
        >
          <Card className="bg-pane/80 backdrop-blur-md border-subtle shadow-xl shadow-black/5 dark:shadow-black/20">
            <CardHeader>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <CardTitle className="flex items-center gap-2">
                    <Server className="w-5 h-5 text-sky-500" />
                    上游服务调用统计
                  </CardTitle>
                  <CardDescription className="mt-1">
                    各服务 Key 的真实出站调用次数（只计成功调用，用于掌握 Groq / Tavily 等免费额度消耗）
                  </CardDescription>
                </div>
                <Badge variant="secondary" className="text-sm">
                  累计 {totalProviderCalls} 次
                </Badge>
              </div>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
                {PROVIDER_META.map((m) => {
                  const stat = providerMap.get(m.id);
                  const calls = stat?.calls || 0;
                  // zen 等有自定义名的服务优先显示后端返回的展示名（如 mimo-v2.5）
                  const title = stat?.label || m.label;
                  return (
                    <div
                      key={m.id}
                      className="flex flex-col p-4 bg-app/50 rounded-lg border border-subtle/50 hover:border-subtle transition-colors"
                    >
                      <span className="font-bold text-pri">{title}</span>
                      <span className="text-[11px] text-mut mt-0.5">{m.desc}</span>
                      <span className="mt-2 text-2xl font-black tabular-nums text-sky-500">
                        {calls}
                        <span className="ml-1 text-xs font-medium text-mut">次</span>
                      </span>
                      <span className="mt-1 text-[11px] text-mut truncate" title={formatDateTime(stat?.last_used_at)}>
                        {stat?.last_used_at ? formatDateTime(stat.last_used_at) : "从未调用"}
                      </span>
                    </div>
                  );
                })}
              </div>
              <p className="mt-3 text-xs text-mut">
                说明：检测流水线每次跑会调用 1 次声明抽取模型（如 Groq）＋按声明条数多次调用搜索（如 Tavily）。计数从本次更新部署后开始（历史调用不补记），数据存数据库，服务重启不丢失。
              </p>
            </CardContent>
          </Card>
        </motion.div>
      </div>
    </div>
  );
}
