# TruthLens 开放 API 文档（中文）

> 拿走即用：一份文档 + 一个 Key，就能在任何地方调用 TruthLens。
> 服务地址：`http://服务器IP:6655`（Docker 默认 `6655:6655` 单端口，WebUI 和 API 同口）
> 在线 Swagger：`http://服务器IP:6655/docs`


## 0. 核心是怎么用的（先看懂这个，再看参数）

TruthLens 是 AI 回答的**验真网关**：把模型的一句话 / 一段话丢进来，
它告诉你**哪句可信、哪句是编的、依据是什么**。一次调用内部走 4 步：

1. **拆断言**：把回答拆成一条条可验证的 claim；观点、寒暄自动跳过，不冤枉主观表达。
2. **找证据**：每条 claim 去联网（Tavily）、知识库文档、历史对话里找依据。
3. **做裁决**：逐条判状态 —— `VERIFIED` 证实 / `CONTRADICTED` 证伪 /
   `UNVERIFIED` 找不到证据，并附引用链接。
4. **算总分**：汇总成 `overall_risk_score`（0-100）+ `risk_level`
  （LOW / MODERATE / HIGH / CRITICAL）。

推荐用法：LOW 直接展示；MODERATE 标黄并附引用；HIGH / CRITICAL 拦截，
只把 CONTRADICTED 的那几句标红展示。

### 一个完整案例（真跑过的数据）

输入（一段 2 真 1 假的话）：

```text
水在标准大气压下100摄氏度沸腾，铁的化学符号是Fe，火星是太阳系中最大的行星。
```

系统做了什么：拆出 3 条 claim → 前两条联网证实 → 第三条被证伪（最大的其实是木星）
→ 总分 80，CRITICAL。

```json
{"overall_risk_score": 80.0, "risk_level": "CRITICAL",
 "claims": [
  {"status": "VERIFIED",     "text": "水在标准大气压下100摄氏度沸腾"},
  {"status": "VERIFIED",     "text": "铁的化学符号是Fe"},
  {"status": "CONTRADICTED", "text": "火星是太阳系中最大的行星"}]}
```

返回怎么看：只看 `claims` 里 `status == CONTRADICTED` 的那几条，就是编的；
点它带的 `citations` 链接就是依据。你该怎么做：标红“火星是太阳系中最大的行星”
并提示“实际最大的是木星”，前两句正常展示。

---

## 1. 鉴权（3 秒看懂）

需要鉴权的接口（`detect / chat / v1 兼容接口 / v1/models`）认这个请求头（二选一）：

```http
Authorization: Bearer tl-你的key
# 或
X-API-Key: tl-你的key
```

* Key 去哪拿：WebUI 侧边栏 → **API Key** 页 → 创建（明文只显示一次，立即复制）；
  或在服务器 `.env` 里填 `TRUTHLENS_API_KEY=tl-xxx` 当主 Key。
* 当前是**兼容开放模式**（`API_KEY_REQUIRED=False`）：带 Key 自动计数，不带也能调，保证老插件不断。
  公网部署建议改 `True`，无 Key 直接 401。

---

## 2. 核心接口：幻觉检测（最常用）

### `POST /api/v1/detect`

把 AI 的一句话（或一段回答）丢进来，返回风险分 + 逐条 claim 判定 + 引用。

**请求：**

```json
{
  "model_response": "巴黎是意大利的首都",
  "conversation_history": [],
  "config": {"check_web": true, "check_documents": true, "check_conversation": true}
}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `model_response` | 是 | 待检测的 AI 回答文本 |
| `conversation_history` | 否 | 上下文 `[{role,content}]`，默认 `[]` |
| `config.check_web` | 否 | 联网核验（Tavily），默认 true |
| `config.check_documents` | 否 | 用知识库文档核验，默认 true |
| `config.check_conversation` | 否 | 用历史对话核验，默认 true |
| `document_ids` | 否 | 指定文档 id 列表 |
| `conversation_id` | 否 | 关联会话 id |

**返回（重点看这 3 个）：**

```json
{
  "overall_risk_score": 100.0,
  "risk_level": "CRITICAL",
  "warning_message": "Response contains likely hallucinated content",
  "claims": [
    {"text": "巴黎是意大利的首都", "status": "CONTRADICTED",
     "risk_score": 95.0, "citations": ["https://en.wikipedia.org/wiki/Paris"]}
  ],
  "metadata": {"processing_time_ms": 9938, "claims_extracted": 1, "claims_verified": 1}
}
```

* `risk_level`：`LOW / MODERATE / HIGH / CRITICAL`（≈ 0-30 / 30-65 / 65-85 / 85-100）。
* 单条 claim 的 `status`：`VERIFIED`（证实）/ `CONTRADICTED`（证伪）/ `UNVERIFIED`（找不到证据）/
  `UNVERIFIABLE_SOURCE`（来源打不开）/ `OPINION`（观点，主观表达不判）/ `SKIPPED`（跳过）。
* 实测耗时 15~85 秒（联网 + 仲裁，claim 越多越慢）。

### `POST /api/v1/detect` 扩展模式（浏览器扩展 / 多消息批量）

除单条 `model_response` 外，同一个接口还支持浏览器扩展的多消息格式：
请求带 `platform + conversation + messages[]`，后端会同步会话并对其中
尚未检测过的 assistant 消息逐条分析，返回按消息区分的 `results[]`。

```json
{
  "platform": "chatgpt",
  "conversation": {
    "id": "platform-conversation-id",
    "url": "https://chatgpt.com/c/xxx",
    "title": "示例会话"
  },
  "messages": [
    {"role": "user", "id": "user-0", "text": "介绍一下巴黎"},
    {"role": "assistant", "id": "assistant-1", "text": "巴黎是意大利的首都", "sources": []}
  ]
}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `platform` | 是 | `chatgpt / claude / gemini / deepseek / copilot` |
| `conversation` | 是 | `{id, url?, title?}`，外部会话元数据 |
| `messages[]` | 是 | 会话消息数组；assistant 消息可带 `sources[]` 作为平台引用 |
| `messages[].id` | 否 | 平台消息 ID，用于去重 |
| `messages[].role` | 是 | `user / assistant` |
| `messages[].text` | 是 | 消息正文（扩展格式用 `text`；单条模式用 `content`） |
| `incrementalSync` | 否 | 增量同步状态，供扩展判断本次新增范围 |
| `summary` | 否 | 扩展统计信息，如消息数、来源数 |

扩展模式返回与单条模式基本一致，额外多了 `results[]`（每条 assistant
消息一个 `{messageId, messageIndex, risk_score, risk_level, claims[]}`）。

---

## 3. OpenAI 兼容接口（给第三方工具用，零改代码）

### `POST /v1/chat/completions`

标准 OpenAI 入参。`model` 可写完整 id（如 `openai/gpt-oss-120b`），
也可写 provider 简写（`groq / zen / nvidia / openrouter / gemini`），服务端自动映射到该渠道第一个可用模型。

```json
{"model": "groq", "messages": [{"role": "user", "content": "你好"}], "stream": false}
```

返回标准 OpenAI 格式：`choices[0].message.content` 即回答。`stream:true` 返回 SSE（`data: {...}` … `data: [DONE]`）。

### `GET /v1/models`

返回 OpenAI 格式模型列表（只列服务端配好 Key 的）。

### 原生接口对照

| 你要干的事 | 调这个 |
|---|---|
| 幻觉检测 | `POST /api/v1/detect` |
| 原生聊天（非流/流） | `POST /api/v1/chat`（body：`model_id/message/conversation_history/stream`） |
| OpenAI 式聊天 | `POST /v1/chat/completions` |
| 模型列表 | `GET /v1/models`（OpenAI 格式）或 `GET /api/v1/models`（原生） |
| Key 管理 | `GET /api/v1/apikeys`、`POST /api/v1/apikeys`、`DELETE /api/v1/apikeys/{key_id}`，状态 `GET /api/v1/apikeys/status` |
| 健康检查 | `GET /health` |

---

## 4. 数据 / 管理接口（WebUI、知识库、统计、设置会用到）

> 说明：这些接口的请求头鉴权规则同前；当前实现里强制鉴权
> （`API_KEY_REQUIRED=True`）只对 `detect / chat / v1 兼容接口 / v1/models`
> 生效。若公网部署，请用防火墙 / 网关保护以下管理类接口。

### 4.1 知识库文档

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/v1/documents/upload` | 上传文档，自动切块 + 向量化。`multipart/form-data`，`file` 必填；不带 `conversation_id` 即进入全局知识库 |
| `GET` | `/api/v1/documents` | 文档列表。支持 `?conversation_id=xxx` 或 `?global_only=true` |
| `GET` | `/api/v1/documents/{doc_id}` | 文档元数据 |
| `DELETE` | `/api/v1/documents/{doc_id}` | 删除文档及其向量块 |

`POST /api/v1/documents/upload` 表单字段：

| 字段 | 必填 | 说明 |
|---|---|---|
| `file` | 是 | 要上传的文档（PDF/TXT/MD 等） |
| `conversation_id` | 否 | 前端会话 ID；缺省时归入全局知识库 |
| `external_conversation_id` + `platform` | 否 | 扩展上传用，自动解析为内部会话 |
| `conversation_url` / `conversation_title` | 否 | 扩展会话元数据 |

返回示例：

```json
{
  "id": "doc-uuid",
  "filename": "guide.pdf",
  "file_type": "application/pdf",
  "file_size_bytes": 12345,
  "chunk_count": 3,
  "created_at": "2026-09-05T12:00:00Z"
}
```

### 4.2 会话管理

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/v1/conversations` | 新建会话。body：`{title?, platform?, metadata?}` |
| `GET` | `/api/v1/conversations` | 会话列表，支持 `?limit=100&offset=0` |
| `GET` | `/api/v1/conversations/{conv_id}` | 会话详情（含 messages） |
| `POST` | `/api/v1/conversations/{conv_id}/messages` | 给会话加消息。body：`{role, content, model_id?}` |
| `DELETE` | `/api/v1/conversations/{conv_id}` | 删除会话（级联清理关联记录） |
| `POST` | `/api/v1/conversations/sync` | 扩展增量同步：`{external_id, platform, title?, external_url?, messages[]}` |

会话消息对象示例：

```json
{
  "role": "assistant",
  "content": "巴黎是意大利的首都",
  "model_id": "openai/gpt-oss-120b"
}
```

### 4.3 统计 / 分析

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/v1/analytics/overview` | 仪表盘总览，支持 `?days=7`；默认 7 天 |

返回字段包括：`summary`、`timeline`、`models`、`api_keys`、`providers`。

### 4.4 API Key 管理

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/v1/apikeys/status` | 服务地址、主 Key 是否配置、是否强制鉴权 |
| `GET` | `/api/v1/apikeys` | 子 Key 列表（不返回明文） |
| `POST` | `/api/v1/apikeys` | 创建子 Key。body：`{name?}`；`api_key` 明文只返回一次 |
| `DELETE` | `/api/v1/apikeys/{key_id}` | 吊销子 Key（软删除 `is_active=false`） |

### 4.5 设置管理（WebUI 设置弹窗）

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/v1/settings/schema` | 可配置项 schema，前端按分类渲染 |
| `GET` | `/api/v1/settings/status` | 每项是否已配置（脱敏）+ 当前生效总览 |
| `PUT` | `/api/v1/settings` | 保存 `.env` 并热重载，body：`{values: {"GROQ_API_KEY": "…"}}` |
| `POST` | `/api/v1/settings/effective` | 查询当前配置是否已生效 |
| `POST` | `/api/v1/settings/test` | 连通测试。body：`{key: "TAVILY_API_KEY", value?: "…"}`；不传 `value` 测已保存值；Tavily/Serper 测试会消耗 1 次查询额度 |

---

## 5. 使用案例（复制即用）

### 案例 1：Python 一行判幻觉（最常用，拿去贴进你的脚本）

```python
import requests
BASE = "http://服务器IP:6655"
KEY = "tl-你的key"
H = {"Authorization": f"Bearer {KEY}"}

def hallucinating(text: str) -> dict:
    r = requests.post(f"{BASE}/api/v1/detect", headers=H, timeout=300,
        json={"model_response": text, "conversation_history": []})
    r.raise_for_status()
    d = r.json()
    return {"score": d["overall_risk_score"], "level": d["risk_level"],
            "claims": [(c["status"], c["text"]) for c in d.get("claims", [])]}

print(hallucinating("巴黎是意大利的首都"))
# {'score': 100.0, 'level': 'CRITICAL', 'claims': [('CONTRADICTED', '巴黎是意大利的首都')]}
```

### 案例 2：用 OpenAI SDK 调 TruthLens 的聊天（LangChain / 任意 Agent 框架同理）

```python
from openai import OpenAI
client = OpenAI(base_url="http://服务器IP:6655/v1", api_key="tl-你的key")
resp = client.chat.completions.create(
    model="groq",  # 或完整 id: openai/gpt-oss-120b / mimo-v2.5
    messages=[{"role": "user", "content": "用一句话说长城是什么"}])
print(resp.choices[0].message.content)
```

### 案例 3：Dify / Cherry Studio / n8n 这类工具里配置

* 类型选 **OpenAI 兼容**（OpenAI-Compatible）；
* `Base URL` 填 `http://服务器IP:6655/v1`；
* `API Key` 填 `tl-你的key`；
* 模型名填 `groq`（或 `GET /v1/models` 里看到的完整 id）。

### 案例 4：cURL（Shell / 服务器上随手测）

```bash
# 幻觉检测
curl -X POST http://服务器IP:6655/api/v1/detect \
  -H "Authorization: Bearer tl-你的key" \
  -H "Content-Type: application/json" \
  -d '{"model_response":"巴黎是意大利的首都","conversation_history":[]}'

# OpenAI 兼容聊天
curl -X POST http://服务器IP:6655/v1/chat/completions \
  -H "Authorization: Bearer tl-你的key" \
  -H "Content-Type: application/json" \
  -d '{"model":"groq","messages":[{"role":"user","content":"你好"}]}'
```

### 案例 5：前端 JS（fetch）

```js
const BASE = "http://服务器IP:6655";
const H = {"Authorization": "Bearer tl-你的key", "Content-Type": "application/json"};
const r = await fetch(`${BASE}/api/v1/detect`, {method: "POST", headers: H,
  body: JSON.stringify({model_response: "巴黎是意大利的首都", conversation_history: []})});
const d = await r.json();
console.log(d.overall_risk_score, d.risk_level);
```

### 案例 6：给 AI 回答自动加“验真”网关（推荐模式）

```text
你的模型出回答 → 调 /api/v1/detect → risk_level 为 LOW 才直接展示，
MODERATE 以上标黄并附 citations，HIGH/CRITICAL 拦截 + 展示 CONTRADICTED 的那几句。
```

---

## 6. 错误码

| 码 | 含义 | 怎么办 |
|---|---|---|
| 400 | 参数错（如 `messages` 为空、不支持的模型） | 看 `detail` 改入参 |
| 401 | Key 无效（仅强制模式 `API_KEY_REQUIRED=True`） | 检查 `Authorization` 头 |
| 404 | 资源不存在（文档/会话/Key 已删除） | 检查 `id` 是否正确 |
| 422 | 请求体字段格式校验失败 | 按错误里的字段路径修正类型/枚举 |
| 500 | 上游模型/搜索出错 | 看 `detail`，稍后重试 |

---

## 7. WebUI 里也有这份文档

侧边栏 → **API 文档**：同样的 curl/Python/JS（自动填你的 Key，一键复制），
外加**在线试调用**（填文本点发送，不用写代码）。改了本文档记得同步那一页。
