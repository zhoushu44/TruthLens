# -*- coding: utf-8 -*-
"""TruthLens comprehensive API smoke suite (run against a fresh instance).

用法:  python api_full_smoke_test.py [base_url]   (默认 http://127.0.0.1:6666)
退出码 = 失败用例数。所有结果打印到 stdout。
"""
import io
import json
import sys
import time
import uuid

import requests

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:6666"
H = {"Content-Type": "application/json"}

results = []  # (name, status, detail)
created_keys = []  # 需要清理的 apikey id


def log(name, ok, detail="", err=None):
    status = "PASS" if ok else "FAIL"
    extra = f" | {err}" if err else ""
    results.append((name, status, f"{detail}{extra}"))
    print(f"[{status}] {name}  ({detail:.0f}s)" if isinstance(detail, float) else f"[{status}] {name}  {detail}{extra}")


def t_req(method, path, **kw):
    t0 = time.time()
    kw.setdefault("timeout", 25)
    kw.setdefault("headers", H)
    r = requests.request(method, BASE + path, **kw)
    return r, time.time() - t0


def expect(name, cond, r, extra=""):
    ok = bool(cond(r))
    dt = r.elapsed.total_seconds() if hasattr(r, "elapsed") else 0
    try:
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text[:200]
        detail = body if not ok or extra else f"HTTP {r.status_code}"
    except Exception:
        detail = r.text[:200]
    log(name, ok, f"HTTP {r.status_code} {str(detail)[:180]} {extra}" if not ok else (extra or f"HTTP {r.status_code}"))
    return r


# ─────────────────────────── 0. meta ───────────────────────────
r, dt = t_req("GET", "/health")
expect("GET /health", lambda r: r.status_code == 200 and "healthy" in r.text, r)
if r.status_code == 200:
    h = r.json()
    log("health components", True, f"nli={h['components']['nli_model']['loaded']} web={h['components']['web_search']['enabled']} models={len(h.get('supported_models', []))}")

r, _ = t_req("GET", "/docs")
expect("GET /docs (Swagger)", lambda r: r.status_code == 200, r)

r, _ = t_req("GET", "/openapi.json")
expect("GET /openapi.json", lambda r: r.status_code == 200 and len(r.json().get("paths", {})) >= 20, r)

# ─────────────────────────── 1. models ───────────────────────────
r, _ = t_req("GET", "/v1/models")
expect("GET /v1/models (OpenAI 兼容)", lambda r: r.status_code == 200 and r.json().get("data"), r)
avail = []
if r.status_code == 200:
    avail = [m["id"] for m in r.json()["data"]]
    log("可用模型数", True, f"{len(avail)}: {avail}")

r, _ = t_req("GET", "/api/v1/models")
expect("GET /api/v1/models (别名)", lambda r: r.status_code == 200, r)

# ─────────────────────────── 2. apikeys ───────────────────────────
r, _ = t_req("GET", "/api/v1/apikeys/status")
expect("GET /apikeys/status", lambda r: r.status_code == 200 and "master_configured" in r.json(), r)
if r.status_code == 200:
    s = r.json()
    log("apikeys/status 内容", True, f"master_configured={s['master_configured']} api_key_required={s['api_key_required']}")

r, _ = t_req("GET", "/api/v1/apikeys")
expect("GET /apikeys 列表", lambda r: r.status_code == 200 and "keys" in r.json(), r)

r, _ = t_req("POST", "/api/v1/apikeys", json={"name": f"自动全面测试-{uuid.uuid4().hex[:6]}"})
if r.status_code == 200 and "api_key" in r.json():
    raw_key = r.json()["api_key"]
    kid = r.json()["id"]
    created_keys.append(kid)
    log("POST /apikeys 创建", True, f"prefix={r.json()['prefix']} key开头={raw_key[:8]}…")
    expect("Key 格式 tl- 前缀", lambda r: raw_key.startswith("tl-") and len(raw_key) > 20, r)
    # 用子 Key 走一次鉴权计数(免费端点)
    r2, _ = t_req("GET", "/v1/models", headers={"Authorization": f"Bearer {raw_key}"})
    expect("带子Key调用 /v1/models 计数", lambda r: r.status_code == 200, r2)
    r3, _ = t_req("GET", "/api/v1/apikeys")
    used = [k for k in r3.json()["keys"] if k["id"] == kid]
    expect("usage_count 已递增", lambda r: used and used[0]["usage_count"] >= 1, r3)
    # 无效 Key 兼容模式不报错
    r4, _ = t_req("GET", "/v1/models", headers={"Authorization": "Bearer tl-invalid-key-000"})
    expect("无效Key(兼容模式) 放行", lambda r: r.status_code == 200, r4)
else:
    log("POST /apikeys 创建", False, f"HTTP {r.status_code} {r.text[:200]}")

# ─────────────────────────── 3. settings ───────────────────────────
r, _ = t_req("GET", "/api/v1/settings/schema")
expect("GET /settings/schema", lambda r: r.status_code == 200 and r.json().get("items"), r)
if r.status_code == 200:
    log("schema 项数", True, f"items={len(r.json()['items'])} categories={r.json()['categories']}")

r, _ = t_req("GET", "/api/v1/settings/status")
expect("GET /settings/status", lambda r: r.status_code == 200 and "effective" in r.json(), r)
eff0 = None
if r.status_code == 200:
    eff0 = r.json()["effective"]
    log("settings/status effective", True, f"claim_ready={eff0['claim_ready']} adj={eff0['adjudicator']} chat_models={eff0['chat_models_count']}")

r, _ = t_req("POST", "/api/v1/settings/effective")
expect("POST /settings/effective", lambda r: r.status_code == 200 and r.json().get("effective"), r)

# 连通性测试(已保存值/草稿值)
r, _ = t_req("POST", "/api/v1/settings/test", json={"key": "GROQ_API_KEY"})
expect("settings/test GROQ(已保存值)", lambda r: r.status_code == 200 and r.json().get("ok") is True, r)
r, _ = t_req("POST", "/api/v1/settings/test", json={"key": "TAVILY_API_KEY"})
expect("settings/test TAVILY(已保存值)", lambda r: r.status_code == 200 and r.json().get("ok") is True, r)
r, _ = t_req("POST", "/api/v1/settings/test", json={"key": "ZEN_BASE_URL"})
expect("settings/test ZEN_BASE_URL", lambda r: r.status_code == 200 and r.json().get("ok") is True, r)
r, _ = t_req("POST", "/api/v1/settings/test", json={"key": "SERPER_API_KEY", "value": "sk-bogus-value-123"})
expect("settings/test 错误草稿Key 应失败", lambda r: r.status_code == 200 and r.json().get("ok") is False, r)
r, _ = t_req("POST", "/api/v1/settings/test", json={"key": "GEMINI_API_KEY"})
expect("settings/test GEMINI(未配置) 应失败", lambda r: r.status_code == 200 and r.json().get("ok") is False, r)
r, _ = t_req("POST", "/api/v1/settings/test", json={"key": "NOT_A_REAL_KEY"})
expect("settings/test 未知Key 应失败", lambda r: r.status_code == 200 and r.json().get("ok") is False, r)

# ─────────────────────────── 4. conversations ───────────────────────────
conv_id = None
r, _ = t_req("POST", "/api/v1/conversations", json={"title": f"全面测试会话{uuid.uuid4().hex[:4]}", "platform": "frontend", "metadata": {"t": 1}})
if r.status_code == 200:
    conv_id = r.json()["id"]
    log("POST /conversations 创建", True, f"id={conv_id}")
else:
    log("POST /conversations 创建", False, f"HTTP {r.status_code} {r.text[:200]}")

if conv_id:
    r, _ = t_req("GET", "/api/v1/conversations")
    expect("GET /conversations 列表含新建", lambda r: r.status_code == 200 and any(c["id"] == conv_id for c in r.json()), r)
    r, _ = t_req("POST", f"/api/v1/conversations/{conv_id}/messages", json={"role": "user", "content": "巴黎是法国的首都吗？"})
    expect("POST messages (user)", lambda r: r.status_code == 200, r)
    r, _ = t_req("POST", f"/api/v1/conversations/{conv_id}/messages", json={"role": "assistant", "content": "是的，巴黎是法国的首都。", "model_id": "openai/gpt-oss-20b"})
    expect("POST messages (assistant)", lambda r: r.status_code == 200, r)
    r, _ = t_req("GET", f"/api/v1/conversations/{conv_id}")
    expect("GET /conversations/{id} 含2条消息", lambda r: r.status_code == 200 and len(r.json().get("messages", [])) == 2, r)

# sync(扩展平台导入)
r, _ = t_req("POST", "/api/v1/conversations/sync", json={
    "platform": "chatgpt", "external_id": f"ext-{uuid.uuid4().hex[:8]}", "title": "sync测试",
    "messages": [
        {"role": "user", "text": "你好", "external_id": "user-0", "message_index": 0, "role_index": 0},
        {"role": "assistant", "text": "你好！有什么可以帮你？", "external_id": "assistant-1", "message_index": 1, "role_index": 0, "model_id": "openai/gpt-oss-20b"},
    ],
})
sync_id = None
if r.status_code == 200:
    sync_id = r.json().get("conversation_id")
    expect("POST /conversations/sync 导入2条", lambda r: r.json().get("total_messages", 0) == 2 and r.json().get("messages_synced") == 2, r)
    r2, _ = t_req("POST", "/api/v1/conversations/sync", json={
        "platform": "chatgpt", "external_id": r.json()["external_id"] if "external_id" in r.json() else "", "title": "sync测试",
        "messages": [{"role": "assistant", "text": "第二条回复", "external_id": "assistant-2", "message_index": 2, "role_index": 1}],
    })
    expect("POST /conversations/sync 幂等+增量(只新增1条)", lambda r: r.json().get("messages_synced") == 1 and r.json().get("total_messages") == 3, r2)
else:
    log("POST /conversations/sync", False, f"HTTP {r.status_code} {r.text[:200]}")

# ─────────────────────────── 5. documents ───────────────────────────
doc_id = None
r, _ = t_req("GET", "/api/v1/documents")
expect("GET /documents 列表", lambda r: r.status_code == 200, r)
content = ("The Eiffel Tower is 330 metres tall and is located in Paris, France. "
           "It was completed in 1889 for the World's Fair. Paris is the capital of France.")
files = {"file": (f"eiffel-{uuid.uuid4().hex[:6]}.txt", io.BytesIO(content.encode("utf-8")), "text/plain")}
r = requests.post(BASE + "/api/v1/documents/upload", files=files, timeout=180)
if r.status_code == 200:
    j = r.json()
    doc_id = j.get("document_id") or j.get("id")
    log("POST /documents/upload", True, f"id={doc_id} chunks={j.get('chunks')}")
else:
    log("POST /documents/upload", False, f"HTTP {r.status_code} {r.text[:300]}")

if doc_id:
    r, _ = t_req("GET", f"/api/v1/documents/{doc_id}")
    expect("GET /documents/{id} 元数据", lambda r: r.status_code == 200, r)
    r, _ = t_req("DELETE", f"/api/v1/documents/{doc_id}")
    expect("DELETE /documents/{id}", lambda r: r.status_code == 200, r)
    r, _ = t_req("GET", f"/api/v1/documents/{doc_id}")
    expect("删除后 GET 应 404", lambda r: r.status_code == 404, r)

# 空文件 400
r = requests.post(BASE + "/api/v1/documents/upload", files={"file": ("empty.txt", io.BytesIO(b""), "text/plain")}, timeout=30)
expect("空文件上传应 400", lambda r: r.status_code == 400, r)

# ─────────────────────────── 6. analytics ───────────────────────────
r, _ = t_req("GET", "/api/v1/analytics/overview?days=7")
expect("GET /analytics/overview?days=7", lambda r: r.status_code == 200, r)
if r.status_code == 200:
    log("analytics 顶层字段", True, f"{list(r.json().keys())}")
r, _ = t_req("GET", "/api/v1/analytics/overview?days=0")
expect("analytics days=0 应 422", lambda r: r.status_code == 422, r)
r, _ = t_req("GET", "/api/v1/analytics/overview?days=91")
expect("analytics days=91 应 422", lambda r: r.status_code == 422, r)

# ─────────────────────────── 7. chat (真实LLM) ───────────────────────────
chat_model = None
for m in avail:
    if "gpt-oss-20b" in m:
        chat_model = m
        break
chat_model = chat_model or (avail[0] if avail else None)

if chat_model:
    r = requests.post(BASE + "/api/v1/chat", json={
        "conversation_id": conv_id, "model_id": chat_model,
        "message": "Reply with exactly: OK", "conversation_history": [], "stream": False,
    }, timeout=90)
    ok = r.status_code == 200 and r.json().get("response")
    expect("POST /api/v1/chat 非流式(真实LLM)", lambda r: ok, r,
           extra=f"model={chat_model} resp={r.json().get('response','')[:40]!r}" if r.status_code == 200 else "")
    # 流式
    t0 = time.time()
    try:
        with requests.post(BASE + "/api/v1/chat", json={
            "conversation_id": conv_id, "model_id": chat_model,
            "message": "Reply with exactly: HI", "conversation_history": [], "stream": True,
        }, stream=True, timeout=(10, 60)) as rr:
            buf = ""
            for line in rr.iter_lines(decode_unicode=True):
                if line:
                    buf += line + "\n"
                if "data: [DONE]" in buf or "data: [ERROR]" in buf or time.time() - t0 > 55:
                    break
            ok2 = rr.status_code == 200 and "data:" in buf and ("[DONE]" in buf or "[ERROR]" in buf)
            log("POST /api/v1/chat 流式SSE", ok2 and "[ERROR]" not in buf, f"HTTP {rr.status_code} 收到chunk+结束标记 {time.time()-t0:.0f}s",
                err=None if ok2 else buf[:300])
    except Exception as e:
        log("POST /api/v1/chat 流式SSE", False, str(e)[:200])
    r, _ = t_req("POST", "/api/v1/chat", json={"model_id": "no-such-model", "message": "x", "stream": False})
    expect("未知 model_id 应 400", lambda r: r.status_code == 400, r)
    # gemini 无 Key 模型应 400
    for gm in ("gemini-3-flash-preview", "gemma-2-9b-it"):
        r, _ = t_req("POST", "/api/v1/chat", json={"model_id": gm, "message": "x", "stream": False})
        if r.status_code == 400:
            log(f"无Key模型 {gm} 应 400", True, f"{r.text[:120]}")
            break
else:
    log("chat 测试跳过", False, "无可用模型")

# ─────────────────────────── 8. openai 兼容 ───────────────────────────
if chat_model:
    r = requests.post(BASE + "/v1/chat/completions", json={
        "model": chat_model,
        "messages": [{"role": "user", "content": "Reply with exactly: OPENAI_OK"}],
        "stream": False,
    }, timeout=90)
    okc = r.status_code == 200 and r.json().get("choices")
    expect("POST /v1/chat/completions 非流式", lambda r: okc, r,
           extra=f"resp={r.json()['choices'][0]['message']['content'][:40]!r}" if okc else r.text[:200])
    t0 = time.time()
    try:
        with requests.post(BASE + "/v1/chat/completions", json={
            "model": chat_model,
            "messages": [{"role": "user", "content": "Reply with exactly: SSE"}],
            "stream": True,
        }, stream=True, timeout=(10, 60)) as rr:
            buf = ""
            for line in rr.iter_lines(decode_unicode=True):
                if line:
                    buf += line + "\n"
                if "data: [DONE]" in buf or time.time() - t0 > 55:
                    break
            okc2 = rr.status_code == 200 and "chat.completion.chunk" in buf and "data: [DONE]" in buf
            log("POST /v1/chat/completions 流式(OpenAI分块)", okc2, f"HTTP {rr.status_code} {time.time()-t0:.0f}s",
                err=None if okc2 else buf[:300])
    except Exception as e:
        log("POST /v1/chat/completions 流式", False, str(e)[:200])
    # provider 简写映射
    r, _ = t_req("POST", "/v1/chat/completions", json={"model": "groq", "messages": [{"role": "user", "content": "hi"}], "stream": False})
    expect("model='groq' 简写映射", lambda r: r.status_code == 200, r)
    # 空 messages
    r, _ = t_req("POST", "/v1/chat/completions", json={"model": chat_model, "messages": [], "stream": False})
    expect("空 messages 应 400", lambda r: r.status_code == 400, r)
else:
    log("openai 兼容测试跳过", False, "无可用模型")

# ─────────────────────────── 9. detect 主流程 ───────────────────────────
def run_detect(payload, name, timeout=240):
    t0 = time.time()
    try:
        r = requests.post(BASE + "/api/v1/detect", json=payload, timeout=timeout)
        dt = time.time() - t0
        if r.status_code == 200:
            j = r.json()
            claims = j.get("claims", [])
            verdicts = {}
            for c in claims:
                verdicts[c.get("status", "?")] = verdicts.get(c.get("status", "?"), 0) + 1
            md = j.get("metadata", {})
            log(name, True,
                f"risk={j.get('overall_risk_score')} level={j.get('risk_level')} claims={len(claims)} "
                f"verdicts={verdicts} meta={md.get('processing_time_ms')}ms "
                f"({dt:.0f}s)", extra="")
            if claims:
                c0 = claims[0]
                log(f"{name} 首条claim结构", True,
                    f"text={c0.get('text','')[:50]!r} risk={c0.get('risk_score')} srcs={len(c0.get('citations', []))}")
            return True
        else:
            log(name, False, f"HTTP {r.status_code} {r.text[:400]} ({dt:.0f}s)")
            return False
    except Exception as e:
        log(name, False, f"EXC {str(e)[:200]} ({time.time()-t0:.0f}s)")
        return False

run_detect({
    "model_response": "埃菲尔铁塔高330米，位于法国巴黎。它于1889年完工。巴黎是法国的首都。",
    "conversation_history": [{"role": "user", "content": "介绍一下埃菲尔铁塔和巴黎"}],
    "model_id": chat_model,
    "config": {"check_web": True, "check_documents": False, "check_conversation": False},
}, "POST /detect 中文+联网验证")

if doc_id is None:
    # 再传一个文档用于 detect 文档通道
    content = ("The Eiffel Tower is 330 metres tall and is located in Paris, France. "
               "It was completed in 1889 for the World's Fair. Paris is the capital of France.")
    files = {"file": (f"eiffel-kb-{uuid.uuid4().hex[:6]}.txt", io.BytesIO(content.encode("utf-8")), "text/plain")}
    r = requests.post(BASE + "/api/v1/documents/upload", files=files, timeout=180)
    if r.status_code == 200:
        doc_id = r.json().get("document_id") or r.json().get("id")
    if doc_id:
        run_detect({
            "model_response": "The Eiffel Tower is 330 metres tall. Paris is the capital of France.",
            "conversation_history": [{"role": "user", "content": "Tell me about the Eiffel Tower and Paris."}],
            "document_ids": [doc_id], "model_id": chat_model,
            "config": {"check_web": False, "check_documents": True, "check_conversation": False},
        }, "POST /detect 文档知识库验证")
        requests.delete(BASE + f"/api/v1/documents/{doc_id}", timeout=30)

run_detect({
    "platform": "chatgpt", "conversation": {"id": f"ext-detect-{uuid.uuid4().hex[:6]}", "title": "扩展检测测试"},
    "messages": [
        {"role": "user", "text": "地球到太阳的距离是多少？", "id": "user-0", "index": 0},
        {"role": "assistant", "text": "地球到太阳的平均距离约为1.5亿公里，即1个天文单位。", "id": "assistant-1", "index": 1},
    ],
    "summary": {"enabled": True, "kind": "final"},
    "model_id": chat_model,
    "config": {"check_web": True, "check_documents": False, "check_conversation": False},
}, "POST /detect 扩展(extension)模式")

# 非法参数校验
r, _ = t_req("POST", "/api/v1/detect", json={"conversation_history": [{"role": "banana", "content": "x"}]})
expect("detect 非法 role 应 422", lambda r: r.status_code == 422, r)

# ─────────────────────────── 10. 清理与杂项 ───────────────────────────
if conv_id:
    r, _ = t_req("DELETE", f"/api/v1/conversations/{conv_id}")
    expect("DELETE /conversations/{id}", lambda r: r.status_code == 200, r)
if sync_id:
    requests.delete(BASE + f"/api/v1/conversations/{sync_id}", timeout=30)
for kid in created_keys:
    requests.delete(BASE + f"/api/v1/apikeys/{kid}", timeout=30)

r, _ = t_req("GET", "/definitely-not-a-route")
expect("未知路由 404", lambda r: r.status_code == 404, r)

# ─────────────────────────── 汇总 ───────────────────────────
fails = [x for x in results if x[1] == "FAIL"]
print("\n" + "=" * 70)
print(f"TOTAL={len(results)}  PASS={len(results) - len(fails)}  FAIL={len(fails)}")
for f in fails:
    print("FAIL:", f[0], "|", f[2][:200])
sys.exit(len(fails))
