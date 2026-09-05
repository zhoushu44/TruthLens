import { useState } from "react";
import { BookOpen, Copy, Play, Check, Lightbulb, ListChecks } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { getServerOrigin, getStoredApiKey, tryDetectWithKey } from "@/lib/api";

type Lang = "curl" | "python" | "js";

export function ApiDocsPage() {
  const [lang, setLang] = useState<Lang>("curl");
  const [key, setKey] = useState(getStoredApiKey());
  const [text, setText] = useState("巴黎是意大利的首都");
  const [result, setResult] = useState("");
  const [running, setRunning] = useState(false);
  const [copied, setCopied] = useState(false);
  const origin = getServerOrigin();
  const k = key || "tl-你的key";

  const snippets: Record<Lang, string> = {
    curl: `# 1）幻觉检测（核心）\ncurl -X POST ${origin}/api/v1/detect \\\n  -H "Authorization: Bearer ${k}" \\\n  -H "Content-Type: application/json" \\\n  -d '{"model_response": "巴黎是意大利的首都", "conversation_history": [], "config": {"check_web": true}}'\n\n# 2）OpenAI 兼容聊天（给 Dify/n8n/其他客户端用）\ncurl -X POST ${origin}/v1/chat/completions \\\n  -H "Authorization: Bearer ${k}" \\\n  -H "Content-Type: application/json" \\\n  -d '{"model": "groq", "messages": [{"role": "user", "content": "你好"}]}'\n\n# 3）模型列表\ncurl ${origin}/v1/models -H "Authorization: Bearer ${k}"`,
    python: `import requests\nBASE = "${origin}"\nKEY = "${k}"\nH = {"Authorization": f"Bearer {KEY}"}\n\n# 幻觉检测\nr = requests.post(f"{BASE}/api/v1/detect", headers=H, json={\n    "model_response": "巴黎是意大利的首都",\n    "conversation_history": [],\n    "config": {"check_web": True},\n})\nprint(r.json()["overall_risk_score"], r.json()["risk_level"])\n\n# OpenAI 兼容聊天\nr2 = requests.post(f"{BASE}/v1/chat/completions", headers=H, json={\n    "model": "groq", "messages": [{"role": "user", "content": "你好"}]\n})\nprint(r2.json()["choices"][0]["message"]["content"])`,
    js: `const BASE = "${origin}";\nconst KEY = "${k}";\nconst H = { "Authorization": \`Bearer \${KEY}\`, "Content-Type": "application/json" };\n\n// 幻觉检测\nconst r = await fetch(\`\${BASE}/api/v1/detect\`, {\n  method: "POST", headers: H,\n  body: JSON.stringify({ model_response: "巴黎是意大利的首都", conversation_history: [] })\n});\nconsole.log(await r.json());\n\n// OpenAI 兼容聊天\nconst r2 = await fetch(\`\${BASE}/v1/chat/completions\`, {\n  method: "POST", headers: H,\n  body: JSON.stringify({ model: "groq", messages: [{ role: "user", content: "你好" }] })\n});\nconsole.log(await r2.json());`,
  };

  const copy = async () => {
    await navigator.clipboard.writeText(snippets[lang]);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };
  const run = async () => {
    setRunning(true);
    setResult("");
    try {
      const data = await tryDetectWithKey(key, text);
      setResult(JSON.stringify({ overall_risk_score: data.overall_risk_score, risk_level: data.risk_level, claims: (data.claims || []).slice(0, 3).map((c: any) => ({ text: c.text, status: c.status, risk_score: c.risk_score })) }, null, 2));
    } catch (e: any) { setResult("失败：" + e.message); }
    finally { setRunning(false); }
  };

  return (
    <div className="flex-1 overflow-y-auto p-6 max-w-4xl mx-auto w-full">
      <h1 className="text-xl font-bold flex items-center gap-2"><BookOpen className="w-5 h-5" /> API 文档（中文）</h1>
      <p className="text-sm text-mut mt-1">服务地址 <code className="bg-hover px-1 rounded">{origin}</code> · 鉴权 <code className="bg-hover px-1 rounded">Authorization: Bearer tl-xxx</code>（兼容模式无 Key 也能调，有 Key 自动计数）· 完整 Swagger：{origin}/docs</p>

        <Card className="p-4 mt-4">
          <div className="text-sm font-semibold flex items-center gap-2"><Lightbulb className="w-4 h-4" /> 核心是怎么用的（先看懂这个，再看参数）</div>
          <p className="text-sm mt-2">TruthLens 是 AI 回答的<b>验真网关</b>：把模型的一句话 / 一段话丢进来，它告诉你<b>哪句可信、哪句是编的、依据是什么</b>。一次调用内部走 4 步：</p>
          <ol className="text-sm mt-2 space-y-1 list-decimal ml-5">
            <li><b>拆断言</b>：把回答拆成一条条可验证的 claim；观点、寒暄自动跳过，不冤枉主观表达。</li>
            <li><b>找证据</b>：每条 claim 去联网（Tavily）、知识库文档、历史对话里找依据。</li>
            <li><b>做裁决</b>：逐条判状态 —— <code className="bg-hover px-1 rounded">VERIFIED</code> 证实 / <code className="bg-hover px-1 rounded">CONTRADICTED</code> 证伪 / <code className="bg-hover px-1 rounded">UNVERIFIED</code> 找不到证据，并附引用链接。</li>
            <li><b>算总分</b>：汇总成 <code className="bg-hover px-1 rounded">overall_risk_score</code>（0-100）+ <code className="bg-hover px-1 rounded">risk_level</code>（LOW / MODERATE / HIGH / CRITICAL）。</li>
          </ol>
          <p className="text-sm mt-2 text-mut">推荐用法：LOW 直接展示；MODERATE 标黄并附引用；HIGH / CRITICAL 拦截，只把 CONTRADICTED 的那几句标红展示。</p>
        </Card>

        <Card className="p-4 mt-4">
          <div className="text-sm font-semibold flex items-center gap-2"><ListChecks className="w-4 h-4" /> 一个完整案例（真跑过的数据）</div>
          <div className="text-sm mt-2 space-y-2">
            <div><b>输入</b>（一段 2 真 1 假的话）：<code className="bg-hover px-1 rounded">水在标准大气压下100摄氏度沸腾，铁的化学符号是Fe，火星是太阳系中最大的行星。</code></div>
            <div><b>系统做了什么</b>：拆出 3 条 claim → 前两条联网证实 → 第三条被证伪（最大的其实是木星）→ 总分 80，CRITICAL。</div>
            <pre className="text-xs bg-black/40 p-3 rounded overflow-x-auto whitespace-pre-wrap">{`{"overall_risk_score": 80.0, "risk_level": "CRITICAL",
"claims": [
  {"status": "VERIFIED",     "text": "水在标准大气压下100摄氏度沸腾"},
  {"status": "VERIFIED",     "text": "铁的化学符号是Fe"},
  {"status": "CONTRADICTED", "text": "火星是太阳系中最大的行星"}
]}`}</pre>
            <div><b>返回怎么看</b>：只看 <code className="bg-hover px-1 rounded">claims</code> 里 <code className="bg-hover px-1 rounded">status == CONTRADICTED</code> 的那几条，就是编的；点它带的 <code className="bg-hover px-1 rounded">citations</code> 链接就是依据。</div>
            <div><b>你该怎么做</b>：页面上标红“火星是太阳系中最大的行星”并提示“实际最大的是木星”，前两句正常展示。下面试调用里把这段话粘进去点发送，能复现这个结果。</div>
          </div>
        </Card>

      <Card className="p-4 mt-4">
        <div className="text-sm font-semibold">快速开始（3 步）</div>
        <ol className="text-sm mt-2 space-y-1 list-decimal ml-5">
          <li>去「API Key 管理」页创建一个 Key（或在 .env 填 TRUTHLENS_API_KEY）。</li>
          <li>用下面的 <code className="bg-hover px-1 rounded">/api/v1/detect</code> 发第一条检测。</li>
          <li>看返回 <code className="bg-hover px-1 rounded">overall_risk_score / risk_level / claims</code>。</li>
        </ol>
      </Card>

      <Card className="p-4 mt-4">
        <div className="flex items-center gap-2">
          {(["curl", "python", "js"] as Lang[]).map((l) => (
            <Button key={l} size="sm" variant={lang === l ? "default" : "outline"} onClick={() => setLang(l)}>{l === "curl" ? "cURL" : l === "python" ? "Python" : "JS"}</Button>
          ))}
          <Button size="sm" variant="outline" className="ml-auto" onClick={copy}>{copied ? <Check className="w-4 h-4 mr-1" /> : <Copy className="w-4 h-4 mr-1" />}{copied ? "已复制" : "复制代码"}</Button>
        </div>
        <pre className="mt-3 text-xs bg-black/40 p-3 rounded overflow-x-auto whitespace-pre-wrap">{snippets[lang]}</pre>
      </Card>

      <Card className="p-4 mt-4">
        <div className="text-sm font-semibold">接口一览</div>
        <div className="text-xs text-mut mt-1">以下为当前已实现接口；请求/响应完整字段以 Swagger 为准。</div>
        <div className="text-sm mt-3 space-y-3">
          <div>
            <div className="font-semibold">核心 / 开放调用</div>
            <div className="mt-1 space-y-1">
              <div><code className="bg-hover px-1 rounded">POST /api/v1/detect</code> —— 幻觉检测（单条模式 + 扩展多消息模式）</div>
              <div><code className="bg-hover px-1 rounded">POST /api/v1/chat</code> —— 原生聊天（body: model_id / message / conversation_history / stream）</div>
              <div><code className="bg-hover px-1 rounded">POST /v1/chat/completions</code> —— OpenAI 兼容聊天</div>
              <div><code className="bg-hover px-1 rounded">GET /v1/models</code> —— OpenAI 格式模型列表 · <code className="bg-hover px-1 rounded">GET /api/v1/models</code> —— 原生格式</div>
              <div><code className="bg-hover px-1 rounded">GET /health</code> —— 健康检查</div>
            </div>
          </div>
          <div>
            <div className="font-semibold">知识库文档</div>
            <div className="mt-1 space-y-1">
              <div><code className="bg-hover px-1 rounded">POST /api/v1/documents/upload</code> —— 上传文档（multipart/form-data）</div>
              <div><code className="bg-hover px-1 rounded">GET /api/v1/documents</code> —— 文档列表（?conversation_id= 或 ?global_only=true）</div>
              <div><code className="bg-hover px-1 rounded">GET /api/v1/documents/{'{doc_id}'}</code> —— 文档元数据 · <code className="bg-hover px-1 rounded">DELETE /api/v1/documents/{'{doc_id}'}</code> —— 删除文档</div>
            </div>
          </div>
          <div>
            <div className="font-semibold">会话管理</div>
            <div className="mt-1 space-y-1">
              <div><code className="bg-hover px-1 rounded">POST /api/v1/conversations</code> —— 新建 · <code className="bg-hover px-1 rounded">GET /api/v1/conversations</code> —— 列表</div>
              <div><code className="bg-hover px-1 rounded">GET /api/v1/conversations/{'{conv_id}'}</code> —— 详情 · <code className="bg-hover px-1 rounded">DELETE /api/v1/conversations/{'{conv_id}'}</code> —— 删除</div>
              <div><code className="bg-hover px-1 rounded">POST /api/v1/conversations/{'{conv_id}'}/messages</code> —— 添加消息 · <code className="bg-hover px-1 rounded">POST /api/v1/conversations/sync</code> —— 扩展增量同步</div>
            </div>
          </div>
          <div>
            <div className="font-semibold">统计 / Key / 设置</div>
            <div className="mt-1 space-y-1">
              <div><code className="bg-hover px-1 rounded">GET /api/v1/analytics/overview</code> —— 仪表盘统计（?days=7）</div>
              <div><code className="bg-hover px-1 rounded">GET /api/v1/apikeys/status</code> —— 服务/鉴权状态 · <code className="bg-hover px-1 rounded">GET /api/v1/apikeys</code> —— Key 列表</div>
              <div><code className="bg-hover px-1 rounded">POST /api/v1/apikeys</code> —— 创建 Key · <code className="bg-hover px-1 rounded">DELETE /api/v1/apikeys/{'{key_id}'}</code> —— 吊销 Key</div>
              <div><code className="bg-hover px-1 rounded">GET /api/v1/settings/schema</code> · <code className="bg-hover px-1 rounded">GET /api/v1/settings/status</code> · <code className="bg-hover px-1 rounded">PUT /api/v1/settings</code> · <code className="bg-hover px-1 rounded">POST /api/v1/settings/effective</code> · <code className="bg-hover px-1 rounded">POST /api/v1/settings/test</code> —— 设置项 schema / 状态 / 保存 / 生效 / 连通测试（Tavily/Serper 测试耗 1 次额度）</div>
            </div>
          </div>
          <div className="text-xs text-mut">错误码：400 参数错 / 401 Key 无效（强制模式）/ 404 资源不存在 / 422 字段校验失败 / 500 上游模型错</div>
        </div>
      </Card>

      <Card className="p-4 mt-4">
        <div className="text-sm font-semibold flex items-center gap-2"><Play className="w-4 h-4" /> 在线试调用（不懂代码也能点）</div>
        <div className="text-xs text-mut mt-1">填 Key（可空，兼容模式）+ 待检测文本，点发送，直接看风险分。</div>
        <Input className="mt-2" value={key} onChange={(e) => setKey(e.target.value)} placeholder="tl-你的key（可空）" />
        <Textarea className="mt-2" rows={3} value={text} onChange={(e) => setText(e.target.value)} />
        <Button className="mt-2" onClick={run} disabled={running}>{running ? "检测中..." : "发送检测"}</Button>
        {result && <pre className="mt-2 text-xs bg-black/40 p-3 rounded overflow-x-auto whitespace-pre-wrap">{result}</pre>}
      </Card>
    </div>
  );
}
