# -*- coding: utf-8 -*-
"""TruthLens 强制鉴权模式(API_KEY_REQUIRED=true)下的鉴权执行测试.

用法: python api_auth_enforcement_test.py <base> <master_key>
"""
import sys
import time
import uuid

import requests

BASE = sys.argv[1]
MASTER = sys.argv[2]
H = {"Content-Type": "application/json"}
results = []


def log(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  {detail}")


def req(method, path, headers=None, **kw):
    kw.setdefault("timeout", 30)
    hh = dict(H)
    hh.update(headers or {})
    kw["headers"] = hh
    return requests.request(method, BASE + path, **kw)


# 公开端点
r = req("GET", "/health")
log("公开: /health 无Key 200", r.status_code == 200, f"HTTP {r.status_code}")
r = req("GET", "/api/v1/apikeys/status")
log("公开: /apikeys/status 无Key", r.status_code == 200, f"HTTP {r.status_code} (管理页设计为无鉴权)")

# 应被强制拦截的端点
for name, m, p in [
    ("强制: GET /v1/models 无Key", "GET", "/v1/models"),
    ("强制: POST /v1/chat/completions 无Key", "POST", "/v1/chat/completions"),
    ("强制: POST /api/v1/chat 无Key", "POST", "/api/v1/chat"),
    ("强制: POST /api/v1/detect 无Key", "POST", "/api/v1/detect"),
]:
    if p == "/v1/models":
        r = req(m, p)
    elif p == "/v1/chat/completions":
        r = req(m, p, json={"model": "openai/gpt-oss-20b", "messages": [{"role": "user", "content": "hi"}]})
    else:
        r = req(m, p, json={})
    log(name, r.status_code == 401, f"HTTP {r.status_code} {r.text[:120]}")

# 错误 Key
r = req("GET", "/v1/models", headers={"Authorization": "Bearer tl-invalid-key-000000000"})
log("强制: 无效Key 401", r.status_code == 401, f"HTTP {r.status_code}")
r = req("GET", "/v1/models", headers={"X-API-Key": "tl-invalid-key-000000000"})
log("强制: X-API-Key 无效 401", r.status_code == 401, f"HTTP {r.status_code}")

# 主 Key(Bearer + X-API-Key)
r = req("GET", "/v1/models", headers={"Authorization": f"Bearer {MASTER}"})
log("强制: 主Key Bearer 200", r.status_code == 200, f"HTTP {r.status_code}")
r = req("GET", "/v1/models", headers={"X-API-Key": MASTER})
log("强制: 主Key X-API-Key 200", r.status_code == 200, f"HTTP {r.status_code}")

# DB 子 Key 生命周期
r = req("POST", "/api/v1/apikeys", json={"name": "强制模式测试"})
if r.status_code == 200:
    raw = r.json()["api_key"]
    kid = r.json()["id"]
    log("强制: 创建子Key(管理端点开放)", True, f"HTTP 200 prefix={r.json()['prefix']}")
    r = req("GET", "/v1/models", headers={"Authorization": f"Bearer {raw}"})
    log("强制: 子Key 放行 200", r.status_code == 200, f"HTTP {r.status_code}")
    r = req("DELETE", f"/api/v1/apikeys/{kid}")
    log("强制: 吊销子Key", r.status_code == 200 and r.json().get("ok"), f"HTTP {r.status_code}")
    r = req("GET", "/v1/models", headers={"Authorization": f"Bearer {raw}"})
    log("强制: 已吊销子Key 401", r.status_code == 401, f"HTTP {r.status_code} {r.text[:100]}")
else:
    log("强制: 创建子Key", False, f"HTTP {r.status_code} {r.text[:200]}")

# 未加鉴权的数据端点是否仍开放(安全观察)
r = req("GET", "/api/v1/conversations")
log("观察: 强制模式下 GET /conversations 无Key", r.status_code == 200, f"HTTP {r.status_code} (无鉴权依赖→仍开放)")
r = req("GET", "/api/v1/settings/status")
log("观察: 强制模式下 GET /settings/status 无Key", r.status_code == 200, f"HTTP {r.status_code} (无鉴权依赖→仍开放)")
r = req("GET", "/api/v1/analytics/overview?days=7")
log("观察: 强制模式下 GET /analytics/overview 无Key", r.status_code == 200, f"HTTP {r.status_code} (无鉴权依赖→仍开放)")
r = req("GET", "/api/v1/documents")
log("观察: 强制模式下 GET /documents 无Key", r.status_code == 200, f"HTTP {r.status_code} (无鉴权依赖→仍开放)")

print("\n" + "=" * 60)
fails = [x for x in results if not x[1]]
print(f"AUTH TOTAL={len(results)} PASS={len(results)-len(fails)} FAIL={len(fails)}")
for f in fails:
    print("FAIL:", f[0], "|", f[2][:200])
sys.exit(len(fails))
