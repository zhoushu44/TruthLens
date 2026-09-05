# ── TruthLens 全栈单镜像（API 推理模式，无需 GPU/CUDA）──────────────────
# 用法（与本仓库 docker-compose.yml 一致，context=仓库根目录）：
#   docker build -f backend/Dockerfile -t <hub-user>/<repo> .
# 由 .github/workflows/docker-hub.yml 在 push 时自动构建并推送到 Docker Hub。
#
# 说明：
#   - 推理(NLI/裁决/声明提取/联网验证)全部走第三方 API（Groq/Zen/Tavily/Serper…），
#     因此不再需要 nvidia/cuda 基础镜像；
#   - 仍保留两块“本地”能力：spaCy NER（en_core_web_sm，启动加载）与
#     SentenceTransformers 文档向量化（all-MiniLM-L6-v2，上传文档/知识库检索用，CPU 推理）；
#   - 运行时通过环境变量提供：DATABASE_URL / GROQ_API_KEY / ZEN_API_KEY /
#     ZEN_BASE_URL / ZEN_MODEL / TAVILY_API_KEY 等（Key 只注入运行环境，不打进镜像）。

# ══ Stage 1: Python 依赖（构建 /opt/venv）═══════════════════════════════
FROM python:3.11-slim AS python-deps

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# libgomp1: torch CPU 运行需要；curl: 健康检查
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# 与本地 venv 一致使用 Python 3.11
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /deps
COPY backend/requirements.txt .
# torch 先装 CPU 专用 wheel：requirements 里 torch==2.6.0 在 PyPI 默认是 CUDA 版，
# 会拖进约 2.5GB 的 nvidia-* 依赖；CPU 版体积小一个量级，文档向量化走 CPU 推理足够用。
# 注意：预装的 2.6.0+cpu 满足 sentence-transformers 的 torch>=1.11 约束，无需重复安装。
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir --retries 10 --timeout 120 "torch==2.6.0+cpu" --index-url https://download.pytorch.org/whl/cpu \
    && grep -vE '^[[:space:]]*torch==' requirements.txt > /tmp/requirements-notorch.txt \
    && pip install --no-cache-dir --retries 10 --timeout 120 -r /tmp/requirements-notorch.txt

# spaCy NER 模型（启动时加载 en_core_web_sm）
RUN python -m spacy download en_core_web_sm

# 预下载文档向量化模型（best-effort；失败不影响构建，运行时首次上传会自动下载。
# 国内网络可在构建时加 --build-arg HF_ENDPOINT=https://hf-mirror.com 换镜像源）
ARG HF_ENDPOINT=https://huggingface.co
ENV HF_ENDPOINT=${HF_ENDPOINT}
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')" \
    || echo "WARN: HF 预下载失败（运行时首次上传文档时会自动下载）"

# ══ Stage 2: 前端构建 ═════════════════════════════════════════════════
FROM node:22-slim AS frontend-builder
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci
COPY frontend/ ./
# .dockerignore 已排除 frontend/dist 与 node_modules，镜像内重新构建
RUN npm run build

# ══ Stage 3: 运行镜像 ═════════════════════════════════════════════════
FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PORT=6655 \
    HOST=0.0.0.0

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=python-deps /opt/venv /opt/venv

WORKDIR /app
# 后端代码（app / alembic / alembic.ini / 迁移等）
COPY backend/ /app/
# 前端产物 → /app/frontend_dist（backend/app/main.py 挂载 WebUI 的目录）
COPY --from=frontend-builder /web/dist /app/frontend_dist

# 数据库默认值（与本地默认一致；容器化部署请用环境变量覆盖为你的 Postgres 地址）
ENV DATABASE_URL="postgresql+asyncpg://detection_admin:detection_pass@localhost:5433/ai_detection" \
    WEB_SEARCH_ENABLED="True"

EXPOSE 6655

# 健康检查（镜像内自带 curl）
HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=3 \
    CMD curl -fsS http://127.0.0.1:6655/health || exit 1

# 监听 0.0.0.0:${PORT}（默认 6655）
CMD ["sh", "-c", "uvicorn app.main:app --host ${HOST:-0.0.0.0} --port ${PORT:-6655}"]
