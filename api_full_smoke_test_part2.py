# -*- coding: utf-8 -*-
"""TruthLens 第二轮针对性用例(修正第一轮脚本缺陷 + 复现产品缺陷)."""
import io
import sys
import time
import uuid

import requests

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:6666"
H = {"Content-Type": "application/json"}
results = []
cleanup_ids = []


def log(name, ok, detail="", err=None):
    results.append((name, "PASS" if ok else "FAIL", detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  {detail}" + (f"  | {err}" if err else ""))


def run_detect(payload, name, timeout=300):
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
            log(name, True, f"risk={j.get('overall_risk_score')} level={j.get('risk_level')} "
                            f"claims={len(claims)} verdicts={verdicts} {md.get('processing_time_ms')}ms ({dt:.0f}s)")
            if claims:
                c0 = claims[0]
                log(name + " [首条]", True,
                    f"text={c0.get('text','')[:40]!r} status={c0.get('status')} risk={c0.get('risk_score')} "
                    f"reasoning={str(c0.get('reasoning'))[:60]!r} citations={len(c0.get('citations', []))}")
            # 记录会话id方便清理
            mid = md.get("conversation_id")
            if mid:
                cleanup_ids.append(mid)
            return True
        log(name, False, f"HTTP {r.status_code} {r.text[:300]} ({dt:.0f}s)")
        return False
    except Exception as e:
        log(name, False, f"EXC {str(e)[:300]} ({time.time()-t0:.0f}s)")
        return False


# ── 1. detect 主流程重跑(真实调用) ──
m = None
try:
    m = [x["id"] for x in requests.get(BASE + "/v1/models", timeout=20).json()["data"]]
except Exception:
    pass
model = next((x for x in (m or []) if "gpt-oss-20b" in x), (m or [None])[0])

run_detect({
    "model_response": "埃菲尔铁塔高330米，位于法国巴黎。它于1889年完工。巴黎是法国的首都。",
    "conversation_history": [{"role": "user", "content": "介绍一下埃菲尔铁塔和巴黎"}],
    "model_id": model,
    "config": {"check_web": True, "check_documents": False, "check_conversation": False},
}, "detect#1 中文+联网")

run_detect({
    "platform": "chatgpt",
    "conversation": {"id": f"ext-detect2-{uuid.uuid4().hex[:6]}", "title": "扩展检测二轮"},
    "messages": [
        {"role": "user", "text": "地球到太阳的距离是多少？", "id": "user-0", "index": 0},
        {"role": "assistant", "text": "地球到太阳的平均距离约为1.5亿公里，即1个天文单位。", "id": "assistant-1", "index": 1},
    ],
    "summary": {"enabled": True, "kind": "final"},
    "model_id": model,
    "config": {"check_web": True, "check_documents": False, "check_conversation": False},
}, "detect#2 扩展(extension)模式")

# ── 2. 参数校验(修正版) ──
r = requests.post(BASE + "/api/v1/detect", json={
    "model_response": "x" * 5,
    "conversation_history": [{"role": "banana", "content": "x"}],
}, timeout=20)
log("detect 非法 role 应 422", r.status_code == 422, f"HTTP {r.status_code} {r.text[:150]}")

r = requests.post(BASE + "/api/v1/detect", json={}, timeout=20)
log("detect 空body(两种模式都没给) 应 400 提示", r.status_code == 400 and "Provide either" in r.text, f"HTTP {r.status_code}")

r = requests.get(BASE + "/api/v1/no-such-endpoint", timeout=20)
log("未知 /api/v1 路由 应 404", r.status_code == 404, f"HTTP {r.status_code}")

# ── 3. sync total_messages 缺陷复现 ──
ext = f"ext-repro-{uuid.uuid4().hex[:8]}"
r = requests.post(BASE + "/api/v1/conversations/sync", json={
    "platform": "chatgpt", "external_id": ext, "title": "同步缺陷复现",
    "messages": [
        {"role": "user", "text": "第一条", "external_id": "user-0", "message_index": 0, "role_index": 0},
        {"role": "assistant", "text": "第一条回复", "external_id": "assistant-1", "message_index": 1, "role_index": 0},
    ],
}, timeout=30)
if r.status_code == 200:
    j = r.json()
    cid = j["conversation_id"]
    cleanup_ids.append(cid)
    log("sync#1: messages_synced=2 应 total=2",
        j["messages_synced"] == 2 and j["total_messages"] == 2,
        f"返回 messages_synced={j['messages_synced']} total_messages={j['total_messages']} ← 若total=0即缺陷")
    g = requests.get(BASE + f"/api/v1/conversations/{cid}", timeout=20)
    stored = len(g.json().get("messages", []))
    log("sync#1 后 GET 实际条数", stored == 2, f"GET={stored} (与total_messages字段对比)")
else:
    log("sync#1", False, f"HTTP {r.status_code} {r.text[:200]}")

# ── 4. 文档上传: 带 conversation_id vs 不带(全局知识库) ──
conv = requests.post(BASE + "/api/v1/conversations", json={"title": "上传用会话"}, timeout=20).json()
cleanup_ids.append(conv["id"])
content = b"The Eiffel Tower is 330 metres tall. Paris is the capital of France. It was completed in 1889."
r1 = requests.post(BASE + "/api/v1/documents/upload",
                   files={"file": ("doc-with-conv.txt", io.BytesIO(content), "text/plain")},
                   data={"conversation_id": conv["id"]}, timeout=240)
ok1 = r1.status_code == 200
log("upload 带 conversation_id 应 200", ok1, f"HTTP {r1.status_code} " + (str(r1.json())[:120] if ok1 else r1.text[:150]))
if ok1:
    did = r1.json().get("document_id") or r1.json().get("id")
    requests.delete(BASE + f"/api/v1/documents/{did}", timeout=30)

r2 = requests.post(BASE + "/api/v1/documents/upload",
                   files={"file": ("doc-global-kb.txt", io.BytesIO(content), "text/plain")}, timeout=240)
log("upload 不带 conversation(全局KB) 应 200(当前代码意图)",
    r2.status_code == 200,
    f"HTTP {r2.status_code} " + (str(r2.json())[:150] if r2.status_code == 200 else r2.text[:200]) +
    ("  ← 实际500=缺陷(DB列 NOT NULL 未迁移)" if r2.status_code == 500 else ""))

# ── 5. settings PUT 空/no-op 与同值回写(不改语义) ──
r = requests.put(BASE + "/api/v1/settings", json={"values": {}}, timeout=20)
log("PUT /settings values={} 应 ok", r.status_code == 200 and r.json().get("ok"), f"HTTP {r.status_code} {r.text[:120]}")

# ── 清理 ──
for cid in cleanup_ids:
    try:
        requests.delete(BASE + f"/api/v1/conversations/{cid}", timeout=30)
    except Exception:
        pass
print("\n" + "=" * 60)
fails = [x for x in results if x[1] == "FAIL"]
print(f"PART2 TOTAL={len(results)} PASS={len(results)-len(fails)} FAIL={len(fails)}")
for f in fails:
    print("FAIL:", f[0], "|", f[2][:250])
sys.exit(len(fails))
