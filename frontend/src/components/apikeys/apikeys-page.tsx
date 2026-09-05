import { useEffect, useState } from "react";
import { KeyRound, Plus, Copy, Trash2, Check, Server } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  fetchApiKeys, createApiKey, revokeApiKey, fetchApiKeyStatus,
  getServerOrigin, getStoredApiKey, setStoredApiKey,
  type ApiKeyItem, type ApiKeyStatus,
} from "@/lib/api";

export function ApiKeysPage() {
  const [keys, setKeys] = useState<ApiKeyItem[]>([]);
  const [status, setStatus] = useState<ApiKeyStatus | null>(null);
  const [name, setName] = useState("我的外部调用");
  const [newKey, setNewKey] = useState("");
  const [copied, setCopied] = useState(false);
  const [activeKey, setActiveKey] = useState(getStoredApiKey());
  const [loading, setLoading] = useState(false);

  const load = async () => {
    try { setStatus(await fetchApiKeyStatus()); } catch {}
    try { setKeys(await fetchApiKeys()); } catch {}
  };
  useEffect(() => { load(); }, []);

  const onCreate = async () => {
    setLoading(true);
    try {
      const r = await createApiKey(name || "我的外部调用");
      setNewKey(r.api_key);
      setActiveKey(r.api_key);
      setStoredApiKey(r.api_key);
      setCopied(false);
      await load();
    } catch (e: any) { alert(e.message); }
    finally { setLoading(false); }
  };
  const onRevoke = async (id: string) => {
    if (!confirm("确定吊销这个 Key？吊销后用它的外部调用会失败（强制模式下）。")) return;
    await revokeApiKey(id);
    await load();
  };
  const copy = async (t: string) => {
    await navigator.clipboard.writeText(t);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  const origin = getServerOrigin();
  const demoKey = newKey || activeKey || "tl-你的key";
  const curl = `curl -X POST ${origin}/api/v1/detect \\
  -H "Authorization: Bearer ${demoKey}" \\
  -H "Content-Type: application/json" \\
  -d '{"model_response": "巴黎是意大利的首都", "conversation_history": []}'`;

  return (
    <div className="flex-1 overflow-y-auto p-6 max-w-4xl mx-auto w-full">
      <h1 className="text-xl font-bold flex items-center gap-2"><KeyRound className="w-5 h-5" /> API Key 管理</h1>
      <p className="text-sm text-mut mt-1">自己用：创建一个 Key，给 Python 脚本 / n8n / 其他系统调用。开放模式，不限流，只计数。</p>

      <Card className="p-4 mt-4">
        <div className="flex items-center gap-2 text-sm font-semibold"><Server className="w-4 h-4" /> 服务器状态</div>
        <div className="text-sm mt-2 space-y-1">
          <div>服务地址：<code className="bg-hover px-1 rounded">{origin}</code>（Docker 映射 <code className="bg-hover px-1 rounded">6655:6655</code>）</div>
          <div>主 Key（.env TRUTHLENS_API_KEY）：{status ? (status.master_configured ? `已配置（${status.master_prefix}...）` : "未配置（当前兼容开放模式）") : "加载中..."}</div>
          <div>强制鉴权：{status ? (status.api_key_required ? "开（无 Key 拒绝）" : "关（有 Key 计数，无 Key 放行）") : "..."}</div>
          <div className="text-mut">在线文档：{origin}/docs</div>
        </div>
      </Card>

      <Card className="p-4 mt-4">
        <div className="text-sm font-semibold">创建新 Key</div>
        <div className="flex gap-2 mt-2">
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="比如：n8n调用、Python脚本" />
          <Button onClick={onCreate} disabled={loading}><Plus className="w-4 h-4 mr-1" />{loading ? "创建中" : "创建"}</Button>
        </div>
        {newKey && (
          <div className="mt-3 p-3 rounded bg-amber-500/10 border border-amber-500/30">
            <div className="text-sm font-semibold text-amber-600">明文只显示一次，立即复制：</div>
            <code className="block mt-1 break-all text-sm">{newKey}</code>
            <Button size="sm" className="mt-2" onClick={() => copy(newKey)}>{copied ? <Check className="w-4 h-4 mr-1" /> : <Copy className="w-4 h-4 mr-1" />}{copied ? "已复制" : "复制"}</Button>
          </div>
        )}
      </Card>

      <Card className="p-4 mt-4">
        <div className="text-sm font-semibold">本机默认调用 Key（存浏览器，用于文档页一键试调用）</div>
        <div className="flex gap-2 mt-2">
          <Input value={activeKey} onChange={(e) => { setActiveKey(e.target.value); setStoredApiKey(e.target.value); }} placeholder="tl-..." />
          <Button variant="outline" onClick={() => { setActiveKey(""); setStoredApiKey(""); }}>清空</Button>
        </div>
      </Card>

      <div className="mt-4 space-y-2">
        {keys.length === 0 && <div className="text-sm text-mut">还没有子 Key，点上面创建。主 Key（如有）在 .env 里，不在这里列出。</div>}
        {keys.map((k) => (
          <Card key={k.id} className="p-3 flex items-center justify-between">
            <div>
              <div className="font-medium text-sm">{k.name} <span className="text-mut">({k.prefix}...)</span> {!k.is_active && <span className="text-red-500">已吊销</span>}</div>
              <div className="text-xs text-mut">调用 {k.usage_count} 次 · 最后 {k.last_used_at || "从未"}</div>
            </div>
            <Button size="sm" variant="outline" onClick={() => onRevoke(k.id)} disabled={!k.is_active}><Trash2 className="w-4 h-4 mr-1" />吊销</Button>
          </Card>
        ))}
      </div>

      <Card className="p-4 mt-4">
        <div className="text-sm font-semibold">外部调用示例（复制即用）</div>
        <pre className="mt-2 text-xs bg-black/40 p-3 rounded overflow-x-auto whitespace-pre-wrap">{curl}</pre>
        <Button size="sm" className="mt-2" onClick={() => copy(curl)}><Copy className="w-4 h-4 mr-1" />复制 curl</Button>
      </Card>
    </div>
  );
}
