# 🛡️ AI Hallucination Detection System

> **Detect, flag, and explain LLM hallucinations before users trust them.**

A production-grade system that intercepts AI-generated responses, extracts claims, verifies them against multiple authoritative sources, and presents detailed hallucination risk analysis with source-backed explanations.

---

## 🇨🇳 中文部署教程（本分支）

> 🙏 **致谢原作者**：本项目基于 **prajwalmandlecha** 的开源项目
> [hallucinationdetection](https://github.com/prajwalmandlecha/hallucinationdetection)
> 二次开发，核心检测管线（声明提取 → 多源验证 → NLI 判定 → LLM 裁决）的设计归功于原作者。
> 本分支的改动点见 [🆕 与原方案的差异](#-与原方案的差异)。

### 0. 端口一览（先记住这两个）

| 端口 | 说明 |
|---|---|
| **6655** | **WebUI + 后端 API（单端口）**：浏览器打开 `http://localhost:6655`；接口文档 `http://localhost:6655/docs` |
| **5433** | PostgreSQL（`docker compose` 把容器内 5432 映射到宿主机 **5433**，避免与本机 postgres 冲突） |

### 1. 最快上手：直接拉 Docker Hub 镜像运行

前置：安装 Docker Desktop；准备好一个 PostgreSQL（需 `pgvector` 扩展；没有的话用第 2 节 compose 自带的一键起）。

```bash
# 拉镜像（4.1 与 latest 是同一镜像的两个标签）
docker pull zhoushu1/truthlens:4.1

# 运行（把下面 xxx 换成你自己的 Key / 数据库地址）
docker run -d --name truthlens --restart unless-stopped \
  -p 6655:6655 \
  -e DATABASE_URL="postgresql+asyncpg://detection_admin:detection_pass@host.docker.internal:5433/ai_detection" \
  -e GROQ_API_KEY="xxx" \
  -e ZEN_API_KEY="xxx" \
  -e ZEN_BASE_URL="https://opencode.ai/zen/go/v1" \
  -e ZEN_MODEL="mimo-v2.5" \
  -e TAVILY_API_KEY="xxx" \
  -e SERPER_API_KEY="xxx" \
  zhoushu1/truthlens:4.1

# 看日志，出现 "Server ready!" 即启动成功（首次启动要加载 spaCy/向量模型，多等半分钟）
docker logs -f truthlens
```

然后打开 **`http://localhost:6655`** 即用；Swagger 文档：`http://localhost:6655/docs`。

> Windows（PowerShell）用户：把上面的 `\` 换行符去掉拼成一行执行，或直接用第 2 节的 compose 方式。

Key 配置速查（也可运行时不传，进 WebUI 的「设置」页可视化填写，保存即热重载生效）：

| 变量 | 必填 | 说明 |
|---|---|---|
| `GROQ_API_KEY` | 三选一 | 聊天 + 声明提取主通道（最快），三者至少填一个 |
| `NVIDIA_API_KEY` / `OPENROUTER_API_KEY` | 三选一 | 备用/兜底通道 |
| `ZEN_API_KEY` + `ZEN_BASE_URL` + `ZEN_MODEL` | 推荐 | 最终裁决通道（OpenAI 兼容网关，默认 mimo-v2.5） |
| `TAVILY_API_KEY` | 推荐 | 联网验证主力 |
| `SERPER_API_KEY` | 选填 | Google 聚合搜索，垂直领域召回更好 |
| `TRUTHLENS_API_KEY` | 选填 | 主调用 Key（`tl-` 开头）；留空 = 兼容开放模式 |
| `API_KEY_REQUIRED` | 选填 | `True` = 外部调用 detect/chat 强制要 Key |
| `DATABASE_URL` | 必填（单容器运行时） | 指向你的 Postgres，格式见上例 |

### 2. 推荐：docker compose 全栈（含数据库，一条命令）

```bash
# 1. 配 Key（复制模板后填写）
cp backend/.env.example backend/.env
# 用记事本打开 backend/.env，按上表填 GROQ / ZEN / TAVILY 等 Key

# 2. 启动（自动构建 CPU 版镜像 + pgvector 数据库）
docker compose up -d --build

# 3. 看日志 / 打开页面
docker compose logs -f backend
# 浏览器打开 http://localhost:6655

# 停止（保留数据）
docker compose down
# 停止并清空数据库数据卷
docker compose down -v
```

### 3. 本地开发（Windows，不用 Docker）

```powershell
# 1. 只起数据库
docker compose up -d postgres

# 2. 后端
cd backend
.\venv\Scripts\Activate.ps1   # 没有 venv 就先 python -m venv venv
pip install -r requirements.txt
python -m spacy download en_core_web_sm
copy .env.example .env   # 填写各家 Key
uvicorn app.main:app --host 0.0.0.0 --port 6655

# 3. 前端（另开一个终端）
cd frontend
npm install
npm run dev      # 开发模式 http://localhost:5173
npm run build    # 构建产物到 frontend/dist，后端 6655 会自动挂载
```

### 4. 浏览器扩展（Chrome / Edge）

`chrome://extensions` → 右上打开「开发者模式」→「加载已解压的扩展程序」→ 选择本仓库的 `extension/` 目录。
扩展会把各大 AI 网站的对话同步到后端（默认 `http://127.0.0.1:6655`）做幻觉检测。

### 5. 常用命令与排错

```bash
docker ps                                   # 看容器是否在跑
docker logs -f truthlens                    # 看日志
docker stop truthlens && docker rm truthlens  # 停掉单容器
docker pull zhoushu1/truthlens:latest       # 跟最新版（与 4.1 同步更新）
netstat -ano | findstr 6655                 # Windows 查端口占用
```

| 现象 | 原因/办法 |
|---|---|
| `/health` 长时间不通 | 首次启动在加载 spaCy/向量模型，等 1~2 分钟再看日志 |
| 上传文档失败 | 确认 `sentence-transformers` 模型能联网下载；国内服务器可在构建时用 `--build-arg HF_ENDPOINT=https://hf-mirror.com` |
| 连不上数据库 | 单容器运行时 `DATABASE_URL` 必须指向容器可达地址（本机 PG 用 `host.docker.internal`，不要写 `localhost`） |
| `latest` 和 `4.1` 哪个新 | 同一镜像的两个标签，内容一致；`latest` 永远跟随最新推送 |

---

## 🙏 致谢

- 原作者 **[prajwalmandlecha](https://github.com/prajwalmandlecha)** 与原项目
  [hallucinationdetection](https://github.com/prajwalmandlecha/hallucinationdetection)：
  提供了完整的多源幻觉检测管线设计与前后端雏形，本分支站在它的肩膀上。
- 本地向量化：[sentence-transformers](https://www.sbert.net/)（all-MiniLM-L6-v2）；命名实体：[spaCy](https://spacy.io/)。

## 🆕 与原方案的差异（本分支新增 / 修改）

1. **推理全面 API 化**：NLI 判定 / 声明提取 / 最终裁决 / 联网验证全部走第三方 API（Groq / Zen 网关 / Tavily / Serper），不再需要本地大模型与 GPU。
2. **API Key 管理体系**：主 Key（`.env`）+ 数据库子 Key，兼容开放 / 强制鉴权两种模式，WebUI 自带 Key 管理页（创建、计数、吊销）。
3. **OpenAI 兼容接口**：`GET /v1/models`、`POST /v1/chat/completions`（支持流式、provider 简写映射），外部系统零改代码即可调用。
4. **WebUI 新增页面**：Settings 可视化配置（改 Key 落盘 `.env` + 热重载）、Analytics 仪表盘、API 文档页（含 curl/Python/JS 示例与在线试调用）。
5. **缺陷修复**：
   - 全局知识库文档上传 500（`documents.conversation_id` NOT NULL 未随模型改可空；新增 alembic 迁移 `003` + 启动自愈 DDL）；
   - `/conversations/sync` 返回的 `total_messages` 漏算本次新增（计数先于 flush）；
   - API 文档页 JSX 路径占位符被当成变量导致干净构建失败。
6. **部署生产化**：CPU 瘦身镜像（torch 换 CPU wheel、去 CUDA，镜像约 2.48GB，容器监听 `0.0.0.0:6655`）、compose 去掉 GPU 保留段、GitHub Actions push 自动构建并推送 Docker Hub（`zhoushu1/truthlens:4.1` + `:latest`）。
7. **测试资产**：中文全面测试报告（`全面测试报告_2026-09-05.md`）与可复用脚本（`api_full_smoke_test*.py`、`api_auth_enforcement_test.py`、`verify_fixes.py`）。

---

## 📋 Table of Contents

- [Overview](#overview)
- [System Architecture](#system-architecture)
- [Features](#features)
- [Tech Stack](#tech-stack)
- [Backend — Detection Pipeline](#backend--detection-pipeline)
  - [Pipeline Flow](#pipeline-flow)
  - [Step 1: Parallel Extraction](#step-1-parallel-extraction-ner--claim-extraction)
  - [Step 2: Multi-Source Verification](#step-2-multi-source-verification)
  - [Step 3: NLI-Based Claim Verification](#step-3-nli-based-claim-verification)
  - [Step 4: LLM Adjudication](#step-4-llm-adjudication)
  - [API Endpoints](#api-endpoints)
- [Frontend — Multi-Model Chat Interface](#frontend--multi-model-chat-interface)
- [Browser Extension](#browser-extension)
- [Storage Architecture](#storage-architecture)
- [Supported LLM Models](#supported-llm-models)
- [Getting Started](#getting-started)

---

## Overview

Large Language Models (LLMs) frequently generate confident-sounding but factually incorrect responses — a phenomenon known as **hallucination**. This system acts as a **post-generation verification layer** that:

1. **Intercepts** any AI response (via our chat frontend or browser extension)
2. **Extracts** individual factual claims from the response
3. **Verifies** each claim against multiple sources (conversation history, user documents, web search, APIs)
4. **Scores** hallucination risk at both claim-level and response-level using NLI and LLM Adjudication
5. **Displays** results with detailed explanations, source links, and warnings

### What Makes This Different?

| Feature | Our System |
|---|---|
| **Multi-source verification** | Checks against conversation history, user documents, specific domains (Arxiv, CrossRef, PubMed, Semantic Scholar) AND web search simultaneously |
| **Claim-level granularity** | Every individual claim is scored, not just the entire response |
| **LLM-driven source selection** | The claim extractor intelligently suggests which sources to check per claim |
| **Source attribution** | Every verification result links back to the actual source (URL, document chunk, conversation turn) |
| **ZERO-dependency Vectorization** | Runs locally inside the Python process using SentenceTransformers (`all-MiniLM-L6-v2`) — NO Ollama needed |
| **Hybrid Analysis Pipeline** | Uses fast DeBERTa-v3 NLP for entailment/contradiction math, plus Gemini 3 Flash for intelligent final-adjudication |

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        PRESENTATION LAYER                               │
│                                                                         │
│  ┌──────────────────────┐         ┌──────────────────────────────────┐  │
│  │   Browser Extension  │         │   Chat Frontend (Next.js)        │  │
│  │   (Chrome MV3)       │         │   Multi-model comparison (≤3)    │  │
│  │                      │         │                                  │  │
│  │  • ChatGPT overlay   │         │  ┌────────┬────────┬────────┐    │  │
│  │  • Claude overlay    │         │  │Model A │Model B │Model C │    │  │
│  │  • Gemini overlay    │         │  │+ risk  │+ risk  │+ risk  │    │  │
│  │  • Any LLM website   │         │  │overlay │overlay │overlay │    │  │
│  └──────────┬───────────┘         │  └────────┴────────┴────────┘    │  │
│             │                     │  [  Unified Message Input Bar  ] │  │
│             │                     └──────────────┬───────────────────┘  │
│             │                                    │                      │
└─────────────┼────────────────────────────────────┼──────────────────────┘
              │              ┌─────────────────────┘
              ▼              ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                        BACKEND (FastAPI)                                │
│                                                                         │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │    POST /detect  │  POST /chat  │  POST /documents/upload        │   │
│  └──────────┬───────────────────────────────────────────────────────┘   │
│             │                                                           │
│             ▼                                                           │
│  ┌─────────────────────────────────────────────────────────────┐        │
│  │              STEP 1: PARALLEL EXTRACTION                    │        │
│  │  ┌─────────────────────┐   ┌──────────────────────────────┐ │        │
│  │  │  NER Extractor      │   │  Claim Extractor (LLM)       │ │        │
│  │  │  (spaCy en_core_    │   │  (Groq Llama 3.3 70B)        │ │        │
│  │  │   web_sm)           │   │                              │ │        │
│  │  └────────┬────────────┘   └────────────┬─────────────────┘ │        │
│  └───────────┼─────────────────────────────┼───────────────────┘        │
│              ▼                             ▼                            │
│  ┌─────────────────────────────────────────────────────────────┐        │
│  │        STEP 2: MULTI-SOURCE VERIFICATION (Domain Router)    │        │
│  │                                                             │        │
│  │  ┌────────────────┐ ┌────────────────┐ ┌─────────────────┐  │        │
│  │  │ Memory (NER)   │ │ Vector DB      │ │ Web / Domain API│  │        │
│  │  │ Match prior    │ │ ST natively    │ │ Arxiv, PubMed,  │  │        │
│  │  │ chat history   │ │ pgvector 384d  │ │ Tavily, Serper  │  │        │
│  │  └──────┬─────────┘ └──────┬─────────┘ └──────┬──────────┘  │        │
│  └─────────┼──────────────────┼──────────────────┼─────────────┘        │
│            └─────────┬────────┘──────────────────┘                      │
│                      ▼                                                  │
│  ┌─────────────────────────────────────────────────────────────┐        │
│  │        STEP 3: NLI VERIFICATION (DeBERTa-v3-base)           │        │
│  │        Scores Entailment, Contradiction, and Neutral        │        │
│  └───────────────────────────┬─────────────────────────────────┘        │
│                              ▼                                          │
│  ┌─────────────────────────────────────────────────────────────┐        │
│  │        STEP 4: LLM ADJUDICATION (Gemini 3 Flash)            │        │
│  │        Generates natural language reasoning and risk levels │        │
│  └─────────────────────────────────────────────────────────────┘        │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Features

### 🔍 Core Detection Engine
- **LLM-powered claim extraction** — Uses Groq Llama 3.3 70B to decompose AI responses into individual verifiable claims
- **Domain-Specific Verification Router** — Routes claims automatically to highly specific academic endpoints (`Crossref`, `Semantic Scholar`, `Pubmed`, `arXiv`)
- **Native PGVector Hybrid Search** — Utilizes `all-MiniLM-L6-v2` locally inside Python for 384d chunk embedding, completely free from constraints.
- **NLI-based semantic verification** — DeBERTa-v3-base cross-encoder classifies each (claim, evidence) pair 
- **LLM Adjudication** — Resolves edge-cases using Gemini 3 Preview model reasoning capabilities to output an intuitive human-readable justification.
- **Source-attributed explanations** — Every flagged claim links back to the actual source URL, document chunk, or conversation turn

### 💬 Multi-Model Chat Interface
- **Compare up to 3 LLMs side-by-side** — Send one message, get responses from multiple models simultaneously
- **10+ free models** — Groq, NVIDIA NIM, OpenRouter — no paid API keys required
- **Dynamic layout** — Chat window automatically adjusts from 1 to 2 to 3 columns based on selected models
- **Per-model analysis** — Each response independently analyzed for hallucinations in parallel
- **Streaming** — Real-time SSE streaming for all model responses
- **Document upload** — Upload reference documents that become part of the verification knowledge base

### 🧩 Browser Extension
- **Works on ChatGPT, Claude, Gemini** — Content script detects AI response bubbles on LLM chat websites
- **Non-intrusive overlay** — Small risk badge on each response; click to expand full analysis panel
- **Same backend** — Extension calls the exact same `/detect` API endpoint as the chat frontend

### 📊 Analysis Display
- **Risk score gauge** (0–100) with color-coded severity (green → amber → orange → red)
- **Warning banner** with contextual messages for risky responses
- **Claim-by-claim breakdown** showing verification status, evidence, and source links
- **Inline response highlighting** — Risky claims highlighted directly in the AI response text
- **Source panel** — Clickable links to web sources, document chunks, and conversation references

---

## Tech Stack

| Layer | Technology | Why |
|---|---|---|
| **Backend** | Python 3.13 + FastAPI | Async-first, high performance, native Python ML ecosystem |
| **Frontend** | Next.js (React) + TypeScript | Component-based, SSR, great DX |
| **Extension** | Chrome Manifest V3 | Modern extension standard, vanilla JS |
| **Database** | PostgreSQL 16 | Battle-tested, extensible with pgvector |
| **Vector Store** | pgvector (PostgreSQL extension) | No separate infra, L2 distance, hybrid queries |
| **NLI Model** | DeBERTa-v3-base (cross-encoder) | 92.38% SNLI accuracy, GPU-accelerated (CUDA 12.4) |
| **Claim Extraction** | Llama 3.3 70B (via Groq) | Ultra-fast inference, excellent JSON output, free tier |
| **NER** | spaCy (en_core_web_sm) | Fast, accurate entity extraction |
| **Embeddings** | all-MiniLM-L6-v2 (via SentenceTransformers) | Local, free, 384d vectors |
| **Web Search** | Tavily, Serper, Google Fact Check API, Wikipedia API,etc Domain Specific Search APIs | Domain Specific Knowledge |
| **Chat LLMs** | Groq / NVIDIA NIM / OpenRouter | All free-tier — no paid API keys needed |
| **Containerization** | Docker + Docker Compose + NVIDIA Container Toolkit | GPU passthrough, reproducible deployment |

---

## Backend — Detection Pipeline

### Step 1: Parallel Extraction (NER + Claim Extraction)

When a request arrives, two operations run **in parallel**:

#### 1A. NER Extraction (spaCy)
- Extract named entities from conversation messages using `en_core_web_sm`
- **Entity types**: PERSON, ORG, GPE, DATE, CARDINAL, EVENT, PRODUCT, etc.
- **Incremental processing**: Tracks `last_processed_index` — only runs spaCy on new messages, not the full conversation history
- **Storage**: Each entity stored as a flat row in PostgreSQL's `extracted_entities` table, linked to its source `conversation_id`
- **Duplicates**: If "Einstein" appears in messages #1, #3, and #5 → 3 separate rows, each linked to its source message. The verifier finds all matches and NLI picks the best evidence.

#### 1B. Claim Extraction (LLM-Powered)
Send the AI response to **Groq Llama 3.3 70B** to generate atomic claims categorized by domain.

### Step 2: Multi-Source Verification
The system utilizes a `DomainSourceRouter` to gather 10-20 pieces of evidence per claim from:
- Academic endpoints (PubMed, Arxiv, SemanticScholar)
- Web (Tavily/Serper)
- Vector DB (user document uploads)
- Semantic History matches


### Step 3: NLI-Based Claim Verification

For each `(claim, evidence)` pair retrieved from the sources, run through the **DeBERTa NLI cross-encoder**:
- **ENTAILMENT** (0–1) | Evidence supports the claim 
- **CONTRADICTION** (0–1) | Evidence contradicts the claim
- **NEUTRAL** (0–1) | Evidence is inconclusive


### Step 4: LLM Adjudication

Raw math is passed into **Gemini 3 Flash Preview** to output an easy-to-understand status:
`VERIFIED`, `PARTIALLY_VERIFIED`, `CONTRADICTED`, `SKIPPED`.

The adjudicator catches nuanced contradictions, understands temporal shifts, flags subjective hallucination statements masquerading as factual, and provides a conversational `reasoning` and `suggestion` for the frontend.

### API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/health` | Health check |
| `GET` | `/api/v1/models` | List all available LLM models (native format) |
| `POST` | `/api/v1/detect` | Main hallucination detection — accepts AI response + context, returns full analysis |
| `POST` | `/api/v1/chat` | Proxy to LLM APIs — forwards user message to selected model, supports SSE streaming |
| `POST` | `/api/v1/documents/upload` | Upload document → chunk → natively embed via SentenceTransformers → store in pgvector |
| `GET` | `/api/v1/documents/{doc_id}` | Get document metadata |
| `DELETE` | `/api/v1/documents/{doc_id}` | Delete document and its embeddings |
| `POST` | `/api/v1/conversations` | Create a new conversation context |
| `GET` | `/api/v1/conversations/{conv_id}` | Get conversation with messages |
| `POST` | `/api/v1/conversations/{conv_id}/messages` | Add messages to a conversation |
| `GET` | `/v1/models` | OpenAI-compatible model list |
| `POST` | `/v1/chat/completions` | OpenAI-compatible chat completions (streaming supported) |
| `GET` | `/api/v1/documents` | List documents (`?conversation_id=` or `?global_only=true`) |
| `GET` | `/api/v1/conversations` | List conversations |
| `DELETE` | `/api/v1/conversations/{conv_id}` | Delete conversation and related records |
| `POST` | `/api/v1/conversations/sync` | Sync messages from external platform / extension |
| `GET` | `/api/v1/analytics/overview` | Analytics dashboard overview (`?days=7`) |
| `GET` | `/api/v1/apikeys/status` | API key / auth status |
| `GET` | `/api/v1/apikeys` | List sub keys (no plaintext) |
| `POST` | `/api/v1/apikeys` | Create a sub key (plaintext returned once) |
| `DELETE` | `/api/v1/apikeys/{key_id}` | Revoke a sub key |
| `GET` | `/api/v1/settings/schema` | Settings schema for WebUI rendering |
| `GET` | `/api/v1/settings/status` | Configured status (masked) + effective summary |
| `PUT` | `/api/v1/settings` | Save settings and hot-reload |
| `POST` | `/api/v1/settings/effective` | Query whether saved settings are effective |
| `POST` | `/api/v1/settings/test` | Test a provider key / URL connectivity |

#### `POST /api/v1/detect` — Request

```json
{
  "conversation_id": "uuid",
  "model_id": "llama-3.3-70b-versatile",
  "model_response": "The Eiffel Tower was built in 1889 by Gustave Eiffel...",
  "conversation_history": [
    { "role": "user", "content": "Tell me about the Eiffel Tower" },
    { "role": "assistant", "content": "The Eiffel Tower was built in 1889..." }
  ],
  "document_ids": ["doc-uuid-1"],
  "config": {
    "check_web": true,
    "check_documents": true,
    "check_conversation": true,
    "claim_threshold": 0.3
  }
}
```

> **Note:** Config booleans are **opt-out overrides**. Set to `false` to force-disable a source. When `true` (default), the LLM's per-claim `suggested_sources` drives which sources are actually queried.

#### `POST /api/v1/chat` — Request

```json
{
  "conversation_id": "uuid",
  "model_id": "llama-3.3-70b-versatile",
  "message": "Tell me about the Eiffel Tower",
  "conversation_history": [],
  "stream": true
}
```

---

## Frontend — Multi-Model Chat Interface

### Layout & Functionality

The chat frontend is built with **Next.js (React + TypeScript)**, featuring a dynamic multi-model comparison view:

```
┌─────────────────────────────────────────────────────────┐
│  🛡️ AI Hallucination Detector         [Upload Doc] [⚙] │
├───────────────────┬───────────────────┬─────────────────┤
│ ▼ Llama 3.3 70B   │ ▼ Mistral 7B      │ ▼ Gemma 2 9B    │
│   (Groq)          │   (NVIDIA)        │   (Groq)        │
│                   │                   │                 │
│ User: Tell me...  │ User: Tell me...  │ User: Tell me...│
│                   │                   │                 │
│ AI: The Eiffel... │ AI: Built in...   │ AI: The iconic..│
│ ┌──────────────┐  │ ┌──────────────┐  │ ┌─────────────┐│
│ │ Risk: 23 🟢  │  │ │ Risk: 45 🟡  │  │ │ Risk: 71 🟠 ││
│ │ 5/5 verified │  │ │ 3/5 verified │  │ │ 2/6 verified││
│ │ [Details ▼]  │  │ │ [Details ▼]  │  │ │ [Details ▼] ││
│ └──────────────┘  │ └──────────────┘  │ └─────────────┘│
│                   │                   │                 │
├───────────────────┴───────────────────┴─────────────────┤
│ [📎] Type your message...                     [Send ➤] │
│ [+ Add Model]                     [Models: 3/3]         │
└─────────────────────────────────────────────────────────┘
```

### Key Behaviors

| Feature | Implementation |
|---|---|
| **Dynamic columns** | 1 model = full width, 2 models = 50/50, 3 models = 33/33/33. CSS Grid with smooth transitions |
| **Model selector** | Dropdown in each column header with all 10+ free models |
| **Parallel detection** | After each model responds, frontend calls `POST /detect` for each response independently |
| **Hallucination overlay** | Expandable panel below each AI response showing: risk gauge, claim cards, source links, warnings |
| **Document upload** | Upload PDFs/docs → backend chunks + embeds → `document_ids` included in all future `/detect` calls |
| **Streaming** | Model responses stream in real-time via SSE; detection runs after stream completes |

---

## Browser Extension

### Architecture (Chrome Manifest V3)

```
extension/
├── manifest.json        # Permissions, content scripts, background service worker
├── content.js           # Injected into ChatGPT/Claude/Gemini pages
├── background.js        # Service worker for API calls
├── popup.html           # Extension popup UI (settings, status)
├── popup.js             # Popup logic
├── overlay.js           # Risk badge + floating analysis panel
└── styles.css           # Overlay styling
```

### How It Works

1. **Content script** detects AI response elements on supported LLM websites using DOM observers
2. When a new AI response appears, content script extracts the response text + conversation history
3. Background worker calls `POST /api/v1/detect` with the response and context
4. Content script renders a **risk badge** (🟢🟡🟠🔴) overlaid on the AI response
5. Click to expand full analysis panel with claim breakdown, warnings, and source links

---

## Storage Architecture

We use **one PostgreSQL 16 database** with the `pgvector` extension, configured identically for sync and async IO:

- `conversations` & `messages`
- `documents` & `document_chunks` (containing `embedding vector(384)`)
- `extracted_entities` (for memory search)

**Why pgvector over FAISS/ChromaDB?**
- **Same database** — no additional infrastructure to deploy/manage
- **Hybrid queries** — combine vector similarity with relational filters in a single SQL query
- **Concurrent performance** — outperforms ChromaDB under concurrent load
- **Scales** — handles 10–100M vectors before needing specialized solutions

---

## Supported LLM Models

All supported models utilize **free-tier APIs**:

| Tier | Model | Provider |
|---|---|---|
| 🥇 | Llama 3.3 70B | Groq |
| 🥇 | Gemini 3 Flash Preview | Google GenAI |
| 🥇 | Llama 3.1 70B | NVIDIA NIM |
| 🥇 | Nemotron 70B | OpenRouter |
| 🥈 | Llama 3.1 8B | Groq |
| 🥈 | Gemma 2 9B | Groq |

---

## Getting Started

### Prerequisites

- **Python 3.13+**
- **Docker & Docker Compose** (for PostgreSQL)
- **NVIDIA GPU** with driver ≥ 556.12 (for CUDA 12.4 NLI inference)
- **NVIDIA Container Toolkit** (for GPU passthrough in Docker)
- **API Keys** :
  - **Groq** — `console.groq.com` (Claim extraction + chat)
  - **Tavily / Serper** (Web Search)
  - **NVIDIA NIM** — `build.nvidia.com` (chat)
  - **OpenRouter** — `openrouter.ai` (chat)
  - **Gemini** — `aistudio.google.com` (Adjudication and Chat)

### Quick Start

```bash
# 1. Clone the repository
git clone <repo-url>
cd AI_HallicunationDetectionSystem

# 2. Start PostgreSQL with pgvector
docker compose up -d postgres

# 3. Backend setup
cd backend
python -m venv venv
.\venv\Scripts\Activate.ps1            # Windows

# Install Dependencies 
pip install -r requirements.txt
python -m spacy download en_core_web_sm

# 4. Configure environment
cp .env.example .env
# Edit .env with your API keys 

# 5. Run database migrations
alembic upgrade head

# 6. Start Backend
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
# API docs at http://localhost:8000/docs
```

### Docker (Full Stack with GPU)

```bash
# Build and start everything (PostgreSQL + Backend with GPU)
docker compose up --build

# Backend will be at http://localhost:8000
# Requires NVIDIA Container Toolkit for GPU passthrough
```