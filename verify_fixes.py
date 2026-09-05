# -*- coding: utf-8 -*-
"""修复验证: 缺陷A(全局文档上传) 与 缺陷B(sync total_messages)."""
import io
import uuid

import requests

BASE = "http://127.0.0.1:6655"
H = {"Content-Type": "application/json"}

print("== 缺陷A: 不带 conversation_id 的全局文档上传 ==")
content = ("The Eiffel Tower is 330 metres tall and is located in Paris, France. "
           "It was completed in 1889. Paris is the capital of France.")
r = requests.post(BASE + "/api/v1/documents/upload",
                  files={"file": (f"global-kb-fix-{uuid.uuid4().hex[:6]}.txt",
                                  io.BytesIO(content.encode("utf-8")), "text/plain")},
                  timeout=240)
print("HTTP", r.status_code, r.text[:200] if r.status_code != 200 else "OK -> uploaded (global KB)")
assert r.status_code == 200, "缺陷A 未修复: 全局上传仍失败"
did = r.json().get("document_id") or r.json().get("id")
assert did, "上传响应缺少 document_id/id"

r2 = requests.get(BASE + f"/api/v1/documents/{did}", timeout=20)
print("元数据 GET:", r2.status_code, {k: r2.json().get(k) for k in ("id", "filename", "file_type", "chunk_count") if k in (r2.json() or {})})
assert r2.status_code == 200

r3 = requests.delete(BASE + f"/api/v1/documents/{did}", timeout=20)
print("清理 DELETE:", r3.status_code, r3.text[:120])
assert r3.status_code == 200

print("\n== 缺陷B: sync 后 total_messages 应包含本次新增 ==")
ext = f"ext-fix-{uuid.uuid4().hex[:8]}"
r = requests.post(BASE + "/api/v1/conversations/sync", json={
    "platform": "chatgpt", "external_id": ext, "title": "修复验证",
    "messages": [
        {"role": "user", "text": "第一条", "external_id": "user-0", "message_index": 0, "role_index": 0},
        {"role": "assistant", "text": "第一条回复", "external_id": "assistant-1", "message_index": 1, "role_index": 0},
    ],
}, timeout=30)
print("sync#1:", r.status_code, r.text[:200])
j = r.json()
cid = j["conversation_id"]
assert j["messages_synced"] == 2 and j["total_messages"] == 2, f"sync#1 计数错误: {j}"

r = requests.post(BASE + "/api/v1/conversations/sync", json={
    "platform": "chatgpt", "external_id": ext, "title": "修复验证",
    "messages": [{"role": "assistant", "text": "第二条回复", "external_id": "assistant-2", "message_index": 2, "role_index": 1}],
}, timeout=30)
print("sync#2:", r.status_code, r.text[:200])
j2 = r.json()
assert j2["messages_synced"] == 1 and j2["total_messages"] == 3, f"sync#2 计数错误: {j2}"

g = requests.get(BASE + f"/api/v1/conversations/{cid}", timeout=20)
stored = len(g.json().get("messages", []))
print("GET 实际条数:", stored)
assert stored == 3 and j2["total_messages"] == stored, "sync total 与实际不符"

requests.delete(BASE + f"/api/v1/conversations/{cid}", timeout=20)
print("\n两项修复验证通过，测试数据已清理。")
